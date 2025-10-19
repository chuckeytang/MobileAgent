# mobile_v3/a2a_server/a2a_state.py

import asyncio
from typing import Dict, Any

# 任务 ID 计数器
TASK_COUNTER: int = 1000

# 活跃任务的上下文 {task_id: {'queue': asyncio.Queue, 'future': asyncio.Future}}
ACTIVE_TASK_CONTEXTS: Dict[int, Dict[str, Any]] = {}

# 等待 L2 回复的 Futures {task_id: asyncio.Future}
ACTION_REPLY_FUTURES: Dict[int, asyncio.Future] = {}

def get_action_reply_future(task_id: int) -> asyncio.Future:
    """
    获取或创建用于接收 L2 动作回复的 Future。
    """
    if task_id not in ACTION_REPLY_FUTURES or ACTION_REPLY_FUTURES[task_id].done():
        ACTION_REPLY_FUTURES[task_id] = asyncio.Future()
    return ACTION_REPLY_FUTURES[task_id]