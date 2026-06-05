# persistent-memory-knowledge-Openwebui
A persistent memory function with customisable knowledge and in chat curating and retrieval.

# What This Script Does

This Python script is a **Persistent Memory and Knowledge Filter for OpenWebUI (v3)**. It acts as middleware (inlet and outlet) between the user and the LLM to provide long-term statefulness and context management.

*   **Memory Management**: Automatically summarizes and stores conversation turns in a local SQLite database, embedding them using the `BAAI/bge-m3` model [1].
*   **Knowledge Base**: Allows manual ingestion of external facts (`knowledge_items`) with metadata like confidence scores, tags, and source URLs [1].
*   **Source Policies**: Lets you define rules to `prefer`, mark as `unreliable`, or `block` specific domains when retrieving knowledge [1].
*   **Context Injection**: Automatically retrieves relevant past memories and knowledge (auto-recall) or via explicit `recall:` commands, injecting them into the system prompt before the LLM generates a response [1].
*   **Obsidian Integration**: Exports conversation logs to a local directory formatted for Obsidian (`/app/backend/data/ObsidianVault/Memories`) [1].
*   **Scoping**: Supports `user`, `household`, and `global` scopes, allowing shared memories across a family or team instance [1].

# Why You Would Need It

Standard LLMs are stateless and forget everything once a context window closes or a new chat is started. You need this if:
*   You want the AI to remember your personal preferences, coding projects, or household routines across different chat sessions.
*   You want to build a curated, high-signal knowledge base without the noise and high token costs of full-document RAG (Retrieval-Augmented Generation).
*   You share an OpenWebUI instance with family or colleagues and need shared (`household`) memories alongside private ones.
*   You want a human-readable, searchable journal of your AI interactions via Obsidian.

# Prerequisites

*   **OpenWebUI**: A running instance of OpenWebUI with the Functions/Filters feature enabled.
*   **Python Environment**: The OpenWebUI backend must have `numpy` and `sentence-transformers` installed in its Python environment to load the embedding model [1].
*   **Hardware Resources**: The `BAAI/bge-m3` model requires roughly 2.2 GB of RAM/VRAM to load and run efficiently.
*   **Filesystem Access**: Write permissions to the `BASE_PATH` (default `/app/backend/data`) for the SQLite database and Obsidian vault directory [1].

# How to Install It

1.  **Install Dependencies**: If using Docker, you may need to build a custom OpenWebUI image or execute `pip install sentence-transformers numpy` inside the running container.
2.  **Add the Function**:
    *   Navigate to the OpenWebUI Admin Panel -> **Functions** (or Workspace -> Functions).
    *   Click **Create** (or import) and paste the entire script.
    *   Name it (e.g., "Persistent Memory v3") and save.
3.  **Configure and Enable**:
    *   Toggle the function to **Active**.
    *   Adjust `BASE_PATH` in the code if your OpenWebUI data directory differs from `/app/backend/data` [1].
4.  **Verify**: Type `memory stats:` or `source list:` in a chat to ensure the filter is intercepting commands and responding correctly.

# Long-Term Benefits

*   **Compounding Context**: The AI becomes increasingly personalized and effective as the memory database grows, reducing the need to repeatedly explain your background, tech stack, or project constraints.
*   **Structured Knowledge**: Over time, your manually added `knowledge_items` create a highly optimized, deduplicated, and policy-filtered retrieval system that outperforms naive document chunking.
*   **Digital Journal**: The automatic Obsidian export builds a comprehensive, searchable markdown archive of your problem-solving processes and research.

# For Whom

*   **Power Users & Self-Hosters**: Individuals running local or private cloud LLMs who want maximum control over their data and context.
*   **Families/Teams**: Groups sharing an OpenWebUI instance who want to leverage the `household` scope for shared grocery lists, Wi-Fi passwords, or project documentation [1].
*   **Researchers & Writers**: Those who need the AI to remember specific literature, facts, or drafting preferences over months of work.

# Is It Safe?

The primary security concern with any persistent memory system is data exposure, but this script is fundamentally safe for self-hosted environments. 

*   **Data Locality**: All memories, knowledge, and embeddings are stored locally in a SQLite database (`memories.db`) and local markdown files [1]. No data is sent to external APIs by the filter itself.
*   **Model Safety**: The embedding model (`BAAI/bge-m3`) is a standard, open-source model downloaded from Hugging Face [1].
*   **Risks and Counterarguments**: If your OpenWebUI instance is exposed to the public internet without robust authentication, any unauthorized user could access your entire memory database or inject malicious knowledge. Furthermore, because the script executes Python code within the OpenWebUI backend, a vulnerability in OpenWebUI's function sandboxing could theoretically allow arbitrary code execution. However, this is a platform-level risk rather than a flaw in this specific script. 
*   **Privacy Controls**: The script includes a `--private` flag to skip storing specific turns and a `memory mute:` command to pause storage entirely for sensitive sessions [1].
