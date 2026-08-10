from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.export_release import (
    EXPECTED_MULTI_TASKS,
    EXPECTED_SINGLE_TASKS,
    ROOT_PUBLIC_FIELDS,
    ReleaseExportError,
    export_release,
)


FROZEN_MERGE = "a" * 64
FROZEN_SPLIT = "b" * 64
PRIVATE_MARKER = "DO_NOT_EXPORT_REQUESTER_OR_EVALUATOR_CONTENT"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _task_manifest(task_id: str, *, multi: bool) -> dict[str, object]:
    episode: dict[str, object] = {
        "brief": f"Complete the public task {task_id}.",
        "result_paths": ["results/final.json"],
        "start_time": "09:00",
    }
    if multi:
        episode["worker_count"] = 2
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "title": f"Title {task_id}",
        "domain": "synthetic",
        "summary": "A synthetic public export fixture.",
        "source": {
            "adaptation": "original fixture",
            "benchmark": "Synthetic",
            "license": "MIT",
            "revision": "fixture-v1",
            "task_id": f"upstream_{task_id}",
            "url": "https://example.invalid/source",
            "unexpected_private_note": PRIVATE_MARKER,
        },
        "runtime": {
            "cpus": 1,
            "family": "openclaw",
            "memory_gb": 1,
            "modalities": ["text"],
            "worker_timeout_seconds": 60,
            "unexpected": PRIVATE_MARKER,
        },
        "episode": episode,
        "baseline": {
            "model": "provider/model",
            "on_question": "continue",
            "user_availability": "unavailable",
        },
        "assets": {
            "public": ["public/output_contract.json", "public/readme.txt"],
            "private": ["private/profile.json", "private/rubric.json"],
            "validators": [
                "private-profile leakage scan",
                "reference and partial grader fixtures",
            ],
        },
        "attention": {
            "decision_question": PRIVATE_MARKER,
            "profile_key": "private.choice",
        },
        "grading": {
            "script": "grade.py",
            "checkpoints": [PRIVATE_MARKER],
        },
        "release": {
            "profile_visibility": PRIVATE_MARKER,
            "worker_only_overall_ceiling": 0.7,
        },
    }


class ExportFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        _write_json(
            root / "RELEASE.json",
            {
                "canonical_modes": {
                    "merge": {"sha256_manifest_sha256": FROZEN_MERGE},
                    "split": {"sha256_manifest_sha256": FROZEN_SPLIT},
                },
                "internal_note": PRIVATE_MARKER,
            },
        )
        self.single_ids = [
            f"single_task_{index:02d}" for index in range(EXPECTED_SINGLE_TASKS)
        ]
        self.multi_ids = [
            f"multi_task_{index:02d}" for index in range(EXPECTED_MULTI_TASKS)
        ]
        _write_json(
            root / "single_agent_v1" / "reports" / "release_manifest.json",
            {
                "tasks": [
                    {"task_id": task_id, "private_hash": PRIVATE_MARKER}
                    for task_id in self.single_ids
                ],
                "private_report": PRIVATE_MARKER,
            },
        )
        multi_selector = (
            root / "multi_agent_v1" / "mas_v1" / "release_v1_tasks.txt"
        )
        multi_selector.parent.mkdir(parents=True, exist_ok=True)
        multi_selector.write_text("\n".join(self.multi_ids) + "\n", encoding="utf-8")

        for topology, task_ids in (
            ("single", self.single_ids),
            ("multi", self.multi_ids),
        ):
            base = (
                root / "single_agent_v1" / "benchmark" / "tasks"
                if topology == "single"
                else root
                / "multi_agent_v1"
                / "mas_v1"
                / "benchmark"
                / "tasks"
            )
            for task_id in task_ids:
                task_dir = base / task_id
                _write_json(
                    task_dir / "task.json",
                    _task_manifest(task_id, multi=topology == "multi"),
                )
                _write_json(
                    task_dir / "public" / "output_contract.json",
                    {"required": ["results/final.json"]},
                )
                (task_dir / "public" / "readme.txt").write_text(
                    "Public fixture.\n", encoding="utf-8"
                )
                _write_json(
                    task_dir / "private" / "profile.json",
                    {"requester": PRIVATE_MARKER},
                )
                _write_json(
                    task_dir / "private" / "rubric.json",
                    {"grader": PRIVATE_MARKER},
                )

    def task_dir(self, topology: str, task_id: str) -> Path:
        if topology == "single":
            return (
                self.root
                / "single_agent_v1"
                / "benchmark"
                / "tasks"
                / task_id
            )
        return (
            self.root
            / "multi_agent_v1"
            / "mas_v1"
            / "benchmark"
            / "tasks"
            / task_id
        )


class ExportReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.source = root / "canonical"
        self.output = root / "release"
        self.source.mkdir()
        self.output.mkdir()
        self.fixture = ExportFixture(self.source)

    def test_exports_only_public_projection_and_declared_assets(self) -> None:
        export_release(self.source, self.output)

        single_root = self.output / "tasks" / "single_agent"
        multi_root = self.output / "tasks" / "multi_agent"
        self.assertEqual(
            sorted(path.name for path in single_root.iterdir()),
            sorted(self.fixture.single_ids),
        )
        self.assertEqual(
            sorted(path.name for path in multi_root.iterdir()),
            sorted(self.fixture.multi_ids),
        )

        single = json.loads(
            (single_root / self.fixture.single_ids[0] / "task.public.json").read_text()
        )
        multi = json.loads(
            (multi_root / self.fixture.multi_ids[0] / "task.public.json").read_text()
        )
        self.assertEqual(set(single), set(ROOT_PUBLIC_FIELDS))
        self.assertEqual(set(multi), set(ROOT_PUBLIC_FIELDS))
        self.assertEqual(set(single["source"]), {
            "adaptation", "benchmark", "license", "revision", "task_id", "url"
        })
        self.assertEqual(set(single["episode"]), {"brief", "result_paths"})
        self.assertEqual(
            set(multi["episode"]), {"brief", "result_paths", "worker_count"}
        )
        self.assertEqual(set(single["assets"]), {"public"})
        self.assertFalse(any(self.output.rglob("private")))
        all_exported = b"".join(
            path.read_bytes() for path in self.output.rglob("*") if path.is_file()
        )
        self.assertNotIn(PRIVATE_MARKER.encode(), all_exported)

        provenance = json.loads((self.output / "PROVENANCE.json").read_text())
        self.assertEqual(
            provenance["canonical_identity"]["frozen_single_modes"],
            {"merge": FROZEN_MERGE, "split": FROZEN_SPLIT},
        )
        self.assertNotIn(str(self.source), json.dumps(provenance))

        checksum_lines = (self.output / "SHA256SUMS").read_text().splitlines()
        self.assertTrue(checksum_lines)
        self.assertFalse(any(line.endswith("  SHA256SUMS") for line in checksum_lines))
        for line in checksum_lines:
            digest, relative = line.split("  ", 1)
            actual = hashlib.sha256((self.output / relative).read_bytes()).hexdigest()
            self.assertEqual(digest, actual)

    def test_rejects_nonempty_output_without_modifying_it(self) -> None:
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ReleaseExportError, "must be empty"):
            export_release(self.source, self.output)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_rejects_path_traversal_in_public_asset_declaration(self) -> None:
        task_id = self.fixture.single_ids[0]
        manifest_path = self.fixture.task_dir("single", task_id) / "task.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["assets"]["public"][0] = "public/../private/profile.json"
        _write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ReleaseExportError, "path traversal"):
            export_release(self.source, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_rejects_public_symlink(self) -> None:
        task_id = self.fixture.single_ids[0]
        task_dir = self.fixture.task_dir("single", task_id)
        target = task_dir / "public" / "readme.txt"
        target.unlink()
        os.symlink(task_dir / "private" / "profile.json", target)
        with self.assertRaisesRegex(ReleaseExportError, "symlink"):
            export_release(self.source, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_rejects_unlisted_public_file(self) -> None:
        task_id = self.fixture.single_ids[0]
        extra = self.fixture.task_dir("single", task_id) / "public" / "extra.txt"
        extra.write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(ReleaseExportError, "exactly cover"):
            export_release(self.source, self.output)

    def test_supports_a_declared_public_directory_without_broadening_scope(self) -> None:
        task_id = self.fixture.multi_ids[0]
        task_dir = self.fixture.task_dir("multi", task_id)
        materials = task_dir / "public" / "materials"
        materials.mkdir()
        (task_dir / "public" / "readme.txt").replace(materials / "readme.txt")
        manifest_path = task_dir / "task.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["assets"]["public"] = [
            "public/output_contract.json",
            "public/materials",
        ]
        _write_json(manifest_path, manifest)

        export_release(self.source, self.output)
        self.assertTrue(
            (
                self.output
                / "tasks"
                / "multi_agent"
                / task_id
                / "public"
                / "materials"
                / "readme.txt"
            ).is_file()
        )

    def test_retains_an_inert_missing_public_declaration_without_copying_it(self) -> None:
        task_id = self.fixture.multi_ids[0]
        task_dir = self.fixture.task_dir("multi", task_id)
        manifest_path = task_dir / "task.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["assets"]["public"].append("public/future_workspace")
        _write_json(manifest_path, manifest)

        export_release(self.source, self.output)
        exported_task = (
            self.output / "tasks" / "multi_agent" / task_id
        )
        projected = json.loads((exported_task / "task.public.json").read_text())
        self.assertIn("public/future_workspace", projected["assets"]["public"])
        self.assertFalse((exported_task / "public" / "future_workspace").exists())

    def test_rejects_evaluator_named_file_inside_public_tree(self) -> None:
        task_id = self.fixture.single_ids[0]
        task_dir = self.fixture.task_dir("single", task_id)
        rubric = task_dir / "public" / "rubric.json"
        _write_json(rubric, {"not": "public"})
        manifest_path = task_dir / "task.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["assets"]["public"].append("public/rubric.json")
        _write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ReleaseExportError, "denied path category"):
            export_release(self.source, self.output)

    def test_rejects_credential_like_public_content_without_echoing_it(self) -> None:
        task_id = self.fixture.single_ids[0]
        path = self.fixture.task_dir("single", task_id) / "public" / "readme.txt"
        secret = "sk-" + "this_is_a_synthetic_secret_123456789"
        path.write_text(secret, encoding="utf-8")
        with self.assertRaises(ReleaseExportError) as context:
            export_release(self.source, self.output)
        self.assertIn("credential-like", str(context.exception))
        self.assertNotIn(secret, str(context.exception))

    def test_rejects_unsafe_selected_task_id(self) -> None:
        selector = (
            self.source / "single_agent_v1" / "reports" / "release_manifest.json"
        )
        manifest = json.loads(selector.read_text())
        manifest["tasks"][0]["task_id"] = "../private"
        _write_json(selector, manifest)
        with self.assertRaisesRegex(ReleaseExportError, "unsafe task identifier"):
            export_release(self.source, self.output)

    def test_rejects_overlapping_output(self) -> None:
        nested = self.source / "empty-output"
        nested.mkdir()
        with self.assertRaisesRegex(ReleaseExportError, "must not overlap"):
            export_release(self.source, nested)


if __name__ == "__main__":
    unittest.main()
