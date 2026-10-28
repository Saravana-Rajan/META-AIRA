"""Tests never reach a real model: force mock mode and blank keys, whatever a local .env says."""
import pytest

from aira import config, meta_client


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
    yield
    config.get_settings.cache_clear()
    meta_client.meta_client.cache_clear()
    meta_client.reader2_client.cache_clear()
