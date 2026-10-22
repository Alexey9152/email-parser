"""Read/write JSON and CSV artefacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import ContactResult, Restaurant
from .utils import ensure_parent

CSV_COLUMNS = (
    ("Name", "title"),
    ("Website", "website"),
    ("Emails", "emails"),
    ("Primary Email", "primary_email"),
    ("Facebook", "facebook_url"),
    ("Phone", "phone"),
    ("Address", "address"),
    ("Status", "status"),
    ("Sources", "sources"),
    ("Google Maps URL", "google_maps_url"),
    ("Note", "note"),
)


def save_restaurants_json(path: str | Path, items: list[Restaurant]) -> Path:
    path = ensure_parent(path)
    payload = [item.to_dict() for item in items]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_restaurants(path: str | Path) -> list[Restaurant]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run the maps stage first: "
            f'python main.py -q "restaurants in City, ST" --stage maps'
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Restaurant.from_dict(row) for row in data]


def save_results_json(path: str | Path, results: list[ContactResult]) -> Path:
    path = ensure_parent(path)
    payload = [row.to_dict() for row in results]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_results_csv(path: str | Path, results: list[ContactResult]) -> Path:
    path = ensure_parent(path)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[name for name, _ in CSV_COLUMNS])
        writer.writeheader()
        for row in results:
            data = row.to_dict()
            data["primary_email"] = row.primary_email
            writer.writerow({name: data.get(key, "") for name, key in CSV_COLUMNS})
    return path
