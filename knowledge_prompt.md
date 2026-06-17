To make this as efficient as possible, I have designed this template to solve the "truncation" problem. Since your system truncates knowledge items to ~220 words, the template instructs the AI to create a **concise summary** for the `knowledge add:` command (to ensure it's always searchable) while providing the **full recipe** in Markdown for your immediate use.

Copy and paste the block below into a new chat (or use it as a "System Prompt" if you use custom instructions) whenever you have a recipe to save.

***

# Recipe Extraction & Knowledge Template

**Task:** Extract the recipe from the provided [URL/Text] and perform two distinct outputs.

### Output 1: Clean Markdown Recipe
Provide a beautifully formatted, easy-to-read recipe using Markdown. Include:
* `# [Recipe Title]`
* `## Ingredients` (with bullet points)
* `## Instructions` (numbered list)
* `## Notes` (if applicable)

### Output 2: Knowledge Command
Generate a single-line `knowledge add:` command that I can copy and paste to save this to my memory. 

**Strict Formatting Rules for the Command:**
1. **The `<text>` part:** Do **NOT** paste the whole recipe here. Instead, write a concise, high-density summary (50–100 words) that includes the main ingredients and the core cooking method. This ensures the entry is never truncated during retrieval.
2. **The structure must be exactly:** 
`knowledge add: [Summary] | source=[URL] | confidence=0.9 | scope=user | tags=recipe,[additional tags] | title=[Recipe Title]`
3. **Tags:** Default to `recipe`. Add 1-2 more based on the content (e.g., `keto`, `vegan`, `dessert`).

---
**[PASTE URL OR TEXT BELOW]**

***

## How to use this effectively:

1.  **The "One-Two Punch":** When you paste this, the AI will give you the full recipe (for you to read/cook) and the command (for your database).
2.  **The Copy-Paste:** You simply highlight the `knowledge add:...` line, paste it into our chat, and hit enter.
3.  **Why the summary is important:** If you tried to put the *entire* recipe into the `knowledge add:` command, your system would cut it off halfway through the instructions, making the "Knowledge List" look messy and making retrieval less reliable. The summary acts as a "searchable index" while the Markdown version serves as your "cookbook."