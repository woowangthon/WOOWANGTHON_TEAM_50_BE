"""AI 진로 동료: 직업에 대한 정해진 질문에 답하기 · 학생과 대화하기 (Gemini).

- 순위·적합도는 코드가 계산하고, LLM은 그 결과와 커리어넷 설명을 근거로 말로 풀어 주기만 한다.
- 호출 실패·타임아웃·이상한 응답이면 None → API가 기록 기반 답변(fallback_*)으로 대신한다.
- 위기 표현은 LLM에 보내지 않고 정해진 안내(CRISIS_REPLY)로 답한다.
- 대화 내용은 저장하지 않는다. 프론트가 최근 대화를 매번 함께 보낸다.
"""
import logging
from datetime import date, datetime
from uuid import UUID

from google.genai import types
from pydantic import BaseModel, ValidationError

from app.journey import KST

log = logging.getLogger(__name__)

CHAT_DAILY_LIMIT = 30     # 사용자당 하루 대화 횟수 (비용·과몰입 방지)
MAX_ANSWER = 400
MAX_REPLY = 500
MAX_HISTORY = 10          # LLM에 넘기는 최근 대화 수

RULES = """당신은 진로를 아직 정하지 못한 중학생 곁에서 함께 탐색하는 AI 진로 동료예요.

규칙
- "~해요" 체로 짧고 쉽게. 어려운 말은 풀어서.
- 아래 [학생 기록]과 [직업 정보]에 있는 내용만 근거로 말하기. 연봉·자격증·근무시간·합격 방법처럼 자료에 없는 구체적 사실은 지어내지 말고, 모르면 모른다고 하기.
- "지금까지 반응을 보면"처럼 경향으로 말하고, 성격이나 미래를 단정하지 않기.
- 특정 직업을 하라고 정해 주거나 다른 길을 깎아내리지 않기. 고르는 건 학생이에요.
- 성별·외모·지역·집안 형편 같은 고정관념과 엮지 않기.
- 진로·공부·흥미 탐색과 상관없는 요청(숙제 대신 하기, 게임 공략, 어른용 주제 등)은 한 문장으로 정중히 거절하고 진로 이야기로 돌아오기.
- 이름·연락처·학교 같은 개인정보를 묻지 않기.
- 많이 힘들어 보이면 믿을 수 있는 어른이나 청소년상담전화 1388을 알려 주기.
- 마크다운, 이모지 없이 문장만."""

QUESTIONS = {
    "why_match": "왜 나와 이만큼 맞나요?",
    "day": "하루를 어떻게 보내나요?",
    "hard": "어떤 점이 힘들 수 있나요?",
    "start": "지금 뭘 해 보면 좋을까요?",
}
HARD_TIPS = {
    "R": "몸을 쓰거나 같은 작업을 오래 반복해야 하는 날이 있어요",
    "I": "답이 바로 나오지 않아 오래 붙잡고 있어야 할 때가 많아요",
    "A": "정답이 없어서 스스로 기준을 세워야 하고 평가가 엇갈릴 수 있어요",
    "S": "사람을 계속 만나다 보니 마음이 지칠 때가 있어요",
    "E": "결과에 대한 책임과 다른 사람을 설득해야 하는 부담이 따라요",
    "C": "작은 실수도 놓치면 안 돼서 꼼꼼함이 계속 필요해요",
    "team": "여러 사람의 의견을 맞추는 데 시간이 걸려요",
    "field": "날씨나 이동 같은 현장 상황에 영향을 받아요",
    "novelty": "계속 새로 배워야 해서 익숙해질 틈이 적어요",
    "autonomy": "스스로 계획하고 그 결과를 책임져야 해요",
    "impact": "내 일이 다른 사람에게 영향을 줘서 부담이 될 수 있어요",
}

CRISIS_WORDS = ("자살", "자해", "죽고 싶", "죽고싶", "사라지고 싶", "사라지고싶", "살기 싫", "살기싫")
CRISIS_REPLY = (
    "그런 마음이 들 만큼 힘들었군요. 혼자 견디지 않아도 되는 일이에요. "
    "지금 바로 청소년상담전화 1388(24시간, 문자·메신저 상담 가능)이나 자살예방상담전화 109에 이야기해 볼 수 있어요. "
    "가까운 어른이나 선생님께도 꼭 알려 줬으면 해요."
)
STARTERS = ["내 성향을 쉽게 설명해 줘", "나한테 맞는 직업이 궁금해", "아직 꿈이 없어도 괜찮을까?", "오늘 뭘 해 보면 좋을까?"]


