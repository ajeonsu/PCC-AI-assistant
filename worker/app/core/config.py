"""Typed settings loaded from the monorepo root .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Application settings shared across Drive sync and ingestion scripts."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    google_drive_root_folder_id: str = ""
    google_oauth_credentials_path: str = "worker/credentials.json"
    google_oauth_token_path: str = "worker/token.json"

    database_url: str = ""
    embedding_provider: str = "gemini"
    embedding_dim: int = 768

    groq_api_key: str = ""
    gemini_api_key: str = ""

    def resolve_path(self, configured_path: str) -> Path:
        """Resolve relative paths against the repository root."""
        path = Path(configured_path)
        if path.is_absolute():
            return path
        return REPO_ROOT / path

    @property
    def credentials_file(self) -> Path:
        return self.resolve_path(self.google_oauth_credentials_path)

    @property
    def token_file(self) -> Path:
        return self.resolve_path(self.google_oauth_token_path)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
