import numpy as np

from app.content import N_DIM, load_content
import pytest

from app.report import MAX_SUMMARY, JobMatch, _join, build_report, first_sentence, josa, match_jobs, rank_majors

CONTENT = load_content()
CODES = {a.code: a.idx for a in CONTENT.attributes}


LIKES = [{"card_id": "c0001", "reaction": "like"}] * 5


def vector(**scores) -> np.ndarray:
    v = np.zeros(N_DIM, dtype=np.float32)
    for code, s in scores.items():
        v[CODES[code]] = s
    return v


def test_top_interests_and_strengths_follow_scores():
    r = build_report(CONTENT, vector(I=0.6, R=0.4, A=0.1, team=0.2, impact=0.5, field=0.1), 10, LIKES)
    assert [a.code for a in r.top_interests] == ["I", "R"]
    assert [a.code for a in r.strengths] == ["impact", "team"]
    assert r.summary.startswith("탐구형·현실형 카드에 자주 끌렸어요.")
    assert "사회에 도움이 되는 일과 여럿이 함께 일하는 방식을" in r.summary
    assert len(r.study_tips) == 2


def test_zero_axes_are_not_reported():
    r = build_report(CONTENT, vector(S=0.5), 10, LIKES)
    assert [a.code for a in r.top_interests] == ["S"] and r.strengths == []
    assert "사람을 돕고 가르치는 일에" in r.summary


def test_pass_and_dislike_do_not_open_report():
    reactions = [{"card_id": "c0001", "reaction": "pass"}] * 6 + [{"card_id": "c0001", "reaction": "dislike"}] * 4
    r = build_report(CONTENT, vector(), 10, reactions)
    assert (r.ready, r.remaining, r.jobs, r.summary) == (False, 5, [], "")
    assert r.reaction_counts == {"like": 0, "dislike": 4, "save": 0, "pass": 6, "choose": 0}


def test_signal_reactions_are_counted():
    reactions = [{"card_id": "c0001", "reaction": x} for x in ["like", "save", "choose", "pass", "dislike"]]
    assert build_report(CONTENT, vector(I=0.5), 5, reactions).remaining == 2


def test_zero_vector_stays_locked_even_with_enough_reactions():
    r = build_report(CONTENT, vector(), 10, LIKES)
    assert (r.ready, r.remaining) == (False, 1)


def test_jobs_have_one_line_summary():
    r = build_report(CONTENT, vector(I=0.6, R=0.4), 10, LIKES)
    assert all(j.summary and len(j.summary) <= MAX_SUMMARY for j in r.jobs)


def test_first_sentence():
    assert first_sentence("수의사는 동물을 치료합니다. 예방접종도 해요.") == "수의사는 동물을 치료합니다."
    assert first_sentence(None) == ""
    long = "가" * (MAX_SUMMARY + 20) + "."
    assert first_sentence(long).endswith("…") and len(first_sentence(long)) == MAX_SUMMARY


def test_matched_jobs_fit_the_vector():
    jobs = match_jobs(CONTENT, vector(S=1.0), k=5)
    assert len(jobs) == 5 and len({j.job_id for j in jobs}) == 5
    for j in jobs:
        card = next(c for c in CONTENT.cards if c.job_id == j.job_id)
        assert card.attrs[CODES["S"]] > 0


def test_excluded_jobs_are_skipped():
    top = match_jobs(CONTENT, vector(R=1.0, field=0.5), k=3)
    again = match_jobs(CONTENT, vector(R=1.0, field=0.5), exclude_jobs=[top[0].job_id], k=3)
    assert top[0].job_id not in {j.job_id for j in again}


def test_disliked_job_is_excluded_from_report():
    v = vector(R=1.0, field=0.5)
    best = match_jobs(CONTENT, v, k=1)[0]
    card = next(c for c in CONTENT.cards if c.job_id == best.job_id)
    r = build_report(CONTENT, v, 10, LIKES + [{"card_id": card.id, "reaction": "dislike"}])
    assert best.job_id not in {j.job_id for j in r.jobs}


def test_majors_are_weighted_by_job_score_and_major_count():
    jobs = [
        JobMatch(1, "가", None, ["간호학과", "보건학과"], 0.9),        # 0.45씩
        JobMatch(2, "나", None, ["보건학과", "체육학과"], 0.5),        # 0.25씩
        JobMatch(3, "다", None, ["체육학과"], 0.3),
        JobMatch(4, "라", None, [], 0.95),                            # 학과 정보 없음
    ]
    assert rank_majors(jobs, k=3) == ["보건학과", "체육학과", "간호학과"]


def test_locked_report_counts_remaining():
    r = build_report(CONTENT, vector(I=1.0), 3, LIKES[:3])
    assert (r.ready, r.remaining, r.jobs, r.summary) == (False, 2, [], "")


@pytest.mark.parametrize("word, expected", [
    ("방식", "방식을"), ("일", "일을"), ("자유", "자유를"), ("AI", "AI를"), ("", "를"),
])
def test_josa_follows_final_consonant(word, expected):
    assert josa(word, "을", "를") == expected


@pytest.mark.parametrize("words, expected", [
    (["방식"], "방식"),
    (["방식", "일"], "방식과 일"),
    (["자유", "일"], "자유와 일"),
    (["가", "나", "다"], "가, 나와 다"),
])
def test_join_uses_matching_particle(words, expected):
    assert _join(words) == expected
