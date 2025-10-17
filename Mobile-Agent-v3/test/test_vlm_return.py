# test_vlm_return.py (临时测试脚本)
import json
import os
import requests
import base64
from PIL import Image
from io import BytesIO

# 假设这两个文件在您的环境中可以直接导入
from mobile_v3.utils.mobile_agent_e import InfoPool, Manager
from mobile_v3.utils.call_mobile_agent_e import GUIOwlWrapper # 假设这个 Wrapper 封装了 VLM 调用逻辑

# --- VLM 配置 ---
VLM_API_KEY = "" 
VLM_BASE_URL = "http://region-9.autodl.pro:50134" 
VLM_MODEL = "iic/GUI-Owl-7B"

# --- 任务和图片 ---
TASK_INSTRUCTION = "打开抖音。"
IMAGE_PATH = "case1/1.jpg" # 初始截图 (手机桌面)

def pil_to_base64(image):
    """将 PIL 图像转换为 Base64 字符串"""
    buffer = BytesIO()
    image.save(buffer, format="PNG") 
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

def test_manager_vlm_call():
    """模拟 Manager Agent 的首次 VLM 调用"""
    print("--- 1. 初始化 Agent 状态 ---")
    info_pool = InfoPool(instruction=TASK_INSTRUCTION)
    manager = Manager()
    
    # 模拟 VLM Wrapper (用于调用 VLM 服务)
    vllm_wrapper = GUIOwlWrapper(VLM_API_KEY, VLM_BASE_URL, VLM_MODEL)
    
    # 1.1 生成 Manager Agent 的 Prompt
    prompt = manager.get_prompt(info_pool)
    print(f"生成的 Manager Prompt (部分):\n{prompt[:500]}...")

    # 1.2 准备多模态输入
    try:
        image = Image.open(IMAGE_PATH)
        image_b64 = pil_to_base64(image)
        # GUIOwlWrapper.predict_mm 接收的是文件路径列表或 Base64 列表
        image_inputs = [image_b64] 
    except FileNotFoundError:
        print(f"错误: 找不到图片文件 {IMAGE_PATH}。请确保文件在当前目录下。")
        return
        
    print("\n--- 2. 调用 VLM Service ---")
    
    try:
        # GUIOwlWrapper.predict_mm 是 Mobile-Agent-v3 中调用 VLM 的核心方法
        # 它应该返回 output_planning (VLM 原始文本回复)
        output_planning, _, _ = vllm_wrapper.predict_mm(
            prompt,
            image_inputs 
        )
        
        print("\n--- 3. 接收 VLM 原始回复 ---")
        if not output_planning:
             print("!!! 警告: VLM 返回空回复。请检查 VLM 服务状态和 API Key。")
             return

        print(f"VLM 原始回复:\n{output_planning}")

        print("\n--- 4. 尝试解析 (Manager.parse_response) ---")
        parsed_result = manager.parse_response(output_planning)
        
        print(json.dumps(parsed_result, indent=4, ensure_ascii=False))

        return output_planning

    except Exception as e:
        print(f"\n!!! 致命错误: VLM 调用失败: {e}")
        # 如果 vllm_wrapper 内部使用 requests，可能是网络或 JSON 解析错误
        print("请确保 VLM_BASE_URL 和 VLM_API_KEY 配置正确，且服务正在运行。")
        return

if __name__ == "__main__":
    if not os.path.exists(IMAGE_PATH):
        print("请先将 1.jpg 文件放在此脚本同级目录下！")
    else:
        vlm_raw_response = test_manager_vlm_call()
        
        if vlm_raw_response:
            # --- 5. 获取最终的 Mock 返回值 ---
            print("\n==============================================")
            print("--- 成功获取 VLM 的 Manager Agent Mock 返回值 ---")
            print("==============================================")
            # 这里的 raw_response 就是您单元测试需要 Mock 的值
            print(vlm_raw_response)