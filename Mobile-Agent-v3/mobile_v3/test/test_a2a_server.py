# mobile_v3/test/test_a2a_server.py

import pytest
import asyncio
import logging
import os
import sys
from typing import List
from unittest.mock import patch, AsyncMock

# 确保 Python 能够找到 mobile_v3 包
# 假设脚本在 Mobile-Agent-v3/test/，项目根目录是 Mobile-Agent-v3/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- 真实的 VLM 配置 (请替换为您的实际值) ---
REAL_VLM_API_KEY = ""
REAL_VLM_BASE_URL = "http://localhost:6006/v1" 
REAL_VLM_MODEL = "iic/GUI-Owl-7B"

# 导入 A2A Server 核心执行器
from mobile_v3.a2a_server.mobile_agent_a2a import MobileAgentTaskExecutor 

# 导入 Mock 辅助组件
from mobile_v3.a2a_server.a2a_mock import (
    MOCK_IMAGES_CASE2,
    VLMStub, 
    A2AClientStub, 
    MOCK_SCREENSHOT_1_B64, 
    MOCK_SCREENSHOT_2_B64,
    # 导入 VLM 原始回复常量 (假设已在 a2a_mock.py 中定义)
    MANAGER_PLAN_MOCK_1,
    EXECUTOR_ACTION_MOCK_1,
    REFLECTOR_OUTCOME_1,
    NOTETAKER_NOTES_1,
    MANAGER_PLAN_MOCK_2,
    EXECUTOR_ACTION_MOCK_2
)

def get_b64_from_mock(n: int) -> str:
    """从 Case 2 字典中获取 Base64 截图 (用于初始状态)"""
    return MOCK_IMAGES_CASE2.get(n, "PLACEHOLDER_B64")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# ----------------------------------------------------------------------
# 单元测试用例
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_app_launch_cycle():
    """
    测试一个完整的任务周期：打开抖音 -> 成功点击 -> 反思成功 -> 结束回答 (Answer Action)。
    
    预期流程:
    1. Manager 规划 (图1)
    2. Executor 决策 (图1, Click)
    3. Client 执行 Click
    4. Reflector 反思 (图1/图2, Outcome A)
    5. Notetaker 记忆 (图2)
    6. Manager 再次规划 (图2, 决策 Answer)
    7. Executor 决策 (图2, Answer) -> 任务结束
    """
    TASK_ID = 2001
    INSTRUCTION = "打开抖音。"
    MAX_STEPS = 5 # 任务应该在 2 个 Agent 循环内完成 (总共 6 次 VLM 调用)

    # 1. 准备 VLM Mock 脚本
    # 按照 VLM 调用顺序排列：Manager -> Executor -> Reflector -> Notetaker -> Manager -> Executor
    vlm_script: List[str] = [
        MANAGER_PLAN_MOCK_1,      
        EXECUTOR_ACTION_MOCK_1,   
        REFLECTOR_OUTCOME_1,      
        NOTETAKER_NOTES_1,        
        MANAGER_PLAN_MOCK_2,      
        EXECUTOR_ACTION_MOCK_2    
    ]

    # 2. 实例化 Mock 对象
    vlm_stub = VLMStub(vlm_script)
    a2a_client_stub = A2AClientStub(TASK_ID, logging.getLogger(f'Test_{TASK_ID}'))
    
    # 3. 实例化 Executor (注入 VLM Stub)
    # VLM 配置参数在单元测试中被忽略
    executor = MobileAgentTaskExecutor(
        api_key="mock", 
        base_url="mock", 
        model="mock", 
        vlm_wrapper=vlm_stub # 注入 VLM Mock
    )

    # 4. 执行任务
    await executor.execute_task(
        task_id=TASK_ID,
        instruction=INSTRUCTION,
        # Node.js Client 提供的初始 Base64 截图
        initial_screenshot_b64=MOCK_SCREENSHOT_1_B64, 
        if_notetaker=True, 
        max_step=MAX_STEPS,
        a2a_interface_mock=a2a_client_stub # 注入 A2A I/O Mock
    )

    # 5. 断言检查 (验证整个流程的完整性)
    
    # 5.1 检查 VLM 调用次数
    assert vlm_stub.call_counter == 6, "VLM Stub 应该被调用了 6 次以完成这个成功的任务周期。"
    
    # 5.2 检查 A2A 动作请求次数
    # 任务包含 1次 Click 动作 和 1次 Answer 动作。
    # - Click 动作需要 Client (A2A Client Stub) 执行 ADB 并回复截图 (a2a_client_stub.wait_for_client_reply 被调用)
    # - Answer 动作是 Agent 内部的 terminate 信号，不需要 Client 执行 ADB，不增加 action_counter
    assert a2a_client_stub.action_counter == 1, "A2A Client Stub 应该只被请求了 1 次外部动作 (Click)。"

    # 5.3 检查执行器是否正确处理了任务完成信号 (Answer action)
    # (这需要检查 execute_task 内部是否正确地退出了循环，此处简化为对 VLM Call 次数的断言)
    
    print(f"\n--- 单元测试 {TASK_ID} 成功完成 ---")
    print(f"**验证结果:** VLM 调用次数正确 ({vlm_stub.call_counter} 次), 外部动作请求次数正确 ({a2a_client_stub.action_counter} 次)。")

