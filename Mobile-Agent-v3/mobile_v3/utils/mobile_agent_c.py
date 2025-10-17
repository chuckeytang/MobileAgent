from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import re

@dataclass
class InfoPool:
    """Keeping track of all information across the agents."""
    
    # User input / accumulated knowledge
    instruction: str = ""
    task_name: str = ""
    additional_knowledge_manager: str = ""
    additional_knowledge_executor: str = ""
    add_info_token = "[add_info]"
    
    ui_elements_list_before: str = "" # List of UI elements with index
    ui_elements_list_after: str = "" # List of UI elements with index
    action_pool: list = field(default_factory=list)

    # Working memory
    summary_history: list = field(default_factory=list)  # List of action descriptions
    action_history: list = field(default_factory=list)  # List of actions
    action_outcomes: list = field(default_factory=list)  # List of action outcomes
    error_descriptions: list = field(default_factory=list)

    last_summary: str = ""  # Last action description
    last_action: str = ""  # Last action
    last_action_thought: str = ""  # Last action thought
    important_notes: str = ""
    
    error_flag_plan: bool = False # if an error is not solved for multiple attempts with the executor
    error_description_plan: bool = False # explanation of the error for modifying the plan

    # Planning
    plan: str = ""
    completed_plan: str = ""
    progress_status: str = ""
    progress_status_history: list = field(default_factory=list)
    finish_thought: str = ""
    current_subgoal: str = ""
    # prev_subgoal: str = ""
    err_to_manager_thresh: int = 2

    # future tasks
    future_tasks: list = field(default_factory=list)

class BaseAgent(ABC):
    @abstractmethod
    def get_prompt(self, info_pool: InfoPool) -> str:
        pass
    @abstractmethod
    def parse_response(self, response: str) -> dict:
        pass

