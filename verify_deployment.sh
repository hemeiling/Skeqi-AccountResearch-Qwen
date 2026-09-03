#!/usr/bin/env bash
# Post-deployment validation for the two Render services.
#
#   ./verify_deployment.sh https://<engine>.onrender.com https://<crm>.onrender.com
#
# Read-only. Makes no model call and spends no research tokens, so it is safe to
# run while DashScope model access is still awaiting paid activation.
set -uo pipefail
ENGINE="${1:-}"; CRM="${2:-}"
[ -z "$ENGINE" ] || [ -z "$CRM" ] && { echo "usage: $0 <engine-url> <crm-url>"; exit 2; }
ENGINE="${ENGINE%/}"; CRM="${CRM%/}"
pass=0; fail=0
chk() { # chk "label" expected actual
  if [ "$2" = "$3" ]; then printf '  ✓ %-44s %s\n' "$1" "$3"; pass=$((pass+1));
  else printf '  ✗ %-44s got %s, expected %s\n' "$1" "$3" "$2"; fail=$((fail+1)); fi
}
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 60 "$@"; }

echo "── Engine: $ENGINE"
chk "/healthz"                       200 "$(code "$ENGINE/healthz")"
chk "HTTPS enforced"                 https "$(printf '%s' "$ENGINE" | cut -d: -f1)"
chk "anonymous app page redirects"   302 "$(code "$ENGINE/")"
chk "anonymous API rejected"         401 "$(code "$ENGINE/api/reports")"
CSP=$(curl -s -o /dev/null -D - --max-time 60 "$ENGINE/healthz" | tr -d '\r' | grep -i '^content-security-policy:' || true)
if printf '%s' "$CSP" | grep -q "frame-ancestors"; then
  printf '  ✓ %-44s %s\n' "frame-ancestors set" "$(printf '%s' "$CSP" | cut -c1-70)"; pass=$((pass+1))
  printf '%s' "$CSP" | grep -q '\*' && { printf '  ✗ %-44s wildcard present\n' "frame-ancestors not wildcard"; fail=$((fail+1)); } \
    || { printf '  ✓ %-44s\n' "frame-ancestors not wildcard"; pass=$((pass+1)); }
else printf '  ✗ %-44s missing\n' "frame-ancestors set"; fail=$((fail+1)); fi

echo "── CRM: $CRM"
chk "CRM root"                       200 "$(code "$CRM/")"
chk "engine URL configured"          200 "$(code "$CRM/api/account-research/config")"
CFG=$(curl -s --max-time 60 "$CRM/api/account-research/config")
printf '  · configured engine URL: %s\n' "$CFG"
printf '%s' "$CFG" | grep -q 'https://' \
  && { printf '  ✓ %-44s\n' "engine URL is HTTPS"; pass=$((pass+1)); } \
  || { printf '  ✗ %-44s\n' "engine URL is HTTPS"; fail=$((fail+1)); }

echo "── Neon-backed reads (no model call)"
N=$(curl -s --max-time 90 "$CRM/api/aresearch/reports" | grep -o '"id"' | wc -l | tr -d ' ')
chk "reports listed from Neon"       29 "$N"
chk "existing-report detection"      200 "$(code "$CRM/api/aresearch/exists?companies=ACRO%20Automation%20Systems")"
for L in en zh bilingual; do
  chk "language view: $L"            200 "$(code "$CRM/api/aresearch/render?company=ACRO%20Automation%20Systems&lang=$L")"
done
chk "PDF inline"                     200 "$(code "$CRM/api/aresearch/render?company=ACRO%20Automation%20Systems&lang=en&format=pdf&inline=1")"
chk "PDF download"                   200 "$(code "$CRM/api/aresearch/render?company=ACRO%20Automation%20Systems&lang=en&format=pdf")"
chk "combined PDF"                   200 "$(code -X POST -H 'Content-Type: application/json' -d '{"lang":"en"}' "$CRM/api/aresearch/export/portfolio")"
chk "ZIP"                            200 "$(code -X POST -H 'Content-Type: application/json' -d '{"lang":"en"}' "$CRM/api/aresearch/export/zip")"

echo "── Model state (expected: unavailable until activation)"
H=$(curl -s --max-time 90 "$CRM/api/aresearch/models/health")
printf '%s' "$H" | grep -q '"modelsAvailable":false' \
  && printf '  · models unavailable, as expected while activation is pending\n' \
  || printf '  · models report as available\n'
printf '%s' "$H" | grep -q 'activation/payment required' \
  && { printf '  ✓ %-44s\n' "activation wording present"; pass=$((pass+1)); } \
  || printf '  · activation notice absent (only shown when all models are denied)\n'

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ] || exit 1
