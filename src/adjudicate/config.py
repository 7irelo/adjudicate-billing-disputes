"""Settings, loaded from the environment and `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_prefix="ADJUDICATE_", extra="ignore"
    )

    owner_dsn: str = "postgresql://adjudicate_owner:owner_dev_pw@localhost:55491/adjudicate"
    admin_dsn: str = "postgresql://adjudicate_admin:admin_dev_pw@localhost:55491/adjudicate"
    app_dsn: str = "postgresql://adjudicate_app:app_dev_pw@localhost:55491/adjudicate"
    checkpoint_dsn: str = (
        "postgresql://adjudicate_checkpoint:checkpoint_dev_pw@localhost:55491/adjudicate"
    )
    token_secret: str = Field(default="dev-only-secret-change-me-at-least-32-bytes", min_length=32)
    token_ttl_seconds: int = 300
    mcp_url: str = "http://localhost:8765/mcp"

    decide_model: str = "claude-opus-5-5"
    decide_effort: str = "high"
    judge_model: str = "claude-sonnet-5-5"
    fast_model: str = "claude-haiku-5-5"

    auto_finalise_rejections: bool = False
    follow_up_enabled: bool = True
    world_seed: int = 20260101


@lru_cache
def get_settings() -> Settings:
    return Settings()
