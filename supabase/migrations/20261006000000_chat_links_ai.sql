-- Chat between companies, super administrators in the workspace, and the AI assistant's settings.
--
-- 1. members.is_super: the company's super administrators (the company's creator, and whoever a super administrator
--    promotes). Only they connect the company to another company's chat or install the AI assistant.
-- 2. Chat links: two companies talk in a shared room. A super administrator of company A creates a one-time code
--    (valid 48 hours) and gives it to company B by phone or email; a super administrator of B enters it. Members of
--    both companies then read and write there. A super administrator of either company can end the link at any time;
--    the history stays readable for both, nobody can write any more. Everything goes through the functions below
--    (row level security: members read only the links of their own companies; nobody writes the tables directly).
-- 3. ai_config: the assistant's settings and the provider key, per company. No policy lets anyone read the table:
--    only the `ai` edge function (service key) does, so the key never reaches a browser.

create extension if not exists pgcrypto with schema extensions;

-- ------------------------------------------------------------------ super administrators
alter table public.members add column if not exists is_super boolean not null default false;
update public.members m set is_super = true
  from public.companies c where c.id = m.company_id and lower(m.email) = lower(c.created_by) and not m.is_super;

create or replace function public.beyond_is_super(p_company uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from members m join companies c on c.id = m.company_id
                 where m.company_id = p_company and lower(m.email) = public.beyond_caller() and m.active and not m.pending
                   and (m.is_super or lower(m.email) = lower(coalesce(c.created_by, ''))))
$$;

create or replace function public.beyond_require_super(p_company uuid) returns void
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.beyond_is_super(p_company) then
    raise exception 'only a super administrator of this company can do this' using errcode = '42501';
  end if;
end $$;

-- the app's Users screen mirrors its super administrators here
create or replace function public.set_member_super(p_company uuid, p_email text, p_super boolean) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_super(p_company);
  update members set is_super = coalesce(p_super, false)
   where company_id = p_company and lower(email) = lower(trim(p_email));
  if not exists (select 1 from members m join companies c on c.id = m.company_id
                 where m.company_id = p_company and m.active and (m.is_super or lower(m.email) = lower(coalesce(c.created_by, '')))) then
    raise exception 'a company needs at least one super administrator';
  end if;
end $$;

create or replace function public.my_super(p_company uuid) returns boolean
language sql stable security definer set search_path = public as $$ select public.beyond_is_super(p_company) $$;

-- ------------------------------------------------------------------ chat between companies
create table if not exists public.chat_links (
  id            uuid primary key default gen_random_uuid(),
  company_a     uuid not null references public.companies(id) on delete cascade,
  company_b     uuid references public.companies(id) on delete cascade,
  name_a        text not null default '',
  name_b        text not null default '',
  code_hash     text unique,                 -- sha256 of the one-time code; cleared once used
  code_expires  timestamptz,
  status        text not null default 'pending' check (status in ('pending', 'active', 'ended')),
  created_by    text,
  created_at    timestamptz not null default now(),
  accepted_by   text,
  accepted_at   timestamptz,
  ended_by      text,
  ended_company uuid,
  ended_at      timestamptz
);
create table if not exists public.chat_link_messages (
  id          bigserial primary key,
  link_id     uuid not null references public.chat_links(id) on delete cascade,
  company_id  uuid not null,
  sender      text not null,
  sender_name text not null default '',
  body        text not null default '',
  files       jsonb not null default '[]',   -- [{name, mime, size, data (base64)}]
  created_at  timestamptz not null default now(),
  edited_at   timestamptz,
  deleted_at  timestamptz
);
create index if not exists chat_link_messages_link on public.chat_link_messages (link_id, id);
create table if not exists public.chat_link_reads (
  link_id   uuid not null references public.chat_links(id) on delete cascade,
  email     text not null,
  last_read bigint not null default 0,
  primary key (link_id, email)
);
alter table public.chat_links enable row level security;
alter table public.chat_link_messages enable row level security;
alter table public.chat_link_reads enable row level security;
revoke all on public.chat_links, public.chat_link_messages, public.chat_link_reads from anon, authenticated;

