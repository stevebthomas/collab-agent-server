"""
Remi - File Watcher Daemon
Runs silently in the background, watches ALL registered projects simultaneously.
Each project gets its own watcher, room, log, and memory.
"""

import os
import sys
from pathlib import Path

# ── Load API key before importing agent ──────────────────────────────────────
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

from agent import run_agent, infer_intent
from mapper import get_connected_files, read_connected_content, build_map, save_map

# ── Global paths ──────────────────────────────────────────────────────────────
GLOBAL_CONFIG_DIR  = Path.home() / ".collab-agent"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "config.json"
PROJECTS_PATH      = GLOBAL_CONFIG_DIR / "projects.json"
LOG_PATH           = GLOBAL_CONFIG_DIR / "daemon.log"

DEBOUNCE_SECONDS = 3
SYNC_INTERVAL    = 5
IGNORED_DIRS     = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".idea", ".vscode", ".remi"
}
WATCHED_EXTS = {".py", ".js", ".ts", ".cs", ".json", ".yaml", ".yml", ".md", ".lua"}

# ── Logging setup ─────────────────────────────────────────────────────────────
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("remi")


# ── Config loading ────────────────────────────────────────────────────────────

def load_global_config() -> dict:
    if not GLOBAL_CONFIG_PATH.exists():
        print("❌ Global config not found. Run install.py first.")
        sys.exit(1)
    with open(GLOBAL_CONFIG_PATH) as f:
        return json.load(f)


def load_all_project_configs() -> list:
    """Return a config dict for every active project."""
    global_config  = load_global_config()
    developer_name = global_config.get("developer_name", "Developer")

    # Migration: if no projects.json but old single-project config exists, auto-create it
    if not PROJECTS_PATH.exists() and "project_path" in global_config:
        projects = {
            global_config["project_path"]: {
                "name":    Path(global_config["project_path"]).name,
                "room_id": global_config.get("room_id", "default"),
                "active":  True
            }
        }
        with open(PROJECTS_PATH, "w") as f:
            json.dump(projects, f, indent=2)
        log.info("Auto-created projects.json from legacy single-project config")

    if not PROJECTS_PATH.exists():
        print("❌ No projects registered. cd into a project and run 'remi init'.")
        sys.exit(1)

    with open(PROJECTS_PATH) as f:
        projects = json.load(f)

    configs = []
    for project_path, info in projects.items():
        if not info.get("active"):
            continue

        # Load per-project config from .remi/config.json if present
        project_config_file = Path(project_path) / ".remi" / "config.json"
        if project_config_file.exists():
            with open(project_config_file) as f:
                cfg = json.load(f)
        else:
            # Fall back to constructing from projects.json + global defaults
            cfg = {
                "project_name": info.get("name", Path(project_path).name),
                "room_id":      info.get("room_id", "default"),
                "server_url":   global_config.get(
                    "server_url",
                    "https://collab-agent-server-production.up.railway.app"
                ),
            }

        # Global developer_name always wins — single source of truth
        cfg["developer_name"] = developer_name
        cfg["project_path"]   = project_path
        configs.append(cfg)

    return configs


# ── Utilities ─────────────────────────────────────────────────────────────────

def file_hash(path: str) -> str:
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
    """Fire a native macOS notification with the Remi icon."""
    import platform, shutil
    if platform.system() != "Darwin":
        return
    title   = "Remi"
    message = f"{developer} just made changes to {file_path}"
    icon    = str(Path(__file__).parent / "assets" / "remi_icon.png")

    if shutil.which("terminal-notifier") and os.path.exists(icon):
        os.system(
            f'terminal-notifier -title "{title}" -message "{message}" '
            f'-contentImage "{icon}" -sound default -timeout 20'
        )
    else:
        os.system(f'osascript -e \'display notification "{message}" with title "{title}"\'')


def should_watch(path: str) -> bool:
    p = Path(path)
    if p.name.startswith("."):
        return False
    if p.name in {"remi_log.md", "remi_memory.json", "codebase_map.json",
                  "remi_updates.md", "agent_log.md", "agent_memory.json"}:
        return False
    for part in p.parts:
        if part in IGNORED_DIRS:
            return False
    return p.suffix in WATCHED_EXTS


