"""
title: Memory Tool v5
author: household-ai
description: Persistent memory and knowledge management — search, store (pinned or unpinned), pin/unpin, list, delete memories, knowledge items, and source policies
version: 5.2.1
requirements: numpy,sentence-transformers,tiktoken
"""

import os
import sys
import logging

logger = logging.getLogger("memory_tool")

# ── Lazy import helper ──
# OWUI's validator exec()s this module at save time. If memory_core
# can't be imported during validation, the exec fails and OWUI reports
# "No Function class found" instead of the real ImportError. By wrapping
# the import in try/except, the class definition always survives validation.
# At runtime, _ensure_core() is called by __init__ to guarantee imports.

_mc = None

def _ensure_core():
    global _mc
    if _mc is not None:
        return _mc
    _data_path = os.environ.get("OWUI_DATA_PATH", "/app/backend/data")
    if _data_path not in sys.path:
        sys.path.insert(0, _data_path)
    import memory_core
    _mc = memory_core
    return _mc

# Try import at module level (works at runtime, silently fails during validation)
try:
    _ensure_core()
except Exception as _e:
    logger.warning(f"memory_core not yet available at import: {_e}")


class Tools:
    def __init__(self):
        _ensure_core()

    # ─────────────────────── Memory operations ───────────────────────

    def memory_search(self, query: str, __user__: dict = None) -> str:
        """
        Search your persistent memories for relevant past conversations and knowledge.
        Use this when you need to recall something the user discussed previously.
        
        Args:
            query: Natural language search query describing what you're looking for.
        Returns:
            Formatted list of matching memories with similarity scores and snippets.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        hits = mc.search_memories(query, user_id, mc.EXPLICIT_RECALL_MIN_SIM, top_k=10)
        if not hits:
            return "No matching memories found."
        lines = []
        for hit in hits:
            snip = mc.truncate_words(hit.get("summary") or hit.get("user_msg", ""), 80)
            tag_str = f" [{','.join(sorted(hit.get('tags', set())))}]" if hit.get("tags") else ""
            lines.append(f"[mem_{hit['id']}] sim={round(hit['sim'],3)}{tag_str} — {snip}")
        return "\n---\n".join(lines)

    def memory_store(self, text: str, tags: str = "", pin: bool = False, __user__: dict = None) -> str:
        """
        Store a memory. Default is UNPINNED — fully searchable, no ranking priority.
        Use pin=true ONLY for critical items that must get recall priority
        (current infrastructure state, corrections that must outrank stale
        rivals, standing preferences/decisions, active project state).
        Pinned items must still clear a 0.45 relevance floor (v5.2) —
        pinning is not an always-inject. Pin sparingly.
        
        Args:
            text: The content to store as a memory.
            tags: Comma-separated tags (e.g. "technical,reference"). Optional.
            pin: Default False (normal memory, searchable only). True = pinned
                priority memory (ranking boost, 0.45 relevance floor).
        Returns:
            Confirmation with the new memory ID.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        text = text.strip()
        if not text:
            return "Error: No text provided to store."
        try:
            # v5.2.1: single path via store_memory — tags + dedup now honored;
            # the pin flag controls priority instead of always pinning.
            mem_id = mc.store_memory(user_id, "manual", text, "",
                                     tags=tags or None, pinned=bool(pin))
            if mem_id is None:
                return "⚠️ Not stored — near-duplicate of a very recent memory (dedup)."
            if pin:
                return f"✅ Stored as PINNED memory mem_{mem_id} (recall priority). Tags: {tags or 'none'}"
            return f"✅ Stored as memory mem_{mem_id} (unpinned — searchable, no priority). Tags: {tags or 'none'}"
        except RuntimeError as e:
            return f"Error: {e}"

    def memory_get(self, id: str, __user__: dict = None) -> str:
        """
        Retrieve the full content of a specific memory by its ID (e.g. "mem_42" or "42").
        
        Args:
            id: Memory ID (e.g. "mem_42" or just "42").
        Returns:
            Full memory content including user message and assistant response.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        target = id.strip().replace("mem_", "").replace("MEM_", "").strip()
        if not target.isdigit():
            hits = mc.search_memories(target, user_id, mc.EXPLICIT_RECALL_MIN_SIM, top_k=5)
            if not hits:
                return f"No memories found matching '{id}'"
            target = str(hits[0]["id"])
        mem_id = int(target)
        with mc.db_connect() as conn:
            row = conn.execute(
                "SELECT id, user_msg, assistant_msg, tags, timestamp, "
                "conversation_id, use_count, pinned "
                "FROM memories WHERE id=? AND user_id IN (?, ?) AND deleted_at IS NULL",
                (mem_id, user_id, mc.HOUSEHOLD_USER_ID)).fetchone()
        if not row:
            return f"❌ No memory found with ID mem_{mem_id}"
        mid, user_msg, assistant_msg, tags_str, ts, conv_id, use_count, pinned = row
        lines = [f"**Memory mem_{mid}**", f"📅 {ts}"]
        if tags_str:
            lines.append(f"🏷️ Tags: {tags_str}")
        if conv_id:
            lines.append(f"💬 Conversation: {conv_id[:8]}")
        lines.append(f"📊 Recalled {use_count or 0} times")
        if pinned:
            lines.append("📌 Pinned")
        lines.append("")
        lines.append("**User:**")
        lines.append(user_msg or "(empty)")
        lines.append("")
        lines.append("**Assistant:**")
        lines.append(assistant_msg or "(empty)")
        return "\n".join(lines)

    def memory_delete(self, id: str, __user__: dict = None) -> str:
        """
        Soft-delete a memory by its ID. The memory will no longer appear in searches.
        
        Args:
            id: Memory ID (e.g. "mem_42" or "42").
        Returns:
            Confirmation of deletion.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        target = id.strip().replace("mem_", "").replace("MEM_", "").strip()
        if not target.isdigit():
            return f"Error: Invalid memory ID '{id}'. Use format like 'mem_42' or '42'."
        mem_id = int(target)
        with mc.db_connect() as conn:
            cur = conn.execute(
                "UPDATE memories SET deleted_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND user_id IN (?, ?) AND deleted_at IS NULL",
                (mem_id, user_id, mc.HOUSEHOLD_USER_ID))
            conn.commit()
        if cur.rowcount == 0:
            return f"❌ No memory with id={mem_id} found (or not yours)."
        return f"🗑️ Forgotten memory mem_{mem_id}."

    def memory_pin(self, id: str, __user__: dict = None) -> str:
        """
        Pin a memory so it gets recall priority (ranking boost + 0.45 relevance
        floor instead of the 0.55 auto-recall gate). Pin sparingly — a large
        pinned set dilutes recall quality.
        
        Args:
            id: Memory ID (e.g. "mem_42" or "42").
        Returns:
            Confirmation of pinning.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        target = id.strip().replace("mem_", "").replace("MEM_", "").strip()
        if not target.isdigit():
            return f"Error: Invalid memory ID '{id}'. Use format like 'mem_42' or '42'."
        mem_id = int(target)
        with mc.db_connect() as conn:
            cur = conn.execute(
                "UPDATE memories SET pinned=1 "
                "WHERE id=? AND user_id IN (?, ?) AND deleted_at IS NULL",
                (mem_id, user_id, mc.HOUSEHOLD_USER_ID))
            conn.commit()
        if cur.rowcount == 0:
            return f"❌ No memory with id={mem_id} found (or not yours)."
        return f"📌 Pinned mem_{mem_id} — gets recall priority when relevant (≥0.45 floor)."

    def memory_unpin(self, id: str, __user__: dict = None) -> str:
        """
        Remove pin priority from a memory. It remains fully searchable —
        unpinned items surface on any query above the normal relevance gates
        (0.55 auto-recall / 0.35 explicit search). Unpinned, unused items become
        eligible for the 90-day prune (manual tool, never automatic).
        
        Args:
            id: Memory ID (e.g. "mem_42" or "42").
        Returns:
            Confirmation of unpinning.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        target = id.strip().replace("mem_", "").replace("MEM_", "").strip()
        if not target.isdigit():
            return f"Error: Invalid memory ID '{id}'. Use format like 'mem_42' or '42'."
        mem_id = int(target)
        with mc.db_connect() as conn:
            cur = conn.execute(
                "UPDATE memories SET pinned=0 "
                "WHERE id=? AND user_id IN (?, ?) AND deleted_at IS NULL",
                (mem_id, user_id, mc.HOUSEHOLD_USER_ID))
            conn.commit()
        if cur.rowcount == 0:
            return f"❌ No memory with id={mem_id} found (or not yours)."
        return f"⬆️ Unpinned mem_{mem_id} — still searchable, no priority; prune-eligible if unused 90+ days."

    def memory_list(self, pinned_only: bool = False, __user__: dict = None) -> str:
        """
        List stored memories. Shows the 20 most recent memories.
        
        Args:
            pinned_only: If true, show only pinned memories. Default false.
        Returns:
            Formatted list of memories with IDs, timestamps, and snippets.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        with mc.db_connect() as conn:
            if pinned_only:
                rows = conn.execute(
                    "SELECT id, substr(summary,1,120), timestamp, user_id FROM memories "
                    "WHERE user_id IN (?, ?) AND deleted_at IS NULL AND pinned = 1 "
                    "ORDER BY id DESC LIMIT 20",
                    (user_id, mc.HOUSEHOLD_USER_ID)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, substr(summary,1,120), pinned, timestamp, user_id "
                    "FROM memories WHERE user_id IN (?, ?) AND deleted_at IS NULL "
                    "ORDER BY pinned DESC, id DESC LIMIT 20",
                    (user_id, mc.HOUSEHOLD_USER_ID)).fetchall()
        if not rows:
            return "No memories found."
        lines = []
        if pinned_only:
            for mid, snip, ts, owner in rows:
                badge = " 🏠" if owner == mc.HOUSEHOLD_USER_ID else ""
                lines.append(f"[{mid}]{badge} {ts} — {snip}…")
        else:
            for mid, snip, p, ts, owner in rows:
                pin = " 📌" if p else ""
                badge = " 🏠" if owner == mc.HOUSEHOLD_USER_ID else ""
                lines.append(f"[{mid}]{pin}{badge} {ts} — {snip}…")
        return "\n".join(lines)

    def memory_stats(self, __user__: dict = None) -> str:
        """
        Get statistics about stored memories and knowledge items.
        
        Returns:
            Summary counts for personal memories, household memories, and knowledge items.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        with mc.db_connect() as conn:
            personal = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(pinned),0), COALESCE(SUM(use_count),0) "
                "FROM memories WHERE user_id=? AND deleted_at IS NULL",
                (user_id,)).fetchone()
            household = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id=? AND deleted_at IS NULL",
                (mc.HOUSEHOLD_USER_ID,)).fetchone()
            knowledge = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(pinned),0), COALESCE(SUM(use_count),0) "
                "FROM knowledge_items WHERE owner_user_id IN (?, ?, ?) AND deleted_at IS NULL",
                (user_id, mc.HOUSEHOLD_USER_ID, mc.GLOBAL_OWNER_ID)).fetchone()
        return (
            f"Memory — Personal: {personal[0]} total, {personal[1]} pinned, {personal[2]} recalls. "
            f"Household: {household[0]}. Knowledge: {knowledge[0]} total, {knowledge[1]} pinned, {knowledge[2]} recalls."
        )

    def memory_transcript(self, id: str, __user__: dict = None) -> str:
        """
        Get the full conversation transcript for a memory. Shows all messages from
        the same conversation as the specified memory.
        
        Args:
            id: Memory ID (e.g. "mem_42" or "42").
        Returns:
            Full conversation transcript with all messages.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        target = id.strip().replace("mem_", "").replace("MEM_", "").strip()
        if not target.isdigit():
            return f"Error: Invalid memory ID '{id}'."
        mid = int(target)
        with mc.db_connect() as conn:
            row = conn.execute(
                "SELECT conversation_id FROM memories WHERE id=? AND user_id IN (?, ?) AND deleted_at IS NULL",
                (mid, user_id, mc.HOUSEHOLD_USER_ID)).fetchone()
        if not row or not row[0]:
            return f"No conversation found for memory id={mid}."
        conv_id = row[0]
        with mc.db_connect() as conn:
            rows = conn.execute(
                "SELECT id, user_msg, assistant_msg, timestamp FROM memories "
                "WHERE conversation_id=? AND user_id IN (?, ?) AND deleted_at IS NULL "
                "ORDER BY id ASC LIMIT 30",
                (conv_id, user_id, mc.HOUSEHOLD_USER_ID)).fetchall()
        if not rows:
            return f"No memories found for conversation {conv_id[:8]}."
        lines = [f"Conversation {conv_id[:8]} — {len(rows)} messages:"]
        for mid, user_msg, asst_msg, ts in rows:
            u = mc.truncate_words(user_msg or "(empty)", 60)
            a = mc.truncate_words(asst_msg or "(empty)", 100)
            lines.append(f"\n[{mid}] {ts}\n  User: {u}\n  Assistant: {a}")
        return "\n".join(lines)

    def memory_prune(self, __user__: dict = None) -> str:
        """
        Prune stale, unpinned memories older than 90 days with zero recalls.
        Use sparingly — this permanently removes old unused memories.
        
        Returns:
            Count of pruned memories.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        with mc.db_connect() as conn:
            cur = conn.execute(
                f"UPDATE memories SET deleted_at=CURRENT_TIMESTAMP "
                f"WHERE user_id=? AND deleted_at IS NULL AND pinned=0 "
                f"AND use_count<=? AND timestamp < datetime('now', '-{mc.PRUNE_AGE_DAYS} days')",
                (user_id, mc.PRUNE_MAX_USE_COUNT))
            conn.commit()
            return f"Pruned {cur.rowcount} stale memories."

    def memory_mute(self, __user__: dict = None) -> str:
        """Temporarily disable automatic memory storage for the current session."""
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        mc.mute_user(user_id)
        return "🔇 Memory storage muted for this session."

    def memory_unmute(self, __user__: dict = None) -> str:
        """Re-enable automatic memory storage if it was muted."""
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        mc.unmute_user(user_id)
        return "🔊 Memory storage resumed."

    # ─────────────────────── Tag management ───────────────────────

    def memory_tag_add(self, tag: str, __user__: dict = None) -> str:
        """
        Add a tag to the full-detail list. Memories/knowledge with full-detail tags
        always return their complete text on recall, not just snippets.
        
        Args:
            tag: Tag name to add (e.g. "medical", "legal").
        Returns:
            Confirmation and current full-detail tag list.
        """
        mc = _ensure_core()
        tag = tag.strip().lower()
        if not tag:
            return "Error: No tag provided."
        tags = mc.add_full_detail_tag(tag)
        return f"✅ Added `{tag}` to full-detail tags.\nCurrent: {', '.join(sorted(tags))}"

    def memory_tag_remove(self, tag: str, __user__: dict = None) -> str:
        """
        Remove a tag from the full-detail list.
        
        Args:
            tag: Tag name to remove.
        Returns:
            Confirmation and remaining full-detail tag list.
        """
        mc = _ensure_core()
        tag = tag.strip().lower()
        if not tag:
            return "Error: No tag provided."
        tags = mc.remove_full_detail_tag(tag)
        return f"🗑️ Removed `{tag}` from full-detail tags.\nCurrent: {', '.join(sorted(tags)) if tags else '(none)'}"

    def memory_tag_list(self, __user__: dict = None) -> str:
        """List all full-detail tags (tags that always return full text on recall)."""
        mc = _ensure_core()
        tags = mc.load_full_detail_tags()
        return f"📋 Full-detail tags:\n{', '.join(sorted(tags)) if tags else '(none)'}"

    # ─────────────────────── Knowledge operations ───────────────────────

    def knowledge_add(self, content: str, source: str = "", confidence: float = 0.7,
                      scope: str = "user", tags: str = "", title: str = "",
                      __user__: dict = None) -> str:
        """
        Store a knowledge item — a durable fact, reference, or instruction.
        Unlike auto-stored memories, knowledge items are explicitly curated.
        
        Args:
            content: The knowledge content to store.
            source: URL or domain name where this info came from. Optional.
            confidence: Confidence level 0.0-1.0. Default 0.7.
            scope: Visibility — "user" (personal), "household", or "global". Default "user".
            tags: Comma-separated tags (e.g. "technical,reference"). Optional.
            title: Short title for the knowledge item. Optional.
        Returns:
            Confirmation with the new knowledge ID.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        content = content.strip()
        if not content:
            return "Error: No content provided."
        try:
            kid = mc.store_knowledge(
                user_id, content,
                source=source.strip(),
                confidence=confidence,
                scope=scope.strip().lower(),
                tags=tags.strip() or None,
                title=title.strip() or None)
        except (ValueError, RuntimeError) as e:
            return f"Error: {e}"
        owner = mc.scope_to_owner(scope, user_id)
        owner_label = ("🏠 household" if owner == mc.HOUSEHOLD_USER_ID
                       else ("🌐 global" if owner == mc.GLOBAL_OWNER_ID else "personal"))
        src_label = mc.normalize_domain(source) if source else "no-source"
        tag_info = f", tags={tags}" if tags else ""
        return (f"✅ Stored as knowledge id={kid} ({owner_label}, "
                f"conf={round(confidence,2)}, source={src_label}{tag_info}, len={len(content)}).\n"
                f"Retrieve with: knowledge_show(id={kid})")

    def knowledge_search(self, query: str, __user__: dict = None) -> str:
        """
        Search knowledge items by semantic similarity. Finds facts, references,
        and instructions you've previously stored.
        
        Args:
            query: Natural language search query.
        Returns:
            Formatted list of matching knowledge items with similarity scores.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        hits = mc.search_knowledge(query, user_id, mc.KNOWLEDGE_EXPLICIT_MIN_SIM, top_k=10)
        if not hits:
            return "No matching knowledge items found."
        lines = []
        for h in hits:
            lines.append(
                f"[{h['id']}] sim={round(h['sim'],3)} conf={round(h['confidence'],2)} "
                f"src={h['domain'] or 'n/a'} — {mc.truncate_words(h['content'], mc.KNOWLEDGE_SEARCH_WORDS)}")
        return "\n".join(lines)

    def knowledge_list(self, query: str = "", __user__: dict = None) -> str:
        """
        List knowledge items. With no query, shows the 20 most recent.
        With a query, searches for matching items.
        
        Args:
            query: Optional search query to filter knowledge items.
        Returns:
            Formatted list of knowledge items with IDs, sources, and snippets.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        query = (query or "").strip()
        if not query:
            with mc.db_connect() as conn:
                rows = conn.execute(
                    f"SELECT id, substr(content,1,{mc.LIST_SNIPPET_CHARS}), source_domain, "
                    f"confidence, pinned, owner_user_id, timestamp, LENGTH(content) "
                    "FROM knowledge_items WHERE owner_user_id IN (?, ?, ?) AND deleted_at IS NULL "
                    "ORDER BY pinned DESC, id DESC LIMIT 20",
                    (user_id, mc.HOUSEHOLD_USER_ID, mc.GLOBAL_OWNER_ID)).fetchall()
            if not rows:
                return "No knowledge items stored."
            out_lines = []
            for kid, snip, dom, conf, p, owner, ts, full_len in rows:
                badge = (" 🏠" if owner == mc.HOUSEHOLD_USER_ID
                         else (" 🌐" if owner == mc.GLOBAL_OWNER_ID else ""))
                pin = " 📌" if p else ""
                more = "…" if full_len and full_len > mc.LIST_SNIPPET_CHARS else ""
                out_lines.append(
                    f"[{kid}]{pin}{badge} {ts} — conf={round(conf or 0.0,2)} "
                    f"src={dom or 'n/a'} len={full_len} — {snip}{more}")
            return "\n".join(out_lines)
        hits = mc.search_knowledge(query, user_id, min_sim=0.0, top_k=20)
        if not hits:
            return "No matching knowledge items."
        lines = []
        for h in hits:
            lines.append(
                f"[{h['id']}] sim={round(h['sim'],3)} conf={round(h['confidence'],2)} "
                f"src={h['domain'] or 'n/a'} — {mc.truncate_words(h['content'], mc.KNOWLEDGE_SEARCH_WORDS)}")
        return "\n".join(lines)

    def knowledge_show(self, id: str, __user__: dict = None) -> str:
        """
        Retrieve the full content of a specific knowledge item by its ID.
        
        Args:
            id: Knowledge item ID (numeric).
        Returns:
            Full knowledge item content with all metadata.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        try:
            kid = int(id.strip())
        except ValueError:
            return f"Error: Invalid knowledge ID '{id}'."
        with mc.db_connect() as conn:
            row = conn.execute(
                "SELECT id, owner_user_id, scope, title, content, source_url, "
                "source_domain, confidence, tags, pinned, timestamp, LENGTH(content) "
                "FROM knowledge_items WHERE id=? AND owner_user_id IN (?, ?, ?) AND deleted_at IS NULL",
                (kid, user_id, mc.HOUSEHOLD_USER_ID, mc.GLOBAL_OWNER_ID)).fetchone()
        if not row:
            return f"❌ No knowledge item with id={kid} (or not accessible)."
        (kid, owner, scope, title, content, src_url, src_dom, conf,
         tags, pinned, ts, content_len) = row
        header = (
            f"id={kid} scope={scope} owner={owner} pinned={bool(pinned)}\n"
            f"title={title or '(none)'}\n"
            f"source_url={src_url or '(none)'} | source_domain={src_dom or '(none)'}\n"
            f"confidence={conf} tags={tags or '(none)'}\n"
            f"timestamp={ts} content_len={content_len}\n--- CONTENT ---\n"
        )
        return header + (content or "")

    def knowledge_delete(self, id: str, __user__: dict = None) -> str:
        """
        Soft-delete a knowledge item by its ID.
        
        Args:
            id: Knowledge item ID (numeric).
        Returns:
            Confirmation of deletion.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        try:
            kid = int(id.strip())
        except ValueError:
            return f"Error: Invalid knowledge ID '{id}'."
        with mc.db_connect() as conn:
            cur = conn.execute(
                "UPDATE knowledge_items SET deleted_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND owner_user_id IN (?, ?, ?) AND deleted_at IS NULL",
                (kid, user_id, mc.HOUSEHOLD_USER_ID, mc.GLOBAL_OWNER_ID))
            conn.commit()
        if cur.rowcount == 0:
            return f"❌ No knowledge item with id={kid} found (or not yours)."
        return f"🗑️ Forgotten knowledge id={kid}."

    def knowledge_pin(self, id: str, __user__: dict = None) -> str:
        """
        Pin a knowledge item so it gets priority in recall results.
        
        Args:
            id: Knowledge item ID (numeric).
        Returns:
            Confirmation of pinning.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        try:
            kid = int(id.strip())
        except ValueError:
            return f"Error: Invalid knowledge ID '{id}'."
        with mc.db_connect() as conn:
            cur = conn.execute(
                "UPDATE knowledge_items SET pinned=1 "
                "WHERE id=? AND owner_user_id IN (?, ?, ?) AND deleted_at IS NULL",
                (kid, user_id, mc.HOUSEHOLD_USER_ID, mc.GLOBAL_OWNER_ID))
            conn.commit()
        if cur.rowcount == 0:
            return f"❌ No knowledge item with id={kid} (or not yours)."
        return f"📌 Pinned knowledge id={kid}."

    # ─────────────────────── Source policy ───────────────────────

    def source_policy_set(self, domain: str, policy: str, scope: str = "user",
                          __user__: dict = None) -> str:
        """
        Set a source policy for a domain — controls how content from that domain
        is treated in knowledge recall.
        
        Args:
            domain: Domain name (e.g. "wikipedia.org") or full URL.
            policy: One of "prefer", "unreliable", or "block".
            scope: Visibility — "user", "household", or "global". Default "user".
        Returns:
            Confirmation of the policy setting.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        domain_clean = mc.normalize_domain(domain)
        if not domain_clean:
            return f"Error: Invalid domain '{domain}'."
        policy = policy.strip().lower()
        if policy not in {"prefer", "unreliable", "block"}:
            return f"Error: Policy must be 'prefer', 'unreliable', or 'block'. Got '{policy}'."
        scope = scope.strip().lower()
        if scope not in {"user", "household", "global"}:
            scope = "user"
        owner = mc.scope_to_owner(scope, user_id)
        mc.upsert_source_policy(owner, domain_clean, policy)
        scope_lbl = ("🏠 household" if owner == mc.HOUSEHOLD_USER_ID
                     else ("🌐 global" if owner == mc.GLOBAL_OWNER_ID else "personal"))
        return f"✅ Source policy set: {domain_clean} → {policy} ({scope_lbl})."

    def source_policy_remove(self, domain: str, scope: str = "user",
                             __user__: dict = None) -> str:
        """
        Remove a source policy for a domain.
        
        Args:
            domain: Domain name to remove policy for.
            scope: Which scope to remove from — "user", "household", or "global".
        Returns:
            Confirmation of removal.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        domain_clean = mc.normalize_domain(domain)
        if not domain_clean:
            return f"Error: Invalid domain '{domain}'."
        scope = scope.strip().lower()
        if scope not in {"user", "household", "global"}:
            scope = "user"
        owner = mc.scope_to_owner(scope, user_id)
        with mc.db_connect() as conn:
            cur = conn.execute(
                "DELETE FROM source_policies WHERE owner_user_id=? AND domain=?",
                (owner, domain_clean))
            conn.commit()
        return f"Removed {cur.rowcount} source policy for {domain_clean}."

    def source_policy_list(self, __user__: dict = None) -> str:
        """
        List all source policies (prefer, unreliable, block) across all scopes.
        
        Returns:
            Formatted list of all source policies.
        """
        mc = _ensure_core()
        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return "Error: Could not identify user."
        owners = (user_id, mc.HOUSEHOLD_USER_ID, mc.GLOBAL_OWNER_ID)
        with mc.db_connect() as conn:
            rows = conn.execute(
                "SELECT owner_user_id, domain, policy, updated_at FROM source_policies "
                "WHERE owner_user_id IN (?, ?, ?) ORDER BY owner_user_id, policy, domain",
                owners).fetchall()
        if not rows:
            return "No source policies set."
        lines = []
        for owner, domain, policy, ts in rows:
            badge = ("🏠" if owner == mc.HOUSEHOLD_USER_ID
                     else ("🌐" if owner == mc.GLOBAL_OWNER_ID else "👤"))
            lines.append(f"{badge} {mc.normalize_domain(domain)} → {policy} ({ts})")
        return "\n".join(lines)
