"""Supervisor pattern: Worker-Supervisor review loop with quality gate.

A Worker Agent produces a JSON analysis report for a given task.
A Supervisor Agent scores the output on accuracy, depth, and format,
then decides whether to accept or request revision (max 3 rounds).

Usage:
    from patterns.supervisor import supervisor

    result = supervisor("分析 Python 3.12 的新特性")
    print(result["output"])
    print(f"Score: {result['final_score']}, attempts: {result['attempts']}")
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path for direct script execution
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from pipeline.model_client import chat, chat_json

logger = logging.getLogger(__name__)

_WORKER_SYSTEM_PROMPT = (
    "你是一个专业的技术分析师。请根据用户的任务输出一份 JSON 格式的分析报告。\n\n"
    "输出格式（仅 JSON，不要额外文字）：\n"
    '{\n'
    '  "title": "报告标题",\n'
    '  "summary": "200 字以内的摘要",\n'
    '  "key_points": ["要点1", "要点2", "要点3"],\n'
    '  "analysis": "详细分析内容（300-500 字）",\n'
    '  "conclusion": "结论"'
    '}'
)

_SUPERVISOR_SYSTEM_PROMPT = (
    "你是一个质量审核员。请对以下分析报告进行评分，评分维度：\n"
    "- 准确性 (accuracy, 1-10)：事实是否正确、逻辑是否严密\n"
    "- 深度 (depth, 1-10)：分析是否透彻、是否有洞察\n"
    "- 格式 (format, 1-10)：JSON 结构是否完整、语言是否规范\n\n"
    "总分 = accuracy + depth + format（满分 30）。\n"
    "通过条件：总分 >= 21（即平均 7 分）\n\n"
    "输出 JSON（仅 JSON，不要额外文字）：\n"
    '{\n'
    '  "accuracy": 0,\n'
    '  "depth": 0,\n'
    '  "format": 0,\n'
    '  "total_score": 0,\n'
    '  "passed": false,\n'
    '  "feedback": "修改建议"'
    '}'
)

_PASS_THRESHOLD = 21
_MAX_TOTAL = 30


def _worker_attempt(task: str, feedback: str | None = None) -> str:
    """Run the Worker Agent to produce an analysis report.

    Args:
        task: The original task description.
        feedback: Optional supervisor feedback from a previous attempt.

    Returns:
        The worker's raw text output (should be JSON).
    """
    user_prompt = task
    if feedback:
        user_prompt = (
            f"任务：{task}\n\n"
            f"上一版审核反馈：\n{feedback}\n\n"
            "请根据反馈修改你的分析报告。"
        )
    text, _usage = chat(user_prompt, system=_WORKER_SYSTEM_PROMPT, temperature=0.7)
    return text


def _supervisor_review(output: str) -> dict[str, Any]:
    """Run the Supervisor Agent to review the worker's output.

    Args:
        output: The worker's raw text output.

    Returns:
        A dict with keys: accuracy, depth, format, total_score, passed, feedback.
    """
    try:
        result, _usage = chat_json(
            output,
            system=_SUPERVISOR_SYSTEM_PROMPT,
            temperature=0.0,
        )
    except json.JSONDecodeError:
        result = {
            "accuracy": 0,
            "depth": 0,
            "format": 0,
            "total_score": 0,
            "passed": False,
            "feedback": "审核响应无法解析为 JSON，请重试。",
        }

    result.setdefault("total_score",
                       result.get("accuracy", 0) + result.get("depth", 0) + result.get("format", 0))
    result.setdefault("passed", result.get("total_score", 0) >= _PASS_THRESHOLD)
    result.setdefault("feedback", "无反馈")
    result.setdefault("accuracy", 0)
    result.setdefault("depth", 0)
    result.setdefault("format", 0)
    return result


def supervisor(task: str, max_retries: int = 3) -> dict[str, Any]:
    """Execute a Worker-Supervisor review loop.

    The Worker produces a JSON analysis report; the Supervisor scores it.
    If the score is below threshold, the Worker revises with feedback
    (up to ``max_retries`` rounds).

    Args:
        task: The task description for the Worker Agent.
        max_retries: Maximum number of revision rounds (default: 3).

    Returns:
        A dict with:
          - output: The final accepted worker output (str).
          - attempts: Number of attempts made (int).
          - final_score: The score from the last review (int).
          - warning: Warning message if max retries exceeded (str|None).
    """
    last_feedback: str | None = None

    for attempt in range(1, max_retries + 2):
        worker_output = _worker_attempt(task, last_feedback)

        review = _supervisor_review(worker_output)
        total_score = review.get("total_score", 0)
        passed = review.get("passed", False)

        if passed:
            return {
                "output": worker_output,
                "attempts": attempt,
                "final_score": total_score,
                "warning": None,
            }

        if attempt <= max_retries:
            last_feedback = review.get("feedback", "请改进分析质量。")
            logger.info(
                "Attempt %d: score=%d/%d, redoing...",
                attempt, total_score, _MAX_TOTAL,
            )
        else:
            logger.warning(
                "Max retries (%d) exceeded, force returning. "
                "Last score: %d/%d",
                max_retries, total_score, _MAX_TOTAL,
            )
            return {
                "output": worker_output,
                "attempts": attempt,
                "final_score": total_score,
                "warning": f"超过最大重试次数（{max_retries}），结果未通过审核。",
            }

    return {
        "output": "",
        "attempts": max_retries + 1,
        "final_score": 0,
        "warning": "未知错误。",
    }


if __name__ == "__main__":
    import sys

    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "分析 Python 3.12 的新特性"
    result = supervisor(task)

    print(f"\n{'=' * 60}")
    print(f"Task: {task}")
    print(f"{'=' * 60}")
    print(f"Attempts: {result['attempts']}")
    print(f"Final Score: {result['final_score']}/30")
    if result["warning"]:
        print(f"Warning: {result['warning']}")
    print(f"\n--- Output ---\n{result['output']}")