def is_crisis(text: str) -> bool:
    return any(word in text for word in CRISIS_WORDS)


def clean(text: str | None, limit: int) -> str | None:
    text = " ".join((text or "").split()).strip("\"'“”")
    return text if 0 < len(text) <= limit else None


# ---------- 직업 질문 ----------

def job_questions(detail: dict) -> list[dict]:
    """이 직업에 물어볼 수 있는 질문. 적합도가 아직 없으면 '왜 맞나요'는 뺀다."""
    return [{"id": k, "label": v} for k, v in QUESTIONS.items() if k != "why_match" or detail["match"] is not None]


def fallback_answer(detail: dict, question_id: str) -> str:
    """LLM 없이 데이터만으로 만든 답."""
    traits = detail["traits"]
    if question_id == "why_match":
        shared = sorted((t for t in traits if t["mine"] > 0), key=lambda t: -t["level"] * t["mine"])[:2]
        core = "·".join(t["label"] for t in traits[:2])
        if not shared:
            return f"이 직업은 {core} 비중이 큰데, 아직 그쪽 카드에는 많이 끌리지 않아서 {detail['match']}%로 나왔어요. 반응이 쌓이면 달라질 수 있어요."
        mine = ", ".join(f"{t['label']} {t['mine']}%" for t in shared)
        return f"이 직업은 {core} 비중이 커요. 지금까지 내 반응에서도 {mine}로 겹치는 부분이 있어서 {detail['match']}%로 계산됐어요."
    if question_id == "day":
        sentences = [s.strip() for s in detail["summary"].replace("…", ".").split(". ") if s.strip()]
        return ". ".join(sentences[:2]).rstrip(".") + "." if sentences else "아직 자세한 설명이 준비되지 않았어요."
    if question_id == "hard":
        tips = [HARD_TIPS[t["code"]] for t in traits[:2]]
        return f"{detail['name']}처럼 {'·'.join(t['label'] for t in traits[:2])} 성격이 강한 일에서는 보통 {', '.join(tips)}. 사람마다 다르게 느낄 수 있어요."
    items = detail["steps"][0]["items"][:2] if detail["steps"] else []
    if not items:
        return "관심 가는 부분을 하나 골라 더 찾아보는 것부터 시작해 보세요."
    return " ".join(f"{i['title']}: {i['detail']}" if i["title"] else i["detail"] for i in items)


def job_prompt(detail: dict, question_id: str) -> str:
    traits = " / ".join(f"{t['label']}(비중 {t['level']}/3, 학생 {t['mine']}%)" for t in detail["traits"])
    lines = [
        RULES, "",
        "[직업 정보]",
        f"이름: {detail['name']}",
        f"하는 일: {detail['summary']}",
        f"핵심 성향: {traits}",
        f"관련 학과: {', '.join(detail['majors'][:6]) or '자료 없음'}",
        f"학생과의 적합도: {detail['match'] if detail['match'] is not None else '아직 계산 전'}%",
        "준비 단계: " + " / ".join(f"{s['title']} - " + "; ".join(i["detail"] for i in s["items"][:3]) for s in detail["steps"]),
        f"참고 답변: {fallback_answer(detail, question_id)}",
        "",
        f"[질문] {QUESTIONS[question_id]}",
        "위 자료만 근거로 3문장 이내, 250자 이내로 답해 주세요.",
    ]
    return "\n".join(lines)


# ---------- 대화 ----------

def describe(ctx: dict) -> str:
    """LLM에 넘기는 학생 기록 요약. 닉네임·이메일 등 개인정보는 넣지 않는다."""
    persona = ctx.get("persona")
    lines = ["[학생 기록]", f"반응한 카드 수: {ctx['n_reactions']}"]
    if persona:
        lines.append(f"성향 유형: {persona['code']}형 {persona['name']}")
    if ctx["interests"]:
        lines.append(f"끌린 흥미 유형: {', '.join(ctx['interests'])}")
    if ctx["strengths"]:
        lines.append(f"중요하게 여기는 방식·가치: {', '.join(ctx['strengths'])}")
    if ctx["jobs"]:
        lines.append("잘 맞는 직업: " + ", ".join(f"{j['name']}({j['match']}%)" for j in ctx["jobs"]))
    if ctx["saved"]:
        lines.append(f"저장한 카드: {', '.join(ctx['saved'])}")
    if len(lines) == 2:
        lines.append("아직 성향이 드러날 만큼 반응하지 않았어요.")
    return "\n".join(lines)


