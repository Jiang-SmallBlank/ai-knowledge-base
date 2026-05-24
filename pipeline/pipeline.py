#!/usr/bin/env python3
"""Four-step knowledge base automation pipeline.

Steps: Collect -> Analyze -> Organize -> Save

Usage:
    python pipeline/pipeline.py --sources github,rss --limit 20
    python pipeline/pipeline.py --sources github --limit 5
    python pipeline/pipeline.py --sources rss --limit 10
    python pipeline/pipeline.py --sources github --limit 5 --dry-run
    python pipeline/pipeline.py --verbose
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx

from model_client import create_provider, chat_with_retry

logger = logging.getLogger(__name__)

RAW_DIR = Path("knowledge/raw")
ARTICLES_DIR = Path("knowledge/articles")
RSS_CONFIG = Path("pipeline/rss_sources.yaml")
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"

TODAY = datetime.now(timezone.utc).strftime("%Y%m%d")

ANALYZE_SYSTEM_PROMPT = (
    "You are an AI knowledge base curator. Analyze the given content and "
    "return ONLY a valid JSON object. No markdown, no code fences, no extra text.\n\n"
    'Return JSON with these exact fields:\n'
    '- "summary": Chinese summary (20-50 characters)\n'
    '- "tags": array of 1-4 tags from this list: '
    'ai, llm, agent, open-source, coding-agent, agent-framework, workflow, cli, '
    'tooling, research, framework, application, tutorial, news, model-release, '
    'python, typescript, rust, go, benchmark, dataset, inference, deployment, '
    'rag, fine-tuning, mcp, privacy, automation, api, sdk, platform, education\n'
    '- "relevance_score": integer 1-10\n'
    '- "key_points": array of 2-3 strings\n'
    '- "impact": one-sentence impact assessment\n'
    '- "category": one of "model-release", "tooling", "research", "framework", '
    '"application", "tutorial", "news"'
)

STANDARD_TAGS = {
    "ai", "llm", "agent", "open-source", "coding-agent", "agent-framework",
    "workflow", "cli", "tooling", "research", "framework", "application",
    "tutorial", "news", "model-release", "python", "typescript", "rust", "go",
    "benchmark", "dataset", "inference", "deployment", "rag", "fine-tuning",
    "mcp", "privacy", "automation", "api", "sdk", "platform", "education",
}

VALID_CATEGORIES = {
    "model-release", "tooling", "research", "framework",
    "application", "tutorial", "news",
}

SOURCE_PREFIX = {"github": "gh", "rss": "rs", "hackernews": "hn"}


# ─────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────


@dataclass
class RawItem:
    source_type: str
    title: str
    url: str
    content: str
    collected_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.collected_at:
            self.collected_at = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )


@dataclass
class AnalyzedArticle:
    id: str = ""
    title: str = ""
    source_url: str = ""
    source_type: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = "draft"
    collected_at: str = ""
    analyzed_at: str = ""
    raw_content: str = ""
    analysis: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# Step 1: Collect
# ─────────────────────────────────────────────────────────────


def _github_auth_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_API_KEY") or ""
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def collect_github(limit: int) -> list[RawItem]:
    logger.info("Collecting from GitHub Search API (limit=%d)", limit)
    headers = _github_auth_headers()
    headers["Accept"] = "application/vnd.github+json"
    items: list[RawItem] = []

    per_page = min(limit, 100)
    params: dict[str, Any] = {
        "q": "ai OR llm OR agent OR machine-learning",
        "sort": "stars",
        "order": "desc",
        "per_page": per_page,
    }

    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(
                GITHUB_SEARCH_URL, headers=headers, params=params
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.error("GitHub API error: %s", e)
        return items
    except httpx.RequestError as e:
        logger.error("GitHub API request failed: %s", e)
        return items

    for repo in data.get("items", []):
        if len(items) >= limit:
            break

        name: str = repo.get("full_name", "")
        description: str = repo.get("description") or ""
        topics: list[str] = repo.get("topics", [])
        stars: int = repo.get("stargazers_count", 0)
        lang: str = repo.get("language") or ""

        if _is_ai_related(name, topics, description):
            content = (
                f"{name}: {description} "
                f"(stars: {stars:,} | lang: {lang} | topics: {', '.join(topics)})"
            )
            items.append(RawItem(
                source_type="github",
                title=name,
                url=f"https://github.com/{name}",
                content=content,
                metadata={"stars": stars, "language": lang, "topics": topics},
            ))

    logger.info("GitHub collected %d items", len(items))
    return items


def _is_ai_related(
    name: str, topics: list[str], description: str
) -> bool:
    keywords = [
        "ai", "llm", "agent", "gpt", "machine-learning", "deep-learning",
        "nlp", "chatgpt", "claude", "llama", "rag", "embedding",
        "neural", "transformer", "langchain", "pytorch", "tensorflow",
    ]
    lower_name = name.lower()
    lower_desc = description.lower()
    for kw in keywords:
        if kw in lower_name or kw in lower_desc:
            return True
    for t in topics:
        if t.lower() in keywords:
            return True
    return False


def _load_rss_config() -> list[dict[str, Any]]:
    if not RSS_CONFIG.exists():
        logger.warning("RSS config not found: %s", RSS_CONFIG)
        return []

    try:
        import yaml as yaml_lib
        with open(RSS_CONFIG, "r") as f:
            data = yaml_lib.safe_load(f)
        return data.get("sources", [])
    except ImportError:
        logger.warning("PyYAML not installed, falling back to manual parse")
        return _parse_yaml_fallback()


def _parse_yaml_fallback() -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    with open(RSS_CONFIG, "r") as f:
        for line in f:
            line = line.rstrip()
            m = re.match(r"^  - name: (.+)$", line)
            if m:
                if current:
                    sources.append(current)
                current = {"name": m.group(1)}
                continue
            m = re.match(r"^    url: (.+)$", line)
            if m and current is not None:
                current["url"] = m.group(1).strip()
            m = re.match(r"^    category: (.+)$", line)
            if m and current is not None:
                current["category"] = m.group(1).strip()
            m = re.match(r"^    enabled: (.+)$", line)
            if m and current is not None:
                current["enabled"] = m.group(1).strip() == "true"
    if current:
        sources.append(current)
    return sources


def _parse_rss_items(xml_text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    pattern = re.compile(r"<item>(.*?)</item>", re.DOTALL)
    for item_match in pattern.finditer(xml_text):
        block = item_match.group(1)
        title = _extract_xml_tag(block, "title")
        link = _extract_xml_tag(block, "link")
        desc = _extract_xml_tag(block, "description")
        pub_date = _extract_xml_tag(block, "pubDate")
        if title or link:
            items.append({
                "title": title or "",
                "link": link or "",
                "description": desc or "",
                "pub_date": pub_date or "",
            })
    return items


def _extract_xml_tag(text: str, tag: str) -> str:
    m = re.search(
        rf"<{tag}[^>]*>(.*?)</{tag}>", text, re.DOTALL
    )
    if not m:
        return ""
    value = m.group(1).strip()
    value = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", value)
    value = re.sub(r"<[^>]+>", "", value)
    return value


def _is_recent(pub_date_str: str, days: int = 7) -> bool:
    if not pub_date_str:
        return True
    try:
        parsed = datetime.strptime(
            pub_date_str.replace("GMT", "").strip(),
            "%a, %d %b %Y %H:%M:%S ",
        )
        delta = datetime.now(timezone.utc) - parsed.replace(tzinfo=timezone.utc)
        return delta.days <= days
    except (ValueError, IndexError):
        return True


def collect_rss(limit: int) -> list[RawItem]:
    logger.info("Collecting from RSS sources (limit=%d)", limit)
    sources = _load_rss_config()
    enabled = [s for s in sources if s.get("enabled")]
    logger.info("Found %d enabled RSS sources", len(enabled))
    items: list[RawItem] = []

    for source in enabled:
        if len(items) >= limit:
            break
        name: str = source.get("name", "unknown")
        url: str = source.get("url", "")
        if not url:
            continue
        logger.info("Fetching RSS: %s (%s)", name, url)
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
                rss_items = _parse_rss_items(response.text)
        except Exception as e:
            logger.error("Failed to fetch RSS %s: %s", name, e)
            continue

        for rss_item in rss_items:
            if len(items) >= limit:
                break
            if not _is_recent(rss_item.get("pub_date", "")):
                continue
            rss_title = rss_item.get("title", "")
            rss_link = rss_item.get("link", "")
            rss_desc = rss_item.get("description", "")
            content = f"{rss_title}: {rss_desc}"
            if not rss_title and not rss_desc:
                continue
            items.append(RawItem(
                source_type="rss",
                title=rss_title or rss_link,
                url=rss_link,
                content=content,
                metadata={"source_name": name, "category": source.get("category", "")},
            ))

    logger.info("RSS collected %d items", len(items))
    return items


# ─────────────────────────────────────────────────────────────
# Step 2: Analyze
# ─────────────────────────────────────────────────────────────


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*\]", "]", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def analyze_item(
    item: RawItem, provider: Any, total: int, idx: int
) -> AnalyzedArticle | None:
    logger.info("Analyzing [%d/%d]: %s", idx + 1, total, item.title[:60])

    user_prompt = (
        f"Analyze this {item.source_type} content:\n\n"
        f"Title: {item.title}\n"
        f"URL: {item.url}\n"
        f"Content: {item.content[:2000]}"
    )

    try:
        response = chat_with_retry(
            provider,
            messages=[
                {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=800,
        )
    except Exception as e:
        logger.error("LLM analysis failed for '%s': %s", item.title[:40], e)
        return None

    parsed = _extract_json(response.content)
    if not parsed:
        logger.warning(
            "Failed to parse LLM response for '%s': %s",
            item.title[:40], response.content[:100],
        )
        return None

    summary = str(parsed.get("summary", ""))[:200]
    tags_raw = parsed.get("tags", [])
    tags = [t for t in tags_raw if isinstance(t, str) and t in STANDARD_TAGS]
    score = parsed.get("relevance_score", 5)
    if isinstance(score, (int, float)):
        score = max(1, min(10, int(score)))
    else:
        score = 5
    key_points = parsed.get("key_points", [])
    if not isinstance(key_points, list):
        key_points = []
    impact = str(parsed.get("impact", ""))[:300]
    category = str(parsed.get("category", "tooling"))
    if category not in VALID_CATEGORIES:
        category = "tooling"

    if not tags:
        tags = ["ai"]
    if not summary:
        summary = item.title

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return AnalyzedArticle(
        title=item.title,
        source_url=item.url,
        source_type=item.source_type,
        summary=summary,
        tags=tags[:6],
        status="draft",
        collected_at=item.collected_at,
        analyzed_at=now,
        raw_content=item.content,
        analysis={
            "relevance_score": score,
            "key_points": key_points[:5],
            "impact": impact,
            "category": category,
        },
    )


# ─────────────────────────────────────────────────────────────
# Step 3: Organize
# ─────────────────────────────────────────────────────────────


def _existing_urls() -> set[str]:
    urls: set[str] = set()
    for fpath in ARTICLES_DIR.glob("*.json"):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                url = data.get("source_url", "")
                if url:
                    urls.add(url)
            elif isinstance(data, list):
                for entry in data:
                    url = entry.get("source_url", "")
                    if url:
                        urls.add(url)
        except (json.JSONDecodeError, OSError):
            continue
    return urls


def _next_sequence(source_type: str) -> int:
    prefix = SOURCE_PREFIX.get(source_type, "xx")
    max_seq = 0
    for fpath in ARTICLES_DIR.glob(f"*{prefix}*.json"):
        m = re.search(rf"-{prefix}-(\d+)-", fpath.stem)
        if m:
            seq = int(m.group(1))
            if seq > max_seq:
                max_seq = seq
    return max_seq + 1


def organize(
    raw_items: list[RawItem],
    analyzed: list[AnalyzedArticle | None],
) -> list[AnalyzedArticle]:
    logger.info("Organizing: dedup + format + ID assignment")
    existing = _existing_urls()
    seen_urls: set[str] = set()
    organized: list[AnalyzedArticle] = []
    seq_counter: dict[str, int] = {}

    for item, article in zip(raw_items, analyzed):
        if article is None:
            continue
        if article.source_url in existing or article.source_url in seen_urls:
            logger.info("Skipping duplicate: %s", article.title[:50])
            continue
        seen_urls.add(article.source_url)

        st = article.source_type
        if st not in seq_counter:
            seq_counter[st] = _next_sequence(st)
        else:
            seq_counter[st] += 1
        seq = seq_counter[st]
        prefix = SOURCE_PREFIX.get(st, "xx")
        article.id = f"{TODAY}-{prefix}-{seq:03d}"
        organized.append(article)

    logger.info("Organized %d articles (deduped %d)", len(organized),
                len(raw_items) - len(organized))
    return organized


# ─────────────────────────────────────────────────────────────
# Step 4: Save
# ─────────────────────────────────────────────────────────────


def save_articles(articles: list[AnalyzedArticle]) -> int:
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for article in articles:
        fname = f"{article.id}.json"
        fpath = ARTICLES_DIR / fname
        data = {
            "id": article.id,
            "title": article.title,
            "source_url": article.source_url,
            "source_type": article.source_type,
            "summary": article.summary,
            "tags": article.tags,
            "status": article.status,
            "collected_at": article.collected_at,
            "analyzed_at": article.analyzed_at,
            "published_at": None,
            "raw_content": article.raw_content,
            "analysis": article.analysis,
        }
        try:
            fpath.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            saved += 1
            logger.info("Saved: %s", fname)
        except OSError as e:
            logger.error("Failed to save %s: %s", fname, e)
    return saved


# ─────────────────────────────────────────────────────────────
# Pipeline orchestrator
# ─────────────────────────────────────────────────────────────


def _save_raw(raw_items: list[RawItem]) -> int:
    fname = f"raw-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    fpath = RAW_DIR / fname
    data = [
        {
            "source_type": i.source_type,
            "title": i.title,
            "url": i.url,
            "content": i.content,
            "collected_at": i.collected_at,
            "metadata": i.metadata,
        }
        for i in raw_items
    ]
    try:
        fpath.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("Saved raw data: %s (%d items)", fname, len(raw_items))
        return len(raw_items)
    except OSError as e:
        logger.error("Failed to save raw data: %s", e)
        return 0


def _load_latest_raw() -> list[RawItem]:
    files = sorted(RAW_DIR.glob("raw-*.json"), reverse=True)
    if not files:
        logger.warning("No raw data files found in %s", RAW_DIR)
        return []
    fpath = files[0]
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        items = [
            RawItem(
                source_type=d["source_type"],
                title=d["title"],
                url=d["url"],
                content=d["content"],
                collected_at=d.get("collected_at", ""),
                metadata=d.get("metadata", {}),
            )
            for d in data
        ]
        logger.info("Loaded raw data: %s (%d items)", fpath.name, len(items))
        return items
    except (json.JSONDecodeError, KeyError, OSError) as e:
        logger.error("Failed to load raw data: %s", e)
        return []


def run_pipeline(
    sources: list[str],
    limit: int,
    dry_run: bool = False,
    verbose: bool = False,
    steps: list[int] | None = None,
) -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    if steps is None:
        steps = [1, 2, 3, 4]

    raw_items: list[RawItem] = []

    if 1 in steps:
        if "github" in sources:
            raw_items.extend(collect_github(limit))
        if "rss" in sources:
            raw_items.extend(collect_rss(limit))
        raw_items = raw_items[:limit]
        logger.info("Total collected: %d items", len(raw_items))
    else:
        raw_items = _load_latest_raw()

    if not raw_items:
        logger.warning("No items collected from any source.")
        return 0

    if 2 in steps:
        saved = _save_raw(raw_items)
        logger.info("Step 2 complete: saved %d raw items", saved)
        if steps == [1, 2]:
            return 0

    if dry_run:
        logger.info("DRY RUN — skipping analysis and save.")
        for item in raw_items:
            logger.info("  Would analyze: %s (%s)", item.title[:60], item.url[:60])
        return 0

    analyzed: list[AnalyzedArticle | None] = []

    if 3 in steps:
        logger.info("Starting LLM analysis for %d items...", len(raw_items))
        try:
            provider = create_provider()
        except ValueError as e:
            logger.error("Failed to create LLM provider: %s", e)
            return 1

        for i, item in enumerate(raw_items):
            article = analyze_item(item, provider, len(raw_items), i)
            analyzed.append(article)

        logger.info("Step 3 complete: analyzed %d items", len(analyzed))
        if steps == [3]:
            return 0

    if 4 in steps:
        if not analyzed:
            for item in raw_items:
                analyzed.append(AnalyzedArticle(
                    title=item.title,
                    source_url=item.url,
                    source_type=item.source_type,
                    summary=item.content,
                    status="draft",
                    collected_at=item.collected_at,
                    raw_content=item.content,
                ))

        articles = organize(raw_items, analyzed)

        if not articles:
            logger.warning("No new articles to save (all duplicates?).")
            return 0

        saved = save_articles(articles)
        logger.info(
            "Step 4 complete: collected=%d, analyzed=%d, saved=%d",
            len(raw_items), len(articles), saved,
        )

    return 0


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Knowledge Base — four-step automation pipeline",
    )
    parser.add_argument(
        "--sources",
        default="github,rss",
        help='Comma-separated sources: github, rss (default: github,rss)',
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max items to collect (default: 20)",
    )
    parser.add_argument(
        "--steps",
        default="",
        help='Comma-separated step numbers (e.g. "1,2" or "3,4"; default: all)',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect only, skip LLM and save",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    for s in sources:
        if s not in ("github", "rss"):
            logger.error("Unknown source: %s (use github or rss)", s)
            sys.exit(1)

    steps = None
    if args.steps:
        try:
            steps = [int(s.strip()) for s in args.steps.split(",") if s.strip()]
            for s in steps:
                if s not in (1, 2, 3, 4):
                    logger.error("Invalid step: %d (use 1-4)", s)
                    sys.exit(1)
        except ValueError:
            logger.error("Invalid --steps format, use comma-separated numbers (e.g. '1,2')")
            sys.exit(1)

    logger.info(
        "Pipeline: sources=%s limit=%d steps=%s dry_run=%s",
        sources, args.limit, steps or "all", args.dry_run,
    )

    exit_code = run_pipeline(
        sources=sources,
        limit=args.limit,
        dry_run=args.dry_run,
        verbose=args.verbose,
        steps=steps,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
