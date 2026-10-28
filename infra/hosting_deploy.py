"""Deploy a static directory to Firebase Hosting site "meta-aira" via the Hosting REST API.

No firebase CLI needed: uses an OAuth access token (HOSTING_TOKEN, else `gcloud auth print-access-token`).

    python infra/hosting_deploy.py infra/dist            # dist/ has index.html (app) and dashboard/

Refuses any site other than meta-aira, so it can never touch aira-shop, sarvik or the SPT site.
"""
import gzip
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

PROJECT = "gemini-enterprise-475109"
SITE = "meta-aira"
API = "https://firebasehosting.googleapis.com/v1beta1"

CONFIG = {
    "rewrites": [
        {"glob": "/dashboard/**", "path": "/dashboard/index.html"},
        {"glob": "**", "path": "/index.html"},
    ],
    "headers": [
        {"glob": "**/*.@(js|css|png|svg|webmanifest)", "headers": {"Cache-Control": "public, max-age=3600"}},
        {"glob": "**/@(index.html|sw.js)", "headers": {"Cache-Control": "no-cache"}},
    ],
}


# A personal gcloud login needs a quota project header; a service-account token (CI) does not,
# and sending it there would require an extra serviceusage permission.
USER_LOGIN = "HOSTING_TOKEN" not in os.environ


def token() -> str:
    t = os.environ.get("HOSTING_TOKEN")
    if t:
        return t
    return subprocess.run(
        ["gcloud", "auth", "print-access-token"], check=True, capture_output=True, text=True
    ).stdout.strip()


def call(method: str, url: str, tok: str, body: bytes | dict | None = None, ctype: str = "application/json"):
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {tok}")
    if USER_LOGIN:
        req.add_header("x-goog-user-project", PROJECT)
    if data is not None:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def main(root: str, site: str = SITE) -> None:
    if site != SITE:
        sys.exit(f"refusing to deploy to site {site!r}: only {SITE!r} is allowed")
    base = Path(root)
    if not (base / "index.html").is_file():
        sys.exit(f"{base}/index.html not found")
    tok = token()

    files: dict[str, bytes] = {}
    hashes: dict[str, str] = {}
    for p in sorted(base.rglob("*")):
        if p.is_file():
            gz = gzip.compress(p.read_bytes(), mtime=0)
            h = hashlib.sha256(gz).hexdigest()
            files[h] = gz
            hashes["/" + p.relative_to(base).as_posix()] = h

    version = call("POST", f"{API}/sites/{site}/versions", tok, {"config": CONFIG})["name"]
    pop = call("POST", f"{API}/{version}:populateFiles", tok, {"files": hashes})
    upload_url = pop.get("uploadUrl")
    for h in pop.get("uploadRequiredHashes", []):
        call("POST", f"{upload_url}/{h}", tok, files[h], ctype="application/octet-stream")
    call("PATCH", f"{API}/{version}?update_mask=status", tok, {"status": "FINALIZED"})
    rel = call("POST", f"{API}/sites/{site}/releases?versionName={version}", tok, {})
    print(f"released {len(hashes)} files to https://{site}.web.app ({rel.get('name', version)})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "infra/dist")