create or replace function public.beyond_link_side(p_link uuid, p_company uuid) returns public.chat_links
language plpgsql stable security definer set search_path = public as $$
declare l chat_links;
begin
  select * into l from chat_links where id = p_link;
  if l.id is null or p_company is null or (l.company_a <> p_company and coalesce(l.company_b::text, '') <> p_company::text)
     or not public.beyond_in(p_company) then
    raise exception 'no such chat connection' using errcode = '42501';
  end if;
  return l;
end $$;

-- The connections of the caller's company (pending codes, active and ended), with unread counts.
create or replace function public.chat_link_list(p_company uuid) returns json
language sql stable security definer set search_path = public as $$
  select coalesce(json_agg(json_build_object(
           'id', l.id, 'status', l.status, 'created_at', l.created_at, 'accepted_at', l.accepted_at, 'ended_at', l.ended_at,
           'code_expires', case when l.status = 'pending' then l.code_expires end,
           'other', case when l.company_a = p_company then l.name_b else l.name_a end,
           'ended_by_us', l.ended_company = p_company,
           'last_id', (select max(id) from chat_link_messages x where x.link_id = l.id),
           'unread', (select count(*) from chat_link_messages x where x.link_id = l.id and x.deleted_at is null
                        and lower(x.sender) <> public.beyond_caller()
                        and x.id > coalesce((select last_read from chat_link_reads r where r.link_id = l.id
                                               and r.email = public.beyond_caller()), 0)))
         order by l.created_at desc), '[]'::json)
  from chat_links l
  where public.beyond_in(p_company) and (l.company_a = p_company or l.company_b = p_company)
$$;

-- A super administrator starts a connection: returns the one-time code to give the other company.
create or replace function public.chat_link_create(p_company uuid) returns json
language plpgsql security definer set search_path = public as $$
declare code text := upper(substr(replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''), 1, 20));
        l chat_links;
begin
  perform public.beyond_require_super(p_company);
  if (select count(*) from chat_links where company_a = p_company and status = 'pending' and code_expires > now()) >= 5 then
    raise exception 'too many unused codes — end some of them first';
  end if;
  insert into chat_links(company_a, name_a, code_hash, code_expires, created_by)
  values (p_company, (select name from companies where id = p_company), encode(extensions.digest(code, 'sha256'), 'hex'),
          now() + interval '48 hours', public.beyond_caller())
  returning * into l;
  return json_build_object('id', l.id, 'code', substr(code, 1, 5) || '-' || substr(code, 6, 5) || '-' || substr(code, 11, 5) || '-' || substr(code, 16, 5),
                           'expires', l.code_expires);
end $$;

-- A super administrator of the other company enters the code.
create or replace function public.chat_link_accept(p_company uuid, p_code text) returns json
language plpgsql security definer set search_path = public as $$
declare l chat_links; h text := encode(extensions.digest(upper(regexp_replace(coalesce(p_code, ''), '[^A-Za-z0-9]', '', 'g')), 'sha256'), 'hex');
begin
  perform public.beyond_require_super(p_company);
  select * into l from chat_links where code_hash = h and status = 'pending' for update;
  if l.id is null or l.code_expires < now() then raise exception 'this code is not valid (wrong, used or expired)'; end if;
  if l.company_a = p_company then raise exception 'this code was made by your own company — give it to the other company'; end if;
  if exists (select 1 from chat_links where status = 'active' and ((company_a = l.company_a and company_b = p_company)
                                                                  or (company_a = p_company and company_b = l.company_a))) then
    raise exception 'your companies are already connected';
  end if;
  update chat_links set company_b = p_company, name_b = (select name from companies where id = p_company), status = 'active',
         code_hash = null, code_expires = null, accepted_by = public.beyond_caller(), accepted_at = now()
   where id = l.id returning * into l;
  return json_build_object('id', l.id, 'other', l.name_a);
end $$;

