#!/usr/bin/env python3
"""
memory_core v5.2 patch — recall quality fixes + latency instrumentation.

Root cause (diagnosed Sept 1, 2026): pinned memories bypass the similarity gate
(`if sim >= min_sim or pinned:`) in search_memories() and search_knowledge().
With 144 pinned items, off-topic pinned memories (sim 0.31-0.48) routinely
consume the entire MAX_ITEMS=3 recall budget, crowding out relevant hits and
clouding the model's context. Additionally: keyword LIKE-fallback pads
knowledge slots with sim≈0 items during auto-recall, and the uncapped
use_count boost creates a rich-get-richer ranking loop.

Fixes in this patch:
  1. PINNED_MIN_SIM=0.45 — pinned items get ranking priority but must still
     clear a relevance floor to be injected.
  2. Keyword fallback gated to explicit (low-threshold) searches only.
  3. use_count recall boost capped at 10.
  4. Latency + quality instrumentation: embed_ms, scan_ms, selected count,
     mean_sim of injected items — logged on every recall.

Safety:
  - Verifies every anchor string appears EXACTLY ONCE before touching anything.
    Aborts without writing if the live file differs from expectations.
  - Creates timestamped backup before editing.
  - py_compile syntax check after editing; restores backup on failure.

Run INSIDE the openwebui container:
  docker exec openwebui python3 /tmp/memory_core_v5.2_patch.py
"""

import os
import shutil
import sys
import py_compile
from datetime import datetime

BASE = os.environ.get("OWUI_DATA_PATH", "/app/backend/data")
TARGET = os.path.join(BASE, "memory_core.py")

# ── Surgical edits: (anchor_old, replacement_new, description) ──
EDITS = [
    # 1. New constant: pinned relevance floor
    (
        "FULL_TEXT_SIM_THRESHOLD = 0.80",
        "FULL_TEXT_SIM_THRESHOLD = 0.80\n"
        "# v5.2: pinned items must still clear a relevance floor to be auto-recalled\n"
        "PINNED_MIN_SIM = 0.45",
        "Add PINNED_MIN_SIM = 0.45 constant",
    ),
    # 2. search_memories(): pinned bypass → relevance floor
    (
        "        if sim >= min_sim or pinned:\n"
        "            scored.append({",
        "        if sim >= min_sim or (pinned and sim >= PINNED_MIN_SIM):\n"
        "            scored.append({",
        "search_memories: pinned items must clear PINNED_MIN_SIM gate",
    ),
    # 3. search_knowledge(): pinned bypass → relevance floor
    (
        "            if sim >= min_sim or pinned:",
        "            if sim >= min_sim or (pinned and sim >= PINNED_MIN_SIM):",
        "search_knowledge: pinned items must clear PINNED_MIN_SIM gate",
    ),
    # 4. search_memories(): cap rich-get-richer boost
    (
        "        effective += (use_count or 0) * RECALL_BOOST_FACTOR",
        "        effective += min(use_count or 0, 10) * RECALL_BOOST_FACTOR",
        "search_memories: cap use_count recall boost at 10",
    ),
    # 5. search_knowledge(): cap rich-get-richer boost
    (
        "            weighted += (use_count or 0) * RECALL_BOOST_FACTOR",
        "            weighted += min(use_count or 0, 10) * RECALL_BOOST_FACTOR",
        "search_knowledge: cap use_count recall boost at 10",
    ),
    # 6. search_knowledge(): keyword fallback only for explicit searches
    (
        "    # Keyword fallback if not enough results\n"
        "    if len(selected) < top_k:",
        "    # Keyword fallback if not enough results\n"
        "    # v5.2: only for explicit/low-threshold searches; auto-recall must not\n"
        "    # pad the prompt with near-zero-sim keyword matches\n"
        "    if len(selected) < top_k and min_sim <= KNOWLEDGE_EXPLICIT_MIN_SIM:",
        "search_knowledge: keyword fallback gated to explicit searches",
    ),
    # 7. search_memories(): timing — embed phase
    (
        "    q_vec = embed(query)\n"
        "    if q_vec is None:\n"
        "        return []\n"
        "    full_detail_tags = load_full_detail_tags()",
        "    t0 = time.time()\n"
        "    q_vec = embed(query)\n"
        "    if q_vec is None:\n"
        "        return []\n"
        "    _embed_ms = (time.time() - t0) * 1000\n"
        "    t0 = time.time()\n"
        "    full_detail_tags = load_full_detail_tags()",
        "search_memories: instrument embed latency",
    ),
    # 8. search_memories(): timing — scan phase + quality log
    (
        "    for hit in selected:\n"
        "        del hit[\"embedding\"]\n"
        "    return selected",
        "    for hit in selected:\n"
        "        del hit[\"embedding\"]\n"
        "    _scan_ms = (time.time() - t0) * 1000\n"
        "    _mean_sim = sum(h[\"sim\"] for h in selected) / len(selected) if selected else 0.0\n"
        "    logger.info(\n"
        "        f\"search_memories: selected={len(selected)} embed_ms={_embed_ms:.1f} \"\n"
        "        f\"scan_ms={_scan_ms:.1f} mean_sim={_mean_sim:.3f}\")\n"
        "    return selected",
        "search_memories: log scan latency + mean sim of injected items",
    ),
    # 9. search_knowledge(): timing — embed phase
    (
        "def search_knowledge(query: str, user_id: str, min_sim: float,\n"
        "                     top_k: int = MAX_KNOWLEDGE_ITEMS) -> list:\n"
        "    q_vec = embed(query)\n"
        "    policy_map = get_source_policy_map(user_id)",
        "def search_knowledge(query: str, user_id: str, min_sim: float,\n"
        "                     top_k: int = MAX_KNOWLEDGE_ITEMS) -> list:\n"
        "    t0 = time.time()\n"
        "    q_vec = embed(query)\n"
        "    _embed_ms = (time.time() - t0) * 1000\n"
        "    t0 = time.time()\n"
        "    policy_map = get_source_policy_map(user_id)",
        "search_knowledge: instrument embed latency",
    ),
    # 10. search_knowledge(): scan timing + quality log
    (
        "    out = []\n"
        "    for _, sim, kid, title, content, d, conf, pol, tags_str, _ in selected:",
        "    _scan_ms = (time.time() - t0) * 1000\n"
        "    _mean_sim = sum(r[1] for r in selected) / len(selected) if selected else 0.0\n"
        "    logger.info(\n"
        "        f\"search_knowledge: selected={len(selected)} embed_ms={_embed_ms:.1f} \"\n"
        "        f\"scan_ms={_scan_ms:.1f} mean_sim={_mean_sim:.3f}\")\n"
        "    out = []\n"
        "    for _, sim, kid, title, content, d, conf, pol, tags_str, _ in selected:",
        "search_knowledge: log scan latency + mean sim of injected items",
    ),
]


