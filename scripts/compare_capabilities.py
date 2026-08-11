#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_EXACT = (
    "architecture",
    "os",
    "libc",
    "python",
    "node",
    "openclaw",
    "gateway_help",
    "git",
    "sqlite",
    "pandoc",
    "pdfinfo",
    "pdftotext",
    "python_packages",
    "wkhtmltopdf",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bootstrap", type=Path)
    parser.add_argument("canonical", type=Path)
    args = parser.parse_args()
    bootstrap = json.loads(args.bootstrap.read_text(encoding="utf-8"))
    canonical = json.loads(args.canonical.read_text(encoding="utf-8"))
    differences = {
        key: {"bootstrap": bootstrap.get(key), "canonical": canonical.get(key)}
        for key in REQUIRED_EXACT
        if bootstrap.get(key) != canonical.get(key)
    }
    print(json.dumps({"ok": not differences, "differences": differences}, indent=2, sort_keys=True))
    return 1 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
