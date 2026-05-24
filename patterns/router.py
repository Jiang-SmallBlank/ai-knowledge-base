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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

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


def _load_knowledge_index() -> list[dict[str, Any]]:
    """Load knowledge articles from the index file."""
    index_path = Path("knowledge/articles/index.json")
    if not index_path.exists():
        return []
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _handle_knowledge_query(query: str) -> str:
    """Search the local knowledge base index for matching articles."""
    index = _load_knowledge_index()
    if not index:
        return "知识库索引不存在或为空。"

    query_lower = query.lower()
    results: list[dict[str, Any]] = []
    for entry in index:
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        tags = entry.get("tags", [])
        content = entry.get("raw_content", "")
        search_text = f"{title} {summary} {' '.join(tags)} {content}".lower()
        if query_lower in search_text:
            results.append(entry)

    if not results:
        return "知识库中未找到相关内容。"

    lines: list[str] = [f"知识库匹配结果（共 {len(results)} 条）："]
    for entry in results[:5]:
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


def route(query: str) -> str:
    """Route a user query to the appropriate handler.

    Two-layer strategy:
      1. Keyword matching (zero-cost)
      2. LLM classification fallback

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
    return handler(query)


# ── Test entry ──

if __name__ == "__main__":
    test_queries = [
        "find me a rust web framework on github",
        "介绍 Python 是什么",
        "搜索关于 agent 的知识库文章",
    ]
    for q in test_queries:
        print(f"\n{'=' * 60}")
        print(f"Query: {q}")
        print(f"{'=' * 60}")
        result = route(q)
        print(result)
