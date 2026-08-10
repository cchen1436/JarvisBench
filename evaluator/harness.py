#!/usr/bin/env python3
"""Validated host-side bridge for a separately sealed evaluator bundle.

This process validates inputs and projects a narrow public score.  It is not a
security sandbox; the operator must provide filesystem and network isolation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


MAX_TREE_ENTRIES = 100_000
MAX_SCORE_BYTES = 5_000_000
MAX_SCORE_TYPES = 64
SAFE_TASK_ID = re.compile(r"[A-Za-z0-9_.:@+-]{1,160}\Z")
SAFE_SCORE_TYPE = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z")
SAFE_SCHEMA_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*\Z")


def require_directory(path: Path, *, writable: bool) -> Path:
    resolved = path.resolve(strict=True)
    if path.is_symlink() or not resolved.is_dir():
        raise SystemExit("evaluator paths must be real directories")
    if writable != os.access(resolved, os.W_OK):
        expectation = "writable" if writable else "read-only"
        raise SystemExit(f"path must be {expectation}")
    return resolved


def content_tree_checksum(root: Path) -> str:
    """Hash a regular-file tree without following links or special files."""

    digest = hashlib.sha256()
    entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    if len(entries) > MAX_TREE_ENTRIES:
        raise SystemExit("evaluator input tree exceeds its entry bound")
    for entry in entries:
        relative = entry.relative_to(root).as_posix()
        metadata = entry.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise SystemExit("evaluator input trees must not contain symlinks")
        if stat.S_ISDIR(metadata.st_mode):
            digest.update(b"directory\0" + relative.encode("utf-8") + b"\0")
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise SystemExit("evaluator input trees must contain only directories and files")
        file_digest = hashlib.sha256()
        with entry.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                file_digest.update(chunk)
        digest.update(
            b"file\0"
            + relative.encode("utf-8")
            + b"\0"
            + str(metadata.st_size).encode("ascii")
            + b"\0"
            + file_digest.digest()
        )
    return digest.hexdigest()


def _number(value: object, name: str, *, maximum: float = 1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SystemExit(f"sealed evaluator returned invalid {name}")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= maximum:
        raise SystemExit(f"sealed evaluator returned invalid {name}")
    return number


def project_public_score(raw: object) -> dict[str, object]:
    """Project an evaluator result to a text-free nested numeric schema.

    Evaluator-only checkpoint evidence and validity messages may be present in
    ``raw``.  They are deliberately ignored rather than copied recursively.
    """

    if not isinstance(raw, dict):
        raise SystemExit("sealed evaluator returned an invalid score contract")
    schema_version = raw.get("schema_version")
    task_id = raw.get("task_id")
    status_value = raw.get("status")
    if not isinstance(schema_version, str) or not SAFE_SCHEMA_VERSION.fullmatch(
        schema_version
    ):
        raise SystemExit("sealed evaluator returned an invalid schema version")
    if not isinstance(task_id, str) or not SAFE_TASK_ID.fullmatch(task_id):
        raise SystemExit("sealed evaluator returned an invalid task id")
    if status_value != "scored":
        raise SystemExit("sealed evaluator returned an invalid score status")

    by_type_raw = raw.get("by_type")
    if not isinstance(by_type_raw, dict) or len(by_type_raw) > MAX_SCORE_TYPES:
        raise SystemExit("sealed evaluator returned invalid score types")
    by_type: dict[str, dict[str, object]] = {}
    for score_type, value in sorted(by_type_raw.items()):
        if not isinstance(score_type, str) or not SAFE_SCORE_TYPE.fullmatch(score_type):
            raise SystemExit("sealed evaluator returned an invalid score type id")
        if not isinstance(value, dict):
            raise SystemExit("sealed evaluator returned an invalid score type")
        earned = _number(value.get("earned"), f"{score_type}.earned")
        weight = _number(value.get("weight"), f"{score_type}.weight")
        normalized_raw = value.get("normalized")
        if normalized_raw is None and weight == 0.0:
            normalized: float | None = None
        else:
            normalized = _number(normalized_raw, f"{score_type}.normalized")
        by_type[score_type] = {
            "earned": earned,
            "weight": weight,
            "normalized": normalized,
        }

    validity_raw = raw.get("validity")
    if not isinstance(validity_raw, dict) or not isinstance(validity_raw.get("ok"), bool):
        raise SystemExit("sealed evaluator returned invalid score validity")
    failures = validity_raw.get("failures", [])
    if not isinstance(failures, list) or len(failures) > 10_000:
        raise SystemExit("sealed evaluator returned invalid score validity")

    return {
        "schema_version": schema_version,
        "task_id": task_id,
        "status": "scored",
        "overall": _number(raw.get("overall"), "overall"),
        "by_type": by_type,
        # Never expose evaluator-authored failure/evidence text.  Only its
        # bounded cardinality crosses the public handoff.
        "validity": {"ok": validity_raw["ok"], "failure_count": len(failures)},
    }


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = require_directory(args.bundle, writable=False)
    run_dir = require_directory(args.run_dir, writable=False)
    if bundle == run_dir or _is_within(bundle, run_dir) or _is_within(run_dir, bundle):
        raise SystemExit("evaluator bundle and participant run directory must be disjoint")
    entrypoint = bundle / "grade.py"
    if not entrypoint.is_file() or entrypoint.is_symlink():
        raise SystemExit("sealed evaluator bundle has no regular grade.py")
    bundle_before = content_tree_checksum(bundle)
    run_before = content_tree_checksum(run_dir)
    with tempfile.TemporaryDirectory(prefix="jarvisbench-evaluator-") as temporary:
        grader_output = Path(temporary) / "grader_output.json"
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(entrypoint),
                    "--run-dir",
                    str(run_dir),
                    "--output",
                    str(grader_output),
                ],
                cwd=bundle,
                check=False,
                text=True,
                capture_output=True,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONNOUSERSITE": "1",
                },
            )
        except OSError:
            raise SystemExit("sealed evaluator could not be executed") from None
        bundle_after = content_tree_checksum(bundle)
        run_after = content_tree_checksum(run_dir)
        if bundle_after != bundle_before or run_after != run_before:
            # This is integrity detection, not a sandbox claim.  Operators
            # must still enforce immutable mounts outside this process.
            raise SystemExit("sealed evaluator modified an input tree")
        if completed.returncode or not grader_output.is_file() or grader_output.is_symlink():
            # Do not echo grader output: it can contain evaluator-only details.
            raise SystemExit("sealed evaluator failed")
        if grader_output.stat().st_size > MAX_SCORE_BYTES:
            raise SystemExit("sealed evaluator output exceeds its bound")
        try:
            raw = json.loads(grader_output.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise SystemExit("sealed evaluator returned an invalid score contract") from None
    score = project_public_score(raw)
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output_parent = output.parent.resolve(strict=True)
    if output.parent.is_symlink() or not output_parent.is_dir():
        raise SystemExit("public score output directory is unsafe")
    output_resolved = output_parent / output.name
    if _is_within(output_resolved, bundle) or _is_within(output_resolved, run_dir):
        raise SystemExit("public score output must be outside evaluator input trees")
    if output.exists() and (output.is_symlink() or not output.is_file()):
        raise SystemExit("public score output path is unsafe")
    encoded = (json.dumps(score, sort_keys=True, indent=2) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output_parent
    )
    temporary = Path(temporary_name)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, output_resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
