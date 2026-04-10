"""
Collab Agent - File Watcher Daemon
Runs silently in the background, watches for file changes,
syncs with partner, and triggers the agent automatically.
"""

import os
import sys
import time
import json
import hashlib
import logging
import threading
import requests
from pathlib import Path
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from agent import run_agent

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_PATH = Path.home() / ".collab-agent" / "config.json"
LOG_PATH    = Path.home() / ".collab-agent" / "daemon.log"
STATE_PATH  = Path.home() / ".collab-agent" / "file_state.json"

DEBOUNCE_SECONDS = 3      # Wait this long after last save before triggering
SYNC_INTERVAL    = 5      # How often to poll server for partner changes (seconds)
IGNORED_DIRS     = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}
WATCHED_EXTS     = {".py", ".js", ".ts", ".cs", ".json", ".yaml", ".yml"}

# ── Logging setup ─────────────────────────────────────────────────────────────
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("collab-agent")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print("❌ Config not found. Please run install.py first.")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def file_hash(path: str) -> str:
    """Get MD5 hash of a file's contents."""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return ""


def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def should_watch(path: str) -> bool:
    """Filter out files we don't care about."""
    p = Path(path)
    # Ignore hidden files and log/config files
    if p.name.startswith("."):
        return False
    if p.name in {"agent_log.md", "agent_memory.json"}:
        return False
    # Ignore certain directories
    for part in p.parts:
        if part in IGNORED_DIRS:
            return False
    # Only watch certain extensions
    if p.suffix not in WATCHED_EXTS:
        return False
    return True


# ── Server sync ───────────────────────────────────────────────────────────────

def push_change(config: dict, file_path: str, content: str, intent: str = ""):
    """Push a local file change to the sync server."""
    try:
        server = config["server_url"]
        payload = {
            "room":      config["room_id"],
            "developer": config["developer_name"],
            "file":      file_path,
            "content":   content,
            "intent":    intent,
            "timestamp": datetime.now().isoformat()
        }
        r = requests.post(f"{server}/push", json=payload, timeout=5)
        return r.json() if r.ok else None
    except Exception as e:
        log.warning(f"Could not push to server: {e}")
        return None


def poll_partner_changes(config: dict) -> list:
    """Poll server for any changes from partner developers."""
    try:
        server = config["server_url"]
        params = {
            "room":      config["room_id"],
            "developer": config["developer_name"]
        }
        r = requests.get(f"{server}/poll", params=params, timeout=5)
        return r.json().get("changes", []) if r.ok else []
    except Exception as e:
        log.warning(f"Could not poll server: {e}")
        return []


# ── Change handler ────────────────────────────────────────────────────────────

class ChangeHandler(FileSystemEventHandler):
    def __init__(self, config: dict, project_path: str):
        self.config       = config
        self.project_path = project_path
        self.state        = load_state()
        self.pending      = {}   # file -> timer
        self.lock         = threading.Lock()

    def on_modified(self, event):
        if event.is_directory:
            return
        path = event.src_path
        if not should_watch(path):
            return
        self._schedule(path)

    def on_created(self, event):
        if event.is_directory:
            return
        path = event.src_path
        if not should_watch(path):
            return
        self._schedule(path)

    def _schedule(self, path: str):
        """Debounce — reset timer on every save, only fire after silence."""
        with self.lock:
            if path in self.pending:
                self.pending[path].cancel()
            timer = threading.Timer(DEBOUNCE_SECONDS, self._handle, args=[path])
            self.pending[path] = timer
            timer.start()

    def _handle(self, path: str):
        """Called after debounce period — process the change."""
        with self.lock:
            self.pending.pop(path, None)

        new_hash    = file_hash(path)
        old_hash    = self.state.get(path, {}).get("hash", "")

        # Skip if file hasn't actually changed
        if new_hash == old_hash:
            return

        content = read_file(path)
        rel_path = os.path.relpath(path, self.project_path)

        log.info(f"Change detected: {rel_path}")

        # Update local state
        self.state[path] = {
            "hash":      new_hash,
            "timestamp": datetime.now().isoformat(),
            "developer": self.config["developer_name"]
        }
        save_state(self.state)

        # Push to sync server
        server_response = push_change(self.config, rel_path, content)

        # Check if server has a partner change for this same file
        if server_response and server_response.get("conflict"):
            partner_change = server_response["conflict"]
            log.info(f"Conflict found with {partner_change['developer']} on {rel_path}")
            self._resolve(rel_path, content, partner_change)
        else:
            log.info(f"Change pushed, no conflict yet: {rel_path}")

    def _resolve(self, rel_path: str, my_content: str, partner_change: dict):
        """Run the agent to resolve a conflict."""
        dev_a = {
            "developer": self.config["developer_name"],
            "file":      rel_path,
            "intent":    f"Recent changes to {rel_path}",
            "code":      my_content
        }
        dev_b = {
            "developer": partner_change["developer"],
            "file":      rel_path,
            "intent":    partner_change.get("intent", f"Recent changes to {rel_path}"),
            "code":      partner_change["content"]
        }

        log.info(f"Running agent for conflict on {rel_path}...")
        try:
            result = run_agent(dev_a, dev_b)
            log.info(f"Agent resolved conflict on {rel_path} (confidence: {result.get('confidence')})")

            # Write merged code back to disk
            full_path = os.path.join(self.project_path, rel_path)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(result["merged_code"])

            log.info(f"Merged code written to {rel_path}")

        except Exception as e:
            log.error(f"Agent failed to resolve conflict: {e}")


# ── Partner poller ────────────────────────────────────────────────────────────

class PartnerPoller(threading.Thread):
    """Background thread that polls for partner changes periodically."""

    def __init__(self, config: dict, handler: ChangeHandler):
        super().__init__(daemon=True)
        self.config  = config
        self.handler = handler
        self.seen    = set()

    def run(self):
        while True:
            try:
                changes = poll_partner_changes(self.config)
                for change in changes:
                    key = f"{change['developer']}:{change['file']}:{change['timestamp']}"
                    if key in self.seen:
                        continue
                    self.seen.add(key)

                    # Check if we have a local version of this file
                    full_path = os.path.join(self.handler.project_path, change["file"])
                    if os.path.exists(full_path):
                        my_content = read_file(full_path)
                        log.info(f"Partner change detected: {change['developer']} → {change['file']}")
                        self.handler._resolve(change["file"], my_content, change)

            except Exception as e:
                log.warning(f"Partner poll error: {e}")

            time.sleep(SYNC_INTERVAL)


# ── Main daemon ───────────────────────────────────────────────────────────────

def run_daemon():
    config       = load_config()
    project_path = config["project_path"]

    log.info(f"Collab Agent started")
    log.info(f"Developer: {config['developer_name']}")
    log.info(f"Watching:  {project_path}")
    log.info(f"Room:      {config['room_id']}")

    handler  = ChangeHandler(config, project_path)
    observer = Observer()
    observer.schedule(handler, project_path, recursive=True)
    observer.start()

    # Start partner poller
    poller = PartnerPoller(config, handler)
    poller.start()

    log.info("Watching for changes... (running silently in background)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        log.info("Collab Agent stopped")

    observer.join()


if __name__ == "__main__":
    run_daemon()
