-- Portalis: аккаунты, друзья, пати и чат. Выполнить один раз в Supabase -> SQL Editor (можно повторно:
-- всё создаётся «если нет»). Доступ к данным только через правила RLS ниже: каждый видит себя, своих
-- друзей, свои пати и их чат. Пароли хранит сам Supabase Auth (хэш bcrypt), в этих таблицах их нет.

create extension if not exists pgcrypto;

-- Профиль игрока (создаётся сам при регистрации).
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  login text not null unique check (login ~ '^[a-z0-9_]{3,20}$'),
  nick text not null check (char_length(nick) between 2 and 24),
  avatar text,                                  -- ник Minecraft, чей скин показывать
  status text not null default 'offline',       -- online / playing / offline
  status_detail text,                           -- «играет: OneBlock» и т. п.
  last_seen timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, login, nick)
  values (new.id,
          lower(coalesce(new.raw_user_meta_data->>'login', split_part(new.email, '@', 1))),
          coalesce(nullif(new.raw_user_meta_data->>'nick', ''), split_part(new.email, '@', 1)))
  on conflict (id) do nothing;
  return new;
end $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- Дружба: одна строка на пару (кто позвал -> кого), pending до принятия.
create table if not exists public.friendships (
  requester uuid not null references public.profiles(id) on delete cascade,
  addressee uuid not null references public.profiles(id) on delete cascade,
  status text not null default 'pending' check (status in ('pending', 'accepted')),
  created_at timestamptz not null default now(),
  primary key (requester, addressee),
  check (requester <> addressee)
);

-- Пати и участники.
create table if not exists public.parties (
  id uuid primary key default gen_random_uuid(),
  owner uuid not null references public.profiles(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 40),
  game_code text,            -- код приглашения в игру (MC1-...) от хозяина
  game_info jsonb,           -- карта, сборка, версия, адрес - чтобы показать участникам
  game_at timestamptz,
  created_at timestamptz not null default now()
);
create table if not exists public.party_members (
  party_id uuid not null references public.parties(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  joined_at timestamptz not null default now(),
  primary key (party_id, user_id)
);
create table if not exists public.party_invites (
  party_id uuid not null references public.parties(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  invited_by uuid not null references public.profiles(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (party_id, user_id)
);

-- Сообщения: в чат пати (party_id) или лично другу (to_user).
create table if not exists public.messages (
  id bigint generated always as identity primary key,
  party_id uuid references public.parties(id) on delete cascade,
  to_user uuid references public.profiles(id) on delete cascade,
  from_user uuid not null references public.profiles(id) on delete cascade default auth.uid(),
  body text not null check (char_length(body) between 1 and 500),
  created_at timestamptz not null default now(),
  check ((party_id is null) <> (to_user is null))
);
create index if not exists messages_party_idx on public.messages (party_id, id);
create index if not exists messages_dm_idx on public.messages (to_user, from_user, id);

-- Помощники для правил (security definer: не упираются в RLS сами).
create or replace function public.is_party_member(p uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from party_members where party_id = p and user_id = auth.uid())
$$;
create or replace function public.is_friend(u uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from friendships where status = 'accepted'
                 and ((requester = auth.uid() and addressee = u) or (addressee = auth.uid() and requester = u)))
$$;

alter table public.profiles enable row level security;
alter table public.friendships enable row level security;
alter table public.parties enable row level security;
alter table public.party_members enable row level security;
alter table public.party_invites enable row level security;
alter table public.messages enable row level security;

-- profiles: найти игрока по логину может любой вошедший; менять - только себя.
drop policy if exists profiles_read on public.profiles;
create policy profiles_read on public.profiles for select to authenticated using (true);
drop policy if exists profiles_update on public.profiles;
create policy profiles_update on public.profiles for update to authenticated
  using (id = auth.uid()) with check (id = auth.uid());

-- friendships
drop policy if exists fr_read on public.friendships;
create policy fr_read on public.friendships for select to authenticated
  using (requester = auth.uid() or addressee = auth.uid());
drop policy if exists fr_insert on public.friendships;
create policy fr_insert on public.friendships for insert to authenticated
  with check (requester = auth.uid() and status = 'pending');
drop policy if exists fr_accept on public.friendships;
create policy fr_accept on public.friendships for update to authenticated
  using (addressee = auth.uid()) with check (addressee = auth.uid());
drop policy if exists fr_delete on public.friendships;
create policy fr_delete on public.friendships for delete to authenticated
  using (requester = auth.uid() or addressee = auth.uid());

-- parties: видят участники и приглашённые; создаёт кто угодно (хозяин - он сам); меняет и удаляет хозяин.
drop policy if exists party_read on public.parties;
create policy party_read on public.parties for select to authenticated
  using (owner = auth.uid() or public.is_party_member(id)
         or exists (select 1 from public.party_invites i where i.party_id = id and i.user_id = auth.uid()));
drop policy if exists party_insert on public.parties;
create policy party_insert on public.parties for insert to authenticated with check (owner = auth.uid());
drop policy if exists party_update on public.parties;
create policy party_update on public.parties for update to authenticated
  using (owner = auth.uid() or public.is_party_member(id)) with check (true);
drop policy if exists party_delete on public.parties;
create policy party_delete on public.parties for delete to authenticated using (owner = auth.uid());

-- party_members: видят участники; вступить можно хозяину или по приглашению; выйти - самому, выгнать - хозяину.
drop policy if exists pm_read on public.party_members;
create policy pm_read on public.party_members for select to authenticated
  using (public.is_party_member(party_id) or user_id = auth.uid());
drop policy if exists pm_join on public.party_members;
create policy pm_join on public.party_members for insert to authenticated with check (
  user_id = auth.uid() and (
    exists (select 1 from public.parties p where p.id = party_id and p.owner = auth.uid())
    or exists (select 1 from public.party_invites i where i.party_id = party_members.party_id and i.user_id = auth.uid())));
drop policy if exists pm_leave on public.party_members;
create policy pm_leave on public.party_members for delete to authenticated using (
  user_id = auth.uid() or exists (select 1 from public.parties p where p.id = party_id and p.owner = auth.uid()));

-- party_invites: звать могут участники и только своих друзей; видит приглашённый и пати; отклонить - приглашённый.
drop policy if exists pi_read on public.party_invites;
create policy pi_read on public.party_invites for select to authenticated
  using (user_id = auth.uid() or public.is_party_member(party_id));
drop policy if exists pi_insert on public.party_invites;
create policy pi_insert on public.party_invites for insert to authenticated
  with check (invited_by = auth.uid() and public.is_party_member(party_id) and public.is_friend(user_id));
drop policy if exists pi_delete on public.party_invites;
create policy pi_delete on public.party_invites for delete to authenticated
  using (user_id = auth.uid() or invited_by = auth.uid());

-- messages: в пати пишут и читают участники; лично - только друзья.
drop policy if exists msg_read on public.messages;
create policy msg_read on public.messages for select to authenticated using (
  (party_id is not null and public.is_party_member(party_id))
  or (to_user is not null and (to_user = auth.uid() or from_user = auth.uid())));
drop policy if exists msg_insert on public.messages;
create policy msg_insert on public.messages for insert to authenticated with check (
  from_user = auth.uid() and (
    (party_id is not null and public.is_party_member(party_id))
    or (to_user is not null and public.is_friend(to_user))));

-- Старые сообщения чистятся сами (держим 30 дней), чтобы бесплатной базы хватало надолго.
create or replace function public.cleanup_messages() returns void language sql security definer as $$
  delete from public.messages where created_at < now() - interval '30 days'
$$;
