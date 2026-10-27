-- Portalis: шаги 8, 9 и 10 одним запросом (каждый можно выполнять повторно).
-- Portalis, шаг 8: что популярно у игроков Portalis.
-- plays - во что играли (одна строка на игрока, карту/сборку и день); top_played - самое популярное за N дней
-- (сколько разных игроков), top_rated - лучшие по оценкам (с названием и ссылкой из plays, если есть).

create table if not exists public.plays (
  id bigint generated always as identity primary key,
  user_id uuid not null references public.profiles(id) on delete cascade default auth.uid(),
  kind text not null check (kind in ('map', 'pack')),
  key text not null check (char_length(key) between 1 and 200),
  title text check (char_length(title) <= 120),
  url text check (char_length(url) <= 400),
  day date not null default current_date,
  unique (user_id, kind, key, day)
);
create index if not exists plays_day_idx on public.plays (kind, day);
alter table public.plays enable row level security;
drop policy if exists pl_insert on public.plays;
create policy pl_insert on public.plays for insert to authenticated with check (user_id = auth.uid());
drop policy if exists pl_read on public.plays;
create policy pl_read on public.plays for select to authenticated using (user_id = auth.uid());

create or replace function public.top_played(days int default 7, k text default 'map', lim int default 20)
returns table (key text, title text, url text, players bigint, plays bigint)
language sql stable security definer set search_path = public as $$
  select p.key, max(p.title), max(p.url), count(distinct p.user_id), count(*)
  from plays p
  where p.kind = k and p.day > current_date - greatest(1, least(days, 90))
  group by p.key
  order by 4 desc, 5 desc
  limit greatest(1, least(lim, 50))
$$;

create or replace function public.top_rated(min_n int default 2, lim int default 20)
returns table (key text, title text, url text, avg numeric, n bigint)
language sql stable security definer set search_path = public as $$
  select r.map_key, max(p.title), max(p.url), round(avg(r.stars)::numeric, 1), count(distinct r.user_id)
  from map_reviews r left join plays p on p.key = r.map_key and p.kind = 'map'
  group by r.map_key
  having count(distinct r.user_id) >= greatest(1, min_n)
  order by 4 desc, 5 desc
  limit greatest(1, least(lim, 50))
$$;

revoke all on function public.top_played(int, text, int) from public, anon;
revoke all on function public.top_rated(int, int) from public, anon;
grant execute on function public.top_played(int, text, int) to authenticated;
grant execute on function public.top_rated(int, int) to authenticated;

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

-- Portalis, часть 10: подписки на игроков, ссылки на каналы и соцсети в профиле, картинки на стене,
-- лента постов друзей и тех, на кого подписан. Выполнить после portalis_5_social.sql. Только добавляет.

-- ссылки в профиле: [{"kind": "youtube", "url": "https://..."}, ...]
alter table public.profiles add column if not exists links jsonb not null default '[]'::jsonb;
alter table public.profiles drop constraint if exists profiles_links_check;
alter table public.profiles add constraint profiles_links_check
  check (jsonb_typeof(links) = 'array' and jsonb_array_length(links) <= 8 and pg_column_size(links) <= 3000);
grant select (links) on public.profiles to authenticated;
drop view if exists public.profiles_public;
create view public.profiles_public as
  select id, login, nick, avatar, mood, about, favorites, color, created_at, stats, banner, skin, banner_img, frame, links,
         case when presence = 'invisible' and id <> auth.uid() then 'offline' else status end as status,
         case when presence = 'invisible' and id <> auth.uid() then null else status_detail end as status_detail,
         case when presence = 'invisible' and id <> auth.uid() then null else last_seen end as last_seen,
         case when id = auth.uid() then presence when presence = 'dnd' then 'dnd' else 'auto' end as presence
  from public.profiles;
revoke all on public.profiles_public from anon, public;
grant select on public.profiles_public to authenticated;

-- подписки: подписаться можно на любого игрока, без его согласия (как на канал)
create table if not exists public.follows (
  follower uuid not null references public.profiles(id) on delete cascade default auth.uid(),
  followee uuid not null references public.profiles(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (follower, followee),
  check (follower <> followee)
);
create index if not exists follows_followee_idx on public.follows (followee);
alter table public.follows enable row level security;
drop policy if exists fo_read on public.follows;
create policy fo_read on public.follows for select to authenticated using (true);
drop policy if exists fo_insert on public.follows;
create policy fo_insert on public.follows for insert to authenticated with check (follower = auth.uid());
drop policy if exists fo_delete on public.follows;
create policy fo_delete on public.follows for delete to authenticated using (follower = auth.uid());

create or replace function public.is_following(u uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from follows where follower = auth.uid() and followee = u)
$$;

create or replace function public.follow_stats(u uuid) returns json
language sql stable security definer set search_path = public as $$
  select json_build_object(
    'followers', (select count(*) from follows where followee = u),
    'following', (select count(*) from follows where follower = u),
    'me', exists (select 1 from follows where follower = auth.uid() and followee = u))
$$;
grant execute on function public.follow_stats(uuid) to authenticated;

-- стена: картинка к записи (ссылка на файл в хранилище media); запись может быть одной картинкой
alter table public.wall_posts add column if not exists image text
  check (image is null or (char_length(image) <= 400 and image like 'https://%'));
alter table public.wall_posts drop constraint if exists wall_posts_body_check;
alter table public.wall_posts add constraint wall_posts_body_check
  check (char_length(body) <= 500 and (char_length(body) >= 1 or image is not null));
create index if not exists wall_author_idx on public.wall_posts (author, id desc);

-- записи на своей стене (посты) видят и подписчики; на стену по-прежнему пишут только хозяин и друзья
drop policy if exists wall_read on public.wall_posts;
create policy wall_read on public.wall_posts for select to authenticated
  using (owner = auth.uid() or author = auth.uid() or public.is_friend(owner)
         or (author = owner and public.is_following(owner)));

-- события ленты (достижения, уровни) - тоже подписчикам
drop policy if exists fe_read on public.feed_events;
create policy fe_read on public.feed_events for select to authenticated
  using (user_id = auth.uid() or public.is_friend(user_id) or public.is_following(user_id));

-- реакции на посты видят и ставят подписчики
create or replace function public.can_see(tgt text, tid bigint) returns boolean
language sql stable security definer set search_path = public as $$
  select case when tgt = 'wall' then exists (
           select 1 from wall_posts w where w.id = tid and (w.owner = auth.uid() or w.author = auth.uid() or is_friend(w.owner)
                                                            or (w.author = w.owner and is_following(w.owner))))
         else exists (
           select 1 from messages m where m.id = tid and (
             (m.party_id is not null and is_party_member(m.party_id))
             or (m.to_user is not null and (m.to_user = auth.uid() or m.from_user = auth.uid())))) end
$$;
