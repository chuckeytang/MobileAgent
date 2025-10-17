# mobile_v3/a2a_server/a2a_mock.py

import json
import base64
from io import BytesIO
import logging
import os
from PIL import Image
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from mobile_v3.a2a_server.mobile_agent_a2a import A2AInterfaceBase 
from mobile_v3.a2a_server.mobile_agent_a2a import ACTION_REPLY_FUTURES

# 加载所有图片，返回一个字典 {1: b64_str, 2: b64_str, ...}
def load_all_case2_images(case_name: str = "case2", num_images: int = 15) -> Dict[int, str]:
    """加载指定测试用例的所有截图并转换为 Base64。"""
    images = {}
    # 假设 test/case2/ 位于项目根目录下
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'test', case_name))
    for i in range(1, num_images + 1):
        path = os.path.join(base_dir, f"{i}.jpg")
        if os.path.exists(path):
            # 注意：返回纯 Base64 字符串
            images[i] = load_image_b64(path) 
        else:
            images[i] = "PLACEHOLDER_B64"
            print(f"Warning: Missing image file: {path}. Using placeholder.")
    return images

def load_image_b64(path: str) -> str:
    """加载图片并转换为 Base64 字符串"""
    if not os.path.exists(path):
        # 如果文件不存在，返回一个简单的占位符 Base64 字符串
        return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYGD4DwAADgAEEhAAaAAAAABJRU5ErkJggg=="
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")
    
# 预加载所有图片
MOCK_IMAGES_CASE2 = load_all_case2_images(case_name="case2", num_images=15)

# 预加载测试图片
MOCK_SCREENSHOT_1_B64 = load_image_b64("test/case1/1.jpg") # 初始桌面
MOCK_SCREENSHOT_2_B64 = load_image_b64("test/case1/2.jpg") # 抖音启动后

def get_complex_client_script() -> List[Dict[str, Any]]:
    """为复杂任务生成 Client Stub 的回复序列。"""
    script = []
    # 共有 14 个外部动作 (Action 1 到 Action 14) 导致图片变化
    for i in range(1, 15):
        # Action i 发生后，Client 回复下一张图片 (i+1)
        script.append({
            "screenshot_b64": MOCK_IMAGES_CASE2.get(i + 1, "MISSING"), 
            "width": 1080, "height": 1920,
            "note": f"Action {i} executed. Replied with image {i+1}.jpg."
        })
    return script

class A2AEventSink:
    """
    模拟 L2 Node.js 客户端的事件接收器和回复器。
    它接收 L1 Agent 的事件流 (通过 Queue)，并处理其中的动作请求。
    """
    def __init__(self, task_id: int, mock_reply_script: List[Dict[str, Any]]):
        self.task_id = task_id
        self.mock_reply_script = mock_reply_script
        self.reply_counter = 0
        self.received_events: List[Dict[str, Any]] = []
        # L1 Agent 会将事件推送到这个队列
        self.event_queue = asyncio.Queue() 

    async def run_event_loop(self):
        """
        异步地从队列中读取事件，并在收到 action_request 时，
        将回复推送到 L1 Agent 的 Future 中。
        """
        while True:
            try:
                # 设置超时，防止无限阻塞
                event = await asyncio.wait_for(self.event_queue.get(), timeout=120) 
            except asyncio.TimeoutError:
                break # 如果 120 秒没有新事件，则任务可能已完成或出错
            
            self.received_events.append(event)
            
            if event.get('type') == 'action_request':
                if self.reply_counter >= len(self.mock_reply_script):
                    # 模拟脚本耗尽，等待 L1 Agent 达到 Max Step 结束
                    continue 

                # 1. 获取 L1 Agent 正在等待的 Future
                # 注意：L1 Agent 必须在推送 'action_request' 之前创建好 Future
                reply_future = ACTION_REPLY_FUTURES.get(self.task_id)
                
                if not reply_future or reply_future.done():
                    # 致命错误：L1 Agent 没有在等待
                    raise Exception(f"Task {self.task_id} received action request but Future not found/ready.")

                # 2. 获取并处理 Mock 回复
                reply_data = self.mock_reply_script[self.reply_counter]
                self.reply_counter += 1
                
                # 3. 模拟 L2 通过 HTTP POST /reply 接口回复
                # ❗ 关键：设置 Future 的结果，解除 L1 Agent 内部的阻塞 ❗
                reply = {
                    "type": "action_reply",
                    "screenshot_b64": reply_data["screenshot_b64"],
                    "screenshot_width": reply_data["width"],
                    "screenshot_height": reply_data["height"],
                    "note": reply_data["note"] # 仅用于日志
                }

                reply_future.set_result(reply)
                self.logger.info(f"[Fake L2 Reply] Replied to Future. Action count: {self.reply_counter}")

            if event.get('type') == 'task_finalized':
                break
            
