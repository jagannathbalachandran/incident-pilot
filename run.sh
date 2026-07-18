#!/bin/bash
# ============================================================================
#  IncidentPilot — One-Click Run Script
# ============================================================================
# Usage:
#   ./run.sh              — interactive: choose actions via menu
#   ./run.sh full         — full setup → build → start → test
#   ./run.sh quick        — quick test (skip Docker build, use static data)
#   ./run.sh up           — just bring Docker stack up + health check
#   ./run.sh down         — take Docker stack down
#   ./run.sh test         — run unit tests only
#
# Prerequisites:
#   - Python 3.11
#   - uv (https://docs.astral.sh/uv/getting-started/installation/)
#   - Docker (for monitoring stack)
#   - GROQ_API_KEY in .env file or environment
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ---- Colors ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

UV="uv"
PYTHON="python3.11"
if ! command -v "$PYTHON" &>/dev/null; then
    PYTHON="python3"
fi
UV_RUN="$UV run python"

# ============================================================================
#  Helper Functions
# ============================================================================

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()    { echo -e "${RED}[FAIL]${NC}  $*"; }
header()  { echo -e "\n${BOLD}$*${NC}\n"; }

check_prereqs() {
    local ok=true

    if command -v "$PYTHON" &>/dev/null; then
        pyver=$("$PYTHON" --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
        if awk "BEGIN {exit !($pyver >= 3.11)}"; then
            ok "Python $pyver+ found"
        else
            warn "Python $pyver found (3.11+ recommended)"
        fi
    else
        fail "Python 3.11+ not found"
        ok=false
    fi

    if command -v "$UV" &>/dev/null; then
        ok "uv found: $(uv --version)"
    else
        fail "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
        ok=false
    fi

    if command -v docker &>/dev/null; then
        ok "Docker found: $(docker --version)"
    else
        fail "Docker not found"
        ok=false
    fi

    if docker compose version &>/dev/null; then
        ok "Docker Compose found"
    else
        fail "Docker Compose not found"
        ok=false
    fi

    if [[ -n "${GROQ_API_KEY:-}" ]]; then
        ok "GROQ_API_KEY is set"
        return 0
    fi
    if [[ -f "$SCRIPT_DIR/.env" ]] && grep -q 'GROQ_API_KEY' "$SCRIPT_DIR/.env" 2>/dev/null; then
        ok "GROQ_API_KEY found in .env"
        export GROQ_API_KEY=$(grep 'GROQ_API_KEY' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2-)
        return 0
    fi

    fail "GROQ_API_KEY not set. Create a .env file"
    return 1
}

setup_venv() {
    if [[ -d ".venv" ]]; then
        info "Virtual environment already exists"
    else
        info "Creating virtual environment..."
        uv venv --python "$PYTHON"
    fi

    # Sync dependencies FIRST (creates/updates .venv)
    info "Syncing dependencies..."
    uv sync --group test
    ok "Dependencies synced"

    # Install torch SECOND (special index URL, into existing .venv)
    if ! uv run python -c "import torch" 2>/dev/null; then
        info "Installing PyTorch (CPU)..."
        uv pip install torch --index-url https://download.pytorch.org/whl/cpu --quiet
        ok "PyTorch installed"
    else
        ok "PyTorch already installed"
    fi
}

build_vectorstore() {
    if [[ -d "$SCRIPT_DIR/synthetic-data/vectorstore" ]]; then
        info "Vector store already exists"
        info "Rebuild? (y/N) "
        if [[ "${1:-n}" != "y" ]]; then
            info "Skipping (use --rebuild or option 13 to force)"
            return
        fi
    fi
    $UV_RUN src/ingestion.py
    ok "Vector store ready"
}

start_docker() {
    info "Starting Docker stack..."
    docker compose up -d 2>&1 | tail -3
    ok "Docker stack starting..."

    info "Waiting for all services to be healthy..."
    for i in $(seq 1 30); do
        sleep 2
        local all_ok=true
        for url in \
            "http://localhost:9090/-/ready" \
            "http://localhost:3100/ready" \
            "http://localhost:5001/health" \
            "http://admin:admin@localhost:3000/api/health"; do
            resp=$(curl -s --connect-timeout 3 --max-time 5 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || echo "000")
            [[ "$resp" != "200" ]] && all_ok=false && break
        done
        if $all_ok; then
            echo ""
            echo "  ┌────────────────────────────────────────────────────────┐"
            echo "  │  All 4 services healthy!                              │"
            echo "  │  FastAPI  → http://localhost:5001/docs                │"
            echo "  │  Grafana  → http://localhost:3000 (admin/admin)       │"
            echo "  │  Prometheus → http://localhost:9090                   │"
            echo "  │  Loki     → http://localhost:3100                     │"
            echo "  └────────────────────────────────────────────────────────┘"
            return 0
        fi
    done
    warn "Some services may not be healthy yet. Check: docker compose ps"
}

stop_docker() {
    info "Stopping Docker stack..."
    docker compose down
    ok "Docker stack stopped"
}

check_docker_health() {
    for url in \
        "http://localhost:9090/-/ready" \
        "http://localhost:3100/ready" \
        "http://localhost:5001/health" \
        "http://admin:admin@localhost:3000/api/health"; do
        resp=$(curl -s -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || echo "000")
        name=$(echo "$url" | sed 's|http://[^/]*/||; s|/.*||')
        [[ "$resp" == "200" ]] && ok "  $name: $resp" || warn "  $name: $resp"
    done
}

trigger_incident() {
    local kind="${1:-pool}"
    info "Triggering $kind incident..."
    curl -s -X POST "http://localhost:5001/api/incidents/$kind/trigger" \
        -H 'Content-Type: application/json' -d '{"auto_resolve":true}' |
        $UV_RUN -m json.tool
    echo ""
    info "Monitoring state..."
    for i in 3 6 9 12 15; do
        sleep 3
        echo "--- tick ~$i ---"
        curl -s "http://localhost:5001/api/incidents/state" |
            $UV_RUN -m json.tool 2>/dev/null || echo "(waiting...)"
    done
}

trigger_random() {
    info "Triggering random incident..."
    curl -s -X POST "http://localhost:5001/api/incidents/trigger-random" |
        $UV_RUN -m json.tool
}

run_agent() {
    header "Running IncidentPilot Agent"
    TOKENIZERS_PARALLELISM=false $UV_RUN src/incident_pilot.py
}

run_tests() {
    local filter="${1:-}"
    if [[ -n "$filter" ]]; then
        header "Running: $filter"
        TOKENIZERS_PARALLELISM=false $UV_RUN -m pytest "tests/$filter" -v
    else
        header "Running All Tests (131 total)"
        TOKENIZERS_PARALLELISM=false $UV_RUN -m pytest tests/ -v
    fi
}

launch_ui() {
    header "Launching Gradio UI"
    echo "  Open http://127.0.0.1:7860 in your browser"
    echo "  Press Ctrl+C to stop"
    echo ""
    TOKENIZERS_PARALLELISM=false $UV_RUN src/app.py
}

# ============================================================================
#  Interactive Menu
# ============================================================================

show_menu() {
    clear 2>/dev/null || true
    echo ""
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║         🚑  IncidentPilot Control Panel         ║"
    echo "  ╠══════════════════════════════════════════════════╣"
    echo "  ║                                                  ║"
    echo "  ║  Setup                                            ║"
    echo "  ║    1. Full: setup → vector store → Docker → test ║"
    echo "  ║    2. Quick: setup → vector store → test → agent ║"
    echo "  ║                                                  ║"
    echo "  ║  Docker Stack                                    ║"
    echo "  ║    3. Start Docker stack                         ║"
    echo "  ║    4. Stop Docker stack                          ║"
    echo "  ║    5. Check Docker health                        ║"
    echo "  ║                                                  ║"
    echo "  ║  Tests                                            ║"
    echo "  ║    6.  Run all 131 tests                         ║"
    echo "  ║    7.  Run FastAPI tests (64)                    ║"
    echo "  ║    8.  Run guardrail tests (24)                  ║"
    echo "  ║    9.  Run data-layer tests (43)                 ║"
    echo "  ║                                                  ║"
    echo "  ║  Development                                      ║"
    echo "  ║    10. Launch Gradio UI                          ║"
    echo "  ║    11. Run agent (CLI)                           ║"
    echo "  ║    12. Sync deps (uv sync)                       ║"
    echo "  ║    13. Rebuild vector store                      ║"
    echo "  ║                                                  ║"
    echo "  ║  Incidents (requires Docker)                      ║"
    echo "  ║    14. Trigger pool exhaustion                   ║"
    echo "  ║    15. Trigger cache failover                    ║"
    echo "  ║    16. Trigger fraud outage                      ║"
    echo "  ║    17. Trigger random incident                   ║"
    echo "  ║                                                  ║"
    echo "  ║  0. Exit                                         ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo ""
    read -p "  Choose an option [0-17]: " choice
    echo ""

    case "$choice" in
        1)  cmd_full ;;
        2)  cmd_quick ;;
        3)  start_docker ;;
        4)  stop_docker ;;
        5)  check_docker_health ;;
        6)  run_tests ;;
        7)  run_tests "test_fastapi_generator.py" ;;
        8)  run_tests "test_incident_pilot.py" ;;
        9)  run_tests "test_query_logs.py" ;;
        10) launch_ui ;;
        11) run_agent ;;
        12) setup_venv ;;
        13) setup_venv; build_vectorstore "y" ;;
        14) trigger_incident "pool" ;;
        15) trigger_incident "cache" ;;
        16) trigger_incident "fraud" ;;
        17) trigger_random ;;
        0)  echo "Goodbye!"; exit 0 ;;
        *)  warn "Invalid option"; sleep 1 ;;
    esac
    pause
}

