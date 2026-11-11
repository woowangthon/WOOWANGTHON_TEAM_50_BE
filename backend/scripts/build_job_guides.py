"""커리어넷 상세 캐시(data/raw/careernet) → 목표 직업 안내 data/job_guides.json.

    python -m scripts.build_job_guides

API 호출 없이 fetch_careernet.py가 받아 둔 캐시만 읽는다. data/raw/는 git에 없으므로
서버는 이 스크립트가 만든 job_guides.json만 쓴다. 모든 문장은 커리어넷 원문 그대로다.
"""
import json
import re
import sys
from typing import Any

from scripts.common import DATA_DIR, RAW_DIR

GUIDES_PATH = DATA_DIR / "job_guides.json"
CAREERNET_DIR = RAW_DIR / "careernet"
MAX_ITEMS = 5                                   # 항목마다 최대 개수
ACTIVITY_SEP = re.compile(r"\s+[-–]\s+")        # "제목 - 설명" (en-dash도 섞여 있음)


def _clean(s: Any) -> str:
    return " ".join(str(s or "").split())


def _texts(items: list | None, key: str) -> list[str]:
    out: list[str] = []
    for item in items or []:
        text = _clean((item or {}).get(key))
        if text and text not in out:
            out.append(text)
    return out[:MAX_ITEMS]


def to_activity(text: str) -> dict[str, str | None]:
    """'생물 관련 동아리 활동 - 생물 실험을 ...' → {title, detail}. 구분자가 없으면 title은 None."""
    parts = ACTIVITY_SEP.split(text, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return {"title": parts[0], "detail": parts[1]}
    return {"title": None, "detail": text}


def to_guide(job_id: int, detail: dict | None) -> dict | None:
    """상세 응답 → JobGuide dict. 안내할 내용이 하나도 없으면 None."""
    detail = detail or {}
    ready = detail.get("jobReadyList") or {}
    abilities = sorted(
        (a for a in detail.get("abilityList") or [] if a and a.get("ability_name")),
        key=lambda a: str(a.get("SORT_ORDR") or ""),
    )
    guide = {
        "job_id": job_id,
        "activities": [to_activity(t) for t in _texts(detail.get("researchList"), "research")],
        "abilities": _texts(abilities, "ability_name"),
        "aptitudes": _texts(detail.get("aptitudeList"), "aptitude"),
        "education": _texts(ready.get("curriculum"), "curriculum") + _texts(ready.get("training"), "training"),
        "certificates": _texts(ready.get("certificate"), "certificate"),
        "careers": _texts(ready.get("recruit"), "recruit"),
    }
    if not any(v for k, v in guide.items() if k != "job_id"):
        return None
    return guide


def main() -> None:
    jobs = json.loads((DATA_DIR / "jobs.json").read_text(encoding="utf-8"))
    guides, missing = [], []
    for job in jobs:
        path = CAREERNET_DIR / f"{job['id']}.json"
        if not path.exists():
            missing.append(job["id"])
            continue
        guide = to_guide(job["id"], json.loads(path.read_text(encoding="utf-8")))
        if guide:
            guides.append(guide)
    GUIDES_PATH.write_text(json.dumps(guides, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with_activities = sum(1 for g in guides if g["activities"])
    print(f"{len(guides)}/{len(jobs)}개 직업 안내 저장 (탐색 활동 있음 {with_activities}) → {GUIDES_PATH}", file=sys.stderr)
    if missing:
        print(f"캐시 없음 {len(missing)}개: {missing[:20]}", file=sys.stderr)


if __name__ == "__main__":
    main()
