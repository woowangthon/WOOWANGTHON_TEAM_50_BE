-- 저장(북마크)을 반응에서 분리한다. 저장은 성향 벡터를 바꾸지 않고, 언제든 취소할 수 있다.
-- 카드당 한 번만 저장된다 (PK). card_id는 레포 JSON의 ID라 FK 없이 text.

create table public.saved_cards (
  user_id    uuid not null references auth.users (id) on delete cascade,
  card_id    text not null,
  created_at timestamptz not null default now(),
  primary key (user_id, card_id)
);
create index saved_cards_user_created_idx on public.saved_cards (user_id, created_at desc);
alter table public.saved_cards enable row level security;

-- 지금까지 save 반응으로 저장한 카드를 옮긴다 (같은 카드는 가장 최근 저장 시각으로).
-- 기존 save 반응 행과 그 반응이 바꾼 성향 벡터는 그대로 둔다.
insert into public.saved_cards (user_id, card_id, created_at)
select user_id, card_id, max(created_at)
from public.reactions
where reaction = 'save'
group by user_id, card_id
on conflict do nothing;
