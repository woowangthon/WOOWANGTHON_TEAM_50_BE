"""백엔드 데이터 → 프론트 목업 데이터(src/mocks/data.js). backend/ 에서: python -m scripts.export_mock <FE>/src/mocks/data.js"""
import json
import sys

from app import jobs as J
from app import journey as Y
from app import report as R
from app.llm import coach as C
from app.content import load_content

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
    "activityTips": "highSchoolTips": J."minSignal": R.MIN_SIGNAL, "jobQuestions": C.QUESTIONS, "hardTips": C.HARD_TIPS,
    "crisisWords": C.CRISIS_WORDS, "crisisReply": C.CRISIS_REPLY, "chatStarters": C.STARTERS,
}
attributes = [{"code": a.code, "label": a.label, "group": a.group, "definition": a.definition} for a in content.attributes]

dump = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))
out = (
    "// 백엔드 저장소의 backend/data 와 문구 표를 그대로 옮긴 목업 데이터입니다. 직접 고치지 말고 다시 내보내세요.\n"
    f"export const ATTRIBUTES = {dump(attributes)}\n"
    f"export const TABLES = {dump(tables)}\n"
    f"export const JOBS = {dump(jobs)}\n"
    f"export const CARDS = {dump(cards)}\n"
)
open(sys.argv[1], "w", encoding="utf-8", newline="\n").write(out)
print(len(cards), "cards", len(jobs), "jobs", len(out) // 1024, "KB")
