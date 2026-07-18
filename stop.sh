#!/bin/bash
# ============================================================================
#  stop.sh — Stop all IncidentPilot Docker containers
# ============================================================================
# Usage:
#   ./stop.sh            — stop containers (keep volumes)
#   ./stop.sh --volumes  — stop containers AND delete volumes (data loss!)
#   ./stop.sh --help     — print this message
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

case "${1:-}" in
    --help|-h)
        sed -n '3,10p' "$0"
        exit 0
        ;;
    --volumes)
        info "Stopping containers and deleting volumes..."
        docker compose down -v
        ok "Containers stopped and volumes deleted."
        ;;
    "")
        info "Stopping containers (volumes preserved)..."
        docker compose down
        ok "All containers stopped. Volumes preserved."
        echo ""
        echo "  To also delete volumes (removes Loki/Grafana data):"
        echo "    $0 --volumes"
        ;;
    *)
        echo "Usage: $0 [--volumes] [--help]"
        exit 1
        ;;
esac