class Manager(BaseAgent):

    def get_prompt(self, info_pool: InfoPool) -> str:
        prompt = "您是一位能代表用户操作安卓手机的智能体。**请使用简体中文进行思考和输出，并严格遵循提供的格式。**您的目标是跟踪进度并制定实现用户请求的高级计划。\n\n"
        prompt += "请**务必**使用**简体中文**进行思考和输出。\n\n"
        prompt += "### 用户请求 ###\n"
        prompt += f"{info_pool.instruction}\n\n"

        task_specific_note = ""
        if ".html" in info_pool.instruction:
            task_specific_note = "注意: .html 文件可能包含额外的可交互元素，例如绘图画布(drawing canvas)或游戏(game)。在完成 .html 文件中的任务之前，请勿打开其他应用。"
        elif "Audio Recorder" in info_pool.instruction:
            task_specific_note = "注意: 停止录音图标是一个白色方块，位于底部从左数第四个。请不要点击中间的圆形暂停图标。"

        if info_pool.plan == "":
            # first time planning
            prompt += "---\n"
            prompt += "请制定一个高层次计划来完成用户请求。如果请求复杂，将其分解为子目标。屏幕截图显示了手机的起始状态。\n"
            prompt += "重要提示：对于明确要求回答的请求，请务必将“执行 `answer` 动作”作为计划的最后一步！\n\n"
            if task_specific_note != "":
                prompt += f"{task_specific_note}\n\n"
            
            prompt += "### 指南 ###\n"
            prompt += "以下指南将帮助您制定此请求的计划。\n"
            prompt += "通用指南:\n"
            prompt += "如果适用搜索功能，请使用搜索快速查找具有特定名称的文件或条目。\n"
            prompt += "任务特定指南:\n"
            if info_pool.additional_knowledge_manager != "":
                prompt += f"{info_pool.additional_knowledge_manager}\n\n"
            else:
                prompt += f"{info_pool.add_info_token}\n\n"
            
            prompt += "请按照以下包含两部分内容的格式输出：\n"
            prompt += "### Thought ###\n"
            prompt += "对计划和子目标的理由进行详细解释（**使用简体中文**）。\n\n"
            prompt += "### 计划 ###\n"
            prompt += "1. 第一个子目标\n"
            prompt += "2. 第二个子目标\n"
            prompt += "...\n"
        else:
            if info_pool.completed_plan != "No completed subgoal.":
                prompt += "### 历史操作 ###\n"
                prompt += "已完成的操作:\n"
                prompt += f"{info_pool.completed_plan}\n\n"
            prompt += "### 计划 ###\n"
            prompt += f"{info_pool.plan}\n\n"
            prompt += f"### 上一个动作 ###\n"
            prompt += f"{info_pool.last_action}\n\n"
            prompt += f"### 上一个动作描述 ###\n"
            prompt += f"{info_pool.last_summary}\n\n"
            prompt += "### 重要笔记 ###\n"
            if info_pool.important_notes != "":
                prompt += f"{info_pool.important_notes}\n\n"
            else:
                prompt += "未记录任何重要笔记。\n\n"
            prompt += "### 指南 ###\n"
            prompt += "以下指南将帮助您制定此请求的计划。\n"
            prompt += "指南:\n"
            prompt += "如果适用搜索功能，请使用搜索快速查找具有特定名称的文件或条目。\n"
            prompt += "任务特定指南:\n"
            if info_pool.additional_knowledge_manager != "":
                prompt += f"{info_pool.additional_knowledge_manager}\n\n"
            else:
                prompt += f"{info_pool.add_info_token}\n\n"
            if info_pool.error_flag_plan:
                prompt += "### 潜在卡住! ###\n"
                prompt += "您遇到了几次失败的尝试。以下是一些日志:\n"
                k = info_pool.err_to_manager_thresh
                recent_actions = info_pool.action_history[-k:]
                recent_summaries = info_pool.summary_history[-k:]
                recent_err_des = info_pool.error_descriptions[-k:]
                for i, (act, summ, err_des) in enumerate(zip(recent_actions, recent_summaries, recent_err_des)):
                    prompt += f"- 尝试: 动作: {act} | 描述: {summ} | 结果: 失败 | 反馈: {err_des}\n"

            prompt += "---\n"
            prompt += "请仔细评估当前状态和提供的截图。检查是否需要修改当前计划。确定用户请求是否已完全完成。如果您确信无需进一步操作，请在输出中将计划标记为“Finished”。如果用户请求未完成，请更新计划。如果您因错误而受阻，请逐步思考是否需要修改总体计划来解决错误。\n"
            prompt += "注意: 1. 如果当前情况阻止了继续执行原始计划或需要用户澄清，请做出合理的假设并相应地修改计划。在这种情况下，请表现得像您是用户一样。 2. 请先参考指南中的有用信息和步骤进行规划。 3. 如果计划中的第一个子目标已完成，请根据屏幕截图和进度及时更新计划，确保下一个子目标始终是计划中的第一项。 4. 如果第一个子目标未完成，请复制上一轮的计划或根据子目标的完成情况更新计划。\n"
            prompt += "重要提示: 如果下一步需要 `answer` 动作，请确保计划中包含执行 `answer` 动作的步骤。在这种情况下，除非上一个动作是 `answer`，否则您不应将计划标记为“Finished”。\n"
            if task_specific_note != "":
              prompt += f"{task_specific_note}\n\n"

            prompt += "请按照以下包含三部分内容的格式输出：\n\n"
            prompt += "### Thought ###\n"
            prompt += "对更新后的计划和当前子目标的理由进行解释（**使用简体中文**）。\n\n"
            prompt += "### 历史操作 ###\n"
            prompt += "尝试在现有历史操作的基础上添加最近完成的子目标。请勿删除任何现有的历史操作。如果没有新完成的子目标，则复制现有的历史操作。\n\n"
            prompt += "### 计划 ###\n"
            prompt += "请根据当前页面和进度更新或复制现有计划。请密切关注历史操作。请不要重复已完成内容的计划，除非您可以从屏幕状态判断某个子目标确实未完成。\n"
            
        return prompt

    def parse_response(self, response: str) -> dict:
        if "### 历史操作" in response:
            thought = response.split("### Thought")[-1].split("### 历史操作")[0].replace("\n", " ").replace("  ", " ").replace("###", "").strip()
            completed_subgoal = response.split("### 历史操作")[-1].split("### 计划")[0].replace("\n", " ").replace("  ", " ").replace("###", "").strip()
        else:
            thought = response.split("### Thought")[-1].split("### 计划")[0].replace("\n", " ").replace("  ", " ").replace("###", "").strip()
            completed_subgoal = "No completed subgoal."
        plan = response.split("### 计划")[-1].replace("\n", " ").replace("  ", " ").replace("###", "").strip()#.split("### 当前子目标")[0].replace("\n", " ").replace("  ", " ").replace("###", "").strip()
        return {"thought": thought, "completed_subgoal": completed_subgoal,  "plan": plan}#, "current_subgoal": current_subgoal

