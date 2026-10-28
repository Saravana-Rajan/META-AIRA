"""Settings. The ONLY place model IDs and endpoints are written."""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Role = Literal["agent", "read", "verify", "sam", "asr"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_prefix="", extra="ignore")

    # Meta Model API. The OpenAI SDK does not read MODEL_API_KEY by itself, so we pass it explicitly.
    model_api_key: str = ""
    meta_base_url: str = "https://api.meta.ai/v1"
    meta_asr_url: str = "wss://api.meta.ai/v1/asr/realtime"

    # Mock mode: every model call returns canned output and nothing is billed.
    # On by default when there is no key, so a fresh checkout runs without credentials.
    aira_mock: bool | None = None

    # Model per role. "contributor" variants are ~10x cheaper; switch to standard for the recorded demo.
    model_agent: str = "muse-spark-1.3-contributor"
    model_read: str = "muse-spark-1.3-contributor"
    # The independent second reader: a different model generation so the two reads do not fail alike.
    model_verify: str = "muse-spark-1.2-contributor"
    model_sam: str = "sam-3.1"
    model_asr: str = "muse-voice-transcribe-1.0"
    reasoning_effort: Literal["minimal", "low", "medium", "high"] = "minimal"

    # Optional open-weights second reader (Muse Spark Open on Baseten, Glimmer on Modal, ...).
    # When set, it replaces model_verify; any OpenAI-compatible endpoint works.
    reader2_base_url: str = ""
    reader2_api_key: str = ""
    reader2_model: str = ""

    # Storage.
    store: Literal["memory", "firestore"] = "memory"
    gcp_project: str = "gemini-enterprise-475109"
    firestore_db: str = "meta-aira"
    shop_id: str = "aira-cricket"

    # Voice.
    asr_languages: list[str] = Field(default_factory=lambda: ["Tamil", "English", "Hindi"])

    # Shared secret between the phone app / dashboard and the backend (empty = open, for local dev).
    app_key: str = ""
    # Browser origins allowed to call the API (the hosted web apps plus local Vite dev servers).
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "https://meta-aira.web.app",
            "https://meta-aira.firebaseapp.com",
            "http://localhost:5173",
            "http://localhost:5174",
        ]
    )

    @property
    def mock(self) -> bool:
        return self.aira_mock if self.aira_mock is not None else not self.model_api_key

    def model_for(self, role: Role) -> str:
        return {
            "agent": self.model_agent,
            "read": self.model_read,
            "verify": self.reader2_model or self.model_verify,
            "sam": self.model_sam,
            "asr": self.model_asr,
        }[role]


@lru_cache
def get_settings() -> Settings:
    return Settings()