# ── Activity feed ────────────────────────────────────────────────────────────

def write_update(project_path: str, emoji: str, developer: str, file_path: str, message: str):
    """Append a 2-line entry to remi_updates.md, with a date header if needed."""
    updates_path = Path(project_path) / "remi_updates.md"
    now          = datetime.now()
    today_header = f"## {now.strftime('%A, %B %-d')}"
    time_str     = now.strftime("%H:%M")

    existing = updates_path.read_text() if updates_path.exists() else ""

    with open(updates_path, "a") as f:
        if today_header not in existing:
            if existing and not existing.endswith("\n\n"):
                f.write("\n\n")
            f.write(f"{today_header}\n\n")
        f.write(f"{emoji} {time_str}  {developer}  {file_path}\n")
        f.write(f"          {message}\n\n")


# ── Server sync ───────────────────────────────────────────────────────────────

def push_change(config: dict, file_path: str, content: str, intent: str = ""):
    """Push a local file change to the sync server."""
    try:
        payload = {
            "room":      config["room_id"],
            "developer": config["developer_name"],
            "file":      file_path,
            "content":   content,
            "intent":    intent,
            "timestamp": datetime.now().isoformat()
        }
        r = requests.post(f"{config['server_url']}/push", json=payload, timeout=5)
        return r.json() if r.ok else None
    except Exception as e:
        log.warning(f"Could not push to server: {e}")
        return None


def push_intent(config: dict, file_path: str, intent: str) -> bool:
    """Push a file's inferred intent to the registry. Returns True if this is a new file."""
    if not intent:
        return False
    try:
        payload = {
            "room_id":   config["room_id"],
            "developer": config["developer_name"],
            "file_path": file_path,
            "intent":    intent
        }
        r = requests.post(f"{config['server_url']}/intent/update", json=payload, timeout=5)
        if r.ok:
            log.info(f"Intent pushed for {file_path}")
            return r.json().get("new_file", False)
    except Exception as e:
        log.warning(f"Could not push intent: {e}")
    return False


def poll_partner_changes(config: dict) -> list:
    """Poll server for any changes from partner developers."""
    try:
        params = {
            "room":      config["room_id"],
            "developer": config["developer_name"],
            "ttl_hours": config.get("change_ttl_hours", 24)
        }
        r = requests.get(f"{config['server_url']}/poll", params=params, timeout=5)
        return r.json().get("changes", []) if r.ok else []
    except Exception as e:
        log.warning(f"Could not poll server: {e}")
        return []


# ── Change handler ────────────────────────────────────────────────────────────

