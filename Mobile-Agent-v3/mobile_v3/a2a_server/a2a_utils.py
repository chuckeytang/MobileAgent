# mobile_v3/a2a_server/a2a_utils.py

import logging
import os
import sys
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

# =========================================================================
# 1. 任务日志隔离设置
# =========================================================================

def setup_task_logger(task_id: int) -> logging.Logger:
    """为每个任务设置独立的 Logger，并输出到独立的文件。"""
    log_file = f"logs/a2a_task_{task_id}.log"
    
    # 确保 logs 目录存在
    os.makedirs('logs', exist_ok=True)
    
    logger_name = f'TaskLogger_{task_id}'
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.handlers = [] # 清除旧的 handlers

    # 文件 Handler (将日志输出到独立文件)
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    )
    logger.addHandler(file_handler)

    # Console Handler (保留控制台输出)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        logging.Formatter('%(asctime)s - [%(name)s] - %(levelname)s - %(message)s')
    )
    logger.addHandler(console_handler)
    
    return logger

# =========================================================================
# 2. A2A 事件格式化 (用于推送给 Client)
# =========================================================================

def create_a2a_event(event_type: str, task_id: int, content: Dict[str, Any]) -> Dict[str, Any]:
    """格式化一个标准的 A2A 事件用于 Event Stream 推送。"""
    return {
        "kind": "custom_event",
        "type": event_type,
        "taskId": task_id,
        "timestamp": datetime.now().isoformat(),
        "metadata": content,
    }

def create_action_request_event(task_id: int, action_json: str, thought: str) -> Dict[str, Any]:
    """创建请求 Client 执行动作的特殊事件。"""
    # 注意：action_json 已经是 Executor Agent 的 JSON 输出
    return create_a2a_event("action_request", task_id, {
        "thought": thought,
        "action_json": action_json,
        "wait_for_reply": True,
        "description": "Client must execute this action and reply with new screenshot.",
    })

def create_a2a_status_update(task_id: int, status: str, final: bool = False, error: Optional[str] = None) -> Dict[str, Any]:
    """
    创建标准的 A2A 状态更新事件 (kind: 'status-update')。
    """
    event = {
        "kind": "status-update",
        "timestamp": datetime.now().isoformat(),
        "id": str(task_id),
        "status": {
            "state": status, # 'running', 'completed', 'failed', 'canceled'
            "final": final
        }
    }
    if error:
        event["status"]["error"] = error
    return event

def create_a2a_message_stream_event(a2a_event: Dict[str, Any], rpc_id: Any) -> str:
    """
    将 A2A 事件字典封装为 SSE (Server-Sent Events) 格式。
    A2A 协议要求流式事件作为 JSON-RPC 2.0 响应的 'result' 字段。
    """
    
    # 1. 构建 JSON-RPC 响应体
    rpc_response = {
        "jsonrpc": "2.0",
        "id": rpc_id,      # <-- 匹配 L2 Client 的请求 ID
        "result": a2a_event # <-- A2A 事件本身 (e.g., status-update)
    }

    # 2. 将响应体转换为 JSON 字符串
    json_data = json.dumps(rpc_response)
    
    # 3. 格式化为 SSE 消息 (data: [JSON])
    return f"data: {json_data}\n\n"

def V3_to_A2A_Event(task_id: int, v3_event: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 Mobile Agent V3 的内部事件转换为 A2A 标准事件。
    """
    v3_type = v3_event.get('type')
    v3_data = v3_event.get('metadata')

    # 宏观的 Manager Plan 或 Reflector Outcome，映射为 A2A 消息或状态更新
    if v3_type == 'manager_plan':
        # 映射为 A2A 的 'message' 或 'artifact-update'
        return {
            "kind": "message",
            "timestamp": datetime.now().isoformat(),
            "id": str(task_id),
            "role": "assistant",
            "parts": [{
                "kind": "text",
                "text": f"Manager Plan: {v3_data.get('plan', '')}"
            }]
        }
    
    elif v3_type == 'action_request':
        # 动作请求，通常在 A2A 中也是一个 Message
        return {
            "kind": "tool_call_request", # 建议使用自定义的 kind，更清晰
            "timestamp": datetime.now().isoformat(),
            "taskId": task_id,
            # 将 L1 Agent 动作JSON和思考作为核心数据传递
            "data": v3_data 
        }

    elif v3_type == 'task_finalized':
        # 任务结束，映射为最终状态更新
        status = v3_data.get('status', 'completed')
        return create_a2a_status_update(task_id, status, final=True)
    
    # 其他 V3 事件（如 'action_reflection', 'important_notes'）可以根据需要映射或忽略

    # 默认返回原始事件（如果 A2A Client Service 知道如何处理）
    return {
        "kind": "message",
        "timestamp": datetime.now().isoformat(),
        "id": str(task_id),
        "role": "system",
        "parts": [{
            "kind": "text",
            "text": f"V3 Internal Event: {v3_type} - {v3_data}"
        }]
    }