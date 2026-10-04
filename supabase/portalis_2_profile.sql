-- Portalis, часть 2: профиль (аватар, статус, настроение, любимые игры, о себе) и стена.
-- Выполнить после portalis.sql. Только добавляет: существующие данные не трогает.

alter table public.profiles add column if not exists presence text not null default 'auto'
  check (presence in ('auto', 'dnd', 'invisible'));             -- В сети (сам) / Не беспокоить / Невидимка
alter table public.profiles add column if not exists mood text check (char_length(mood) <= 80);       -- своя строка статуса
alter table public.profiles add column if not exists about text check (char_length(about) <= 500);   -- о себе
alter table public.profiles add column if not exists favorites jsonb not null default '[]'::jsonb;    -- любимые игры
alter table public.profiles add column if not exists color text check (color ~ '^#[0-9a-fA-F]{6}$'); -- цвет шапки
-- avatar: 'preset:<n>' | 'skin:<ник>' | 'data:image/png;base64,...' (маленькая картинка 96x96)
alter table public.profiles drop constraint if exists profiles_avatar_len;
alter table public.profiles add constraint profiles_avatar_len check (avatar is null or char_length(avatar) <= 40000);

-- Невидимку остальные видят «не в сети»: статус и подробности прячутся на уровне базы.
-- Напрямую из таблицы профилей можно читать только открытые поля, статус - только через представление.
revoke select on public.profiles from anon, authenticated;
grant select (id, login, nick, avatar, mood, about, favorites, color, created_at) on public.profiles to authenticated;
drop view if exists public.profiles_public;
create view public.profiles_public as
  select id, login, nick, avatar, mood, about, favorites, color, created_at,
         case when presence = 'invisible' and id <> auth.uid() then 'offline' else status end as status,
         case when presence = 'invisible' and id <> auth.uid() then null else status_detail end as status_detail,
         case when presence = 'invisible' and id <> auth.uid() then null else last_seen end as last_seen,
         case when id = auth.uid() then presence when presence = 'dnd' then 'dnd' else 'auto' end as presence
  from public.profiles;
revoke all on public.profiles_public from anon, public;
grant select on public.profiles_public to authenticated;

-- Стена: записи на странице игрока. Видят и пишут хозяин и его друзья; удалить может автор или хозяин.
create table if not exists public.wall_posts (
  id bigint generated always as identity primary key,
  owner uuid not null references public.profiles(id) on delete cascade,
  author uuid not null references public.profiles(id) on delete cascade default auth.uid(),
  body text not null check (char_length(body) between 1 and 500),
  created_at timestamptz not null default now()
);
create index if not exists wall_owner_idx on public.wall_posts (owner, id desc);
alter table public.wall_posts enable row level security;
drop policy if exists wall_read on public.wall_posts;
create policy wall_read on public.wall_posts for select to authenticated
  using (owner = auth.uid() or author = auth.uid() or public.is_friend(owner));
drop policy if exists wall_insert on public.wall_posts;
create policy wall_insert on public.wall_posts for insert to authenticated
  with check (author = auth.uid() and (owner = auth.uid() or public.is_friend(owner)));
drop policy if exists wall_delete on public.wall_posts;
create policy wall_delete on public.wall_posts for delete to authenticated
  using (author = auth.uid() or owner = auth.uid());
