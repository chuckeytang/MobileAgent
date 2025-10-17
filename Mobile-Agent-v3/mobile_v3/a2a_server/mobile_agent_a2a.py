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
from utils.mobile_agent_e import (
    InfoPool, 
    Manager, 
    Executor, 
    Notetaker, 
    ActionReflector,
    INPUT_KNOW
)
# 导入 VLM Wrapper 和 A2A 辅助工具
from utils.call_mobile_agent_e import GUIOwlWrapper
from .a2a_utils import setup_task_logger, create_action_request_event, create_a2a_event

# 假设 A2A SDK 提供了以下抽象接口 (您需要在实际项目中集成 A2A SDK)
class A2AInterfaceMock:
    """模拟 A2A Server SDK 提供的事件推送和消息等待接口"""
    def __init__(self, task_id):
        self.task_id = task_id
    
    async def push_event(self, event: Dict[str, Any]):
        """模拟将事件推送到 Node.js Client"""
        print(f"[A2A_PUSH] Task {self.task_id}: {event['type']}")
        # 实际实现应调用 A2A SDK 的 stream/event 接口
        pass

    async def wait_for_client_reply(self, timeout: int = 60) -> Dict[str, Any]:
        """模拟阻塞等待 Node.js Client 返回截图 (Action Reply)"""
        # 实际实现应等待 A2A 消息队列或回调
        await asyncio.sleep(1)  # 模拟网络延迟和执行时间
        
        # NOTE: 在实际项目中，这里需要实现一个机制，
        # 等待 Node.js Client 通过 A2A Message API 返回的动作回复消息
        
        # 伪造一个回复，包含动作后的截图
        return {
            "type": "action_reply",
            "screenshot_b64": "MOCK_B64_SCREENSHOT_AFTER_ACTION",
            "screenshot_width": 1080,
            "screenshot_height": 1920,
        }

class MobileAgentTaskExecutor:
    
    def __init__(self, api_key: str, base_url: str, model: str, vlm_wrapper: Optional[Any] = None):
        # 如果提供了 vlm_wrapper (Mock), 则使用它；否则创建真实的 GUIOwlWrapper
        if vlm_wrapper:
            self.vllm = vlm_wrapper
        else:
            self.vllm = GUIOwlWrapper(api_key, base_url, model)

    async def execute_task(
        self, 
        task_id: int, 
        instruction: str, 
        initial_screenshot_b64: str,
        add_info: str = "",
        max_step: int = 25, 
        coor_type: str = "abs",
        if_notetaker: bool = False,
        a2a_interface_mock: Optional[Any] = None
    ):
        task_logger = setup_task_logger(task_id)
        task_logger.info(f"Task {task_id} started. Instruction: {instruction}")
        
        # --- A2A 接口实例化 (使用注入的 Mock 或默认 Mock) ---
        if a2a_interface_mock:
            a2a_interface = a2a_interface_mock
        else:
            # 实际部署模式（需替换为真实的 A2A SDK 接口）
            a2a_interface = A2AInterfaceReal(task_id)

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
            # 假设 VLM Wrapper 支持 Base64 输入
            output_planning, _, _ = await self.vllm.predict_mm(
                prompt_planning, [current_screenshot_b64] 
            )
            parsed_result_planning = manager.parse_response(output_planning)
            info_pool.completed_plan = parsed_result_planning['completed_subgoal']
            info_pool.plan = parsed_result_planning['plan']
            
            await a2a_interface.push_event(create_a2a_event("manager_plan", task_id, {
                "plan": info_pool.plan,
                "thought": parsed_result_planning['thought'],
            }))
            
            if "Finished" in info_pool.plan.strip():
                task_logger.info("Task finished by Manager.")
                break # 任务结束
            
            # --- Executor ---
            prompt_action = executor.get_prompt(info_pool)
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
                continue

            # 3. 更新执行后的环境状态
            screenshot_after_b64 = reply["screenshot_b64"]
            current_width = reply["screenshot_width"]
            current_height = reply["screenshot_height"]
            
            # -----------------------------------------------------
            # IV/V. 反思 (Reflector) 和 记忆 (Notetaker)
            # -----------------------------------------------------
            
            # --- Reflector ---
            prompt_action_reflect = action_reflector.get_prompt(info_pool)
            output_action_reflect, _, _ = await self.vllm.predict_mm(
                prompt_action_reflect,
                [current_screenshot_b64, screenshot_after_b64], # 动作前后两张截图
            )
            
            parsed_result_action_reflect = action_reflector.parse_response(output_action_reflect)
            action_outcome = parsed_result_action_reflect['outcome'] # A/B/C
            
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
                output_note, _, _ = await self.vllm.predict_mm(
                    prompt_note, [screenshot_after_b64]
                )
                parsed_result_note = notetaker.parse_response(output_note)
                info_pool.important_notes = parsed_result_note['important_notes']
                
                await a2a_interface.push_event(create_a2a_event("important_notes", task_id, {
                    "notes": info_pool.important_notes,
                }))

            # 准备下一轮：更新当前截图为动作后的截图
            current_screenshot_b64 = screenshot_after_b64

        task_logger.info(f"Task {task_id} completed.")
        # 推送最终状态
        await a2a_interface.push_event(create_a2a_event("task_finalized", task_id, {"status": "success"}))