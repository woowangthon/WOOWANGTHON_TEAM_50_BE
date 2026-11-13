# 백엔드 작업 체크리스트

> 기준: README.md. 완료 항목은 `[x]`, 진행 중은 `[~]`. 담당은 README 10절(BE1 = API·DB·로그, BE2 = 추천·LLM).

## 0. 기반 · 데이터 정의
- [x] 11차원 정의 `backend/data/attributes.csv` (2026-11-08 stability 삭제, 12 → 11)
- [x] 태깅 루브릭 `docs/dimensions.md`
- [x] 카드·직업·선택지 Pydantic 모델 + 규칙 검증 (`app/content/models.py`)
- [x] 콘텐츠 로더: 검증 → 카드 행렬(L2 정규화)·선택지 벡터 (`app/content/loader.py`)
- [x] 샘플 카드 3장 (`job_day` / `a_vs_b` / `situation`)
- [x] 콘텐츠 테스트 (`tests/test_content.py`)
- [x] `.env.example`
- [x] `requirements.txt`에 FastAPI·uvicorn·supabase·httpx·pyjwt·google-genai 추가 (pywebpush는 웹 푸시 미적용으로 뺌)

## 1. 콘텐츠 수집 · 생성 (BE2)
- [x] 커리어넷 직업백과 수집 스크립트 `scripts/fetch_careernet.py` (API 에러 응답 시 즉시 중단)
- [x] API 키로 `--limit 5` 실행 → 응답 필드명 확인 (보정 불필요, `seq` 파라미터에 `job_cd` 사용 확인)
- [x] 전체 수집 → `data/jobs.json` (552개, 10개 분야, 전공 정보 없는 직업 66개)
- [x] 샘플 카드 `c0001.job_id`를 실제 수의사 코드(15)로 수정
- [x] LLM 카드 생성 스크립트 `scripts/generate_cards.py` (raw 캐시 → `job_day` 카드 초안 + attrs 태깅, 루브릭 프롬프트, 검증 실패 시 재요청)
- [x] Gemini 키로 `--per-field 1` 시범 실행 (9장) → 가치 축이 비는 문제 발견, 프롬프트 수정
- [x] 직업 552개 전수 태깅·분석 `scripts/tag_jobs.py` → `data/analysis/job_tags.csv`, 분석은 `docs/job-tagging-analysis.md`
- [x] 태깅 규칙 결정: 기존 2~4축 규칙 유지, stability 삭제까지만 반영
- [x] 시범 카드 재생성 (묶음 생성으로 전환, `--batch` 기본 10장)
- [x] 직업 카드 분야별 12장 생성 (120장, 미검수)
- [x] 선택형 카드(`a_vs_b` / `situation`) 축별 8장 이상 (38장) + 가입 설문 카드 5장 (미검수)
- [x] 검수용 CSV 내보내기 / 되돌려 넣기 스크립트 (`scripts/review_cards.py`)
- [ ] 카드 검수 후 `reviewed: true` 반영, 분야별 최소 수량 확보
- [ ] 운영 시 `reviewed_only=True` 적용

## 2. 추천 로직 (BE2) — `app/recommend/`, DB·HTTP 의존 없음
- [x] 반응 가중치 정의: 저장 1.5 · 좋아요 1.0 · 선택 1.0 · 패스 0 · 싫어요 −0.5 (`vector.py`)
- [x] 사용자 벡터 EMA 갱신 함수 (선택형은 선택지 벡터 사용, α=0.2, [0,1] 클립)
- [x] 랭킹: 코사인 유사도 + 이미 본 카드 제외 (`rank.py`)
- [x] 탐색: 무작위 슬롯 20%, 맞춤 결과에 없는 분야 우선 (첫 장은 항상 맞춤)
- [x] 콜드 스타트: 가입 설문(5장)을 건너뛰어 반응 5개 미만이거나 벡터가 0이면 전체 카드에서 무작위 (2026-11-11 분야별 균등 샘플링 → 완전 무작위)
- [x] 추천 출처 태그 `personalized` / `random` / `cold_start` + 점수(코사인)
- [x] 단위 테스트 (`tests/test_recommend.py`)
- [x] 카드 소진 정책: 다시 보여 주지 않고 빈 목록 반환. 반응하지 않은 카드는 소진하지 않고 다음 피드에 재노출

## 3. DB · 인증 (BE1)
- [x] Supabase 프로젝트 생성, 키를 `.env`·Render에 설정
- [x] 마이그레이션 `supabase/migrations/`: `profiles`, `user_vectors`, `impressions`, `reactions`, `push_subscriptions`
- [ ] RLS 정책 (service role만 쓰기)
- [x] Supabase JWT 검증 의존성 (`app/core/auth.py`, JWKS · ES256)
- [x] 설정 로딩 (`app/core/config.py`, pydantic-settings)
- [~] DB 접근 계층 (`app/db/`) — 프로필·벡터 조회 완료

