from __future__ import annotations

import json
import re
from pathlib import Path


DENIED_PATH_PARTS = {
    "private",
    "rubric",
    "reference_solution",
    "partial_solution",
    "requester_profile",
    "jarvis_user_context_v1.json",
    ".env",
    ".ssh",
    ".openclaw",
}
DENIED_TEXT_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)api[_-]?key[ \t]*[:=][ \t]*[\"']?[A-Za-z0-9_-]{12,}"),
    # Split the server prefix so the scanner does not flag its own source.
    re.compile("/" + "lustre" + "/fsw/"),
)
IGNORED_TOP_LEVEL = {".git", ".venv", ".pytest_cache", "build", "dist", "results"}


def assert_safe_relative_path(path: Path) -> None:
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe path: {path}")
    lowered = {part.lower() for part in path.parts}
    if lowered & DENIED_PATH_PARTS:
        raise ValueError(f"denied path: {path}")


def scan_release_tree(root: Path) -> list[str]:
    findings: list[str] = []
    root = Path(root).resolve()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in IGNORED_TOP_LEVEL:
            continue
        try:
            assert_safe_relative_path(rel)
        except ValueError as exc:
            findings.append(str(exc))
            continue
        if path.is_symlink():
            findings.append(f"symlink forbidden: {rel}")
            continue
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in DENIED_TEXT_PATTERNS:
            if pattern.search(text):
                findings.append(f"sensitive pattern in {rel}: {pattern.pattern}")
    return findings


def load_public_task(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    forbidden = {"attention", "grading", "private", "profile_visibility", "worker_only_overall_ceiling"}

    def walk(value: object) -> None:
        if isinstance(value, dict):
            overlap = forbidden & set(value)
            if overlap:
                raise ValueError(f"public task contains forbidden keys: {sorted(overlap)}")
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return data
