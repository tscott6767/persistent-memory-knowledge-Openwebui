#!/usr/bin/env bash
#
# install.sh — Automated installer for Persistent Memory & Knowledge System (Open WebUI)
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
#
# This script:
#   1. Detects your OWUI data directory (Docker or bare-metal)
#   2. Copies memory_core.py to the correct location
#   3. Runs the migration script to verify schema and list existing functions
#   4. Prints step-by-step instructions for creating Filter + Tool in OWUI admin
#
set -euo pipefail

# ─── Colors ───
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  Persistent Memory & Knowledge System — Installer${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_FILE="${SCRIPT_DIR}/memory_core.py"
MIGRATE_FILE="${SCRIPT_DIR}/migrate_v5.py"

if [[ ! -f "$CORE_FILE" ]]; then
    echo -e "${RED}❌ memory_core.py not found in ${SCRIPT_DIR}${NC}"
    exit 1
fi

# ─── Detect OWUI setup ───
CONTAINER_NAME="${OWUI_CONTAINER:-openwebui}"
DATA_DIR_HOST="${OWUI_DATA_DIR:-/opt/openwebui/data}"
DATA_DIR_CONTAINER="/app/backend/data"

echo -e "${YELLOW}Step 1: Detecting Open WebUI installation...${NC}"
echo ""

# Check if Docker container exists
if docker inspect "$CONTAINER_NAME" &>/dev/null; then
    echo -e "${GREEN}  ✅ Docker container '${CONTAINER_NAME}' found${NC}"
    INSTALL_MODE="docker"
else
    echo -e "${YELLOW}  ⚠️  No Docker container '${CONTAINER_NAME}' found.${NC}"
    echo -e "     Assuming bare-metal install at: ${DATA_DIR_HOST}"
    INSTALL_MODE="baremetal"
fi
echo ""

# ─── Copy memory_core.py ───
echo -e "${YELLOW}Step 2: Installing memory_core.py...${NC}"

if [[ "$INSTALL_MODE" == "docker" ]]; then
    # Try host-side copy first (if the data dir is bind-mounted)
    if [[ -d "$DATA_DIR_HOST" ]]; then
        cp "$CORE_FILE" "${DATA_DIR_HOST}/memory_core.py"
        echo -e "${GREEN}  ✅ Copied to ${DATA_DIR_HOST}/memory_core.py${NC}"
    else
        # Fall back to docker cp
        docker cp "$CORE_FILE" "${CONTAINER_NAME}:${DATA_DIR_CONTAINER}/memory_core.py"
        echo -e "${GREEN}  ✅ Copied via docker cp to ${DATA_DIR_CONTAINER}/memory_core.py${NC}"
    fi
else
    if [[ -d "$DATA_DIR_HOST" ]]; then
        cp "$CORE_FILE" "${DATA_DIR_HOST}/memory_core.py"
        echo -e "${GREEN}  ✅ Copied to ${DATA_DIR_HOST}/memory_core.py${NC}"
    else
        echo -e "${RED}  ❌ Data directory ${DATA_DIR_HOST} does not exist${NC}"
        echo -e "     Set OWUI_DATA_DIR to the correct path and re-run."
        exit 1
    fi
fi
echo ""

# ─── Run migration script ───
echo -e "${YELLOW}Step 3: Running migration script...${NC}"
echo ""

if [[ "$INSTALL_MODE" == "docker" ]]; then
    docker cp "$MIGRATE_FILE" "${CONTAINER_NAME}:/tmp/migrate_v5.py" 2>/dev/null || true
    # Run non-interactively (migration is read-only except for optional cleanup)
    # NOTE: use -i (not -it) — a TTY is not available when stdin is redirected
    docker exec -i "$CONTAINER_NAME" python3 /tmp/migrate_v5.py <<< "n" || \
    echo -e "${YELLOW}  ⚠️  Migration script could not run automatically. Run manually:${NC}"
    echo -e "     docker exec -it ${CONTAINER_NAME} python3 /tmp/migrate_v5.py"
else
    python3 "$MIGRATE_FILE" <<< "n" || \
    echo -e "${YELLOW}  ⚠️  Migration script could not run. Run manually:${NC}"
    echo -e "     python3 ${MIGRATE_FILE}"
fi
echo ""

# ─── Print next steps ───
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  Installation complete!${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${GREEN}Next steps — create the Filter and Tool in OWUI admin:${NC}"
echo ""
echo -e "  ${YELLOW}1. Create Filter:${NC}"
echo -e "     Admin → Functions → New Filter"
echo -e "     Name: Memory Filter v5.1"
echo -e "     Paste contents of memory_filter.py"
echo -e "     Toggle: ON, Global: YES"
echo ""
echo -e "  ${YELLOW}2. Create Tool:${NC}"
echo -e "     Admin → Functions → New Tool"
echo -e "     Name: Memory Tool v5"
echo -e "     Paste contents of memory_tool.py"
echo -e "     Toggle: ON, Global: YES"
echo ""
echo -e "  ${YELLOW}3. Attach Tool to your model:${NC}"
echo -e "     Workspace → Models → edit your model"
echo -e "     Enable 'Memory Tool v5' in the tools list"
echo ""
echo -e "  ${YELLOW}4. Install Python dependencies (if not already installed):${NC}"
if [[ "$INSTALL_MODE" == "docker" ]]; then
    echo -e "     docker exec -it ${CONTAINER_NAME} pip install numpy sentence-transformers tiktoken"
else
    echo -e "     pip install numpy sentence-transformers tiktoken"
fi
echo ""
echo -e "  ${YELLOW}5. Set the system prompt:${NC}"
echo -e "     Admin → Settings → General (or per-model in Workspace → Models)"
echo -e "     Paste the contents of 'system prompt.md'"
echo -e "     Adapt the identity, paths, and project references to your deployment"
echo ""
echo -e "  ${YELLOW}6. Test:${NC}"
echo -e '     Ask: "What do you remember about me?"'
echo -e "     → Should trigger memory_search tool call"
echo ""
echo -e "  ${YELLOW}7. Deactivate any old memory filters${NC} (if upgrading from v4.x)"
echo -e "     legacy/function-persistent-memory-V4.1 is NOT compatible with v5 — do not"
echo -e "     run it alongside the new Filter/Tool pair"
echo ""