## 4. API (BE1, 추천 연동은 BE2)
- [x] `main.py`: 앱 생성, CORS, 시작 시 콘텐츠 로드
- [x] `GET /health`
- [x] `POST /me` · `GET /me` · `PATCH /me` (테스트는 저장소 대역 사용, 실제 DB 연동 확인 필요)
- [x] `GET /feed` (노출 로그 + 출처 저장)
- [x] `POST /reactions` (반응 저장 + 벡터 갱신을 `record_reaction` 트랜잭션으로 처리, 재시도 시 중복 방지)
- [x] `GET /me/report` — 점수 기반 (흥미 유형 상위 2 · 방식/가치 상위 2 · 어울리는 직업 5 · 관련 학과 5 · 공부 팁), 좋아요·선택이 1개 이상이면 열림 (2026-11-11 최소 5개 → 1개, FE 요청)
- [x] `GET /me/saved` — 저장한 카드 목록 (최신 순)
- [x] 저장을 반응에서 분리 (2026-11-11, FE 요청): `saved_cards` 테이블 · `PUT·DELETE /me/saved/{card_id}` · 피드 카드 `saved` · `POST /reactions`의 `save`는 422. 기존 save 반응은 마이그레이션으로 옮김
- [x] ~~`GET /onboarding` (선택형 카드 5장 가입 설문)~~ — 2026-11-13 삭제. FE가 호출하지 않고 12문항 관심사 설문(`POST /interest-survey`)만 쓰기로 결정. 카드 c0160~c0164는 일반 피드 카드로 남음
- [x] 마이그레이션 `20261111000000_onboarding_source.sql` 적용 확인 (2026-11-12, `scripts/check_deploy.py`). `/onboarding` 삭제 후에도 마이그레이션은 이력이라 그대로 둔다 (DB 제약에 `onboarding` 값이 남아 있지만 쓰지 않음)
- [x] 피드 카드에 관련 학과 `majors` 추가
- [x] 직업 로드맵 `GET /me/roadmap` (저장한 직업 카드마다, `saved_cards` 기준) · `GET /me/roadmap/{job_id}` (미리보기). 단계: 지금 해 볼 수 있는 일 → 키워 볼 능력 → 진학 준비 → 자격과 진출, 커리어넷 원문 (`data/job_guides.json`, `scripts/build_job_guides.py`)
- [ ] 요청·응답 스키마 (`app/schemas/`), 에러 형식 통일
- [ ] API 테스트 (Supabase 목킹)

## 5. LLM (BE2) — `app/llm/`
- [ ] Gemini 클라이언트 + 타임아웃·실패 시 기본 문구
- [ ] 성향 리포트 설명 문장 프롬프트
- [ ] "왜 이 카드?" 추천 이유 문장
- [ ] 결과 캐싱 (같은 벡터·카드면 재호출 안 함)

## 6. 웹 푸시 — 미적용 (2026-11-13 결정)
- ~~`POST /push/subscribe` · VAPID 키 · "오늘의 카드" 발송 작업~~ — 마감 전 넣지 않기로 함. README 9절 "미적용"에 명시. `push_subscriptions` 테이블은 초기 마이그레이션 이력이라 남겨 둠 (쓰지 않음)

## 7. 배포 · 마무리
- [x] Render 배포 — https://findme-api-rxg3.onrender.com (2026-11-13, `render.yaml` Blueprint, 환경 변수 등록 완료)
- [x] 마이그레이션 `20261111010000_interest_survey.sql` · `20261111020000_saved_cards.sql` 적용 (2026-11-13, 5개 전부 적용 확인)
- [x] 배포 점검 `python -m scripts.check_deploy --api https://findme-api-rxg3.onrender.com --origin <프론트 주소>` 13/13 (2026-11-13, origin은 localhost)
- [ ] 프론트 배포 후 Render `CORS_ORIGINS`에 프론트 주소 추가
- [x] 카드 검수 114장 (2026-11-13, 직업 100 = 분야별 10 · 선택형 14) — `data/raw/review/demo_cards*.csv` → `review_cards import`
- [ ] Render `CONTENT_REVIEWED_ONLY=true`
- [x] 시드 스크립트 `scripts/seed_demo.py` (2026-11-13, 배포 서버에서 임시 계정으로 검증 후 삭제)
- [ ] 심사위원 계정 만들기 — 카드 검수·`CONTENT_REVIEWED_ONLY` 결정·이 브랜치 배포 뒤에 `--reset`으로 실행
- [x] `GET /interest-survey` (설문 완료 여부) — FE가 localStorage 대신 이걸로 설문 화면을 건너뛰어야 새 브라우저에서도 설문이 다시 안 뜸
- [x] keepalive (`.github/workflows/keepalive.yml`, 10분마다 `/health`)
- [ ] 프론트와 API 계약 확정 (`/docs` Swagger 공유)
- [x] 맞춤 vs 무작위 반응률 비교 쿼리 `supabase/queries/source_reaction_rates.sql` (끌려요 비율 · 반응률 · 저장률 · z값, E2E 계정 제외). 2026-11-13 기준 실제 데이터 0건 → 테스터 데이터 수집 필요
- [ ] README 7절(실행 방법)·9절(AI 활용 범위 TBD) 채우기
