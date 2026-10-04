-- Portalis, часть 4: предложения в пати «во что пойти» и голоса за них.
-- Выполнить после portalis_3_progress.sql. Только добавляет.

create table if not exists public.party_proposals (
  id bigint generated always as identity primary key,
  party_id uuid not null references public.parties(id) on delete cascade,
  author uuid not null references public.profiles(id) on delete cascade default auth.uid(),
  kind text not null check (kind in ('map', 'web', 'server', 'text')),  -- карта каталога / из интернета / сервер / идея
  title text not null check (char_length(title) between 1 and 80),
  payload jsonb not null default '{}'::jsonb check (pg_column_size(payload) <= 2000),
  created_at timestamptz not null default now()
);
create index if not exists proposals_party_idx on public.party_proposals (party_id, id desc);
create table if not exists public.party_votes (
  proposal_id bigint not null references public.party_proposals(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade default auth.uid(),
  primary key (proposal_id, user_id)
);
alter table public.party_proposals enable row level security;
alter table public.party_votes enable row level security;

create or replace function public.proposal_party(pid bigint) returns uuid
language sql stable security definer set search_path = public as $$
  select party_id from party_proposals where id = pid
$$;

drop policy if exists pp_read on public.party_proposals;
create policy pp_read on public.party_proposals for select to authenticated using (public.is_party_member(party_id));
drop policy if exists pp_insert on public.party_proposals;
create policy pp_insert on public.party_proposals for insert to authenticated
  with check (author = auth.uid() and public.is_party_member(party_id));
drop policy if exists pp_delete on public.party_proposals;
create policy pp_delete on public.party_proposals for delete to authenticated using (
  author = auth.uid() or exists (select 1 from public.parties p where p.id = party_id and p.owner = auth.uid()));

drop policy if exists pv_read on public.party_votes;
create policy pv_read on public.party_votes for select to authenticated
  using (public.is_party_member(public.proposal_party(proposal_id)));
drop policy if exists pv_insert on public.party_votes;
create policy pv_insert on public.party_votes for insert to authenticated
  with check (user_id = auth.uid() and public.is_party_member(public.proposal_party(proposal_id)));
drop policy if exists pv_delete on public.party_votes;
create policy pv_delete on public.party_votes for delete to authenticated using (user_id = auth.uid());
