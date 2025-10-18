# mobile_v3/a2a_server/mobile_agent_a2a.py

import os
import uuid
import json
import time
import asyncio
from datetime import datetime
from PIL import Image
from typing import Dict, Any, Tuple, Optional

# 导入 V3 核心组件
from mobile_v3.utils.mobile_agent_e import (
    InfoPool, 
    Manager, 
    Executor, 
    Notetaker, 
    ActionReflector,
    INPUT_KNOW
)
# 导入 VLM Wrapper 和 A2A 辅助工具
from ..utils.call_mobile_agent_e import GUIOwlWrapper
from .a2a_utils import setup_task_logger, create_action_request_event, create_a2a_event

# ---------------------------------------------------------------------
# 1. 异步任务回复管理器 (用于解除 L1 任务的阻塞)
# ---------------------------------------------------------------------
# 全局字典，存储等待 L2 客户端回复的 Future 对象 {task_id: asyncio.Future}
ACTION_REPLY_FUTURES: Dict[int, asyncio.Future] = {}

def get_action_reply_future(task_id: int) -> asyncio.Future:
    """
    获取或创建用于接收 L2 动作回复的 Future。
    外部 A2A Server 接口（如 /v1/tasks/{task_id}:reply）将调用 set_result 来解除阻塞。
    """
    if task_id not in ACTION_REPLY_FUTURES or ACTION_REPLY_FUTURES[task_id].done():
        ACTION_REPLY_FUTURES[task_id] = asyncio.Future()
    return ACTION_REPLY_FUTURES[task_id]

# ---------------------------------------------------------------------
# 2. 抽象 A2A 接口 (Mock/Real 的基类)
# ---------------------------------------------------------------------
class A2AInterfaceBase:
    """定义 A2A 事件推送和动作回复等待的抽象接口"""
    def __init__(self, task_id: int):
        self.task_id = task_id
    
    async def push_event(self, event: Dict[str, Any]):
        """将事件推送到 Node.js Client（A2A 协议流）"""
        raise NotImplementedError
        
    async def wait_for_client_reply(self, timeout: int = 60) -> Dict[str, Any]:
        """阻塞等待 Node.js Client 返回截图 (Action Reply)"""
        raise NotImplementedError
    
# ---------------------------------------------------------------------
# 3. 改造 A2AInterfaceMock (用于单元测试)
# ---------------------------------------------------------------------
class A2AInterfaceMock(A2AInterfaceBase):
    """用于单元测试的模拟接口，保留原有逻辑"""
    # ... (原有 __init__ 和 push_event 逻辑不变，继承自 Base) ...

    async def push_event(self, event: Dict[str, Any]):
        """模拟事件推送"""
        print(f"[A2A_PUSH] Task {self.task_id}: {event['type']}")
        pass # 单元测试中通常不需要真正的推送

    async def wait_for_client_reply(self, timeout: int = 60) -> Dict[str, Any]:
        """
        模拟阻塞等待 Node.js Client 返回截图。
        注意：单元测试中如果需要异步控制回复，可以注入 Future，但这里保留简单模拟。
        """
        # 实际实现应等待 A2A 消息队列或回调
        await asyncio.sleep(0.01) # 缩短等待时间以加速测试
        
        # 伪造一个回复，包含动作后的截图
        return {
            "type": "action_reply",
            "screenshot_b64": "MOCK_B64_SCREENSHOT_AFTER_ACTION",
            "screenshot_width": 1080,
            "screenshot_height": 1920,
        }

