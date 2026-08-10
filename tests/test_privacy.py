from pathlib import Path

from jarvisbench.core.privacy import scan_release_tree


def test_privacy_scanner_rejects_private_path(tmp_path: Path):
    path = tmp_path / "tasks" / "private"
    path.mkdir(parents=True)
    (path / "profile.json").write_text("{}")
    assert scan_release_tree(tmp_path)


def test_privacy_scanner_rejects_api_key_shape(tmp_path: Path):
    label = "api" + "_key"
    (tmp_path / "oops.txt").write_text(f"{label} = secret-value")
    assert scan_release_tree(tmp_path)
