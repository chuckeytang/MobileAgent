# mobile_v3/a2a_server/run_a2a_server.py

import argparse
import asyncio
import logging
import os
from .mobile_agent_a2a import MobileAgentTaskExecutor

# 设置全局日志 (仅用于Server启动和错误信息)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('A2AServer')

# ---------------------------------------------------------------------
# 注意: 实际项目需要集成一个 A2A SDK 框架
# ---------------------------------------------------------------------

async def start_a2a_server(host: str, port: int, api_key: str, base_url: str, model: str):
    executor = MobileAgentTaskExecutor(api_key, base_url, model)
    
    logger.info(f"Starting Mobile-Agent-v3 A2A Server on {host}:{port}")
    logger.info(f"VLM Model: {model} at {base_url}")

    # -----------------------------------------------------------------
    # 核心: 在实际 A2A 框架中，这里会启动 HTTP/WebSocket 服务器
    # 并注册一个处理函数，该函数会为每个入站任务调用 execute_task
    # -----------------------------------------------------------------
    
    # 模拟启动一个任务来测试逻辑
    logger.info("MOCK: Simulating execution of a test task...")
    
    # 模拟任务参数 (在实际项目中，这些参数来自 Node.js 客户端的 A2A 请求)
    mock_task_id = 123
    mock_instruction = "在小红书上找到三个理发店并点赞。"
    # 模拟 Base64 初始截图 (Node.js 客户端应该在发起请求时提供)
    mock_initial_screenshot = "MOCK_B64_INITIAL_SCREENSHOT" 
    
    await executor.execute_task(
        task_id=mock_task_id, 
        instruction=mock_instruction,
        initial_screenshot_b64=mock_initial_screenshot,
        if_notetaker=True
    )
    
    logger.info("MOCK: Server running in background loop (Ctrl+C to stop)")
    # 实际应用中，这里会是服务器的主循环，永远不会退出
    await asyncio.Future() 


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Run Mobile-Agent-v3 A2A Server"
    )
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--api_key", type=str, required=True)
    parser.add_argument("--base_url", type=str, required=True)
    parser.add_argument("--model", type=str, required=True)
    
    args = parser.parse_args()
    
    # 检查 logs 目录是否存在
    os.makedirs('logs', exist_ok=True)

    try:
        asyncio.run(start_a2a_server(
            args.host, args.port, args.api_key, args.base_url, args.model
        ))
    except KeyboardInterrupt:
        logger.info("Server stopped by user.")