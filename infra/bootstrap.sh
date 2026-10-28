#!/usr/bin/env bash
# One-time setup of the AIRA-on-Meta cloud resources in gemini-enterprise-475109.
# Creates ONLY: Artifact Registry "meta-aira", Firestore DB "meta-aira", secrets meta-aira-*,
# service account meta-aira-run, Firebase Hosting site "meta-aira". Touches nothing else.
#
#   DRY_RUN=1 infra/bootstrap.sh     # print every command, change nothing
#   infra/bootstrap.sh               # asks for confirmation, then for each secret value (hidden input)
#
# Re-running is safe: existing meta-aira resources are skipped (secrets get a new version only if you
# type a value).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=infra/guard.sh
source "$HERE/guard.sh"

P="$META_PROJECT"
R="$META_REGION"
guard_all

require_cmd gcloud
[[ "${DRY_RUN:-0}" == "1" ]] || require_cmd firebase

banner "BOOTSTRAP AIRA-on-Meta"
cat >&2 <<EOF
 Will enable APIs and create (if missing):
   Artifact Registry : $META_AR_REPO ($R, docker)
   Firestore DB      : $META_FIRESTORE_DB ($R, native mode)
   Secrets           : $META_SECRET_MODEL_KEY, $META_SECRET_APP_KEY, $META_SECRET_READER2_KEY
   Service account   : $META_RUN_SA
                       roles/datastore.user (conditioned on database $META_FIRESTORE_DB)
                       roles/secretmanager.secretAccessor on the three meta-aira secrets only
   Hosting site      : $META_HOSTING_SITE
EOF
confirm "Create these in project $P?"

# exists CMD...: read-only describe; true if the resource exists. Always false in DRY_RUN.
exists() {
  [[ "${DRY_RUN:-0}" == "1" ]] && return 1
  "$@" >/dev/null 2>&1
}

# 1. APIs ------------------------------------------------------------------------------------
run gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  firebasehosting.googleapis.com \
  --project="$P"

# 2. Artifact Registry -----------------------------------------------------------------------
guard_name "$META_AR_REPO" "Artifact Registry repo"
if exists gcloud artifacts repositories describe "$META_AR_REPO" --location="$R" --project="$P"; then
  echo "Artifact Registry $META_AR_REPO exists, skipping." >&2
else
  run gcloud artifacts repositories create "$META_AR_REPO" \
    --repository-format=docker --location="$R" \
    --description="AIRA on Meta backend images" --project="$P"
fi

# 3. Firestore database (named, never the default) --------------------------------------------
guard_name "$META_FIRESTORE_DB" "Firestore database"
if exists gcloud firestore databases describe --database="$META_FIRESTORE_DB" --project="$P"; then
  echo "Firestore database $META_FIRESTORE_DB exists, skipping." >&2
else
  run gcloud firestore databases create \
    --database="$META_FIRESTORE_DB" --location="$R" --type=firestore-native --project="$P"
fi

# 4. Secrets (values read from stdin without echo, piped straight to gcloud) --------------------
put_secret() {
  local name="$1" label="$2" value
  guard_name "$name" "secret"
  if ! exists gcloud secrets describe "$name" --project="$P"; then
    run gcloud secrets create "$name" --replication-policy=automatic \
      --labels=app=meta-aira --project="$P"
  fi
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[dry-run] would prompt for $label and add it as a new version of $name" >&2
    return 0
  fi
  read -r -s -p "$label for $name (hidden; empty = keep current/skip): " value
  echo >&2
  if [[ -z "$value" ]]; then
    echo "No value for $name, skipping new version." >&2
    return 0
  fi
  echo "+ gcloud secrets versions add $name --data-file=- --project=$P   (value from stdin)" >&2
  printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- --project="$P" >/dev/null
  unset value
}
put_secret "$META_SECRET_MODEL_KEY" "Meta Model API key"
put_secret "$META_SECRET_APP_KEY" "Shared app key (phone/dashboard <-> backend)"
put_secret "$META_SECRET_READER2_KEY" "Second-reader API key (Glimmer/Modal VLLM_API_KEY; empty if unused)"

# 5. Runtime service account -----------------------------------------------------------------
guard_name "$META_RUN_SA_NAME" "service account"
if exists gcloud iam service-accounts describe "$META_RUN_SA" --project="$P"; then
  echo "Service account $META_RUN_SA exists, skipping." >&2
else
  run gcloud iam service-accounts create "$META_RUN_SA_NAME" \
    --display-name="AIRA on Meta Cloud Run runtime" --project="$P"
fi

# Firestore access, limited by IAM condition to the meta-aira database only.
run gcloud projects add-iam-policy-binding "$P" \
  --member="serviceAccount:$META_RUN_SA" \
  --role=roles/datastore.user \
  --condition="expression=resource.name==\"projects/$P/databases/$META_FIRESTORE_DB\",title=meta-aira-db-only,description=Only the meta-aira Firestore database" \
  --project="$P"

# Secret access, bound per secret (never project-wide).
for s in "$META_SECRET_MODEL_KEY" "$META_SECRET_APP_KEY" "$META_SECRET_READER2_KEY"; do
  guard_name "$s" "secret"
  run gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:$META_RUN_SA" \
    --role=roles/secretmanager.secretAccessor \
    --project="$P"
done

# 6. Firebase Hosting site ---------------------------------------------------------------------
guard_name "$META_HOSTING_SITE" "Hosting site"
run firebase hosting:sites:create "$META_HOSTING_SITE" --project "$P" \
  || echo "Hosting site $META_HOSTING_SITE may already exist; continuing." >&2

echo "Bootstrap done. Next: infra/deploy_backend.sh, then infra/deploy_frontend.sh" >&2
