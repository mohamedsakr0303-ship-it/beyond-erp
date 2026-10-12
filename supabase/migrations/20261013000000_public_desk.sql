-- The public desk: pages people outside the company use without signing in — online booking, web forms (leads),
-- signing an offer, and chatting with the company as a guest.
--
-- A company publishes a page (public_pages) reached by a long random token in its link. What visitors send lands in
-- public_inbox; the company's app claims new rows (one device takes each row, so nothing is imported twice), adds them
-- to the books (a booking, a lead, a signature, a message) and can answer guests and confirm bookings there.
--
-- Visitors never read the tables: the anon role only calls the public_* functions below, which check the page is
-- live, keep each payload small and limit how much one page can receive per hour. A visitor manages their own booking
-- or guest conversation with a second random token (only its hash is stored).

create extension if not exists pgcrypto with schema extensions;

create table if not exists public.public_pages (
  id          uuid primary key default gen_random_uuid(),
  company_id  uuid not null references public.companies(id) on delete cascade,
  kind        text not null check (kind in ('booking', 'form', 'sign', 'guest')),
  token       text not null unique,
  title       text not null default '',
  config      jsonb not null default '{}',     -- what visitors see (services, hours, fields, the offer to sign…)
  active      boolean not null default true,
  created_by  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists public_pages_company on public.public_pages (company_id);

create table if not exists public.public_inbox (
  id          bigserial primary key,
  page_id     uuid not null references public.public_pages(id) on delete cascade,
  company_id  uuid not null references public.companies(id) on delete cascade,
  kind        text not null,                   -- booking, reschedule, cancel, lead, signature, message, reply, confirm
  direction   text not null default 'in' check (direction in ('in', 'out')),
  ref         text,                            -- the visitor's booking / conversation reference
  manage_hash text,                            -- sha256 of the visitor's own token (first row of a ref)
  slot        text,                            -- booking: 'YYYY-MM-DD HH:MM' it holds (null once cancelled)
  payload     jsonb not null default '{}',
  status      text not null default 'new' check (status in ('new', 'taken', 'rejected')),
  created_at  timestamptz not null default now()
);
create index if not exists public_inbox_company on public.public_inbox (company_id, id);
create index if not exists public_inbox_page_ref on public.public_inbox (page_id, ref);
create index if not exists public_inbox_page_slot on public.public_inbox (page_id, slot) where slot is not null;

alter table public.public_pages enable row level security;
alter table public.public_inbox enable row level security;
revoke all on public.public_pages, public.public_inbox from anon, authenticated;

create or replace function public.beyond_hash(p text) returns text
language sql immutable as $$ select encode(extensions.digest(coalesce(p, ''), 'sha256'), 'hex') $$;

create or replace function public.beyond_token(p_len int default 24) returns text
language sql volatile as $$
  select substr(replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''), 1, greatest(8, least(p_len, 60)))
$$;

-- ------------------------------------------------------------------ visitors (no sign-in)
-- The page as visitors see it: title, kind and its public settings; a booking page also lists the times already taken.
create or replace function public.public_page(p_token text) returns json
language plpgsql stable security definer set search_path = public as $$
declare pg public_pages; held json;
begin
  select * into pg from public_pages where token = p_token and active;
  if pg.id is null then
    raise exception 'This link is not available (it may have been switched off)' using errcode = 'P0002';
  end if;
  if pg.kind = 'booking' then
    select coalesce(json_agg(json_build_object('slot', slot, 'n', n)), '[]'::json) into held
    from (select slot, count(*) n from public_inbox
          where page_id = pg.id and slot is not null and status <> 'rejected' and slot >= to_char(now(), 'YYYY-MM-DD')
          group by slot) s;
  end if;
  return json_build_object('kind', pg.kind, 'title', pg.title, 'config', pg.config, 'held', held,
                           'company', (select name from companies where id = pg.company_id));
end $$;

