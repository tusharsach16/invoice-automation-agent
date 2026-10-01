"""
Application settings loaded from environment variables / .env file.

This is the single source of truth for all configuration.
No other module should read os.environ directly.
"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    llm_model: str = Field(default="gemini-2.0-flash", alias="LLM_MODEL")

    # Invoices at or above this INR amount pause execution for human approval.
    approval_threshold: float = Field(default=100_000.0, alias="APPROVAL_THRESHOLD")

    database_url: str = Field(default="./hulchul.db", alias="DATABASE_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = {"env_file": ".env", "populate_by_name": True}


# Module-level singleton — import `settings` everywhere instead of
# constructing a new Settings() per call.
settings = Settings()
