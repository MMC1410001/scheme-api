#!/usr/bin/env bash
# Security scans for the Scheme API. Reports go to qa/security/reports/.
#
#   qa/security/run_scans.sh            # all three scans (API must be running for ZAP)
#   qa/security/run_scans.sh sast deps  # just some of them
#
# Env: BASE_URL (default http://localhost:8000), API_KEY (sent as X-API-Key to ZAP).
#
#   sast  bandit     — Python static analysis of app/ and ingestion/
#   deps  pip-audit  — known CVEs in the locked dependencies
#   zap   OWASP ZAP  — active API scan driven by /openapi.json (needs Docker)
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REPORTS="$ROOT/qa/security/reports"
BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-}"
SCANS=("${@:-sast deps zap}")
SCANS=(${SCANS[*]})
status=0
mkdir -p "$REPORTS"

run_sast() {
  echo "== bandit (static analysis)"
  uvx bandit -q -r "$ROOT/app" "$ROOT/ingestion" -f txt -o "$REPORTS/bandit.txt"
  local rc=$?
  cat "$REPORTS/bandit.txt"
  return $rc
}

run_deps() {
  echo "== pip-audit (dependency CVEs)"
  (cd "$ROOT" && uv export --quiet --no-hashes --no-dev --no-emit-project > "$REPORTS/requirements.txt")
  uvx pip-audit -r "$REPORTS/requirements.txt" --progress-spinner off 2>&1 | tee "$REPORTS/pip-audit.txt"
  return "${PIPESTATUS[0]}"
}

run_zap() {
  echo "== OWASP ZAP API scan against $BASE_URL"
  # Inside the container, "localhost" is the container itself.
  local target="${BASE_URL/localhost/host.docker.internal}"
  target="${target/127.0.0.1/host.docker.internal}"
  local auth=()
  if [[ -n "$API_KEY" ]]; then
    auth=(-z "-config replacer.full_list(0).description=apikey -config replacer.full_list(0).enabled=true \
-config replacer.full_list(0).matchtype=REQ_HEADER -config replacer.full_list(0).matchstr=X-API-Key \
-config replacer.full_list(0).regex=false -config replacer.full_list(0).replacement=$API_KEY")
  fi
  docker run --rm -v "$REPORTS:/zap/wrk:rw" ghcr.io/zaproxy/zaproxy:stable \
    zap-api-scan.py -t "$target/openapi.json" -f openapi \
    -r zap-report.html -w zap-report.md -J zap-report.json "${auth[@]}"
  # zap-api-scan exit codes: 0 = no issues, 1 = FAIL alerts, 2 = WARN alerts only
  local rc=$?
  [[ $rc -eq 2 ]] && { echo "ZAP: warnings only (see zap-report.md)"; return 0; }
  return $rc
}

for scan in "${SCANS[@]}"; do
  case "$scan" in
    sast) run_sast || status=1 ;;
    deps) run_deps || status=1 ;;
    zap)  run_zap  || status=1 ;;
    *) echo "unknown scan: $scan (use sast, deps, zap)"; status=2 ;;
  esac
done

echo
[[ $status -eq 0 ]] && echo "Security scans passed. Reports: $REPORTS" || echo "Security scans found issues. Reports: $REPORTS"
exit $status
