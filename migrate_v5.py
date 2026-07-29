#!/usr/bin/env python3
"""
Migration script for memory rebuild v5.0.
Run this on the OWUI host (inside the container or with access to the DB).

What it does:
  1. Verifies the current schema is v4 (no migration needed — schema unchanged)
  2. Lists all filter/tool functions in the DB
  3. Optionally deactivates old filter functions (interactive)
  4. Copies memory_core.py to the data directory
  5. Reports memory count for verification

Usage:
  docker exec -it openwebui python3 /tmp/migrate_v5.py
  # or if memory_core.py is already in /app/backend/data/:
  docker exec -it openwebui python3 -c "
    import sys; sys.path.insert(0, '/tmp')
    from migrate_v5 import main; main()
  "
"""

import os
import sys
import sqlite3
import json
import shutil

DB_PATH = os.environ.get("OWUI_DB_PATH", "/app/backend/data/webui.db")
MEMORIES_DB_PATH = os.environ.get("OWUI_MEMORIES_DB_PATH", "/app/backend/data/memories.db")
DATA_PATH = os.environ.get("OWUI_DATA_PATH", "/app/backend/data")

def list_functions(db_path):
    """List all filter/tool functions in the OWUI database."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, name, type, is_active, is_global, updated_at "
        "FROM function ORDER BY type, is_active DESC, updated_at DESC"
    ).fetchall()
    conn.close()
    return rows

def count_memories(mem_db_path):
    """Count active memories in the memories database."""
    conn = sqlite3.connect(mem_db_path)
    total = conn.execute("SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL").fetchone()[0]
    pinned = conn.execute("SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL AND pinned=1").fetchone()[0]
    knowledge = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE deleted_at IS NULL").fetchone()[0]
    conn.close()
    return total, pinned, knowledge

def get_schema_version(mem_db_path):
    conn = sqlite3.connect(mem_db_path)
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else 0

def deactivate_function(db_path, func_id):
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE function SET is_active=0 WHERE id=?", (func_id,))
    conn.commit()
    conn.close()

def main():
    print("=" * 60)
    print("Memory Rebuild v5.0 — Migration Script")
    print("=" * 60)

    # 1. Schema check
    print("\n1. Schema check...")
    try:
        version = get_schema_version(MEMORIES_DB_PATH)
        print(f"   Memories DB schema version: {version}")
        if version >= 4:
            print("   ✅ Schema is v4 — no migration needed")
        else:
            print(f"   ⚠️  Schema is v{version}, expected v4. Manual migration required.")
    except Exception as e:
        print(f"   ❌ Could not read schema: {e}")
        print(f"   (Is the memories DB at {MEMORIES_DB_PATH}?)")

    # 2. Memory counts
    print("\n2. Memory counts...")
    try:
        total, pinned, knowledge = count_memories(MEMORIES_DB_PATH)
        print(f"   Memories: {total} active ({pinned} pinned)")
        print(f"   Knowledge items: {knowledge}")
    except Exception as e:
        print(f"   ❌ Could not count memories: {e}")

    # 3. List functions
    print("\n3. Functions in OWUI database...")
    try:
        functions = list_functions(DB_PATH)
        active = [f for f in functions if f[3]]
        inactive = [f for f in functions if not f[3]]
        print(f"   Active: {len(active)}, Inactive: {len(inactive)}")
        if active:
            print("\n   Active functions:")
            for fid, name, ftype, ia, ig, ts in active:
                scope = "global" if ig else "user"
                print(f"     [{fid}] {name} ({ftype}, {scope}) — updated {ts}")
        if inactive:
            print(f"\n   Inactive functions ({len(inactive)}):")
            for fid, name, ftype, ia, ig, ts in inactive:
                print(f"     [{fid}] {name} ({ftype}) — updated {ts}")
    except Exception as e:
        print(f"   ❌ Could not list functions: {e}")
        print(f"   (Is the OWUI DB at {DB_PATH}?)")

    # 4. Interactive deactivation
    print("\n4. Cleanup old functions...")
    try:
        functions = list_functions(DB_PATH)
        active_filters = [f for f in functions if f[3] and f[2] == "filter"]
        if len(active_filters) > 1:
            print(f"   Found {len(active_filters)} active filters. Recommend keeping only the new v5 filter.")
            answer = input("   Deactivate all other active filters? (y/N): ").strip().lower()
            if answer == "y":
                # Don't deactivate the new one — user will identify it
                for fid, name, ftype, ia, ig, ts in active_filters:
                    if "v5" not in name.lower() and "rebuild" not in name.lower():
                        print(f"   Deactivating [{fid}] {name}...")
                        deactivate_function(DB_PATH, fid)
                print("   ✅ Done. Activate the new v5 filter in OWUI admin panel.")
        else:
            print("   Only one active filter — no cleanup needed.")
    except KeyboardInterrupt:
        print("\n   Skipped.")
    except Exception as e:
        print(f"   ❌ Cleanup failed: {e}")

    # 5. Copy memory_core.py
    print("\n5. Install memory_core.py...")
    core_src = os.path.join(os.path.dirname(__file__), "memory_core.py")
    core_dst = os.path.join(DATA_PATH, "memory_core.py")
    if os.path.exists(core_src):
        try:
            shutil.copy2(core_src, core_dst)
            print(f"   ✅ Copied memory_core.py to {core_dst}")
        except Exception as e:
            print(f"   ❌ Copy failed: {e}")
            print(f"   Manual: cp memory_core.py {core_dst}")
    else:
        print(f"   ⚠️  memory_core.py not found at {core_src}")
        print(f"   Copy it manually to {core_dst}")

    print("\n" + "=" * 60)
    print("Migration complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. In OWUI Admin → Functions:")
    print("     a. Create new Filter from memory_filter.py content")
    print("     b. Create new Tool from memory_tool.py content")
    print("     c. Activate both, deactivate old filter")
    print("  2. Test with: 'What do you remember about me?'")
    print("  3. Verify auto-store works after a short conversation")
    print()

if __name__ == "__main__":
    main()
