-- Who may publish public pages (booking, forms, signing, guest chat). Administrators always may; the app also lets
-- people whose permissions allow it: "Online Booking & Forms" at full rights publishes any page, "Offers" at full rights
-- only signing pages for offers. The books keep those permissions; the app copies them here (desk_publishers) because
-- the database cannot read the books.

alter table public.members add column if not exists desk_publish text not null default ''
  check (desk_publish in ('', 'sign', 'all'));

create or replace function public.beyond_require_publisher(p_company uuid, p_kind text) returns public.members
language plpgsql stable security definer set search_path = public as $$
declare m members;
begin
  m := public.beyond_member(p_company);
  if m.email is null or not (m.is_admin or m.desk_publish = 'all' or (m.desk_publish = 'sign' and p_kind = 'sign')) then
    raise exception 'you are not allowed to publish pages for this company — ask an administrator for full rights on Online Booking & Forms'
      using errcode = '42501';
  end if;
  return m;
end $$;

-- Create (p_id null) or change a page: administrators, and people allowed to publish this kind of page.
create or replace function public.desk_page_save(p_company uuid, p_id uuid, p_kind text, p_title text, p_config jsonb,
                                                 p_active boolean default true) returns json
language plpgsql security definer set search_path = public as $$
declare pg public_pages; k text := p_kind;
begin
  if p_id is not null then
    select kind into k from public_pages where id = p_id and company_id = p_company;
    if k is null then
      raise exception 'no such page' using errcode = 'P0002';
    end if;
  end if;
  perform public.beyond_require_publisher(p_company, k);
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
  end if;
  return json_build_object('id', pg.id, 'token', pg.token, 'kind', pg.kind);
end $$;

-- The app sends the whole list each time (administrators only): everyone named gets that right, everyone else none.
create or replace function public.desk_publishers(p_company uuid, p_all text[], p_sign text[]) returns int
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  perform public.beyond_require_admin(p_company);
  update members m set desk_publish = case
      when lower(m.email) = any(select lower(trim(x)) from unnest(coalesce(p_all, '{}')) x) then 'all'
      when lower(m.email) = any(select lower(trim(x)) from unnest(coalesce(p_sign, '{}')) x) then 'sign'
      else '' end
   where m.company_id = p_company;
  get diagnostics n = row_count;
  return n;
end $$;

-- what the signed-in person may publish here ('all', 'sign' or '')
create or replace function public.desk_my_publish(p_company uuid) returns text
language sql stable security definer set search_path = public as $$
  select case when m.is_admin then 'all' else coalesce(m.desk_publish, '') end
  from members m where m.company_id = p_company and lower(m.email) = public.beyond_caller() and m.active and not m.pending
$$;

revoke execute on function public.beyond_require_publisher(uuid, text) from public, anon, authenticated;
revoke execute on function public.desk_publishers(uuid, text[], text[]), public.desk_my_publish(uuid) from public, anon;
grant execute on function public.desk_publishers(uuid, text[], text[]), public.desk_my_publish(uuid) to authenticated;
