-- 맞춤(personalized) vs 무작위(random) 추천 반응 비교. Supabase SQL Editor에서 실행 (읽기 전용).
--
-- 지표
--   끌려요 비율 = 끌려요 / (끌려요 + 별로예요)   ← 주 지표. 선택형 카드의 '선택'은 선호가 아니라서 뺀다
--   반응률     = 반응한 노출 / 전체 노출
--   저장률     = 저장한 카드 / 전체 노출 (saved_cards, 같은 사용자·카드)
-- z = 맞춤과 무작위의 끌려요 비율 차이를 두 비율 z-검정으로 본 값. |z| ≥ 1.96이면 유의수준 5%에서 차이가 있다.
--
-- 주의
--   - 콜드 스타트(cold_start)는 반응 5개 전 무작위 노출이라 비교에서 빼고 참고로만 보여 준다.
--   - 맞춤 피드도 20%는 무작위 슬롯(source='random')이 섞인다. 같은 사용자·같은 시기에 섞여 나오므로 공정한 비교다.
--   - E2E 테스트 계정(e2e-*@example.com)과 시드 계정(demo-*)은 뺀다. 스크립트가 만든 반응이라 실제 결과가 아니다.
--   - 반응 수가 적으면 차이가 우연일 수 있다. 결과에는 n(노출·반응 수)을 함께 적는다.

with base as (
  select i.source, i.user_id, r.reaction, (s.card_id is not null) as saved
  from public.impressions i
  join auth.users u on u.id = i.user_id
   and u.email not like 'e2e-%@example.com'   -- E2E 테스트 계정
   and u.email not like 'demo-%'              -- 심사위원·시연용 시드 계정 (scripts/seed_demo.py)
  left join public.reactions r on r.impression_id = i.id
  left join public.saved_cards s on s.user_id = i.user_id and s.card_id = i.card_id
),
by_source as (
  select
    source,
    count(*)                                            as impressions,
    count(distinct user_id)                             as users,
    count(reaction)                                     as reacted,
    count(*) filter (where reaction = 'like')           as likes,
    count(*) filter (where reaction = 'dislike')        as dislikes,
    count(*) filter (where reaction = 'choose')         as chooses,
    count(*) filter (where saved)                       as saves
  from base
  group by source
)
select
  source                                                                       as "출처",
  users                                                                        as "사용자",
  impressions                                                                  as "노출",
  reacted                                                                      as "반응",
  likes                                                                        as "끌려요",
  dislikes                                                                     as "별로예요",
  chooses                                                                      as "선택",
  saves                                                                        as "저장",
  round(100.0 * likes / nullif(likes + dislikes, 0), 1)                        as "끌려요 비율(%)",
  round(100.0 * reacted / nullif(impressions, 0), 1)                           as "반응률(%)",
  round(100.0 * saves / nullif(impressions, 0), 1)                             as "저장률(%)",
  (select round(
     ((p.likes::numeric / nullif(p.likes + p.dislikes, 0)) - (q.likes::numeric / nullif(q.likes + q.dislikes, 0)))
     / nullif(sqrt(
         ((p.likes + q.likes)::numeric / nullif(p.likes + p.dislikes + q.likes + q.dislikes, 0))
         * (1 - (p.likes + q.likes)::numeric / nullif(p.likes + p.dislikes + q.likes + q.dislikes, 0))
         * (1.0 / nullif(p.likes + p.dislikes, 0) + 1.0 / nullif(q.likes + q.dislikes, 0))
       ), 0), 2)
   from by_source p, by_source q
   where p.source = 'personalized' and q.source = 'random' and b.source = 'personalized')     as "z (맞춤 vs 무작위)"
from by_source b
order by array_position(array['personalized', 'random', 'cold_start'], source);
