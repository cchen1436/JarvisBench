#!/usr/bin/env python3
"""Export the canonical public JarvisBench task surface.

The canonical research checkout intentionally co-locates public task inputs,
requester-private profiles, and evaluator-only material.  This exporter never
copies a directory recursively from that checkout.  It reads two frozen task
selectors, projects an explicitly bounded subset of each selected task
manifest, and copies only files declared by ``assets.public``.

The destination must already exist and be empty.  Export is assembled in a
sibling temporary directory and replaces the empty destination only after all
validation and checksum generation succeeds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit


SCHEMA_VERSION = "1.0"
POLICY_ID = "jarvisbench-canonical-public-allowlist-v1"
EXPECTED_SINGLE_TASKS = 20
EXPECTED_MULTI_TASKS = 10
MAX_CONTROL_FILE_BYTES = 8 * 1024 * 1024
MAX_PUBLIC_FILE_BYTES = 256 * 1024 * 1024

# These are the only canonical-source path classes this program reads.  The
# templates are also recorded in sanitized provenance so an export can be
# audited without revealing an absolute source path.
SOURCE_READ_ALLOWLIST = (
    "RELEASE.json#canonical_modes.{merge,split}.sha256_manifest_sha256",
    "single_agent_v1/reports/release_manifest.json#tasks[].task_id",
    "single_agent_v1/benchmark/tasks/<selected-task>/task.json#public-projection",
    "single_agent_v1/benchmark/tasks/<selected-task>/public/**",
    "multi_agent_v1/mas_v1/release_v1_tasks.txt",
    "multi_agent_v1/mas_v1/benchmark/tasks/<selected-task>/task.json#public-projection",
    "multi_agent_v1/mas_v1/benchmark/tasks/<selected-task>/public/**",
)

DENIED_CATEGORIES = (
    "credentials_and_environment",
    "requester_private_profiles_and_memory",
    "evaluator_graders_rubrics_and_reference_material",
    "raw_runs_traces_session_stores_and_logs",
    "research_archives_reports_and_source_snapshots",
    "server_runtime_images_and_bootstrap_archives",
    "reference_controller_implementations",
)

SOURCE_FIELDS = (
    "adaptation",
    "benchmark",
    "license",
    "revision",
    "task_id",
    "url",
)
RUNTIME_FIELDS = (
    "cpus",
    "family",
    "memory_gb",
    "modalities",
    "worker_timeout_seconds",
)
BASELINE_FIELDS = ("model", "on_question", "user_availability")
ROOT_PUBLIC_FIELDS = (
    "schema_version",
    "task_id",
    "title",
    "domain",
    "summary",
    "source",
    "runtime",
    "episode",
    "baseline",
    "assets",
)

TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

FORBIDDEN_PATH_COMPONENTS = {
    ".supervisor_private",
    "controller_private",
    "evaluator",
    "grader",
    "grading",
    "partial_solution",
    "private",
    "private_history",
    "reference_solution",
    "rubric",
}
FORBIDDEN_FILE_NAMES = {
    ".env",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets",
    "secrets.json",
}
FORBIDDEN_FILE_SUFFIXES = {".key", ".p12", ".pfx"}

TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".conf",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"),
    re.compile(
        rb"(?:OPENAI_API_KEY|MY_PROXY_API_KEY|NVIDIA_API_KEY|AWS_SECRET_ACCESS_KEY)"
        rb"\s*[:=]\s*[\"']?(?!<|\$\{|example|placeholder|changeme|your[_-])"
        rb"[^\s\"']{12,}",
        re.IGNORECASE,
    ),
)
SECRET_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "key",
    "secret",
    "token",
}


class ReleaseExportError(RuntimeError):
    """A safe, non-content-bearing export failure."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    path.chmod(0o644)


