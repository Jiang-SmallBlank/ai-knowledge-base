"""Supervisor 监督模式 — Worker 执行 + Supervisor 审核 + 反馈修正循环

核心设计：
- Worker 只负责执行，Supervisor 只负责审核（职责隔离）
- 审核反馈结构化（评分 + 弱项 + 建议），支持定向修改
- max_retries=3 兜底，避免无限循环
"""

import json
import sys
from pathlib import Path
from typing import Any

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from pipeline.model_client import chat


WORKER_SYSTEM = """你是 AI 技术分析师。请按要求完成分析任务。
输出 JSON 格式，包含：summary, key_points, recommendation。"""

SUPERVISOR_SYSTEM = """你是质量审核专家。请审核以下分析报告。

评分维度（每维度 1-10）：
1. 准确性：信息是否准确无误
2. 深度：分析是否有洞察力
3. 格式：是否符合 JSON 规范，结构清晰

输出严格 JSON：
{"passed": true/false, "score": 1-10总分, "feedback": "具体改进建议"}
只输出 JSON，不要其他内容。"""


def supervisor(task: str, max_retries: int = 3) -> dict[str, Any]:
    """监督模式：Worker 产出 + Supervisor 审核，不通过就重做

    职责隔离：Worker 只执行，Supervisor 只审核。
    审核反馈包含评分 + 弱项定位 + 改进建议，支持定向修改。
    max_retries 兜底，避免无限循环。

    Args:
        task: 分析任务描述
        max_retries: 最大重试次数

    Returns:
        {"output": str, "attempts": int, "final_score": int, "warning": str|None}
    """
    worker_output = None
    feedback = ""
    final_score = 0

    for attempt in range(1, max_retries + 1):
        # --- Worker 执行 ---
        if attempt == 1:
            worker_output, _ = chat(task, system=WORKER_SYSTEM)
        else:
            revision_prompt = (
                f"原始任务: {task}\n\n"
                f"上次产出: {worker_output}\n\n"
                f"审核反馈: {feedback}\n\n"
                f"请根据反馈改进，保持 JSON 格式。"
            )
            worker_output, _ = chat(revision_prompt, system=WORKER_SYSTEM)

        # --- Supervisor 审核 ---
        review_prompt = f"请审核以下分析报告：\n{worker_output}"
        review_text, _ = chat(review_prompt, system=SUPERVISOR_SYSTEM, temperature=0.2)

        try:
            review_data = json.loads(review_text)
        except json.JSONDecodeError:
            review_data = {"passed": False, "score": 0, "feedback": "审核输出格式错误"}

        score = review_data.get("score", 0)
        final_score = score
        feedback = review_data.get("feedback", "请改进质量")
        print(f"  第 {attempt} 轮审核: 得分 {score}/10")

        # --- 判断是否通过 ---
        if review_data.get("passed", False) or score >= 7:
            return {
                "output": worker_output,
                "attempts": attempt,
                "final_score": score,
            }

    # 达到最大重试次数
    return {
        "output": worker_output,
        "attempts": max_retries,
        "final_score": final_score,
        "warning": f"达到最大重试次数({max_retries})，可能质量不达标",
    }


# --- 测试入口 ---
if __name__ == "__main__":
    print("=" * 50)
    print("Supervisor 监督模式测试")
    print("=" * 50)

    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "请分析 LangGraph 框架的优缺点和适用场景"
    result = supervisor(task)

    print(f"\n最终结果:")
    print(f"  审核轮次: {result['attempts']}")
    print(f"  最终得分: {result['final_score']}/10")
    if result.get("warning"):
        print(f"  警告: {result['warning']}")
    print(f"  输出预览: {result['output'][:200]}...")
