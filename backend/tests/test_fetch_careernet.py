from app.content import Job
import pytest

from scripts.fetch_careernet import CareernetApiError, check_api_error, to_job

LIST_ITEM = {"job_cd": "396", "job_nm": " 수의사 ", "top_nm": "보건·의료", "work": "목록 요약"}
DETAIL = {
    "baseInfo": {"job_cd": 396, "job_nm": "수의사"},
    "workList": [{"work": "동물을 진료한다."}, {"work": "수술을\n한다."}],
    "departList": [{"depart_name": "수의예과"}, {"depart_name": "수의예과"}, {"depart_name": "수의학과"}],
}


def test_to_job_with_detail():
    j = to_job(LIST_ITEM, DETAIL)
    assert j == {
        "id": 396, "name": "수의사", "field": "보건·의료",
        "summary": "동물을 진료한다. 수술을 한다.",
        "majors": ["수의예과", "수의학과"],
    }
    Job.model_validate(j)


def test_to_job_without_detail_falls_back_to_list():
    j = to_job({**LIST_ITEM, "top_nm": None, "tob_nm": "보건"}, None)
    assert j["summary"] == "목록 요약" and j["majors"] == [] and j["field"] == "보건"


def test_summary_truncated():
    j = to_job(LIST_ITEM, {"workList": [{"work": "가" * 500}]})
    assert len(j["summary"]) == 300 and j["summary"].endswith("…")


def test_api_error_response_raises():
    with pytest.raises(CareernetApiError, match="인증키"):
        check_api_error({"hashMap": {"code": "-2", "message": "인증키 없습니다."}})


def test_normal_response_passes():
    check_api_error({"count": 1, "jobs": [{"job_cd": 1}]})
