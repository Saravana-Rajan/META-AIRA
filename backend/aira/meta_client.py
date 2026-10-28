"""Clients for the Meta Model API (OpenAI-compatible) and the optional open-weights second reader."""
from functools import lru_cache

from openai import AsyncOpenAI

from aira.config import get_settings


@lru_cache
def meta_client() -> AsyncOpenAI:
    s = get_settings()
    return AsyncOpenAI(base_url=s.meta_base_url, api_key=s.model_api_key or "mock", max_retries=2, timeout=30)


@lru_cache
def reader2_client() -> AsyncOpenAI:
    """Client for the second reader: an open-weights endpoint if configured, else Meta Model API."""
    s = get_settings()
    if s.reader2_base_url:
        return AsyncOpenAI(base_url=s.reader2_base_url, api_key=s.reader2_api_key, max_retries=2, timeout=30)
    return meta_client()