@pytest.mark.asyncio
async def test_real_vlm_single_cycle():
    """
    集成测试：使用真实的 VLM 模型，验证 Agent 流程的实际推理能力。
    
    流程:
    1. Agent (真实VLM) 规划 (图1) -> 预期: Click 动作
    2. Client Stub 执行 Click -> 返回 图2
    3. Agent (真实VLM) 反思 (图1/图2) -> 预期: Outcome A
    4. Agent (真实VLM) 再次规划 (图2) -> 预期: Answer 动作
    5. 任务终止 (Answer)
    """
    TASK_ID = 3001
    INSTRUCTION = "打开抖音。"
    MAX_STEPS = 5

    # 1. 实例化真实的 VLM Executor
    executor = MobileAgentTaskExecutor(
        api_key=REAL_VLM_API_KEY, 
        base_url=REAL_VLM_BASE_URL, 
        model=REAL_VLM_MODEL,
        # 不注入 vlm_wrapper，使用真实的 GUIOwlWrapper
    )

    # 2. 保持 Client I/O 的 Mock
    a2a_client_stub = A2AClientStub(TASK_ID, logging.getLogger(f'Test_{TASK_ID}'))
    
    # 3. 执行任务
    print(f"\n--- 启动任务 {TASK_ID}，连接真实 VLM ---")
    
    await executor.execute_task(
        task_id=TASK_ID,
        instruction=INSTRUCTION,
        # 传递 Base64 字符串 (假设 MOCK_SCREENSHOT_1_B64 是纯 Base64)
        initial_screenshot_b64=MOCK_SCREENSHOT_1_B64, 
        if_notetaker=True, 
        max_step=MAX_STEPS,
        a2a_interface_mock=a2a_client_stub 
    )

    # 4. 断言检查 (预期 VLM 行为)
    
    # 4.1 预期 VLM 至少调用 4 次 (M1, E1, R1, M2/E2)
    # 由于无法直接访问 vlm_wrapper.call_counter，我们必须依赖日志或 Client Stub 的行为
    
    # 在这个集成测试中，我们依赖于 Client Stub 被请求了 1 次外部动作 (Click)
    # 并且任务成功终止 (Final Answer Event)
    
    assert a2a_client_stub.action_counter == 1, "Client Stub 应该只被请求了 1 次外部动作 (Click)，以确保 Agent 成功推理出 Answer。"
    
    # NOTE: 实际断言应检查最终 A2A Event Stream 中是否包含 'final_answer' 事件，
    # 但此处我们依赖 action_counter 来确认 Click 动作已发生且 Answer 动作未被错误请求。
    
    print(f"\n--- 集成测试 {TASK_ID} 完成 ---")
    print(f"**验证结果:** Client 动作请求次数为 {a2a_client_stub.action_counter}。")
    
