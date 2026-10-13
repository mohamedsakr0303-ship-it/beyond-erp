-- Notifications that reach a phone or computer even when BeyondERP is closed (Web Push).
--   push_subs    one row per device that turned them on: where to send (the browser's push address and keys), whose
--                device it is, for which company, and that person's notification settings on that device
--   push_config  the company-independent signing keys (VAPID), made by the push function the first time it runs;
--                only the server reads them
--   push_log     what was sent when, to keep sending within limits
-- None of these can be read from a browser: only the push function (service key) and the functions below touch them.

create table if not exists public.push_subs (
  endpoint    text primary key,
  company_id  uuid not null references public.companies(id) on delete cascade,
  email       text not null,
  p256dh      text not null,
  auth        text not null,
  prefs       jsonb not null default '{}'::jsonb,
  device      text not null default '',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  last_ok     timestamptz
);
create index if not exists push_subs_company on public.push_subs(company_id, email);

create table if not exists public.push_config (
  id          int primary key default 1 check (id = 1),
  keys        jsonb not null,
  created_at  timestamptz not null default now()
);

create table if not exists public.push_log (
  id          bigserial primary key,
  company_id  uuid,
  sender      text not null,
  kind        text not null,
  sent        int not null default 0,
  at          timestamptz not null default now()
);
create index if not exists push_log_sender on public.push_log(sender, at);

alter table public.push_subs enable row level security;
alter table public.push_config enable row level security;
alter table public.push_log enable row level security;
revoke all on public.push_subs, public.push_config, public.push_log from anon, authenticated;

-- This device's push address for the signed-in person in this company (a device follows the last company it signed in to).
create or replace function public.push_subscribe(p_company uuid, p_endpoint text, p_p256dh text, p_auth text,
                                                 p_prefs jsonb default '{}'::jsonb, p_device text default '') returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.beyond_in(p_company) then
    raise exception 'not a member of this company' using errcode = '42501';
  end if;
  if p_endpoint !~ '^https://' or length(p_endpoint) > 1000 or length(coalesce(p_p256dh, '')) not between 80 and 100
     or length(coalesce(p_auth, '')) not between 16 and 30 then
    raise exception 'that is not a push address' using errcode = '22023';
  end if;
  if length(coalesce(p_prefs, '{}')::text) > 4000 then
    raise exception 'settings too large' using errcode = '22023';
  end if;
  if (select count(*) from push_subs where lower(email) = public.beyond_caller() and endpoint <> p_endpoint) >= 20 then
    delete from push_subs where endpoint = (select endpoint from push_subs where lower(email) = public.beyond_caller()
                                            order by updated_at limit 1);   -- the oldest of too many devices
  end if;
  insert into push_subs(endpoint, company_id, email, p256dh, auth, prefs, device)
  values (p_endpoint, p_company, public.beyond_caller(), p_p256dh, p_auth, coalesce(p_prefs, '{}'), left(coalesce(p_device, ''), 120))
  on conflict (endpoint) do update set company_id = excluded.company_id, email = excluded.email, p256dh = excluded.p256dh,
    auth = excluded.auth, prefs = excluded.prefs, device = excluded.device, updated_at = now();
end $$;

-- The person changed their notification settings: their devices follow (only their own rows).
create or replace function public.push_prefs(p_company uuid, p_endpoint text, p_prefs jsonb) returns void
language sql security definer set search_path = public as $$
  update push_subs set prefs = coalesce(p_prefs, '{}'), updated_at = now()
   where endpoint = p_endpoint and company_id = p_company and lower(email) = public.beyond_caller()
     and length(coalesce(p_prefs, '{}')::text) <= 4000
$$;

-- Turn them off on this device.
create or replace function public.push_unsubscribe(p_endpoint text) returns void
language sql security definer set search_path = public as $$
  delete from push_subs where endpoint = p_endpoint and lower(email) = public.beyond_caller()
$$;

-- How many of my devices get them (for Settings).
create or replace function public.push_my_devices(p_company uuid) returns int
language sql stable security definer set search_path = public as $$
  select count(*)::int from push_subs where company_id = p_company and lower(email) = public.beyond_caller()
$$;

-- keep the send log short
create or replace function public.push_log_trim() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if random() < 0.02 then delete from push_log where at < now() - interval '2 days'; end if;
  return null;
end $$;
drop trigger if exists push_log_trim on public.push_log;
create trigger push_log_trim after insert on public.push_log for each statement execute function public.push_log_trim();

revoke execute on function public.push_subscribe(uuid, text, text, text, jsonb, text), public.push_prefs(uuid, text, jsonb),
  public.push_unsubscribe(text), public.push_my_devices(uuid), public.push_log_trim() from public, anon;
grant execute on function public.push_subscribe(uuid, text, text, text, jsonb, text), public.push_prefs(uuid, text, jsonb),
  public.push_unsubscribe(text), public.push_my_devices(uuid) to authenticated;

-- Reminders for later (a follow-up due this morning…): kept here and sent by the push function when their time comes —
-- a scheduled job calls it every few minutes, so they arrive even when no one has the app open.
create table if not exists public.push_queue (
  company_id  uuid not null references public.companies(id) on delete cascade,
  key         text not null,
  at          timestamptz not null,
  msg         jsonb not null,
  sender      text not null default '',
  created_at  timestamptz not null default now(),
  primary key (company_id, key)
);
create index if not exists push_queue_at on public.push_queue(at);
alter table public.push_queue enable row level security;
revoke all on public.push_queue from anon, authenticated;

-- the reminders whose time has come, each handed out once (the push function, service key only)
create or replace function public.push_due(p_limit int default 200) returns setof public.push_queue
language sql security definer set search_path = public as $$
  delete from push_queue q using (
    select company_id, key from push_queue where at <= now() order by at limit greatest(1, least(coalesce(p_limit, 200), 500))
    for update skip locked) d
  where q.company_id = d.company_id and q.key = d.key
  returning q.*
$$;
revoke execute on function public.push_due(int) from public, anon, authenticated;
grant execute on function public.push_due(int) to service_role;