class ChangeHandler(FileSystemEventHandler):
    def __init__(self, config: dict, project_path: str, codebase_map: dict = None):
        self.config       = config
        self.project_path = project_path
        self.state_path   = Path(project_path) / ".remi" / "file_state.json"
        self.state        = self._load_state()
        self.pending      = {}
        self.lock         = threading.Lock()
        self.codebase_map = codebase_map or {}
        self.map_lock     = threading.Lock()

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump(self.state, f, indent=2)

    def on_modified(self, event):
        if not event.is_directory and should_watch(event.src_path):
            self._schedule(event.src_path)

    def on_created(self, event):
        if not event.is_directory and should_watch(event.src_path):
            self._schedule(event.src_path)

    def _schedule(self, path: str):
        with self.lock:
            if path in self.pending:
                self.pending[path].cancel()
            timer = threading.Timer(DEBOUNCE_SECONDS, self._handle, args=[path])
            self.pending[path] = timer
            timer.start()

    def _handle(self, path: str):
        with self.lock:
            self.pending.pop(path, None)

        new_hash = file_hash(path)
        old_hash = self.state.get(path, {}).get("hash", "")
        if new_hash == old_hash:
            return

        content  = read_file(path)
        rel_path = os.path.relpath(path, self.project_path)
        name     = self.config.get("project_name", self.project_path)

        log.info(f"[{name}] Change detected: {rel_path}")
        print(f"💾 Remi [{name}]: Change detected: {rel_path}")

        # Silently infer intent from file content
        intent = infer_intent(rel_path, content)
        if intent:
            is_new = push_intent(self.config, rel_path, intent)
            log.info(f"[{name}] Intent inferred for {rel_path}: {intent}")
            if is_new:
                write_update(self.project_path, "📄",
                             self.config["developer_name"], rel_path, intent)
                log.info(f"[{name}] New file registered: {rel_path}")

        self.state[path] = {
            "hash":      new_hash,
            "timestamp": datetime.now().isoformat(),
            "developer": self.config["developer_name"]
        }
        self._save_state()

        with self.map_lock:
            codebase_map = self.codebase_map
        connected_files  = get_connected_files(codebase_map, rel_path)
        codebase_context = read_connected_content(self.project_path, connected_files)
        if connected_files:
            print(f"🔗 Connected files: {[c['file'] for c in connected_files]}")

        server_response = push_change(self.config, rel_path, content)

        if server_response and server_response.get("conflict"):
            partner_change = server_response["conflict"]
            log.info(f"[{name}] Conflict with {partner_change['developer']} on {rel_path}")
            self._resolve(rel_path, content, partner_change, codebase_context)
        else:
            log.info(f"[{name}] Change pushed, no conflict: {rel_path}")

    def _resolve(self, rel_path: str, my_content: str, partner_change: dict, codebase_context: str = ""):
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
            # Save a backup of the current file before overwriting
            full_path  = os.path.join(self.project_path, rel_path)
            backup_dir = Path(self.project_path) / ".remi" / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_name = (datetime.now().strftime("%Y%m%d_%H%M%S") + "_"
                           + rel_path.replace("/", "_").replace("\\", "_"))
            (backup_dir / backup_name).write_text(my_content, encoding="utf-8")
            log.info(f"Backup saved: .remi/backups/{backup_name}")

            result = run_agent(dev_a, dev_b, codebase_context, config=self.config)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(result["merged_code"])

            confidence = result.get("confidence", "?")
            log.info(f"Merged code written to {rel_path} (confidence: {confidence})")
            notify_mac("✅ Conflict resolved", f"Agent merged changes to {rel_path}")
            write_update(self.project_path, "✅",
                         self.config["developer_name"], rel_path,
                         f"Merged with {dev_b['developer']} — confidence: {confidence}")
            if confidence == "low":
                write_update(self.project_path, "⚠️",
                             self.config["developer_name"], rel_path,
                             "Low confidence merge — please review manually")

        except Exception as e:
            log.error(f"Agent failed to resolve conflict: {e}")


# ── Partner poller ────────────────────────────────────────────────────────────

