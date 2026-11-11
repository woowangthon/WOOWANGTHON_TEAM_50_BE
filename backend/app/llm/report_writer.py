"""성향 리포트 설명 문장 (Gemini).

점수 계산·직업 순위는 app/report.py 가 정하고, LLM은 그 결과를 학생에게 건네는 말로 풀어 쓰기만 한다.
호출이 실패하거나 결과가 이상하면 None 을 돌려주고, API는 점수 기반 문장(report.summary)을 그대로 쓴다.
"""
import logging
from uuid import UUID

from google import genai
from google.genai import types

from app.report import Report

log = logging.getLogger(__name__)

MAX_CHARS = 300          # 이보다 길면 지시를 어긴 응답으로 보고 버린다
TEMPERATURE = 0.7

INSTRUCTIONS = """당신은 진로를 아직 정하지 못한 중학생 곁에서 함께 탐색하는 AI 진로 동료예요.
아래는 학생이 진로 카드에 반응한 결과를 점수로 정리한 것이에요. 이 내용만 근거로, 학생에게 건네는 짧은 설명을 써 주세요.

규칙
- 2~3문장, 200자 이내, "~해요" 체
- "지금까지 반응을 보면"처럼 경향으로 말하고, 성격이나 미래를 단정하지 않기
- 특정 직업을 하라고 권하거나 다른 길을 깎아내리지 않기
- 성별·외모·지역 등 고정관념과 엮지 않기
- 아래에 없는 사실(성적, 가정환경, 학생 이름 등)을 지어내지 않기
- 마크다운, 이모지, 따옴표 없이 문장만"""

REACTION_LABELS = {"like": "끌려요", "save": "저장", "choose": "선택형 답변", "dislike": "별로예요"}


def build_prompt(report: Report) -> str:
    counts = ", ".join(f"{label} {report.reaction_counts.get(k, 0)}" for k, label in REACTION_LABELS.items())
    lines = [INSTRUCTIONS, "", f"[반응 수] {counts}"]
    if report.top_interests:
        lines.append("[끌린 흥미 유형] " + " / ".join(f"{a.label}: {a.definition}" for a in report.top_interests))
    if report.strengths:
        lines.append("[중요하게 여기는 방식·가치] " + " / ".join(f"{a.label}: {a.definition}" for a in report.strengths))
    if report.jobs:
        lines.append("[성향과 가까운 직업] " + ", ".join(j.name for j in report.jobs))
    if report.majors:
        lines.append("[관련 학과] " + ", ".join(report.majors))
    return "\n".join(lines)


def clean(text: str | None) -> str | None:
    """공백 정리. 비었거나 너무 길면 None."""
    text = " ".join((text or "").split()).strip("\"'“”")
    return text if 0 < len(text) <= MAX_CHARS else None


class ReportWriter:
    def __init__(self, client, model: str):
        self._client = client
        self._model = model
        self._config = types.GenerateContentConfig(temperature=TEMPERATURE)
        # ponytail: 서버 메모리 캐시 (재시작하면 비워지고 인스턴스끼리 공유 안 됨). 인스턴스를 늘리면 DB에 저장
        self._cache: dict[UUID, tuple[int, str]] = {}

    def summarize(self, user_id: UUID, report: Report) -> str | None:
        """반응 수가 그대로면 이전 문장을 다시 쓴다. 실패는 캐시하지 않는다 (다음 요청에서 재시도)."""
        cached = self._cache.get(user_id)
        if cached and cached[0] == report.n_reactions:
            return cached[1]
        try:
            resp = self._client.models.generate_content(
                model=self._model, contents=build_prompt(report), config=self._config
            )
            text = clean(resp.text)
        except Exception:
            log.warning("Gemini 리포트 문장 생성 실패, 점수 기반 문장을 사용", exc_info=True)
            return None
        if text is None:
            log.warning("Gemini 리포트 문장이 비었거나 너무 김, 점수 기반 문장을 사용")
            return None
        self._cache[user_id] = (report.n_reactions, text)
        return text


def create_gemini(api_key: str, timeout_s: float) -> genai.Client:
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))


def create_writer(api_key: str, model: str, timeout_s: float) -> ReportWriter:
    return ReportWriter(create_gemini(api_key, timeout_s), model)
