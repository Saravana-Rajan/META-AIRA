"""Tests never reach a real model: force mock mode and blank keys, whatever a local .env says."""
import pytest

from aira import config, limits, meta_client


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("AIRA_MOCK", "1")
    monkeypatch.setenv("MODEL_API_KEY", "")
    monkeypatch.setenv("APP_KEY", "")
    monkeypatch.setenv("READER2_BASE_URL", "")
    monkeypatch.setenv("STORE", "memory")
    config.get_settings.cache_clear()
    meta_client.meta_client.cache_clear()
    meta_client.reader2_client.cache_clear()
    limits.guard.__init__()  # fresh spend counters per test (the guard is a process-wide singleton)
    yield
    config.get_settings.cache_clear()
    meta_client.meta_client.cache_clear()
    meta_client.reader2_client.cache_clear()
