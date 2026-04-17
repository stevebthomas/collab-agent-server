"""
Collab Agent - Sync Server
Lightweight server that sits between developers.
Receives pushes, detects conflicts, routes changes.
Deploy this once to Railway/Render/any cheap host.
"""

from flask import Flask, request, jsonify
from datetime import datetime
import threading
import sqlite3
import json
import os

app = Flask(__name__)

DB_PATH    = os.environ.get("DB_PATH", "collab_agent.db")
store_lock = threading.Lock()


# ── Database setup ─────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS changes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id       TEXT NOT NULL,
                file_path     TEXT NOT NULL,
                developer     TEXT NOT NULL,
                content       TEXT NOT NULL,
                intent        TEXT DEFAULT '',
                timestamp     TEXT NOT NULL,
                timestamp_unix REAL NOT NULL,
                UNIQUE(room_id, file_path, developer)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS intent_registry (
                room_id        TEXT NOT NULL,
                developer      TEXT NOT NULL,
                file_path      TEXT NOT NULL,
                intent         TEXT NOT NULL,
                timestamp_unix REAL NOT NULL,
                UNIQUE(room_id, file_path) ON CONFLICT REPLACE
            )
        """)
        conn.commit()


init_db()


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_partner_change(conn, room_id: str, file_path: str, developer: str):
    """Return the most recent change to this file by any other developer."""
    row = conn.execute("""
        SELECT * FROM changes
        WHERE room_id = ? AND file_path = ? AND developer != ?
        ORDER BY timestamp_unix DESC
        LIMIT 1
    """, (room_id, file_path, developer)).fetchone()
    return dict(row) if row else None


def upsert_change(conn, room_id, file_path, developer, content, intent, timestamp):
    conn.execute("""
        INSERT INTO changes (room_id, file_path, developer, content, intent, timestamp, timestamp_unix)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(room_id, file_path, developer) DO UPDATE SET
            content        = excluded.content,
            intent         = excluded.intent,
            timestamp      = excluded.timestamp,
            timestamp_unix = excluded.timestamp_unix
    """, (room_id, file_path, developer, content, intent, timestamp, datetime.now().timestamp()))
    conn.commit()


def delete_change(conn, room_id: str, file_path: str, developer: str):
    """Remove a change after a conflict is resolved so it doesn't re-trigger."""
    conn.execute("""
        DELETE FROM changes
        WHERE room_id = ? AND file_path = ? AND developer = ?
    """, (room_id, file_path, developer))
    conn.commit()


# ── Routes ─────────────────────────────────────────────────────────────────────

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
        with get_db() as conn:
            conflict = get_partner_change(conn, room_id, file_path, developer)
            upsert_change(conn, room_id, file_path, developer, content, intent, timestamp)

            if conflict:
                # Clear both sides so the resolved version doesn't re-trigger
                delete_change(conn, room_id, file_path, developer)
                delete_change(conn, room_id, file_path, conflict["developer"])

    if conflict:
        return jsonify({"status": "conflict", "conflict": conflict})
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

    ttl_hours = float(request.args.get("ttl_hours", 24))
    cutoff    = datetime.now().timestamp() - (ttl_hours * 3600)

    with store_lock:
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM changes
                WHERE room_id = ? AND developer != ? AND timestamp_unix > ?
                ORDER BY timestamp_unix ASC
            """, (room_id, developer, cutoff)).fetchall()

    changes = []
    for row in rows:
        d = dict(row)
        d["file"] = d["file_path"]   # alias so watcher.py can use change['file']
        changes.append(d)
    return jsonify({"changes": changes})


@app.route("/resolve", methods=["POST"])
def resolve():
    """
    Mark a conflict as resolved — removes both sides from the store.
    Called by the agent after writing the merged file.
    """
    data      = request.json
    room_id   = data.get("room")
    file_path = data.get("file")

    if not room_id or not file_path:
        return jsonify({"error": "Missing room or file"}), 400

    with store_lock:
        with get_db() as conn:
            conn.execute("""
                DELETE FROM changes WHERE room_id = ? AND file_path = ?
            """, (room_id, file_path))
            conn.commit()

    return jsonify({"status": "resolved"})


@app.route("/intent/update", methods=["POST"])
def intent_update():
    """Upsert a file's intent into the registry."""
    data      = request.json
    room_id   = data.get("room_id")
    developer = data.get("developer")
    file_path = data.get("file_path")
    intent    = data.get("intent")

    if not all([room_id, developer, file_path, intent]):
        return jsonify({"error": "Missing required fields"}), 400

    with store_lock:
        with get_db() as conn:
            existing = conn.execute("""
                SELECT 1 FROM intent_registry WHERE room_id = ? AND file_path = ?
            """, (room_id, file_path)).fetchone()
            new_file = existing is None

            conn.execute("""
                INSERT INTO intent_registry (room_id, developer, file_path, intent, timestamp_unix)
                VALUES (?, ?, ?, ?, ?)
            """, (room_id, developer, file_path, intent, datetime.now().timestamp()))
            conn.commit()

    return jsonify({"status": "ok", "new_file": new_file})