from mobile_v3.utils.new_json_action import *

ATOMIC_ACTION_SIGNITURES_noxml = {
    ANSWER: {
        "arguments": ["text"],
        "description": lambda info: "Answer user's question. Usage example: {\"action\": \"answer\", \"text\": \"the content of your answer\"}"
    },
    CLICK: {
        "arguments": ["coordinate"],
        "description": lambda info: "Click the point on the screen with specified (x, y) coordinates. Usage Example: {\"action\": \"click\", \"coordinate\": [x, y]}"
    },
    LONG_PRESS: {
        "arguments": ["coordinate"],
        "description": lambda info: "Long press on the position (x, y) on the screen. Usage Example: {\"action\": \"long_press\", \"coordinate\": [x, y]}"
    },
    TYPE: {
        "arguments": ["text"],
        "description": lambda info: "Type text into current activated input box or text field. If you have activated the input box, you can see the words \"ADB Keyboard {on}\" at the bottom of the screen. If not, click the input box to confirm again. Please make sure the correct input box has been activated before typing. Usage Example: {\"action\": \"type\", \"text\": \"the text you want to type\"}"
    },
    SYSTEM_BUTTON: {
        "arguments": ["button"],
        "description": lambda info: "Press a system button, including back, home, and enter. Usage example: {\"action\": \"system_button\", \"button\": \"Home\"}"
    },
    SWIPE: {
        "arguments": ["coordinate", "coordinate2"],
        "description": lambda info: "Scroll from the position with coordinate to the position with coordinate2. Please make sure the start and end points of your swipe are within the swipeable area and away from the keyboard (y1 < 1400). Usage Example: {\"action\": \"swipe\", \"coordinate\": [x1, y1], \"coordinate2\": [x2, y2]}"
    }
}

INPUT_KNOW = "If you've activated an input field, you'll see \"ADB Keyboard {on}\" at the bottom of the screen. This phone doesn't display a soft keyboard. So, if you see \"ADB Keyboard {on}\" at the bottom of the screen, it means you can type. Otherwise, you'll need to tap the correct input field to activate it."