-- Send something to a page: a booking, a form, a signature or a guest's message. The first row of a booking /
-- conversation returns its reference and the visitor's own token (shown once, kept in their browser).
create or replace function public.public_send(p_token text, p_kind text, p_payload jsonb, p_ref text default null,
                                              p_manage text default null) returns json
language plpgsql security definer set search_path = public as $$
declare pg public_pages; v_ref text := nullif(p_ref, ''); v_mng text; v_slot text; cap int; first public_inbox; sz int;
begin
  select * into pg from public_pages where token = p_token and active;
  if pg.id is null then
    raise exception 'This link is not available (it may have been switched off)' using errcode = 'P0002';
  end if;
  if (pg.kind, p_kind) not in (('booking', 'booking'), ('form', 'lead'), ('sign', 'signature'), ('guest', 'message')) then
    raise exception 'not accepted here' using errcode = '22023';
  end if;
  sz := length(coalesce(p_payload, '{}')::text);
  if jsonb_typeof(coalesce(p_payload, '{}')) <> 'object' or sz > (case when p_kind = 'signature' then 400000 else 20000 end) then
    raise exception 'too much to send' using errcode = '22023';
  end if;
  -- a page takes at most 120 submissions an hour (stops floods); a conversation at most 30 messages an hour
  if (select count(*) from public_inbox where page_id = pg.id and direction = 'in' and created_at > now() - interval '1 hour') >= 120 then
    raise exception 'Too many requests right now — please try again a little later' using errcode = '54000';
  end if;
  if v_ref is not null then
    select * into first from public_inbox i where i.page_id = pg.id and i.ref = p_ref and manage_hash is not null order by id limit 1;
    if first.id is null or first.manage_hash <> public.beyond_hash(p_manage) then
      raise exception 'not allowed' using errcode = '42501';
    end if;
    if (select count(*) from public_inbox i where i.page_id = pg.id and i.ref = p_ref and i.created_at > now() - interval '1 hour') >= 30 then
      raise exception 'Too many messages right now — please try again a little later' using errcode = '54000';
    end if;
  else
    v_ref := upper(public.beyond_token(8));
    v_mng := public.beyond_token(32);
  end if;
  if p_kind = 'booking' then
    v_slot := p_payload ->> 'slot';
    if v_slot is null or v_slot !~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$' or v_slot < to_char(now(), 'YYYY-MM-DD HH24:MI') then
      raise exception 'Pick a time that is still ahead' using errcode = '22023';
    end if;
    cap := greatest(1, coalesce((pg.config ->> 'per_slot')::int, 1));
    perform pg_advisory_xact_lock(hashtext(pg.id::text || v_slot));
    if (select count(*) from public_inbox i where i.page_id = pg.id and i.slot = v_slot and i.status <> 'rejected') >= cap
       or coalesce(pg.config -> 'busy', '[]'::jsonb) ? v_slot then
      raise exception 'That time was just taken — please pick another one' using errcode = '23505';
    end if;
  end if;
  insert into public_inbox(page_id, company_id, kind, ref, manage_hash, slot, payload)
  values (pg.id, pg.company_id, p_kind, v_ref, case when v_mng is not null then public.beyond_hash(v_mng) end, v_slot, coalesce(p_payload, '{}'));
  return json_build_object('ref', v_ref, 'manage', v_mng);
end $$;

-- A visitor's own booking / conversation: see it, move or cancel a booking, read the company's answers.
create or replace function public.public_manage(p_token text, p_ref text, p_manage text, p_action text default 'view',
                                                p_slot text default null, p_after bigint default 0) returns json
