# mobile_v3/a2a_server/a2a_mock.py

import json
import base64
from io import BytesIO
import logging
import os
from PIL import Image
import asyncio
from typing import Dict, Any, List

# --- 测试图片准备 ---
# 假设 1.jpg 是初始屏幕 (桌面)
# 假设 2.jpg 是抖音启动后的屏幕 (内容流)

def load_image_b64(path: str) -> str:
    """加载图片并转换为 Base64 字符串"""
    if not os.path.exists(path):
        # 如果文件不存在，返回一个简单的占位符 Base64 字符串
        return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYGD4DwAADgAEEhAAaAAAAABJRU5ErkJggg=="
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

# 预加载测试图片
MOCK_SCREENSHOT_1_B64 = load_image_b64("../../test/case1/1.jpg") # 初始桌面
MOCK_SCREENSHOT_2_B64 = load_image_b64("../../test/case1/2.jpg") # 抖音启动后

class A2AClientStub:
    """
    Mock A2A Client 的行为，用于单元测试 Python A2A Server 的执行循环。
    它模拟了 Client 接收动作请求 -> 执行 ADB -> 返回截图的过程。
    """
    
    def __init__(self, task_id: int, logger: logging.Logger):
        self.task_id = task_id
        self.logger = logger
        self.action_counter = 0

        # 定义一个简单的执行脚本，用于返回截图数据
        # 脚本: [初始截图, 动作1后截图, 动作2后截图, ...]
        self.mock_script: List[Dict[str, Any]] = [
            # 初始状态：Client 应该在 execute_task 函数外部提供初始截图
            # 动作 1：模拟点击 '抖音' 图标
            {
                "screenshot_b64": MOCK_SCREENSHOT_2_B64, 
                "width": 1080, "height": 1920,
                "note": "Client executed: click 抖音. Screenshot 2 returned."
            },
            # 动作 2：模拟点击 '关注' 按钮 (假设下一个动作)
            {
                "screenshot_b64": MOCK_SCREENSHOT_2_B64, 
                "width": 1080, "height": 1920,
                "note": "Client executed: click 关注. Screenshot 2 (no change) returned."
            },
            # 动作 3：模拟 swipe down (假设下一个动作)
            {
                "screenshot_b64": MOCK_SCREENSHOT_2_B64, 
                "width": 1080, "height": 1920,
                "note": "Client executed: swipe down. Screenshot 2 returned again."
            },
            # ... 您可以根据需要添加更多步骤 ...
        ]

    # ----------------------------------------------------------------------
    # 核心 Mock 接口 (模拟 A2A Server 调用的接口)
    # ----------------------------------------------------------------------

    async def push_event(self, event: Dict[str, Any]):
        """
        Mock A2A Server 推送 Event Stream 到 Client 的行为。
        我们主要关心的是 Manager/Executor 推送的 'action_request' 事件。
        """
        self.logger.info(f"[Mock A2A Push] Type: {event.get('type')}")
        if event.get('type') == 'action_request':
            action_json = event['metadata'].get('action_json')
            self.logger.info(f"[Mock A2A Push] --> ACTION REQUESTED: {action_json}")
        
        # 实际 A2A SDK 会发送网络数据，这里只记录日志
        await asyncio.sleep(0.01)

    async def wait_for_client_reply(self, timeout: int = 60) -> Dict[str, Any]:
        """
        Mock A2A Server 阻塞等待 Client 回复截图的行为。
        这是单元测试的关键阻塞点。
        """
        if self.action_counter >= len(self.mock_script):
            self.logger.warning("[Mock A2A Reply] Mock script exhausted. Returning last screenshot.")
            # 返回最后一个状态，防止无限循环崩溃
            return {
                "type": "action_reply",
                "screenshot_b64": self.mock_script[-1]["screenshot_b64"],
                "screenshot_width": self.mock_script[-1]["width"],
                "screenshot_height": self.mock_script[-1]["height"],
            }

        # 模拟 Client 执行 ADB 操作的时间
        await asyncio.sleep(0.1) 
        
        # 获取当前步骤的 Mock 回复数据
        reply_data = self.mock_script[self.action_counter]
        self.action_counter += 1
        
        self.logger.info(f"[Mock A2A Reply] <-- REPLY: Action {self.action_counter}. Note: {reply_data['note']}")
        
        # 封装成 A2A Server 期望的 Message Reply 格式
        return {
            "type": "action_reply",
            "screenshot_b64": reply_data["screenshot_b64"],
            "screenshot_width": reply_data["width"],
            "screenshot_height": reply_data["height"],
        }
    
class VLMStub:
    """
    Mock VLM Wrapper，用于单元测试 Agent 的决策逻辑。
    """
    def __init__(self, script: List[str]):
        # script 是预设的 VLM 响应列表，按 Manager, Executor, Reflector 的顺序
        self.script = script
        self.call_counter = 0

    async def predict_mm(self, prompt: str, image_inputs: List[Any]) -> Tuple[str, Any, Any]:
        """
        模拟 VLM 的异步调用。
        每次调用返回 script 中的下一个预设响应。
        """
        if self.call_counter >= len(self.script):
            return "Finished", None, True # 脚本结束，返回终止信号
        
        # 模拟 VLM 推理时间
        await asyncio.sleep(0.005) 
        
        response = self.script[self.call_counter]
        self.call_counter += 1
        
        # 返回 (response_text, message_history, raw_response_flag)
        return response, None, True