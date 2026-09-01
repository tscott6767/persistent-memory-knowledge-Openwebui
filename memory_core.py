# memory_core.py — Shared backend for OWUI memory filter + tool
# Rebuild v5.0 (2025-07-14)
#
# This module is imported by both the lightweight Filter (auto-recall + auto-store)
# and the model-callable Tool (all memory/knowledge/source operations).
#
# Design principles:
#   - No command parsing here — the Tool handles that via method signatures
#   - Graceful embedding fallback (keyword search if model unavailable)
#   - Actual token counting via tiktoken if available, else char/4 approximation
#   - Adaptive dedup thresholds based on corpus size
#   - All functions are sync (OWUI runs them in async wrappers)

import os
import re
import json
import time
import sqlite3
import hashlib
import logging
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Set

import numpy as np
from numpy.linalg import norm

logger = logging.getLogger("memory_core")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")

# ───────────────────────── Config ─────────────────────────
BASE_PATH = os.environ.get("OWUI_DATA_PATH", "/app/backend/data")
DB_PATH = os.path.join(BASE_PATH, "memories.db")
VAULT_DIR = os.path.join(BASE_PATH, "ObsidianVault/Memories")
os.makedirs(VAULT_DIR, exist_ok=True)

EMBED_MODEL_NAME = "BAAI/bge-m3"
EMBED_DIM = 1024

# Recall thresholds
AUTO_RECALL_MIN_SIM = 0.55
EXPLICIT_RECALL_MIN_SIM = 0.35
KNOWLEDGE_AUTO_MIN_SIM = 0.50
KNOWLEDGE_EXPLICIT_MIN_SIM = 0.30

# Limits
MAX_ITEMS = 3
MAX_KNOWLEDGE_ITEMS = 4
MAX_WORDS_PER_SUMMARY = 300
MAX_WORDS_PER_KNOWLEDGE = 220
RECALL_TOKEN_BUDGET = 2500
RECENCY_HALFLIFE_DAYS = 30
DEDUP_THRESHOLD = 0.88
SEARCH_DEDUP_THRESHOLD = 0.93

MAX_KNOWLEDGE_CHARS = 200_000
LIST_SNIPPET_CHARS = 400
KNOWLEDGE_SEARCH_WORDS = 120

MAX_EMBED_USER_CHARS = 500
MAX_EMBED_ASST_CHARS = 350

CONTEXT_EXPAND_MIN_SIM = 0.72
CONTEXT_EXPAND_MAX_MSGS = 5
CONTEXT_EXPAND_TOKENS = 600

# Full-detail tags (always return full text on recall)
DEFAULT_FULL_DETAIL_TAGS = {"recipe", "technical", "medical", "reference"}
FULL_TEXT_SIM_THRESHOLD = 0.80
# v5.2: pinned items must still clear a relevance floor to be auto-recalled
PINNED_MIN_SIM = 0.45
RECALL_BOOST_FACTOR = 0.01
TIME_DECAY_HALFLIFE_DAYS = 30
TIME_DECAY_MAX_PENALTY = 0.10
CACHE_TTL = 60

RECALL_BLOCK_MARKER = "Contextual Notes from previous discussion:"
HOUSEHOLD_USER_ID = "household"
HOUSEHOLD_MEMBER_IDS = {
    "REMOVED-HOUSEHOLD-UUID",  # Tony
    "REMOVED-HOUSEHOLD-UUID",  # Maria
}

def is_household_member(user_id: str) -> bool:
    return user_id in HOUSEHOLD_MEMBER_IDS

def get_household_id(user_id: str) -> str:
    """Returns 'household' for members, phantom ID for non-members."""
    if is_household_member(user_id):
        return HOUSEHOLD_USER_ID
    return "__no_household_access__"
GLOBAL_OWNER_ID = "global"

PRUNE_AGE_DAYS = 90
PRUNE_MAX_USE_COUNT = 0
SCHEMA_VERSION = 4

ALLOWED_KV_KEYS = {"source", "confidence", "scope", "tags", "title", "note"}

# ───────────────────────── Token counting ─────────────────────────
_tiktoken_enc = None
_tiktoken_tried = False

def _get_tiktoken():
    global _tiktoken_enc, _tiktoken_tried
    if _tiktoken_tried:
        return _tiktoken_enc
    _tiktoken_tried = True
    try:
        import tiktoken
        _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        logger.info("tiktoken loaded for accurate token counting")
    except Exception as e:
        logger.info(f"tiktoken unavailable, using char/4 approximation: {e}")
        _tiktoken_enc = None
    return _tiktoken_enc

def count_tokens(text: str) -> int:
    enc = _get_tiktoken()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)

def truncate_to_budget(text: str, max_tokens: int) -> str:
    if count_tokens(text) <= max_tokens:
        return text
    enc = _get_tiktoken()
    if enc is not None:
        try:
            tokens = enc.encode(text)
            truncated = enc.decode(tokens[:max_tokens])
            return truncated + " … [truncated]"
        except Exception:
            pass
    # Fallback: char-based
    char_budget = int(max_tokens * 4 * 0.9)
    clipped = text[:char_budget]
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped + " … [truncated]"

