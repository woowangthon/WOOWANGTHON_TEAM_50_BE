"""카드·직업·차원 데이터 모델.

data/ 아래 정적 파일을 서버 시작 시 이 모델로 검증한다.
태깅 규칙은 docs/dimensions.md 참고.
"""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

N_DIM = 12
MIN_NONZERO, MAX_NONZERO = 2, 4   # 카드당 0이 아닌 축 개수
MAX_CORE = 2                      # 3점(핵심) 축 최대 개수
MIN_OPTION_GAP = 2                # 선택지끼리 최소 한 축에서 벌어져야 하는 점수 차

Score = Annotated[int, Field(ge=0, le=3)]
AttrVec = Annotated[list[Score], Field(min_length=N_DIM, max_length=N_DIM)]
CardType = Literal["job_day", "situation", "a_vs_b"]


def check_attrs(v: list[int], where: str) -> None:
    nonzero = sum(1 for x in v if x > 0)
    if not MIN_NONZERO <= nonzero <= MAX_NONZERO:
        raise ValueError(f"{where}: 0이 아닌 축은 {MIN_NONZERO}~{MAX_NONZERO}개여야 함 (현재 {nonzero})")
    core = sum(1 for x in v if x == 3)
    if core > MAX_CORE:
        raise ValueError(f"{where}: 3점 축은 최대 {MAX_CORE}개 (현재 {core})")


class Attribute(BaseModel):
    idx: int = Field(ge=0, lt=N_DIM)
    code: str
    label: str
    group: Literal["interest", "style", "value"]
    definition: str
    not_this: str


class Job(BaseModel):
    id: int                                   # 커리어넷 직업코드
    name: str
    field: str | None = None                  # 커리어넷 대분류 (콜드 스타트 분야별 샘플링용)
    summary: str | None = None
    majors: list[str] = []


class Option(BaseModel):
    id: str
    label: str = Field(max_length=40)
    attrs: AttrVec

    @model_validator(mode="after")
    def _attrs(self):
        check_attrs(self.attrs, self.id)
        return self


class Card(BaseModel):
    id: str = Field(pattern=r"^c\d{4}$")      # c0001
    type: CardType
    job_id: int | None = None
    title: str = Field(max_length=30)
    body: str = Field(max_length=200)
    attrs: AttrVec                            # 선택형이면 "이 카드가 묻는 축"
    options: list[Option] = []
    source: Literal["careernet", "llm", "manual"]
    reviewed: bool = False
    version: int = 1

    @model_validator(mode="after")
    def _by_type(self):
        check_attrs(self.attrs, self.id)
        n = len(self.options)
        if self.type == "job_day":
            if self.job_id is None:
                raise ValueError(f"{self.id}: job_day는 job_id 필수")
            if n:
                raise ValueError(f"{self.id}: job_day는 선택지를 가질 수 없음")
        elif self.type == "a_vs_b" and n != 2:
            raise ValueError(f"{self.id}: a_vs_b는 선택지가 정확히 2개 (현재 {n})")
        elif self.type == "situation" and not 2 <= n <= 4:
            raise ValueError(f"{self.id}: situation은 선택지 2~4개 (현재 {n})")

        for o in self.options:
            if not o.id.startswith(f"{self.id}-"):
                raise ValueError(f"{o.id}: 선택지 id는 '{self.id}-'로 시작해야 함")
        if n >= 2:
            gap = max(
                abs(a - b)
                for i, o1 in enumerate(self.options)
                for o2 in self.options[i + 1:]
                for a, b in zip(o1.attrs, o2.attrs)
            )
            if gap < MIN_OPTION_GAP:
                raise ValueError(
                    f"{self.id}: 선택지끼리 최소 한 축에서 {MIN_OPTION_GAP}점 이상 차이 나야 함 (최대 차 {gap})"
                )
        return self


class CardDeck(BaseModel):
    schema_version: int
    cards: list[Card]

    @model_validator(mode="after")
    def _unique_ids(self):
        ids = [c.id for c in self.cards] + [o.id for c in self.cards for o in c.options]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"id 중복: {sorted(dup)}")
        return self
