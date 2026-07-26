#!/bin/bash
# Quick health check for all incident-pilot Docker services.
# Usage: ./scripts/healthcheck.sh

check() {
    local name="$1" url="$2"
    resp=$(curl -s -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || echo "000")
    if [[ "$resp" == "200" ]]; then
        echo "OK    $name ($resp) -> $url"
    else
        echo "FAIL  $name ($resp) -> $url"
    fi
}

check "flask-generator" "http://localhost:5001/health"
check "prometheus"      "http://localhost:9090/-/ready"
check "loki"            "http://localhost:3100/ready"
check "grafana"         "http://admin:admin@localhost:3000/api/health"
check "incident-pilot"  "http://localhost:7860/"

echo ""
docker ps --format 'table {{.Names}}\t{{.Status}}'
