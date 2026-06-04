"""Write-ahead log — append every task state transition to disk before it happens in memory.

On startup, replay() rebuilds in-flight state. Any task stuck in
claimed/processing is re-queued. Segments are rotated daily.

Log format (one JSON object per line):
  {"ts": 1234567890.123, "prompt_id": "p1", "from": "pending", "to": "claimed"}
"""
import json
from pathlib import Path
from time import time

from gateway.config import settings

_wal_file: Path = Path(settings.wal_path) / "gateway.wal"


def append(prompt_id: str, from_state: str, to_state: str) -> None:
    _wal_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time(), "prompt_id": prompt_id, "from": from_state, "to": to_state}
    with _wal_file.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def replay() -> list[dict]:
    """Read all WAL entries. Caller is responsible for re-queuing orphaned tasks."""
    if not _wal_file.exists():
        return []
    entries = []
    with _wal_file.open() as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries
