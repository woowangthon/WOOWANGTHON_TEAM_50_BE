"""목업 내보내기 스크립트가 실제로 실행되고, FE가 읽는 형식을 지키는지 확인한다."""
import json
import re

from app.content import load_content
from scripts.export_mock import export

EXPORT = re.compile(r"^export const (\w+) = (.+)$", re.M)


def read_exports(path) -> dict:
    return {name: json.loads(body) for name, body in EXPORT.findall(path.read_text(encoding="utf-8"))}


def test_export_writes_data_and_guides(tmp_path):
    counts = export(tmp_path / "data.js")
    data, guides = read_exports(tmp_path / "data.js"), read_exports(tmp_path / "guides.js")
    content = load_content()

    assert set(data) == {"ATTRIBUTES", "TABLES", "JOBS", "CARDS"} and set(guides) == {"GUIDES"}
    assert len(data["CARDS"]) == counts["cards"] == len(content.cards)
    assert len(data["ATTRIBUTES"]) == len(content.attributes)
    # FE 목업(mockBackend.js)이 읽는 문구 표 키
    assert {"dailyGoal", "minSignal", "jobQuestions", "crisisWords", "chatStarters", "studyTips"} <= set(data["TABLES"])

    job_ids = {j["id"] for j in data["JOBS"]}
    assert counts["guides"] > 0 and {int(k) for k in guides["GUIDES"]} <= job_ids
    guide = next(iter(guides["GUIDES"].values()))
    assert set(guide) == {"activities", "abilities", "aptitudes", "education", "certificates", "careers"}
