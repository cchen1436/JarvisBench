#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def run(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    if shutil.which(command[0]) is None:
        return None
    try:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def version(command: list[str]) -> str | None:
    result = run(command)
    if result is None or result.returncode != 0:
        return None
    lines = (result.stdout or result.stderr).splitlines()
    return lines[0].strip() if lines else ""


def succeeds(command: list[str]) -> bool:
    result = run(command)
    return result is not None and result.returncode == 0


def sha256(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return {"id": values.get("ID", ""), "version_id": values.get("VERSION_ID", "")}


print(
    json.dumps(
        {
            "schema_version": "jarvisbench.runtime-capabilities.v1",
            "architecture": platform.machine(),
            "os": os_release(),
            "libc": " ".join(platform.libc_ver()),
            "python": platform.python_version(),
            "python_supported": sys.version_info >= (3, 10),
            "node": version(["node", "--version"]),
            "openclaw": version(["openclaw", "--version"]),
            "gateway_help": succeeds(["openclaw", "gateway", "--help"]),
            "openclaw_paths": {
                "home": os.environ.get("OPENCLAW_HOME", ""),
                "state_dir": os.environ.get("OPENCLAW_STATE_DIR", ""),
                "config_path": os.environ.get("OPENCLAW_CONFIG_PATH", ""),
            },
            "git": version(["git", "--version"]),
            "sqlite": version(["sqlite3", "--version"]),
            "pandoc": version(["pandoc", "--version"]),
            "wkhtmltopdf": version(["wkhtmltopdf", "--version"]),
            "contracts": {
                "apt_snapshot": os.environ.get("JARVISBENCH_APT_SNAPSHOT", ""),
                "openclaw_lock_sha256": sha256(
                    Path("/opt/openclaw-runtime/package-lock.json")
                ),
                "task_manifest_sha256": sha256(
                    Path("/opt/jarvisbench/TASKS_SHA256SUMS")
                ),
            },
        },
        indent=2,
        sort_keys=True,
    )
)
