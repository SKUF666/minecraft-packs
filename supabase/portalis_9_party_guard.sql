-- Portalis, шаг 9: защита пати (выполнить в SQL Editor Supabase один раз, кнопка Run).
-- 1) Участник пати может менять лобби и код приглашения в игру, но НЕ хозяина, открытость, название и настройки
--    пати - это только хозяин (раньше политика party_update пускала любого участника менять любые колонки).
-- 2) Лобби меняется одной командой на сервере (слияние полей), без «прочитал - поменял - записал»:
--    два участника одновременно больше не затирают друг другу поля.
-- 3) «Стать хозяином» удаётся, только если хозяин ещё тот, кого видел игрок (двое сразу хозяевами не станут).

create or replace function public.party_guard() returns trigger
language plpgsql as $$
begin
  if auth.uid() is not null and auth.uid() is distinct from old.owner then
    if new.owner is distinct from old.owner
       or new.name is distinct from old.name
       or new.is_public is distinct from old.is_public
       or new.plan is distinct from old.plan
       or new.launcher is distinct from old.launcher
       or new.version is distinct from old.version
       or new.max_players is distinct from old.max_players then
      raise exception 'only the party owner can change party settings' using errcode = '42501';
    end if;
  end if;
  return new;
end $$;

drop trigger if exists party_guard on public.parties;
create trigger party_guard before update on public.parties
  for each row execute function public.party_guard();

create or replace function public.party_lobby_merge(p uuid, patch jsonb) returns jsonb
language sql security invoker as $$
  update public.parties set lobby = coalesce(lobby, '{}'::jsonb) || coalesce(patch, '{}'::jsonb)
   where id = p
  returning lobby;
$$;

create or replace function public.party_take_host(p uuid, expected uuid) returns boolean
language plpgsql security invoker as $$
declare n int;
begin
  update public.parties
     set lobby = coalesce(lobby, '{}'::jsonb) || jsonb_build_object('host', auth.uid())
   where id = p
     and coalesce(nullif(lobby->>'host', '')::uuid, owner) is not distinct from expected;
  get diagnostics n = row_count;
  return n > 0;
end $$;

revoke all on function public.party_lobby_merge(uuid, jsonb) from public, anon;
revoke all on function public.party_take_host(uuid, uuid) from public, anon;
grant execute on function public.party_lobby_merge(uuid, jsonb) to authenticated;
grant execute on function public.party_take_host(uuid, uuid) to authenticated;
