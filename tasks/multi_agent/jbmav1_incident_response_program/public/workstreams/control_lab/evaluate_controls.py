"""Score supplied remediation candidates without choosing the budget priority."""

from __future__ import annotations

import csv
from pathlib import Path


def evaluate(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    raise NotImplementedError("calculate coverage_per_point for every candidate")


def write_scores(rows: list[dict[str, str]], path: Path) -> None:
    fields = ["initiative", "owner", "effort_points", "incidents_addressed", "coverage_per_point", "audit_evidence", "reversible"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
