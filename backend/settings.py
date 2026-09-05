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

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
