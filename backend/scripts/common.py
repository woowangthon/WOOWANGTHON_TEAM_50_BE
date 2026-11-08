"""배치 스크립트 공용 도구."""
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"
RAW_DIR = DATA_DIR / "raw"


def read_env(name: str, required: bool = True, default: str | None = None) -> str | None:
    """환경 변수 → backend/.env 순으로 찾는다."""
    value = os.environ.get(name)
    env = BACKEND_DIR / ".env"
    if not value and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, sep, v = line.partition("=")
            if sep and key.strip() == name:
                value = v.strip().strip("'\"")
    if not value and required:
        sys.exit(f"{name}가 없습니다. backend/.env 에 넣거나 환경 변수로 지정하세요.")
    return value or default