pause() {
    echo ""
    read -p "  Press Enter to return to menu... " _
    show_menu
}

# ============================================================================
#  Command modes
# ============================================================================

cmd_full() {
    header "IncidentPilot — Full Run"
    check_prereqs || exit 1
    setup_venv
    build_vectorstore "y"
    start_docker
    run_tests
    trigger_incident "pool"
    ok "Full run complete!"
    echo "  • Launch UI:  TOKENIZERS_PARALLELISM=false $UV_RUN src/app.py"
    echo "  • Open Grafana: http://localhost:3000 (admin/admin)"
}

cmd_quick() {
    header "IncidentPilot — Quick Test"
    check_prereqs || exit 1
    setup_venv
    build_vectorstore
    run_tests
    run_agent
}

# ============================================================================
#  Main
# ============================================================================

case "${1:-menu}" in
    full)       cmd_full ;;
    quick)      cmd_quick ;;
    up)         start_docker ;;
    down)       stop_docker ;;
    health)     check_docker_health ;;
    trigger)    trigger_incident "${2:-pool}" ;;
    agent)      run_agent ;;
    test)       run_tests ;;
    sync)       setup_venv ;;
    ui)         launch_ui ;;
    rebuild-vs) setup_venv; build_vectorstore "y" ;;
    menu|"")    show_menu ;;
    *)
        echo "Usage: $0 {full|quick|up|down|health|trigger|agent|test|sync|ui|rebuild-vs}"
        echo ""
        echo "  Or run without arguments for the interactive menu."
        exit 1
        ;;
esac
