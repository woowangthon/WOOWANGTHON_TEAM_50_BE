from pydantic import BaseModel


class Gain(BaseModel):
    code: str
    label: str
    delta: int                   # 이번 반응으로 오른 정도 (퍼센트 포인트)


class Level(BaseModel):
    n: int
    name: str
    next_at: int | None          # 다음 레벨까지 필요한 누적 반응 수. 최고 레벨이면 null
    progress: float              # 현재 레벨 안에서의 진행도 0~1


class Persona(BaseModel):
    code: str                    # 흥미 상위 축 코드. 예: "IA"
    name: str                    # 예: "감각적인 탐구가"
    tagline: str
    labels: list[str]


class Mission(BaseModel):
    id: str                      # 날짜가 들어 있어 매일 바뀐다. 완료 여부는 프론트가 이 id로 기억
    text: str
    trait: str | None


class Badge(BaseModel):
    id: str
    name: str
    description: str
    earned: bool


class JourneyResponse(BaseModel):
    streak: int                  # 연속 접속 일수 (한국 시간 기준)
    today_count: int             # 오늘 반응한 카드 수
    daily_goal: int
    n_reactions: int
    level: Level
    persona: Persona | None      # 흥미 성향이 아직 없으면 null
    report_ready: bool
    report_remaining: int
    mission: Mission
    badges: list[Badge]
