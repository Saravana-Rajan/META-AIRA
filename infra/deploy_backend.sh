#!/usr/bin/env bash
# Build the backend image and deploy Cloud Run service "meta-aira" (asia-south1).
#
#   DRY_RUN=1 infra/deploy_backend.sh   # print commands only
#   infra/deploy_backend.sh             # Cloud Build (default)
#   BUILDER=docker infra/deploy_backend.sh   # local docker build + push instead
#
# Optional env passed through to the service:
#   AIRA_MOCK (default 0), READER2_BASE_URL, READER2_MODEL, MODEL_AGENT, MODEL_READ, MODEL_VERIFY,
#   REASONING_EFFORT, SHOP_ID.  TAG overrides the image tag (default: git short sha or timestamp).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=infra/guard.sh
source "$HERE/guard.sh"

P="$META_PROJECT"
R="$META_REGION"
SERVICE="$META_RUN_SERVICE"
REPO="$META_AR_REPO"
guard_all
guard_project "$P"
guard_region "$R"
guard_name "$SERVICE" "Cloud Run service"
guard_name "$REPO" "Artifact Registry repo"
guard_name "$META_RUN_SA" "service account"

require_cmd gcloud
BUILDER="${BUILDER:-cloudbuild}"
TAG="${TAG:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d-%H%M%S)}"
IMAGE="${R}-docker.pkg.dev/${P}/${REPO}/${SERVICE}:${TAG}"

# Second-reader secret is optional: map it only if it exists and has a version (read-only check).
SECRETS="MODEL_API_KEY=${META_SECRET_MODEL_KEY}:latest,APP_KEY=${META_SECRET_APP_KEY}:latest"
if [[ "${DRY_RUN:-0}" == "1" ]] || \
   gcloud secrets versions describe latest --secret="$META_SECRET_READER2_KEY" --project="$P" >/dev/null 2>&1; then
  SECRETS="${SECRETS},READER2_API_KEY=${META_SECRET_READER2_KEY}:latest"
fi

# Env vars, joined with a custom delimiter (^|^) so values may contain commas.
ENV_VARS=(
  "STORE=firestore"
  "FIRESTORE_DB=${META_FIRESTORE_DB}"
  "GCP_PROJECT=${P}"
  "AIRA_MOCK=${AIRA_MOCK:-0}"
)
for v in READER2_BASE_URL READER2_MODEL MODEL_AGENT MODEL_READ MODEL_VERIFY REASONING_EFFORT SHOP_ID; do
  if [[ -n "${!v:-}" ]]; then ENV_VARS+=("${v}=${!v}"); fi
done
ENV_ARG="^|^$(IFS='|'; echo "${ENV_VARS[*]}")"

banner "DEPLOY BACKEND -> Cloud Run '$SERVICE'"
cat >&2 <<EOF
 image           : $IMAGE  (builder: $BUILDER)
 service         : $SERVICE  region $R
 service account : $META_RUN_SA
 env             : ${ENV_VARS[*]}
 secrets         : $SECRETS
 scaling         : min 0, max 2; session affinity; timeout 3600s (WebSockets)
EOF
confirm "Build and deploy Cloud Run service '$SERVICE' in $P/$R?"

# 1. Build and push to the meta-aira registry ---------------------------------------------------
case "$BUILDER" in
  cloudbuild)
    run gcloud builds submit "$ROOT/backend" --tag="$IMAGE" --region="$R" --project="$P"
    ;;
  docker)
    require_cmd docker
    run gcloud auth configure-docker "${R}-docker.pkg.dev" --quiet --project="$P"
    run docker build --platform=linux/amd64 -t "$IMAGE" "$ROOT/backend"
    run docker push "$IMAGE"
    ;;
  *) die "BUILDER must be 'cloudbuild' or 'docker' (got '$BUILDER')." ;;
esac

# 2. Deploy ---------------------------------------------------------------------------------
run gcloud run deploy "$SERVICE" \
  --image="$IMAGE" \
  --project="$P" \
  --region="$R" \
  --platform=managed \
  --service-account="$META_RUN_SA" \
  --port=8080 \
  --session-affinity \
  --timeout=3600 \
  --min-instances=0 \
  --max-instances=2 \
  --concurrency=40 \
  --cpu=1 \
  --memory=1Gi \
  --set-env-vars="$ENV_ARG" \
  --set-secrets="$SECRETS" \
  --labels=app=meta-aira \
  --allow-unauthenticated

if [[ "${DRY_RUN:-0}" != "1" ]]; then
  URL="$(gcloud run services describe "$SERVICE" --project="$P" --region="$R" --format='value(status.url)')"
  echo "Deployed: $URL   (health: $URL/health)" >&2
fi
