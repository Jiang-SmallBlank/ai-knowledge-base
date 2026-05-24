"""Supervisor pattern: Worker-Supervisor review loop with quality gate.

The Worker Agent analyses articles from the local knowledge base
(knowledge/articles/*.json) for a given task, then produces a JSON report.
The Supervisor Agent scores the output and may request revisions.

Usage:
    from patterns.supervisor import supervisor

    result = supervisor("RAG 技术的最新进展")
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


# ── Knowledge base lookup (reused from router) ──


def _load_all_articles() -> list[dict[str, Any]]:
    """Load all individual article JSON files from knowledge/articles/."""
    articles_dir = Path("knowledge/articles")
    if not articles_dir.is_dir():
        return []
    articles: list[dict[str, Any]] = []
    for fpath in sorted(articles_dir.glob("[0-9]*.json")):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                articles.append(data)
            elif isinstance(data, list):
                articles.extend(data)
        except (json.JSONDecodeError, OSError):
            continue
    return articles


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from a query."""
    stops = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都",
        "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你",
        "会", "着", "没有", "看", "好", "自己", "这", "什么", "怎么",
        "如何", "为什么", "哪个", "哪些", "关于", "搜索", "查找", "找",
        "最近", "最新", "里", "吗", "啊", "呢", "吧",
    }
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]*|[^\s]", text)
    return [t for t in tokens if t.lower() not in stops and len(t) > 1]


def _search_knowledge_base(task: str) -> list[dict[str, Any]]:
    """Search the local knowledge base for articles relevant to the task.

    Extracts keywords from the task and returns articles sorted by
    keyword match count (most relevant first).

    Args:
        task: The task description.

    Returns:
        A list of matching article dicts.
    """
    articles = _load_all_articles()
    if not articles:
        return []

    keywords = _extract_keywords(task)
    if not keywords:
        return articles[:5]

    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in articles:
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        tags = entry.get("tags", [])
        content = entry.get("raw_content", "")
        search_text = f"{title} {summary} {' '.join(tags)} {content}".lower()
        count = sum(1 for kw in keywords if kw.lower() in search_text)
        if count > 0:
            scored.append((count, entry))

    scored.sort(key=lambda x: -x[0])
    return [entry for _, entry in scored[:5]]


# ── Prompts ──


_WORKER_SYSTEM_PROMPT_TPL = (
    "你是一个专业的技术分析师。你的任务是基于知识库中的采集文章，"
    "对用户提出的课题进行分析总结。\n\n"
    "以下是相关的知识库文章（JSON 格式），请基于这些内容进行分析：\n\n"
    "{articles}\n\n"
    "输出格式（仅 JSON，不要额外文字）：\n"
    '{{"title": "报告标题",\n'
    '  "summary": "200 字以内的摘要",\n'
    '  "key_points": ["要点1", "要点2", "要点3"],\n'
    '  "analysis": "详细分析内容（300-500 字，引用知识库中的文章）",\n'
    '  "conclusion": "结论",\n'
    '  "references": ["文章标题1", "文章标题2"]}}'
)

_SUPERVISOR_SYSTEM_PROMPT = (
    "你是一个质量审核员。请对以下分析报告进行评分，评分维度：\n"
    "- 准确性 (accuracy, 1-10)：事实是否正确、是否基于提供的知识库内容\n"
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


# ── Worker & Supervisor ──


def _worker_attempt(task: str, articles_json: str,
                    feedback: str | None = None) -> str:
    """Run the Worker Agent to produce an analysis report.

    Args:
        task: The original task description.
        articles_json: JSON string of relevant knowledge base articles.
        feedback: Optional supervisor feedback from a previous attempt.

    Returns:
        The worker's raw text output (should be JSON).
    """
    system_prompt = _WORKER_SYSTEM_PROMPT_TPL.replace("{articles}", articles_json)
    user_prompt = task
    if feedback:
        user_prompt = (
            f"任务：{task}\n\n"
            f"上一版审核反馈：\n{feedback}\n\n"
            "请根据反馈修改你的分析报告。"
        )
    text, _usage = chat(user_prompt, system=system_prompt, temperature=0.7)
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
    """Execute a Worker-Supervisor review loop grounded in the knowledge base.

    Steps:
      1. Search local knowledge base for articles relevant to the task.
      2. Worker Agent produces a JSON analysis report based on those articles.
      3. Supervisor Agent scores the report.
      4. If score < threshold, Worker revises with feedback (up to max_retries).

    Args:
        task: The task description for the Worker Agent.
        max_retries: Maximum number of revision rounds (default: 3).

    Returns:
        A dict with:
          - output: The final accepted worker output (str).
          - attempts: Number of attempts made (int).
          - final_score: The score from the last review (int).
          - articles_found: Number of relevant articles found (int).
          - warning: Warning message if max retries exceeded (str|None).
    """
    articles = _search_knowledge_base(task)
    articles_json = json.dumps(articles, ensure_ascii=False, indent=2) if articles else "（未找到相关文章）"

    last_feedback: str | None = None

    for attempt in range(1, max_retries + 2):
        worker_output = _worker_attempt(task, articles_json, last_feedback)

        review = _supervisor_review(worker_output)
        total_score = review.get("total_score", 0)
        passed = review.get("passed", False)

        if passed:
            return {
                "output": worker_output,
                "attempts": attempt,
                "final_score": total_score,
                "articles_found": len(articles),
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
                "articles_found": len(articles),
                "warning": f"超过最大重试次数（{max_retries}），结果未通过审核。",
            }

    return {
        "output": "",
        "attempts": max_retries + 1,
        "final_score": 0,
        "articles_found": 0,
        "warning": "未知错误。",
    }


if __name__ == "__main__":
    import re

    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "AI Agent 框架对比"
    result = supervisor(task)

    print(f"\n{'=' * 60}")
    print(f"Task: {task}")
    print(f"{'=' * 60}")
    print(f"Attempts: {result['attempts']}")
    print(f"Final Score: {result['final_score']}/30")
    print(f"Articles Found: {result['articles_found']}")
    if result["warning"]:
        print(f"Warning: {result['warning']}")
    print(f"\n--- Output ---\n{result['output']}")
