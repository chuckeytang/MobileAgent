# mobile_v3/a2a_server/agent_card.py (新增文件)
import os


AGENT_BASE_URL = os.environ.get(
    "MOBILE_V3_A2A_EXTERNAL_URL", 
    "http://localhost:6006" # 使用 6006 作为本地默认端口
)

AGENT_CARD = {
    "url": AGENT_BASE_URL,
    "version": "1.0",
    "name": "MobileAgent-V3-A2A",
    "description": "An Agent2Agent compliant wrapper for Mobile Agent V3 (GUI-Owl VLM). Executes UI automation tasks on mobile devices.",
    "agentId": "mobile-agent-v3",
    "capabilities": {
        "streaming": True,
        "messages:sendStream": {
            "description": "Send a Message to initiate a mobile automation task and receive a stream of status updates and artifacts.",
            "type": "StreamingMessage",
            "formats": ["text/plain", "image/png"],
            "artifacts": ["image/png", "text/json"], # 支持截图和 JSON 输出
            "streaming": True
        }
        # A2A 协议通常不直接包含 action_request / action_reply 的自定义接口，
        # 我们将在 messages:sendStream 内部处理动作流。
    },
    "endpoints": {
        "messages:sendStream": "/v1/messages:sendStream",
        "health": "/health"
    }
}