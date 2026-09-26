-- Beyond ERP shared workspace.
--
-- The app keeps its full accounting engine on every device. What the devices share is an ordered log of changes
-- (ops): each device appends its changes here and replays everyone else's in the same order, so every copy of the
-- books stays identical. Snapshots let a new device start from a recent copy instead of replaying the whole log.
--
-- Security: only workspace members can read anything; nobody writes the tables directly — all writes go through
-- the functions below, which check membership, keep the log gap-free (one sequence, no forks) and stamp who wrote it.
-- The app's own permission matrix is re-checked by every device when it replays a change.

create table if not exists public.members (
  email      text primary key,
  is_admin   boolean not null default false,
  gateway    boolean not null default false,  -- a desktop server that relays changes for its own local users
  active     boolean not null default true,
  added_by   text,
  added_at   timestamptz not null default now()
);

create table if not exists public.ops (
  seq          bigint primary key,
  version      text not null,
  actor        text not null,           -- workspace account that wrote the change (from the login token)
  app_user     text not null,           -- app user the change is replayed as
  device       text,
  method       text not null,
  path         text not null,
  body         text,
  content_type text,
  clock        timestamp not null,      -- the author's wall-clock time of the change; replays use it as "now"
  created_at   timestamptz not null default now()
);

create table if not exists public.snapshots (
  seq        bigint primary key,        -- the copy includes every change up to and including this one
  version    text not null,
  data       text not null,             -- gzip + base64 SQLite image
  created_by text,
  created_at timestamptz not null default now()
);

alter table public.members   enable row level security;
alter table public.ops       enable row level security;
alter table public.snapshots enable row level security;

create or replace function public.beyond_caller() returns text
language sql stable as $$ select lower(coalesce(auth.jwt() ->> 'email', '')) $$;

create or replace function public.beyond_is_member() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from members where lower(email) = public.beyond_caller() and active)
$$;

drop policy if exists "members read members" on public.members;
create policy "members read members" on public.members for select to authenticated using (public.beyond_is_member());
drop policy if exists "members read ops" on public.ops;
create policy "members read ops" on public.ops for select to authenticated using (public.beyond_is_member());
drop policy if exists "members read snapshots" on public.snapshots;
create policy "members read snapshots" on public.snapshots for select to authenticated using (public.beyond_is_member());

revoke all on public.members, public.ops, public.snapshots from anon;
revoke insert, update, delete on public.members, public.ops, public.snapshots from authenticated;
grant select on public.members, public.ops, public.snapshots to authenticated;

-- What the signed-in person can do here (works for non-members too, so the app can offer "create workspace").
create or replace function public.workspace_info() returns json
language plpgsql stable security definer set search_path = public as $$
declare m members; s snapshots;
begin
  select * into m from members where lower(email) = public.beyond_caller() and active;
  select seq, version, created_at into s.seq, s.version, s.created_at from snapshots order by seq desc limit 1;
  return json_build_object(
    'email', public.beyond_caller(),
    'has_members', exists (select 1 from members),
    'is_member', m.email is not null,
    'is_admin', coalesce(m.is_admin, false),
    'gateway', coalesce(m.gateway, false),
    'last_seq', case when m.email is not null then (select coalesce(max(seq), 0) from ops) end,
    'snapshot_seq', case when m.email is not null then s.seq end,
    'snapshot_version', case when m.email is not null then s.version end);
end $$;

-- The first person to call this on an empty workspace becomes its administrator.
create or replace function public.claim_workspace() returns json
language plpgsql security definer set search_path = public as $$
begin
  if public.beyond_caller() = '' then raise exception 'sign in first' using errcode = '42501'; end if;
  perform pg_advisory_xact_lock(424201);
  if exists (select 1 from members) then
    raise exception 'this workspace already has members — ask an administrator to add you' using errcode = '42501';
  end if;
  insert into members(email, is_admin, added_by) values (public.beyond_caller(), true, public.beyond_caller());
  return public.workspace_info();
end $$;

