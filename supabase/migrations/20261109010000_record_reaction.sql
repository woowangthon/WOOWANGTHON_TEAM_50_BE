-- 반응 저장과 성향 벡터 갱신을 한 트랜잭션으로 처리한다.
-- 새 벡터는 백엔드가 계산하고, p_expected_version으로 그사이 다른 갱신이 없었는지 확인한다.
-- p_expected_version이 null이면 아직 벡터 행이 없는 사용자다.
-- 반환값: 'recorded' | 'duplicate'(이미 반응한 노출) | 'conflict'(다시 읽고 재시도)

create function public.record_reaction(
  p_impression_id    uuid,
  p_user_id          uuid,
  p_card_id          text,
  p_reaction         text,
  p_option_id        text,
  p_vector           real[],
  p_expected_version integer
) returns text
language plpgsql
set search_path = ''
as $$
begin
  insert into public.reactions (impression_id, user_id, card_id, reaction, option_id)
  values (p_impression_id, p_user_id, p_card_id, p_reaction, p_option_id)
  on conflict (impression_id) do nothing;

  if not found then
    return 'duplicate';
  end if;

  if p_expected_version is null then
    insert into public.user_vectors (user_id, vector, n_reactions, version)
    values (p_user_id, p_vector, 1, 1)
    on conflict (user_id) do nothing;
  else
    update public.user_vectors
    set vector = p_vector,
        n_reactions = n_reactions + 1,
        version = version + 1,
        updated_at = now()
    where user_id = p_user_id and version = p_expected_version;
  end if;

  if not found then
    delete from public.reactions where impression_id = p_impression_id;
    return 'conflict';
  end if;

  return 'recorded';
end;
$$;

-- public 스키마 함수는 PostgREST로 노출되므로 백엔드만 호출할 수 있게 막는다
revoke all on function public.record_reaction(uuid, uuid, text, text, text, real[], integer)
  from public, anon, authenticated;
grant execute on function public.record_reaction(uuid, uuid, text, text, text, real[], integer)
  to service_role;
