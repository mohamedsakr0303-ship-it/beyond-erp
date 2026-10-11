-- People in a company: administrators add people (and approve join requests); changing an existing member or removing
-- someone is for the company's super administrators. Same rule as the app's own Users screen.

-- Add, approve, change or deactivate a member (the last active administrator cannot be removed).
create or replace function public.set_member(p_company uuid, p_email text, p_is_admin boolean, p_gateway boolean,
  p_active boolean) returns void
language plpgsql security definer set search_path = public as $$
declare e text := lower(trim(coalesce(p_email, ''))); cur members%rowtype;
begin
  perform public.beyond_require_admin(p_company);
  if e !~ '^[^@\s]+@[^@\s]+$' then raise exception 'enter an email address'; end if;
  perform pg_advisory_xact_lock(424201, hashtext(p_company::text));
  select * into cur from members where company_id = p_company and lower(email) = e;
  if found and not cur.pending and e <> public.beyond_caller()
     and (cur.is_admin is distinct from p_is_admin or cur.gateway is distinct from p_gateway or cur.active is distinct from p_active) then
    perform public.beyond_require_super(p_company);     -- changing someone already in the company
  end if;
  insert into members(company_id, email, is_admin, gateway, active, pending, added_by)
  values (p_company, e, p_is_admin, p_gateway, p_active, false, public.beyond_caller())
  on conflict (company_id, email) do update set is_admin = excluded.is_admin, gateway = excluded.gateway,
                                                active = excluded.active, pending = false;
  insert into people(email) values (e) on conflict (email) do nothing;
  if not exists (select 1 from members where company_id = p_company and is_admin and active and not pending) then
    raise exception 'at least one active administrator must remain';
  end if;
end $$;

-- Remove someone from a company: super administrators only. Turning down a join request stays with administrators.
create or replace function public.remove_member(p_company uuid, p_email text) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_admin(p_company);
  if lower(trim(p_email)) = public.beyond_caller() then raise exception 'you cannot remove yourself'; end if;
  if exists (select 1 from members where company_id = p_company and lower(email) = lower(trim(p_email)) and not pending) then
    perform public.beyond_require_super(p_company);
  end if;
  delete from members where company_id = p_company and lower(email) = lower(trim(p_email));
  if not exists (select 1 from members where company_id = p_company and is_admin and active and not pending) then
    raise exception 'at least one active administrator must remain';
  end if;
end $$;
