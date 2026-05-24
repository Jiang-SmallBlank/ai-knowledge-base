"""MCP 知识库搜索服务器 — 通过 stdio 提供 knowledge/articles/ 的搜索能力。"""

import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp-knowledge")

ARTICLES_DIR = Path(__file__).resolve().parent / "knowledge" / "articles"


def load_articles() -> list[dict[str, Any]]:
    """加载 knowledge/articles/ 下所有 JSON 文件。"""
    articles = []
    if not ARTICLES_DIR.is_dir():
        logger.warning("articles dir not found: %s", ARTICLES_DIR)
        return articles
    for path in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("skip %s: %s", path.name, e)
            continue
        if isinstance(data, list):
            articles.extend(data)
        else:
            articles.append(data)
    return articles


def search_articles(
    articles: list[dict[str, Any]], keyword: str, limit: int = 5
) -> list[dict[str, Any]]:
    """按关键词搜索文章标题和摘要（大小写不敏感）。"""
    kw = keyword.lower()
    matched = []
    for a in articles:
        title = (a.get("title") or "").lower()
        summary = (a.get("summary") or "").lower()
        tags = [t.lower() for t in (a.get("tags") or [])]
        if kw in title or kw in summary or any(kw in t for t in tags):
            matched.append(a)
    matched.sort(key=lambda x: x.get("analysis", {}).get("relevance_score", 0), reverse=True)
    return matched[:limit]


def knowledge_stats(
    articles: list[dict[str, Any]],
) -> dict[str, Any]:
    """统计文章总数、来源分布、热门标签。"""
    total = len(articles)
    source_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    for a in articles:
        source = a.get("source_type", "unknown")
        source_counter[source] += 1
        for tag in a.get("tags", []):
            tag_counter[tag] += 1
    return {
        "total_articles": total,
        "source_distribution": dict(source_counter.most_common()),
        "top_tags": [{"tag": t, "count": c} for t, c in tag_counter.most_common(20)],
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_articles",
        "description": "按关键词搜索知识库文章（标题、摘要、标签）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
                "limit": {"type": "integer", "description": "最大返回条数", "default": 5},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_article",
        "description": "按 ID 获取文章完整内容",
        "inputSchema": {
            "type": "object",
            "properties": {
                "article_id": {"type": "string", "description": "文章 ID"},
            },
            "required": ["article_id"],
        },
    },
    {
        "name": "knowledge_stats",
        "description": "返回知识库统计信息（文章总数、来源分布、热门标签）",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

_articles: list[dict[str, Any]] | None = None


def get_articles() -> list[dict[str, Any]]:
    global _articles
    if _articles is None:
        _articles = load_articles()
    return _articles


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mcp-knowledge-server", "version": "1.0.0"},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        articles = get_articles()

        if tool_name == "search_articles":
            keyword = arguments.get("keyword", "")
            limit = arguments.get("limit", 5)
            results = search_articles(articles, keyword, limit)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(results, ensure_ascii=False, indent=2)}]
                },
            }

        if tool_name == "get_article":
            article_id = arguments.get("article_id", "")
            for a in articles:
                if a.get("id") == article_id:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(a, ensure_ascii=False, indent=2)}]
                        },
                    }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": f"article not found: {article_id}"},
            }

        if tool_name == "knowledge_stats":
            stats = knowledge_stats(articles)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(stats, ensure_ascii=False, indent=2)}]
                },
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"unknown tool: {tool_name}"},
        }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"unknown method: {method}"},
    }


def main() -> None:
    logger.info("MCP knowledge server starting, articles dir: %s", ARTICLES_DIR)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
        except json.JSONDecodeError as e:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        except Exception as e:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
