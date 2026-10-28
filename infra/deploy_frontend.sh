#!/usr/bin/env bash
# Build the phone app and the dashboard with the Cloud Run URL baked in, then deploy both to
# Firebase Hosting site "meta-aira":  app at /,  dashboard at /dashboard/.
#
#   DRY_RUN=1 infra/deploy_frontend.sh
#   BACKEND_URL=https://meta-aira-xxxx.a.run.app infra/deploy_frontend.sh   # skip the URL lookup
#
# Vite env baked into both builds (public URLs only): VITE_BACKEND_URL (https://...), VITE_BACKEND_WS (wss://.../ws).
# No secret is ever baked in: share the app link with ?key=<APP_KEY> privately instead.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=infra/guard.sh
source "$HERE/guard.sh"

P="$META_PROJECT"
R="$META_REGION"
SITE="$META_HOSTING_SITE"
guard_all
guard_name "$SITE" "Hosting site"
guard_name "$META_RUN_SERVICE" "Cloud Run service"

require_cmd npm
[[ "${DRY_RUN:-0}" == "1" ]] || require_cmd firebase

# Backend URL: env override, else read-only lookup of the meta-aira service.
if [[ -z "${BACKEND_URL:-}" ]]; then
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    BACKEND_URL="https://meta-aira-DRYRUN.a.run.app"
  else
    require_cmd gcloud
    BACKEND_URL="$(gcloud run services describe "$META_RUN_SERVICE" \
      --project="$P" --region="$R" --format='value(status.url)')"
  fi
fi
[[ "$BACKEND_URL" == https://* ]] || die "BACKEND_URL must start with https:// (got '$BACKEND_URL')."
WS_URL="wss://${BACKEND_URL#https://}/ws"

OUT="$HERE/dist"   # Hosting "public" dir (gitignored via dist/)

banner "DEPLOY FRONTEND -> Firebase Hosting site '$SITE'"
cat >&2 <<EOF
 backend         : $BACKEND_URL
 websocket       : $WS_URL
 app             : frontend/app       -> https://$SITE.web.app/
 dashboard       : frontend/dashboard -> https://$SITE.web.app/dashboard/
 config          : infra/firebase.json (hosting target '$SITE' only)
EOF
confirm "Build and deploy both apps to Hosting site '$SITE' in $P?"

# Only PUBLIC URLs are baked into the bundles. Never put a secret in a VITE_ variable: it ships in the JS.
# The app key reaches the phone via a privately shared link (https://meta-aira.web.app/?key=...),
# which the app reads once and keeps in localStorage.
unset APP_KEY VITE_APP_KEY
export VITE_BACKEND_URL="$BACKEND_URL"
export VITE_BACKEND_WS="$WS_URL"

build_app() { # build_app DIR BASE
  local dir="$1" base="$2"
  [[ -f "$dir/package.json" ]] || die "$dir/package.json not found."
  if [[ -f "$dir/package-lock.json" ]]; then
    run npm --prefix "$dir" ci
  else
    run npm --prefix "$dir" install
  fi
  run npm --prefix "$dir" run build -- --base="$base"
}

build_app "$ROOT/frontend/app" "/"
build_app "$ROOT/frontend/dashboard" "/dashboard/"

run rm -rf "$OUT"
run mkdir -p "$OUT/dashboard"
run cp -R "$ROOT/frontend/app/dist/." "$OUT/"
run cp -R "$ROOT/frontend/dashboard/dist/." "$OUT/dashboard/"

# Deploy only the meta-aira hosting target (mapped to site meta-aira in infra/.firebaserc).
cd "$HERE"
run firebase deploy --only "hosting:$SITE" --project "$P" --config "$HERE/firebase.json" --non-interactive

echo "Done: https://$SITE.web.app/  and  https://$SITE.web.app/dashboard/" >&2
