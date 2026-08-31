# Legacy Files

**⚠️ Superseded — do not install these files.**

This folder contains old versions kept for reference and for anyone migrating from
an earlier setup.

## `function-persistent-memory-V4.1`

The original monolithic Open WebUI function (single-file, pre-v5 architecture) from
the V4.1 era. It handled auto-recall, auto-store, and text-command parsing
(`memory list:`, `recall: <query>`, etc.) all in one Filter.

**It has been replaced by the v5 split architecture:**

| Legacy (V4.1 monolith) | Current (v5.x) |
|---|---|
| Single filter function doing everything | `memory_core.py` (shared core) + `memory_filter.py` (Filter) + `memory_tool.py` (Tool) |
| Text-command parsing (`memory list:`, `recall:`) | Model-callable tools via function-calling (`memory_search()`, `memory_list()`, …) |
| No pattern tagging | Pattern-based auto-tagging (infrastructure, path, decision, config, hardware, fix) |
| No trivial-exchange filtering | Short acknowledgments skipped automatically |
| No scopes | User / Household / Global scopes |

## Upgrading

If you are still running V4.1:

1. **Do not run both old and new together** — deactivate the V4.1 filter before
   activating the new Filter + Tool pair.
2. The SQLite schema is unchanged since v4, so your existing `memories.db` carries
   over as-is — no data migration needed.
3. Follow the installation instructions in the [main README](../README.md)
   (Option A: `install.sh`, or Option B: manual).
4. Run `migrate_v5.py` to verify the schema and list/deactivate any old filter
   functions still active in your database.

## When to use this folder

- Reference for the old text-command interface (e.g., restoring behavior, or
  curiosity about the pre-v5 design)
- The v5 migration guide above

For anything else, use the current files in the repo root.
