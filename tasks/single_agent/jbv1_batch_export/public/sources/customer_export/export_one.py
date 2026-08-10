
import json
from pathlib import Path

def export_one(record, destination: Path):
    if not record.get("customer_id"):
        raise ValueError("missing customer_id")
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"{record['customer_id']}.json"
    path.write_text(json.dumps(record, sort_keys=True))
    return path