# ───────────────────────── Lazy embedding model ─────────────────────────
_EMB_MODEL = None
_EMB_LAST_FAIL_AT = None
_EMB_RETRY_AFTER_SEC = 60

def get_embedder():
    global _EMB_MODEL, _EMB_LAST_FAIL_AT
    if _EMB_MODEL is not None:
        return _EMB_MODEL if _EMB_MODEL is not False else None
    if _EMB_LAST_FAIL_AT is not None:
        elapsed = (datetime.now(timezone.utc) - _EMB_LAST_FAIL_AT).total_seconds()
        if elapsed < _EMB_RETRY_AFTER_SEC:
            return None
    try:
        from sentence_transformers import SentenceTransformer
        _EMB_MODEL = SentenceTransformer(EMBED_MODEL_NAME)
        logger.info(f"Loaded embedding model {EMBED_MODEL_NAME}")
        _EMB_LAST_FAIL_AT = None
        return _EMB_MODEL
    except Exception as e:
        logger.error(f"Failed to load embedding model: {e}")
        _EMB_MODEL = None
        _EMB_LAST_FAIL_AT = datetime.now(timezone.utc)
        return None

def embed(text: str):
    m = get_embedder()
    if m is None:
        return None
    v = m.encode(text, normalize_embeddings=False).astype(np.float32)
    n = norm(v)
    return v / n if n > 0 else v

# ───────────────────────── DB ─────────────────────────
def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    try:
        with db_connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                    conversation_id TEXT, user_msg TEXT, assistant_msg TEXT,
                    summary TEXT, embedding BLOB, embedding_model TEXT,
                    embedding_dim INTEGER, pinned INTEGER DEFAULT 0,
                    use_count INTEGER DEFAULT 0, last_used_at DATETIME,
                    deleted_at DATETIME, tags TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user ON memories(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_active ON memories(user_id, deleted_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conv ON memories(conversation_id)")
            # Ensure tags column exists
            cols = [r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()]
            if "tags" not in cols:
                conn.execute("ALTER TABLE memories ADD COLUMN tags TEXT")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, owner_user_id TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'user', title TEXT, content TEXT NOT NULL,
                    source_url TEXT, source_domain TEXT, confidence REAL DEFAULT 0.7,
                    tags TEXT, embedding BLOB, embedding_model TEXT, embedding_dim INTEGER,
                    pinned INTEGER DEFAULT 0, use_count INTEGER DEFAULT 0,
                    last_used_at DATETIME, deleted_at DATETIME,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_owner_active ON knowledge_items(owner_user_id, deleted_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_scope_active ON knowledge_items(scope, deleted_at)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS source_policies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, owner_user_id TEXT NOT NULL,
                    domain TEXT NOT NULL, policy TEXT NOT NULL, note TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(owner_user_id, domain)
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source_owner_domain ON source_policies(owner_user_id, domain)")
            conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                         ["full_detail_tags", json.dumps(sorted(DEFAULT_FULL_DETAIL_TAGS))])
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current = row[0] if row and row[0] is not None else 0
            if current < SCHEMA_VERSION:
                conn.execute("INSERT INTO schema_version(version) VALUES(?)", (SCHEMA_VERSION,))
            conn.commit()
    except Exception as e:
        logger.error(f"init_db failed: {e}")

# Initialize on import
init_db()

# ───────────────────────── Utilities ─────────────────────────
def content_to_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            txt = content_to_text(item)
            if txt.strip():
                parts.append(txt)
        return "\n".join(parts)
    if isinstance(content, dict):
        for key in ("text", "content", "value", "data", "body", "message"):
            val = content.get(key)
            if val is not None:
                txt = content_to_text(val)
                if txt.strip():
                    return txt
        for val in content.values():
            txt = content_to_text(val)
            if txt.strip():
                return txt
    return str(content) if content else ""

def extract_assistant_text(msg: dict) -> str:
    if not isinstance(msg, dict):
        return ""
    text = content_to_text(msg.get("content")).strip()
    if text:
        return text
    text = content_to_text(msg.get("output")).strip()
    if text:
        return text
    info = msg.get("info")
    if isinstance(info, dict):
        text = content_to_text(info.get("content")).strip()
        if text:
            return text
        text = content_to_text(info.get("output")).strip()
        if text:
            return text
    return ""

def truncate_words(text: str, max_words: int) -> str:
    w = text.split()
    if len(w) <= max_words:
        return text
    return " ".join(w[:max_words]) + " … [truncated]"

def truncate_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped + " … [truncated]"

def strip_recall_block(content) -> str:
    text = content_to_text(content)
    if RECALL_BLOCK_MARKER in text:
        m = re.search(r"User's Current Question:\s*\n(.*)$", text, re.S)
        if m:
            return m.group(1).strip()
    return text

def recency_weight(timestamp_str: str) -> float:
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        return 0.5 ** (age_days / RECENCY_HALFLIFE_DAYS)
    except Exception:
        return 1.0

def get_user_id(__user__):
    if not __user__ or "id" not in __user__:
        return None
    return __user__["id"]

def get_conversation_id(body: dict) -> str:
    return body.get("chat_id") or body.get("id") or "unknown"

def normalize_domain(value: str) -> str:
    if not value:
        return ""
    value = value.strip().lower()
    if not value:
        return ""
    if "://" in value:
        try:
            host = urlparse(value).netloc.lower()
        except Exception:
            host = value
    else:
        host = value.split("/")[0].lower()
    host = host.split("@")[-1]
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host.strip(".")

def parse_tags(tags_str) -> set:
    if not tags_str:
        return set()
    return {t.strip().lower() for t in tags_str.split(",") if t.strip()}

def scope_to_owner(scope: str, user_id: str) -> str:
    s = (scope or "user").strip().lower()
    if s == "household":
        if not is_household_member(user_id):
            return user_id  # Non-members fall back to personal scope
        return HOUSEHOLD_USER_ID
    if s == "global":
        return GLOBAL_OWNER_ID
    return user_id

# ───────────────────────── Tag config ─────────────────────────
_full_detail_tags_cache = None
_full_detail_tags_loaded_at = 0

def load_full_detail_tags() -> set:
    global _full_detail_tags_cache, _full_detail_tags_loaded_at
    now = time.time()
    if _full_detail_tags_cache is not None and (now - _full_detail_tags_loaded_at) <= CACHE_TTL:
        return _full_detail_tags_cache
    try:
        with db_connect() as conn:
            row = conn.execute("SELECT value FROM config WHERE key = 'full_detail_tags'").fetchone()
        if row:
            _full_detail_tags_cache = set(json.loads(row[0]))
        else:
            _full_detail_tags_cache = DEFAULT_FULL_DETAIL_TAGS.copy()
            with db_connect() as conn:
                conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                             ["full_detail_tags", json.dumps(sorted(DEFAULT_FULL_DETAIL_TAGS))])
                conn.commit()
    except Exception as e:
        logger.warning(f"Failed to load full-detail tags: {e}")
        _full_detail_tags_cache = DEFAULT_FULL_DETAIL_TAGS.copy()
    _full_detail_tags_loaded_at = now
    return _full_detail_tags_cache

def add_full_detail_tag(tag: str) -> set:
    global _full_detail_tags_loaded_at
    tags = load_full_detail_tags()
    tag = tag.lower().strip()
    tags.add(tag)
    with db_connect() as conn:
        conn.execute("UPDATE config SET value = ? WHERE key = 'full_detail_tags'",
                     [json.dumps(sorted(tags))])
        conn.commit()
    _full_detail_tags_loaded_at = 0
    return tags

def remove_full_detail_tag(tag: str) -> set:
    global _full_detail_tags_loaded_at
    tags = load_full_detail_tags()
    tag = tag.lower().strip()
    tags.discard(tag)
    with db_connect() as conn:
        conn.execute("UPDATE config SET value = ? WHERE key = 'full_detail_tags'",
                     [json.dumps(sorted(tags))])
        conn.commit()
    _full_detail_tags_loaded_at = 0
    return tags

# ───────────────────────── Time decay ─────────────────────────
def time_decay(mem_tags: set, pinned: bool, timestamp_str: str, full_detail_tags: set) -> float:
    if mem_tags & full_detail_tags:
        return 0.0
    if pinned:
        return 0.0
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        return -TIME_DECAY_MAX_PENALTY * (age_days / TIME_DECAY_HALFLIFE_DAYS)
    except Exception:
        return 0.0

# ───────────────────────── Adaptive dedup ─────────────────────────
def _get_adaptive_dedup_threshold() -> float:
    """Adjust dedup threshold based on corpus size to avoid over/under-dedup."""
    try:
        with db_connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL"
            ).fetchone()[0]
        if count > 5000:
            return min(0.95, DEDUP_THRESHOLD + 0.03)
        elif count < 500:
            return max(0.82, DEDUP_THRESHOLD - 0.03)
        return DEDUP_THRESHOLD
    except Exception:
        return DEDUP_THRESHOLD

