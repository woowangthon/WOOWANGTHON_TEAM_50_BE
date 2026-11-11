from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from app.llm.coach import (
    CHAT_DAILY_LIMIT, Coach, chat_prompt, describe, fallback_answer, fallback_chat, is_crisis, job_prompt, job_questions,
)

DETAIL = {
    "job_id": 15, "name": "수의사", "field": "보건·의료직", "match": 82,
    "summary": "수의사는 동물의 질병을 진단하고 치료합니다. 예방 접종과 수술을 합니다. 보호자에게 관리 방법을 알려 줍니다.",
    "majors": ["수의학과"],
    "traits": [
        {"code": "I", "label": "탐구형", "group": "interest", "definition": "", "level": 3, "mine": 60},
        {"code": "S", "label": "사회형", "group": "interest", "definition": "", "level": 2, "mine": 0},
    ],
    "steps": [{"key": "explore", "title": "지금 해 볼 수 있는 일", "items": [
        {"title": "동물 보호 봉사", "detail": "유기 동물 보호소에서 봉사해 보세요."}, {"title": None, "detail": "생명과학 탐구 보고서를 써 보세요."}]}],
    "similar": [],
}
CTX = {"n_reactions": 8, "persona": {"code": "IS", "name": "다정한 탐구가", "tagline": "", "labels": []},
       "interests": ["탐구형", "사회형"], "strengths": ["사회적 영향"],
       "jobs": [{"name": "수의사", "match": 82}], "saved": ["수의사의 하루"]}
EMPTY = {"n_reactions": 0, "persona": None, "interests": [], "strengths": [], "jobs": [], "saved": []}


class FakeClient:
    def __init__(self, text="지금까지 반응을 보면 잘 맞아 보여요.", error=None):
        self.calls, self.text, self.error = [], text, error
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


def test_crisis_detection():
    assert is_crisis("요즘 죽고 싶어") and is_crisis("자해 했어") and not is_crisis("수의사가 되고 싶어")


def test_job_questions_hide_why_match_without_score():
    assert [q["id"] for q in job_questions(DETAIL)][0] == "why_match"
    assert "why_match" not in [q["id"] for q in job_questions({**DETAIL, "match": None})]


def test_fallback_answers_use_only_job_data():
    why = fallback_answer(DETAIL, "why_match")
    assert "82%" in why and "탐구형 60%" in why and "사회형 0%" not in why
    none = fallback_answer({**DETAIL, "traits": [{**t, "mine": 0} for t in DETAIL["traits"]]}, "why_match")
    assert "아직" in none and "82%" in none
    assert fallback_answer(DETAIL, "day") == "수의사는 동물의 질병을 진단하고 치료합니다. 예방 접종과 수술을 합니다."
    assert "오래 붙잡고" in fallback_answer(DETAIL, "hard")
    assert fallback_answer(DETAIL, "start") == "동물 보호 봉사: 유기 동물 보호소에서 봉사해 보세요. 생명과학 탐구 보고서를 써 보세요."
    assert "부분을 하나 골라" in fallback_answer({**DETAIL, "steps": []}, "start")


def test_prompts_carry_facts_and_no_personal_info():
    p = job_prompt(DETAIL, "day")
    assert "수의사" in p and "수의학과" in p and "하루를 어떻게" in p and "지어내지" in p and "유기 동물 보호소" in p
    d = describe(CTX)
    assert "다정한 탐구가" in d and "수의사(82%)" in d and "수의사의 하루" in d
    assert "아직 성향이" in describe(EMPTY)
    c = chat_prompt(CTX, [{"role": "user", "text": "안녕"}, {"role": "ai", "text": "반가워요"}])
    assert "학생: 안녕" in c and "AI 동료: 반가워요" in c


def test_fallback_chat():
    assert "다정한 탐구가" in fallback_chat(CTX)["reply"] and "수의사" in fallback_chat(CTX)["reply"]
    assert "끌려요" in fallback_chat(EMPTY)["reply"] and fallback_chat(EMPTY)["suggestions"]


def test_answer_job_caches_shared_and_personal_separately():
    client, a, b = FakeClient(), uuid4(), uuid4()
    coach = Coach(client, "m")
    assert coach.answer_job(a, 5, DETAIL, "day") and coach.answer_job(b, 9, DETAIL, "day")
    assert len(client.calls) == 1                         # 직업 설명 질문은 모두가 같이 쓴다
    coach.answer_job(a, 5, DETAIL, "why_match"); coach.answer_job(a, 5, DETAIL, "why_match")
    coach.answer_job(b, 5, DETAIL, "why_match"); coach.answer_job(a, 6, DETAIL, "why_match")
    assert len(client.calls) == 4                         # '왜 맞나요'는 학생·반응 수마다 따로


def test_answer_job_failure_is_none_and_retried():
    client = FakeClient(error=TimeoutError())
    coach = Coach(client, "m")
    assert coach.answer_job(uuid4(), 1, DETAIL, "day") is None
    client.error = None
    assert coach.answer_job(uuid4(), 1, DETAIL, "day")
    assert Coach(FakeClient(text="가" * 900), "m").answer_job(uuid4(), 1, DETAIL, "day") is None


def test_chat_parses_json_and_trims_suggestions():
    good = '{"reply": "좋은 질문이에요.", "suggestions": ["하나", "둘", "셋", "넷", "' + "가" * 40 + '"]}'
    assert Coach(FakeClient(text=good), "m").chat(CTX, [{"role": "user", "text": "안녕"}]) == {
        "reply": "좋은 질문이에요.", "suggestions": ["하나", "둘", "셋"]}
    for bad in ["그냥 글", "", '{"reply": ""}', '{"reply": "' + "가" * 600 + '"}']:
        assert Coach(FakeClient(text=bad), "m").chat(CTX, [{"role": "user", "text": "안녕"}]) is None
    assert Coach(FakeClient(error=RuntimeError()), "m").chat(CTX, [{"role": "user", "text": "안녕"}]) is None


def test_daily_limit_resets_next_day():
    coach, uid = Coach(FakeClient(), "m"), uuid4()
    day = date(2026, 11, 12)
    assert all(coach.allow(uid, day) for _ in range(CHAT_DAILY_LIMIT))
    assert not coach.allow(uid, day) and coach.allow(uuid4(), day)
    assert coach.allow(uid, date(2026, 11, 13))
