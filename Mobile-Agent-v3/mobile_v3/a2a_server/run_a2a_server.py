# mobile_v3/a2a_server/run_a2a_server.py 

import json
import asyncio
import logging
import os
import sys
from typing import Any
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
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
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

class DebugLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        
        # 1. 打印请求路径和方法
        logger.info(f"[DEBUG LOG] REQUEST START: {request.method} {request.url.path}")

        # 2. 只有在 POST 请求时才尝试读取 Body，并使用 try/except 确保不会崩溃
        if request.method == "POST":
            try:
                # 重新封装一个 Body 副本，以供下游路由（handle_a2a_rpc_root）再次读取
                # 这是一个标准的 Fast API 模式，用于在中间件中消费 Body
                request.state.body = await request.body()
                
                # 解析并打印 Payload
                payload = json.loads(request.state.body)
                logger.info(f"[DEBUG LOG] POST Payload Peek: {payload.get('method')}")
                # logger.debug(f"[DEBUG LOG] Full Payload: {json.dumps(payload, indent=2)}")
                
                # 将 body 副本放回流中，供 handle_a2a_rpc_root 再次读取
                request.scope["body"] = request.state.body
            except json.JSONDecodeError as e:
                logger.error(f"[DEBUG LOG] JSON Decode Failed (Middleware). Body: {request.state.body.decode('utf-8')[:100]}...")
                # ❗ 关键：如果解析失败，直接返回 400 错误，并附带详情 ❗
                return JSONResponse(
                    status_code=400, 
                    content={"detail": "Invalid JSON-RPC Payload format.", "error": str(e)}
                )
            except Exception as e:
                logger.error(f"[DEBUG LOG] Unexpected error reading body: {e}")
                
        response = await call_next(request)
        logger.info(f"[DEBUG LOG] REQUEST END: {response.status_code}")
        return response

app.add_middleware(DebugLogMiddleware)

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

async def _handle_stream_logic(a2a_message: dict, rpc_id: Any) -> StreamingResponse:
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

    # 1. 查找 kind == 'file' 的部分
    screenshot_part_file = next((p for p in a2a_message.get('parts', []) if p.get('kind') == 'file'), None)
    if not screenshot_part_file:
        error_detail = "A2A Message missing 'file' part for initial screenshot (V3 requires initial image)."
        logger.error(f"STREAM START FAILED: {error_detail}") 
        raise HTTPException(status_code=400, detail=error_detail)
    
    # 2. 尝试提取 Base64 和 MimeType
    try:
        # 提取 'bytes' 属性
        initial_screenshot_b64 = screenshot_part_file['file']['bytes']
        mimetype = screenshot_part_file['file'].get('mimeType') 
    except KeyError:
        # 如果 FilePart 结构不完整 (如缺少 'file' 或 'bytes')
        error_detail = "A2A Message FilePart is malformed (missing 'file' or 'bytes' field)."
        logger.error(f"STREAM START FAILED: {error_detail}") 
        raise HTTPException(status_code=400, detail=error_detail)
    
    # 3. 检查 MIME Type (可选但推荐)
    if not mimetype or not mimetype.startswith('image/'):
        logger.warn(f"Received unexpected MIME type: {mimetype}. Continuing with image data.")

    # 4. 初始化 V3 任务
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
            initial_status_event = create_a2a_status_update(l1_task_id, 'running')
            yield create_a2a_message_stream_event(initial_status_event, rpc_id)
            
            while True:
                v3_event = await event_queue.get()
                logger.debug(f"Task {l1_task_id} [event_generator] received v3_event: {v3_event}")
                if not v3_event or not isinstance(v3_event, dict):
                    logger.warn(f"Task {l1_task_id} [event_generator] received an invalid/empty event. Skipping.")
                    continue

                a2a_event = V3_to_A2A_Event(l1_task_id, v3_event)
                logger.debug(f"Task {l1_task_id} [event_generator] converted to a2a_event: {a2a_event}")
                
                if a2a_event:
                    # (a) 发送事件
                    yield create_a2a_message_stream_event(a2a_event, rpc_id)

                    # (b) 检查是否为终止事件 (合并到同一个 'if' 块中)
                    if a2a_event.get('kind') == 'status-update' and a2a_event.get('status', {}).get('final'):
                        logger.info(f"Task {l1_task_id} received final status event. Closing stream.")
                        break
                else:
                    # 如果 V3_to_A2A_Event 返回了 None (例如，我们决定忽略某个事件)
                    logger.warn(f"Task {l1_task_id} [event_generator] V3_to_A2A_Event returned None for {v3_event.get('type')}. Event ignored.")

        except asyncio.CancelledError:
            logger.warn(f"Task {l1_task_id} stream cancelled by client.")
        except Exception as e:
            logger.error(f"Error during A2A task stream for {l1_task_id}: {e}", exc_info=True)
            logger.error(f"Failing v3_event was: {v3_event}")
            final_status_event = create_a2a_status_update(l1_task_id, 'failed', final=True, error=str(e))
            yield create_a2a_message_stream_event(final_status_event, rpc_id)
        finally:
            task_future.cancel()
            ACTIVE_TASK_CONTEXTS.pop(l1_task_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/")
async def handle_a2a_rpc_root(request: Request):
    """
    捕获 Node.js SDK 发送到顶级 URL 的 RPC 请求，并根据 method 字段进行分发。
    """
    try:
        raw_body = await request.body()
    except Exception as e:
        logger.error(f"Failed to read raw request body: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error while reading request.")
    
    try:
        rpc_request = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.error(f"Failed to decode JSON. Raw Body: {raw_body.decode('utf-8')[:200]}...")
        raise HTTPException(status_code=400, detail="Invalid JSON format.")
    
    method = rpc_request.get("method")
    rpc_id = rpc_request.get("id")
    logger.info(f"RPC Method: {method}, Request ID: {rpc_id}")
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
        
        return await _handle_stream_logic(a2a_message, rpc_id)

    # 检查是否是 tasks/cancel 请求
    if method == "tasks/cancel":
        params = rpc_request.get("params")
        task_id = params.get("id")
        # 您需要在这里实现任务取消逻辑
        logger.info(f"Received tasks/cancel request for Task {task_id}")
        return JSONResponse({"jsonrpc": "2.0", "result": {"id": task_id, "status": "canceled"}, "id": rpc_request.get("id")})
        
    raise HTTPException(status_code=405, detail=f"RPC Method '{method}' not supported.")

@app.post("/v1/tasks/{l1_task_id}/reply")
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