def _get_adaptive_search_dedup_threshold() -> float:
    try:
        with db_connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL"
            ).fetchone()[0]
        if count > 5000:
            return min(0.96, SEARCH_DEDUP_THRESHOLD + 0.02)
        elif count < 500:
            return max(0.88, SEARCH_DEDUP_THRESHOLD - 0.03)
        return SEARCH_DEDUP_THRESHOLD
    except Exception:
        return SEARCH_DEDUP_THRESHOLD

# ───────────────────────── Memory search ─────────────────────────
def search_memories(query: str, user_id: str, min_sim: float,
                    top_k: int = MAX_ITEMS,
                    exclude_conversation_id: str = None) -> list:
    t0 = time.time()
    q_vec = embed(query)
    if q_vec is None:
        return []
    _embed_ms = (time.time() - t0) * 1000
    t0 = time.time()
    full_detail_tags = load_full_detail_tags()
    search_dedup = _get_adaptive_search_dedup_threshold()
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT id, summary, embedding, timestamp, pinned, user_id, "
            "user_msg, assistant_msg, tags, conversation_id, use_count "
            "FROM memories WHERE user_id IN (?, ?) AND deleted_at IS NULL "
            "AND embedding_model=? AND embedding_dim=?",
            (user_id, get_household_id(user_id), EMBED_MODEL_NAME, EMBED_DIM),
        ).fetchall()
    if not rows:
        return []
    scored = []
    for (mid, summary, blob, ts, pinned, owner, user_msg, assistant_msg,
         tags_str, conv_id, use_count) in rows:
        if not blob:
            continue
        if exclude_conversation_id and conv_id == exclude_conversation_id:
            continue
        try:
            emb = np.frombuffer(blob, dtype=np.float32)
            if emb.shape[0] != EMBED_DIM:
                continue
        except Exception:
            continue
        sim = float(np.dot(q_vec, emb))
        mem_tags = parse_tags(tags_str)
        effective = sim
        effective += min(use_count or 0, 10) * RECALL_BOOST_FACTOR
        effective += time_decay(mem_tags, pinned, ts, full_detail_tags)
        weighted = effective * recency_weight(ts)
        if pinned:
            weighted += 0.15
        if owner == HOUSEHOLD_USER_ID:
            weighted += 0.05
        if sim >= min_sim or (pinned and sim >= PINNED_MIN_SIM):
            scored.append({
                "id": mid, "sim": sim, "effective": effective, "weighted": weighted,
                "summary": summary or "", "user_msg": user_msg or "",
                "assistant_msg": assistant_msg or "", "tags": mem_tags,
                "conversation_id": conv_id or "", "recall_count": use_count or 0,
                "pinned": bool(pinned), "timestamp": ts, "embedding": emb,
            })
    scored.sort(key=lambda x: x["weighted"], reverse=True)
    selected = []
    for hit in scored:
        if len(selected) >= top_k:
            break
        dup = False
        for prev in selected:
            if float(np.dot(hit["embedding"], prev["embedding"])) > search_dedup:
                dup = True
                break
        if not dup:
            selected.append(hit)
    # Update use counts
    selected_ids = [h["id"] for h in selected]
    if selected_ids:
        try:
            with db_connect() as conn:
                conn.executemany(
                    "UPDATE memories SET use_count=use_count+1, last_used_at=CURRENT_TIMESTAMP WHERE id=?",
                    [(i,) for i in selected_ids])
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to update memory use stats: {e}")
    for hit in selected:
        del hit["embedding"]
    _scan_ms = (time.time() - t0) * 1000
    _mean_sim = sum(h["sim"] for h in selected) / len(selected) if selected else 0.0
    logger.info(
        f"search_memories: selected={len(selected)} embed_ms={_embed_ms:.1f} "
        f"scan_ms={_scan_ms:.1f} mean_sim={_mean_sim:.3f}")
    return selected

