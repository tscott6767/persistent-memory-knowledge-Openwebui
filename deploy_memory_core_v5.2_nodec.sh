#!/bin/bash
# deploy_memory_core_v5.2_nodec.sh — deploy memory v5.2 patch
# Run on your Proxmox host as root.
#
# Uses pct pull/push to move the patch file between LXCs (no HTTP file
# serving dependency).

set -euo pipefail

LXC_SRC=111   # LXC hosting this repo checkout (adjust to your setup)
LXC_DST=110   # LXC running the Open WebUI stack (adjust to your setup)

echo "── Pulling patch script from LXC $LXC_SRC..."
pct pull $LXC_SRC /projects/memory-rebuild/memory_core_v5.2_patch.py /tmp/memory_core_v5.2_patch.py

echo "── Pushing patch script into LXC $LXC_DST..."
pct push $LXC_DST /tmp/memory_core_v5.2_patch.py /tmp/memory_core_v5.2_patch.py

echo "── Copying into openwebui container + applying patch..."
pct exec $LXC_DST -- bash -c "docker cp /tmp/memory_core_v5.2_patch.py openwebui:/tmp/ && docker exec openwebui python3 /tmp/memory_core_v5.2_patch.py"

echo "── Restarting openwebui..."
pct exec $LXC_DST -- docker restart openwebui

echo
echo "Deployed. Verify after next chat message (from LXC 110 or via pct exec):"
echo "  pct exec $LXC_DST -- bash -c 'docker logs openwebui --tail 50 | grep search_'"
echo "(expect: search_memories: selected=N embed_ms=... scan_ms=... mean_sim=...)"
echo
echo "If the patch printed ABORT or COMPILE FAILED, nothing was changed —"
echo "paste its output back into the chat so anchors can be corrected."
