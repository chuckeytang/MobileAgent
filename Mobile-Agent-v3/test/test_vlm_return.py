# test_vlm_return.py (修改后的异步测试脚本，获取 Manager 和 Executor 的 Mock 值)

import json
import os
import requests
import base64
from PIL import Image
from io import BytesIO
import asyncio
from typing import Dict, Any, Tuple, Optional

# 假设这两个文件在您的环境中可以直接导入
from mobile_v3.utils.mobile_agent_e import InfoPool, Manager, Executor
from mobile_v3.utils.call_mobile_agent_e import GUIOwlWrapper 

# --- VLM 配置 (请替换为您的实际值) ---
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

async def test_agent_vlm_calls():
    """模拟 Manager 和 Executor Agent 的连续 VLM 调用"""
    
    # 1. 准备多模态输入
    try:
        image = Image.open(IMAGE_PATH)
        image_b64 = pil_to_base64(image)
        image_inputs = [image_b64] 
    except FileNotFoundError:
        print(f"错误: 找不到图片文件 {IMAGE_PATH}。请确保文件路径正确。")
        return
        
    vllm_wrapper = GUIOwlWrapper(VLM_API_KEY, VLM_BASE_URL, VLM_MODEL)
    info_pool = InfoPool(instruction=TASK_INSTRUCTION)
    manager = Manager()
    executor = Executor()
    
    results = {}
    
    # =================================================================
    # A. 第一步：获取 Manager Agent 的 Mock 返回值
    # =================================================================
    print("\n\n##################################################")
    print("--- A. Manager Agent 调用 (获取 Plan Mock) ---")
    print("##################################################")
    
    prompt_manager = manager.get_prompt(info_pool)
    print(f"Manager Prompt (部分):\n{prompt_manager[:500]}...")

    try:
        output_manager, _, _ = await vllm_wrapper.predict_mm(
            prompt_manager, image_inputs 
        )
        
        if not output_manager:
             print("!!! 警告: Manager VLM 返回空回复。")
             return

        print("\n--- Manager VLM 原始回复 ---")
        results['manager_raw'] = output_manager
        print(output_manager)

        print("\n--- Manager Agent 解析结果 ---")
        parsed_manager = manager.parse_response(output_manager)
        info_pool.plan = parsed_manager['plan']
        info_pool.completed_plan = parsed_manager['completed_subgoal']
        print(json.dumps(parsed_manager, indent=4, ensure_ascii=False))

    except Exception as e:
        print(f"\n!!! 致命错误: Manager VLM 调用失败: {e}")
        return
        
    # =================================================================
    # B. 第二步：获取 Executor Agent 的 Mock 返回值
    # =================================================================
    print("\n\n##################################################")
    print("--- B. Executor Agent 调用 (获取 Action Mock) ---")
    print("##################################################")

    # 确保 Manager 成功规划后再调用 Executor
    if "Finished" in info_pool.plan.strip():
        print("Manager 已返回 Finished，跳过 Executor 调用。")
        return

    prompt_executor = executor.get_prompt(info_pool)
    print(f"Executor Prompt (部分):\n{prompt_executor[:500]}...")
    
    try:
        output_executor, _, _ = await vllm_wrapper.predict_mm(
            prompt_executor, image_inputs 
        )
        
        if not output_executor:
             print("!!! 警告: Executor VLM 返回空回复。")
             return
             
        print("\n--- Executor VLM 原始回复 ---")
        results['executor_raw'] = output_executor
        print(output_executor)

        print("\n--- Executor Agent 解析结果 ---")
        parsed_executor = executor.parse_response(output_executor)
        print(json.dumps(parsed_executor, indent=4, ensure_ascii=False))
        
        # 提取 Action JSON 以供单元测试 Mock
        print("\n--- 提取的 Action JSON 字符串 (单元测试 Mock 目标) ---")
        action_json_str = parsed_executor['action'].replace("```", "").replace("json", "").strip()
        print(action_json_str)

    except Exception as e:
        print(f"\n!!! 致命错误: Executor VLM 调用失败: {e}")
        return
        
    # =================================================================
    # C. 总结 Mock 值
    # =================================================================
    print("\n\n====================================================")
    print("--- 单元测试 Mock 值总结 ---")
    print("====================================================")
    print("请将以下原始回复复制到 VLMStub.script 列表中:")
    print("1. Manager Mock 值 (用于 Manager Agent):")
    print(results.get('manager_raw', 'N/A'))
    print("\n2. Executor Mock 值 (用于 Executor Agent):")
    print(results.get('executor_raw', 'N/A'))
    
if __name__ == "__main__":
    if not os.path.exists(IMAGE_PATH):
        print(f"请先确保图片文件 {IMAGE_PATH} 存在！")
    else:
        # 使用 asyncio 运行异步测试函数
        try:
            asyncio.run(test_agent_vlm_calls())
        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                # 忽略某些环境下 event loop 已关闭的错误
                pass
            else:
                raise