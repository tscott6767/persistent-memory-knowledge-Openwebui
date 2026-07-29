"""
title: Memory Filter v5
author: household-ai
description: Auto-recall and auto-store for persistent memory — lightweight filter companion to Memory Tool v5
version: 5.0.0
"""

import os
import sys
import re
import logging

logger = logging.getLogger("memory_filter")

# ── Lazy import (same pattern as memory_tool.py) ──
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

try:
    _ensure_core()
except Exception as _e:
    logger.warning(f"memory_core not yet available at import: {_e}")


class Filter:
    def __init__(self):
        _ensure_core()

    def inlet(self, body: dict, __user__: dict = None):
        """Auto-recall: find relevant memories/knowledge and inject as context."""
        try:
            mc = _ensure_core()
            user_id = mc.get_user_id(__user__)
            if not user_id:
                return body

            messages = body.get("messages", [])
            if not messages or messages[-1].get("role") != "user":
                return body

            last_raw = messages[-1].get("content", "")
            last = mc.content_to_text(last_raw).strip()
            if not last:
                return body

            # Strip --private flag if present (prevents recall AND storage)
            query_text = re.sub(r"\s*--private\b", "", last).strip()
            if not query_text:
                return body

            conv_id = mc.get_conversation_id(body)

            # Auto-recall memories (exclude current conversation to avoid self-recall)
            mem_hits = mc.search_memories(
                query_text, user_id, mc.AUTO_RECALL_MIN_SIM,
                exclude_conversation_id=conv_id)

            # Auto-recall knowledge
            know_hits = mc.search_knowledge(query_text, user_id, mc.KNOWLEDGE_AUTO_MIN_SIM)

            # Context expansion (find related conversation)
            conv_expansion = None
            if mem_hits:
                conv_memories = mc.expand_conversation_context(query_text, user_id)
                if conv_memories:
                    conv_expansion = mc.truncate_to_budget(
                        mc.format_conversation_expansion(conv_memories),
                        mc.CONTEXT_EXPAND_TOKENS)

            # Inject context if we found anything
            if mem_hits or know_hits or conv_expansion:
                src_txt = mc.format_source_policy_summary(user_id)
                block = mc.build_context_block(
                    query_text, mem_hits, know_hits, src_txt, conv_expansion)
                self._inject_system(messages, block)
                logger.info(
                    f"Auto recall: mem={len(mem_hits)} knowledge={len(know_hits)} "
                    f"conv_expand={bool(conv_expansion)} for {user_id}")

        except Exception as e:
            logger.error(f"inlet error: {e}")
        return body

    def outlet(self, body: dict, __user__: dict = None):
        """Auto-store: save the conversation exchange as a memory."""
        try:
            mc = _ensure_core()
            user_id = mc.get_user_id(__user__)
            if not user_id:
                return body

            if mc.is_muted(user_id):
                return body

            messages = body.get("messages", [])
            if len(messages) < 2:
                return body

            # Find the latest assistant message
            asst = next(
                (m for m in reversed(messages) if m.get("role") == "assistant"), None)
            if not asst:
                return body

            asst_text = mc.extract_assistant_text(asst)
            if not asst_text:
                return body

            # Find the corresponding user message
            user = next(
                (m for m in reversed(messages) if m.get("role") == "user"), None)
            if not user:
                return body

            user_text = mc.strip_recall_block(user.get("content", "")).strip()
            if not user_text:
                return body

            # Skip if --private flag
            if "--private" in user_text.lower():
                return body

            # Skip if this looks like a tool-call result or system prompt
            if user_text.startswith("Respond with exactly the following text"):
                return body

            conv_id = mc.get_conversation_id(body)
            mc.store_memory(user_id, conv_id, user_text, asst_text)

        except Exception as e:
            logger.error(f"outlet error: {e}")
        return body

    @staticmethod
    def _inject_system(messages: list, block: str):
        """Insert a system message before the last user message."""
        sys_msg = {"role": "system", "content": block}
        messages.insert(len(messages) - 1, sys_msg)
