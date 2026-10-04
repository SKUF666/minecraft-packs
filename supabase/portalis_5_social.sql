-- Portalis, часть 5: свой фон профиля и рамка аватара, лента друзей с лайками, реакции и ответы,
-- оценки карт, хранилище (картинки в чате, миры в облаке), сборка как предложение в пати.
-- Выполнить после portalis_4_proposals.sql. Только добавляет.

-- профиль: своя картинка шапки (маленький JPEG data:) и рамка аватара
alter table public.profiles add column if not exists banner_img text check (char_length(banner_img) <= 90000);
alter table public.profiles add column if not exists frame text check (char_length(frame) <= 30);
grant select (banner_img, frame) on public.profiles to authenticated;
drop view if exists public.profiles_public;
create view public.profiles_public as
  select id, login, nick, avatar, mood, about, favorites, color, created_at, stats, banner, skin, banner_img, frame,
         case when presence = 'invisible' and id <> auth.uid() then 'offline' else status end as status,
         case when presence = 'invisible' and id <> auth.uid() then null else status_detail end as status_detail,
         case when presence = 'invisible' and id <> auth.uid() then null else last_seen end as last_seen,
         case when id = auth.uid() then presence when presence = 'dnd' then 'dnd' else 'auto' end as presence
  from public.profiles;
revoke all on public.profiles_public from anon, public;
grant select on public.profiles_public to authenticated;

-- предложения: ещё и сборка целиком (список модов конструктора)
alter table public.party_proposals drop constraint if exists party_proposals_kind_check;
alter table public.party_proposals add constraint party_proposals_kind_check check (kind in ('map', 'web', 'server', 'text', 'pack'));
alter table public.party_proposals drop constraint if exists party_proposals_payload_check;
alter table public.party_proposals add constraint party_proposals_payload_check check (pg_column_size(payload) <= 12000);

-- стена: ответы
alter table public.wall_posts add column if not exists reply_to bigint references public.wall_posts(id) on delete cascade;