language plpgsql security definer set search_path = public as $$
declare pg public_pages; first public_inbox; cap int; cur text;
begin
  select * into pg from public_pages where token = p_token;
  if pg.id is null then
    raise exception 'This link is not available' using errcode = 'P0002';
  end if;
  select * into first from public_inbox where page_id = pg.id and ref = p_ref and manage_hash is not null order by id limit 1;
  if first.id is null or first.manage_hash <> public.beyond_hash(p_manage) then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  if p_action in ('cancel', 'move') then
    if first.kind <> 'booking' or not pg.active then
      raise exception 'not possible' using errcode = '22023';
    end if;
    if first.status = 'rejected' then
      raise exception 'This booking was declined' using errcode = '22023';
    end if;
    if first.slot is null then
      raise exception 'This booking is already cancelled' using errcode = '22023';
    end if;
    cur := first.slot;
    if p_action = 'move' then
      if p_slot is null or p_slot !~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$' or p_slot < to_char(now(), 'YYYY-MM-DD HH24:MI') then
        raise exception 'Pick a time that is still ahead' using errcode = '22023';
      end if;
      cap := greatest(1, coalesce((pg.config ->> 'per_slot')::int, 1));
      perform pg_advisory_xact_lock(hashtext(pg.id::text || p_slot));
      if (select count(*) from public_inbox where page_id = pg.id and slot = p_slot and status <> 'rejected') >= cap
         or coalesce(pg.config -> 'busy', '[]'::jsonb) ? p_slot then
        raise exception 'That time was just taken — please pick another one' using errcode = '23505';
      end if;
    end if;
    update public_inbox set slot = case when p_action = 'move' then p_slot end where id = first.id;
    insert into public_inbox(page_id, company_id, kind, ref, slot, payload)
    values (pg.id, pg.company_id, case when p_action = 'move' then 'reschedule' else 'cancel' end, p_ref, null,
            jsonb_build_object('from', cur, 'to', p_slot));
  end if;
  return json_build_object(
    'kind', first.kind, 'ref', p_ref, 'slot', (select slot from public_inbox where id = first.id), 'status', first.status,
    'payload', first.payload, 'created_at', first.created_at,
    'messages', (select coalesce(json_agg(json_build_object('id', id, 'kind', kind, 'direction', direction, 'body', payload ->> 'body',
                                                             'at', created_at) order by id), '[]'::json)
                 from public_inbox where page_id = pg.id and ref = p_ref and id > coalesce(p_after, 0)
                   and kind in ('message', 'reply', 'confirm', 'reschedule', 'cancel')));
end $$;

revoke execute on function public.public_page(text), public.public_send(text, text, jsonb, text, text),
  public.public_manage(text, text, text, text, text, bigint), public.beyond_hash(text), public.beyond_token(int) from public;
grant execute on function public.public_page(text), public.public_send(text, text, jsonb, text, text),
  public.public_manage(text, text, text, text, text, bigint) to anon, authenticated;

-- ------------------------------------------------------------------ the company
create or replace function public.desk_pages(p_company uuid) returns json
language sql stable security definer set search_path = public as $$
  select coalesce(json_agg(json_build_object('id', id, 'kind', kind, 'token', token, 'title', title, 'config', config,
                                             'active', active, 'created_at', created_at, 'updated_at', updated_at,
                                             'new', (select count(*) from public_inbox i where i.page_id = p.id and i.status = 'new'))
                           order by created_at), '[]'::json)
  from public_pages p where company_id = p_company and public.beyond_in(p_company)
$$;

-- Create (p_id null) or change a page. Only administrators of the company publish pages.
create or replace function public.desk_page_save(p_company uuid, p_id uuid, p_kind text, p_title text, p_config jsonb,
                                                 p_active boolean default true) returns json
language plpgsql security definer set search_path = public as $$
declare pg public_pages;
begin
  perform public.beyond_require_admin(p_company);
  if length(coalesce(p_config, '{}')::text) > 200000 then
    raise exception 'the page settings are too large' using errcode = '22023';
  end if;
  if p_id is null then
    if (select count(*) from public_pages where company_id = p_company) >= 200 then
      raise exception 'too many pages — switch off or reuse some' using errcode = '54000';
    end if;
    insert into public_pages(company_id, kind, token, title, config, active, created_by)
    values (p_company, p_kind, public.beyond_token(24), left(coalesce(p_title, ''), 200), coalesce(p_config, '{}'),
            coalesce(p_active, true), public.beyond_caller())
    returning * into pg;
  else
    update public_pages set title = left(coalesce(p_title, title), 200), config = coalesce(p_config, config),
                            active = coalesce(p_active, active), updated_at = now()
    where id = p_id and company_id = p_company returning * into pg;
    if pg.id is null then
      raise exception 'no such page' using errcode = 'P0002';
    end if;
  end if;
  return json_build_object('id', pg.id, 'token', pg.token, 'kind', pg.kind);
