from types import SimpleNamespace
from uuid import uuid4

from app.content import load_content
from app.llm.report_writer import MAX_CHARS, ReportWriter, build_prompt, clean
from app.report import JobMatch, Report

ATTRS = {a.code: a for a in load_content().attributes}


def make_report(n_reactions=6) -> Report:
    return Report(
        n_reactions=n_reactions, ready=True, remaining=0,
        top_interests=[ATTRS["I"], ATTRS["S"]], strengths=[ATTRS["impact"]],
        summary="점수 기반 문장",
        jobs=[JobMatch(15, "수의사", "의료", ["수의학과"], 0.9)], majors=["수의학과"],
        reaction_counts={"like": 4, "save": 1, "choose": 1, "dislike": 0, "pass": 0},
    )


class FakeClient:
    def __init__(self, text="지금까지 반응을 보면 원리를 탐구하는 일에 끌리는 편이에요.", error=None):
        self.calls, self.text, self.error = [], text, error
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


def test_prompt_contains_report_facts_only():
    p = build_prompt(make_report())
    for fact in ["탐구형", "사회형", "사회적 영향", "수의사", "수의학과", "끌려요 4", "저장 1"]:
        assert fact in p
    assert "0.9" not in p        # 코사인 점수는 넘기지 않는다


def test_clean():
    assert clean('  "두 문장이에요.\n 정말요."  ') == "두 문장이에요. 정말요."
    assert clean("") is None and clean(None) is None
    assert clean("가" * (MAX_CHARS + 1)) is None


def test_summarize_caches_until_reactions_change():
    client, uid = FakeClient(), uuid4()
    w = ReportWriter(client, "m")
    assert w.summarize(uid, make_report(6)).startswith("지금까지")
    w.summarize(uid, make_report(6))
    assert len(client.calls) == 1                 # 같은 반응 수 → 재사용
    w.summarize(uid, make_report(7))
    assert len(client.calls) == 2                 # 반응이 늘면 새로 생성
    w.summarize(uuid4(), make_report(7))
    assert len(client.calls) == 3                 # 다른 사용자는 따로


def test_failure_returns_none_and_is_not_cached():
    client, uid = FakeClient(error=TimeoutError("slow")), uuid4()
    w = ReportWriter(client, "m")
    assert w.summarize(uid, make_report()) is None
    client.error = None
    assert w.summarize(uid, make_report()) is not None   # 다음 요청에서 재시도
    assert len(client.calls) == 2


def test_empty_or_too_long_answer_falls_back():
    assert ReportWriter(FakeClient(text=""), "m").summarize(uuid4(), make_report()) is None
    assert ReportWriter(FakeClient(text="가" * 500), "m").summarize(uuid4(), make_report()) is None
