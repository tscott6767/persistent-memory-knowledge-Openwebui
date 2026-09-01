# Persistent Memory & Knowledge System for Open WebUI

A hybrid **Filter + Tool** architecture that gives Open WebUI persistent memory, semantic knowledge search, and source policy management — all stored locally in SQLite with vector embeddings.

## What's New

### v5.1 — Memory Filter (Aug 2026)
- **Pattern-based auto-tagging** — Detects infrastructure, path, decision, config, hardware, and fix patterns in assistant responses. Tagged exchanges get enhanced embeddings for better future recall.
- **Technical content extraction** — Key technical lines (commands, paths, configs) are extracted and prepended to the stored memory embedding, improving semantic searchability.
- **Trivial exchange filtering** — Short acknowledgment responses ("ok", "got it", "done") are skipped to reduce noise in the memory store.

### v4 — System Prompt (Aug 2026)
- **Memory Storage Protocol** — 7 explicit "store immediately" triggers that tell models when they MUST persist information (working commands, infrastructure changes, decisions, hardware specs, problem fixes, file paths, explicit user requests).
- **5-layer storage hierarchy** — Clear distinction between persistent memory, knowledge items, chat history, notes, and knowledge bases.
- **Context integrity canary** — CK timestamp stamp that makes it visible when a model has lost context.

## Features

- **Auto-recall** — Relevant past conversations and knowledge are automatically injected as context into every new message (Filter inlet)
- **Auto-store** — Each conversation exchange is automatically saved as a memory (Filter outlet)
- **Pattern-based tagging** — Technical content is auto-detected and tagged (infrastructure, path, decision, config, hardware, fix) for better searchability
- **Trivial exchange filtering** — Short acknowledgment responses are skipped to keep the memory store clean
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
| **Filter** | `memory_filter.py` | Inlet: auto-recall. Outlet: auto-store with pattern tagging + trivial filtering. |
| **Tool** | `memory_tool.py` | All memory/knowledge/source operations as model-callable methods |
| **Migration** | `migrate_v5.py` | Schema check, function cleanup, core installation |
| **System prompt** | `system prompt.md` | Reference system prompt with Memory Storage Protocol and 5-layer hierarchy |

### Why split Filter and Tool?

Open WebUI has two function types:
- **Filters** intercept the request/response pipeline (`inlet`/`outlet` hooks). They run automatically but can't be called on-demand by the model.
- **Tools** expose methods the model can call via function-calling. The model decides when to use them.

This system gives each job to the right mechanism: the Filter handles passive recall/store, the Tool handles interactive operations.

### How the Filter and System Prompt Work Together

The **system prompt** tells the model *when and why* to store memories (the 7 explicit triggers in the Memory Storage Protocol). The **filter** provides a safety net — it automatically stores exchanges even when the model doesn't proactively call `memory_store`. The filter also enhances stored content with pattern-based tagging and technical content extraction that the model wouldn't know to add.

This dual approach ensures:
1. Models that follow the system prompt will proactively store important context
2. Models that don't (or can't) still get coverage from the filter
3. Technical content gets properly tagged and enhanced for future searchability

## Memory Filter v5.1 Details

### Pattern Detection

The filter scans assistant responses for storeable technical content using regex patterns across 6 categories:

| Tag | Detects |
|-----|--------|
| `infrastructure` | Commands (systemctl, docker, cmake, pct, qm), flags (--port, --ctx-size, -ngl), binary paths |
| `path` | File paths starting with /opt, /etc, /var, /usr, /home, /projects, /models, etc. |
| `decision` | Switch/choose/decided/migrated/deprecated/decommissioned language |
| `config` | Key=value pairs, config files (.service, .conf, .yaml, .json, .env), systemd units |
| `hardware` | Hardware mentions (Xeon, RTX, GPU, VRAM, RAM, NVMe, SSD, PCIe, ZFS) |
| `fix` | Problem resolution language (fixed, resolved, root cause, the error was) |

### Technical Content Extraction

When patterns are detected, key technical lines (commands, paths, configs) are extracted from the assistant response and prepended to the memory embedding. This ensures future semantic searches can find the technical content even if the user's question was phrased differently.

### Trivial Exchange Filtering

Exchanges are skipped when:
- The assistant response is under 30 characters (unless patterns are detected)
- The response is a pure acknowledgment ("ok", "got it", "will do", etc.)
- The response is very short with no sentence punctuation

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
   - Name: `Memory Filter v5.1`
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

6. **Set the system prompt:**
   - Admin → Settings → General (or per-model)
   - Paste the contents of `system prompt.md`
   - Adapt the identity, paths, and project references to your deployment

7. **Test:**
   - Ask: "What do you remember about me?" → should trigger `memory_search`
   - Have a short conversation → check logs for auto-store firing with tags
   - Try: "Store this: the WiFi password is X" → should call `knowledge_add`
   - Check logs for pattern detection: `Auto-store with tags [infrastructure, path] for...`

## Database Schema

All data is stored in SQLite (`memories.db` in the OWUI data directory). No external database required.

| Table | Purpose |
|-------|---------|
| `memories` | Conversation memories (auto-stored + pinned, with tags) |
| `knowledge_items` | Curated knowledge entries |
| `source_policies` | Domain trust/block rules |
| `config` | Key-value settings (full_detail_tags) |
| `schema_version` | Version tracking (currently v4) |

## Configuration

All configuration is via environment variables (set in OWUI container):

| Variable | Default | Description |
|----------|---------|-------------|
| `OWUI_DATA_PATH` | `/app/backend/data` | Path to OWUI data directory |
| `OWUI_HOUSEHOLD_MEMBERS` | *(empty)* | Comma-separated Open WebUI user UUIDs that share the "household" scope, e.g. `OWUI_HOUSEHOLD_MEMBERS="uuid1,uuid2"`. Users not in this list fall back to personal scope. Leave unset if you don't use household scope. |

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

## Version History

| Version | Date | Changes |
|---------|------|---------|
| Memory Filter v5.1 | Aug 2026 | Pattern-based tagging, technical content extraction, trivial exchange filtering |
| Memory Filter v5.0 | Aug 2026 | Initial filter release — auto-recall + auto-store |
| System Prompt v4 | Aug 2026 | Memory Storage Protocol (7 triggers), 5-layer hierarchy, CK canary |
| Memory Core v5 | Jul 2026 | Shared core with embeddings, Obsidian sync, source policies |

## License

MIT — see [LICENSE](LICENSE)