def main():
    if not os.path.exists(TARGET):
        print(f"ABORT — target not found: {TARGET}")
        sys.exit(3)

    with open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()

    # 1. Verify all anchors match exactly once before touching anything
    problems = []
    for old, _, desc in EDITS:
        n = src.count(old)
        if n != 1:
            problems.append(f"  {desc}\n      anchor found {n} times (expected 1)")
    if problems:
        print("ABORT — live file does not match expected v5.x. No changes written.")
        print("Offending edits:")
        print("\n".join(problems))
        print("\nIf the live memory_core.py has drifted from the v5.0 source in")
        print("/projects/memory-rebuild/, diff it and update the anchors before patching.")
        sys.exit(1)

    # 2. Timestamped backup
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = f"{TARGET}.bak.{stamp}"
    shutil.copy2(TARGET, bak)

    # 3. Apply edits
    for old, new, _ in EDITS:
        src = src.replace(old, new, 1)
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(src)

    # 4. Syntax check — restore backup on failure
    try:
        py_compile.compile(TARGET, doraise=True)
    except Exception as e:
        shutil.copy2(bak, TARGET)
        print(f"COMPILE FAILED after patching — backup restored. Error: {e}")
        sys.exit(2)

    print(f"OK — {len(EDITS)} edits applied to {TARGET}")
    print(f"Backup: {bak}")
    print()
    print("Applied changes:")
    for _, _, desc in EDITS:
        print(f"  - {desc}")
    print()
    print("Restart the container to reload the module:")
    print("  docker restart openwebui")
    print()
    print("Verify after first chat message:")
    print("  docker logs openwebui --tail 50 | grep search_")
    print("(expect: search_memories: selected=N embed_ms=... scan_ms=... mean_sim=...)")
    print()
    print(f"Rollback if needed:")
    print(f"  docker exec openwebui cp {bak} {TARGET}")
    print("  docker restart openwebui")


if __name__ == "__main__":
    main()