# ───────────────────────── Conversation context expansion ─────────────────────────
def expand_conversation_context(query: str, user_id: str,
                                 min_sim: float = CONTEXT_EXPAND_MIN_SIM,
                                 max_msgs: int = CONTEXT_EXPAND_MAX_MSGS) -> list:
    q_vec = embed(query)
    if q_vec is None:
        return []
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT id, conversation_id, embedding FROM memories "
            "WHERE user_id IN (?, ?) AND deleted_at IS NULL "
            "AND embedding_model=? AND embedding_dim=? AND conversation_id IS NOT NULL",
            (user_id, get_household_id(user_id), EMBED_MODEL_NAME, EMBED_DIM),
        ).fetchall()
    best_conv_id = None
    best_mid = None
    best_sim = 0.0
    for mid, conv_id, blob in rows:
        if not blob or not conv_id:
            continue
        try:
            emb = np.frombuffer(blob, dtype=np.float32)
            if emb.shape[0] != EMBED_DIM:
                continue
        except Exception:
            continue
        sim = float(np.dot(q_vec, emb))
        if sim > best_sim:
            best_sim = sim
            best_conv_id = conv_id
            best_mid = mid
    if best_sim < min_sim or not best_conv_id:
        return []
    with db_connect() as conn:
        conv_rows = conn.execute(
            "SELECT id, user_msg, assistant_msg, timestamp FROM memories "
            "WHERE conversation_id=? AND user_id IN (?, ?) AND deleted_at IS NULL AND id != ? "
            "ORDER BY id ASC LIMIT ?",
            (best_conv_id, user_id, get_household_id(user_id), best_mid, max_msgs),
        ).fetchall()
    return [(r[1] or "", r[2] or "", r[3] or "") for r in conv_rows]

def format_conversation_expansion(conv_memories: list) -> str:
    if not conv_memories:
        return ""
    lines = []
    for user_msg, asst_msg, ts in conv_memories:
        u = truncate_words(user_msg, 40)
        a = truncate_words(asst_msg, 60)
        lines.append(f"[{ts}] User: {u}\nAssistant: {a}")
    return "\n---\n".join(lines)

# ───────────────────────── Knowledge search ─────────────────────────
_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "can", "to", "of", "in", "on", "at",
    "by", "for", "with", "from", "as", "it", "its", "this", "that", "these",
    "those", "and", "or", "but", "not", "no", "if", "then", "so", "what",
    "how", "why", "when", "where", "who", "which", "my", "your", "our",
    "i", "you", "we", "they", "he", "she", "me", "him", "her", "them",
    "about", "into", "than", "also", "just", "get", "got", "like",
})