class PartnerPoller(threading.Thread):
    def __init__(self, config: dict, handler: ChangeHandler):
        super().__init__(daemon=True)
        self.config      = config
        self.handler     = handler
        self.seen        = set()
        self.known_files = set()

    def _seed_known_files(self):
        """Populate known_files from current registry so startup doesn't flood updates."""
        try:
            r = requests.get(
                f"{self.config['server_url']}/intent/registry",
                params={"room_id": self.config["room_id"]},
                timeout=5
            )
            if r.ok:
                self.known_files = set(r.json().keys())
                log.info(f"Seeded known_files with {len(self.known_files)} entries")
        except Exception as e:
            log.warning(f"Could not seed known_files: {e}")

    def poll_new_files(self):
        """Check registry for files added by partners since last check."""
        try:
            r = requests.get(
                f"{self.config['server_url']}/intent/registry",
                params={"room_id": self.config["room_id"]},
                timeout=5
            )
            if not r.ok:
                return
            registry = r.json()
            for file_path, info in registry.items():
                if file_path in self.known_files:
                    continue
                self.known_files.add(file_path)
                developer = info.get("developer", "unknown")
                # Skip our own files — we already logged those in _handle
                if developer == self.config["developer_name"]:
                    continue
                intent = info.get("intent", "")
                write_update(self.handler.project_path, "📄",
                             developer, file_path, intent)
                print(f"📄 New file: {file_path} ({developer}) — {intent}")
                log.info(f"Partner new file: {file_path} from {developer}")
        except Exception as e:
            log.warning(f"New file poll error: {e}")

    def run(self):
        self._seed_known_files()
        new_file_tick = 0

        while True:
            try:
                # Poll for partner conflict changes every SYNC_INTERVAL seconds
                changes = poll_partner_changes(self.config)
                for change in changes:
                    key = f"{change['developer']}:{change['file']}:{change['timestamp']}"
                    if key in self.seen:
                        continue
                    self.seen.add(key)

                    full_path = os.path.join(self.handler.project_path, change["file"])
                    if os.path.exists(full_path):
                        my_content = read_file(full_path)
                        log.info(f"Partner change: {change['developer']} → {change['file']}")
                        notify_mac(change["developer"], change["file"])
                        with self.handler.map_lock:
                            codebase_map = self.handler.codebase_map
                        connected_files  = get_connected_files(codebase_map, change["file"])
                        codebase_context = read_connected_content(
                            self.handler.project_path, connected_files
                        )
                        self.handler._resolve(change["file"], my_content, change, codebase_context)

                # Poll for new files in registry every 30 seconds (6 × 5s ticks)
                new_file_tick += 1
                if new_file_tick >= 6:
                    new_file_tick = 0
                    self.poll_new_files()

            except Exception as e:
                log.warning(f"Partner poll error: {e}")

            time.sleep(SYNC_INTERVAL)


# ── Map rebuilder ─────────────────────────────────────────────────────────────

class MapRebuilder(threading.Thread):
    def __init__(self, handler: ChangeHandler, project_path: str):
        super().__init__(daemon=True)
        self.handler      = handler
        self.project_path = project_path

    def run(self):
        while True:
            time.sleep(30 * 60)
            try:
                log.info(f"Rebuilding codebase map for {self.project_path}...")
                new_map = build_map(self.project_path)
                save_map(self.project_path, new_map)
                with self.handler.map_lock:
                    self.handler.codebase_map = new_map
                log.info(f"Map rebuilt: {new_map['file_count']} files")
            except Exception as e:
                log.warning(f"Map rebuild failed: {e}")


# ── Main daemon ───────────────────────────────────────────────────────────────

def run_daemon():
    configs = load_all_project_configs()

    if not configs:
        print("❌ No active projects. cd into a project and run 'remi init'.")
        sys.exit(1)

    print(f"🐀 Remi is waking up...")

    observers = []

    for config in configs:
        project_path = config["project_path"]
        name         = config.get("project_name", Path(project_path).name)

        log.info(f"Starting watcher for {name}")
        print(f"\n   📁 {name}")
        print(f"      Developer : {config['developer_name']}")
        print(f"      Room      : {config['room_id']}")
        print(f"      Watching  : {project_path}")
        print(f"      Server    : {config['server_url']}")

        print(f"      🗺️  Building codebase map...")
        codebase_map = build_map(project_path)
        save_map(project_path, codebase_map)
        print(f"      ✅ Mapped {codebase_map['file_count']} files, "
              f"{len(codebase_map['connections'])} connections")

        handler   = ChangeHandler(config, project_path, codebase_map)
        observer  = Observer()
        observer.schedule(handler, project_path, recursive=True)
        observer.start()

        PartnerPoller(config, handler).start()
        MapRebuilder(handler, project_path).start()

        observers.append(observer)

    print(f"\n✅ Remi is awake — watching {len(configs)} project(s)")
    log.info(f"Remi started — watching {len(configs)} project(s)")
    print(f"👁  Watching for changes... (Ctrl-C to stop)\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        for observer in observers:
            observer.stop()
        log.info("Remi is going to sleep")
        print("🐀 Remi is going to sleep")

    for observer in observers:
        observer.join()


if __name__ == "__main__":
    run_daemon()
