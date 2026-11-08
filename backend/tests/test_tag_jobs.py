import json

from app.content import Job
from app.content.loader import DATA_DIR, load_attributes
from scripts.tag_jobs import build_batch_prompt, parse_batch, report

ATTRS = load_attributes(DATA_DIR / "attributes.csv")
CODES = [a.code for a in ATTRS]


def tag(job_id, **scores):
    a = {c: 0 for c in CODES}
    a.update(scores)
    return {"job_id": job_id, "attrs": a, "reason": "근거"}


def test_parse_batch_splits_ok_and_bad():
    text = json.dumps({"tags": [
        tag(1, R=3, field=2),
        tag(2, R=1),                    # 0 아닌 축 1개 → 규칙 위반
        tag(3, I=3, A=3, S=3),          # 3점 3개
        tag(99, R=3, C=2),              # 요청하지 않은 직업 → 무시
        tag(1, I=3, C=1),               # 중복 → 처음 것만
    ]})
    ok, bad = parse_batch(text, {1, 2, 3, 4}, ATTRS)
    assert ok[1]["attrs"][CODES.index("R")] == 3 and ok[1]["attrs"][CODES.index("I")] == 0
    assert set(bad) == {2, 3, 4} and bad[4] == "응답에서 빠짐"


def test_parse_batch_bad_json_fails_all():
    ok, bad = parse_batch("nope", {1, 2}, ATTRS)
    assert not ok and set(bad) == {1, 2}


def test_out_of_range_rejected():
    ok, bad = parse_batch(json.dumps({"tags": [tag(1, R=4, C=1)]}), {1}, ATTRS)
    assert not ok and "0~3" in bad[1]


def test_prompt_includes_every_job_id():
    items = [(Job(id=i, name=f"직업{i}", field="F"), {"workList": [{"work": f"일{i}"}]}) for i in (5, 8)]
    p = build_batch_prompt(items, ATTRS, "RUBRIC")
    assert '<job id="5">' in p and '<job id="8">' in p and "일8" in p and "RUBRIC" in p


def test_report_counts():
    base = {c: 0 for c in CODES}
    rows = [
        {**base, "job_id": 1, "name": "가", "category": "F1", "R": 3, "C": 1},
        {**base, "job_id": 2, "name": "나", "category": "F1", "R": 3, "C": 1},
        {**base, "job_id": 3, "name": "다", "category": "F2", "I": 2, "field": 2},
    ]
    text = report(rows, ATTRS)
    assert "직업 3개" in text
    assert "| field | 현장·야외 | 1 | 33% | 0 |" in text     # 축 field가 분야 열에 덮이지 않음
    assert "| F1 | 2 |" in text and "| F2 | 1 |" in text
    assert "| R | 현실형 | 2 | 67% | 2 |" in text
    assert "다른 직업과 벡터가 똑같은 직업 2개" in text


def test_invalid_cache_is_retagged(tmp_path, monkeypatch):
    import scripts.tag_jobs as tj
    monkeypatch.setattr(tj, "CACHE_DIR", tmp_path)
    n = len(CODES)
    (tmp_path / "1.json").write_text(json.dumps({"job_id": 1, "attrs": [3, 2] + [0] * (n - 2)}))
    (tmp_path / "2.json").write_text(json.dumps({"job_id": 2, "attrs": [3] + [0] * (n - 1)}))      # 축 1개
    (tmp_path / "3.json").write_text(json.dumps({"job_id": 3, "attrs": [3, 2] + [0] * n}))         # 길이 다름
    assert tj.cached_valid(1, n)
    assert not tj.cached_valid(2, n) and not tj.cached_valid(3, n) and not tj.cached_valid(4, n)