-- Append one change. p_seq must be exactly the next number: if someone else got there first the call fails with
-- SQLSTATE 40001 and the app pulls their change, re-checks its own and tries again.
create or replace function public.append_op(p_seq bigint, p_version text, p_app_user text, p_device text,
  p_method text, p_path text, p_body text, p_content_type text, p_clock timestamp) returns json
language plpgsql security definer set search_path = public as $$
declare m members; last bigint; who text;
begin
  select * into m from members where lower(email) = public.beyond_caller() and active;
  if m.email is null then raise exception 'not a member of this workspace' using errcode = '42501'; end if;
  if p_method not in ('POST', 'PUT', 'DELETE') or left(p_path, 5) <> '/api/' then
    raise exception 'invalid change' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(424202);
  select coalesce(max(seq), 0) into last from ops;
  if p_seq <> last + 1 then
    raise exception 'conflict: next change is %', last + 1 using errcode = '40001';
  end if;
  -- a gateway relays its own local users; anyone else's changes are recorded (and replayed) as themselves
  who := case when m.gateway and coalesce(p_app_user, '') <> '' then p_app_user else m.email end;
  insert into ops(seq, version, actor, app_user, device, method, path, body, content_type, clock)
  values (p_seq, p_version, m.email, who, left(p_device, 80), p_method, p_path, p_body, p_content_type, p_clock);
  return json_build_object('seq', p_seq, 'app_user', who);
end $$;

-- Save a full copy of the books at change p_seq (administrators and gateways; the app does this automatically).
create or replace function public.put_snapshot(p_seq bigint, p_version text, p_data text) returns bigint
language plpgsql security definer set search_path = public as $$
declare m members;
begin
  select * into m from members where lower(email) = public.beyond_caller() and active;
  if m.email is null or not (m.is_admin or m.gateway) then
    raise exception 'only administrators can save workspace copies' using errcode = '42501';
  end if;
  if p_seq > (select coalesce(max(seq), 0) from ops) then raise exception 'snapshot is ahead of the log'; end if;
  insert into snapshots(seq, version, data, created_by) values (p_seq, p_version, p_data, m.email)
  on conflict (seq) do update set version = excluded.version, data = excluded.data, created_by = excluded.created_by,
                                  created_at = now();
  -- keep the three newest copies
  delete from snapshots where seq not in (select seq from snapshots order by seq desc limit 3);
  return p_seq;
end $$;

-- Add, change or deactivate a member (administrators only; the last active administrator cannot be removed).
create or replace function public.set_member(p_email text, p_is_admin boolean, p_gateway boolean, p_active boolean)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not exists (select 1 from members where lower(email) = public.beyond_caller() and active and is_admin) then
    raise exception 'only administrators can manage members' using errcode = '42501';
  end if;
  if coalesce(trim(p_email), '') !~ '^[^@\s]+@[^@\s]+$' then raise exception 'enter an email address'; end if;
  perform pg_advisory_xact_lock(424201);
  insert into members(email, is_admin, gateway, active, added_by)
  values (lower(trim(p_email)), p_is_admin, p_gateway, p_active, public.beyond_caller())
  on conflict (email) do update set is_admin = excluded.is_admin, gateway = excluded.gateway, active = excluded.active;
  if not exists (select 1 from members where is_admin and active) then
    raise exception 'at least one active administrator must remain';
  end if;
end $$;

revoke execute on function public.workspace_info(), public.claim_workspace(),
  public.append_op(bigint, text, text, text, text, text, text, text, timestamp),
  public.put_snapshot(bigint, text, text), public.set_member(text, boolean, boolean, boolean),
  public.beyond_is_member(), public.beyond_caller() from public, anon;
grant execute on function public.workspace_info(), public.claim_workspace(),
  public.append_op(bigint, text, text, text, text, text, text, text, timestamp),
  public.put_snapshot(bigint, text, text), public.set_member(text, boolean, boolean, boolean),
  public.beyond_is_member(), public.beyond_caller() to authenticated;