def get_source_policy_map(user_id: str) -> dict:
    result = {}
    _hid = get_household_id(user_id)
    rank = {GLOBAL_OWNER_ID: 1, _hid: 2, user_id: 3}
    owners = (GLOBAL_OWNER_ID, _hid, user_id)
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT owner_user_id, domain, policy FROM source_policies WHERE owner_user_id IN (?, ?, ?)",
            owners).fetchall()
    tmp = {}
    for owner, domain, policy in rows:
        d = normalize_domain(domain)
        if not d:
            continue
        r = rank.get(owner, 0)
        if d not in tmp or r > tmp[d][0]:
            tmp[d] = (r, policy.strip().lower())
    for d, (_, p) in tmp.items():
        result[d] = p
    return result

def _keyword_fallback(query: str, user_id: str, limit: int,
                      exclude_ids: set, policy_map: dict) -> list:
    terms = [w for w in re.findall(r"\w+", query.lower())
             if len(w) >= 2 and w not in _STOP_WORDS]
    if not terms:
        return []
    like_clauses = " OR ".join(["LOWER(content) LIKE ?" for _ in terms])
    params = [f"%{t}%" for t in terms]
    params.extend([user_id, get_household_id(user_id), GLOBAL_OWNER_ID])
    with db_connect() as conn:
        rows = conn.execute(
            f"""SELECT id, title, content, source_domain, source_url,
                       confidence, pinned, owner_user_id, timestamp, tags
                FROM knowledge_items
                WHERE owner_user_id IN (?, ?, ?) AND deleted_at IS NULL
                  AND ({like_clauses})
                ORDER BY pinned DESC, timestamp DESC LIMIT ?""",
            [*params, limit + len(exclude_ids)]).fetchall()
    results = []
    for (kid, title, content, domain, source_url, confidence, pinned,
         owner, ts, tags_str) in rows:
        if kid in exclude_ids:
            continue
        d = normalize_domain(domain or source_url or "")
        pol = policy_map.get(d)
        if pol == "block":
            continue
        try:
            conf = float(confidence) if confidence is not None else 0.7
        except Exception:
            conf = 0.7
        conf = min(1.0, max(0.0, conf))
        content_lower = (content or "").lower()
        hit_count = sum(1 for t in terms if t in content_lower)
        mem_tags = parse_tags(tags_str)
        full_detail_tags = load_full_detail_tags()
        weighted = 0.0
        weighted += (conf - 0.5) * 0.12
        if pinned:
            weighted += 0.12
        if owner == HOUSEHOLD_USER_ID:
            weighted += 0.04
        elif owner == GLOBAL_OWNER_ID:
            weighted += 0.02
        if pol == "prefer":
            weighted += 0.08
        elif pol == "unreliable":
            weighted -= 0.08
        weighted += hit_count * 0.01
        weighted += time_decay(mem_tags, pinned, ts, full_detail_tags)
        results.append((weighted, 0.0, kid, title, content, d, conf, pol, tags_str, None, hit_count))
        if len(results) >= limit:
            break
    results.sort(key=lambda x: (x[-1], x[0]), reverse=True)
    return [r[:10] for r in results]

def search_knowledge(query: str, user_id: str, min_sim: float,
                     top_k: int = MAX_KNOWLEDGE_ITEMS) -> list:
    t0 = time.time()
    q_vec = embed(query)
    _embed_ms = (time.time() - t0) * 1000
    t0 = time.time()
    policy_map = get_source_policy_map(user_id)
    full_detail_tags = load_full_detail_tags()
    search_dedup = _get_adaptive_search_dedup_threshold()
    scored = []
    if q_vec is not None:
        with db_connect() as conn:
            rows = conn.execute(
                "SELECT id, title, content, source_domain, source_url, confidence, "
                "embedding, timestamp, pinned, owner_user_id, tags, use_count "
                "FROM knowledge_items "
                "WHERE owner_user_id IN (?, ?, ?) AND deleted_at IS NULL "
                "AND embedding_model=? AND embedding_dim=?",
                (user_id, get_household_id(user_id), GLOBAL_OWNER_ID, EMBED_MODEL_NAME, EMBED_DIM),
            ).fetchall()
        for (kid, title, content, domain, source_url, confidence, blob,
             ts, pinned, owner, tags_str, use_count) in rows:
            if not blob:
                continue
            try:
                emb = np.frombuffer(blob, dtype=np.float32)
                if emb.shape[0] != EMBED_DIM:
                    continue
            except Exception:
                continue
            d = normalize_domain(domain or source_url or "")
            pol = policy_map.get(d)
            if pol == "block":
                continue
            sim = float(np.dot(q_vec, emb))
            weighted = sim * recency_weight(ts)
            try:
                conf = float(confidence) if confidence is not None else 0.7
            except Exception:
                conf = 0.7
            conf = min(1.0, max(0.0, conf))
            weighted += (conf - 0.5) * 0.12
            if pinned:
                weighted += 0.12
            if owner == HOUSEHOLD_USER_ID:
                weighted += 0.04
            elif owner == GLOBAL_OWNER_ID:
                weighted += 0.02
            if pol == "prefer":
                weighted += 0.08
            elif pol == "unreliable":
                weighted -= 0.08
            weighted += min(use_count or 0, 10) * RECALL_BOOST_FACTOR
            mem_tags = parse_tags(tags_str)
            weighted += time_decay(mem_tags, pinned, ts, full_detail_tags)
            if sim >= min_sim or (pinned and sim >= PINNED_MIN_SIM):
                scored.append((weighted, sim, kid, title, content, d, conf, pol, tags_str, emb))
        scored.sort(key=lambda x: x[0], reverse=True)
    selected = []
    for row in scored:
        if len(selected) >= top_k:
            break
        emb = row[-1]
        dup = False
        for prev in selected:
            prev_emb = prev[-1]
            if prev_emb is not None and emb is not None:
                if float(np.dot(emb, prev_emb)) > search_dedup:
                    dup = True
                    break
        if not dup:
            selected.append(row)
    # Keyword fallback if not enough results
    # v5.2: only for explicit/low-threshold searches; auto-recall must not
    # pad the prompt with near-zero-sim keyword matches
    if len(selected) < top_k and min_sim <= KNOWLEDGE_EXPLICIT_MIN_SIM:
        existing_ids = {row[2] for row in selected}
        needed = top_k - len(selected)
        fallback = _keyword_fallback(query, user_id, needed, existing_ids, policy_map)
        selected.extend(fallback)
    if not selected:
        return []
    selected_ids = [row[2] for row in selected]
    if selected_ids:
        try:
            with db_connect() as conn:
                conn.executemany(
                    "UPDATE knowledge_items SET use_count=use_count+1, last_used_at=CURRENT_TIMESTAMP WHERE id=?",
                    [(i,) for i in selected_ids])
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to update knowledge use stats: {e}")
    _scan_ms = (time.time() - t0) * 1000
    _mean_sim = sum(r[1] for r in selected) / len(selected) if selected else 0.0
    logger.info(
        f"search_knowledge: selected={len(selected)} embed_ms={_embed_ms:.1f} "
        f"scan_ms={_scan_ms:.1f} mean_sim={_mean_sim:.3f}")
    out = []
    for _, sim, kid, title, content, d, conf, pol, tags_str, _ in selected:
        out.append({
            "sim": sim, "id": kid, "title": title or "",
            "content": content or "", "domain": d or "",
            "confidence": conf, "policy": pol or "normal",
            "tags": parse_tags(tags_str),
        })
    return out

