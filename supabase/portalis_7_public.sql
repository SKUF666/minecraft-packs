-- Portalis, шаг 7: открытые пати.
-- Хозяин ставит галочку «Кто угодно может войти» и пишет, во что играем, через какой лаунчер и на сколько человек.
-- Такие пати видны всем в списке «Открытые пати» (пока хозяин в сети), вступить можно без приглашения, пока есть места.

alter table public.parties add column if not exists is_public boolean not null default false;
alter table public.parties add column if not exists plan text check (plan is null or char_length(plan) <= 80);
alter table public.parties add column if not exists launcher text check (launcher is null or char_length(launcher) <= 30);
alter table public.parties add column if not exists version text check (version is null or char_length(version) <= 20);
alter table public.parties add column if not exists max_players int not null default 8 check (max_players between 2 and 20);

-- Открыта и есть место (security definer: считает участников, не упираясь в правила чтения).
create or replace function public.party_open(p uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from parties x where x.id = p and x.is_public
                 and (select count(*) from party_members m where m.party_id = p) < x.max_players)
$$;

-- Открытую пати видит любой вошедший.
drop policy if exists party_read on public.parties;
create policy party_read on public.parties for select to authenticated
  using (owner = auth.uid() or is_public or public.is_party_member(id)
         or exists (select 1 from public.party_invites i where i.party_id = id and i.user_id = auth.uid()));

-- Вступить: хозяину, по приглашению или в открытую пати с местом.
drop policy if exists pm_join on public.party_members;
create policy pm_join on public.party_members for insert to authenticated with check (
  user_id = auth.uid() and (
    exists (select 1 from public.parties p where p.id = party_id and p.owner = auth.uid())
    or exists (select 1 from public.party_invites i where i.party_id = party_members.party_id and i.user_id = auth.uid())
    or public.party_open(party_id)));

-- Список открытых пати: хозяин был в сети последние 15 минут, свежие сверху.
create or replace function public.public_parties()
returns table (id uuid, name text, plan text, launcher text, version text, max_players int, members int,
               owner uuid, owner_nick text, owner_avatar text, owner_login text, created_at timestamptz)
language sql stable security definer set search_path = public as $$
  select p.id, p.name, p.plan, p.launcher, p.version, p.max_players,
         (select count(*)::int from party_members m where m.party_id = p.id),
         p.owner, pr.nick, pr.avatar, pr.login, p.created_at
  from parties p join profiles pr on pr.id = p.owner
  where p.is_public and pr.last_seen > now() - interval '15 minutes'
  order by p.created_at desc
  limit 60
$$;
revoke all on function public.public_parties() from public, anon;
grant execute on function public.public_parties() to authenticated;
