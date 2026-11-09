-- FIND-ME 초기 스키마
-- 사용자가 만드는 데이터만 저장한다. 카드·직업은 레포 JSON이므로 card_id는 FK 없이 text로 둔다.
-- 모든 접근은 백엔드(service role)를 거친다. RLS를 켜고 정책을 두지 않아 anon·authenticated는 차단된다.

create table public.profiles (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  nickname   text not null check (char_length(nickname) between 1 and 20),
  grade      smallint check (grade between 1 and 3),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.user_vectors (
  user_id     uuid primary key references auth.users (id) on delete cascade,
  vector      real[] not null check (cardinality(vector) = 11),
  n_reactions integer not null default 0 check (n_reactions >= 0),
  version     integer not null default 0,   -- 동시 갱신 감지용 (낙관적 잠금)
  updated_at  timestamptz not null default now()
);

create table public.impressions (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,
  card_id    text not null,
  source     text not null check (source in ('personalized', 'random', 'cold_start')),
  score      real,                          -- 코사인 유사도. cold_start는 null
  created_at timestamptz not null default now(),
  unique (id, user_id, card_id)             -- reactions의 복합 FK 대상
);
create index impressions_user_card_idx on public.impressions (user_id, card_id);
create index impressions_user_created_idx on public.impressions (user_id, created_at desc);

create table public.reactions (
  id            uuid primary key default gen_random_uuid(),
  impression_id uuid not null unique,       -- 노출당 반응 1개 (재시도로 벡터가 두 번 갱신되는 것 방지)
  user_id       uuid not null,
  card_id       text not null,
  reaction      text not null check (reaction in ('like', 'dislike', 'save', 'pass', 'choose')),
  option_id     text,
  created_at    timestamptz not null default now(),
  -- 반응의 사용자·카드는 노출 기록과 항상 일치해야 한다
  foreign key (impression_id, user_id, card_id)
    references public.impressions (id, user_id, card_id) on delete cascade,
  -- option_id는 choose일 때만 있다 (recommend.vector.target_vector와 같은 규칙)
  check ((reaction = 'choose') = (option_id is not null))
);
create index reactions_user_created_idx on public.reactions (user_id, created_at desc);

create table public.push_subscriptions (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,
  endpoint   text not null unique,
  p256dh     text not null,
  auth       text not null,
  created_at timestamptz not null default now()
);
create index push_subscriptions_user_idx on public.push_subscriptions (user_id);

alter table public.profiles           enable row level security;
alter table public.user_vectors       enable row level security;
alter table public.impressions        enable row level security;
alter table public.reactions          enable row level security;
alter table public.push_subscriptions enable row level security;
