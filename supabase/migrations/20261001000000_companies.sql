-- Several companies in one workspace (multi-tenant).
--
-- The standard shared-schema design: every shared row carries its company (company_id), people belong to companies
-- through memberships (members: company_id + email, admin or not), and row level security lets a signed-in person
-- read only the rows of companies they are an active member of. One sign-in account per person (people: email and a
-- username unique across the whole workspace); the same person can belong to several companies with a different
-- role in each. Anyone signed in can create a company (they become its administrator) or ask to join one with its
-- invite code (an administrator of that company approves the request).
--
-- Existing data becomes the first company. The functions without a company argument keep working for app versions
-- before 1.3 (they act on the caller's own company when they have exactly one).

-- ------------------------------------------------------------------ tables
create table if not exists public.companies (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  invite_code text unique,
  created_by  text,
  created_at  timestamptz not null default now()
);

create table if not exists public.people (
  email    text primary key,
  username text
);
create unique index if not exists people_username_key on public.people (lower(username)) where username is not null;
insert into public.people(email, username)
  select lower(email), username from public.members on conflict (email) do nothing;

alter table public.members   add column if not exists company_id uuid references public.companies(id) on delete cascade;
alter table public.members   add column if not exists pending boolean not null default false;
alter table public.ops       add column if not exists company_id uuid references public.companies(id) on delete cascade;
alter table public.snapshots add column if not exists company_id uuid references public.companies(id) on delete cascade;

create or replace function public.beyond_new_code() returns text language sql volatile as $$
  select upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 8))
$$;

-- the books that existed before companies become the first company
do $$
declare cid uuid;
begin
  if exists (select 1 from public.members where company_id is null) or exists (select 1 from public.ops where company_id is null)
     or exists (select 1 from public.snapshots where company_id is null) then
    select id into cid from public.companies order by created_at limit 1;
    if cid is null then
      insert into public.companies(name, invite_code, created_by)
      values ('My company', public.beyond_new_code(),
              (select email from public.members where is_admin order by added_at limit 1))
      returning id into cid;
    end if;
    update public.members   set company_id = cid where company_id is null;
    update public.ops       set company_id = cid where company_id is null;
    update public.snapshots set company_id = cid where company_id is null;
  end if;
end $$;

alter table public.members   alter column company_id set not null;
alter table public.ops       alter column company_id set not null;
alter table public.snapshots alter column company_id set not null;
alter table public.members   drop constraint if exists members_pkey;
alter table public.members   add primary key (company_id, email);
alter table public.ops       drop constraint if exists ops_pkey;
alter table public.ops       add primary key (company_id, seq);
alter table public.snapshots drop constraint if exists snapshots_pkey;
alter table public.snapshots add primary key (company_id, seq);
drop index if exists public.members_username_key;          -- usernames now live in people (unique workspace-wide)
create index if not exists members_email_idx on public.members (lower(email));

alter table public.companies enable row level security;
alter table public.people    enable row level security;

-- ------------------------------------------------------------------ helpers
-- The caller's membership of a company (null when not an active, approved member).
create or replace function public.beyond_member(p_company uuid) returns public.members
language sql stable security definer set search_path = public as $$
  select * from members where company_id = p_company and lower(email) = public.beyond_caller() and active and not pending
$$;

create or replace function public.beyond_in(p_company uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from members where company_id = p_company and lower(email) = public.beyond_caller()
                 and active and not pending)
$$;

-- The caller's only company (for app versions before companies); null if none or several.
create or replace function public.beyond_default_company() returns uuid
language sql stable security definer set search_path = public as $$
  select case when count(*) = 1 then min(company_id::text)::uuid end
  from members where lower(email) = public.beyond_caller() and active and not pending
$$;

create or replace function public.beyond_require_admin(p_company uuid) returns public.members
language plpgsql stable security definer set search_path = public as $$
declare m members;
begin
  m := public.beyond_member(p_company);
  if m.email is null or not m.is_admin then
    raise exception 'only administrators of this company can do this' using errcode = '42501';
  end if;
  return m;
end $$;

-- ------------------------------------------------------------------ row level security
drop policy if exists "members read members" on public.members;
create policy "members read members" on public.members for select to authenticated using (public.beyond_in(company_id));
drop policy if exists "members read ops" on public.ops;
create policy "members read ops" on public.ops for select to authenticated using (public.beyond_in(company_id));
drop policy if exists "members read snapshots" on public.snapshots;
create policy "members read snapshots" on public.snapshots for select to authenticated using (public.beyond_in(company_id));
drop policy if exists "members read companies" on public.companies;
create policy "members read companies" on public.companies for select to authenticated using (public.beyond_in(id));