end $$;

-- New rows for the books, each handed to one device only: they are marked taken as they are returned.
create or replace function public.desk_claim(p_company uuid, p_limit int default 200) returns json
language plpgsql security definer set search_path = public as $$
declare out json;
begin
  if not public.beyond_in(p_company) then
    raise exception 'not a member of this company' using errcode = '42501';
  end if;
  with picked as (
    select id from public_inbox where company_id = p_company and status = 'new' and direction = 'in'
    order by id limit greatest(1, least(coalesce(p_limit, 200), 500)) for update skip locked
  ), done as (
    update public_inbox i set status = 'taken' from picked where i.id = picked.id
    returning i.id, i.page_id, i.kind, i.ref, i.slot, i.payload, i.created_at
  )
  select coalesce(json_agg(json_build_object('id', d.id, 'page_id', d.page_id, 'page_kind', p.kind, 'page_title', p.title,
                                             'kind', d.kind, 'ref', d.ref, 'slot', d.slot, 'payload', d.payload, 'at', d.created_at)
                           order by d.id), '[]'::json) into out
  from done d join public_pages p on p.id = d.page_id;
  return out;
end $$;

-- Answer a guest, confirm or decline a booking (the visitor sees it on their page).
create or replace function public.desk_reply(p_company uuid, p_page uuid, p_ref text, p_kind text, p_body text) returns json
language plpgsql security definer set search_path = public as $$
declare first public_inbox; rid bigint;
begin
  if not public.beyond_in(p_company) then
    raise exception 'not a member of this company' using errcode = '42501';
  end if;
  if p_kind not in ('reply', 'confirm', 'decline') then
    raise exception 'unknown answer' using errcode = '22023';
  end if;
  select * into first from public_inbox where page_id = p_page and company_id = p_company and ref = p_ref and manage_hash is not null
  order by id limit 1;
  if first.id is null then
    raise exception 'no such booking or conversation' using errcode = 'P0002';
  end if;
  if p_kind = 'decline' then
    update public_inbox set status = 'rejected', slot = null where id = first.id;
  end if;
  insert into public_inbox(page_id, company_id, kind, direction, ref, payload, status)
  values (p_page, p_company, case when p_kind = 'decline' then 'cancel' else p_kind end, 'out', p_ref,
          jsonb_build_object('body', left(coalesce(p_body, ''), 4000), 'by', public.beyond_caller()), 'taken')
  returning id into rid;
  return json_build_object('id', rid);
end $$;

-- A guest conversation as the company sees it (both sides).
create or replace function public.desk_thread(p_company uuid, p_page uuid, p_ref text) returns json
language sql stable security definer set search_path = public as $$
  select coalesce(json_agg(json_build_object('id', id, 'kind', kind, 'direction', direction, 'body', payload ->> 'body',
                                             'name', payload ->> 'name', 'at', created_at) order by id), '[]'::json)
  from public_inbox where company_id = p_company and page_id = p_page and ref = p_ref and public.beyond_in(p_company)
$$;

revoke execute on function public.desk_pages(uuid), public.desk_page_save(uuid, uuid, text, text, jsonb, boolean),
  public.desk_claim(uuid, int), public.desk_reply(uuid, uuid, text, text, text), public.desk_thread(uuid, uuid, text) from public, anon;
grant execute on function public.desk_pages(uuid), public.desk_page_save(uuid, uuid, text, text, jsonb, boolean),
  public.desk_claim(uuid, int), public.desk_reply(uuid, uuid, text, text, text), public.desk_thread(uuid, uuid, text) to authenticated;