-- Either company's super administrator ends the connection (or withdraws an unused code). History stays.
create or replace function public.chat_link_end(p_company uuid, p_link uuid) returns void
language plpgsql security definer set search_path = public as $$
declare l chat_links;
begin
  perform public.beyond_require_super(p_company);
  l := public.beyond_link_side(p_link, p_company);
  if l.status = 'pending' then delete from chat_links where id = l.id; return; end if;
  update chat_links set status = 'ended', ended_by = public.beyond_caller(), ended_company = p_company, ended_at = now()
   where id = l.id and status = 'active';
end $$;

create or replace function public.chat_link_messages(p_company uuid, p_link uuid, p_after bigint default 0, p_limit int default 80)
returns json language plpgsql stable security definer set search_path = public as $$
declare l chat_links;
begin
  l := public.beyond_link_side(p_link, p_company);
  return (select coalesce(json_agg(json_build_object(
            'id', m.id, 'mine', lower(m.sender) = public.beyond_caller(), 'ours', m.company_id = p_company,
            'sender_name', m.sender_name, 'body', case when m.deleted_at is null then m.body else '' end,
            'files', case when m.deleted_at is null then (select coalesce(json_agg(json_build_object('i', o - 1, 'name', f->>'name',
                       'mime', f->>'mime', 'size', (f->>'size')::bigint, 'thumb', coalesce(f->>'thumb', ''))), '[]'::json)
                       from jsonb_array_elements(m.files) with ordinality as t(f, o)) else '[]'::json end,
            'at', m.created_at, 'edited', m.edited_at is not null, 'deleted', m.deleted_at is not null,
            'seen', exists (select 1 from chat_link_reads r where r.link_id = m.link_id and r.last_read >= m.id
                            and r.email <> lower(m.sender))) order by m.id), '[]'::json)
          from (select * from chat_link_messages where link_id = l.id and id > coalesce(p_after, 0)
                order by id desc limit least(greatest(coalesce(p_limit, 80), 1), 200)) m);
end $$;

create or replace function public.chat_link_file(p_company uuid, p_message bigint, p_index int) returns json
language plpgsql stable security definer set search_path = public as $$
declare m chat_link_messages; f jsonb;
begin
  select * into m from chat_link_messages where id = p_message;
  if m.id is null then raise exception 'no such file'; end if;
  perform public.beyond_link_side(m.link_id, p_company);
  f := m.files -> p_index;
  if f is null or m.deleted_at is not null then raise exception 'no such file'; end if;
  return f;
end $$;

create or replace function public.chat_link_send(p_company uuid, p_link uuid, p_body text, p_files jsonb default '[]', p_name text default '')
returns bigint language plpgsql security definer set search_path = public as $$
declare l chat_links; new_id bigint;
begin
  l := public.beyond_link_side(p_link, p_company);
  if l.status <> 'active' then raise exception 'this chat connection has ended — the history stays readable'; end if;
  if coalesce(length(p_body), 0) > 20000 then raise exception 'message too long'; end if;
  if jsonb_typeof(coalesce(p_files, '[]')) <> 'array' or length(coalesce(p_files, '[]')::text) > 21000000 then
    raise exception 'files are limited to 15 MB per message';
  end if;
  if coalesce(trim(p_body), '') = '' and jsonb_array_length(coalesce(p_files, '[]')) = 0 then raise exception 'empty message'; end if;
  insert into chat_link_messages(link_id, company_id, sender, sender_name, body, files)
  values (l.id, p_company, public.beyond_caller(), left(coalesce(p_name, ''), 120), coalesce(p_body, ''), coalesce(p_files, '[]'))
  returning id into new_id;
  insert into chat_link_reads(link_id, email, last_read) values (l.id, public.beyond_caller(), new_id)
  on conflict (link_id, email) do update set last_read = greatest(chat_link_reads.last_read, excluded.last_read);
  return new_id;
end $$;