# ───────────────────────── Formatting ─────────────────────────
def _format_index_entry(hit: dict, full_detail_tags: set) -> str:
    matched_tags = hit.get("tags", set()) & full_detail_tags
    tag_label = sorted(matched_tags)[0] if matched_tags else "ref"
    user_msg = hit.get("user_msg", "") or hit.get("summary", "")
    title = " ".join(user_msg.split()[:6]) if user_msg else "(empty)"
    desc = " ".join(user_msg.split()[:15]) if user_msg else ""
    mem_id = f"mem_{hit['id']}"
    return f"[{tag_label}] {title} — {desc} ({mem_id})"

def _format_knowledge_index_entry(h: dict, full_detail_tags: set) -> str:
    matched_tags = h.get("tags", set()) & full_detail_tags
    tag_label = sorted(matched_tags)[0] if matched_tags else "ref"
    title = h.get("title", "") or " ".join(h.get("content", "").split()[:6])
    desc = " ".join(h.get("content", "").split()[:15])
    return f"[{tag_label}] {title} — {desc} (id={h['id']})"

def format_memory_recall(hits: list) -> str:
    if not hits:
        return ""
    full_detail_tags = load_full_detail_tags()
    index_lines, full_text_lines, snippet_lines = [], [], []
    for hit in hits:
        has_full_tag = bool(hit.get("tags", set()) & full_detail_tags)
        is_strong = hit["sim"] >= FULL_TEXT_SIM_THRESHOLD
        if has_full_tag:
            index_lines.append(_format_index_entry(hit, full_detail_tags))
            if is_strong:
                full = f"[mem_{hit['id']}] User: {hit.get('user_msg', '')}\nAssistant: {hit.get('assistant_msg', '')}"
                full_text_lines.append(full)
        elif is_strong:
            full = f"[mem_{hit['id']}] User: {hit.get('user_msg', '')}\nAssistant: {hit.get('assistant_msg', '')}"
            full_text_lines.append(full)
        else:
            snip = truncate_words(hit.get("summary") or hit.get("user_msg", ""), MAX_WORDS_PER_SUMMARY)
            snippet_lines.append(f"(sim={round(hit['sim'],3)}) {snip}")
    parts = []
    if index_lines:
        parts.append("📋 Matching entries:\n" + "\n".join(index_lines))
    if full_text_lines:
        parts.append("\n---\n".join(full_text_lines))
    if snippet_lines:
        parts.append("\n---\n".join(snippet_lines))
    return "\n---\n".join(parts) if parts else ""

