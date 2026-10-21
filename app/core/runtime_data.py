"""Optional display tables live with content, never in the program source."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def tables():
    path = Path(__file__).resolve().parents[2] / "content/catalog/display-titles.json"
    if not path.is_file():
        return {}
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Display table is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("kind") != "fkg-reader-display-tables":
        raise ValueError("Unsupported display table")
    return data


def title_table(name):
    rows = tables().get(name, {})
    if not isinstance(rows, dict) or len(rows) > 20000 or any(
            not isinstance(key, str) or not isinstance(value, str) or len(key) > 500 or len(value) > 2000
            for key, value in rows.items()):
        raise ValueError("Invalid display labels")
    return rows


def audio_aliases():
    result = {}
    for row in tables().get("audioAliases", []):
        if (not isinstance(row, list) or len(row) != 2 or any(
                not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(value, str) for value in pair)
                for pair in row)):
            raise ValueError("Invalid local audio mapping")
        result[tuple(row[0])] = tuple(row[1])
    return result
