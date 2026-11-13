"""백엔드 데이터 → 프론트 목업 데이터. backend/ 에서:

    python -m scripts.export_mock <FE>/src/mocks/data.js

같은 폴더에 guides.js(카드가 있는 직업의 커리어넷 직업 안내, 로드맵 목업용)도 함께 만든다.
"""
import json
import sys
from pathlib import Path

from app import jobs as J
from app import journey as Y
from app import report as R
from app.content import load_content
from app.llm import coach as C

HEADER = "// 백엔드 저장소의 backend/data 와 문구 표를 그대로 옮긴 목업 데이터입니다. 직접 고치지 말고 다시 내보내세요.\n"


def dump(x) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def export(data_path: Path) -> dict[str, int]:
    """data.js 와 같은 폴더의 guides.js 를 쓴다. 내보낸 개수를 돌려준다."""
    content = load_content()
    index = J.load_job_index(content)

    cards = [{
        "card_id": c.id, "type": c.type, "title": c.title, "body": c.body, "job_id": c.job_id, "attrs": c.attrs,
        "options": [{"id": o.id, "label": o.label, "attrs": o.attrs} for o in c.options],
    } for c in content.cards]

    job_ids = sorted({c.job_id for c in content.cards if c.job_id is not None and c.job_id in index.row})
    jobs = [{
        "id": j, "name": content.jobs[j].name, "field": content.jobs[j].field,
        "summary": " ".join((content.jobs[j].summary or "").split()), "majors": content.jobs[j].majors,
        "attrs": [round(float(x) * 3) for x in index.vector(j)],
    } for j in job_ids]

    tables = {
        "dailyGoal": Y.DAILY_GOAL, "levels": Y.LEVELS, "personaNoun": Y.PERSONA_NOUN, "personaAdj": Y.PERSONA_ADJ,
        "personaTagline": Y.PERSONA_TAGLINE, "missions": Y.MISSIONS, "defaultMissions": Y.DEFAULT_MISSIONS,
        "studyTips": R.STUDY_TIPS, "interestPhrases": R.INTEREST_PHRASES, "strengthPhrases": R.STRENGTH_PHRASES,
        "minSignal": R.MIN_SIGNAL, "jobQuestions": C.QUESTIONS, "hardTips": C.HARD_TIPS,
        "crisisWords": C.CRISIS_WORDS, "crisisReply": C.CRISIS_REPLY, "chatStarters": C.STARTERS,
    }
    attributes = [{"code": a.code, "label": a.label, "group": a.group, "definition": a.definition}
                  for a in content.attributes]

    guides = {j: content.guides[j].model_dump(exclude={"job_id"}) for j in job_ids if j in content.guides}

    data_path = Path(data_path)
    data_path.write_text(
        HEADER
        + f"export const ATTRIBUTES = {dump(attributes)}\n"
        + f"export const TABLES = {dump(tables)}\n"
        + f"export const JOBS = {dump(jobs)}\n"
        + f"export const CARDS = {dump(cards)}\n",
        encoding="utf-8", newline="\n",
    )
    (data_path.parent / "guides.js").write_text(
        "// 백엔드 backend/data/job_guides.json(커리어넷 원문)에서 카드가 있는 직업만 옮긴 데이터입니다. "
        "로드맵 목업에서 씁니다. 직접 고치지 말고 scripts/export_mock.py 로 다시 내보내세요.\n"
        + f"export const GUIDES = {dump(guides)}\n",
        encoding="utf-8", newline="\n",
    )
    return {"cards": len(cards), "jobs": len(jobs), "guides": len(guides)}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("사용법: python -m scripts.export_mock <FE>/src/mocks/data.js")
    counts = export(Path(sys.argv[1]))
    print(f"{counts['cards']} cards, {counts['jobs']} jobs, {counts['guides']} guides")
