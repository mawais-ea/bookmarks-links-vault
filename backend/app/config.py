from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # SQLite for the simplest MVP; set to postgresql+psycopg://user:pass@host/db in production.
    database_url: str = "sqlite:///./linkvault.db"

    # Used to sign login tokens. Always override in production.
    secret_key: str = "change-me-in-production"
    token_expire_days: int = 30

    # From .env or the environment. Empty = let the SDK find credentials itself (env var, `ant auth login`).
    anthropic_api_key: str = ""
    llm_enabled: bool = True
    llm_model: str = "claude-haiku-5-5"
    llm_min_confidence: float = 0.5

    # Language for descriptions, summaries and recipes (content in other languages is translated).
    summary_language: str = "English"
    summary_language_code: str = "en"  # used to pick translated video captions when Claude isn't available

    def llm_ready(self) -> bool:
        """True when Claude can be called (key in .env or the environment)."""
        import os
        return self.llm_enabled and bool(
            self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    # Network settings for metadata extraction.
    fetch_timeout: float = 12.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    )
    # Sent when a site rejects the browser UA. Sites like Wikipedia require a contact URL or email here.
    bot_user_agent: str = "LinkVault/0.1 (+https://github.com/linkvault; personal bookmark previews)"
    enable_ytdlp: bool = True
    enable_trafilatura: bool = True

    # Video captions feed the summary. Speech-to-text (pip install faster-whisper) covers videos
    # without captions but takes about a minute per 10 minutes of video, so it's off by default.
    enable_transcripts: bool = True
    enable_whisper: bool = False
    whisper_model: str = "base"  # tiny | base | small | medium | large-v3 (bigger = better, slower)
    whisper_max_minutes: int = 120
    whisper_device: str = "cpu"  # "cuda" for an NVIDIA GPU with CUDA 12 + cuDNN installed

    # Optional Meta app token ("APP_ID|CLIENT_TOKEN") for Instagram/Facebook oEmbed.
    meta_oembed_token: str = ""

    # Uploaded screenshots are stored here.
    files_dir: str = "./uploads"
    max_upload_mb: int = 25

    # Allow open registration. Turn off after creating your own account.
    allow_registration: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
