-- Portalis, часть 3: прогресс в профиле (статистика, фон, скин) и лобби пати (карта пати, «готов»).
-- Выполнить после portalis_2_profile.sql. Только добавляет.

alter table public.profiles add column if not exists stats jsonb not null default '{}'::jsonb;  -- минуты, карты, сборки...
alter table public.profiles add column if not exists banner text check (char_length(banner) <= 40);
alter table public.profiles add column if not exists skin text check (skin ~ '^[A-Za-z0-9_]{2,16}$');
alter table public.profiles drop constraint if exists profiles_stats_len;
alter table public.profiles add constraint profiles_stats_len check (pg_column_size(stats) <= 8000);
grant select (stats, banner, skin) on public.profiles to authenticated;

drop view if exists public.profiles_public;
create view public.profiles_public as
  select id, login, nick, avatar, mood, about, favorites, color, created_at, stats, banner, skin,
         case when presence = 'invisible' and id <> auth.uid() then 'offline' else status end as status,
         case when presence = 'invisible' and id <> auth.uid() then null else status_detail end as status_detail,
         case when presence = 'invisible' and id <> auth.uid() then null else last_seen end as last_seen,
         case when id = auth.uid() then presence when presence = 'dnd' then 'dnd' else 'auto' end as presence
  from public.profiles;
revoke all on public.profiles_public from anon, public;
grant select on public.profiles_public to authenticated;

-- Лобби пати: карта, которую выбрал хозяин, и «готов» у каждого участника.
alter table public.parties add column if not exists lobby jsonb;
alter table public.party_members add column if not exists ready boolean not null default false;
revoke update on public.party_members from anon, authenticated;
grant update (ready) on public.party_members to authenticated;  -- менять можно только «готов», не пати
drop policy if exists pm_ready on public.party_members;
create policy pm_ready on public.party_members for update to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
