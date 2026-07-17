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
NC='\033[0m' # No Color

VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="python3.11"
if ! command -v "$PYTHON" &>/dev/null; then
    PYTHON="python3"
fi

# ============================================================================
#  Helper Functions
# ============================================================================

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()    { echo -e "${RED}[FAIL]${NC}  $*"; }

check_prereqs() {
    local ok=true

    # Python 3.11+
    if command -v "$PYTHON" &>/dev/null; then
        pyver=$("$PYTHON" --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
        if awk "BEGIN {exit !($pyver >= 3.11)}"; then
            ok "Python $pyver+ found"
        else
            warn "Python $pyver found (3.11+ recommended for PyTorch compatibility)"
        fi
    else
        fail "Python 3.11+ not found. Install with: brew install python@3.11"
        ok=false
    fi

    # Docker
    if command -v docker &>/dev/null; then
        ok "Docker found: $(docker --version)"
    else
        fail "Docker not found. Install from https://docs.docker.com/get-docker/"
        ok=false
    fi

    # Docker Compose
    if docker compose version &>/dev/null; then
        ok "Docker Compose found"
    else
        fail "Docker Compose not found"
        ok=false
    fi

    # GROQ_API_KEY
    if [[ -n "${GROQ_API_KEY:-}" ]]; then
        ok "GROQ_API_KEY is set in environment"
    elif [[ -f "$SCRIPT_DIR/.env" ]] && grep -q 'GROQ_API_KEY' "$SCRIPT_DIR/.env" 2>/dev/null; then
        ok "GROQ_API_KEY found in .env file"
        export GROQ_API_KEY=$(grep 'GROQ_API_KEY' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2-)
    else
        fail "GROQ_API_KEY not set. Create a .env file with: GROQ_API_KEY=your_key_here"
        ok=false
    fi

    $ok || return 1
}

setup_venv() {
    if [[ -d "$VENV_DIR" ]]; then
        info "Virtual environment already exists at $VENV_DIR"
    else
        info "Creating virtual environment with $PYTHON..."
        "$PYTHON" -m venv "$VENV_DIR"
        ok "Virtual environment created"
    fi

    source "$VENV_DIR/bin/activate"

    # Install torch first (special index URL)
    if ! python -c "import torch" 2>/dev/null; then
        info "Installing PyTorch (CPU)..."
        pip install torch --index-url https://download.pytorch.org/whl/cpu --quiet
        ok "PyTorch installed"
    else
        ok "PyTorch already installed"
    fi

    # Install requirements
    info "Installing Python dependencies..."
    pip install -r requirements.txt
    ok "Dependencies installed"
}

build_vectorstore() {
    if [[ -d "$SCRIPT_DIR/synthetic-data/vectorstore" ]]; then
        info "Vector store already exists at synthetic-data/vectorstore/"
        info "Rebuild? (y/N) "
        if [[ "${1:-n}" == "y" ]]; then
            "$VENV_DIR/bin/python" src/ingestion.py
            ok "Vector store rebuilt"
        else
            info "Skipping vector store build (use 'run.sh rebuild-vs' to force rebuild)"
        fi
    else
        info "Building vector store..."
        "$VENV_DIR/bin/python" src/ingestion.py
        ok "Vector store built"
    fi
}

start_docker() {
    info "Starting Docker monitoring stack..."
    docker compose up -d 2>&1 | tail -3
    ok "Docker stack starting..."

    info "Waiting for all 4 services to be healthy..."
    local max_attempts=30
    for i in $(seq 1 $max_attempts); do
        sleep 2
        local all_ok=true
        for url in \
            "http://localhost:9090/-/ready" \
            "http://localhost:3100/ready" \
            "http://localhost:5001/health" \
            "http://admin:admin@localhost:3000/api/health"; do
            resp=$(curl -s --connect-timeout 3 --max-time 5 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || echo "000")
            if [[ "$resp" != "200" ]]; then
                all_ok=false
                break
            fi
        done
        if $all_ok; then
            echo ""
            echo "  ┌────────────────────────────────────────────────────────┐"
            echo "  │  All 4 Docker services are healthy!                   │"
            echo "  │                                                        │"
            echo "  │  Flask Generator  → http://localhost:5001/health      │"
            echo "  │  Prometheus       → http://localhost:9090              │"
            echo "  │  Loki             → http://localhost:3100              │"
            echo "  │  Grafana          → http://localhost:3000 (admin/admin) │"
            echo "  └────────────────────────────────────────────────────────┘"
            return 0
        fi
    done
    warn "Some services may not be healthy yet. Check with: docker compose ps"
}

stop_docker() {
    info "Stopping Docker stack..."
    docker compose down
    ok "Docker stack stopped"
}

check_docker_health() {
    info "Checking Docker service health..."
    for url in \
        "http://localhost:9090/-/ready" \
        "http://localhost:3100/ready" \
        "http://localhost:5001/health" \
        "http://admin:admin@localhost:3000/api/health"; do
        resp=$(curl -s -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || echo "000")
        name=$(echo "$url" | sed 's|http://[^/]*/||; s|/.*||')
        if [[ "$resp" == "200" ]]; then
            ok "  $name: HTTP $resp"
        else
            warn "  $name: HTTP $resp"
        fi
    done
}

trigger_incident() {
    local kind="${1:-pool}"
    info "Triggering $kind incident..."
    curl -s -X POST "http://localhost:5001/api/incidents/$kind/trigger" | python3 -m json.tool
    echo ""
    info "Monitoring state (every 3s for 15s)..."
    for i in 3 6 9 12 15; do
        sleep 3
        curl -s "http://localhost:5001/api/incidents/state" | python3 -m json.tool 2>/dev/null || echo "(waiting...)"
    done
}

run_agent() {
    info "Running IncidentPilot agent..."
    echo ""
    TOKENIZERS_PARALLELISM=false "$VENV_DIR/bin/python" src/incident_pilot.py
}

run_tests() {
    info "Running all tests..."
    "$VENV_DIR/bin/python" -m pytest tests/ -v
}

launch_ui() {
    info "Launching Gradio UI at http://127.0.0.1:7860"
    echo ""
    echo "  Press Ctrl+C to stop the server"
    echo ""
    cd src && TOKENIZERS_PARALLELISM=false ../.venv/bin/python app.py
}

show_menu() {
    echo ""
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║              IncidentPilot Menu                  ║"
    echo "  ╠══════════════════════════════════════════════════╣"
    echo "  ║  1. Full setup + build + start + test           ║"
    echo "  ║  2. Quick test (static data, no Docker)          ║"
    echo "  ║  3. Start Docker stack                          ║"
    echo "  ║  4. Stop Docker stack                           ║"
    echo "  ║  5. Run unit tests                               ║"
    echo "  ║  6. Launch Gradio UI                             ║"
    echo "  ║  7. Trigger pool exhaustion incident            ║"
    echo "  ║  8. Run agent (CLI)                              ║"
    echo "  ║  9. Open Grafana dashboards                     ║"
    echo "  ║  0. Exit                                         ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo ""
    read -p "  Choose an option [0-9]: " choice
    echo ""
    case "$choice" in
        1) cmd_full ;;
        2) cmd_quick ;;
        3) start_docker ;;
        4) stop_docker ;;
        5) run_tests ;;
        6) launch_ui ;;
        7) trigger_incident "pool" ;;
        8) run_agent ;;
        9) echo "Opening Grafana..."; open http://localhost:3000 2>/dev/null || echo "Visit http://localhost:3000 (admin/admin)" ;;
        0) exit 0 ;;
        *) warn "Invalid option"; show_menu ;;
    esac
}

# ============================================================================
#  Command modes
# ============================================================================

cmd_full() {
    echo ""
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║        IncidentPilot — Full Run                  ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo ""

    check_prereqs || exit 1
    setup_venv
    build_vectorstore "y"
    start_docker
    run_tests
    trigger_incident "pool"
    echo ""
    ok "Full run complete!"
    echo "  Next steps:"
    echo "    • Launch UI:  cd src && TOKENIZERS_PARALLELISM=false ../.venv/bin/python app.py"
    echo "    • Open Grafana: http://localhost:3000 (admin/admin)"
    echo "    • Stop stack:  docker compose down"
}

cmd_quick() {
    echo ""
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║    IncidentPilot — Quick Test (static data)      ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo ""

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
    ui)         launch_ui ;;
    rebuild-vs) setup_venv; build_vectorstore "y" ;;
    menu|"")    show_menu ;;
    *)
        echo "Usage: $0 {full|quick|up|down|health|trigger|agent|test|ui|rebuild-vs}"
        exit 1
        ;;
esac
