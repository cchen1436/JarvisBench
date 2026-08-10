#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verify(root: Path, manifest: Path) -> list[str]:
    failures: list[str] = []
    root = root.resolve()
    manifest_paths: set[str] = set()
    task_manifest_paths: set[str] = set()
    for line_no, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        try:
            expected, relative = line.split("  ", 1)
            path = PurePosixPath(relative)
            if len(expected) != 64 or path.is_absolute() or ".." in path.parts:
                raise ValueError
        except ValueError:
            failures.append(f"invalid checksum line {line_no}")
            continue
        normalized = path.as_posix()
        if normalized in manifest_paths:
            failures.append(f"duplicate checksum entry: {normalized}")
            continue
        manifest_paths.add(normalized)
        if path.parts and path.parts[0] == "tasks":
            task_manifest_paths.add(normalized)

        target = root / Path(normalized)
        if not target.is_file() or target.is_symlink():
            failures.append(f"missing regular file: {relative}")
        elif digest(target) != expected:
            failures.append(f"checksum mismatch: {relative}")

    task_root = root / "tasks"
    if task_root.is_dir():
        task_tree_paths = {
            candidate.relative_to(root).as_posix()
            for candidate in task_root.rglob("*")
            if candidate.is_symlink() or not candidate.is_dir()
        }
        for relative in sorted(task_tree_paths - task_manifest_paths):
            failures.append(f"extra task file: {relative}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    failures = verify(args.root, args.manifest)
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