# ---------------------------------------------------------------------
# 4. 新增 A2AInterfaceReal (用于实际的 A2A/HTTP 模式)
# ---------------------------------------------------------------------
class A2AInterfaceReal(A2AInterfaceBase):
    """
    实际 A2A 模式下的接口实现。
    使用传入的 event_queue 推送事件，并使用全局 Future 等待回复。
    """
    def __init__(self, task_id: int, event_queue: asyncio.Queue):
        super().__init__(task_id)
        self.event_queue = event_queue # 用于推送 A2A 事件到 L1 Server 的流式接口
        
    async def push_event(self, event: Dict[str, Any]):
        """将事件推送到 L1 Server 的 SSE 流"""
        await self.event_queue.put(event)

    async def wait_for_client_reply(self, timeout: int = 60) -> Dict[str, Any]:
        """
        使用全局 ACTION_REPLY_FUTURES 等待 L2 客户端的异步回复。
        """
        task_logger = setup_task_logger(self.task_id)
        
        # 1. 获取对应的 Future
        reply_future = get_action_reply_future(self.task_id)

        # 2. 异步等待 L2 客户端通过 HTTP POST /reply 接口来设置 Future 的结果
        try:
            task_logger.info(f"Task {self.task_id} is awaiting L2 Client reply...")
            reply = await asyncio.wait_for(reply_future, timeout=timeout)
            task_logger.info(f"Task {self.task_id} received L2 reply.")
            return reply
        except asyncio.TimeoutError:
            task_logger.error(f"L2 Client reply timed out after {timeout}s for Task {self.task_id}")
            raise # 抛出超时错误，将导致 execute_task 失败
        finally:
            # 清理 Future (无论成功失败，如果它仍然在字典中，就移除)
            ACTION_REPLY_FUTURES.pop(self.task_id, None)

