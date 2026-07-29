# Persistent Memory & Knowledge System for Open WebUI

A hybrid **Filter + Tool** architecture that gives Open WebUI persistent memory, semantic knowledge search, and source policy management — all stored locally in SQLite with vector embeddings.

## Features

- **Auto-recall** — Relevant past conversations and knowledge are automatically injected as context into every new message (Filter inlet)
- **Auto-store** — Each conversation exchange is automatically saved as a memory (Filter outlet)
- **Model-callable tools** — The LLM can search, store, list, delete, and pin memories/knowledge via function-calling (no text command parsing)
- **Semantic search** — Uses `BAAI/bge-m3` sentence-transformer embeddings with graceful keyword fallback
- **Knowledge items** — Curated facts/references with source domains, confidence scores, and tags
- **Source policies** — Mark domains as preferred, unreliable, or blocked; recall results are adjusted accordingly
- **Scopes** — User (personal), Household (shared), and Global (all users) scopes for both memories and knowledge
- **Obsidian vault sync** — Memories and knowledge are mirrored to Markdown files in an Obsidian-compatible vault structure
- **Token-aware truncation** — Uses tiktoken when available, falls back to char/4
- **Adaptive deduplication** — Thresholds adjust based on corpus size
- **Conversation context expansion** — Finds related past conversations and includes transcript excerpts
- **`--private` flag** — Prefix any message with `--private` to prevent both recall and storage for that message

## Architecture

| Component | File | Role |
|-----------|------|------|
| **Shared core** | `memory_core.py` | DB schema, embeddings, search, storage, formatting, Obsidian sync — imported by both Filter and Tool |
| **Filter** | `memory_filter.py` | Inlet: auto-recall. Outlet: auto-store. Zero command parsing. |
| **Tool** | `memory_tool.py` | All memory/knowledge/source operations as model-callable methods |
| **Migration** | `migrate_v5.py` | Schema check, function cleanup, core installation |

### Why split Filter and Tool?

Open WebUI has two function types:
- **Filters** intercept the request/response pipeline (`inlet`/`outlet` hooks). They run automatically but can't be called on-demand by the model.
- **Tools** expose methods the model can call via function-calling. The model decides when to use them.

This system gives each job to the right mechanism: the Filter handles passive recall/store, the Tool handles interactive operations.

## Tool Methods (model-callable)

### Memory
| Method | Description |
|--------|-------------|
| `memory_search(query)` | Semantic search of past memories |
| `memory_store(text, tags)` | Store a pinned memory |
| `memory_get(id)` | Get full memory by ID |
| `memory_delete(id)` | Soft-delete a memory |
| `memory_list(pinned_only)` | List memories (20 most recent) |
| `memory_stats()` | Counts and statistics |
| `memory_transcript(id)` | Full conversation transcript for a memory |
| `memory_prune()` | Remove stale memories (>90 days, 0 recalls) |
| `memory_mute()` / `memory_unmute()` | Toggle auto-storage |

### Tag Management
| Method | Description |
|--------|-------------|
| `memory_tag_add(tag)` | Add a full-detail tag |
| `memory_tag_remove(tag)` | Remove a full-detail tag |
| `memory_tag_list()` | List full-detail tags |

### Knowledge
| Method | Description |
|--------|-------------|
| `knowledge_add(content, source, confidence, scope, tags, title)` | Store curated knowledge |
| `knowledge_search(query)` | Semantic search of knowledge |
| `knowledge_list(query)` | List/search knowledge items |
| `knowledge_show(id)` | Get full knowledge item by ID |
| `knowledge_delete(id)` | Soft-delete knowledge item |
| `knowledge_pin(id)` | Pin knowledge item for priority recall |

### Source Policy
| Method | Description |
|--------|-------------|
| `source_policy_set(domain, policy, scope)` | Set prefer/unreliable/block for a domain |
| `source_policy_remove(domain, scope)` | Remove a source policy |
| `source_policy_list()` | List all source policies |

## Requirements

- **Open WebUI** v0.5.x+ (tested on v0.10.0)
- **Python packages** (inside the OWUI container):
  - `numpy`
  - `sentence-transformers` (for the `BAAI/bge-m3` embedding model)
  - `tiktoken` (optional — improves token counting accuracy)

## Installation

### Option A: Automated (recommended)

```bash
# 1. Clone this repo
git clone https://github.com/tscott6767/persistent-memory-knowledge-Openwebui.git
cd persistent-memory-knowledge-Openwebui

# 2. Run the install script (from the OWUI host)
chmod +x install.sh
./install.sh
```

The script will:
- Copy `memory_core.py` to your OWUI data directory
- Run the migration script to verify schema and list existing functions
- Print step-by-step instructions for creating the Filter and Tool in the OWUI admin panel

### Option B: Manual

1. **Copy `memory_core.py` to the OWUI data directory:**
   ```bash
   # Adjust path to match your installation
   cp memory_core.py /opt/openwebui/data/memory_core.py
   # If using Docker:
   docker cp memory_core.py openwebui:/app/backend/data/memory_core.py
   ```

2. **Run the migration script (optional — verifies schema and lists existing functions):**
   ```bash
   docker exec -it openwebui python3 /tmp/migrate_v5.py
   ```

3. **Create the Filter in OWUI:**
   - Admin → Functions → New Filter
   - Name: `Memory Filter v5`
   - Paste contents of `memory_filter.py`
   - Set as global, activate

4. **Create the Tool in OWUI:**
   - Admin → Functions → New Tool
   - Name: `Memory Tool v5`
   - Paste contents of `memory_tool.py`
   - Set as global, activate

5. **Attach the Tool to your model:**
   - Workspace → Models → edit your model
   - Enable the Memory Tool v5 in the tools list

6. **Test:**
   - Ask: "What do you remember about me?" → should trigger `memory_search`
   - Have a short conversation → check logs for auto-store firing
   - Try: "Store this: the WiFi password is X" → should call `knowledge_add`

## Database Schema

All data is stored in SQLite (`memories.db` in the OWUI data directory). No external database required.

| Table | Purpose |
|-------|---------|
| `memories` | Conversation memories (auto-stored + pinned) |
| `knowledge_items` | Curated knowledge entries |
| `source_policies` | Domain trust/block rules |
| `config` | Key-value settings (full_detail_tags) |
| `schema_version` | Version tracking (currently v4) |

## Configuration

All configuration is via environment variables (set in OWUI container):

| Variable | Default | Description |
|----------|---------|-------------|
| `OWUI_DATA_PATH` | `/app/backend/data` | Path to OWUI data directory |

## File Locations (inside container)

| File | Path |
|------|------|
| `memory_core.py` | `/app/backend/data/memory_core.py` |
| `memories.db` | `/app/backend/data/memories.db` |
| Obsidian vault | `/app/backend/data/ObsidianVault/Memories/` |

## Privacy

- All data stays local — no external API calls for storage or search
- Embedding model runs locally via `sentence-transformers`
- Prefix any message with `--private` to prevent both recall and storage
- `memory_mute()` temporarily disables auto-storage for the session
- Delete individual memories or knowledge items at any time via tool calls

## License

MIT — see [LICENSE](LICENSE)
