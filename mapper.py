"""
Collab Agent - Codebase Mapper
Scans the project folder and builds a relationship map of:
- What functions/classes are defined in each file
- What functions/classes each file calls or imports
- Which files are connected to each other

This gives the agent full codebase context, not just single-file awareness.
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime

WATCHED_EXTS = {".py", ".js", ".ts", ".cs"}
IGNORED_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
MAP_FILE     = "codebase_map.json"


# ── Parsers ───────────────────────────────────────────────────────────────────

def extract_python(content: str, rel_path: str) -> dict:
    """Extract definitions and references from Python files."""
    defines  = []
    calls    = []
    imports  = []

    for line in content.splitlines():
        stripped = line.strip()

        # Definitions
        if stripped.startswith("def "):
            match = re.match(r"def (\w+)\s*\(", stripped)
            if match:
                defines.append(match.group(1))

        elif stripped.startswith("class "):
            match = re.match(r"class (\w+)", stripped)
            if match:
                defines.append(match.group(1))

        # Imports
        elif stripped.startswith("import ") or stripped.startswith("from "):
            imports.append(stripped)

        # Function calls (simple heuristic)
        call_matches = re.findall(r"(\w+)\s*\(", stripped)
        calls.extend(call_matches)

    return {
        "file":    rel_path,
        "defines": list(set(defines)),
        "calls":   list(set(calls)),
        "imports": imports
    }


def extract_javascript(content: str, rel_path: str) -> dict:
    """Extract definitions and references from JS/TS files."""
    defines = []
    calls   = []
    imports = []

    for line in content.splitlines():
        stripped = line.strip()

        # Function definitions
        fn_match = re.match(r"(?:function\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s*)?\(|(\w+)\s*:\s*(?:async\s*)?\()", stripped)
        if fn_match:
            name = fn_match.group(1) or fn_match.group(2) or fn_match.group(3)
            if name:
                defines.append(name)

        # Class definitions
        class_match = re.match(r"class\s+(\w+)", stripped)
        if class_match:
            defines.append(class_match.group(1))

        # Imports
        if stripped.startswith("import ") or stripped.startswith("require("):
            imports.append(stripped)

        # Calls
        call_matches = re.findall(r"(\w+)\s*\(", stripped)
        calls.extend(call_matches)

    return {
        "file":    rel_path,
        "defines": list(set(defines)),
        "calls":   list(set(calls)),
        "imports": imports
    }


def extract_info(content: str, rel_path: str) -> dict:
    """Route to the right parser based on file extension."""
    ext = Path(rel_path).suffix
    if ext == ".py":
        return extract_python(content, rel_path)
    elif ext in {".js", ".ts"}:
        return extract_javascript(content, rel_path)
    else:
        # Generic fallback
        return {"file": rel_path, "defines": [], "calls": [], "imports": []}


# ── Map builder ───────────────────────────────────────────────────────────────

def build_map(project_path: str) -> dict:
    """
    Scan entire project and build the relationship map.
    Returns a dict with:
      - files: info about each file (defines, calls, imports)
      - connections: which files are connected to each other and why
    """
    files_info = {}

    for root, dirs, files in os.walk(project_path):
        # Skip ignored directories
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]

        for filename in files:
            ext = Path(filename).suffix
            if ext not in WATCHED_EXTS:
                continue

            full_path = os.path.join(root, filename)
            rel_path  = os.path.relpath(full_path, project_path)

            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                files_info[rel_path] = extract_info(content, rel_path)
            except Exception:
                continue

    # Build connection map — which files are connected to each other
    connections = {}
    for file_a, info_a in files_info.items():
        connected = []
        for file_b, info_b in files_info.items():
            if file_a == file_b:
                continue

            # Check if file_a calls anything defined in file_b
            overlap = set(info_a["calls"]) & set(info_b["defines"])
            if overlap:
                connected.append({
                    "file":   file_b,
                    "reason": f"calls {', '.join(list(overlap)[:3])} which is defined here"
                })

        if connected:
            connections[file_a] = connected

    return {
        "project_path": project_path,
        "built_at":     datetime.now().isoformat(),
        "file_count":   len(files_info),
        "files":        files_info,
        "connections":  connections
    }


def save_map(project_path: str, codebase_map: dict):
    """Save the map to the project folder."""
    map_path = os.path.join(project_path, MAP_FILE)
    with open(map_path, "w") as f:
        json.dump(codebase_map, f, indent=2)


def load_map(project_path: str) -> dict:
    """Load existing map or return empty."""
    map_path = os.path.join(project_path, MAP_FILE)
    if not os.path.exists(map_path):
        return {}
    with open(map_path) as f:
        return json.load(f)


def get_connected_files(codebase_map: dict, changed_file: str) -> list:
    """
    Given a file that just changed, return all files connected to it.
    These are files that call functions defined in the changed file,
    or files whose functions are called by the changed file.
    """
    if not codebase_map:
        return []

    connections = codebase_map.get("connections", {})
    files_info  = codebase_map.get("files", {})
    connected   = []

    # Files that call something in the changed file
    if changed_file in connections:
        for conn in connections[changed_file]:
            connected.append({
                "file":      conn["file"],
                "direction": "outgoing",
                "reason":    conn["reason"]
            })

    # Files whose definitions are called by the changed file
    changed_calls = files_info.get(changed_file, {}).get("calls", [])
    for other_file, info in files_info.items():
        if other_file == changed_file:
            continue
        overlap = set(changed_calls) & set(info.get("defines", []))
        if overlap:
            connected.append({
                "file":      other_file,
                "direction": "incoming",
                "reason":    f"defines {', '.join(list(overlap)[:3])} which {changed_file} calls"
            })

    # Deduplicate
    seen  = set()
    dedup = []
    for c in connected:
        if c["file"] not in seen:
            seen.add(c["file"])
            dedup.append(c)

    return dedup


def read_connected_content(project_path: str, connected_files: list) -> str:
    """Read the actual content of connected files for agent context."""
    if not connected_files:
        return "No connected files found."

    context = ""
    for conn in connected_files[:5]:  # Limit to 5 most relevant
        full_path = os.path.join(project_path, conn["file"])
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            context += f"\n--- {conn['file']} ({conn['reason']}) ---\n"
            context += content[:2000]  # Limit per file
            context += "\n"
        except Exception:
            continue

    return context if context else "Could not read connected files."


# ── Rebuild trigger ───────────────────────────────────────────────────────────

def should_rebuild(codebase_map: dict, max_age_minutes: int = 30) -> bool:
    """Check if the map is stale and needs rebuilding."""
    if not codebase_map:
        return True
    built_at = codebase_map.get("built_at", "")
    if not built_at:
        return True
    try:
        built   = datetime.fromisoformat(built_at)
        age     = (datetime.now() - built).total_seconds() / 60
        return age > max_age_minutes
    except Exception:
        return True


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    print(f"🗺️  Building codebase map for: {path}")
    codebase_map = build_map(path)
    save_map(path, codebase_map)
    print(f"   ✅ Mapped {codebase_map['file_count']} files")
    print(f"   🔗 Found connections in {len(codebase_map['connections'])} files")
    for file, conns in list(codebase_map["connections"].items())[:5]:
        print(f"      {file} → {[c['file'] for c in conns]}")
