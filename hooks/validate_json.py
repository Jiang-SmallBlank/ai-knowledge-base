#!/usr/bin/env python3
"""Validate knowledge entry JSON files.

Usage:
    python hooks/validate_json.py <json_file> [json_file2 ...]
"""

import glob
import json
import re
import sys
from pathlib import Path


REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "title": str,
    "source_url": str,
    "summary": str,
    "tags": list,
    "status": str,
}

VALID_STATUSES = {"draft", "review", "published", "archived"}
ID_PATTERN = re.compile(r"^\d{8}(?:-[a-z]{2})?-\d{3}$")
URL_PATTERN = re.compile(r"^https?://")
VALID_AUDIENCES = {"beginner", "intermediate", "advanced"}
SUMMARY_MIN_LENGTH = 20
TAGS_MIN_COUNT = 1
SCORE_MIN = 1
SCORE_MAX = 10


def validate_entry(entry: dict, filepath: str) -> list[str]:
    """Validate a single knowledge entry dict.

    Args:
        entry: The knowledge entry dict to validate.
        filepath: Source file path for error messages.

    Returns:
        A list of error messages (empty if valid).
    """
    errors: list[str] = []

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in entry:
            errors.append(f"  missing field: {field}")
        elif not isinstance(entry[field], expected_type):
            errors.append(
                f"  field '{field}' type error: expected {expected_type.__name__}, "
                f"got {type(entry[field]).__name__}"
            )

    if "id" in entry and isinstance(entry.get("id"), str):
        if not ID_PATTERN.match(entry["id"]):
            errors.append(
                f"  invalid id format: '{entry['id']}' "
                f"(expected {{YYYYMMDD}}-[{{prefix}}]-{{NNN}}, "
                f"e.g. 20260524-gh-013)"
            )

    if "status" in entry and isinstance(entry.get("status"), str):
        if entry["status"] not in VALID_STATUSES:
            errors.append(
                f"  invalid status: '{entry['status']}' "
                f"(expected one of {', '.join(sorted(VALID_STATUSES))})"
            )

    if "source_url" in entry and isinstance(entry.get("source_url"), str):
        if not URL_PATTERN.match(entry["source_url"]):
            errors.append(
                f"  invalid source_url: '{entry['source_url']}' "
                f"(expected https?://...)"
            )

    if "summary" in entry and isinstance(entry.get("summary"), str):
        if len(entry["summary"]) < SUMMARY_MIN_LENGTH:
            errors.append(
                f"  summary too short: {len(entry['summary'])} chars "
                f"(minimum {SUMMARY_MIN_LENGTH})"
            )

    if "tags" in entry and isinstance(entry.get("tags"), list):
        if len(entry["tags"]) < TAGS_MIN_COUNT:
            errors.append(
                f"  tags must have at least {TAGS_MIN_COUNT} item(s)"
            )

    if "score" in entry:
        score = entry["score"]
        if not isinstance(score, (int, float)):
            errors.append(
                f"  score type error: expected int/float, "
                f"got {type(score).__name__}"
            )
        elif not (SCORE_MIN <= score <= SCORE_MAX):
            errors.append(
                f"  score out of range: {score} "
                f"(expected {SCORE_MIN}-{SCORE_MAX})"
            )

    if "audience" in entry:
        audience = entry["audience"]
        if audience not in VALID_AUDIENCES:
            errors.append(
                f"  invalid audience: '{audience}' "
                f"(expected one of {', '.join(sorted(VALID_AUDIENCES))})"
            )

    return errors


def validate_file(filepath: str) -> tuple[int, int]:
    """Validate a JSON file.

    Args:
        filepath: Path to the JSON file.

    Returns:
        Tuple of (entries_checked, error_count).
    """
    path = Path(filepath)

    if not path.exists():
        print(f"ERROR: file not found: {filepath}")
        return 0, 1

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in {filepath}")
        print(f"  {e}")
        return 0, 1

    entries = data if isinstance(data, list) else [data]
    total_errors = 0

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            print(f"ERROR: {filepath}: entry {i} is not a JSON object")
            total_errors += 1
            continue

        entry_errors = validate_entry(entry, filepath)
        if entry_errors:
            entry_id = entry.get("id", f"index {i}")
            print(f"FAIL: {filepath} (id: {entry_id})")
            for err in entry_errors:
                print(err)
            total_errors += len(entry_errors)

    return len(entries), total_errors


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python hooks/validate_json.py <json_file> [json_file2 ...]")
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

    total_files = 0
    total_errors = 0
    total_entries = 0

    for filepath in sorted(set(files_to_check)):
        entries_count, error_count = validate_file(filepath)
        total_files += 1
        total_entries += entries_count
        total_errors += error_count

    print()
    print(f"Summary: {total_files} file(s), {total_entries} entry(ies), "
          f"{total_errors} error(s)")

    if total_errors > 0:
        sys.exit(1)

    print("All entries valid.")
    sys.exit(0)


if __name__ == "__main__":
    main()
