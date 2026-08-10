
from pathlib import Path
from batch_export import export_batch

def test_all_valid(tmp_path: Path):
    result = export_batch([{"customer_id":"C1"},{"customer_id":"C2"}], tmp_path)
    assert result["succeeded"] == ["C1", "C2"]

def test_failure_is_reported(tmp_path: Path):
    result = export_batch([{"customer_id":"C1"},{"name":"bad"}], tmp_path)
    assert result["failed"]
