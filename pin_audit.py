#!/usr/bin/env python3
"""
pin_audit.py — enumerate ALL pinned memories + knowledge items (read-only).

Purpose: pin cleanup (Sept 2026). 144 pinned memories dilute recall even after
the v5.2 floor fix (pinned items at 0.45-0.55 still outrank better non-pinned
hits via the +0.15 boost). Target: ~25 critical keeps, unpin the rest.

Safety: opens memories.db in READ-ONLY mode (SQLite URI mode=ro). It cannot
modify anything. Output: /tmp/pin_audit_report.txt inside the container.

Deploy (on nodec as root, one block):
  pct pull 111 /projects/memory-rebuild/pin_audit.py /tmp/pin_audit.py
  pct push 110 /tmp/pin_audit.py /tmp/pin_audit.py
  pct exec 110 -- bash -c 'docker cp /tmp/pin_audit.py openwebui:/tmp/ && docker exec openwebui python3 /tmp/pin_audit.py && docker cp openwebui:/tmp/pin_audit_report.txt /tmp/'
  pct pull 110 /tmp/pin_audit_report.txt /tmp/pin_audit_report.txt
  pct push 111 /tmp/pin_audit_report.txt /projects/memory-rebuild/pin_audit_report.txt
"""

import os
import sqlite3
from datetime import datetime, timezone

BASE = os.environ.get("OWUI_DATA_PATH", "/app/backend/data")
DB = os.path.join(BASE, "memories.db")
OUT = "/tmp/pin_audit_report.txt"

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)  # read-only — cannot corrupt
cur = conn.cursor()
lines = []


def first_that_runs(queries):
    """Try queries in order; return (rows, error)."""
    last_err = None
    for sql in queries:
        try:
            return cur.execute(sql).fetchall(), None
        except sqlite3.Error as e:
            last_err = e
    return None, last_err


# ── Pinned memories ──
mrows, merr = first_that_runs([
    "SELECT id, user_id, use_count, timestamp, "
    "substr(COALESCE(summary, user_msg, assistant_msg, ''), 1, 200) "
    "FROM memories WHERE pinned=1 AND deleted_at IS NULL ORDER BY id",
])
n_m = len(mrows) if mrows is not None else "?"
lines.append(f"### PINNED MEMORIES ({n_m}) ###")
if merr:
    lines.append(f"MEM QUERY FAILED: {merr}")
else:
    for mid, uid, uc, ts, s in mrows:
        s = " ".join((s or "").split())
        lines.append(f"mem_{mid} | {(uid or '-')[:10]} | {ts} | use={uc or 0} | {s}")

# ── Pinned knowledge ──
krows, kerr = first_that_runs([
    "SELECT id, owner_user_id, use_count, timestamp, "
    "substr(COALESCE(title, ''), 1, 90), "
    "substr(COALESCE(content, ''), 1, 110) "
    "FROM knowledge_items WHERE pinned=1 AND deleted_at IS NULL ORDER BY id",
])
n_k = len(krows) if krows is not None else "?"
lines.append("")
lines.append(f"### PINNED KNOWLEDGE ({n_k}) ###")
if kerr:
    lines.append(f"KNOWLEDGE QUERY FAILED: {kerr}")
else:
    for kid, oid, uc, ts, t, c in krows:
        t = " ".join((t or "").split())
        c = " ".join((c or "").split())
        lines.append(f"kn_{kid} | {(oid or '-')[:10]} | {ts} | use={uc or 0} | {t} :: {c}")

# ── Totals ──
def totals(table):
    try:
        return cur.execute(
            f"SELECT COUNT(*), COALESCE(SUM(pinned),0) "
            f"FROM {table} WHERE deleted_at IS NULL").fetchone()
    except sqlite3.Error as e:
        return ("?", f"ERR:{e}")

mtot, mpin = totals("memories")
ktot, kpin = totals("knowledge_items")
lines.append("")
lines.append(f"TOTALS: memories {mtot} total / {mpin} pinned | knowledge {ktot} total / {kpin} pinned")
lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()} (UTC)")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
conn.close()

print(f"Pinned memories: {n_m} (of {mtot} total) | pinned knowledge: {n_k} (of {ktot} total)")
print(f"Report written: {OUT} ({os.path.getsize(OUT)} bytes)")
