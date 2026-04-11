"""
Remi - File Watcher Daemon
Runs silently in the background, watches for file changes,
syncs with partner, and triggers the agent automatically.
"""

import os
import sys
from pathlib import Path

# ── Load API key before importing agent (Anthropic client initialises at import time) ──
_API_KEY_PATH = Path.home() / ".collab-agent" / ".api_key"
if _API_KEY_PATH.exists():
    os.environ["ANTHROPIC_API_KEY"] = _API_KEY_PATH.read_text().strip()
else:
    print("❌ API key not found at ~/.collab-agent/.api_key")
    print("   Run install.py or create the file manually.")
    sys.exit(1)

import time
import json
import hashlib
import logging
import threading
import requests
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from agent import run_agent
from mapper import get_connected_files, read_connected_content, build_map, save_map, load_map, should_rebuild

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
log = logging.getLogger("remi")


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


def notify_mac(developer: str, file_path: str):
    """Fire a native Mac OS notification."""
    import platform
    if platform.system() != "Darwin":
        return
    title   = "Remi"
    message = f"{developer} just made changes to {file_path}"
    os.system(f'osascript -e \'display notification "{message}" with title "{title}"\'')


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
    def __init__(self, config: dict, project_path: str, codebase_map: dict = None):
        self.config        = config
        self.project_path  = project_path
        self.state         = load_state()
        self.pending       = {}   # file -> timer
        self.lock          = threading.Lock()
        self.codebase_map  = codebase_map or {}
        self.map_lock      = threading.Lock()

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
        print(f"💾 Remi: Change detected: {rel_path}")

        # Update local state
        self.state[path] = {
            "hash":      new_hash,
            "timestamp": datetime.now().isoformat(),
            "developer": self.config["developer_name"]
        }
        save_state(self.state)

        # Get codebase context for connected files
        with self.map_lock:
            codebase_map = self.codebase_map
        connected_files  = get_connected_files(codebase_map, rel_path)
        codebase_context = read_connected_content(self.project_path, connected_files)
        if connected_files:
            print(f"🔗 Connected files: {[c['file'] for c in connected_files]}")

        # Push to sync server
        server_response = push_change(self.config, rel_path, content)

        # Check if server has a partner change for this same file
        if server_response and server_response.get("conflict"):
            partner_change = server_response["conflict"]
            log.info(f"Conflict found with {partner_change['developer']} on {rel_path}")
            self._resolve(rel_path, content, partner_change, codebase_context)
        else:
            log.info(f"Change pushed, no conflict yet: {rel_path}")

    def _resolve(self, rel_path: str, my_content: str, partner_change: dict, codebase_context: str = ""):
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
            result = run_agent(dev_a, dev_b, codebase_context)
            log.info(f"Agent resolved conflict on {rel_path} (confidence: {result.get('confidence')})")

            # Write merged code back to disk
            full_path = os.path.join(self.project_path, rel_path)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(result["merged_code"])

            log.info(f"Merged code written to {rel_path}")
            notify_mac("✅ Conflict resolved", f"Agent merged changes to {rel_path}")

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
                        notify_mac(change['developer'], change['file'])
                        with self.handler.map_lock:
                            codebase_map = self.handler.codebase_map
                        connected_files  = get_connected_files(codebase_map, change["file"])
                        codebase_context = read_connected_content(self.handler.project_path, connected_files)
                        self.handler._resolve(change["file"], my_content, change, codebase_context)

            except Exception as e:
                log.warning(f"Partner poll error: {e}")

            time.sleep(SYNC_INTERVAL)


# ── Map rebuilder ─────────────────────────────────────────────────────────────

class MapRebuilder(threading.Thread):
    """Rebuilds the codebase map every 30 minutes in the background."""

    def __init__(self, handler: ChangeHandler, project_path: str):
        super().__init__(daemon=True)
        self.handler      = handler
        self.project_path = project_path

    def run(self):
        while True:
            time.sleep(30 * 60)
            try:
                log.info("Rebuilding codebase map...")
                new_map = build_map(self.project_path)
                save_map(self.project_path, new_map)
                with self.handler.map_lock:
                    self.handler.codebase_map = new_map
                print(f"🗺️  Map rebuilt: {new_map['file_count']} files, {len(new_map['connections'])} connections")
                log.info(f"Map rebuilt: {new_map['file_count']} files")
            except Exception as e:
                log.warning(f"Map rebuild failed: {e}")


# ── Main daemon ───────────────────────────────────────────────────────────────

def run_daemon():
    config       = load_config()
    project_path = config["project_path"]

    log.info(f"Remi started")
    log.info(f"Developer: {config['developer_name']}")
    log.info(f"Watching:  {project_path}")
    log.info(f"Room:      {config['room_id']}")

    print(f"🐀 Remi is waking up...")
    print(f"✅ Remi is awake")
    print(f"   Developer : {config['developer_name']}")
    print(f"   Watching  : {project_path}")
    print(f"   Room      : {config['room_id']}")
    print(f"   Server    : {config['server_url']}")

    # Build codebase map on startup
    print(f"🗺️  Building codebase map...")
    codebase_map = build_map(project_path)
    save_map(project_path, codebase_map)
    print(f"   ✅ Mapped {codebase_map['file_count']} files, {len(codebase_map['connections'])} connections")
    log.info(f"Codebase map built: {codebase_map['file_count']} files")

    handler  = ChangeHandler(config, project_path, codebase_map)
    observer = Observer()
    observer.schedule(handler, project_path, recursive=True)
    observer.start()

    # Start partner poller and map rebuilder
    poller   = PartnerPoller(config, handler)
    rebuilder = MapRebuilder(handler, project_path)
    poller.start()
    rebuilder.start()

    log.info("Watching for changes... (running silently in background)")
    print(f"👁  Watching for changes... (Ctrl-C to stop)\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        log.info("Remi is going to sleep")
        print("🐀 Remi is going to sleep")

    observer.join()


if __name__ == "__main__":
    run_daemon()