@pytest.mark.asyncio
async def test_concurrent_task_execution(num_tasks: int = 10, max_steps: int = 5):
    """
    测试高并发场景：同时启动多个 Mobile Agent 任务。
    
    目标: 验证 Python A2A Server (L1) 在 AsyncIO 下的任务隔离和稳定性。
    """
    
    # 真实 VLM/Mock Client 配置
    executor = MobileAgentTaskExecutor(
        api_key=REAL_VLM_API_KEY, 
        base_url=REAL_VLM_BASE_URL, 
        model=REAL_VLM_MODEL,
    )
    INSTRUCTION = "打开抖音。"
    
    tasks = []
    task_ids = range(4001, 4001 + num_tasks) # 从 4001 开始分配 Task ID
    
    print(f"\n--- 启动 {num_tasks} 个并发任务 ---")
    
    async def run_single_concurrent_task(task_id):
        """定义单个任务的执行和断言逻辑"""
        # 确保每个任务都有独立的 Logger 和 A2A Stub
        task_logger = logging.getLogger(f'Test_{task_id}')
        a2a_client_stub = A2AClientStub(task_id, task_logger)

        try:
            await executor.execute_task(
                task_id=task_id,
                instruction=INSTRUCTION,
                initial_screenshot_b64=MOCK_SCREENSHOT_1_B64, 
                if_notetaker=True, 
                max_step=max_steps,
                a2a_interface_mock=a2a_client_stub 
            )
            
            # 关键断言：任务必须完成，且外部动作请求次数正确
            assert a2a_client_stub.action_counter == 1, f"Task {task_id}: 外部动作请求次数应为 1。"
            return "SUCCESS"
        
        except Exception as e:
            task_logger.error(f"Task {task_id}: 致命错误 - {e}")
            return f"FAILURE: {e}"

    # 使用 asyncio.gather 并发运行所有任务
    results = await asyncio.gather(*[run_single_concurrent_task(tid) for tid in task_ids], return_exceptions=True)

    # 4. 最终断言和报告
    failures = [r for r in results if r != "SUCCESS"]
    success_count = len(results) - len(failures)
    
    print("\n================== 并发测试结果总结 ==================")
    print(f"总任务数: {num_tasks}")
    print(f"成功完成: {success_count} / {num_tasks}")
    print(f"失败任务数: {len(failures)}")
    
    if failures:
        print("\n--- 失败详情 ---")
        for fail_result in failures:
            print(f"- {fail_result}")
            
    assert success_count == num_tasks, f"预期所有任务成功，实际失败 {len(failures)} 个。"

@pytest.mark.asyncio
async def test_concurrency_level_10():
    await test_concurrent_task_execution(num_tasks=10)
    
@pytest.mark.asyncio
async def test_real_complex_task():
    """
    集成测试 (真实 VLM): 使用真实 VLM 模型和模拟的 15 步截图序列。
    
    流程: Agent 必须在 14 个动作中正确推理出打开、搜索、点击、评论的序列，
          最终在第 15 张图后推理出 Answer 动作。
    """
    TASK_ID = 5000
    INSTRUCTION = "在抖音中搜索一家理发店并发表评论。"
    MAX_STEPS = 20 # 允许足够多的步数来完成复杂任务

    # 1. 实例化真实的 VLM Executor (使用真实的配置)
    executor = MobileAgentTaskExecutor(
        api_key=REAL_VLM_API_KEY, 
        base_url=REAL_VLM_BASE_URL, 
        model=REAL_VLM_MODEL,
        # NOTICE: vlm_wrapper 参数被省略，使用真实的 self.vlm
    )

    # 2. 实例化 Client I/O Mock (使用 14 步序列)
    # Client Stub 会自动加载并使用 14 步脚本 (返回图2到图15)
    a2a_client_stub = A2AClientStub(TASK_ID, logging.getLogger(f'Test_{TASK_ID}'))

    # 3. 执行任务
    print(f"\n--- 启动真实 VLM 任务 {TASK_ID}：{INSTRUCTION} ---")
    
    await executor.execute_task(
        task_id=TASK_ID,
        instruction=INSTRUCTION,
        # 初始截图是第一张图 (1.jpg)
        initial_screenshot_b64=get_b64_from_mock(1), 
        if_notetaker=True, 
        max_step=MAX_STEPS,
        a2a_interface_mock=a2a_client_stub 
    )

    # 4. 最终断言检查 (验证 Agent 是否成功完成所有步骤)
    expected_actions = 14 # 假设任务需要 14 个外部动作才能到达最终状态
    
    print("\n================== 任务结果断言 ==================")
    print(f"实际动作数: {a2a_client_stub.action_counter}")
    
    # 任务成功的判断标准：Agent 必须执行了所有 14 个模拟动作，并且最终推理出 Answer 动作
    assert a2a_client_stub.action_counter == expected_actions, f"外部动作执行次数错误，预期 {expected_actions} 次，实际 {a2a_client_stub.action_counter} 次。"
    
    # NOTE: 如果任务成功完成，日志中应有 'Task finished by Executor with: answer.'
    
    print(f"\n--- 真实 VLM 集成测试 {TASK_ID} 完成 ---")
    
# @pytest.mark.asyncio
# async def test_failed_action_and_replan():
#     """测试动作失败 (Reflector Outcome C) 导致 Manager 重新规划的流程。"""
#     # ... (需要新的 VLM/A2A Mock 脚本来模拟失败和重试) ...
#     pass