class Executor(BaseAgent):

    def get_prompt(self, info_pool: InfoPool) -> str:
        prompt = "您是代表用户操作 Android 手机的智能体。您的目标是根据手机的当前状态和用户请求来决定要执行的下一个操作。\n\n"
        prompt += "请**务必**使用**简体中文**进行思考和描述。\n\n"

        prompt += "### 用户请求 ###\n"
        prompt += f"{info_pool.instruction}\n\n"

        prompt += "### 总体计划 ###\n"
        prompt += f"{info_pool.plan}\n\n"
        
        prompt += "### 当前子目标 ###\n"
        current_goal = info_pool.plan
        current_goal = re.split(r'(?<=\d)\. ', current_goal)
        truncated_current_goal = ". ".join(current_goal[:4]) + '.'
        truncated_current_goal = truncated_current_goal[:-2].strip()
        prompt += f"{truncated_current_goal}\n\n"

        prompt += "### 进度状态 ###\n"
        if info_pool.progress_status != "":
            prompt += f"{info_pool.progress_status}\n\n"
        else:
            prompt += "尚未有进度。\n\n"

        if info_pool.additional_knowledge_executor != "":
            prompt += "### 指南 ###\n"
            prompt += f"{info_pool.additional_knowledge_executor}\n"

        if "exact duplicates" in info_pool.instruction:
            prompt += "任务特定指南:\n只有名称、日期和详细信息都相同的两个项目才被视为重复项。\n\n"
        elif "Audio Recorder" in info_pool.instruction:
            prompt += "任务特定指南:\n停止录音图标是一个白色方块，位于底部从左数第四个。请不要点击中间的圆形暂停图标。\n\n"
        else:
            prompt += "\n"
        
        prompt += "---\n"        
        prompt += "请仔细检查上面提供的所有信息，并决定要执行的下一步动作。如果您注意到上一步动作中存在未解决的错误，请像人类用户一样思考并尝试纠正它们。您必须从原子动作中选择您的动作。\n\n"
        
        prompt += "#### 原子动作 ####\n"
        prompt += "原子动作函数以 `action(arguments): description` 的格式列出，如下所示:\n"

        for action, value in ATOMIC_ACTION_SIGNITURES_noxml.items():
            prompt += f"- {action}({', '.join(value['arguments'])}): {value['description'](info_pool)}\n"

        prompt += "\n"
        prompt += "### 最新动作历史 ###\n"
        if info_pool.action_history != []:
            prompt += "您之前采取的最近动作以及它们是否成功:\n"
            num_actions = min(5, len(info_pool.action_history))
            latest_actions = info_pool.action_history[-num_actions:]
            latest_summary = info_pool.summary_history[-num_actions:]
            latest_outcomes = info_pool.action_outcomes[-num_actions:]
            error_descriptions = info_pool.error_descriptions[-num_actions:]
            action_log_strs = []
            for act, summ, outcome, err_des in zip(latest_actions, latest_summary, latest_outcomes, error_descriptions):
                if outcome == "A":
                    action_log_str = f"动作: {act} | 描述: {summ} | 结果: 成功\n"
                else:
                    action_log_str = f"动作: {act} | 描述: {summ} | 结果: 失败 | 反馈: {err_des}\n"
                prompt += action_log_str
                action_log_strs.append(action_log_str)
            
            prompt += "\n"
        else:
            prompt += "尚未执行任何动作。\n\n"

        prompt += "---\n"
        prompt += "重要提示:\n1. 不要重复多次失败的动作。尝试更换为其他动作。\n"
        prompt += "2. 请优先完成当前子目标。\n\n"
        prompt += "请按照以下包含三部分内容的格式输出：\n"
        prompt += "### Thought ###\n"
        prompt += "提供对所选动作的理由的详细解释（**使用简体中文**）。\n\n"

        prompt += "### Action ###\n"
        prompt += "从提供的选项中只选择一个动作或快捷方式。\n"
        prompt += "您必须使用有效的 JSON 格式提供您的决定，指定 `action` 和动作的参数。例如，如果您想输入一些文本，您应该写 {\"action\":\"type\", \"text\": \"您想输入的文本\"}。\n\n"
        
        prompt += "### Description ###\n"
        prompt += "对所选动作的简要描述（**使用简体中文**）。不要描述预期结果。\n"
        return prompt

    def parse_response(self, response: str) -> dict:
        thought = response.split("### Thought")[-1].split("### Action")[0].replace("\n", " ").replace("  ", " ").replace("###", "").strip()
        action = response.split("### Action")[-1].split("### Description")[0].replace("\n", " ").replace("  ", " ").replace("###", "").strip()
        description = response.split("### Description")[-1].replace("\n", " ").replace("  ", " ").replace("###", "").strip()
        return {"thought": thought, "action": action, "description": description}

