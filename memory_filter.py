"""
title: Memory Filter v5.1
author: household-ai
description: Auto-recall and auto-store for persistent memory — adds pattern-based tagging,
             technical content extraction, and trivial-exchange filtering.
             Safety net for models that don't proactively call memory_store.
version: 5.1.0
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


# ───────────────────────── Pattern Detection ─────────────────────────
# Patterns detect storeable technical content in assistant responses.
# When matched, the exchange gets tagged and the embedding is enhanced
# with extracted technical lines for better future searchability.

STORE_PATTERNS = {
    "infrastructure": [
        r"(?:llama-server|systemctl|docker|cmake|apt|pip|npm|npx|git\s+(?:clone|pull|push))\s+\S+",
        r"(?:--model|-m)\s+/[^\s]+\.gguf",
        r"(?:--port|-p|--ctx-size|-ngl|-c|-t)\s+\d+",
        r"\./[^\s]+/bin/[^\s]+",
        r"(?:LXC|VM|Proxmox|container)\s+\d+",
        r"(?:pct\s+(?:enter|set|create|start|stop)|qm\s+(?:start|stop|create|set))",
    ],
    "path": [
        r"/(?:opt|etc|var|usr|home|root|app|projects|models|users|mnt|srv|backups)/[^\s\"']{3,}",
    ],
    "decision": [
        r"(?:switch(?:ing|ed)?\s+to|choosing|decided\s+to|going\s+with|migrat(?:ing|ed)\s+to)\s+",
        r"(?:removed|replaced|deprecated|decommissioned|switched\s+from|wiped\s+and\s+installed)\s+",
    ],
    "config": [
        r"(?:port|ip|address|password|key|token|api[_-]?key|base[_-]?url)\s*[:=]\s*\S+",
        r"\.(?:service|conf|yaml|yml|json|env|sh|py|gguf)\b",
        r"(?:systemctl|cron|crontab|EnvironmentFile|ExecStart)\b",
    ],
    "hardware": [
        r"(?:Xeon|RTX|GPU|VRAM|RAM|NVMe|SSD|CPU|motherboard|HBA|SATA|PCIe|ZFS|mergerfs)\b",
        r"\d+\s*GB\s*(?:VRAM|RAM|SSD|NVMe|total)?",
    ],
    "fix": [
        r"(?:fixed|resolved|solved|root\s+cause|the\s+problem\s+was|the\s+fix\s+was|the\s+error\s+was)\s+",
        r"(?:error|fail|crash|oom|OOM|CUDA\s+error|broken)\s+(?:was|is|due\s+to|caused\s+by)\s+",
    ],
}

# Lines containing these patterns are extracted for enhanced embedding
_TECH_LINE_PATTERNS = [
    r"/(?:opt|etc|var|usr|home|root|app|projects|models|users|mnt|srv)/",
    r"(?:llama-server|systemctl|docker|cmake|pct|qm)\s+",
    r"(?:--port|-p|--ctx-size|-ngl|-c|-t|--host)\s+\S+",
    r"\.(?:service|conf|sh|py|gguf)\b",
    r"(?:ip\s+addr|nvidia-smi|curl\s+http|ss\s+-)",
]

# Trivial responses that don't need storage
_TRIVIAL_RESPONSES = frozenset({
    "ok", "okay", "sure", "yes", "no", "done", "thanks", "thank you",
    "got it", "understood", "will do", "sounds good", "makes sense",
    "agreed", "perfect", "great", "cool", "nice", "indeed",
    "you're welcome", "no problem", "no worries", "correct",
})


def _detect_patterns(text: str) -> set:
    """Detect storeable content patterns in text. Returns set of tag strings."""
    tags = set()
    for tag_name, patterns in STORE_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                tags.add(tag_name)
                break
    return tags


def _extract_technical_lines(text: str, max_lines: int = 5) -> str:
    """Extract key technical lines from assistant response for enhanced embedding."""
    lines = text.split("\n")
    tech_lines = []
    for line in lines:
        line = line.strip()
        if not line or len(line) > 300:
            continue
        for pat in _TECH_LINE_PATTERNS:
            if re.search(pat, line, re.IGNORECASE):
                tech_lines.append(line)
                break
        if len(tech_lines) >= max_lines:
            break
    return "\n".join(tech_lines)


def _is_trivial(user_text: str, asst_text: str) -> bool:
    """Check if exchange is too trivial to store."""
    # Never skip if patterns detected
    if _detect_patterns(asst_text):
        return False
    # Skip very short responses
    if len(asst_text) < 30:
        return True
    # Skip pure acknowledgment responses
    stripped = asst_text.lower().strip().rstrip(".!?")
    if stripped in _TRIVIAL_RESPONSES:
        return True
    # Skip if it's just a greeting + short pleasantry
    if len(asst_text) < 80 and not re.search(r"[.?]", asst_text):
        return True
    return False


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
        """Auto-store: save the conversation exchange as a memory, with pattern-based
        tagging and technical content extraction."""
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

            # ── v5.1: Skip trivial exchanges ──
            if _is_trivial(user_text, asst_text):
                return body

            # ── v5.1: Pattern detection ──
            detected_tags = _detect_patterns(asst_text)

            # ── v5.1: Enhanced storage ──
            if detected_tags:
                # Extract key technical lines for better embedding
                tech_summary = _extract_technical_lines(asst_text)
                if tech_summary:
                    # Prepend technical summary to user_text so the embedding
                    # captures the technical content (store_memory embeds the
                    # first ~500 chars of user_text + ~350 chars of asst_text)
                    tag_label = ", ".join(sorted(detected_tags))
                    enhanced_user = (
                        f"{user_text}\n\n"
                        f"[Detected: {tag_label}]\n"
                        f"Key technical content:\n{tech_summary}"
                    )
                    tag_str = ", ".join(sorted(detected_tags))
                else:
                    enhanced_user = user_text
                    tag_str = ", ".join(sorted(detected_tags))
            else:
                enhanced_user = user_text
                tag_str = None

            conv_id = mc.get_conversation_id(body)
            mc.store_memory(user_id, conv_id, enhanced_user, asst_text, tags=tag_str)

            if detected_tags:
                logger.info(
                    f"Auto-store with tags [{tag_str}] for {user_id} "
                    f"(conv {conv_id[:8]})")
            else:
                logger.info(
                    f"Auto-store (no patterns) for {user_id} "
                    f"(conv {conv_id[:8]})")

        except Exception as e:
            logger.error(f"outlet error: {e}")
        return body

    @staticmethod
    def _inject_system(messages: list, block: str):
        """Insert a system message before the last user message."""
        sys_msg = {"role": "system", "content": block}
        messages.insert(len(messages) - 1, sys_msg)
