#!/usr/bin/env python3
"""
Remi - Installer
Run this once. It sets everything up and disappears into the background.
"""

import os
import sys
import json
import platform
import subprocess
from pathlib import Path

CONFIG_DIR  = Path.home() / ".remi"
CONFIG_PATH = CONFIG_DIR / "config.json"
PLIST_PATH  = Path.home() / "Library" / "LaunchAgents" / "com.remi-agent.plist"
SERVICE_NAME = "remi-agent"

REQUIRED_PACKAGES = [
    "anthropic",
    "watchdog",
    "requests",
    "flask"
]


def banner():
    print("""
╔═══════════════════════════════════════╗
║            Remi Installer             ║
║   Set up once. Runs forever silently. ║
╚═══════════════════════════════════════╝
""")


def install_dependencies():
    print("📦 Installing dependencies...")
    for pkg in REQUIRED_PACKAGES:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg, "-q", "--break-system-packages"],
            check=True
        )
    print("   ✅ Dependencies installed\n")


def get_config() -> dict:
    print("⚙️  Let's set up your config (one time only)\n")

    developer_name = input("   Your name (e.g. Alex): ").strip()
    if not developer_name:
        developer_name = "Developer"

    room_id = input("   Room ID — share this with your team (e.g. mygame2024): ").strip()
    if not room_id:
        room_id = "default-room"

    server_url = input("   Server URL (press Enter to use localhost for testing): ").strip()
    if not server_url:
        server_url = "http://localhost:8080"

    project_path = input("   Full path to your project folder: ").strip()
    project_path = os.path.expanduser(project_path)
    if not os.path.isdir(project_path):
        print(f"   ⚠️  Directory not found: {project_path}")
        print("   Creating it...")
        os.makedirs(project_path, exist_ok=True)

    api_key = input("   Anthropic API key: ").strip()

    return {
        "developer_name": developer_name,
        "room_id":        room_id,
        "server_url":     server_url,
        "project_path":   project_path,
        "api_key":        api_key
    }


def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Save config without API key in plain config
    safe_config = {k: v for k, v in config.items() if k != "api_key"}
    with open(CONFIG_PATH, "w") as f:
        json.dump(safe_config, f, indent=2)

    # Save API key separately in a secured file
    key_path = CONFIG_DIR / ".api_key"
    with open(key_path, "w") as f:
        f.write(config["api_key"])
    os.chmod(key_path, 0o600)  # Only owner can read

    print(f"   ✅ Config saved to {CONFIG_PATH}\n")


def get_watcher_path() -> str:
    """Find where watcher.py is located."""
    # Check same directory as this install script
    here = Path(__file__).parent
    watcher = here / "watcher.py"
    if watcher.exists():
        return str(watcher.resolve())
    print("❌ Could not find watcher.py. Make sure it's in the same folder as install.py")
    sys.exit(1)


def register_mac(config: dict):
    """Register as a launchd service on macOS — starts automatically on login."""
    watcher_path = get_watcher_path()
    api_key_path = str(CONFIG_DIR / ".api_key")

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.remi-agent</string>

    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{watcher_path}</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>ANTHROPIC_API_KEY_FILE</key>
        <string>{api_key_path}</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>{str(CONFIG_DIR / "stdout.log")}</string>

    <key>StandardErrorPath</key>
    <string>{str(CONFIG_DIR / "stderr.log")}</string>
</dict>
</plist>"""

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST_PATH, "w") as f:
        f.write(plist_content)

    # Load the service
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True)

    if result.returncode == 0:
        print("   ✅ Registered as macOS background service (starts on login)\n")
    else:
        print(f"   ⚠️  Could not register service: {result.stderr.decode()}")
        print(f"   You can start it manually with: python {watcher_path}\n")


def register_windows(config: dict):
    """Register as a Task Scheduler job on Windows."""
    watcher_path = get_watcher_path()
    api_key_path = str(CONFIG_DIR / ".api_key")

    task_xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>{sys.executable}</Command>
      <Arguments>{watcher_path}</Arguments>
    </Exec>
  </Actions>
  <Settings>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
</Task>"""

    task_path = CONFIG_DIR / "task.xml"
    with open(task_path, "w", encoding="utf-16") as f:
        f.write(task_xml)

    result = subprocess.run(
        ["schtasks", "/create", "/tn", SERVICE_NAME,
         "/xml", str(task_path), "/f"],
        capture_output=True
    )

    if result.returncode == 0:
        subprocess.run(["schtasks", "/run", "/tn", SERVICE_NAME])
        print("   ✅ Registered as Windows scheduled task (starts on login)\n")
    else:
        print(f"   ⚠️  Could not register task: {result.stderr.decode()}")
        print(f"   You can start it manually with: python {watcher_path}\n")


def register_linux(config: dict):
    """Register as a systemd user service on Linux."""
    watcher_path = get_watcher_path()
    api_key_path = str(CONFIG_DIR / ".api_key")

    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_dir / "remi-agent.service"

    service_content = f"""[Unit]
Description=Remi - Silent collaborative coding agent
After=network.target

[Service]
Type=simple
ExecStart={sys.executable} {watcher_path}
Environment=ANTHROPIC_API_KEY_FILE={api_key_path}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""

    with open(service_path, "w") as f:
        f.write(service_content)

    subprocess.run(["systemctl", "--user", "daemon-reload"])
    subprocess.run(["systemctl", "--user", "enable", "remi-agent"])
    subprocess.run(["systemctl", "--user", "start", "remi-agent"])
    print("   ✅ Registered as systemd service (starts on login)\n")


def register_background_service(config: dict):
    print("🔧 Registering background service...")
    system = platform.system()
    if system == "Darwin":
        register_mac(config)
    elif system == "Windows":
        register_windows(config)
    elif system == "Linux":
        register_linux(config)
    else:
        print(f"   ⚠️  Unknown OS: {system}. Start manually with: python watcher.py")


def print_success(config: dict):
    print(f"""
╔═══════════════════════════════════════════════════════╗
║               ✅ Remi is running!                     ║
╚═══════════════════════════════════════════════════════╝

  Developer:  {config['developer_name']}
  Room:       {config['room_id']}
  Watching:   {config['project_path']}
  Server:     {config['server_url']}

  The agent is now running silently in the background.
  It will restart automatically every time you log in.

  The only thing you need to check:
  👉  {config['project_path']}/remi_log.md

  Share your Room ID ({config['room_id']}) with your teammates
  so Remi connects to the same room.

  To check Remi's logs if something seems wrong:
  👉  ~/.remi/daemon.log
""")


def main():
    banner()
    install_dependencies()
    config = get_config()
    save_config(config)
    register_background_service(config)
    print_success(config)


if __name__ == "__main__":
    main()
