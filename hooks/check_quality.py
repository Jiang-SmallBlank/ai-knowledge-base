#!/usr/bin/env python3
"""5-dimension quality scoring for knowledge entry JSON files.

Usage:
    python hooks/check_quality.py <json_file> [json_file2 ...]
"""

import glob
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DimensionScore:
    name: str
    score: float
    max_score: float
    detail: str = ""


@dataclass
class QualityReport:
    filepath: str
    entry_id: str
    dimensions: list[DimensionScore] = field(default_factory=list)
    total_score: float = 0.0
    max_total: float = 100.0
    grade: str = ""

    def compute(self) -> None:
        self.total_score = sum(d.score for d in self.dimensions)
        if self.total_score >= 80:
            self.grade = "A"
        elif self.total_score >= 60:
            self.grade = "B"
        else:
            self.grade = "C"

    def summary_line(self) -> str:
        parts = [f"{d.name}: {d.score:.0f}/{d.max_score}" for d in self.dimensions]
        return f"  {' | '.join(parts)}  =>  Total: {self.total_score:.0f}/{self.max_total}  Grade: {self.grade}"


STANDARD_TAGS: set[str] = {
    "academic-research", "agent", "agent-architecture", "agent-framework",
    "agent-harness", "agent-memory", "ai", "ai-agent", "ai-assistant",
    "ai-assisted-writing", "ai-detection", "ai-engineering", "ai-platform",
    "ai-programming", "alignment", "announcement", "anthropic", "api",
    "automation", "backend", "benchmark", "best-practices", "business",
    "chatgpt", "claude", "claude-code", "cli", "cloud",
    "code-graph", "code-generation", "code-search", "code-understanding",
    "coding-agent", "collaboration", "comfyui", "computer-vision",
    "context-retention", "cpp", "cross-platform", "dataset",
    "data-extraction", "database", "deep-learning", "deepseek",
    "deployment", "design", "developer-tools", "devops", "devtools",
    "docker", "documentation", "economy", "edge", "education",
    "efficiency", "embedding", "ethics", "evaluation", "fastapi",
    "fine-tuning", "flask", "framework", "frontend", "full-stack-ai",
    "function-calling", "gemini", "gemma", "glm", "go", "gpt", "gpt-4",
    "gpt-5", "gradio", "guide", "haystack", "hermes", "humanizer",
    "image-generation", "indexing", "inference", "investment",
    "java", "javascript", "json-mode", "knowledge-graph", "kubernetes",
    "langchain", "learning-path", "library", "linux", "llama",
    "llamaindex", "llm", "llm-app", "llm-inference", "llm-ui",
    "local-ai", "low-code", "machine-learning", "mcp", "mistral",
    "mlops", "model-eval", "model-release", "monitoring",
    "multi-modal", "network", "nlp", "observability",
    "ollama", "on-device", "onnx", "open-source", "opencode",
    "orchestration", "paper", "personal-assistant", "persistence",
    "phi", "platform", "privacy", "privacy-first", "production",
    "programming", "prompt-engineering", "protocol", "pytorch",
    "python", "qwen", "quantization", "rag", "release", "research",
    "research-workflow", "rust", "sdk", "search", "security",
    "self-hosted", "skill-system", "small-llm", "speech", "stable-diffusion",
    "startup", "storage", "streamlit", "superpowers", "swift",
    "testing", "text-generation", "text-processing", "text-to-image",
    "text-to-speech", "text-to-video", "token-optimization",
    "tool-use", "tooling", "transformers", "tts", "tutorial",
    "typescript", "ui", "ux", "vector-database", "vibe-coding",
    "vision", "visual-programming", "voice-synthesis", "web-scraping",
    "web3", "webui", "workflow",
}

BUZZWORDS_CN: list[str] = [
    "赋能", "抓手", "闭环", "打通",
    "全链路", "底层逻辑", "颗粒度",
    "对齐", "拉通", "沉淀",
    "强大的", "革命性的",
]

BUZZWORDS_EN: list[str] = [
    "groundbreaking", "revolutionary",
    "game-changing", "cutting-edge",
    "state-of-the-art", "disruptive",
    "next-generation", "paradigm-shift",
    "bleeding-edge", "industry-leading",
]

