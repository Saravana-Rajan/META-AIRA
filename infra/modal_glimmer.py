"""Muse Glimmer 30B (Meta open weights) on Modal, served by vLLM as an OpenAI-compatible API.

AIRA uses it as the independent *second reader* for cash and invoices: a different model from a
different weights family reads the same frame, and the backend only commits when both agree.

One-time setup (Modal account with credits):
    uv run --with modal modal setup
    uv run --with modal modal secret create meta-aira-glimmer VLLM_API_KEY=<long-random-string>
    # Only if the Hugging Face repo is gated:
    #   add HF_TOKEN=<token> to the same secret.

Deploy (NOT run by the infra scripts; do it by hand when credits are available):
    uv run --with modal modal deploy infra/modal_glimmer.py

Modal prints a URL like https://<workspace>--meta-aira-glimmer-serve.modal.run . Then set, for the
backend (local .env, or `READER2_BASE_URL`/`READER2_MODEL` env on infra/deploy_backend.sh plus the
`meta-aira-reader2-key` secret):
    READER2_BASE_URL=https://<workspace>--meta-aira-glimmer-serve.modal.run/v1
    READER2_MODEL=muse-glimmer-30b
    READER2_API_KEY=<the same VLLM_API_KEY>

Smoke test:
    curl -H "Authorization: Bearer $READER2_API_KEY" "$READER2_BASE_URL/models"

Cost control: scale to zero after ~5 idle minutes; max 1 container. The first request after idle
pays a cold start (weights are cached in a Modal Volume, so no re-download).
"""

import os
import subprocess

import modal

APP_NAME = "meta-aira-glimmer"
# Pinned: the second reader is an integrity control, so its weights must not change underneath us.
MODEL_ID = "meta-models/Muse-Glimmer-30B"
MODEL_REVISION = "cec94fcfd6512f2cfe708138c32533b346ea4f69"  # HF commit of 2026-10-22
SERVED_NAME = "muse-glimmer-30b"
GPU = os.environ.get("GLIMMER_GPU", "H100")  # or "A100-80GB"
VLLM_VERSION = os.environ.get("GLIMMER_VLLM_VERSION", "0.11.0")  # bump if the model needs a newer vLLM
PORT = 8000
MINUTES = 60

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(f"vllm=={VLLM_VERSION}", "huggingface_hub[hf_transfer]")
    .env(
        {
            "HF_HUB_ENABLE_HF_TRANSFER": "1",
            "HF_HOME": "/root/.cache/huggingface",
            "VLLM_USAGE_STATS_SERVER": "",
            "DO_NOT_TRACK": "1",
        }
    )
)

hf_cache = modal.Volume.from_name("meta-aira-glimmer-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("meta-aira-glimmer-vllm-cache", create_if_missing=True)

app = modal.App(APP_NAME)


@app.function(
    image=image,
    gpu=GPU,
    secrets=[modal.Secret.from_name("meta-aira-glimmer")],  # provides VLLM_API_KEY (and optional HF_TOKEN)
    volumes={"/root/.cache/huggingface": hf_cache, "/root/.cache/vllm": vllm_cache},
    scaledown_window=5 * MINUTES,  # scale to zero after 5 idle minutes
    min_containers=0,
    max_containers=1,
    timeout=20 * MINUTES,
)
@modal.concurrent(max_inputs=16)
@modal.web_server(port=PORT, startup_timeout=15 * MINUTES)
def serve() -> None:
    if not os.environ.get("VLLM_API_KEY"):
        raise RuntimeError("VLLM_API_KEY missing: create the Modal secret 'meta-aira-glimmer'.")
    cmd = [
        "vllm",
        "serve",
        MODEL_ID,
        "--revision",
        MODEL_REVISION,
        "--served-model-name",
        SERVED_NAME,
        "--host",
        "0.0.0.0",
        "--port",
        str(PORT),
        "--max-model-len",
        "16384",
        "--limit-mm-per-prompt",
        '{"image": 1}',
        "--gpu-memory-utilization",
        "0.92",
        "--uvicorn-log-level",
        "info",
    ]
    # vLLM reads VLLM_API_KEY from the environment and requires "Authorization: Bearer <key>".
    print("starting:", " ".join(cmd))
    subprocess.Popen(cmd)
