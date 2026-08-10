"""Evaluate one-week research directions against a fixed lab budget."""

from __future__ import annotations

import csv
import json
from pathlib import Path


RESOURCE_FIELDS = ("gpu_hours", "analyst_hours", "annotator_hours", "queries")


def evaluate(capacity_path: Path, requirements_path: Path) -> list[dict[str, str]]:
    capacity = json.loads(capacity_path.read_text(encoding="utf-8"))
    with requirements_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    raise NotImplementedError("calculate feasibility and the binding constraint")
