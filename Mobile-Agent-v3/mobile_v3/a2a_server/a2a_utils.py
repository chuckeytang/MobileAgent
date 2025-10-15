# mobile_v3/a2a_server/a2a_utils.py

import logging
import os
import sys
import json
import uuid
from datetime import datetime
from typing import Dict, Any

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