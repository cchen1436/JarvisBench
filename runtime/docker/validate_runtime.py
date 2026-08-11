#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import shutil
import sys
from pathlib import Path

from jarvisbench.core.privacy import scan_release_tree
from scripts.verify_checksums import verify


EXPECTED_TASKS = {"single_agent": 20, "multi_agent": 10}
REQUIRED_EXECUTABLES = (
    "git",
    "jb",
    "node",
    "openclaw",
    "pandoc",
    "pdfinfo",
    "pdftotext",
    "python3",
    "sqlite3",
    "wkhtmltopdf",
)
REQUIRED_IMPORTS = (
    "PIL",
    "docx",
    "jarvisbench.settings.single_agent_runtime",
    "jarvisbench.settings.multi_agent_runtime",
    "jarvisbench.tracks.agent_collaboration",
    "jarvisbench.tracks.user_interaction",
    "jarvisbench.reference.dynamic_mas",
    "jsonschema",
    "openpyxl",
    "openai",
    "pandas",
    "pdfplumber",
    "pptx",
    "pypdf",
)
EXPECTED_PYTHON_DISTRIBUTIONS = {
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the JarvisBench runtime and mounted public tasks"
    )
    parser.add_argument(
        "--runtime-only",
        action="store_true",
        help="validate the image build before a public task bundle is mounted",
    )
    args = parser.parse_args()
    root = Path(os.environ.get("JARVISBENCH_ROOT", "/opt/jarvisbench")).resolve()
    manifest = root / "TASKS_SHA256SUMS"
    provenance = root / "TASKS_PROVENANCE.json"
    privacy_findings = scan_release_tree(root)
    contract_files = {
        "TASKS_SHA256SUMS": manifest.is_file() and not manifest.is_symlink(),
        "TASKS_PROVENANCE.json": provenance.is_file() and not provenance.is_symlink(),
    }
    contract_failures = [
        name for name, present in contract_files.items() if not present
    ]
    checksum_failures = []
    if not args.runtime_only:
        checksum_failures = (
            verify(root, manifest)
            if contract_files["TASKS_SHA256SUMS"]
            else ["missing regular file: TASKS_SHA256SUMS"]
        )
    task_counts: dict[str, int] = {}
    count_failures: list[str] = []
    if not args.runtime_only:
        for setting, expected in EXPECTED_TASKS.items():
            task_root = root / "tasks" / setting
            observed = (
                len(
                    [
                        path
                        for path in task_root.iterdir()
                        if path.is_dir() and not path.is_symlink()
                    ]
                )
                if task_root.is_dir() and not task_root.is_symlink()
                else 0
            )
            task_counts[setting] = observed
            if observed != expected:
                count_failures.append(
                    f"{setting}: expected {expected}, observed {observed}"
                )
    executables = {
        name: shutil.which(name) is not None for name in REQUIRED_EXECUTABLES
    }
    executable_failures = [
        name for name, present in executables.items() if not present
    ]
    module_imports: dict[str, bool] = {}
    for module in REQUIRED_IMPORTS:
        try:
            importlib.import_module(module)
        except Exception:
            module_imports[module] = False
        else:
            module_imports[module] = True
    import_failures = [
        module for module, imported in module_imports.items() if not imported
    ]
    python_distributions: dict[str, str | None] = {}
    for distribution_name in EXPECTED_PYTHON_DISTRIBUTIONS:
        try:
            python_distributions[distribution_name] = importlib.metadata.version(
                distribution_name
            )
        except importlib.metadata.PackageNotFoundError:
            python_distributions[distribution_name] = None
    distribution_failures = {
        name: {"expected": expected, "observed": python_distributions[name]}
        for name, expected in EXPECTED_PYTHON_DISTRIBUTIONS.items()
        if python_distributions[name] != expected
    }
    python_supported = sys.version_info >= (3, 10)
    ok = not (
        privacy_findings
        or contract_failures
        or checksum_failures
        or count_failures
        or executable_failures
        or import_failures
        or distribution_failures
        or not python_supported
    )
    print(
        json.dumps(
            {
                "schema_version": "jarvisbench.runtime-validation.v1",
                "mode": "runtime-only" if args.runtime_only else "runtime-and-tasks",
                "ok": ok,
                "root": str(root),
                "privacy_findings": privacy_findings,
                "contract_files": contract_files,
                "missing_contract_files": contract_failures,
                "checksum_failures": checksum_failures,
                "task_counts": task_counts,
                "task_count_failures": count_failures,
                "executables": executables,
                "missing_executables": executable_failures,
                "module_imports": module_imports,
                "failed_module_imports": import_failures,
                "python_distributions": python_distributions,
                "python_distribution_failures": distribution_failures,
                "python_supported": python_supported,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
