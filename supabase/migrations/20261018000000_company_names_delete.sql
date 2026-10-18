-- Company names and deleting a company.
--
-- 1. One person cannot have two companies with the same name (compared without case and extra spaces): creating or
--    renaming to a name one of their companies already has is refused. Companies of other people may share a name.
-- 2. A super administrator of a company can delete it, typing its name to confirm. Everything of that company goes
--    with it (its books, members, chat, billing, public pages — all tied to the company with on delete cascade).
-- 3. my_companies also gives when each company was made and how many changes its books hold, so two look-alike
--    companies can be told apart, and whether the caller may delete it.

create or replace function public.beyond_norm_name(p text) returns text language sql immutable as $$
  select lower(regexp_replace(trim(coalesce(p, '')), '\s+', ' ', 'g'))
$$;

-- Does the caller already belong to (or wait to join) another company with this name?
create or replace function public.beyond_name_taken(p_name text, p_except uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from members m join companies c on c.id = m.company_id
                 where lower(m.email) = public.beyond_caller() and (m.active or m.pending)
                   and c.id is distinct from p_except
                   and public.beyond_norm_name(c.name) = public.beyond_norm_name(p_name))
$$;

create or replace function public.my_companies() returns json
language sql stable security definer set search_path = public as $$
  select coalesce(json_agg(json_build_object('id', c.id, 'name', c.name, 'is_admin', m.is_admin, 'pending', m.pending,
                                             'active', m.active, 'created_at', c.created_at,
                                             'is_super', m.active and not m.pending
                                                         and (m.is_super or lower(m.email) = lower(coalesce(c.created_by, ''))),
                                             'changes', case when m.active and not m.pending
                                                             then (select coalesce(max(seq), 0) from ops o where o.company_id = c.id) end)
                            order by c.name, c.created_at), '[]'::json)
  from members m join companies c on c.id = m.company_id
  where lower(m.email) = public.beyond_caller() and (m.active or m.pending)
$$;

create or replace function public.create_company(p_name text) returns json
language plpgsql security definer set search_path = public as $$
declare cid uuid; n text := regexp_replace(trim(coalesce(p_name, '')), '\s+', ' ', 'g');
begin
  if public.beyond_caller() = '' then raise exception 'sign in first' using errcode = '42501'; end if;
  if n = '' or length(n) > 120 then raise exception 'enter the company name (up to 120 characters)'; end if;
  perform pg_advisory_xact_lock(424202, hashtext(public.beyond_caller()));
  if public.beyond_name_taken(n, null) then
    raise exception 'you already have a company called "%" — choose another name', n;
  end if;
  insert into companies(name, invite_code, created_by) values (n, public.beyond_new_code(), public.beyond_caller())
  returning id into cid;
  insert into members(company_id, email, is_admin, added_by) values (cid, public.beyond_caller(), true, public.beyond_caller());
  insert into people(email) values (public.beyond_caller()) on conflict (email) do nothing;
  return json_build_object('id', cid, 'name', n, 'is_admin', true, 'pending', false);
end $$;

create or replace function public.rename_company(p_company uuid, p_name text) returns void
language plpgsql security definer set search_path = public as $$
declare n text := left(regexp_replace(trim(coalesce(p_name, '')), '\s+', ' ', 'g'), 120);
begin
  perform public.beyond_require_admin(p_company);
  if n = '' then raise exception 'enter the company name'; end if;
  if public.beyond_name_taken(n, p_company) then
    raise exception 'you already have a company called "%" — choose another name', n;
  end if;
  update companies set name = n where id = p_company;
end $$;

-- Delete a company and everything of it: its super administrators only, typing its name to confirm.
create or replace function public.delete_company(p_company uuid, p_name text) returns void
language plpgsql security definer set search_path = public as $$
declare c companies;
begin
  perform public.beyond_require_super(p_company);
  select * into c from companies where id = p_company;
  if public.beyond_norm_name(p_name) <> public.beyond_norm_name(c.name) then
    raise exception 'type the company''s name exactly to delete it';
  end if;
  delete from companies where id = p_company;
end $$;

revoke all on function public.delete_company(uuid, text) from public, anon;
grant execute on function public.delete_company(uuid, text) to authenticated;
revoke all on function public.beyond_name_taken(text, uuid) from public, anon, authenticated;
