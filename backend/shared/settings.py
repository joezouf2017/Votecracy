"""Every environment variable this service reads, in one typed place.

The previous arrangement was four modules calling `os.environ.get` with their
own inline defaults. That works until a name is misspelled: `CORS_ORIGIN`
instead of `CORS_ORIGINS` does not raise, it silently returns the localhost
default, and every request in production then fails CORS with nothing in the
logs pointing at the cause. Collecting them here does not stop a typo in the
deployment config — nothing can — but it does mean there is one file to read to
learn what this service can be configured with, and values are parsed and
validated at startup rather than at first use.

Defaults are the local docker-compose setup, so a developer needs no
environment at all.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to this file, and to every directory above it up to the repository
# root. A bare `env_file=".env"` resolves against the working directory, and
# this project has three: the container starts in /app, pytest runs from the
# repo root, alembic from backend/. The failure mode is the whole reason this
# module exists — the file silently does not load and every key reads empty,
# with nothing in the logs saying so.
#
# The candidate list is walked rather than hardcoded to a depth because a
# fixed `parent.parent` broke the moment this module moved from backend/ into
# backend/shared/ during the package split. It went unnoticed until an API
# client complained its key was missing, which is the same silent failure one
# level up.
_HERE = Path(__file__).resolve().parent
_ENV_FILES = tuple(d / ".env" for d in (_HERE, *_HERE.parents))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILES, extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://votecracy:votecracy@localhost:5432/votecracy",
        description="Durable store. Migrations and the vote log.",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Atomic dedupe and tally on the vote path.",
    )
    # Deliberately a plain string rather than list[str]. pydantic-settings
    # treats a list field as complex and JSON-decodes the raw environment value
    # *before* any validator runs, so `CORS_ORIGINS=http://localhost:5173`
    # raises a JSONDecodeError at startup. Later versions offer NoDecode; 2.6.1
    # does not. Keeping the field name identical to the variable also keeps
    # this file greppable from a deployment config.
    cors_origins: str = Field(
        default="http://localhost:5173",
        description="Comma-separated. A frontend on another origin needs adding here.",
    )
    cookie_secure: bool = Field(
        default=False,
        description="Off for local http. Must be on wherever the site is https.",
    )
    log_level: str = Field(
        default="INFO",
        description="Root logger level. DEBUG, INFO, WARNING, ERROR.",
    )

    # Phase 3 content pipeline. Both default to empty so the app, the tests and
    # `docker compose up` all work without a key — nothing on the vote path
    # reads them, and the pipeline is the only caller that should fail loudly
    # when one is missing.
    #
    # SecretStr, not str: it prints as `**********`, so a key cannot reach the
    # logs through a stray repr of Settings. Read it with
    # `.get_secret_value()` at the point of use, which also makes every place
    # that touches the raw value greppable.
    govinfo_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="api.data.gov key for GovInfo. Free from api.data.gov/signup.",
    )
    gemini_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Google AI Studio key. Direct access, kept for a fallback.",
    )
    openrouter_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="One gateway for embeddings, generation and the judge.",
    )

    # The model is configuration; the dimension is not. `chunk_embeddings.
    # embedding` is `vector(768)` in a migration, so the width is owned by
    # `shared.db.engine.EMBEDDING_DIM` and `embedding.py` reads it from there
    # rather than declaring a second 768 of its own.
    #
    # Changing this does not invalidate anything: `model` is part of
    # `chunk_embeddings`' primary key, so vectors from two models coexist and
    # `retrieval.nearest` requires the caller to say which one it wants. That
    # is what makes an A/B possible without a migration — as long as both
    # models can produce 768.
    embedding_model: str = Field(
        default="google/gemini-embedding-001",
        description="OpenRouter slug. Must be able to emit EMBEDDING_DIM dimensions.",
    )
    user_agent: str = Field(
        default="votecracy/0.1 (educational project; contact via repository)",
        description="Sent by every outbound fetch. One string, not one per client.",
    )

    @field_validator("log_level")
    @classmethod
    def _known_level(cls, level: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level.upper() not in valid:
            raise ValueError(f"log_level must be one of {sorted(valid)}, got {level!r}")
        return level.upper()

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]

    @field_validator("database_url")
    @classmethod
    def _require_psycopg_driver(cls, url: str) -> str:
        """docker-compose supplies a bare `postgresql://`; SQLAlchemy needs the
        driver spelled out. Normalising here rather than at each call site means
        the app and Alembic cannot disagree about what they connected to."""
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached so the environment is read and validated once per process."""
    return Settings()