-- лента друзей: события (достижение, карта, сборка...) и лайки
create table if not exists public.feed_events (
  id bigint generated always as identity primary key,
  user_id uuid not null references public.profiles(id) on delete cascade default auth.uid(),
  kind text not null check (kind in ('achievement', 'map', 'pack', 'level', 'party', 'text')),
  text text not null check (char_length(text) between 1 and 200),
  payload jsonb not null default '{}'::jsonb check (pg_column_size(payload) <= 2000),
  created_at timestamptz not null default now()
);
create index if not exists feed_user_idx on public.feed_events (user_id, id desc);
create table if not exists public.feed_likes (
  event_id bigint not null references public.feed_events(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade default auth.uid(),
  primary key (event_id, user_id)
);
create or replace function public.feed_owner(eid bigint) returns uuid
language sql stable security definer set search_path = public as $$ select user_id from feed_events where id = eid $$;
alter table public.feed_events enable row level security;
alter table public.feed_likes enable row level security;
drop policy if exists fe_read on public.feed_events;
create policy fe_read on public.feed_events for select to authenticated using (user_id = auth.uid() or public.is_friend(user_id));
drop policy if exists fe_insert on public.feed_events;
create policy fe_insert on public.feed_events for insert to authenticated with check (user_id = auth.uid());
drop policy if exists fe_delete on public.feed_events;
create policy fe_delete on public.feed_events for delete to authenticated using (user_id = auth.uid());
drop policy if exists fl_read on public.feed_likes;
create policy fl_read on public.feed_likes for select to authenticated
  using (public.feed_owner(event_id) = auth.uid() or public.is_friend(public.feed_owner(event_id)));
drop policy if exists fl_insert on public.feed_likes;
create policy fl_insert on public.feed_likes for insert to authenticated with check (
  user_id = auth.uid() and (public.feed_owner(event_id) = auth.uid() or public.is_friend(public.feed_owner(event_id))));
drop policy if exists fl_delete on public.feed_likes;
create policy fl_delete on public.feed_likes for delete to authenticated using (user_id = auth.uid());

-- реакции на записи стены и сообщения
create table if not exists public.reactions (
  target text not null check (target in ('wall', 'msg')),
  target_id bigint not null,
  user_id uuid not null references public.profiles(id) on delete cascade default auth.uid(),
  emoji text not null check (emoji in ('like', 'fire', 'lol', 'wow', 'gg', 'heart')),
  created_at timestamptz not null default now(),
  primary key (target, target_id, user_id, emoji)
);
create or replace function public.can_see(tgt text, tid bigint) returns boolean
language sql stable security definer set search_path = public as $$
  select case when tgt = 'wall' then exists (
           select 1 from wall_posts w where w.id = tid and (w.owner = auth.uid() or w.author = auth.uid() or is_friend(w.owner)))
         else exists (
           select 1 from messages m where m.id = tid and (
             (m.party_id is not null and is_party_member(m.party_id))
             or (m.to_user is not null and (m.to_user = auth.uid() or m.from_user = auth.uid())))) end
$$;
alter table public.reactions enable row level security;
drop policy if exists re_read on public.reactions;
create policy re_read on public.reactions for select to authenticated using (public.can_see(target, target_id));
drop policy if exists re_insert on public.reactions;
create policy re_insert on public.reactions for insert to authenticated
  with check (user_id = auth.uid() and public.can_see(target, target_id));
drop policy if exists re_delete on public.reactions;
create policy re_delete on public.reactions for delete to authenticated using (user_id = auth.uid());

-- оценки и отзывы карт: видят все игроки Portalis, пишет каждый свою
create table if not exists public.map_reviews (
  map_key text not null check (char_length(map_key) between 1 and 200),
  user_id uuid not null references public.profiles(id) on delete cascade default auth.uid(),
  stars int not null check (stars between 1 and 5),
  text text check (char_length(text) <= 500),
  updated_at timestamptz not null default now(),
  primary key (map_key, user_id)
);
alter table public.map_reviews enable row level security;
drop policy if exists mr_read on public.map_reviews;
create policy mr_read on public.map_reviews for select to authenticated using (true);
drop policy if exists mr_write on public.map_reviews;
create policy mr_write on public.map_reviews for insert to authenticated with check (user_id = auth.uid());
drop policy if exists mr_update on public.map_reviews;
create policy mr_update on public.map_reviews for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists mr_delete on public.map_reviews;
create policy mr_delete on public.map_reviews for delete to authenticated using (user_id = auth.uid());
create or replace view public.map_ratings with (security_invoker = true) as
  select map_key, round(avg(stars)::numeric, 1) as avg, count(*) as n from public.map_reviews group by map_key;
revoke all on public.map_ratings from anon, public;
grant select on public.map_ratings to authenticated;

-- хранилище: media (картинки в чате, ссылки длинные и случайные) и worlds (миры: свои и общие для пати)
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('media', 'media', true, 3145728, array['image/png', 'image/jpeg', 'image/webp'])
on conflict (id) do update set public = true, file_size_limit = 3145728;
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('worlds', 'worlds', false, 52428800, array['application/zip'])
on conflict (id) do update set public = false, file_size_limit = 52428800;
drop policy if exists media_insert on storage.objects;
create policy media_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'media' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists media_delete on storage.objects;
create policy media_delete on storage.objects for delete to authenticated
  using (bucket_id = 'media' and (storage.foldername(name))[1] = auth.uid()::text);
create or replace function public.world_path_ok(path text) returns boolean
language sql stable security definer set search_path = public as $$
  select case when (storage.foldername(path))[1] = auth.uid()::text then true
              when (storage.foldername(path))[1] = 'party' then
                exists (select 1 from party_members where party_id::text = (storage.foldername(path))[2] and user_id = auth.uid())
              else false end
$$;
drop policy if exists worlds_read on storage.objects;
create policy worlds_read on storage.objects for select to authenticated
  using (bucket_id = 'worlds' and public.world_path_ok(name));
drop policy if exists worlds_insert on storage.objects;
create policy worlds_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'worlds' and public.world_path_ok(name));
drop policy if exists worlds_update on storage.objects;
create policy worlds_update on storage.objects for update to authenticated
  using (bucket_id = 'worlds' and public.world_path_ok(name)) with check (bucket_id = 'worlds' and public.world_path_ok(name));
drop policy if exists worlds_delete on storage.objects;
create policy worlds_delete on storage.objects for delete to authenticated
  using (bucket_id = 'worlds' and public.world_path_ok(name));
