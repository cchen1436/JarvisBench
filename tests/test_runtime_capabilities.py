from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from scripts.compare_capabilities import REQUIRED_EXACT


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "runtime" / "docker" / "python-requirements.lock"
DOCKERFILE_PATH = ROOT / "runtime" / "docker" / "Dockerfile"
NPM_PACKAGE_PATH = ROOT / "runtime" / "npm" / "package.json"
NPM_LOCK_PATH = ROOT / "runtime" / "npm" / "package-lock.json"
VALIDATOR_PATH = ROOT / "runtime" / "docker" / "validate_runtime.py"

EXPECTED_DOCUMENT_DISTRIBUTIONS = {
    "Pillow": "12.3.0",
    "jsonschema": "4.25.1",
    "openpyxl": "3.1.5",
    "openai": "2.48.0",
    "pandas": "2.3.3",
    "pdfplumber": "0.11.10",
    "pypdf": "6.14.2",
    "python-docx": "1.2.0",
    "python-pptx": "1.0.2",
}


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_runtime", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_python_runtime_lock_is_complete_and_hash_pinned() -> None:
    text = LOCK_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    entries: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        match = re.fullmatch("([a-zA-Z0-9_-]+)==([^ ]+) \\\\", line)
        if match is None:
            continue
        assert index + 1 < len(lines)
        assert re.fullmatch(
            r"    --hash=sha256:[a-f0-9]{64}", lines[index + 1]
        )
        entries.append((match.group(1), match.group(2)))
    locked = {name.lower().replace("_", "-"): version for name, version in entries}

    for distribution, expected_version in EXPECTED_DOCUMENT_DISTRIBUTIONS.items():
        assert locked[distribution.lower().replace("_", "-")] == expected_version
    assert len(entries) == 43


def test_dockerfile_installs_reproducible_document_runtime() -> None:
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "poppler-utils=22.02.0-2ubuntu0.13" in text
    assert "python3-venv=3.10.6-1~22.04.1" in text
    assert "--require-hashes" in text
    assert "--only-binary=:all:" in text
    assert "/opt/jarvisbench-runtime/python-requirements.lock" in text


def test_openclaw_security_refresh_is_exactly_pinned() -> None:
    package = json.loads(NPM_PACKAGE_PATH.read_text(encoding="utf-8"))
    lock = json.loads(NPM_LOCK_PATH.read_text(encoding="utf-8"))
    packages = lock["packages"]

    assert package["dependencies"] == {"openclaw": "2026.6.34"}
    assert packages[""]["dependencies"] == package["dependencies"]
    assert packages["node_modules/openclaw"]["version"] == "2026.6.34"
    assert packages["node_modules/openclaw/node_modules/tar"]["version"] == "7.5.20"
    assert not any("baileys" in path.lower() for path in packages)

    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "node:22.22.3-bookworm-slim" in dockerfile
    assert "sha256:e21fc383b50d5347dc7a9f1cae45b8f4e2f0d39f7ade28e4eef7d2934522b752" in dockerfile


def test_runtime_contract_checks_document_capabilities() -> None:
    validator = _load_validator()
    assert {"pdfinfo", "pdftotext"}.issubset(validator.REQUIRED_EXECUTABLES)
    assert EXPECTED_DOCUMENT_DISTRIBUTIONS == validator.EXPECTED_PYTHON_DISTRIBUTIONS
    assert {"PIL", "docx", "jsonschema", "openai", "openpyxl", "pandas", "pdfplumber", "pptx", "pypdf"}.issubset(
        validator.REQUIRED_IMPORTS
    )
    assert {"pdfinfo", "pdftotext", "python_packages"}.issubset(REQUIRED_EXACT)