revoke all on public.companies, public.people from anon;
revoke insert, update, delete on public.companies, public.people from authenticated;
revoke select on public.people from authenticated;            -- usernames / emails are looked up on the server only
grant select on public.companies to authenticated;

-- ------------------------------------------------------------------ companies
-- Every company the caller belongs to (or has asked to join).
create or replace function public.my_companies() returns json
language sql stable security definer set search_path = public as $$
  select coalesce(json_agg(json_build_object('id', c.id, 'name', c.name, 'is_admin', m.is_admin, 'pending', m.pending,
                                             'active', m.active) order by c.name), '[]'::json)
  from members m join companies c on c.id = m.company_id
  where lower(m.email) = public.beyond_caller() and (m.active or m.pending)
$$;

-- Start a new company; the caller becomes its administrator.
create or replace function public.create_company(p_name text) returns json
language plpgsql security definer set search_path = public as $$
declare cid uuid; n text := trim(coalesce(p_name, ''));
begin
  if public.beyond_caller() = '' then raise exception 'sign in first' using errcode = '42501'; end if;
  if n = '' or length(n) > 120 then raise exception 'enter the company name (up to 120 characters)'; end if;
  insert into companies(name, invite_code, created_by) values (n, public.beyond_new_code(), public.beyond_caller())
  returning id into cid;
  insert into members(company_id, email, is_admin, added_by) values (cid, public.beyond_caller(), true, public.beyond_caller());
  insert into people(email) values (public.beyond_caller()) on conflict (email) do nothing;
  return json_build_object('id', cid, 'name', n, 'is_admin', true, 'pending', false);
end $$;

-- Ask to join a company with its invite code; an administrator of that company approves it.
create or replace function public.join_company(p_code text) returns json
language plpgsql security definer set search_path = public as $$
declare c companies; m members;
begin
  if public.beyond_caller() = '' then raise exception 'sign in first' using errcode = '42501'; end if;
  select * into c from companies where invite_code = upper(trim(coalesce(p_code, '')));
  if c.id is null then
    perform pg_sleep(0.3);
    raise exception 'no company has this invite code — check it with the company''s administrator';
  end if;
  select * into m from members where company_id = c.id and lower(email) = public.beyond_caller();
  if m.email is null then
    insert into members(company_id, email, is_admin, active, pending, added_by)
    values (c.id, public.beyond_caller(), false, false, true, public.beyond_caller());
    insert into people(email) values (public.beyond_caller()) on conflict (email) do nothing;
  end if;
  return json_build_object('id', c.id, 'name', c.name, 'pending', coalesce(m.pending, m.email is null),
                           'is_member', coalesce(m.active and not m.pending, false));
end $$;

create or replace function public.rename_company(p_company uuid, p_name text) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_admin(p_company);
  if trim(coalesce(p_name, '')) = '' then raise exception 'enter the company name'; end if;
  update companies set name = left(trim(p_name), 120) where id = p_company;
end $$;

create or replace function public.new_invite_code(p_company uuid) returns text
language plpgsql security definer set search_path = public as $$
declare code text := public.beyond_new_code();
begin
  perform public.beyond_require_admin(p_company);
  update companies set invite_code = code where id = p_company;
  return code;
end $$;

-- ------------------------------------------------------------------ one company's workspace
create or replace function public.workspace_info(p_company uuid) returns json
language plpgsql stable security definer set search_path = public as $$
declare m members; c companies; s snapshots; req members;
begin
  select * into c from companies where id = p_company;
  select * into req from members where company_id = p_company and lower(email) = public.beyond_caller();
  m := public.beyond_member(p_company);
  select seq, version into s.seq, s.version from snapshots where company_id = p_company order by seq desc limit 1;
  return json_build_object(
    'email', public.beyond_caller(),
    'company', case when m.email is not null then c.id end,
    'company_name', case when m.email is not null or req.pending then c.name end,
    'invite_code', case when m.is_admin then c.invite_code end,
    'has_members', m.email is not null,
    'is_member', m.email is not null,
    'pending', coalesce(req.pending, false),
    'is_admin', coalesce(m.is_admin, false),
    'gateway', coalesce(m.gateway, false),
    'last_seq', case when m.email is not null then (select coalesce(max(seq), 0) from ops where company_id = p_company) end,
    'snapshot_seq', case when m.email is not null then s.seq end,
    'snapshot_version', case when m.email is not null then s.version end);
