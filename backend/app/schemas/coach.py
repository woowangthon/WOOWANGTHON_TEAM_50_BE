from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class AskIn(BaseModel):
    question_id: Literal["why_match", "day", "hard", "start"]   # 정해진 질문만 (자유 입력 없음)


class AskOut(BaseModel):
    answer: str
    by_ai: bool                  # false면 기록·자료만으로 만든 답 (키 없음·실패)


class ChatMessage(BaseModel):
    role: Literal["user", "ai"]
    text: Text


class ChatIn(BaseModel):
    # 대화는 서버에 저장하지 않는다. 프론트가 최근 대화를 함께 보내고, 마지막은 학생의 말이어야 한다
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)


class ChatOut(BaseModel):
    reply: str
    suggestions: list[str]       # 이어서 누를 수 있는 말 (최대 3개)
    by_ai: bool
