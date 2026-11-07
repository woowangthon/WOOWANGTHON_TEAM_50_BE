# 백엔드 작업 체크리스트

> 기준: README.md. 완료 항목은 `[x]`, 진행 중은 `[~]`. 담당은 README 10절(BE1 = API·DB·로그·푸시, BE2 = 추천·LLM).

## 0. 기반 · 데이터 정의
- [x] 12차원 정의 `backend/data/attributes.csv`
- [x] 태깅 루브릭 `docs/dimensions.md`
- [x] 카드·직업·선택지 Pydantic 모델 + 규칙 검증 (`app/content/models.py`)
- [x] 콘텐츠 로더: 검증 → 카드 행렬(L2 정규화)·선택지 벡터 (`app/content/loader.py`)
- [x] 샘플 카드 3장 (`job_day` / `a_vs_b` / `situation`)
- [x] 콘텐츠 테스트 (`tests/test_content.py`)
- [x] `.env.example`
- [ ] `requirements.txt`에 FastAPI·uvicorn·supabase·httpx·pyjwt·pywebpush·google-genai 추가

## 1. 콘텐츠 수집 · 생성 (BE2)
- [x] 커리어넷 직업백과 수집 스크립트 `scripts/fetch_careernet.py` (API 에러 응답 시 즉시 중단)
- [x] API 키로 `--limit 5` 실행 → 응답 필드명 확인 (보정 불필요, `seq` 파라미터에 `job_cd` 사용 확인)
- [x] 전체 수집 → `data/jobs.json` (552개, 10개 분야, 전공 정보 없는 직업 66개)
- [x] 샘플 카드 `c0001.job_id`를 실제 수의사 코드(15)로 수정
- [ ] LLM 카드 생성 스크립트 (raw 캐시 → 카드 초안 + attrs 태깅, 루브릭 프롬프트)
- [ ] 검수용 CSV 내보내기 / 되돌려 넣기 스크립트
- [ ] 카드 검수 후 `reviewed: true` 반영, 분야별 최소 수량 확보
- [ ] 운영 시 `reviewed_only=True` 적용

## 2. 추천 로직 (BE2) — `app/recommend/`, DB·HTTP 의존 없음
- [ ] 반응 가중치 정의 (좋아요·싫어요·저장·패스·선택)
- [ ] 사용자 벡터 EMA 갱신 함수 (선택형은 선택지 벡터 사용)
- [ ] 랭킹: 코사인 유사도 + 이미 본 카드 제외
- [ ] 탐색: 일부 무작위 분야 섞기 (비율 설정값)
- [ ] 콜드 스타트: 분야(`Job.field`·카드 유형)별 균등 샘플링
- [ ] 추천 출처 태그 `personalized` / `random` / `cold_start`
- [ ] 단위 테스트

## 3. DB · 인증 (BE1)
- [ ] Supabase 프로젝트 생성, 키를 `.env`·Render에 설정
- [ ] 마이그레이션 `supabase/migrations/`: `profiles`, `user_vectors`, `impressions`, `reactions`, `push_subscriptions`
- [ ] RLS 정책 (service role만 쓰기)
- [ ] Supabase JWT 검증 의존성 (`app/core/`)
- [ ] 설정 로딩 (`app/core/config.py`, pydantic-settings)
- [ ] DB 접근 계층 (`app/db/`)

## 4. API (BE1, 추천 연동은 BE2)
- [ ] `main.py`: 앱 생성, CORS, 시작 시 콘텐츠 로드
- [ ] `GET /health`
- [ ] `POST /me` · `GET /me` · `PATCH /me`
- [ ] `GET /feed` (노출 로그 + 출처 저장)
- [ ] `POST /reactions` (반응 저장 + 벡터 갱신, 노출 id 연결)
- [ ] `GET /me/report`
- [ ] `POST /push/subscribe`
- [ ] 요청·응답 스키마 (`app/schemas/`), 에러 형식 통일
- [ ] API 테스트 (Supabase 목킹)

## 5. LLM (BE2) — `app/llm/`
- [ ] Gemini 클라이언트 + 타임아웃·실패 시 기본 문구
- [ ] 성향 리포트 설명 문장 프롬프트
- [ ] "왜 이 카드?" 추천 이유 문장
- [ ] 결과 캐싱 (같은 벡터·카드면 재호출 안 함)

## 6. 웹 푸시 (BE1)
- [ ] VAPID 키 생성
- [ ] "오늘의 카드" 발송 작업 (Render cron 등), 만료 구독 정리

## 7. 배포 · 마무리
- [ ] Render 배포 설정, 환경 변수 등록
- [ ] 프론트와 API 계약 확정 (`/docs` Swagger 공유)
- [ ] 맞춤 vs 무작위 반응률 비교 쿼리
- [ ] README 7절(실행 방법)·9절(AI 활용 범위 TBD) 채우기
