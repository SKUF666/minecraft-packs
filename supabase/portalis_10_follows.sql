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
