# OpenWebUI Persistent Memory Filter

A self-contained memory and knowledge system for [Open WebUI](https://github.com/open-webui/open-webui). Provides automatic conversation recall, manual knowledge storage, source policy management, and Obsidian vault integration — all in a single Python filter file.

## Features

### Memory (auto-stored conversations)
- **Automatic storage**: Every user/assistant exchange is embedded and stored in SQLite
- **Auto-recall**: Relevant past conversations are injected as system context on each message
- **Deduplication**: Similar conversations are skipped (configurable threshold)
- **Full-text storage**: Complete user and assistant messages stored in DB + Obsidian vault

### Knowledge (manually stored facts)
- **Structured storage**: Add knowledge with source, confidence, scope, tags, and title
- **Semantic + keyword search**: Falls back to keyword matching when vector search doesn't find enough
- **Source policies**: Mark domains as preferred, unreliable, or blocked

### v4.1 Features
1. **Dynamic full-detail tags** — tagged content (recipes, technical docs, etc.) always returns full text on recall, never truncated snippets
2. **Two-tier recall** — broad queries get a compact index list; specific queries get full text
3. **Same-conversation dedup** — won't recall memories from the conversation you're currently in
4. **Recall frequency boosting** — frequently-referenced memories get a small ranking boost
5. **Time-decay** — casual chat memories slowly lose priority; tagged reference material never decays
6. **Obsidian vault subfolders** — tagged knowledge goes to `<tag>/`, conversations go to `conversations/`
7. **`memory get` command** — retrieve any memory by ID or description

## Requirements

- **Open WebUI** (tested with Docker deployments)
- **sentence-transformers** Python package
- **numpy** Python package
- An embedding model (default: `BAAI/bge-m3`, 1024-dimensional)

```bash
pip install sentence-transformers numpy
```

## Installation

1. In Open WebUI, go to **Settings → Functions**
2. Click **Create Function**
3. Paste the entire contents of `memory_knowledge_function.py`
4. Save and enable the filter (set as global if you want it for all users)

The database and Obsidian vault are created automatically on first run.

## Configuration

All paths and model settings are configurable via environment variables:

| Variable | Default | Description |
|---|---|---|
| `MEMORY_BASE_PATH` | `/app/backend/data` | Base data directory |
| `MEMORY_DB_PATH` | `<BASE_PATH>/memories.db` | SQLite database path |
| `MEMORY_VAULT_DIR` | `<BASE_PATH>/ObsidianVault/Memories` | Obsidian vault output directory |
| `MEMORY_EMBED_MODEL` | `BAAI/bge-m3` | HuggingFace embedding model name |
| `MEMORY_EMBED_DIM` | `1024` | Embedding dimension (must match model) |

For Docker deployments, set these in your `docker-compose.yaml`:

```yaml
services:
  openwebui:
    environment:
      - MEMORY_BASE_PATH=/app/backend/data
      - MEMORY_EMBED_MODEL=BAAI/bge-m3
      - MEMORY_EMBED_DIM=1024
```

## Commands

### Memory
| Command | Description |
|---|---|
| `memory list:` | List recent memories |
| `memory list pinned:` | List pinned memories |
| `memory search: <query>` | Semantic search, 10 hits |
| `memory transcript: <id>` | All memories from same conversation |
| `memory get: <id>` | Full text by memory ID |
| `memory get: <description>` | Full text by semantic search |
| `memory stats:` | Database statistics |
| `memory pin: <text>` | Pin a memory |
| `memory pin household: <text>` | Pin for household scope |
| `memory forget: <id>` | Delete a memory |
| `memory mute:` / `memory unmute:` | Toggle auto-storage |
| `memory prune:` | Remove stale memories (90+ days, 0 recalls) |

### Full-Detail Tag Management
| Command | Description |
|---|---|
| `memory tag full: <tag>` | Add tag to full-detail set |
| `memory tag unfull: <tag>` | Remove tag from full-detail set |
| `memory tag list:` | Show all full-detail tags |

### Knowledge
| Command | Description |
|---|---|
| `knowledge add: <text> \| source= \| confidence= \| scope= \| tags= \| title=` | Store knowledge |
| `knowledge list:` | List all knowledge |
| `knowledge list: <query>` | Search knowledge |
| `knowledge show: <id>` | Show full knowledge entry |
| `knowledge forget: <id>` | Delete knowledge |
| `knowledge pin: <id>` | Pin knowledge |

### Source Policies
| Command | Description |
|---|---|
| `source prefer: <domain>` | Mark domain as preferred |
| `source unreliable: <domain>` | Mark domain as unreliable |
| `source block: <domain>` | Block domain entirely |
| `source remove: <domain>` | Remove source rule |
| `source list:` | Show all source rules |

### Recall
| Command | Description |
|---|---|
| `recall: <query>` | Explicit recall with full two-tier logic |
| *(automatic)* | Auto-recall runs on every user message |

### Natural Language Aliases
| Input | Maps to |
|---|---|
| `/remember <text>` | `knowledge add: <text> \| scope=user` |
| `/house <text>` | `knowledge add: <text> \| scope=household` |
| `/find <query>` | `recall: <query>` |
| `/trust <domain>` | `source prefer: <domain>` |
| `/avoid <domain>` | `source unreliable: <domain>` |
| `/block <domain>` | `source block: <domain>` (requires confirmation) |
| `remember this: <text>` | `knowledge add: <text>` |
| `remember for household: <text>` | `knowledge add: <text> \| scope=household` |
| `what do you remember about <topic>` | `recall: <topic>` |
| `show my memories` | `memory list:` |
| `forget memory <id>` | `memory forget: <id>` (requires confirmation) |

### Privacy
- Add `--private` to any message to prevent it from being stored
- Commands and slash-commands are never stored as memories

## How Recall Works

### Two-Tier System

When you ask a question, the filter searches all stored memories:

1. **Index entries** (compact, ~25 tokens each): For all memories with full-detail tags (e.g., `recipe`, `technical`), a one-line index entry is shown: `[recipe] Keto Pancakes — almond flour, eggs, cream cheese (mem_847)`

2. **Full text** (for strong matches): When similarity ≥ 0.80, or when the memory has a full-detail tag AND similarity ≥ 0.80, the complete stored exchange is injected

3. **Snippets** (for weak matches): Non-tagged memories below 0.80 similarity get a truncated snippet

### Scoring Formula

```
effective_score = similarity
                + (recall_count × 0.01)          # frequency boost
                + time_decay(memory)              # age penalty

time_decay:
  = 0.0   if memory has a full-detail tag
  = 0.0   if memory is pinned
  = -0.10 × (age_days / 30)   otherwise
```

### Token Budget

Total recall context is capped at 2500 tokens (configurable via `RECALL_TOKEN_BUDGET`). Priority:
1. Index entries (cheap, show everything matching)
2. Full text for strong matches
3. Snippets for remaining matches
4. Budget exceeded → remaining items skipped

## Obsidian Vault Structure

```
vault/
├── conversations/          # Auto-stored chat exchanges
│   └── 20260701_abc12345_def01234.md
├── recipes/                # Knowledge with tags=recipe
│   └── 20260701_keto-pancakes.md
├── technical/              # Knowledge with tags=technical
│   └── 20260701_docker-fix.md
├── medical/                # Knowledge with tags=medical
│   └── 20260701_medication.md
├── reference/              # Knowledge with tags=reference
│   └── 20260701-api-docs.md
└── notes/                  # Knowledge without full-detail tags
    └── 20260701_general.md
```

## Database Schema

### `memories` table
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment ID |
| `user_id` | TEXT | Owner user ID |
| `conversation_id` | TEXT | Chat session ID |
| `user_msg` | TEXT | Full user message |
| `assistant_msg` | TEXT | Full assistant response |
| `summary` | TEXT | Truncated text used for embedding input |
| `embedding` | BLOB | Normalized vector embedding |
| `embedding_model` | TEXT | Model used for embedding |
| `embedding_dim` | INTEGER | Vector dimension |
| `pinned` | INTEGER | 1 if pinned |
| `use_count` | INTEGER | Times recalled |
| `tags` | TEXT | Comma-separated tags |
| `timestamp` | DATETIME | Creation time |
| `deleted_at` | DATETIME | Soft-delete timesp |

### `knowledge_items` table
Similar structure, with additional fields: `scope`, `title`, `source_url`, `source_domain`, `confidence`.

### `source_policies` table
| Column | Type | Description |
|---|---|---|
| `owner_user_id` | TEXT | Who set the policy |
| `domain` | TEXT | Domain name |
| `policy` | TEXT | `prefer`, `unreliable`, or `block` |

### `config` table
| Column | Type | Description |
|---|---|---|
| `key` | TEXT PK | Setting name |
| `value` | TEXT | JSON value |

Currently stores `full_detail_tags` as a JSON array.

## Tuning

Key constants in the config section of the file:

| Constant | Default | Purpose |
|---|---|---|
| `AUTO_RECALL_MIN_SIM` | 0.55 | Minimum similarity for auto-recall |
| `EXPLICIT_RECALL_MIN_SIM` | 0.35 | Minimum for explicit `recall:` command |
| `DEDUP_THRESHOLD` | 0.88 | Skip storage if similarity to recent memory exceeds this |
| `FULL_TEXT_SIM_THRESHOLD` | 0.80 | Similarity needed for full-text recall |
| `RECALL_TOKEN_BUDGET` | 2500 | Max tokens injected as context |
| `RECALL_BOOST_FACTOR` | 0.01 | Score boost per recall |
| `TIME_DECAY_HALFLIFE_DAYS` | 30 | Days for -0.10 penalty on casual memories |
| `CONTEXT_EXPAND_MIN_SIM` | 0.72 | Similarity to trigger conversation expansion |

## License

GPL3.0 

## Changelog

- **v4.1** — Dynamic full-detail tags, two-tier recall, same-conversation dedup, recall frequency boosting, time-decay, Obsidian subfolders, `memory get` command
- **v4.0** — Full-text storage, `memory search`/`memory transcript`, auto context expansion, `output` field fix, longer snippets, higher recall budget
- **v3.x** — Initial release with auto-recall, knowledge storage, source policies, natural language aliases
