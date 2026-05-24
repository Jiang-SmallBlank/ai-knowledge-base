"""Two-layer intent routing: keyword matching -> LLM classification -> handler dispatch.

Intents:
  - github_search    : Search GitHub repos via the Search API
  - knowledge_query  : Search local knowledge/articles/index.json
  - general_chat     : Free-form LLM conversation

Usage:
    from patterns.router import route

    result = route("find me a rust web framework on github")
    print(result)
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path for direct script execution
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from pipeline.model_client import chat, chat_json

_INTENT_GITHUB_SEARCH = "github_search"
_INTENT_KNOWLEDGE_QUERY = "knowledge_query"
_INTENT_GENERAL_CHAT = "general_chat"

# ── Layer 1: keyword rules ──

_KEYWORD_RULES: list[tuple[str, str]] = [
    (r"github|repo|repository|开源项目|仓库|star(?:\s*:)?\s*\d", _INTENT_GITHUB_SEARCH),
    (r"knowledge|知识库|article|文章|信息|内容|资料|文档|wiki|documentation|索引",
     _INTENT_KNOWLEDGE_QUERY),
]


def _keyword_match(query: str) -> str | None:
    """First-layer intent matching via keyword regex.

    Iterates through _KEYWORD_RULES and returns the first matched intent.
    Returns None if no rule matches, triggering the LLM fallback.
    """
    for pattern, intent in _KEYWORD_RULES:
        if re.search(pattern, query, re.IGNORECASE):
            return intent
    return None


# ── Layer 2: LLM classification ──

_CLASSIFY_SYSTEM_PROMPT = (
    "You are an intent classifier. "
    "Classify the user query into exactly one of these intents:\n"
    "- github_search: searching for GitHub repos, projects, or code\n"
    "- knowledge_query: asking about stored knowledge, articles, or documentation\n"
    "- general_chat: general conversation, greetings, or other topics\n\n"
    "Respond with ONLY a JSON object, no extra text:\n"
    '{"intent": "github_search | knowledge_query | general_chat"}'
)


def _llm_classify(query: str) -> str:
    """Second-layer intent classification via LLM.

    Falls back to general_chat on any parse failure.
    """
    try:
        result, _ = chat_json(query, system=_CLASSIFY_SYSTEM_PROMPT, temperature=0.0)
        intent = result.get("intent", _INTENT_GENERAL_CHAT)
    except (json.JSONDecodeError, KeyError, TypeError):
        intent = _INTENT_GENERAL_CHAT

    if intent not in (_INTENT_GITHUB_SEARCH, _INTENT_KNOWLEDGE_QUERY,
                      _INTENT_GENERAL_CHAT):
        intent = _INTENT_GENERAL_CHAT
    return intent


# ── Handlers ──


def _handle_github_search(query: str) -> str:
    """Search GitHub repositories via the Search API.

    Uses urllib.request for zero-dependency HTTP access.
    The query parameter is percent-encoded via urllib.parse.quote.
    """
    # Remove obvious GitHub keywords to keep the actual search term
    cleaned = re.sub(
        r"(?i)\b(github|repo|repository|开源项目|仓库|project|search|find|look\s+for)\b",
        "", query,
    ).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = "ai"

    encoded = urllib.parse.quote(cleaned)
    url = (f"https://api.github.com/search/repositories"
           f"?q={encoded}&sort=stars&order=desc&per_page=5")

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_API_KEY") or ""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data: dict[str, Any] = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return f"GitHub API 请求失败 (HTTP {e.code}): {e.reason}"
    except urllib.error.URLError as e:
        return f"GitHub API 网络错误: {e.reason}"
    except json.JSONDecodeError:
        return "GitHub API 返回了非 JSON 数据。"

    items = data.get("items", [])
    if not items:
        return "未找到匹配的仓库。"

    lines: list[str] = ["GitHub 搜索结果："]
    for repo in items[:5]:
        name = repo.get("full_name", "unknown")
        desc = repo.get("description") or "无描述"
        stars = repo.get("stargazers_count", 0)
        lang = repo.get("language") or ""
        html_url = repo.get("html_url", "")
        topic_tag = ""
        if lang:
            topic_tag = f" [{lang}]"
        lines.append(f"- {name} (⭐{stars}){topic_tag}")
        lines.append(f"  {html_url}")
        if desc:
            lines.append(f"  {desc[:120]}")
    return "\n".join(lines)


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
    """Extract meaningful keywords from a query, dropping common stop words."""
    stops = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都",
        "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你",
        "会", "着", "没有", "看", "好", "自己", "这", "什么", "怎么",
        "如何", "为什么", "哪个", "哪些", "关于", "搜索", "查找", "找",
        "最近", "最新", "里", "吗", "啊", "呢", "吧",
    }
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]*|[^\s]", text)
    return [t for t in tokens if t.lower() not in stops and len(t) > 1]


def _handle_knowledge_query(query: str) -> str:
    """Search all local article JSON files for matching content."""
    articles = _load_all_articles()
    if not articles:
        return "知识库中暂无内容。"

    keywords = _extract_keywords(query)
    if not keywords:
        return "知识库中未找到相关内容。"

    results: list[dict[str, Any]] = []
    for entry in articles:
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        tags = entry.get("tags", [])
        content = entry.get("raw_content", "")
        search_text = f"{title} {summary} {' '.join(tags)} {content}".lower()
        match_count = sum(1 for kw in keywords if kw.lower() in search_text)
        if match_count > 0:
            results.append((match_count, entry))

    if not results:
        return "知识库中未找到相关内容。"

    results.sort(key=lambda x: -x[0])
    lines: list[str] = [f"知识库匹配结果（共 {len(results)} 条）："]
    for score, entry in results[:5]:
        title = entry.get("title", "")
        summary = entry.get("summary", "")[:100]
        tags = entry.get("tags", [])
        tag_str = f"[{', '.join(tags[:4])}]" if tags else ""
        lines.append(f"- {title} {tag_str}")
        if summary:
            lines.append(f"  {summary}")
    return "\n".join(lines)


def _handle_general_chat(query: str) -> str:
    """Delegate to LLM for general conversation."""
    text, _usage = chat(query, temperature=0.7)
    return text


# ── Intent-to-handler mapping ──

_HANDLERS: dict[str, Any] = {
    _INTENT_GITHUB_SEARCH: _handle_github_search,
    _INTENT_KNOWLEDGE_QUERY: _handle_knowledge_query,
    _INTENT_GENERAL_CHAT: _handle_general_chat,
}


# ── Unified entry point ──


def _is_empty_result(result: str) -> bool:
    """Check if a handler returned an empty/no-result message."""
    no_result_indicators = [
        "未找到匹配的仓库", "未找到相关内容",
        "知识库中暂无内容", "知识库中未找到",
        "GitHub API 请求失败", "GitHub API 网络错误",
    ]
    for indicator in no_result_indicators:
        if indicator in result:
            return True
    return False


def route(query: str) -> str:
    """Route a user query to the appropriate handler.

    Two-layer strategy:
      1. Keyword matching (zero-cost)
      2. LLM classification fallback

    If the specialized handler returns no useful result, falls back
    to general_chat so the user always gets a meaningful answer.

    Args:
        query: The raw user input string.

    Returns:
        The handler's response as a string.
    """
    query = query.strip()
    if not query:
        return "请输入查询内容。"

    intent = _keyword_match(query)
    if intent is None:
        intent = _llm_classify(query)

    handler = _HANDLERS.get(intent, _handle_general_chat)
    result = handler(query)

    # Fallback: if specialized handler had no result, let LLM answer directly
    if intent != _INTENT_GENERAL_CHAT and _is_empty_result(result):
        fallback = _handle_general_chat(query)
        return f"{result}\n\n--- 以下是 AI 助手的回答 ---\n{fallback}"

    return result


# ── Test entry ──

if __name__ == "__main__":
    queries = sys.argv[1:] if len(sys.argv) > 1 else [
        "find me a rust web framework on github",
        "介绍 Python 是什么",
        "搜索关于 agent 的知识库文章",
    ]
    for q in queries:
        print(f"\n{'=' * 60}")
        print(f"Query: {q}")
        print(f"{'=' * 60}")
        print(route(q))