TECH_KEYWORDS: set[str] = {
    "ai", "llm", "agent", "gpt", "model", "neural", "nlp",
    "rag", "embedding", "transformer", "attention",
    "fine-tuning", "finetuning", "inference",
    "deep learning", "machine learning", "reinforcement learning",
    "supervised", "unsupervised", "semi-supervised",
    "prompt", "token", "context window", "multimodal", "multi-modal",
    "claude", "openai", "chatgpt", "gemini", "llama", "mistral",
    "deepseek", "qwen", "glm", "langchain", "llamaindex",
    "pytorch", "tensorflow", "transformers",
    "python", "typescript", "rust", "go", "javascript",
    "agentic", "autonomous", "orchestration",
    "mcp", "function calling", "tool use",
    "向量", "模型", "智能", "算法", "语义",
    "知识图谱", "数据", "学习", "网络",
    "自动", "生成", "理解", "推理",
    "编码", "代码", "编程",
    "开源", "框架", "平台",
}

SCORE_FIELD_PATTERNS: list[str] = [
    "analysis.relevance_score",
    "relevance_score",
    "score",
]


def _get_score(entry: dict) -> float | None:
    for path in SCORE_FIELD_PATTERNS:
        parts = path.split(".")
        val: dict | float | int | None = entry
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                val = None
                break
        if isinstance(val, (int, float)) and 1 <= val <= 10:
            return float(val)
    return None


def _count_buzzwords(text: str) -> int:
    if not text:
        return 0
    lower = text.lower()
    total = 0
    for word in BUZZWORDS_CN:
        total += text.count(word)
    for word in BUZZWORDS_EN:
        total += lower.count(word.lower())
    return total


def _count_tech_keywords(text: str) -> int:
    if not text:
        return 0
    lower = text.lower()
    count = 0
    for kw in TECH_KEYWORDS:
        if kw.lower() in lower:
            count += 1
    return min(count, 5)


def _id_format_ok(entry_id: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9_]*-\d{8}-\d{3}$", entry_id))


def _url_format_ok(url: str) -> bool:
    return bool(re.match(r"^https?://", url))


def _timestamp_format_ok(ts: str) -> bool:
    return bool(re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts
    ))


def score_summary_quality(entry: dict) -> DimensionScore:
    summary = entry.get("summary", "")
    title = entry.get("title", "")

    if not isinstance(summary, str):
        return DimensionScore(
            "摘要质量", 0, 25, "summary missing or not a string"
        )

    text_len = len(summary)
    if text_len >= 50:
        base = 20
        detail = f"length={text_len} >= 50, full marks"
    elif text_len >= 40:
        base = 16
        detail = f"length={text_len} (40-49)"
    elif text_len >= 30:
        base = 12
        detail = f"length={text_len} (30-39)"
    elif text_len >= 20:
        base = 8
        detail = f"length={text_len} (20-29)"
    else:
        base = 0
        detail = f"length={text_len} < 20, no points"

    bonus = _count_tech_keywords(summary + " " + title) if isinstance(title, str) else _count_tech_keywords(summary)
    if bonus > 0:
        detail += f", tech keyword bonus +{bonus}"

    total = min(base + bonus, 25)
    return DimensionScore("摘要质量", total, 25, detail)


def score_tech_depth(entry: dict) -> DimensionScore:
    score_val = _get_score(entry)
    if score_val is None:
        return DimensionScore("技术深度", 0, 25, "no score field found")
    mapped = score_val / 10 * 25
    return DimensionScore(
        "技术深度", round(mapped, 1), 25,
        f"score={score_val:.0f}/10 -> {mapped:.1f}/25"
    )


def score_format_compliance(entry: dict) -> DimensionScore:
    score = 0.0
    details: list[str] = []

    entry_id = entry.get("id")
    if isinstance(entry_id, str) and _id_format_ok(entry_id):
        score += 4
        details.append("id:4")
    else:
        details.append(f"id:0 ({entry_id})")

    title = entry.get("title")
    if isinstance(title, str) and len(title.strip()) > 0:
        score += 4
        details.append("title:4")
    else:
        details.append("title:0")

    url = entry.get("source_url")
    if isinstance(url, str) and _url_format_ok(url):
        score += 4
        details.append("url:4")
    else:
        details.append(f"url:0 ({url})")

    status = entry.get("status")
    if isinstance(status, str) and status in (
        "draft", "review", "published", "archived"
    ):
        score += 4
        details.append("status:4")
    else:
        details.append(f"status:0 ({status})")

    ts_score = 0
    for ts_field in ("collected_at", "analyzed_at"):
        ts = entry.get(ts_field)
        if isinstance(ts, str) and _timestamp_format_ok(ts):
            ts_score += 2
        else:
            details.append(f"{ts_field}:invalid")
    score += ts_score
    details.append(f"timestamps:{ts_score:.0f}/4")

    return DimensionScore(
        "格式规范", score, 20, ", ".join(details)
    )


