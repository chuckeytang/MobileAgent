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
ACTIVE_TASK_CONTEXTS = {}
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


# -------------------- SSE 流式处理核心逻辑 --------------------

async def _handle_stream_logic(a2a_message: dict) -> StreamingResponse:
    """
    接收并处理 A2A Message，启动 V3 任务，并通过 SSE 推送 V3 Agent 的实时事件。
    这是原 send_stream 函数的逻辑主体。
    """
    global TASK_COUNTER
    
    # 1. 提取参数
    instruction_part = next((p for p in a2a_message.get('parts', []) if p.get('kind') == 'text'), None)
    if not instruction_part:
        raise HTTPException(status_code=400, detail="A2A Message missing 'text' part for instruction.")
    instruction = instruction_part['text']

    screenshot_part = next((p for p in a2a_message.get('parts', []) if p.get('kind') == 'data' and p.get('contentType') == 'image/png'), None)
    if not screenshot_part:
        raise HTTPException(status_code=400, detail="A2A Message missing 'image/png' part for initial screenshot.")
    initial_screenshot_b64 = screenshot_part['data']

    # 2. 初始化 V3 任务
    TASK_COUNTER += 1
    l1_task_id = TASK_COUNTER
    
    # 获取 VLM 配置 (必须在全局或通过依赖注入获取)
    vlm_api_key = os.environ.get("VLM_API_KEY", "") 
    vlm_base_url = os.environ.get("VLM_BASE_URL", "http://localhost:6001/v1")
    vlm_model = os.environ.get("VLM_MODEL", "iic/GUI-Owl-7B")

    async def event_generator():
        event_queue = asyncio.Queue()
        
        # 启动 Mobile Agent V3 任务（异步执行）
        task_future = asyncio.ensure_future(
            MobileAgentTaskExecutor.execute_task_a2a_mode(
                l1_task_id, 
                instruction, 
                initial_screenshot_b64, 
                event_queue,
                api_key=vlm_api_key, 
                base_url=vlm_base_url, 
                model=vlm_model,
            )
        )
        
        ACTIVE_TASK_CONTEXTS[l1_task_id] = {'queue': event_queue, 'future': task_future}

        try:
            # 初始状态更新 (A2A 协议)
            yield create_a2a_status_update(l1_task_id, 'running')
            
            while True:
                v3_event = await event_queue.get()
                a2a_event = V3_to_A2A_Event(l1_task_id, v3_event)
                
                if a2a_event:
                    yield create_a2a_message_stream_event(a2a_event)

                if a2a_event.get('kind') == 'status-update' and a2a_event['data'].get('final'):
                    break

        except asyncio.CancelledError:
            logger.warn(f"Task {l1_task_id} stream cancelled by client.")
        except Exception as e:
            logger.error(f"Error during A2A task stream for {l1_task_id}: {e}")
            yield create_a2a_status_update(l1_task_id, 'failed', final=True, error=str(e))
        finally:
            task_future.cancel()
            ACTIVE_TASK_CONTEXTS.pop(l1_task_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/")
async def handle_a2a_rpc_root(request: Request):
    """
    捕获 Node.js SDK 发送到顶级 URL 的 RPC 请求，并根据 method 字段进行分发。
    """
    raw_body = await request.body()
    try:
        rpc_request = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.error(f"Failed to decode JSON. Raw Body: {raw_body.decode('utf-8')[:200]}...")
        raise HTTPException(status_code=400, detail="Invalid JSON format.")
    
    method = rpc_request.get("method")
    logger.info(f"RPC Method: {method}, Request ID: {rpc_request.get('id')}")
    logger.debug(f"Full RPC Payload: {json.dumps(rpc_request, indent=2)}")
    
    # 检查是否是流式消息发送请求
    if method == "message/stream":
        params = rpc_request.get("params")
        if not params:
            logger.error("RPC Payload is missing the 'params' field.")
            raise HTTPException(status_code=400, detail="RPC Payload is missing 'params' field.")
            
        a2a_message = params.get("message")
        
        if not a2a_message:
            logger.error("RPC Payload 'params' is missing the 'message' field.")
            raise HTTPException(status_code=400, detail="RPC Payload is missing 'message' field in params.")
        
        return await _handle_stream_logic(a2a_message)

    # 检查是否是 tasks/cancel 请求
    if method == "tasks/cancel":
        params = rpc_request.get("params")
        task_id = params.get("id")
        # 您需要在这里实现任务取消逻辑
        logger.info(f"Received tasks/cancel request for Task {task_id}")
        return JSONResponse({"jsonrpc": "2.0", "result": {"id": task_id, "status": "canceled"}, "id": rpc_request.get("id")})
        
    raise HTTPException(status_code=405, detail=f"RPC Method '{method}' not supported.")

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
    VLM_BASE_URL = os.environ.get("VLM_BASE_URL", "http://localhost:6001/v1")
    VLM_MODEL = os.environ.get("VLM_MODEL", "iic/GUI-Owl-7B")
    
    logger.info("Starting Mobile-Agent-v3 A2A Server...")
    
    # Uvicorn 运行应用
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=6006, 
        log_level="info"
    )