def format_knowledge_recall(hits: list) -> str:
    if not hits:
        return ""
    full_detail_tags = load_full_detail_tags()
    index_lines, full_text_lines, snippet_lines = [], [], []
    for h in hits:
        has_full_tag = bool(h.get("tags", set()) & full_detail_tags)
        is_strong = h["sim"] >= FULL_TEXT_SIM_THRESHOLD
        src = h["domain"] if h["domain"] else "unknown"
        policy_tag = ""
        if h["policy"] == "prefer":
            policy_tag = " ✅preferred"
        elif h["policy"] == "unreliable":
            policy_tag = " ⚠️unreliable"
        title = h["title"] if h["title"] else ""
        if has_full_tag:
            index_lines.append(_format_knowledge_index_entry(h, full_detail_tags) + f" [source: {src}{policy_tag}]")
            if is_strong:
                full_text_lines.append(
                    f"(sim={round(h['sim'],3)}, conf={round(h['confidence'],2)}) {title}{' — ' if title else ''}{h['content']} [source: {src}{policy_tag}]")
        elif is_strong:
            full_text_lines.append(
                f"(sim={round(h['sim'],3)}, conf={round(h['confidence'],2)}) {title}{' — ' if title else ''}{h['content']} [source: {src}{policy_tag}]")
        else:
            body = truncate_words(h["content"], MAX_WORDS_PER_KNOWLEDGE)
            snippet_lines.append(
                f"(sim={round(h['sim'],3)}, conf={round(h['confidence'],2)}) {title}{' — ' if title else ''}{body} [source: {src}{policy_tag}]")
    parts = []
    if index_lines:
        parts.append("📋 Matching knowledge:\n" + "\n".join(index_lines))
    if full_text_lines:
        parts.append("\n---\n".join(full_text_lines))
    if snippet_lines:
        parts.append("\n---\n".join(snippet_lines))
    return "\n---\n".join(parts) if parts else ""

def format_source_policy_summary(user_id: str) -> str:
    _hid = get_household_id(user_id)
    owners = (user_id, _hid, GLOBAL_OWNER_ID)
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT owner_user_id, domain, policy FROM source_policies "
            "WHERE owner_user_id IN (?, ?, ?) ORDER BY owner_user_id, policy, domain",
            owners).fetchall()
    if not rows:
        return "No source policies set."
    prefer, unreliable, block = [], [], []
    for owner, domain, policy in rows:
        d = normalize_domain(domain)
        if not d:
            continue
        mark = "🏠 " if owner == HOUSEHOLD_USER_ID else ("🌐 " if owner == GLOBAL_OWNER_ID else "")
        item = f"{mark}{d}"
        p = policy.strip().lower()
        if p == "prefer":
            prefer.append(item)
        elif p == "unreliable":
            unreliable.append(item)
        elif p == "block":
            block.append(item)
    parts = []
    if prefer:
        parts.append("Prefer: " + ", ".join(prefer[:20]))
    if unreliable:
        parts.append("Unreliable: " + ", ".join(unreliable[:20]))
    if block:
        parts.append("Blocked: " + ", ".join(block[:20]))
    return " | ".join(parts) if parts else "No source policies set."

def build_context_block(query: str, mem_hits: list, know_hits: list,
                        source_policy_text: str, conv_expansion: str = None) -> str:
    sections = []
    if mem_hits:
        sections.append(f"{RECALL_BLOCK_MARKER}\n{format_memory_recall(mem_hits)}")
    if conv_expansion:
        sections.append(f"Related conversation transcript:\n{conv_expansion}")
    if know_hits:
        sections.append(f"Knowledge Notes:\n{format_knowledge_recall(know_hits)}")
    sections.append(f"Source Policy:\n{source_policy_text}")
    sections.append(f"User's Current Question:\n{query}")
    return truncate_to_budget("\n\n".join(sections), RECALL_TOKEN_BUDGET)

# ───────────────────────── Memory storage ─────────────────────────
def store_memory(user_id: str, conv_id: str, user_text: str, asst_text: str,
                 tags: str = None, pinned: bool = False) -> Optional[int]:
    """Store a memory. Returns memory ID or None if skipped (dedup/embed failure)."""
    embed_input = (
        f"User: {truncate_chars(user_text, MAX_EMBED_USER_CHARS)}\n"
        f"Assistant: {truncate_chars(asst_text, MAX_EMBED_ASST_CHARS)}"
    )
    v = embed(embed_input)
    if v is None:
        logger.info("store_memory: embed failed")
        return None
    dedup_threshold = _get_adaptive_dedup_threshold()
    with db_connect() as conn:
        # Check for duplicates
        recent = conn.execute(
            "SELECT embedding FROM memories WHERE user_id=? AND deleted_at IS NULL "
            "AND embedding_model=? AND embedding_dim=? ORDER BY id DESC LIMIT 5",
            (user_id, EMBED_MODEL_NAME, EMBED_DIM)).fetchall()
        for (blob,) in recent:
            if not blob:
                continue
            try:
                prev = np.frombuffer(blob, dtype=np.float32)
                if prev.shape[0] == EMBED_DIM and float(np.dot(v, prev)) > dedup_threshold:
                    logger.info("store_memory: skipped duplicate")
                    return None
            except Exception:
                continue
        cur = conn.execute(
            "INSERT INTO memories(user_id, conversation_id, user_msg, assistant_msg, "
            "summary, embedding, embedding_model, embedding_dim, tags, pinned) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user_id, conv_id, user_text, asst_text, embed_input,
             v.tobytes(), EMBED_MODEL_NAME, EMBED_DIM, tags, 1 if pinned else 0))
        mem_id = cur.lastrowid
        conn.commit()
    _write_obsidian_conversation(conv_id, user_id, user_text, asst_text)
    logger.info(f"Stored memory mem_{mem_id} for {user_id} (conv {conv_id[:8]})")
    return mem_id

