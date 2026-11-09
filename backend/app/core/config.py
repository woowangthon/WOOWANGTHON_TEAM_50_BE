"""서버 설정. 환경 변수 → backend/.env 순으로 읽는다."""
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")
    cors_origins: str = "http://localhost:5173"   # 쉼표 구분
    content_reviewed_only: bool = False           # 운영에서는 true (검수된 카드만 노출)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
