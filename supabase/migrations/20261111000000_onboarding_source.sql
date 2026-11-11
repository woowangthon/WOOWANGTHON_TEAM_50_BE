-- 가입 설문 카드 노출을 피드 노출과 구분해서 기록한다 (impressions.source = 'onboarding').
-- 기본 이름의 check 제약(impressions_source_check)을 바꾼다.
alter table public.impressions drop constraint impressions_source_check;
alter table public.impressions
  add constraint impressions_source_check
  check (source in ('personalized', 'random', 'cold_start', 'onboarding'));
