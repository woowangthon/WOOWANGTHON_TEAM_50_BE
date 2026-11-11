from scripts.build_job_guides import MAX_ITEMS, to_activity, to_guide


def test_activity_splits_title_and_detail_with_hyphen_or_en_dash():
    assert to_activity("생물 동아리 - 실험을 해 봐요.") == {"title": "생물 동아리", "detail": "실험을 해 봐요."}
    assert to_activity("박람회 관람 – 정보를 얻어요.") == {"title": "박람회 관람", "detail": "정보를 얻어요."}
    assert to_activity("구분자 없는 문장-그대로") == {"title": None, "detail": "구분자 없는 문장-그대로"}


def test_guide_extracts_fields_and_skips_blank_items():
    detail = {
        "researchList": [None, {"research": "멘토링  참여 - 직업을 알아봐요."}, {"research": ""}],
        "abilityList": [{"SORT_ORDR": "08", "ability_name": "자기성찰능력"}, {"SORT_ORDR": "02", "ability_name": "수리·논리력"}],
        "aptitudeList": [{"aptitude": "논리력이 필요합니다."}],
        "jobReadyList": {
            "curriculum": [{"curriculum": "수의학과 졸업"}],
            "training": [{"training": "현장 실습"}],
            "certificate": [{"certificate": "수의사 면허"}, {"certificate": "수의사 면허"}],
            "recruit": [],
        },
    }
    g = to_guide(15, detail)
    assert g["activities"] == [{"title": "멘토링 참여", "detail": "직업을 알아봐요."}]
    assert g["abilities"] == ["수리·논리력", "자기성찰능력"]                  # SORT_ORDR 순
    assert g["education"] == ["수의학과 졸업", "현장 실습"]
    assert g["certificates"] == ["수의사 면허"]                               # 중복 제거
    assert g["careers"] == []


def test_guide_limits_items_and_returns_none_when_empty():
    many = {"aptitudeList": [{"aptitude": f"적성 {i}"} for i in range(10)]}
    assert len(to_guide(1, many)["aptitudes"]) == MAX_ITEMS
    assert to_guide(1, {}) is None and to_guide(1, None) is None