-- Edit or take back a message nobody else has seen yet.
create or replace function public.chat_link_edit(p_company uuid, p_message bigint, p_body text, p_delete boolean default false)
returns void language plpgsql security definer set search_path = public as $$
declare m chat_link_messages; l chat_links;
begin
  select * into m from chat_link_messages where id = p_message for update;
  if m.id is null or lower(m.sender) <> public.beyond_caller() then raise exception 'only the author can change a message' using errcode = '42501'; end if;
  l := public.beyond_link_side(m.link_id, p_company);
  if l.status <> 'active' then raise exception 'this chat connection has ended'; end if;
  if exists (select 1 from chat_link_reads r where r.link_id = m.link_id and r.last_read >= m.id and r.email <> lower(m.sender)) then
    raise exception 'someone has already seen this message — it can no longer be changed';
  end if;
  if p_delete then
    update chat_link_messages set body = '', files = '[]', deleted_at = now() where id = m.id;
  else
    if coalesce(trim(p_body), '') = '' then raise exception 'a message cannot be empty'; end if;
    update chat_link_messages set body = left(p_body, 20000), edited_at = now() where id = m.id;
  end if;
end $$;

create or replace function public.chat_link_read(p_company uuid, p_link uuid, p_last bigint) returns void
language plpgsql security definer set search_path = public as $$
declare l chat_links;
begin
  l := public.beyond_link_side(p_link, p_company);
  insert into chat_link_reads(link_id, email, last_read) values (l.id, public.beyond_caller(), coalesce(p_last, 0))
  on conflict (link_id, email) do update set last_read = greatest(chat_link_reads.last_read, excluded.last_read);
end $$;

-- ------------------------------------------------------------------ AI assistant settings (read only by the edge function)
create table if not exists public.ai_config (
  company_id uuid primary key references public.companies(id) on delete cascade,
  provider   text not null default 'anthropic',
  api_key    text,
  settings   jsonb not null default '{}',
  updated_by text,
  updated_at timestamptz not null default now()
);
create table if not exists public.ai_usage (
  company_id uuid not null references public.companies(id) on delete cascade,
  email      text not null,
  day        date not null default current_date,
  calls      int not null default 0,
  primary key (company_id, email, day)
);
alter table public.ai_config enable row level security;
alter table public.ai_usage enable row level security;
revoke all on public.ai_config, public.ai_usage from anon, authenticated;

-- what any member may know: is it on, which model, is a key installed (never the key)
create or replace function public.ai_settings_get(p_company uuid) returns json
language sql stable security definer set search_path = public as $$
  select case when public.beyond_in(p_company) then
    json_build_object('has_key', coalesce((select api_key is not null and api_key <> '' from ai_config where company_id = p_company), false),
                      'settings', coalesce((select settings from ai_config where company_id = p_company), '{}'::jsonb),
                      'is_super', public.beyond_is_super(p_company))
  end
$$;

-- super administrators: settings (always) and the key (p_key null = keep, '' = remove)
create or replace function public.ai_settings_set(p_company uuid, p_settings jsonb, p_key text default null) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_super(p_company);
  insert into ai_config(company_id, settings, api_key, updated_by, updated_at)
  values (p_company, coalesce(p_settings, '{}'), nullif(p_key, ''), public.beyond_caller(), now())
  on conflict (company_id) do update set settings = coalesce(excluded.settings, ai_config.settings),
    api_key = case when p_key is null then ai_config.api_key else nullif(p_key, '') end,
    updated_by = excluded.updated_by, updated_at = now();
end $$;

grant execute on function public.my_super(uuid), public.set_member_super(uuid, text, boolean),
  public.chat_link_list(uuid), public.chat_link_create(uuid), public.chat_link_accept(uuid, text),
  public.chat_link_end(uuid, uuid), public.chat_link_messages(uuid, uuid, bigint, int), public.chat_link_file(uuid, bigint, int),
  public.chat_link_send(uuid, uuid, text, jsonb, text), public.chat_link_edit(uuid, bigint, text, boolean),
  public.chat_link_read(uuid, uuid, bigint), public.ai_settings_get(uuid), public.ai_settings_set(uuid, jsonb, text)
  to authenticated;
