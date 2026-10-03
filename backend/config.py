"""
config.py - ONE place for all settings.

Why: we never hard-code secrets (API keys, passwords) inside code.
Instead they are read from "environment variables" (or a local .env file).
Every other file does `from backend.config import settings` to get them.
"""
import os

from dotenv import load_dotenv

# Reads the file named ".env" (if it exists) and puts its lines into os.environ.
load_dotenv()

# CrewAI sends anonymous usage statistics by default. We switch that off
# because this is a healthcare project and we want zero surprise network calls.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")


class Settings:
    # --- Database -----------------------------------------------------------
    # SQLite = a database stored in a single file. Perfect for a hackathon.
    # For production set DATABASE_URL to a PostgreSQL URL.
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./healthbridge.db")

    # --- Security -----------------------------------------------------------
    # Used to sign login tokens (JWT). CHANGE THIS in production!
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-only-change-me-please-32chars-min")
    TOKEN_EXPIRE_MINUTES: int = int(os.getenv("TOKEN_EXPIRE_MINUTES", "480"))

    # --- AI (Grok from xAI) -------------------------------------------------
    XAI_API_KEY: str = os.getenv("XAI_API_KEY", "")
    # xAI's API speaks the same "language" as OpenAI's, so CrewAI talks to it
    # through its OpenAI-compatible provider: model name = "openai/<grok model>"
    XAI_MODEL: str = os.getenv("XAI_MODEL", "grok-4.7")
    XAI_BASE_URL: str = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
    # Turn AI off completely (the app still works with rule-based fallbacks).
    AI_ENABLED: bool = os.getenv("AI_ENABLED", "true").lower() == "true"

    # --- Misc ---------------------------------------------------------------
    # Seed demo data automatically on first start (synthetic patients only!)
    AUTO_SEED: bool = os.getenv("AUTO_SEED", "true").lower() == "true"
    BREAK_GLASS_HOURS: int = int(os.getenv("BREAK_GLASS_HOURS", "2"))

    @property
    def ai_available(self) -> bool:
        """True only if AI is switched on AND we have an API key."""
        return self.AI_ENABLED and bool(self.XAI_API_KEY)


settings = Settings()