class MobileAgentTaskExecutor:
    
    def __init__(self, api_key: str, base_url: str, model: str, vlm_wrapper: Optional[Any] = None):
        # 如果提供了 vlm_wrapper (Mock), 则使用它；否则创建真实的 GUIOwlWrapper
        if vlm_wrapper:
            self.vllm = vlm_wrapper
        else:
            self.vllm = GUIOwlWrapper(api_key, base_url, model)

    # ---------------------------------------------------------------------
    # 1. A2A 模式入口 (静态方法，用于在 run_a2a_server.py 中调用)
    # ---------------------------------------------------------------------
    @staticmethod
    async def execute_task_a2a_mode(
        task_id: int,
        instruction: str,
        initial_screenshot_b64: str,
        event_queue: asyncio.Queue, # L1 Server 传入的事件队列
        api_key: str, base_url: str, model: str,
        if_notetaker: bool = False,
        max_step: int = 25,
    ):
        """
        A2A 模式下的执行入口。创建 VLM Wrapper 和 A2A 接口 Real 实现。
        """
        # 创建 VLM Wrapper (用于 VLM API 调用)
        # 注意：这里需要从 run_a2a_server.py 传入 VLM 参数
        executor_instance = MobileAgentTaskExecutor(api_key, base_url, model)

        # 实例化 A2A Real 接口 (使用传入的队列推送事件)
        a2a_interface = A2AInterfaceReal(task_id, event_queue)

        # 调用核心逻辑
        await executor_instance._execute_task_logic(
            task_id=task_id,
            instruction=instruction,
            initial_screenshot_b64=initial_screenshot_b64,
            max_step=max_step,
            if_notetaker=if_notetaker,
            a2a_interface=a2a_interface # 传入 Real 接口
        )

    # 兼容性方法：保留原有的 execute_task 签名，用于单元测试
    async def execute_task(
        self, 
        task_id: int, 
        # ... (其他参数) ...
        a2a_interface_mock: Optional[A2AInterfaceBase] = None # 类型现在是 A2AInterfaceBase
    ):
        # ❗ 注意：这里需要确保 VLM 参数被正确设置，或者从实例中获取 ❗
        # 简化：假设 VLM 参数已在 __init__ 中保存
        
        # 如果是单元测试模式，则直接调用核心逻辑，传入 Mock
        if a2a_interface_mock:
            await self._execute_task_logic(
                # ... (参数透传) ...
                a2a_interface=a2a_interface_mock
            )
            return

        # 实际 A2A 模式下，应该通过 run_a2a_server.py 的 A2A API 启动
        # 如果不是 Mock 模式且未通过 A2A API 启动，则抛出错误或执行默认逻辑
        raise ValueError("Cannot run directly without A2A API in Real mode.")
    
    async def _execute_task_logic(
        self, 
        task_id: int, 
        instruction: str, 
        initial_screenshot_b64: str,
        add_info: str = "",
        max_step: int = 25, 
        coor_type: str = "abs",
        if_notetaker: bool = False,
        a2a_interface: A2AInterfaceBase = None 
    ):
        """
        核心 Agent 循环逻辑。现在接受一个 A2AInterfaceBase 实例。
        这个方法的内容与您提供的 execute_task 保持一致，只是使用了注入的 a2a_interface。
        """
        task_logger = setup_task_logger(task_id)
        task_logger.info(f"Task {task_id} started. Instruction: {instruction}")
        
        if a2a_interface is None:
             # 如果没有注入接口，且不是 Mock，则使用默认 Real 接口（仅用于兼容性，A2A模式应始终注入）
             # ❗ 为了兼容单元测试，这里强制要求传入接口，或者在单元测试中传入 Mock ❗
             raise ValueError("A2A Interface must be provided.")
        
        # 初始化 Agent 核心组件 (复用 mobile_agent_e.py)
        info_pool = InfoPool(
            instruction=instruction,
            additional_knowledge_manager=add_info,
            additional_knowledge_executor=INPUT_KNOW,
            err_to_manager_thresh=2
        )
        manager = Manager()
        executor = Executor()
        notetaker = Notetaker()
        action_reflector = ActionReflector()
        
        # 初始状态
        current_screenshot_b64 = initial_screenshot_b64
        current_width, current_height = 1080, 1920 # 假设初始截图信息
        
        for step in range(max_step):
            task_logger.info(f"\n--- STEP {step+1} ---")
            
            # -----------------------------------------------------
            # I/II. 规划 (Manager) 和 动作决策 (Executor)
            # -----------------------------------------------------
            
            # Manager 规划逻辑 (与 run_mobileagentv3.py 类似)
            # ... [省略 skip_manager 逻辑]
            
            # NOTE: 在 A2A 架构中，Manager 和 Executor 步骤都需要截图作为 VLM 输入，
            # 需要将 Base64 截图处理成 VLM Wrapper (GUIOwlWrapper) 可接受的格式（通常是临时文件或 Base64 URL）
            
            # --- Manager ---
            prompt_planning = manager.get_prompt(info_pool)
            image_id_manager = f"step_{step+1}_before_action"
            task_logger.info(f"VLM CALL (Manager): Using image ID: {image_id_manager}")
            
            output_planning, _, _ = await self.vllm.predict_mm(
                prompt_planning, [current_screenshot_b64] 
            )
            parsed_result_planning = manager.parse_response(output_planning)
            info_pool.completed_plan = parsed_result_planning['completed_subgoal']
            info_pool.plan = parsed_result_planning['plan']

            task_logger.info(f"MANAGER THOUGHT: {parsed_result_planning['thought']}...")
            task_logger.info(f"MANAGER PLAN: {info_pool.plan}")
            
            await a2a_interface.push_event(create_a2a_event("manager_plan", task_id, {
                "plan": info_pool.plan,
                "thought": parsed_result_planning['thought'],
            }))
            
            if "Finished" in info_pool.plan.strip():
                task_logger.info("Task finished by Manager.")
                break # 任务结束
            
            # --- Executor ---
            prompt_action = executor.get_prompt(info_pool)
            image_id_executor = f"step_{step+1}_before_action"
            task_logger.info(f"VLM CALL (Executor): Using image ID: {image_id_executor}")

            output_action, _, _ = await self.vllm.predict_mm(
                prompt_action, [current_screenshot_b64]
            )
            parsed_result_action = executor.parse_response(output_action)
            action_object_str = parsed_result_action['action']
            
            # 校验和处理动作 JSON
            try:
                action_object = json.loads(action_object_str)
                action_thought = parsed_result_action['thought']
            except json.JSONDecodeError:
                task_logger.error(f"Invalid JSON output from Executor: {action_object_str}")
                # 处理错误并继续循环...
                continue

            task_logger.info(f"EXECUTOR THOUGHT: {action_thought}...")
            # 使用 json.dumps 确保 JSON 打印清晰，并截断长字符串
            task_logger.info(f"EXECUTOR ACTION JSON: {action_object_str}")
            
            # -----------------------------------------------------
            # III. 动作请求 (替换本地 Controller.py)
            # -----------------------------------------------------
            action_type = action_object.get('action')
            if action_type == "answer":
                task_logger.info(f"Task finished by Executor with: answer.")
                
                # 1. 推送 Answer 事件给 Client (用于前端显示最终答案)
                await a2a_interface.push_event(create_a2a_event("final_answer", task_id, {
                    "text": action_object.get("text", "Task completed."),
                    "status": action_object.get("status", "success")
                }))
                
                # 2. 直接退出整个循环，不需要 Client 执行 ADB 或回复截图
                break # 退出 for step in range(max_step): 循环
            
            # 1. 向 Client 推送动作请求并阻塞
            task_logger.info(f"Pushing action request: {action_type}")
            await a2a_interface.push_event(
                create_action_request_event(task_id, action_object_str, action_thought)
            )
            
            # 2. 等待 Client 执行 ADB 操作并返回新的截图
            reply = await a2a_interface.wait_for_client_reply()
            
            if reply.get("type") != "action_reply":
                task_logger.error("Did not receive valid action reply from client.")
                await a2a_interface.push_event(create_a2a_event("task_finalized", task_id, {"status": "failed", "reason": "Invalid action reply from L2"}))
                break

            # 3. 更新执行后的环境状态
            screenshot_after_b64 = reply["screenshot_b64"]
            current_width = reply["screenshot_width"]
            current_height = reply["screenshot_height"]
            current_screenshot_b64 = screenshot_after_b64
            
            # -----------------------------------------------------
            # IV/V. 反思 (Reflector) 和 记忆 (Notetaker)
            # -----------------------------------------------------
            
            # --- Reflector ---
            prompt_action_reflect = action_reflector.get_prompt(info_pool)
            image_id_reflector_before = f"step_{step+1}_before_action"
            image_id_reflector_after = f"step_{step+1}_after_action"
            task_logger.info(f"VLM CALL (Reflector): Using image IDs: [{image_id_reflector_before}, {image_id_reflector_after}]")

            output_action_reflect, _, _ = await self.vllm.predict_mm(
                prompt_action_reflect,
                [current_screenshot_b64, screenshot_after_b64], # 动作前后两张截图
            )
            
            parsed_result_action_reflect = action_reflector.parse_response(output_action_reflect)
            action_outcome = parsed_result_action_reflect['outcome'] # A/B/C
            error_description = parsed_result_action_reflect['error_description']

            task_logger.info(f"REFLECTOR OUTCOME: {action_outcome}")
            if action_outcome in ["B", "C"]:
                 task_logger.warning(f"REFLECTOR ERROR: {error_description}")
            
            # 更新 InfoPool 状态
            info_pool.action_history.append(action_object)
            info_pool.action_outcomes.append(action_outcome)
            
            await a2a_interface.push_event(create_a2a_event("action_reflection", task_id, {
                "outcome": action_outcome,
                "error_description": parsed_result_action_reflect['error_description'],
            }))

            # --- Notetaker (如果成功且启用) ---
            if action_outcome == "A" and if_notetaker:
                prompt_note = notetaker.get_prompt(info_pool)
                image_id_notetaker = f"step_{step+1}_after_action"
                task_logger.info(f"VLM CALL (Notetaker): Using image ID: {image_id_notetaker}")
                
                output_note, _, _ = await self.vllm.predict_mm(
                    prompt_note, [screenshot_after_b64]
                )
                parsed_result_note = notetaker.parse_response(output_note)
                info_pool.important_notes = parsed_result_note['important_notes']
                
                task_logger.info(f"NOTETAKER NOTES: {info_pool.important_notes}")
                await a2a_interface.push_event(create_a2a_event("important_notes", task_id, {
                    "notes": info_pool.important_notes,
                }))

            # 准备下一轮：更新当前截图为动作后的截图
            current_screenshot_b64 = screenshot_after_b64

        task_logger.info(f"Task {task_id} completed.")
        # 推送最终状态
        await a2a_interface.push_event(create_a2a_event("task_finalized", task_id, {"status": "success"}))