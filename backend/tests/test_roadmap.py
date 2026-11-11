from app.content import JobGuide, load_content
from app.roadmap import _grow_items, build_roadmap, saved_job_ids

CONTENT = load_content()
VET = 15
CARD_JOBS = {c.job_id for c in CONTENT.cards if c.job_id is not None}
NO_CARD_JOB = next(j for j in CONTENT.jobs if j not in CARD_JOBS)


def test_steps_are_in_order_and_built_from_guide():
    r = build_roadmap(CONTENT, VET, [0.3] * 11)
    guide = CONTENT.guides[VET]
    assert [s.key for s in r.steps] == ["explore", "grow", "study", "career"]
    explore, grow, study, career = r.steps
    assert [(i.title, i.detail) for i in explore.items] == [(a.title, a.detail) for a in guide.activities]
    assert [i.title for i in grow.items] == guide.abilities
    assert study.items[-1].title == "관련 학과" and "수의학과" in study.items[-1].detail
    assert {i.title for i in career.items} <= {"자격증", "진출 분야"}


def test_traits_compare_job_card_with_my_scores():
    r = build_roadmap(CONTENT, VET, [0.3] * 11)
    card = CONTENT.card(r.card_id)
    assert card.job_id == VET
    assert [t.job_score for t in r.traits] == sorted((s for s in card.attrs if s), reverse=True)
    assert {t.my_score for t in r.traits} == {0.3}


def test_job_without_card_still_has_roadmap():
    r = build_roadmap(CONTENT, NO_CARD_JOB, [0.0] * 11)
    assert r.card_id is None and r.traits == [] and r.steps


def test_unknown_job_is_none():
    assert build_roadmap(CONTENT, 999999, [0.0] * 11) is None


def test_empty_steps_are_dropped():
    job = next(iter(CONTENT.jobs.values()))
    guides = dict(CONTENT.guides)
    guides[job.id] = JobGuide(job_id=job.id)
    store = type(CONTENT)(**{**vars(CONTENT), "guides": guides})
    keys = [s.key for s in build_roadmap(store, job.id, [0.0] * 11).steps]
    assert keys == (["study"] if job.majors else [])


def test_abilities_get_matching_aptitude_or_default_sentence():
    guide = JobGuide(job_id=1, abilities=["수리·논리력", "창의력"],
                     aptitudes=["논리적으로 사고하는 수리·논리력이 필요합니다.", "꼼꼼함이 필요합니다."])
    items = [(i.title, i.detail) for i in _grow_items(guide)]
    assert items == [
        ("수리·논리력", "논리적으로 사고하는 수리·논리력이 필요합니다."),
        ("창의력", "창의력이 필요해요."),
        (None, "꼼꼼함이 필요합니다."),
    ]


def test_saved_job_ids_keep_newest_and_skip_non_job_cards():
    job_cards = [c for c in CONTENT.cards if c.type == "job_day"][:2]
    choice = next(c for c in CONTENT.cards if c.options)
    reactions = [
        {"card_id": job_cards[1].id, "reaction": "save", "created_at": "t3"},
        {"card_id": choice.id, "reaction": "save", "created_at": "t2"},
        {"card_id": job_cards[0].id, "reaction": "like", "created_at": "t1"},
        {"card_id": job_cards[1].id, "reaction": "save", "created_at": "t0"},
    ]
    assert saved_job_ids(CONTENT, reactions) == [(job_cards[1].job_id, "t3")]