end $$;

create or replace function public.append_op(p_company uuid, p_seq bigint, p_version text, p_app_user text, p_device text,
  p_method text, p_path text, p_body text, p_content_type text, p_clock timestamp) returns json
language plpgsql security definer set search_path = public as $$
declare m members; last bigint; who text;
begin
  m := public.beyond_member(p_company);
  if m.email is null then raise exception 'not a member of this company' using errcode = '42501'; end if;
  if p_method not in ('POST', 'PUT', 'DELETE') or left(p_path, 5) <> '/api/' then
    raise exception 'invalid change' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(424202, hashtext(p_company::text));
  select coalesce(max(seq), 0) into last from ops where company_id = p_company;
  if p_seq <> last + 1 then
    raise exception 'conflict: next change is %', last + 1 using errcode = '40001';
  end if;
  who := case when m.gateway and coalesce(p_app_user, '') <> '' then p_app_user else m.email end;
  insert into ops(company_id, seq, version, actor, app_user, device, method, path, body, content_type, clock)
  values (p_company, p_seq, p_version, m.email, who, left(p_device, 80), p_method, p_path, p_body, p_content_type, p_clock);
  return json_build_object('seq', p_seq, 'app_user', who);
end $$;

create or replace function public.put_snapshot(p_company uuid, p_seq bigint, p_version text, p_data text) returns bigint
language plpgsql security definer set search_path = public as $$
declare m members;
begin
  m := public.beyond_member(p_company);
  if m.email is null or not (m.is_admin or m.gateway) then
    raise exception 'only administrators can save company copies' using errcode = '42501';
  end if;
  if p_seq > (select coalesce(max(seq), 0) from ops where company_id = p_company) then
    raise exception 'snapshot is ahead of the log';
  end if;
  insert into snapshots(company_id, seq, version, data, created_by) values (p_company, p_seq, p_version, p_data, m.email)
  on conflict (company_id, seq) do update set version = excluded.version, data = excluded.data,
                                              created_by = excluded.created_by, created_at = now();
  delete from snapshots where company_id = p_company
    and seq not in (select seq from snapshots where company_id = p_company order by seq desc limit 3);
  return p_seq;
end $$;

-- People of a company, with their usernames (administrators).
create or replace function public.company_members(p_company uuid) returns json
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_admin(p_company);
  return (select coalesce(json_agg(json_build_object('email', m.email, 'username', p.username, 'is_admin', m.is_admin,
                                                     'gateway', m.gateway, 'active', m.active, 'pending', m.pending)
                                   order by m.pending desc, m.email), '[]'::json)
          from members m left join people p on p.email = lower(m.email) where m.company_id = p_company);
end $$;

-- Add, change, approve or deactivate a member (the last active administrator cannot be removed).
create or replace function public.set_member(p_company uuid, p_email text, p_is_admin boolean, p_gateway boolean,
  p_active boolean) returns void
language plpgsql security definer set search_path = public as $$
declare e text := lower(trim(coalesce(p_email, '')));
begin
  perform public.beyond_require_admin(p_company);
  if e !~ '^[^@\s]+@[^@\s]+$' then raise exception 'enter an email address'; end if;
  perform pg_advisory_xact_lock(424201, hashtext(p_company::text));
  insert into members(company_id, email, is_admin, gateway, active, pending, added_by)
  values (p_company, e, p_is_admin, p_gateway, p_active, false, public.beyond_caller())
  on conflict (company_id, email) do update set is_admin = excluded.is_admin, gateway = excluded.gateway,
                                                active = excluded.active, pending = false;
  insert into people(email) values (e) on conflict (email) do nothing;
  if not exists (select 1 from members where company_id = p_company and is_admin and active and not pending) then
    raise exception 'at least one active administrator must remain';
  end if;
end $$;

-- Remove someone from a company (or turn down a join request). Their sign-in account stays.
create or replace function public.remove_member(p_company uuid, p_email text) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_admin(p_company);
  if lower(trim(p_email)) = public.beyond_caller() then raise exception 'you cannot remove yourself'; end if;
  delete from members where company_id = p_company and lower(email) = lower(trim(p_email));
  if not exists (select 1 from members where company_id = p_company and is_admin and active and not pending) then
    raise exception 'at least one active administrator must remain';
  end if;
end $$;

