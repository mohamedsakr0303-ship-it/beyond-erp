-- Usernames for workspace members, so people can also sign in with username + password
-- (the "login" edge function looks up the email for a username on the server — the page never sees it).

alter table public.members add column if not exists username text;
create unique index if not exists members_username_key on public.members (lower(username)) where username is not null;

-- Link an app user to the workspace: make sure their email is a member and record their username.
-- Called by the app when an administrator saves a user with an email. Keeps admin / gateway flags as they are.
create or replace function public.link_member(p_email text, p_username text, p_active boolean default true)
returns void language plpgsql security definer set search_path = public as $$
declare e text := lower(trim(coalesce(p_email, ''))); u text := nullif(lower(trim(coalesce(p_username, ''))), '');
begin
  if not exists (select 1 from members where lower(email) = public.beyond_caller() and active and is_admin) then
    raise exception 'only workspace administrators can add people to the workspace' using errcode = '42501';
  end if;
  if e !~ '^[^@\s]+@[^@\s]+$' then raise exception 'enter an email address'; end if;
  if u is not null and u ~ '@' then u := null; end if;          -- an email is not a username
  perform pg_advisory_xact_lock(424201);
  update members set username = null where lower(username) = u and lower(email) <> e;   -- username moved to someone else
  insert into members(email, username, active, added_by) values (e, u, coalesce(p_active, true), public.beyond_caller())
  on conflict (email) do update set username = excluded.username, active = excluded.active;
  if not exists (select 1 from members where is_admin and active) then
    raise exception 'at least one active administrator must remain';
  end if;
end $$;

revoke execute on function public.link_member(text, text, boolean) from public, anon;
grant execute on function public.link_member(text, text, boolean) to authenticated;
