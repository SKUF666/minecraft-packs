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
