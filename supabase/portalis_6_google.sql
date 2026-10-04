-- Portalis, шаг 6: вход через Google.
-- Выполнить ОДИН раз, когда включаешь Google: Authentication -> Providers -> Google (Client ID и Secret из
-- Google Cloud), Authentication -> URL Configuration -> Redirect URLs: http://127.0.0.1:53682/**
-- Потом в library/social.json добавить "google": true и выпустить версию.
--
-- У игрока из Google нет логина: делаем его из почты (латиница/цифры/_), а если занят - с цифрами.
-- Регистрация по логину и паролю работает как раньше.

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  base text;
  cand text;
  meta_login text := new.raw_user_meta_data->>'login';
begin
  if meta_login is not null then
    cand := lower(meta_login);
  else
    base := left(regexp_replace(lower(split_part(coalesce(new.email, ''), '@', 1)), '[^a-z0-9_]', '', 'g'), 15);
    if char_length(base) < 3 then
      base := 'player';
    end if;
    cand := base;
    while exists (select 1 from public.profiles where login = cand) loop
      cand := base || (1000 + floor(random() * 9000))::int;
    end loop;
  end if;
  insert into public.profiles (id, login, nick)
  values (new.id, cand,
          left(coalesce(nullif(new.raw_user_meta_data->>'nick', ''), nullif(new.raw_user_meta_data->>'full_name', ''),
                        nullif(new.raw_user_meta_data->>'name', ''), cand), 24))
  on conflict (id) do nothing;
  return new;
end $$;
