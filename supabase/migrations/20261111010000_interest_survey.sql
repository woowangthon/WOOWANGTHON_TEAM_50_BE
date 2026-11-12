-- 관심사 설문(프론트 /interests, 12문항) 답변 저장과 성향 벡터 반영을 한 트랜잭션으로 처리한다.
-- 사용자당 한 번만 반영한다 (다시 보내면 'duplicate', 벡터는 그대로).
-- 새 벡터는 백엔드가 계산하고, p_expected_version으로 그사이 다른 갱신이 없었는지 확인한다.
-- 설문은 피드 반응이 아니므로 n_reactions는 늘리지 않는다.
-- 반환값: 'recorded' | 'duplicate' | 'conflict'(다시 읽고 재시도)

create table public.interest_surveys (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  version    smallint not null,
  answers    jsonb not null,
  created_at timestamptz not null default now()
);
alter table public.interest_surveys enable row level security;

create function public.record_interest_survey(
  p_user_id          uuid,
  p_version          smallint,
  p_answers          jsonb,
  p_vector           real[],
  p_expected_version integer
) returns text
language plpgsql
set search_path = ''
as $$
begin
  insert into public.interest_surveys (user_id, version, answers)
  values (p_user_id, p_version, p_answers)
  on conflict (user_id) do nothing;

  if not found then
    return 'duplicate';
  end if;

  if p_expected_version is null then
    insert into public.user_vectors (user_id, vector, n_reactions, version)
    values (p_user_id, p_vector, 0, 1)
    on conflict (user_id) do nothing;
  else
    update public.user_vectors
    set vector = p_vector,
        version = version + 1,
        updated_at = now()
    where user_id = p_user_id and version = p_expected_version;
  end if;

  if not found then
    delete from public.interest_surveys where user_id = p_user_id;
    return 'conflict';
  end if;

  return 'recorded';
end;
$$;

revoke all on function public.record_interest_survey(uuid, smallint, jsonb, real[], integer)
  from public, anon, authenticated;
grant execute on function public.record_interest_survey(uuid, smallint, jsonb, real[], integer)
  to service_role;