def store_pinned_memory(user_id: str, text: str, household: bool = False) -> int:
    """Store a pinned memory (manual). Returns memory ID."""
    if household and not is_household_member(user_id):
        raise RuntimeError("Household scope not available for this user")
    v = embed(text)
    if v is None:
        raise RuntimeError("Embedding model unavailable")
    owner = HOUSEHOLD_USER_ID if household else user_id
    with db_connect() as conn:
        cur = conn.execute(
            "INSERT INTO memories(user_id, summary, user_msg, assistant_msg, "
            "embedding, embedding_model, embedding_dim, pinned) "
            "VALUES (?,?,?,?,?,?,?,1)",
            (owner, text, text, "", v.tobytes(), EMBED_MODEL_NAME, EMBED_DIM))
        mem_id = cur.lastrowid
        conn.commit()
    return mem_id

# ───────────────────────── Knowledge storage ─────────────────────────
def store_knowledge(user_id: str, content: str, source: str = "",
                    confidence: float = 0.7, scope: str = "user",
                    tags: str = None, title: str = None) -> int:
    """Store a knowledge item. Returns knowledge ID."""
    if MAX_KNOWLEDGE_CHARS and len(content) > MAX_KNOWLEDGE_CHARS:
        raise ValueError(f"Content too large ({len(content)} chars). Limit is {MAX_KNOWLEDGE_CHARS}.")
    source_domain = normalize_domain(source)
    confidence = min(1.0, max(0.0, confidence))
    if scope not in {"user", "household", "global"}:
        scope = "user"
    owner = scope_to_owner(scope, user_id)
    emb_text = f"{title or ''}\n{content}\nsource:{source_domain}"
    v = embed(emb_text)
    if v is None:
        raise RuntimeError("Embedding model unavailable")
    with db_connect() as conn:
        cur = conn.execute(
            "INSERT INTO knowledge_items(owner_user_id, scope, title, content, "
            "source_url, source_domain, confidence, tags, embedding, "
            "embedding_model, embedding_dim) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (owner, scope, title, content,
             source if source else None,
             source_domain if source_domain else None,
             confidence, tags, v.tobytes(), EMBED_MODEL_NAME, EMBED_DIM))
        kid = cur.lastrowid
        conn.commit()
    _write_obsidian_knowledge(kid, owner, title, content, tags, source_domain)
    return kid

# ───────────────────────── Source policy ─────────────────────────
def upsert_source_policy(owner_user_id: str, domain: str, policy: str, note: str = None):
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO source_policies(owner_user_id, domain, policy, note, updated_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(owner_user_id, domain) DO UPDATE SET "
            "policy=excluded.policy, note=excluded.note, updated_at=CURRENT_TIMESTAMP",
            (owner_user_id, domain, policy, note))
        conn.commit()

# ───────────────────────── Obsidian vault ─────────────────────────
def _write_obsidian_conversation(conv_id: str, user_id: str, user_text: str, asst_text: str):
    try:
        folder = os.path.join(VAULT_DIR, "conversations")
        os.makedirs(folder, exist_ok=True)
        short = hashlib.sha1(conv_id.encode()).hexdigest()[:8]
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
        fname = f"{date}_{user_id[:8]}_{short}.md"
        path = os.path.join(folder, fname)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry = f"\n## {ts}\n\n**User:** {user_text}\n\n**Assistant:** {asst_text}\n"
        mode = "a" if os.path.exists(path) else "w"
        with open(path, mode, encoding="utf-8") as f:
            if mode == "w":
                f.write(f"# Conversation {conv_id}\n")
            f.write(entry)
    except Exception as e:
        logger.warning(f"Obsidian conversation write failed: {e}")

def _write_obsidian_knowledge(kid: int, owner: str, title: str, content: str,
                               tags_str: str, source_domain: str):
    try:
        full_detail_tags = load_full_detail_tags()
        mem_tags = parse_tags(tags_str)
        matched = mem_tags & full_detail_tags
        if matched:
            subdir = sorted(matched)[0]
        else:
            subdir = "notes"
        folder = os.path.join(VAULT_DIR, subdir)
        os.makedirs(folder, exist_ok=True)
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
        safe_title = re.sub(r"[^\w\s-]", "", title or "").strip().replace(" ", "-")[:50]
        if not safe_title:
            safe_title = f"item-{kid}"
        fname = f"{date}_{safe_title}.md"
        path = os.path.join(folder, fname)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lines = [
            f"# {title or 'Knowledge Item'}",
            f"**Date:** {ts}",
            f"**ID:** knowledge_{kid}",
            f"**Source:** {source_domain or 'unknown'}",
            f"**Tags:** {tags_str or 'none'}",
            "",
            content,
        ]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        logger.warning(f"Obsidian knowledge write failed: {e}")

# ───────────────────────── Muted users (session-level) ─────────────────────────
_MUTED_USERS = set()

def mute_user(user_id: str):
    _MUTED_USERS.add(user_id)

def unmute_user(user_id: str):
    _MUTED_USERS.discard(user_id)

def is_muted(user_id: str) -> bool:
    return user_id in _MUTED_USERS
