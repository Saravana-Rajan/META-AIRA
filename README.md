# AIRA on Meta

AIRA turns a blind person into a shop owner: serving customers, taking payment, counting cash and checking supplier deliveries, all by voice from one phone, with Meta's models as their eyes and back office.

Built from scratch during the **Meta Global AI Developer Hackathon** (Oct 26 – Nov 4, 2026), Indie track, on Muse Spark, Muse Voice Transcribe, SAM 3.1 and Meta open-weight models.

## What it does

The owner talks to AIRA in Tamil, English or Hindi. AIRA:

- **Serves customers:** answers price and stock questions from the shop catalog.
- **Takes payment:** builds the sale, computes the total, reads the cash the customer hands over through the phone camera, and tells the owner the exact change. UPI payments are confirmed by the owner.
- **Counts cash:** two independent model readers must agree on every note before AIRA says a number.
- **Checks supplier deliveries:** reads the invoice, counts the delivered items with SAM 3.1, and says whether they match.
- **Keeps the books:** every sale, payment and delivery is written to a ledger only after the owner says "yes". A live owner dashboard shows stock, sales and the last item count with its boxes.

## Built during the hackathon

All code in this repository was written between **Oct 26 and Nov 4, 2026**, during the hackathon. The git history is the record of that work. Ideas carried over from the earlier AIRA design (see Safety principles) were re-implemented from scratch here; no earlier code was copied.

## Meta models used and how

| Need | Model | How AIRA uses it |
|---|---|---|
| Agent brain | **Muse Spark 1.3** (`muse-spark-1.3`, dev: `-contributor`) | Dialogue, tool calling and vision over the OpenAI-compatible Meta API, low reasoning effort for voice latency |
| Speech to text | **Muse Voice Transcribe 1.0** | Realtime WebSocket ASR with end-of-speech detection, Tamil/English/Hindi |
| Counting delivered items | **SAM 3.1** | Text prompt (e.g. "cricket ball") returns boxes and masks; Python counts them |
| Second reader for cash and invoices | **Muse Spark 1.2**, or open-weights **Muse Glimmer 30B** on Modal (vLLM) | Reads the same frame independently; AIRA only speaks a number when both readers agree |
| Text to speech | Browser `speechSynthesis` | Meta has no TTS model |

All model IDs live in one file, `backend/aira/config.py`. Without a key, the backend runs in **mock mode**: every model call returns canned output and nothing is billed.

## Architecture

```
Phone PWA (browser)                         Cloud Run "meta-aira" (FastAPI)
 |- mic -> PCM16 chunks ---- WebSocket ----> VoiceBridge --> Muse Voice Transcribe (wss realtime)
 |- camera -> JPEG frame on demand -------->      | final transcript
 |- speaker <-- text -> browser TTS <------- Agent (Muse Spark 1.3, tool calling)
 '- status / tool events                          | tool calls
                                                  v
                                 Tools (deterministic Python)
                                  |- catalog / sale / stock / ledger  -> Firestore DB "meta-aira"
                                  |- read_cash    -> Spark + second reader, must agree
                                  |- count_items  -> SAM 3.1 (boxes -> count)
                                  '- read_invoice -> Spark structured JSON (+ second reader)

Owner dashboard (Firebase Hosting "meta-aira" /dashboard/) <-- /api/snapshot, /api/stream (SSE)

Optional: Modal "meta-aira-glimmer" (vLLM, Muse Glimmer 30B, OpenAI-compatible) <-- second reader
```

## Run locally

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
cp .env.example .env            # leave MODEL_API_KEY empty -> mock mode, nothing billed

# Backend (mock mode, in-memory store)
cd backend
uv sync
AIRA_MOCK=1 uv run uvicorn aira.main:app --reload --port 8080 --ws websockets
# health check: curl http://localhost:8080/health
uv run pytest -q                # offline tests
uv run ruff check .

# Phone app (new terminal)
cd frontend/app
npm install
VITE_BACKEND_URL=http://localhost:8080 VITE_BACKEND_WS=ws://localhost:8080/ws npm run dev

# Owner dashboard (new terminal)
cd frontend/dashboard
npm install
VITE_BACKEND_URL=http://localhost:8080 npm run dev
```

The camera and microphone need a secure context: use `localhost` on the same machine, or an HTTPS tunnel for a real phone.

Docker: `docker build -t meta-aira backend/ && docker run -p 8080:8080 -e AIRA_MOCK=1 meta-aira`

## Deploy

Everything goes to GCP project `gemini-enterprise-475109`, which also hosts other live apps. AIRA-on-Meta uses **only** these new resources:

| Resource | Name |
|---|---|
| Cloud Run (asia-south1) | `meta-aira` |
| Firebase Hosting site | `meta-aira` (https://meta-aira.web.app) |
| Firestore database | `meta-aira` |
| Artifact Registry | `meta-aira` |
| Secrets | `meta-aira-model-api-key`, `meta-aira-app-key`, `meta-aira-reader2-key` |
| Runtime service account | `meta-aira-run@gemini-enterprise-475109.iam.gserviceaccount.com` |

Every script sources `infra/guard.sh`, passes `--project`, `--region` and resource names explicitly, aborts if any target name belongs to another app, prints the target and asks for `yes` before acting. `DRY_RUN=1` prints every command without running anything.

```bash
DRY_RUN=1 infra/bootstrap.sh       # preview
infra/bootstrap.sh                 # once: APIs, registry, Firestore DB, secrets (hidden prompt), service account, Hosting site
infra/deploy_backend.sh            # Cloud Build -> Cloud Run meta-aira (BUILDER=docker for a local build)
infra/deploy_frontend.sh           # builds app + dashboard with the Cloud Run URL, deploys to Hosting meta-aira
```

- Cloud Run: session affinity, 3600 s timeout (WebSockets), 0 to 2 instances, `STORE=firestore`, `FIRESTORE_DB=meta-aira`, keys mounted from Secret Manager as `MODEL_API_KEY`, `APP_KEY`, `READER2_API_KEY`.
- Hosting: phone app at `/`, dashboard at `/dashboard/` (`infra/firebase.json`, target `meta-aira` only).
- **No secret is baked into the frontend.** Only public URLs (`VITE_BACKEND_URL`, `VITE_BACKEND_WS`) go into the bundles. The app key reaches the phone through a link you share privately, `https://meta-aira.web.app/?key=<APP_KEY>`; the app reads it once and keeps it in localStorage. Do not post that link publicly.
- Optional second reader on Modal: `uv run --with modal modal deploy infra/modal_glimmer.py`, then set `READER2_BASE_URL=https://<workspace>--meta-aira-glimmer-serve.modal.run/v1`, `READER2_MODEL=muse-glimmer-30b` and the `meta-aira-reader2-key` secret (see the file header).

CI (`.github/workflows/ci.yml`) runs ruff and pytest in mock mode and builds both frontends. There is no deploy workflow; deploys are manual.

## Safety principles

- **The model proposes, code commits.** The model never writes money or stock. Every change goes propose, spoken "yes", commit.
- **Numbers come only from tools.** Totals, change and counts are computed in Python; a spoken-number guard checks that every number AIRA says came from a tool result.
- **Two readers must agree** on cash and invoices; otherwise AIRA says it is not sure and looks again.
- **Idempotent writes:** each commit carries an idempotency key, so a repeated "yes" never double-charges.
- **Honest limits:** AIRA never describes or identifies people and never judges whether a note is fake.
- **No surprise bills:** mock mode is the default without a key; tests and CI never call a model.

## License

[MIT](LICENSE) © 2026 Saravana Rajan
