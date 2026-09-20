"""Runtime settings. Everything configurable, nothing secret, in one place."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
"""Repository root, resolved from this file so the API runs from any cwd."""


class Settings(BaseSettings):
    """Loaded from the environment, or from a local .env in development."""

    model_config = SettingsConfigDict(
        env_prefix="ASTRA_",
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = Field(default="development")
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    data_dir: Path = Field(default=REPO_ROOT / "data")
    db_path: Path = Field(default=REPO_ROOT / "data" / "astra.sqlite")
    cors_origins: str = Field(
        default=(
            "http://localhost:3000,http://127.0.0.1:3000,"
            "http://localhost:3100,http://127.0.0.1:3100"
        ),
        description=(
            "Comma-separated origins allowed to call the API. The deployed frontend "
            "origin is set here per environment; the map fetches its terrain, overlay "
            "and road layers directly from the API, so an unlisted origin means a "
            "blank map rather than a wrong one. localhost and 127.0.0.1 are the "
            "same machine but different origins to a browser, so both are listed."
        ),
    )
    validate_fixtures_on_startup: bool = Field(default=True)

    # --- LLM narration layer. Optional by design (CLAUDE.md section 10). ---
    llm_enabled: bool = Field(default=False)
    llm_provider: str = Field(default="none")
    llm_model: str = Field(default="")
    llm_api_key: str = Field(default="")

    @property
    def fixtures_dir(self) -> Path:
        return self.data_dir / "fixtures"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def provenance_path(self) -> Path:
        return self.data_dir / "provenance.json"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def llm_mode(self) -> str:
        """``connected`` only when a key is actually present. Never assumed."""
        return "connected" if (self.llm_enabled and self.llm_api_key) else "template"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
