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

# 导入 A2A Server 核心执行器
from mobile_v3.a2a_server.mobile_agent_a2a import MobileAgentTaskExecutor 

# 导入 Mock 辅助组件
from mobile_v3.a2a_server.a2a_mock import (
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

# 配置一个临时的 Logger，用于测试时输出到控制台
# 注意：正式运行时，日志隔离由 a2a_utils.setup_task_logger 处理
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

# ----------------------------------------------------------------------
# 更多测试用例 (可选，用于验证错误和重试逻辑)
# ----------------------------------------------------------------------

# @pytest.mark.asyncio
# async def test_failed_action_and_replan():
#     """测试动作失败 (Reflector Outcome C) 导致 Manager 重新规划的流程。"""
#     # ... (需要新的 VLM/A2A Mock 脚本来模拟失败和重试) ...
#     pass