class ActionReflector(BaseAgent):

    def get_prompt(self, info_pool: InfoPool) -> str:
        prompt = "您是代表用户操作 Android 手机的智能体。您的目标是验证上一步动作是否产生了预期的行为，并跟踪总体进度。\n\n"
        prompt += "请**务必**使用**简体中文**进行思考和描述。\n\n"

        prompt += "### 用户请求 ###\n"
        prompt += f"{info_pool.instruction}\n\n"
        
        prompt += "### 进度状态 ###\n"
        if info_pool.completed_plan != "":
            prompt += f"{info_pool.completed_plan}\n\n"
        else:
            prompt += "尚未有进度。\n\n"

        prompt += "---\n"
        prompt += "随附的两张图片是您上一个动作之前和之后截取的手机截图。\n"

        prompt += "---\n"
        prompt += "### 最新动作 ###\n"
        prompt += f"动作: {info_pool.last_action}\n"
        prompt += f"预期: {info_pool.last_summary}\n\n"

        prompt += "---\n"
        prompt += "请仔细检查上面提供的信息，以确定上一步动作是否产生了预期的行为。如果动作成功，请相应地更新进度状态。如果动作失败，请确定失败模式，并说明造成此失败的潜在原因。\n\n"
        prompt += "注意: 对于滑动滚动屏幕以查看更多内容的操作，如果滑动前后显示的内容完全相同，则认为滑动失败，结果为 C: 失败。上一步动作未产生任何变化。这可能是因为内容已滚动到底部。\n\n" # 翻译

        prompt += "请按照以下包含两部分内容的格式输出：\n"
        prompt += "### Outcome ###\n"
        prompt += "请从以下选项中选择。将您的回答给出为“A”、“B”或“C”：\n"
        prompt += "A: 成功或部分成功。上一步动作的结果符合预期。\n"
        prompt += "B: 失败。上一步动作导致了错误的页面。我需要返回到上一个状态。\n"
        prompt += "C: 失败。上一步动作未产生任何变化。\n\n"

        prompt += "### Error Description ###\n"
        prompt += "如果动作失败，请提供错误的详细描述以及导致此失败的潜在原因（**使用简体中文**）。如果动作成功，请在此处填写“None”。\n"

        return prompt

    def parse_response(self, response: str) -> dict:
        outcome = response.split("### Outcome")[-1].split("### Error Description")[0].replace("\n", " ").replace("  ", " ").replace("###", "").strip()
        error_description = response.split("### Error Description")[-1].replace("\n", " ").replace("###", "").replace("  ", " ").strip()
        return {"outcome": outcome, "error_description": error_description}

class Notetaker(BaseAgent):

    def get_prompt(self, info_pool: InfoPool) -> str:
        prompt = "您是操作手机的实用 AI 助手。您的目标是记录与用户请求相关的、重要的内容。\n\n"
        prompt += "请**务必**使用**简体中文**进行思考和描述。\n\n"

        prompt += "### 用户请求 ###\n"
        prompt += f"{info_pool.instruction}\n\n"

        prompt += "### 进度状态 ###\n"
        prompt += f"{info_pool.progress_status}\n\n"

        prompt += "### 现有重要笔记 ###\n"
        if info_pool.important_notes != "":
            prompt += f"{info_pool.important_notes}\n\n"
        else:
            prompt += "未记录任何重要笔记。\n\n"

        if "transactions" in info_pool.instruction and "Simple Gallery" in info_pool.instruction:
            prompt += "### 指南 ###\n您只能记录 DCIM 中的交易信息，因为其他交易与任务无关。\n"
        elif "enter their product" in info_pool.instruction:
            prompt += "### 指南 ###\n请记录每次出现的数字，以便最后计算它们的乘积。\n"
        
        prompt += "---\n"
        prompt += "仔细检查上面的信息，以识别当前屏幕上需要记录的任何与任务相关的重要内容。\n"
        prompt += "重要提示：\n不要记录低级动作；只跟踪与用户请求相关的重要的文本或视觉信息。不要重复用户请求或进度状态。不要编造您不确定的内容。\n\n"

        prompt += "请按照以下格式输出：\n"
        prompt += "### 重要笔记 ###\n"
        prompt += "更新后的重要笔记，结合了旧的和新的笔记（**使用简体中文**）。如果没有新的内容要记录，请复制现有的重要笔记。\n"

        return prompt

    def parse_response(self, response: str) -> dict:
        important_notes = response.split("### 重要笔记")[-1].replace("\n", " ").replace("  ", " ").replace("###", "").strip()
        return {"important_notes": important_notes}