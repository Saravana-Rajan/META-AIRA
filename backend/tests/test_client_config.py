"""Web apps fetch the shared demo key from Cloud Run at startup (never from GitHub or the bundle)."""
from fastapi.testclient import TestClient

from aira.config import Settings
from aira.main import create_app
from aira.store import MemoryStore


def test_client_config_returns_app_key_without_auth():
    settings = Settings(aira_mock=True, app_key="demo-key", model_api_key="")
    app = create_app(settings=settings, store=MemoryStore("t"))
    with TestClient(app) as tc:
        r = tc.get("/api/client-config")
        assert r.status_code == 200
        assert r.json() == {"app_key": "demo-key", "mock": True}
        # the key it hands out then unlocks the protected API
        assert tc.get("/api/snapshot", headers={"x-aira-key": "demo-key"}).status_code == 200
