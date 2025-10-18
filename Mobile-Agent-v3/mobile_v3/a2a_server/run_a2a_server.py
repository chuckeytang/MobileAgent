# mobile_v3/a2a_server/run_a2a_server.py 

import json
import asyncio
import logging
import os
import sys
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# 导入 A2A 标准卡片和 Mobile Agent 核心
from .agent_card import AGENT_CARD
from .mobile_agent_a2a import MobileAgentTaskExecutor # 待修改
# 导入 A2A 协议辅助函数（需要自行实现，例如：将 V3 事件转换为 A2A 事件）
from .a2a_utils import V3_to_A2A_Event, create_a2a_message_stream_event, create_a2a_status_update

app = FastAPI(title="Mobile-Agent-v3 A2A Server")
logger = logging.getLogger('A2AServer')

# 任务 ID 计数器和任务状态存储
TASK_COUNTER = 1000
ACTIVE_TASK_CONTEXTS = {} # 存储 {l1_task_id: {executor: MobileAgentTaskExecutor, task_future: Future}}
ACTION_REPLY_FUTURES = {}
# 获取或创建 Future
def get_reply_future(task_id):
    if task_id not in ACTION_REPLY_FUTURES or ACTION_REPLY_FUTURES[task_id].done():
        ACTION_REPLY_FUTURES[task_id] = asyncio.Future()
    return ACTION_REPLY_FUTURES[task_id]

# -------------------- A2A 标准端点 --------------------

@app.get("/.well-known/agent-card.json")
async def get_agent_card():
    """A2A 协议要求的代理卡片发现端点"""
    return JSONResponse(content=AGENT_CARD)

@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "agent": "MobileAgent-V3-A2A"}


@app.post("/v1/messages:sendStream")
async def send_stream(request: Request):
    """
    A2A 协议的核心流式通信端点。
    接收 A2A Message，启动 V3 任务，并通过 SSE 推送 V3 Agent 的实时事件。
    """
    global TASK_COUNTER

    try:
        # 1. 解析传入的标准 A2A Message
        # ❗ 注意：A2A Client 发送的消息体是 JSON 格式的 Message 对象
        a2a_message = await request.json()
        
        # 提取用户指令（假设 text/plain 在 parts[0]）
        instruction_part = next((p for p in a2a_message.get('parts', []) if p.get('kind') == 'text'), None)
        if not instruction_part:
            raise HTTPException(status_code=400, detail="A2A Message missing 'text' part for instruction.")
        instruction = instruction_part['text']

        # 提取初始截图（假设 image/png 在 parts[1]）
        screenshot_part = next((p for p in a2a_message.get('parts', []) if p.get('kind') == 'data' and p.get('contentType') == 'image/png'), None)
        if not screenshot_part:
            # ❗ 必须要求 A2A Client 在发起任务时提供初始截图 ❗
            raise HTTPException(status_code=400, detail="A2A Message missing 'image/png' part for initial screenshot.")
        initial_screenshot_b64 = screenshot_part['data'] 

    except Exception as e:
        logger.error(f"Failed to parse A2A Message: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid A2A Message format: {e}")

    # 2. 初始化 V3 任务
    TASK_COUNTER += 1
    l1_task_id = TASK_COUNTER
    
    # ❗ 这里的 executor 需要重新初始化，以适配 A2A 的异步/非阻塞模型 ❗
    # 假设 MobileAgentTaskExecutor 可以被重用或以某种方式初始化
    # executor = MobileAgentTaskExecutor(API_KEY, BASE_URL, MODEL) 
    # 简化：使用一个全局或配置的 Executor
    
    # 3. 启动 V3 任务并返回 SSE 响应
    async def event_generator():
        # 队列用于接收 MobileAgentTaskExecutor 推送的 V3 内部事件
        event_queue = asyncio.Queue()
        
        # 启动 Mobile Agent V3 任务（现在必须是非阻塞的）
        # ❗ execute_task 方法需要被修改，它不再阻塞，而是将事件推送到 event_queue ❗
        task_future = asyncio.ensure_future(
            MobileAgentTaskExecutor.execute_task_a2a_mode(
                l1_task_id, 
                instruction, 
                initial_screenshot_b64, 
                event_queue, # 传入队列以接收事件
            )
        )
        
        ACTIVE_TASK_CONTEXTS[l1_task_id] = {'queue': event_queue, 'future': task_future}

        try:
            # 初始状态更新 (A2A 协议)
            yield create_a2a_status_update(l1_task_id, 'running')
            
            while True:
                # 阻塞等待 V3 内部事件
                v3_event = await event_queue.get()
                
                # 4. 转换 V3 内部事件为 A2A 标准事件
                a2a_event = V3_to_A2A_Event(l1_task_id, v3_event)
                
                # 5. 推送 A2A 事件给客户端
                if a2a_event:
                    yield create_a2a_message_stream_event(a2a_event)

                # 任务完成或失败，退出循环
                if a2a_event.get('kind') == 'status-update' and a2a_event['data'].get('final'):
                    break

        except asyncio.CancelledError:
            logger.warn(f"Task {l1_task_id} stream cancelled by client.")
        except Exception as e:
            logger.error(f"Error during A2A task stream for {l1_task_id}: {e}")
            # 推送失败状态
            yield create_a2a_status_update(l1_task_id, 'failed', final=True, error=str(e))
        finally:
            # 清理
            task_future.cancel()
            ACTIVE_TASK_CONTEXTS.pop(l1_task_id, None)

    # 返回 SSE 响应 (text/event-stream)
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/v1/tasks/{l1_task_id}:reply")
async def receive_action_reply(l1_task_id: int, request: Request):
    """
    接收来自 L2 Client 的动作执行结果和新截图，解除 L1 Server 内部 Agent 的阻塞。
    """
    try:
        reply_data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON format.")
    
    if l1_task_id not in ACTION_REPLY_FUTURES:
        raise HTTPException(status_code=404, detail=f"Task {l1_task_id} not found or not awaiting reply.")
    
    future = ACTION_REPLY_FUTURES.pop(l1_task_id) # 移除 Future
    
    if not future.done():
        # 将 L2 传来的数据 (截图等) 传递给等待中的 L1 Agent
        future.set_result(reply_data) 
        return {"status": "ok", "message": "Reply received and task continued."}
    else:
        logger.warn(f"Reply received for Task {l1_task_id}, but the task has already continued.")
        return {"status": "warning", "message": "Task already processed or cancelled."}
    
try:
    import uvicorn
except ImportError:
    logger.error("Uvicorn is not installed. Please install with: pip install uvicorn")
    sys.exit(1)

if __name__ == "__main__":
    VLM_API_KEY = os.environ.get("VLM_API_KEY", "") 
    VLM_BASE_URL = os.environ.get("VLM_BASE_URL", "http://localhost:6007/v1")
    VLM_MODEL = os.environ.get("VLM_MODEL", "iic/GUI-Owl-7B")
    
    logger.info("Starting Mobile-Agent-v3 A2A Server...")
    
    # Uvicorn 运行应用
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=6006, 
        log_level="info"
    )