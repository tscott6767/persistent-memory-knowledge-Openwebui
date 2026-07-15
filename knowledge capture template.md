# Knowledge Capture Template

**Version:** v4.1 compatible
**Purpose:** Extract structured content from a URL or text, then store it in your persistent memory system with proper tags for full-text retrieval.

---

## How to use this

Paste the block below into a new chat whenever you have content to save. The AI will produce two outputs:

1. **Clean Markdown** — formatted for you to read immediately
2. **Knowledge command** — a single-line `knowledge add:` command you copy-paste into chat to store it permanently

In v4.1, tagged knowledge items (e.g. `tags=recipe`) are stored in full and retrieved in full when the similarity is strong enough. You no longer need to write a short summary — store the complete content. The two-tier recall system automatically generates a compact index entry for broad searches and returns full text for specific queries.

---

## Template

**Task:** Extract the content from the provided [URL/Text] and perform two distinct outputs.

### Output 1: Clean Markdown

Provide a well-formatted, easy-to-read document using Markdown. Structure will vary by content type — for a recipe, include:
* `# [Title]`
* `## Ingredients` (bullet points)
* `## Instructions` (numbered list)
* `## Notes` (if applicable)

For technical documentation, medical references, or other content types, use appropriate headings and structure.

### Output 2: Knowledge Command

Generate a single-line `knowledge add:` command to save this to persistent memory.

**Strict Formatting Rules:**
1. **Content:** Store the **full content** — not a summary. The v4.1 tag system ensures tagged items are retrieved in full text on strong matches and shown as compact index entries on broad searches.
2. **Structure must be exactly:**
   `knowledge add: [Full content] | source=[URL] | confidence=0.9 | scope=user | tags=recipe,[additional tags] | title=[Title]`
3. **Tags:** Use the primary content type as the first tag (e.g., `recipe`, `technical`, `medical`, `reference`). Add 1-2 more based on the content (e.g., `keto`, `vegan`, `docker`, `api-docs`).
4. **Scope:** Default to `user`. Use `scope=household` for shared family/household items.

**Why full content works in v4.1:** Tagged knowledge items bypass the snippet truncation that applies to untagged memories. The system automatically creates an index line for broad queries and returns the complete stored text when the query is specific enough (similarity ≥ 0.80). You never lose detail.

---

## Available tags (defaults)

The system ships with these full-detail tags enabled:
- `recipe` — cooking recipes
- `technical` — technical documentation, configs, how-tos
- `medical` — medical information, medications, dosages
- `reference` — API docs, specifications, reference material

Add your own with: `memory tag full: <tag>`

---

## Examples

### Recipe
`knowledge add: Keto Almond Flour Pancakes. Ingredients: 2 cups almond flour, 4 eggs, 4 oz cream cheese, 2 tbsp butter, 1 tsp baking powder, pinch salt. Instructions: 1. Blend all ingredients until smooth. 2. Heat butter in skillet over medium-low. 3. Pour 1/4 cup batter per pancake. 4. Cook 3 min per side until golden. Notes: Batter will be thinner than regular pancake batter. Makes 8 pancakes. | source=example-recipe-site.com | confidence=0.9 | scope=user | tags=recipe,keto,breakfast | title=Keto Almond Flour Pancakes`

### Technical
`knowledge add: Docker compose fix for Open WebUI volume permissions. When /app/backend/data has wrong ownership, add user:1000:1000 to the openwebui service in docker-compose.yaml and run: docker compose down && docker compose up -d. This ensures the container runs as the correct UID for the mounted volume. | source=docker.com | confidence=0.9 | scope=household | tags=technical,docker | title=Docker Volume Permission Fix`

---

**[PASTE URL OR TEXT BELOW]**

---

## How to use this effectively

1. **The dual output:** When you paste this template with your content, the AI gives you the formatted Markdown (to read/use now) and the `knowledge add:` command (to store permanently).
2. **The copy-paste:** Highlight the `knowledge add:...` line, paste it into your chat, hit enter. The filter stores it with embedding, tags, source, and confidence.
3. **Retrieval:** Next time you ask about the topic — "what was that keto pancake recipe?" — the filter's auto-recall finds it by semantic similarity and injects the full text into context automatically. No need to search manually.
4. **Vault export:** Tagged knowledge items are also written to your Obsidian vault under their tag subfolder (e.g., `recipes/`, `technical/`) for browsing outside the AI.