class A2AClientStub(A2AInterfaceBase):
    """
    Mock A2A Client 的行为，用于单元测试 Python A2A Server 的执行循环。
    它模拟了 Client 接收动作请求 -> 执行 ADB -> 返回截图的过程。
    """
    
    def __init__(self, task_id: int, logger: logging.Logger, mock_script: Optional[List[Dict[str, Any]]] = None):
        super().__init__(task_id)
        self.logger = logger
        self.action_counter = 0

        # 如果传入了 mock_script，则使用它；否则使用旧的默认脚本 (兼容 case1)
        self.mock_script: List[Dict[str, Any]] = mock_script if mock_script is not None else get_complex_client_script()

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
    
MANAGER_PLAN_MOCK_1 = """
### Thought ###
The user wants to open the Douyin app, which is visible on the home screen...
### Plan ###
1. Locate the Douyin app icon on the home screen.
2. Tap on the Douyin app icon to open it.
3. Perform the `answer` action.
"""

EXECUTOR_ACTION_MOCK_1 = """
### Thought ###
The user wants to open the Douyin app. The Douyin app icon is visible on the home screen, so the next logical step is to tap on it to open the application.
### Action ###
{"action": "click", "coordinate": [792, 1245]}
### Description ###
Tap on the Douyin app icon.
"""

REFLECTOR_OUTCOME_1 = """
### Outcome ###
A
### Error Description ###
None
"""

NOTETAKER_NOTES_1 = """
### Important Notes ###
Successfully launched the Douyin app. The current screen shows a video playing.
"""

MANAGER_PLAN_MOCK_2 = """
### Thought ###
The previous action successfully opened Douyin. The current task is "打开抖音" and the plan contains "Perform the `answer` action" as the final step. I will execute the answer action now.
### Historical Operations ###
1. Locate the Douyin app icon on the home screen. 2. Tap on the Douyin app icon to open it.
### Plan ###
1. Perform the `answer` action.
"""
EXECUTOR_ACTION_MOCK_2 = """
### Thought ###
The task is complete, I should output the answer action now.
### Action ###
{"action": "answer", "text": "抖音已成功打开。"}
### Description ###
Report the successful completion of the task.
"""

class VLMStub:
    """
    Mock VLM Wrapper，用于单元测试 Agent 的决策逻辑。
    """
    def __init__(self, script: List[str]):
        # script 是预设的 VLM 响应列表，按 Manager, Executor, Reflector 的顺序
        self.script: List[str] = [
            MANAGER_PLAN_MOCK_1,      # Step 1: Manager 规划 (输入图 1)
            EXECUTOR_ACTION_MOCK_1,   # Step 2: Executor 动作 (输入图 1)
            REFLECTOR_OUTCOME_1,      # Step 3: Reflector 反思 (输入图 1/2)
            NOTETAKER_NOTES_1,        # Step 4: Notetaker 记忆 (输入图 2)
            MANAGER_PLAN_MOCK_2,      # Step 5: Manager 再次规划 (输入图 2)
            EXECUTOR_ACTION_MOCK_2    # Step 6: Executor 最终回答 (输入图 2)
            # 总共 6 次 VLM 调用
        ]
        self.call_counter = 0

    async def predict_mm(self, prompt: str, image_inputs: List[Any]) -> Tuple[str, Any, Any]:
        """
        模拟 VLM 的异步调用。
        每次调用返回 script 中的下一个预设响应。
        """
        # 0. VLM 调用计数和脚本推进
        if self.call_counter >= len(self.script):
            return "Finished", None, True

        current_call = self.call_counter
        response = self.script[current_call]
        MIN_B64_LENGTH = 1000
        
        # Step 1 (Manager M1) 和 Step 2 (Executor E1) 只接收初始截图
        if current_call in [0, 1]: 
            # 预期: [MOCK_SCREENSHOT_1_B64]
            assert len(image_inputs) == 1, f"Call {current_call}: Manager/Executor M1/E1 预期只接收 1 张截图。"
        
        # Step 3 (Reflector R1) 接收动作前后两张截图
        elif current_call == 2: 
            # 预期: [MOCK_SCREENSHOT_1_B64, MOCK_SCREENSHOT_2_B64]
            assert len(image_inputs) == 2, f"Call {current_call}: Reflector 预期接收 2 张截图。"
            # 我们可以更进一步，检查第二张图是否是 Client 返回的图
            # 由于 Client 返回的 Base64 字符串很大，我们只检查起始部分
            assert isinstance(image_inputs[0], str) and len(image_inputs[0]) > MIN_B64_LENGTH, f"Call {current_call}: R1 动作前截图数据为空或格式错误。"
            assert isinstance(image_inputs[1], str) and len(image_inputs[1]) > MIN_B64_LENGTH, f"Call {current_call}: R1 动作后截图数据为空或格式错误。"
            
        # Step 4 (Notetaker N1) 和 Step 5 (Manager M2) 接收动作后截图
        elif current_call in [3, 4]:
            # 预期: [MOCK_SCREENSHOT_2_B64]
            assert len(image_inputs) == 1, f"Call {current_call}: Notetaker/Manager M2 预期只接收 1 张截图。"
            # 检查收到的截图是否是 Client 返回的第二张图
            assert isinstance(image_inputs[0], str) and len(image_inputs[0]) > MIN_B64_LENGTH, f"Call {current_call}: E2 接收的截图数据为空或格式错误。"
            
        self.call_counter += 1
        return response, None, True