-- Make someone a member and record their username (app Users screen). A username belongs to one person
-- workspace-wide; it cannot be taken from someone else.
create or replace function public.link_member(p_company uuid, p_email text, p_username text, p_active boolean default true)
returns void language plpgsql security definer set search_path = public as $$
declare e text := lower(trim(coalesce(p_email, ''))); u text := nullif(lower(trim(coalesce(p_username, ''))), '');
begin
  perform public.beyond_require_admin(p_company);
  if e !~ '^[^@\s]+@[^@\s]+$' then raise exception 'enter an email address'; end if;
  if u is not null and u ~ '@' then u := null; end if;
  perform pg_advisory_xact_lock(424201, hashtext(p_company::text));
  if u is not null and exists (select 1 from people where lower(username) = u and email <> e) then
    raise exception 'the username % is already used by another account — choose another', u;
  end if;
  insert into people(email, username) values (e, u)
  on conflict (email) do update set username = coalesce(excluded.username, people.username);
  insert into members(company_id, email, active, added_by) values (p_company, e, coalesce(p_active, true), public.beyond_caller())
  on conflict (company_id, email) do update set active = excluded.active, pending = false;
  if not exists (select 1 from members where company_id = p_company and is_admin and active and not pending) then
    raise exception 'at least one active administrator must remain';
  end if;
end $$;

-- ------------------------------------------------------------------ app versions before 1.3 (one company each)
drop function if exists public.claim_workspace();
create or replace function public.claim_workspace() returns json
language plpgsql security definer set search_path = public as $$
begin
  if exists (select 1 from companies) then
    raise exception 'update the app to create or join a company' using errcode = '42501';
  end if;
  perform public.create_company('My company');
  return public.workspace_info();
end $$;

drop function if exists public.workspace_info();
create or replace function public.workspace_info() returns json
language sql stable security definer set search_path = public as $$
  select public.workspace_info(public.beyond_default_company())
$$;

drop function if exists public.append_op(bigint, text, text, text, text, text, text, text, timestamp);
create or replace function public.append_op(p_seq bigint, p_version text, p_app_user text, p_device text,
  p_method text, p_path text, p_body text, p_content_type text, p_clock timestamp) returns json
language sql security definer set search_path = public as $$
  select public.append_op(public.beyond_default_company(), p_seq, p_version, p_app_user, p_device, p_method, p_path,
                          p_body, p_content_type, p_clock)
$$;

drop function if exists public.put_snapshot(bigint, text, text);
create or replace function public.put_snapshot(p_seq bigint, p_version text, p_data text) returns bigint
language sql security definer set search_path = public as $$
  select public.put_snapshot(public.beyond_default_company(), p_seq, p_version, p_data)
$$;

drop function if exists public.set_member(text, boolean, boolean, boolean);
create or replace function public.set_member(p_email text, p_is_admin boolean, p_gateway boolean, p_active boolean)
returns void language sql security definer set search_path = public as $$
  select public.set_member(public.beyond_default_company(), p_email, p_is_admin, p_gateway, p_active)
$$;

drop function if exists public.link_member(text, text, boolean);
create or replace function public.link_member(p_email text, p_username text, p_active boolean default true)
returns void language sql security definer set search_path = public as $$
  select public.link_member(public.beyond_default_company(), p_email, p_username, p_active)
$$;

drop function if exists public.beyond_is_member();

-- ------------------------------------------------------------------ grants
revoke execute on all functions in schema public from public, anon;
grant execute on function
  public.beyond_caller(), public.beyond_member(uuid), public.beyond_in(uuid), public.beyond_default_company(),
  public.my_companies(), public.create_company(text), public.join_company(text), public.rename_company(uuid, text),
  public.new_invite_code(uuid), public.workspace_info(uuid), public.workspace_info(), public.claim_workspace(),
  public.append_op(uuid, bigint, text, text, text, text, text, text, text, timestamp),
  public.append_op(bigint, text, text, text, text, text, text, text, timestamp),
  public.put_snapshot(uuid, bigint, text, text), public.put_snapshot(bigint, text, text),
  public.company_members(uuid), public.set_member(uuid, text, boolean, boolean, boolean),
  public.set_member(text, boolean, boolean, boolean), public.remove_member(uuid, text),
  public.link_member(uuid, text, text, boolean), public.link_member(text, text, boolean)
  to authenticated;
revoke execute on function public.beyond_new_code(), public.beyond_require_admin(uuid) from authenticated;
grant execute on function public.auth_user_id(text) to service_role;