@app.route("/intent/registry", methods=["GET"])
def intent_registry():
    """Return the full intent map for a room."""
    room_id = request.args.get("room_id")
    if not room_id:
        return jsonify({"error": "Missing room_id"}), 400

    with store_lock:
        with get_db() as conn:
            rows = conn.execute("""
                SELECT file_path, intent, developer, timestamp_unix
                FROM intent_registry
                WHERE room_id = ?
                ORDER BY timestamp_unix ASC
            """, (room_id,)).fetchall()

    result = {}
    for row in rows:
        result[row["file_path"]] = {
            "intent":    row["intent"],
            "developer": row["developer"],
            "updated":   datetime.fromtimestamp(row["timestamp_unix"]).isoformat()
        }

    return jsonify(result)


@app.route("/intent/check", methods=["GET"])
def intent_check():
    """Return files whose intent semantically overlaps with a prompt (keyword match)."""
    room_id = request.args.get("room_id")
    prompt  = request.args.get("prompt", "")

    if not room_id or not prompt:
        return jsonify({"error": "Missing room_id or prompt"}), 400

    words = [w.lower() for w in prompt.split() if len(w) > 2]

    with store_lock:
        with get_db() as conn:
            rows = conn.execute("""
                SELECT file_path, intent, developer, timestamp_unix
                FROM intent_registry
                WHERE room_id = ?
            """, (room_id,)).fetchall()

    matches = {}
    for row in rows:
        if any(word in row["intent"].lower() for word in words):
            matches[row["file_path"]] = {
                "intent":    row["intent"],
                "developer": row["developer"],
                "updated":   datetime.fromtimestamp(row["timestamp_unix"]).isoformat()
            }

    return jsonify({"matches": matches, "query": prompt})


@app.route("/status", methods=["GET"])
def status():
    """Health check + room status."""
    room_id = request.args.get("room")
    with store_lock:
        with get_db() as conn:
            if room_id:
                rows = conn.execute("""
                    SELECT file_path, developer FROM changes WHERE room_id = ?
                """, (room_id,)).fetchall()
                files = {}
                for row in rows:
                    files.setdefault(row["file_path"], []).append(row["developer"])
                return jsonify({"status": "ok", "room": room_id, "active_files": files})

            count = conn.execute("SELECT COUNT(DISTINCT room_id) FROM changes").fetchone()[0]
            return jsonify({"status": "ok", "rooms": count})


@app.route("/")
def index():
    return jsonify({
        "service":   "Collab Agent Sync Server",
        "version":   "2.0.0",
        "storage":   "SQLite (persistent)",
        "endpoints": ["/push", "/poll", "/resolve", "/status",
                      "/intent/update", "/intent/registry", "/intent/check"]
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 Collab Agent server running on port {port}")
    print(f"   Database: {DB_PATH}")
    app.run(host="0.0.0.0", port=port, debug=False)
