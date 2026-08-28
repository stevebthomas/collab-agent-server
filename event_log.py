"""
Remi: Event Log

Append-only source-of-truth log for all file change events observed by the watcher.
Separates concerns between raw event capture and rendered output:

- event_log.py  → append-only source log (every event, unfiltered)
- remi_updates.md → rendered activity feed (compacted, human-readable)

This separation means the compaction layer can be tuned or replaced without
losing historical data. The source log can also be replayed or re-compacted
if compaction logic changes.

Log format: one JSON object per line (newline-delimited JSON / NDJSON).
Each entry has: timestamp, file_path, developer, intent, sha256, event_type.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Paths ─────────────────────────────────────────────────────────────────────

def _log_path(project_path: str) -> Path:
    return Path(project_path) / ".remi" / "event_log.ndjson"


# ── Writing ───────────────────────────────────────────────────────────────────

def append_event(
    project_path: str,
    file_path:    str,
    developer:    str,
    intent:       str,
    sha256:       str,
    event_type:   str = "change",
):
    """
    Append a single event to the source log.
    Creates the log file if it doesn't exist.
    """
    log = _log_path(project_path)
    log.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp":  datetime.utcnow().isoformat() + "Z",
        "file_path":  file_path,
        "developer":  developer,
        "intent":     intent,
        "sha256":     sha256,
        "event_type": event_type,
    }

    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── Reading ───────────────────────────────────────────────────────────────────

def read_events(project_path: str, limit: Optional[int] = None) -> list[dict]:
    """
    Read events from the source log, most recent last.
    Pass limit to cap the number of entries returned.
    """
    log = _log_path(project_path)
    if not log.exists():
        return []

    entries = []
    with open(log, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return entries[-limit:] if limit else entries


def event_count(project_path: str) -> int:
    """Return the total number of events in the source log."""
    return len(read_events(project_path))
