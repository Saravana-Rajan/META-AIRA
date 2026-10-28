#!/usr/bin/env bash
# Shared safety helpers for every infra script. Source it, do not run it:
#   source "$(dirname "${BASH_SOURCE[0]}")/guard.sh"
#
# The GCP project gemini-enterprise-475109 also hosts OTHER LIVE APPS. AIRA-on-Meta may only
# touch resources whose names are in ALLOWED_NAMES below. Every target name passes through
# guard_name before any command runs; a forbidden or unknown name aborts the script.

# ---- the only targets AIRA-on-Meta uses ----
# shellcheck disable=SC2034  # read by the scripts that source this file
readonly META_PROJECT="gemini-enterprise-475109"
# shellcheck disable=SC2034
readonly META_REGION="asia-south1"
readonly META_RUN_SERVICE="meta-aira"
readonly META_HOSTING_SITE="meta-aira"
readonly META_FIRESTORE_DB="meta-aira"
readonly META_AR_REPO="meta-aira"
readonly META_SECRET_MODEL_KEY="meta-aira-model-api-key"
readonly META_SECRET_APP_KEY="meta-aira-app-key"
readonly META_SECRET_READER2_KEY="meta-aira-reader2-key"
readonly META_RUN_SA_NAME="meta-aira-run"
readonly META_RUN_SA="${META_RUN_SA_NAME}@${META_PROJECT}.iam.gserviceaccount.com"

readonly ALLOWED_NAMES=(
  "$META_RUN_SERVICE" "$META_HOSTING_SITE" "$META_FIRESTORE_DB" "$META_AR_REPO"
  "$META_SECRET_MODEL_KEY" "$META_SECRET_APP_KEY" "$META_SECRET_READER2_KEY"
  "$META_RUN_SA_NAME" "$META_RUN_SA"
)

# ---- existing resources of other apps: NEVER reference, modify, deploy to, or delete ----
readonly FORBIDDEN_NAMES=(
  # Cloud Run
  "aira-live" "build-with-ai-constutency"
  # Firestore databases
  "aira" "(default)" "default"
  # Firebase Hosting sites
  "aira-shop" "gemini-enterprise-475109" "sarvik"
  # Artifact Registry repos
  "cloud-run-source-deploy" "gcr.io"
  # Secrets
  "DATABASE_URL" "db-password" "django-secret-key" "email-host-password" "jwt-signing-key"
)
# Any name matching one of these globs is forbidden too (e.g. every aira-* secret).
readonly FORBIDDEN_GLOBS=("aira-*" "*.gcr.io" "gcr.io/*")

die() { echo "ABORT: $*" >&2; exit 1; }

# guard_name NAME [KIND]: abort unless NAME is an AIRA-on-Meta resource and not a forbidden one.
guard_name() {
  local name="${1:-}" kind="${2:-resource}" f g ok=0
  [[ -n "$name" ]] || die "empty $kind name"
  for f in "${FORBIDDEN_NAMES[@]}"; do
    [[ "$name" == "$f" ]] && die "$kind '$name' belongs to another live app (forbidden list)."
  done
  for g in "${FORBIDDEN_GLOBS[@]}"; do
    # shellcheck disable=SC2053  # glob match is intended
    [[ "$name" == $g ]] && die "$kind '$name' matches forbidden pattern '$g'."
  done
  for f in "${ALLOWED_NAMES[@]}"; do
    [[ "$name" == "$f" ]] && ok=1
  done
  [[ $ok -eq 1 ]] || die "$kind '$name' is not an AIRA-on-Meta resource (allowed: ${ALLOWED_NAMES[*]})."
}

# guard_project PROJECT / guard_region REGION: only the one project and region are allowed.
# (The project id is a forbidden *Hosting site* name, so it is checked here, not via guard_name.)
guard_project() { [[ "${1:-}" == "$META_PROJECT" ]] || die "project must be $META_PROJECT (got '${1:-}')."; }
guard_region() { [[ "${1:-}" == "$META_REGION" ]] || die "region must be $META_REGION (got '${1:-}')."; }

# guard_all: check every name used by the scripts in one go (call at the top of each script).
guard_all() {
  guard_project "$META_PROJECT"
  guard_region "$META_REGION"
  local n
  for n in "${ALLOWED_NAMES[@]}"; do guard_name "$n"; done
}

# run CMD...: print the command, then run it unless DRY_RUN=1.
run() {
  printf '+ ' >&2
  printf '%q ' "$@" >&2
  printf '\n' >&2
  if [[ "${DRY_RUN:-0}" == "1" ]]; then return 0; fi
  "$@"
}

# confirm "message": ask for an explicit "yes". Skipped in DRY_RUN; ASSUME_YES=1 for non-interactive use.
confirm() {
  local msg="${1:-Proceed?}" reply
  if [[ "${DRY_RUN:-0}" == "1" ]]; then echo "[dry-run] would ask: $msg" >&2; return 0; fi
  if [[ "${ASSUME_YES:-0}" == "1" ]]; then echo "$msg -> yes (ASSUME_YES=1)" >&2; return 0; fi
  read -r -p "$msg Type 'yes' to continue: " reply
  [[ "$reply" == "yes" ]] || die "cancelled by user."
}

banner() {
  echo "==============================================================" >&2
  echo " $*" >&2
  echo " project=$META_PROJECT  region=$META_REGION  DRY_RUN=${DRY_RUN:-0}" >&2
  echo "==============================================================" >&2
}

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "'$1' is not installed."; }