def fallback_chat(ctx: dict) -> dict:
    """LLM 없이 기록만으로 답한다."""
    persona = ctx.get("persona")
    if not persona:
        reply = "아직 반응이 적어서 성향이 잘 보이지 않아요. 홈에서 마음에 드는 카드에 끌려요를 눌러 보면, 그 기록으로 더 잘 이야기해 줄 수 있어요."
    else:
        reply = f"지금까지 반응을 보면 '{persona['name']}' 유형에 가까워요."
        if ctx["jobs"]:
            reply += f" 잘 맞는 직업으로는 {', '.join(j['name'] for j in ctx['jobs'][:3])} 등이 있어요. 탐색 탭에서 눌러 보면 준비할 것도 볼 수 있어요."
    return {"reply": reply, "suggestions": STARTERS[:3]}


class ChatDraft(BaseModel):
    reply: str
    suggestions: list[str] = []


def chat_prompt(ctx: dict, messages: list[dict]) -> str:
    history = "\n".join(f"{'학생' if m['role'] == 'user' else 'AI 동료'}: {m['text']}" for m in messages[-MAX_HISTORY:])
    return "\n".join([
        RULES, "", describe(ctx), "", "[대화]", history, "",
        "AI 동료의 다음 말을 JSON으로 주세요.",
        '- "reply": 4문장 이내, 300자 이내. 가능하면 끝에 학생이 생각해 볼 질문 하나.',
        '- "suggestions": 학생이 이어서 누를 만한 짧은 말 3개 (각 20자 이내, 학생 말투).',
    ])


class Coach:
    def __init__(self, client, model: str):
        self._client = client
        self._model = model
        # ponytail: 캐시와 사용량이 서버 메모리에 있다 (재시작하면 초기화, 인스턴스끼리 공유 안 됨). 인스턴스를 늘리면 DB로
        self._answers: dict[tuple, str] = {}
        self._usage: dict[UUID, tuple[date, int]] = {}

    def _generate(self, prompt: str, config: types.GenerateContentConfig) -> str | None:
        try:
            return self._client.models.generate_content(model=self._model, contents=prompt, config=config).text
        except Exception:
            log.warning("Gemini 호출 실패, 기록 기반 답변을 사용", exc_info=True)
            return None

    def answer_job(self, user_id: UUID, n_reactions: int, detail: dict, question_id: str) -> str | None:
        """직업 질문 답. '왜 맞나요'만 학생마다 다르고, 나머지는 직업별로 한 번 만들어 같이 쓴다."""
        key = (detail["job_id"], question_id) + ((user_id, n_reactions) if question_id == "why_match" else ())
        if key in self._answers:
            return self._answers[key]
        text = clean(self._generate(job_prompt(detail, question_id), types.GenerateContentConfig(temperature=0.5)), MAX_ANSWER)
        if text:
            self._answers[key] = text
        return text

    def allow(self, user_id: UUID, today: date | None = None) -> bool:
        """오늘 대화 횟수를 하나 쓰고, 한도 안이면 True."""
        today = today or datetime.now(KST).date()
        day, used = self._usage.get(user_id, (today, 0))
        used = used if day == today else 0
        if used >= CHAT_DAILY_LIMIT:
            return False
        self._usage[user_id] = (today, used + 1)
        return True

    def chat(self, ctx: dict, messages: list[dict]) -> dict | None:
        config = types.GenerateContentConfig(
            temperature=0.7, response_mime_type="application/json", response_schema=ChatDraft
        )
        raw = self._generate(chat_prompt(ctx, messages), config)
        try:
            draft = ChatDraft.model_validate_json(raw or "")
        except ValidationError:
            return None
        reply = clean(draft.reply, MAX_REPLY)
        if reply is None:
            return None
        suggestions = [s for s in (clean(s, 30) for s in draft.suggestions) if s][:3]
        return {"reply": reply, "suggestions": suggestions}