def score_tag_precision(entry: dict) -> DimensionScore:
    tags = entry.get("tags")
    if not isinstance(tags, list) or len(tags) == 0:
        return DimensionScore("标签精度", 0, 15, "no tags found")

    total_tags = len(tags)
    valid_count = sum(1 for t in tags if isinstance(t, str) and t in STANDARD_TAGS)
    valid_ratio = valid_count / total_tags

    base = valid_ratio * 15

    if total_tags > 5:
        penalty = 5
    elif total_tags > 3:
        penalty = 2
    else:
        penalty = 0

    final = max(base - penalty, 0)
    detail_parts = [
        f"valid={valid_count}/{total_tags}",
        f"ratio={valid_ratio:.0%}",
    ]
    if penalty > 0:
        detail_parts.append(f"penalty=-{penalty} (tags={total_tags}>3)")
    detail_parts.append(f"score={final:.1f}")

    return DimensionScore("标签精度", round(final, 1), 15, ", ".join(detail_parts))


def score_buzzword(entry: dict) -> DimensionScore:
    texts = [
        entry.get("title", ""),
        entry.get("summary", ""),
        entry.get("raw_content", ""),
    ]
    combined = " ".join(str(t) for t in texts if t)
    count = _count_buzzwords(combined)
    score = max(15 - count * 3, 0)
    detail = (
        f"buzzwords found={count}, " + (f"deducted={count * 3}" if count > 0 else "clean")
    ) if count > 0 else "no buzzwords found"
    return DimensionScore("空洞词检测", score, 15, detail)


DIMENSION_FUNCS = [
    score_summary_quality,
    score_tech_depth,
    score_format_compliance,
    score_tag_precision,
    score_buzzword,
]


def evaluate_entry(entry: dict, filepath: str) -> QualityReport:
    entry_id = entry.get("id", "unknown") if isinstance(entry.get("id"), str) else "unknown"
    report = QualityReport(filepath=filepath, entry_id=entry_id)
    for func in DIMENSION_FUNCS:
        report.dimensions.append(func(entry))
    report.compute()
    return report


def print_progress(current: int, total: int) -> None:
    bar_len = 30
    percent = current / total if total > 0 else 1
    filled = int(bar_len * percent)
    bar = "#" * filled + "-" * (bar_len - filled)
    sys.stdout.write(f"\rProcessing: |{bar}| {current}/{total} ({percent:.0%})")
    sys.stdout.flush()


def validate_file(filepath: str) -> list[QualityReport]:
    path = Path(filepath)
    if not path.exists():
        print(f"\nERROR: file not found: {filepath}")
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"\nERROR: invalid JSON in {filepath}: {e}")
        return []

    entries = data if isinstance(data, list) else [data]
    reports: list[QualityReport] = []

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            print(f"\nERROR: {filepath}: entry {i} is not a JSON object")
            continue
        reports.append(evaluate_entry(entry, filepath))
        print_progress(i + 1, len(entries))

    print()
    return reports


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python hooks/check_quality.py <json_file> [json_file2 ...]")
        sys.exit(1)

    files_to_check: list[str] = []
    for arg in sys.argv[1:]:
        expanded = glob.glob(arg)
        if expanded:
            files_to_check.extend(expanded)
        else:
            files_to_check.append(arg)

    if not files_to_check:
        print("ERROR: no files to validate")
        sys.exit(1)

    all_reports: list[QualityReport] = []
    total_files = 0
    has_c = False

    for filepath in sorted(set(files_to_check)):
        reports = validate_file(filepath)
        total_files += 1
        all_reports.extend(reports)

    print()
    for report in all_reports:
        print(f"File: {report.filepath} (id: {report.entry_id})")
        for dim in report.dimensions:
            if dim.detail:
                print(f"  {dim.name:8s} {dim.score:5.1f}/{dim.max_score:<2}  [{dim.detail}]")
            else:
                print(f"  {dim.name:8s} {dim.score:5.1f}/{dim.max_score:<2}")
        print(f"  {'─' * 40}")
        print(f"  Total: {report.total_score:.0f}/{report.max_total}  Grade: {report.grade}")
        print()
        if report.grade == "C":
            has_c = True

    total_entries = len(all_reports)
    grades = [r.grade for r in all_reports]
    grade_counts = {g: grades.count(g) for g in ("A", "B", "C")}
    avg = sum(r.total_score for r in all_reports) / total_entries if total_entries > 0 else 0

    print(f"Summary: {total_files} file(s), {total_entries} entry(ies), "
          f"avg {avg:.0f}/100  "
          f"(A:{grade_counts['A']} B:{grade_counts['B']} C:{grade_counts['C']})")

    if has_c:
        sys.exit(1)
    print("All entries passed (grade A or B).")
    sys.exit(0)


if __name__ == "__main__":
    main()
