"""
Collab Agent - Sync Server
Lightweight server that sits between developers.
Receives pushes, detects conflicts, routes changes.
Deploy this once to Railway/Render/any cheap host.
"""

from flask import Flask, request, jsonify
from datetime import datetime
import threading
import json
import os

app = Flask(__name__)

# In-memory store (replace with Redis or SQLite for persistence)
# Structure: { room_id: { file_path: { developer: change_data } } }
store      = {}
store_lock = threading.Lock()

CHANGE_TTL_SECONDS = 300  # Changes expire after 5 minutes


def get_room(room_id: str) -> dict:
    if room_id not in store:
        store[room_id] = {}
    return store[room_id]


def clean_expired(room: dict):
    """Remove changes older than TTL."""
    now = datetime.now().timestamp()
    for file_path in list(room.keys()):
        for dev in list(room[file_path].keys()):
            ts = room[file_path][dev].get("timestamp_unix", 0)
            if now - ts > CHANGE_TTL_SECONDS:
                del room[file_path][dev]
        if not room[file_path]:
            del room[file_path]


@app.route("/push", methods=["POST"])
def push():
    """
    Developer pushes a file change.
    If another developer has recently changed the same file, returns conflict data.
    """
    data      = request.json
    room_id   = data.get("room")
    developer = data.get("developer")
    file_path = data.get("file")
    content   = data.get("content")
    intent    = data.get("intent", "")
    timestamp = data.get("timestamp", datetime.now().isoformat())

    if not all([room_id, developer, file_path, content]):
        return jsonify({"error": "Missing required fields"}), 400

    with store_lock:
        room = get_room(room_id)
        clean_expired(room)

        if file_path not in room:
            room[file_path] = {}

        # Check for conflict — another dev has touched this file recently
        conflict = None
        for dev, change in room[file_path].items():
            if dev != developer:
                conflict = change
                break

        # Store this developer's change
        room[file_path][developer] = {
            "developer":     developer,
            "content":       content,
            "intent":        intent,
            "timestamp":     timestamp,
            "timestamp_unix": datetime.now().timestamp()
        }

    if conflict:
        return jsonify({
            "status":   "conflict",
            "conflict": conflict
        })
    else:
        return jsonify({"status": "ok"})


@app.route("/poll", methods=["GET"])
def poll():
    """
    Developer polls for any changes from partners.
    Returns changes to files that the polling developer also has locally.
    """
    room_id   = request.args.get("room")
    developer = request.args.get("developer")

    if not room_id or not developer:
        return jsonify({"error": "Missing room or developer"}), 400

    with store_lock:
        room = get_room(room_id)
        clean_expired(room)

        changes = []
        for file_path, devs in room.items():
            for dev, change in devs.items():
                if dev != developer:
                    changes.append(change)

    return jsonify({"changes": changes})


@app.route("/status", methods=["GET"])
def status():
    """Health check + room status."""
    room_id = request.args.get("room")
    with store_lock:
        if room_id:
            room  = get_room(room_id)
            files = {f: list(devs.keys()) for f, devs in room.items()}
            return jsonify({
                "status":    "ok",
                "room":      room_id,
                "active_files": files
            })
        return jsonify({"status": "ok", "rooms": len(store)})


@app.route("/")
def index():
    return jsonify({
        "service": "Collab Agent Sync Server",
        "version": "1.0.0",
        "endpoints": ["/push", "/poll", "/status"]
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 Collab Agent server running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