def _require_regular_file(path: Path, *, label: str, max_bytes: int) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseExportError(f"required {label} was unavailable") from exc
    if stat.S_ISLNK(info.st_mode):
        raise ReleaseExportError(f"{label} must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise ReleaseExportError(f"{label} must be a regular file")
    if info.st_size > max_bytes:
        raise ReleaseExportError(f"{label} exceeded its size bound")
    return info


def _require_directory(path: Path, *, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseExportError(f"required {label} was unavailable") from exc
    if stat.S_ISLNK(info.st_mode):
        raise ReleaseExportError(f"{label} must not be a symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise ReleaseExportError(f"{label} must be a directory")


def _read_bounded(path: Path, *, label: str, max_bytes: int) -> bytes:
    info = _require_regular_file(path, label=label, max_bytes=max_bytes)
    try:
        with path.open("rb") as handle:
            value = handle.read(max_bytes + 1)
    except OSError as exc:
        raise ReleaseExportError(f"unable to read {label}") from exc
    if len(value) != info.st_size or len(value) > max_bytes:
        raise ReleaseExportError(f"{label} changed while it was read")
    return value


def _read_json(path: Path, *, label: str) -> Mapping[str, Any]:
    raw = _read_bounded(path, label=label, max_bytes=MAX_CONTROL_FILE_BYTES)
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseExportError(f"{label} was not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseExportError(f"{label} must contain one JSON object")
    return value


def _validate_task_id(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not TASK_ID_RE.fullmatch(value):
        raise ReleaseExportError(f"{label} contained an unsafe task identifier")
    if value in {".", ".."}:
        raise ReleaseExportError(f"{label} contained an unsafe task identifier")
    return value


def _safe_relative_path(value: Any, *, label: str, first_component: str | None = None) -> str:
    if not isinstance(value, str) or not value or CONTROL_CHAR_RE.search(value):
        raise ReleaseExportError(f"{label} contained an unsafe relative path")
    if "\\" in value:
        raise ReleaseExportError(f"{label} contained a non-POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix():
        raise ReleaseExportError(f"{label} contained an unsafe relative path")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseExportError(f"{label} contained path traversal")
    if first_component is not None and (
        not path.parts or path.parts[0] != first_component
    ):
        raise ReleaseExportError(f"{label} escaped its allowed root")
    for part in path.parts:
        normalized = part.casefold()
        if (
            normalized in FORBIDDEN_PATH_COMPONENTS
            or PurePosixPath(normalized).stem in FORBIDDEN_PATH_COMPONENTS
        ):
            raise ReleaseExportError(f"{label} entered a denied path category")
        if normalized == ".env" or normalized.startswith(".env."):
            raise ReleaseExportError(f"{label} referenced an environment file")
        if normalized in FORBIDDEN_FILE_NAMES:
            raise ReleaseExportError(f"{label} referenced a credential file")
        if PurePosixPath(normalized).suffix in FORBIDDEN_FILE_SUFFIXES:
            raise ReleaseExportError(f"{label} referenced a credential file")
    return path.as_posix()


def _assert_no_symlink_components(
    root: Path,
    path: Path,
    *,
    label: str,
    allow_missing_leaf: bool = False,
) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ReleaseExportError(f"{label} escaped the canonical source") from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError as exc:
            if allow_missing_leaf:
                return False
            raise ReleaseExportError(f"required {label} was unavailable") from exc
        except OSError as exc:
            raise ReleaseExportError(f"required {label} was unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ReleaseExportError(f"{label} crossed a symlink")
    return True


def _safe_string(value: Any, *, label: str, max_chars: int = 200_000) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > max_chars
        or "\x00" in value
    ):
        raise ReleaseExportError(f"{label} must be a bounded non-empty string")
    return value


def _safe_string_list(
    value: Any, *, label: str, max_items: int = 256, max_chars: int = 4096
) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ReleaseExportError(f"{label} must be a bounded list")
    result: list[str] = []
    for item in value:
        result.append(_safe_string(item, label=label, max_chars=max_chars))
    if len(result) != len(set(result)):
        raise ReleaseExportError(f"{label} contained duplicate values")
    return result


def _safe_positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReleaseExportError(f"{label} must be a positive integer")
    return value


def _project_string_object(
    value: Any, fields: Sequence[str], *, label: str
) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ReleaseExportError(f"{label} must be an object")
    return {
        field: _safe_string(value.get(field), label=f"{label}.{field}")
        for field in fields
    }


def _validate_public_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ReleaseExportError("source.url was not a public HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ReleaseExportError("source.url contained embedded credentials")
    if any(key.casefold() in SECRET_QUERY_KEYS for key, _ in parse_qsl(parsed.query)):
        raise ReleaseExportError("source.url contained a credential query parameter")


def _project_runtime(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseExportError("runtime must be an object")
    result = {
        "cpus": _safe_positive_int(value.get("cpus"), label="runtime.cpus"),
        "family": _safe_string(value.get("family"), label="runtime.family"),
        "memory_gb": _safe_positive_int(
            value.get("memory_gb"), label="runtime.memory_gb"
        ),
        "modalities": _safe_string_list(
            value.get("modalities"), label="runtime.modalities", max_items=32
        ),
        "worker_timeout_seconds": _safe_positive_int(
            value.get("worker_timeout_seconds"),
            label="runtime.worker_timeout_seconds",
        ),
    }
    if set(result) != set(RUNTIME_FIELDS):
        raise AssertionError("runtime projection drifted from its allowlist")
    return result


def _project_episode(value: Any, *, topology: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseExportError("episode must be an object")
    result_paths = _safe_string_list(
        value.get("result_paths"), label="episode.result_paths", max_items=64
    )
    if not result_paths:
        raise ReleaseExportError("episode.result_paths must not be empty")
    normalized_results = [
        _safe_relative_path(
            item, label="episode.result_paths", first_component="results"
        )
        for item in result_paths
    ]
    result: dict[str, Any] = {
        "brief": _safe_string(
            value.get("brief"), label="episode.brief", max_chars=500_000
        ),
        "result_paths": normalized_results,
    }
    if topology == "multi_agent":
        result["worker_count"] = _safe_positive_int(
            value.get("worker_count"), label="episode.worker_count"
        )
    return result


def _contains_secret(value: bytes) -> bool:
    return any(pattern.search(value) is not None for pattern in SECRET_PATTERNS)


def _read_public_file_content(path: Path, relative: str) -> bytes:
    value = _read_bounded(
        path,
        label=f"public asset {relative}",
        max_bytes=MAX_PUBLIC_FILE_BYTES,
    )
    if path.suffix.casefold() in TEXT_SUFFIXES and _contains_secret(value):
        raise ReleaseExportError(
            f"public asset {relative} contained credential-like content"
        )
    return value


def _discover_public_files(
    source_root: Path, task_dir: Path, public_dir: Path
) -> list[str]:
    _assert_no_symlink_components(source_root, public_dir, label="public asset directory")
    _require_directory(public_dir, label="public asset directory")
    result: list[str] = []
    stack = [public_dir]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda item: item.name)
        except OSError as exc:
            raise ReleaseExportError("unable to enumerate a public asset directory") from exc
        for entry in entries:
            candidate = Path(entry.path)
            relative = candidate.relative_to(task_dir).as_posix()
            _safe_relative_path(
                relative, label="public asset path", first_component="public"
            )
            if entry.is_symlink():
                raise ReleaseExportError(f"public asset {relative} must not be a symlink")
            if entry.is_dir(follow_symlinks=False):
                stack.append(candidate)
            elif entry.is_file(follow_symlinks=False):
                _read_public_file_content(candidate, relative)
                result.append(relative)
            else:
                raise ReleaseExportError(
                    f"public asset {relative} was not a regular file or directory"
                )
    return sorted(result)


def _declared_public_coverage(
    source_root: Path,
    task_dir: Path,
    declared: Sequence[str],
    discovered_files: Sequence[str],
) -> None:
    """Require every public file to be covered by a declared file/directory.

    Canonical MAS manifests sometimes declare a public directory rather than
    enumerating each file below it.  A declaration is therefore valid only
    when its source node is a regular file or directory, and every discovered
    file is equal to or below at least one declaration.  The frozen MAS source
    has one inert declaration for a workspace directory that is absent from
    the immutable public tree; a safe absent declaration is retained in the
    projection but cannot authorize or copy any file.
    """

    declarations: list[tuple[PurePosixPath, bool]] = []
    for relative in declared:
        source = task_dir / Path(relative)
        exists = _assert_no_symlink_components(
            source_root,
            source,
            label="declared public asset",
            allow_missing_leaf=True,
        )
        if not exists:
            declarations.append((PurePosixPath(relative), False))
            continue
        try:
            info = source.lstat()
        except OSError as exc:
            raise ReleaseExportError("a declared public asset was unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ReleaseExportError("a declared public asset must not be a symlink")
        if stat.S_ISREG(info.st_mode):
            declarations.append((PurePosixPath(relative), False))
        elif stat.S_ISDIR(info.st_mode):
            declarations.append((PurePosixPath(relative), True))
        else:
            raise ReleaseExportError(
                "a declared public asset was not a regular file or directory"
            )

    for relative in discovered_files:
        candidate = PurePosixPath(relative)
        covered = any(
            candidate == declaration
            or (is_directory and declaration in candidate.parents)
            for declaration, is_directory in declarations
        )
        if not covered:
            raise ReleaseExportError(
                "assets.public did not exactly cover the selected public tree"
            )


def _project_task(
    source_root: Path,
    task_dir: Path,
    *,
    expected_task_id: str,
    topology: str,
) -> tuple[dict[str, Any], list[str]]:
    _assert_no_symlink_components(source_root, task_dir, label="selected task directory")
    _require_directory(task_dir, label="selected task directory")
    manifest_path = task_dir / "task.json"
    _assert_no_symlink_components(source_root, manifest_path, label="selected task manifest")
    manifest = _read_json(manifest_path, label="selected task manifest")
    manifest_task_id = _validate_task_id(
        manifest.get("task_id"), label="selected task manifest"
    )
    if manifest_task_id != expected_task_id:
        raise ReleaseExportError("selected task manifest identity did not match its selector")

    source = _project_string_object(
        manifest.get("source"), SOURCE_FIELDS, label="source"
    )
    _validate_public_url(source["url"])

    assets = manifest.get("assets")
    if not isinstance(assets, dict):
        raise ReleaseExportError("assets must be an object")
    declared = _safe_string_list(
        assets.get("public"), label="assets.public", max_items=4096
    )
    declared = [
        _safe_relative_path(
            value, label="assets.public", first_component="public"
        )
        for value in declared
    ]
    discovered = _discover_public_files(source_root, task_dir, task_dir / "public")
    _declared_public_coverage(source_root, task_dir, declared, discovered)

    projected: dict[str, Any] = {
        "schema_version": _safe_string(
            manifest.get("schema_version"), label="schema_version"
        ),
        "task_id": manifest_task_id,
        "title": _safe_string(manifest.get("title"), label="title"),
        "domain": _safe_string(manifest.get("domain"), label="domain"),
        "summary": _safe_string(
            manifest.get("summary"), label="summary", max_chars=50_000
        ),
        "source": source,
        "runtime": _project_runtime(manifest.get("runtime")),
        "episode": _project_episode(manifest.get("episode"), topology=topology),
        "baseline": _project_string_object(
            manifest.get("baseline"), BASELINE_FIELDS, label="baseline"
        ),
        # Validator descriptions are evaluator metadata in the canonical tree.
        # They are intentionally not copied into the participant projection.
        "assets": {"public": declared},
    }
    if tuple(projected) != ROOT_PUBLIC_FIELDS:
        raise AssertionError("task projection drifted from its root allowlist")
    rendered = _canonical_json(projected)
    if _contains_secret(rendered):
        raise ReleaseExportError(
            "selected public task projection contained credential-like content"
        )
    return projected, discovered


def _selected_single_tasks(source_root: Path) -> tuple[list[str], str]:
    path = source_root / "single_agent_v1" / "reports" / "release_manifest.json"
    _assert_no_symlink_components(source_root, path, label="single task selector")
    raw = _read_bounded(
        path, label="single task selector", max_bytes=MAX_CONTROL_FILE_BYTES
    )
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseExportError("single task selector was not strict UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("tasks"), list):
        raise ReleaseExportError("single task selector had an invalid schema")
    tasks = []
    for entry in manifest["tasks"]:
        if not isinstance(entry, dict):
            raise ReleaseExportError("single task selector had an invalid task entry")
        tasks.append(_validate_task_id(entry.get("task_id"), label="single task selector"))
    if len(tasks) != EXPECTED_SINGLE_TASKS or len(set(tasks)) != len(tasks):
        raise ReleaseExportError(
            f"single task selector must contain {EXPECTED_SINGLE_TASKS} unique tasks"
        )
    return tasks, _sha256_bytes(raw)


def _selected_multi_tasks(source_root: Path) -> tuple[list[str], str]:
    path = source_root / "multi_agent_v1" / "mas_v1" / "release_v1_tasks.txt"
    _assert_no_symlink_components(source_root, path, label="multi task selector")
    raw = _read_bounded(
        path, label="multi task selector", max_bytes=MAX_CONTROL_FILE_BYTES
    )
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseExportError("multi task selector was not UTF-8") from exc
    if "\r" in decoded or "\x00" in decoded:
        raise ReleaseExportError("multi task selector used unsafe framing")
    lines = decoded.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or any(not value or value != value.strip() for value in lines):
        raise ReleaseExportError("multi task selector contained an empty or padded entry")
    tasks = [_validate_task_id(value, label="multi task selector") for value in lines]
    if len(tasks) != EXPECTED_MULTI_TASKS or len(set(tasks)) != len(tasks):
        raise ReleaseExportError(
            f"multi task selector must contain {EXPECTED_MULTI_TASKS} unique tasks"
        )
    return tasks, _sha256_bytes(raw)


def _canonical_mode_hashes(source_root: Path) -> tuple[dict[str, str], str]:
    path = source_root / "RELEASE.json"
    _assert_no_symlink_components(source_root, path, label="canonical release receipt")
    raw = _read_bounded(
        path, label="canonical release receipt", max_bytes=MAX_CONTROL_FILE_BYTES
    )
    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseExportError(
            "canonical release receipt was not strict UTF-8 JSON"
        ) from exc
    try:
        modes = receipt["canonical_modes"]
        result = {
            name: modes[name]["sha256_manifest_sha256"]
            for name in ("merge", "split")
        }
    except (KeyError, TypeError) as exc:
        raise ReleaseExportError("canonical release receipt lacked frozen mode hashes") from exc
    if any(not isinstance(value, str) or not SHA256_RE.fullmatch(value) for value in result.values()):
        raise ReleaseExportError("canonical release receipt had an invalid mode hash")
    return result, _sha256_bytes(raw)


def _copy_public_assets(
    task_dir: Path,
    output_task_dir: Path,
    public_paths: Iterable[str],
) -> None:
    for relative in public_paths:
        source = task_dir / Path(relative)
        target = output_task_dir / Path(relative)
        # Revalidate at copy time so a source mutation after planning cannot
        # silently enter the release.  Copy bytes, not metadata, to avoid
        # propagating server modes.
        _write_bytes(target, _read_public_file_content(source, relative))


def _tree_sha256(paths: Iterable[Path], base: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(base).as_posix()):
        relative = path.relative_to(base).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256_file(path)))
    return digest.hexdigest()


def _regular_output_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            candidate = current_path / name
            if candidate.is_symlink():
                raise ReleaseExportError("export staging unexpectedly contained a symlink")
        for name in files:
            candidate = current_path / name
            info = candidate.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ReleaseExportError(
                    "export staging unexpectedly contained a non-regular file"
                )
            result.append(candidate)
    return result


def _write_checksums(staging: Path) -> None:
    files = [
        path
        for path in _regular_output_files(staging)
        if path.relative_to(staging).as_posix() != "SHA256SUMS"
    ]
    lines = [
        f"{_sha256_file(path)}  {path.relative_to(staging).as_posix()}"
        for path in sorted(files, key=lambda item: item.relative_to(staging).as_posix())
    ]
    _write_bytes(staging / "SHA256SUMS", ("\n".join(lines) + "\n").encode("utf-8"))


def _task_provenance(output_task_dir: Path) -> dict[str, str]:
    public_files = [
        path
        for path in _regular_output_files(output_task_dir / "public")
    ]
    return {
        "task_public_manifest_sha256": _sha256_file(
            output_task_dir / "task.public.json"
        ),
        "public_assets_tree_sha256": _tree_sha256(public_files, output_task_dir),
    }


def export_release(source: Path, output: Path) -> None:
    """Export the canonical participant-visible task surface."""

    source = source.absolute()
    output = output.absolute()
    _require_directory(source, label="canonical source root")
    _require_directory(output, label="release output root")
    if source.is_symlink() or output.is_symlink():
        raise ReleaseExportError("source and output roots must not be symlinks")
    try:
        if any(output.iterdir()):
            raise ReleaseExportError("release output root must be empty")
    except OSError as exc:
        raise ReleaseExportError("unable to inspect release output root") from exc

    source_resolved = source.resolve()
    output_resolved = output.resolve()
    if (
        source_resolved == output_resolved
        or source_resolved in output_resolved.parents
        or output_resolved in source_resolved.parents
    ):
        raise ReleaseExportError("source and output roots must not overlap")

    mode_hashes, release_receipt_hash = _canonical_mode_hashes(source)
    single_tasks, single_selector_hash = _selected_single_tasks(source)
    multi_tasks, multi_selector_hash = _selected_multi_tasks(source)

    # Validate and project every source before creating any exported content.
    plans: dict[str, list[tuple[str, Path, dict[str, Any], list[str]]]] = {
        "single_agent": [],
        "multi_agent": [],
    }
    roots = {
        "single_agent": source / "single_agent_v1" / "benchmark" / "tasks",
        "multi_agent": source
        / "multi_agent_v1"
        / "mas_v1"
        / "benchmark"
        / "tasks",
    }
    for topology, task_ids in (
        ("single_agent", single_tasks),
        ("multi_agent", multi_tasks),
    ):
        for task_id in task_ids:
            task_dir = roots[topology] / task_id
            projected, public_paths = _project_task(
                source,
                task_dir,
                expected_task_id=task_id,
                topology=topology,
            )
            plans[topology].append(
                (task_id, task_dir, projected, public_paths)
            )

    staging_path = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)
    )
    staging_path.chmod(0o700)
    published = False
    try:
        topology_provenance: dict[str, Any] = {}
        for topology, plan in plans.items():
            task_receipts: dict[str, dict[str, str]] = {}
            for task_id, task_dir, projected, public_paths in plan:
                output_task_dir = staging_path / "tasks" / topology / task_id
                _write_bytes(
                    output_task_dir / "task.public.json",
                    _canonical_json(projected),
                )
                _copy_public_assets(task_dir, output_task_dir, public_paths)
                task_receipts[task_id] = _task_provenance(output_task_dir)
            topology_provenance[topology] = {
                "task_count": len(plan),
                "task_ids": [entry[0] for entry in plan],
                "tasks": task_receipts,
            }

        provenance = {
            "schema_version": SCHEMA_VERSION,
            "format": "jarvisbench-public-task-export",
            "policy_id": POLICY_ID,
            "source_read_allowlist": list(SOURCE_READ_ALLOWLIST),
            "denied_categories": list(DENIED_CATEGORIES),
            "canonical_identity": {
                "release_receipt_sha256": release_receipt_hash,
                "single_selection_receipt_sha256": single_selector_hash,
                "multi_selection_receipt_sha256": multi_selector_hash,
                "frozen_single_modes": mode_hashes,
            },
            "topologies": topology_provenance,
        }
        rendered_provenance = _canonical_json(provenance)
        if _contains_secret(rendered_provenance):
            raise ReleaseExportError("sanitized provenance failed its credential scan")
        _write_bytes(staging_path / "PROVENANCE.json", rendered_provenance)
        _write_checksums(staging_path)

        # Publish only after staging is complete.  The caller supplied the
        # required empty directory, so replacing it cannot overwrite content.
        staging_path.chmod(0o755)
        output.rmdir()
        os.replace(staging_path, output)
        published = True
    except ReleaseExportError:
        raise
    except OSError as exc:
        raise ReleaseExportError("filesystem failure while publishing release") from exc
    finally:
        if not published and staging_path.exists():
            shutil.rmtree(staging_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export the fail-closed public JarvisBench task surface."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        export_release(args.source, args.output)
    except ReleaseExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception:
        # Never let an unexpected parser/filesystem exception print source
        # content or an absolute path through a traceback in normal CLI use.
        print("error: unexpected fail-closed export failure", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
