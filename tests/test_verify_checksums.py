from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.verify_checksums import verify


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class VerifyChecksumsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.task_file = self.root / "tasks/single_agent/example/public/input.txt"
        self.task_file.parent.mkdir(parents=True)
        self.task_file.write_bytes(b"public input\n")
        self.manifest = self.root / "TASKS_SHA256SUMS"
        self.manifest_line = (
            f"{_sha256(self.task_file.read_bytes())}  "
            "tasks/single_agent/example/public/input.txt"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_rejects_duplicate_manifest_entries(self) -> None:
        self.manifest.write_text(
            f"{self.manifest_line}\n{self.manifest_line}\n",
            encoding="utf-8",
        )

        self.assertEqual(
            verify(self.root, self.manifest),
            [
                "duplicate checksum entry: "
                "tasks/single_agent/example/public/input.txt"
            ],
        )

    def test_rejects_extra_files_in_task_tree_only(self) -> None:
        extra = self.root / "tasks/single_agent/example/public/unlisted.txt"
        extra.write_bytes(b"not in the manifest\n")
        (self.root / "README.md").write_text("repo metadata\n", encoding="utf-8")
        self.manifest.write_text(f"{self.manifest_line}\n", encoding="utf-8")

        self.assertEqual(
            verify(self.root, self.manifest),
            ["extra task file: tasks/single_agent/example/public/unlisted.txt"],
        )


if __name__ == "__main__":
    unittest.main()
