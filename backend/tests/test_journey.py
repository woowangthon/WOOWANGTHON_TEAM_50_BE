from datetime import date, datetime

import numpy as np

from app.content import N_DIM, load_content
from app.jobs import job_detail, list_fields, load_job_index, match_scores, rank_jobs
from app.roadmap import build_roadmap
from app.journey import KST, activity, badges, card_reason, gains, level, mission, persona

CONTENT = load_content()
INDEX = load_job_index(CONTENT)
IDX = {a.code: a.idx for a in CONTENT.attributes}


def vec(**scores) -> np.ndarray:
    v = np.zeros(N_DIM, dtype=np.float32)
    for code, s in scores.items():
        v[IDX[code]] = s
    return v


def at(day: int, hour: int = 12) -> dict:
    return {"created_at": datetime(2026, 11, day, hour, tzinfo=KST).isoformat(), "reaction": "like", "card_id": "c0001"}


def test_streak_counts_consecutive_days_in_korean_time():
    now = datetime(2026, 11, 12, 9, tzinfo=KST)
    assert activity([], now) == (0, 0)
    assert activity([at(12), at(12), at(11), at(10), at(8)], now) == (3, 2)
    assert activity([at(11), at(10)], now) == (2, 0)          # 오늘 아직 안 했어도 어제까지 연속이면 유지
    assert activity([at(10)], now) == (0, 0)                  # 하루 건너뛰면 끊김
    # UTC 11일 16시 = 한국 12일 01시 → 오늘로 센다
    assert activity([{"created_at": "2026-11-11T16:00:00+00:00"}], now) == (1, 1)


def test_level_progress():
    assert level(0) == {"n": 1, "name": "새싹 탐험가", "next_at": 5, "progress": 0.0}
    assert level(10)["n"] == 2 and level(10)["progress"] == 0.5
    assert level(500)["next_at"] is None and level(500)["progress"] == 1.0


def test_persona_from_top_two_interests():
    assert persona(CONTENT.attributes, vec()) is None
    assert persona(CONTENT.attributes, vec(team=1.0)) is None          # 흥미 축이 없으면 유형 없음
    p = persona(CONTENT.attributes, vec(I=0.8, A=0.5, team=0.9))
    assert (p["code"], p["name"], p["labels"]) == ("IA", "감각적인 탐구가", ["탐구형", "예술형"])
    assert persona(CONTENT.attributes, vec(S=0.3))["name"] == "서포터"


def test_mission_is_stable_per_day_and_follows_top_interest():
    day = date(2026, 11, 12)
    a, b = mission(CONTENT.attributes, vec(A=1.0), day), mission(CONTENT.attributes, vec(A=1.0), day)
    assert a == b and a["trait"] == "예술형" and a["id"].startswith("2026-11-12")
    assert mission(CONTENT.attributes, vec(), day)["trait"] is None


def test_gains_reports_biggest_increases_only():
    g = gains(CONTENT.attributes, vec(I=0.2, A=0.5), vec(I=0.5, A=0.4, S=0.1))
    assert [(x["code"], x["delta"]) for x in g] == [("I", 30), ("S", 10)]
    assert gains(CONTENT.attributes, vec(I=0.5), vec(I=0.5)) == []


def test_card_reason():
    attrs = CONTENT.attributes
    assert "탐구형" in card_reason(attrs, vec(I=0.9, A=0.1), vec(I=1.0, A=0.3), "personalized")
    assert card_reason(attrs, vec(I=0.9), vec(A=1.0), "personalized") is None
    assert card_reason(attrs, vec(I=0.9), vec(I=1.0), "cold_start") is None
    assert card_reason(attrs, vec(), vec(I=1.0), "random")


def test_badges():
    got = {b["id"]: b["earned"] for b in badges(CONTENT, [], 0, False)}
    assert not any(got.values()) and len(got) == 7
    log = [{"card_id": "c0001", "reaction": "save"}] * 3
    got = {b["id"]: b["earned"] for b in badges(CONTENT, log, 3, True)}
    assert got["first_step"] and got["collector"] and got["streak3"] and got["report"] and not got["streak7"]


# ---------- 직업 ----------

def test_job_index_covers_tagged_jobs():
    assert len(INDEX.ids) > 500 and INDEX.raw.shape == (len(INDEX.ids), N_DIM)
    assert np.allclose(np.linalg.norm(INDEX.unit, axis=1), 1.0, atol=1e-5)
    assert len(list_fields(CONTENT, INDEX)) == 10


def test_match_is_none_without_vector_and_percent_with():
    assert match_scores(INDEX, vec()) is None
    assert all(j["match"] is None for j in rank_jobs(CONTENT, INDEX, vec(), limit=5))
    jobs = rank_jobs(CONTENT, INDEX, vec(I=1.0, impact=0.5), limit=10)
    matches = [j["match"] for j in jobs]
    assert matches == sorted(matches, reverse=True) and 0 < matches[0] <= 100
    assert INDEX.vector(jobs[0]["job_id"])[IDX["I"]] > 0      # 탐구형 성향이면 탐구형 직업이 맨 위


def test_rank_jobs_filters_by_field():
    field = list_fields(CONTENT, INDEX)[0]
    jobs = rank_jobs(CONTENT, INDEX, vec(R=1.0), field=field, limit=100)
    assert jobs and {j["field"] for j in jobs} == {field}


def test_job_detail():
    job_id = rank_jobs(CONTENT, INDEX, vec(I=1.0), limit=1)[0]["job_id"]
    d = job_detail(CONTENT, INDEX, vec(I=1.0), job_id)
    assert d["match"] > 50 and d["traits"] and all(1 <= t["level"] <= 3 for t in d["traits"])
    assert next(t for t in d["traits"] if t["code"] == "I")["mine"] == 100
    assert d["steps"] and all(s["items"] and s["key"] in {"explore", "grow", "study", "career"} for s in d["steps"])
    assert d["steps"] == [s.model_dump() for s in build_roadmap(CONTENT, job_id, vec(I=1.0)).steps]   # 로드맵과 같은 내용
    assert len(d["similar"]) == 4 and job_id not in {s["job_id"] for s in d["similar"]}
    assert job_detail(CONTENT, INDEX, vec(), job_id)["match"] is None
    assert job_detail(CONTENT, INDEX, vec(), -1) is None
