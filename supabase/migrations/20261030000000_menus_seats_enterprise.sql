-- Menus by industry, menu control per company, hard user limits and the Enterprise plan.
--
-- 1. Each company's industry is known to the platform (the app reports it when an industry template is applied; the
--    console can set it). Every plan includes the "core" sections plus the starter sections of the company's industry
--    (billing_config.core_by_industry, editable in the console; it starts as the sections each industry template shows).
-- 2. Any section, screen or tab can be switched off for a plan (plan.deny) or for one company (overrides.deny), and a
--    company can be given back what its plan switches off (overrides.allow). Keys: "sales", "sales.offers",
--    "reports.forecast.prices". A company's own switches apply even when billing is off or the company is free.
-- 3. A plan with a set number of users is a hard limit: nobody can be added, approved or re-activated past it, and the
--    app cannot create another user past it either (also through a desktop gateway).
-- 4. The Enterprise plan: price on request (contact: true). It cannot be ordered from the app; the customer sends a
--    request and the platform team sets the company up (users, sections, assistant, support, agreed price).

alter table public.billing_config add column if not exists core_by_industry jsonb not null default '{}'::jsonb;
alter table public.company_billing add column if not exists industry text;

-- the starter sections of each industry: what its template shows (erp/industries.py TYPES minus their hidden sections)
update public.billing_config set core_by_industry = '{"general": {"name": "General business", "name_ar": "نشاط عام", "modules": ["dashboard", "crm", "business_units", "projects", "sales", "purchasing", "stock", "payroll", "assets", "accounting", "partners", "reports", "tax", "import_export", "settings", "users", "team"]}, "interior": {"name": "Interior design & finishing", "name_ar": "التصميم الداخلي والتشطيبات", "modules": ["dashboard", "crm", "business_units", "projects", "contracts", "sales", "estimating", "purchasing", "stock", "equipment", "payroll", "assets", "accounting", "partners", "reports", "tax", "import_export", "settings", "users", "team"]}, "contracting": {"name": "Contracting & construction", "name_ar": "المقاولات العامة والإنشاءات", "modules": ["dashboard", "crm", "business_units", "projects", "contracts", "sales", "estimating", "purchasing", "stock", "equipment", "payroll", "assets", "accounting", "partners", "reports", "tax", "import_export", "settings", "users", "team"]}, "development": {"name": "Real-estate development", "name_ar": "التطوير العقاري", "modules": ["dashboard", "crm", "business_units", "projects", "contracts", "sales", "shop", "estimating", "purchasing", "stock", "equipment", "payroll", "assets", "accounting", "partners", "reports", "tax", "import_export", "settings", "users", "team"]}, "sales": {"name": "Trading & sales", "name_ar": "التجارة والمبيعات", "modules": ["dashboard", "crm", "business_units", "projects", "sales", "shop", "purchasing", "stock", "payroll", "assets", "accounting", "partners", "reports", "tax", "import_export", "settings", "users", "team"]}, "investment": {"name": "Investment & holding", "name_ar": "الاستثمار والقابضة", "modules": ["dashboard", "crm", "business_units", "sales", "purchasing", "payroll", "assets", "accounting", "partners", "reports", "tax", "import_export", "settings", "users", "team"]}, "scaffolding": {"name": "Scaffolding & equipment rental", "name_ar": "تأجير السقالات والمعدات", "modules": ["dashboard", "crm", "business_units", "projects", "sales", "purchasing", "stock", "equipment", "rentals", "payroll", "assets", "accounting", "partners", "reports", "tax", "import_export", "settings", "users", "team"]}, "healthcare": {"name": "Clinics & medical centres", "name_ar": "العيادات والمراكز الطبية", "modules": ["dashboard", "crm", "care", "business_units", "sales", "purchasing", "stock", "equipment", "payroll", "assets", "accounting", "partners", "reports", "tax", "import_export", "settings", "users", "team"]}, "homecare": {"name": "Home healthcare", "name_ar": "الرعاية الصحية المنزلية", "modules": ["dashboard", "crm", "care", "business_units", "sales", "purchasing", "stock", "payroll", "assets", "accounting", "partners", "reports", "tax", "import_export", "settings", "users", "team"]}}'::jsonb
 where id = 1 and core_by_industry = '{}'::jsonb;

-- the Enterprise plan, once (owners who removed it keep it removed: it is added only while no plan is "contact us")
update public.billing_config
   set plans = plans || jsonb_build_array(jsonb_build_object(
     'code', 'enterprise', 'name', 'Enterprise', 'name_ar', 'المؤسسات', 'contact', true, 'price', 0, 'modules', 'all',
     'ai', true, 'support', true, 'badge', 'Contact us', 'badge_ar', 'تواصل معنا',
     'tagline', 'For larger teams and groups of companies', 'tagline_ar', 'للفرق الكبيرة ومجموعات الشركات',
     'features', jsonb_build_array('Users, companies and sections agreed with you', 'Every section and the AI assistant',
                                   'Priority support and on-site visits as agreed', 'Price on request'),
     'features_ar', jsonb_build_array('عدد المستخدمين والشركات والأقسام حسب الاتفاق', 'كل الأقسام والمساعد الذكي',
                                      'دعم بأولوية وزيارات ميدانية حسب الاتفاق', 'السعر عند الطلب'))),
       updated_at = now(), updated_by = 'enterprise plan'
 where id = 1 and not exists (select 1 from jsonb_array_elements(plans) p where coalesce((p->>'contact')::boolean, false));

-- a list of keys in a jsonb value (anything else: none)
create or replace function public.beyond_keys(v jsonb) returns jsonb
language sql immutable as $$
  select case when jsonb_typeof(v) = 'array'
    then coalesce((select jsonb_agg(distinct x) from jsonb_array_elements_text(v) x where x ~ '^[a-z_]+(\.[a-z_]+){0,2}$'), '[]'::jsonb)
    else '[]'::jsonb end
$$;

-- ================================================================== what a company may use
-- modules: null = every section; otherwise the sections it may open (core + its industry's starters + plan + add-ons
-- + given to it). deny: sections / screens / tabs switched off (by its plan unless given back, and for it alone).
create or replace function public.beyond_entitle(cfg public.billing_config, plan jsonb, status text, b public.company_billing) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare full_access boolean; mods jsonb; ai boolean; sup boolean; o jsonb := coalesce(b.overrides, '{}'::jsonb); a jsonb := coalesce(b.addons, '{}'::jsonb);
        deny jsonb;
begin
  full_access := cfg.id is null or not cfg.enabled or status = 'comped' or (status = 'trial' and cfg.trial_full);
  if full_access or o->>'modules' = 'all' or coalesce(plan->>'modules', 'all') = 'all' or jsonb_typeof(plan->'modules') <> 'array' then
    mods := null;
  else
    select coalesce(jsonb_agg(distinct m), '[]'::jsonb) into mods
      from (select jsonb_array_elements_text(coalesce(cfg.core_modules, '[]'::jsonb)) m
            union select jsonb_array_elements_text(public.beyond_keys(cfg.core_by_industry -> coalesce(b.industry, '') -> 'modules'))
            union select jsonb_array_elements_text(plan->'modules')
            union select jsonb_array_elements_text(case when jsonb_typeof(a->'modules') = 'array' then a->'modules' else '[]'::jsonb end)
            union select jsonb_array_elements_text(case when jsonb_typeof(o->'modules') = 'array' then o->'modules' else '[]'::jsonb end)) x;
  end if;
  -- switched off: by the plan (unless the company has it back or has full use), and for this company alone (always)
  select coalesce(jsonb_agg(distinct k), '[]'::jsonb) into deny
    from (select jsonb_array_elements_text(case when full_access then '[]'::jsonb else public.beyond_keys(plan->'deny') end) k
          except select jsonb_array_elements_text(public.beyond_keys(o->'allow'))
          union select jsonb_array_elements_text(public.beyond_keys(o->'deny'))) x;
  ai := case when cfg.id is not null and cfg.ai_mode = 'off' then false
             when o ? 'ai' and jsonb_typeof(o->'ai') = 'boolean' then (o->>'ai')::boolean
             when cfg.id is null or cfg.ai_mode = 'free' or full_access then true
             else coalesce((plan->>'ai')::boolean, false) or coalesce((a->>'ai')::boolean, false) end;
  sup := case when o ? 'support' and jsonb_typeof(o->'support') = 'boolean' then (o->>'support')::boolean
              when cfg.id is null or cfg.support_mode = 'free' or full_access then true
              else coalesce((plan->>'support')::boolean, true) or coalesce((a->>'support')::boolean, false) end;
  return jsonb_build_object('modules', mods, 'ai', ai, 'support', sup, 'deny', deny, 'industry', b.industry);
end $$;

-- Where a company stands (as before). The users a company may have: its plan's (+ extra users bought), or the number
-- set for it in the console — which applies even when billing is off or the company is free. An Enterprise ("contact
-- us") plan has no number of its own: the console sets it per company.
create or replace function public.beyond_billing_state(p_company uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare cfg billing_config; co companies; b company_billing; trial_end timestamptz; status text; ends timestamptz;
        plan jsonb; users int; members_n int; first_one boolean; ent jsonb; set_users int;
begin
  select * into cfg from billing_config where id = 1;
  select * into b from company_billing where company_id = p_company;
  select count(*) into members_n from members where company_id = p_company and active and not pending;
  set_users := case when jsonb_typeof(b.overrides->'users') = 'number' then (b.overrides->>'users')::int end;
  if cfg.id is null or not cfg.enabled then
    -- billing off: free for everyone; how many people it has is still shown (only a number set for it is a limit)
    ent := public.beyond_entitle(cfg, null, 'free', b) || jsonb_build_object('users', members_n, 'users_allowed', set_users,
             'addons', coalesce(b.addons, '{}'::jsonb), 'discount_pct', coalesce(b.discount_pct, 0), 'extra_users', coalesce(b.extra_users, 0));
    if coalesce(b.archived, false) then
      return jsonb_build_object('enabled', false, 'status', 'archived', 'can_write', false) || ent;
    end if;
    if coalesce(b.suspended, false) then
      return jsonb_build_object('enabled', false, 'status', 'suspended', 'can_write', false, 'suspend_note', b.suspend_note) || ent;
    end if;
    return jsonb_build_object('enabled', false, 'status', 'free', 'can_write', true) || ent;
  end if;
  select * into co from companies where id = p_company;
  first_one := co.created_at <= coalesce(cfg.enabled_at, co.created_at)
            or co.created_at <= coalesce(cfg.one_trial_from, '-infinity'::timestamptz)
            or not exists (select 1 from companies o where lower(coalesce(o.created_by, '')) = lower(coalesce(co.created_by, ''))
                           and o.id <> co.id and (o.created_at, o.id::text) < (co.created_at, co.id::text));
  trial_end := coalesce(b.trial_ends,
                        case when first_one then greatest(co.created_at, coalesce(cfg.enabled_at, co.created_at)) + make_interval(days => cfg.trial_days)
                             else co.created_at end);
  if coalesce(b.comped, false) then status := 'comped'; ends := null;
  elsif b.paid_until is not null and b.paid_until > now() then status := 'active'; ends := b.paid_until;
  elsif b.paid_until is not null and b.paid_until + make_interval(days => cfg.grace_days) > now() and b.paid_until >= trial_end then
    status := 'past_due'; ends := b.paid_until + make_interval(days => cfg.grace_days);
  elsif trial_end > now() then status := 'trial'; ends := trial_end;
  elsif b.paid_until is null and not first_one and b.trial_ends is null then status := 'unpaid'; ends := null;
  else status := 'expired'; ends := greatest(trial_end, coalesce(b.paid_until, trial_end));
  end if;
  plan := public.beyond_plan(coalesce(case when status in ('trial', 'unpaid') then null else b.plan end, cfg.trial_plan));
  if plan is null then plan := cfg.plans -> 0; end if;
  users := case when coalesce((plan->>'contact')::boolean, false) then (plan->>'users')::int
                else coalesce((plan->>'users')::int, 1) end
         + case when status in ('trial', 'unpaid') then 0 else coalesce(b.extra_users, 0) end;
  if set_users is not null then users := set_users; end if;
  ent := public.beyond_entitle(cfg, plan, status, b);
  return jsonb_build_object(
    'enabled', true, 'status', case when coalesce(b.archived, false) then 'archived' when coalesce(b.suspended, false) then 'suspended' else status end,
    'billing_status', status,
    'can_write', status not in ('expired', 'unpaid') and not coalesce(b.suspended, false) and not coalesce(b.archived, false),
    'suspend_note', case when coalesce(b.suspended, false) then b.suspend_note end,
    'ends', ends, 'days_left', case when ends is null then null else greatest(0, ceil(extract(epoch from ends - now()) / 86400))::int end,
    'trial_ends', trial_end, 'paid_until', b.paid_until, 'plan', plan->>'code', 'plan_name', plan->>'name',
    'contact', coalesce((plan->>'contact')::boolean, false),
    'users_allowed', case when status = 'comped' and set_users is null then null else users end, 'users', members_n,
    'extra_users', coalesce(b.extra_users, 0), 'cancel_note', b.cancel_note,
    'addons', coalesce(b.addons, '{}'::jsonb), 'discount_pct', coalesce(b.discount_pct, 0),
    'currency', cfg.currency, 'grace_days', cfg.grace_days) || ent;
end $$;

-- A company cannot have more active people than it may have: adding, approving a request or re-activating someone
-- past the number is refused (whether billing is on or not, once a number applies to it).
create or replace function public.beyond_seat_check() returns trigger
language plpgsql security definer set search_path = public as $$
declare st jsonb; n int;
begin
  if not new.active or new.pending then return new; end if;
  if tg_op = 'UPDATE' and old.active and not old.pending and old.company_id = new.company_id and lower(old.email) = lower(new.email) then return new; end if;
  perform pg_advisory_xact_lock(424203, hashtext(new.company_id::text));     -- two additions at once cannot both take the last seat
  st := public.beyond_billing_state(new.company_id);
  if st->>'users_allowed' is null then return new; end if;
  select count(*) into n from members where company_id = new.company_id and active and not pending and lower(email) <> lower(new.email);
  if n + 1 > (st->>'users_allowed')::int then
    raise exception 'seat_limit: % — this company may have % user(s) and all are taken. An administrator can add users in Settings → Subscription, or deactivate someone first.',
      coalesce(st->>'plan_name', 'current plan'), st->>'users_allowed' using errcode = 'P0402';
  end if;
  return new;
end $$;
drop trigger if exists members_seat_check on public.members;
create trigger members_seat_check before insert or update of active, pending, company_id, email on public.members
  for each row execute function public.beyond_seat_check();

-- Changes (as before), and a new user cannot be created in the books once every seat is taken.
create or replace function public.append_op(p_company uuid, p_seq bigint, p_version text, p_app_user text, p_device text,
  p_method text, p_path text, p_body text, p_content_type text, p_clock timestamp) returns json
language plpgsql security definer set search_path = public as $$
declare m members; last bigint; who text; st jsonb; mt jsonb;
begin
  m := public.beyond_member(p_company);
  if m.email is null then raise exception 'not a member of this company' using errcode = '42501'; end if;
  if p_method not in ('POST', 'PUT', 'DELETE') or left(p_path, 5) <> '/api/' then
    raise exception 'invalid change' using errcode = '22023';
  end if;
  select maintenance into mt from billing_config where id = 1;
  if coalesce((mt->>'on')::boolean, false) and not public.beyond_is_owner() then
    raise exception 'maintenance: %', coalesce(nullif(mt->>'message', ''), 'BeyondERP is under maintenance — changes are paused for a short while. Everything stays available to view.')
      using errcode = 'P0503';
  end if;
  st := public.beyond_billing_state(p_company);
  if not (st->>'can_write')::boolean then
    if st->>'status' = 'archived' then
      raise exception 'subscription_ended: this company is archived — contact BeyondERP to reopen it. Everything stays available to view, print and export.'
        using errcode = 'P0402';
    end if;
    if st->>'status' = 'suspended' then
      raise exception 'subscription_ended: this company is suspended%. Everything stays available to view, print and export.',
        coalesce(' — ' || nullif(st->>'suspend_note', ''), '') using errcode = 'P0402';
    end if;
    if st->>'status' = 'unpaid' then
      raise exception 'subscription_ended: this company is not in a subscription yet — an administrator adds it in Settings → Subscription. Everything stays available to view, print and export.'
        using errcode = 'P0402';
    end if;
    raise exception 'subscription_ended: the free trial / paid period of this company has ended — an administrator can subscribe in Settings → Subscription. Everything stays available to view, print and export.'
      using errcode = 'P0402';
  end if;
  if p_method = 'POST' and p_path ~ '^/api/users/?$' and st->>'users_allowed' is not null
     and (st->>'users')::int >= (st->>'users_allowed')::int then
    raise exception 'seat_limit: % — this company may have % user(s) and all are taken. An administrator can add users in Settings → Subscription, or deactivate someone first.',
      coalesce(st->>'plan_name', 'current plan'), st->>'users_allowed' using errcode = 'P0402';
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

-- The app tells the platform which industry a company works in (when an industry template is applied, and when it
-- opens a company whose industry the platform does not know yet). Administrators of the company only.
create or replace function public.company_set_industry(p_company uuid, p_industry text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare m members; k text := lower(trim(coalesce(p_industry, '')));
begin
  m := public.beyond_member(p_company);
  if m.email is null or not (m.is_admin or m.gateway) then raise exception 'administrators only' using errcode = '42501'; end if;
  if k <> '' and k !~ '^[a-z_]{1,40}$' then raise exception 'unknown industry'; end if;
  insert into company_billing(company_id) values (p_company) on conflict (company_id) do nothing;
  update company_billing set industry = nullif(k, ''), updated_at = now()
   where company_id = p_company and coalesce(industry, '') is distinct from k;
  return public.beyond_billing_state(p_company);
end $$;
revoke all on function public.company_set_industry(uuid, text) from public, anon;
grant execute on function public.company_set_industry(uuid, text) to authenticated;
-- ================================================================== the Enterprise plan is not ordered from the app
create or replace function public.beyond_order_price(p_plan text, p_months int, p_lines jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare cfg billing_config; plan jsonb; m int := coalesce(p_months, 1); x jsonb; out jsonb := '[]'::jsonb; total numeric := 0;
        base numeric; extra int; cid uuid; nm text; members_n int; i int := 0; pct numeric; disc numeric; amt numeric;
        mods jsonb; mod text; addon numeric; want_ai boolean; want_sup boolean; incl jsonb; per numeric; users_cap int;
        rows jsonb := '[]'::jsonb; r jsonb;
begin
  select * into cfg from billing_config where id = 1;
  plan := public.beyond_plan(p_plan);
  if plan is null then raise exception 'unknown plan %', p_plan; end if;
  if coalesce((plan->>'contact')::boolean, false) then
    raise exception 'contact_us: the % plan is arranged with us directly — send a request from Settings → Subscription and we will get back to you', plan->>'name'
      using errcode = 'P0402';
  end if;
  if m not in (1, 12) then raise exception 'pay for 1 month or 12 months'; end if;
  if jsonb_typeof(p_lines) <> 'array' or jsonb_array_length(p_lines) = 0 then raise exception 'choose at least one company'; end if;
  if jsonb_array_length(p_lines) > 50 then raise exception 'up to 50 companies in one order'; end if;
  per := case when m = 12 then cfg.yearly_months else 1 end;
  -- what the plan already includes
  incl := case when coalesce(plan->>'modules', 'all') = 'all' or jsonb_typeof(plan->'modules') <> 'array' then null
               else coalesce(cfg.core_modules, '[]'::jsonb) || (plan->'modules') end;
  -- each line's list price first; the most expensive one counts in full
  for x in select v from jsonb_array_elements(p_lines) v loop
    cid := (x->>'company_id')::uuid;
    extra := coalesce((x->>'extra_users')::int, 0);
    if extra < 0 or extra > 500 then raise exception 'extra users must be 0 to 500'; end if;
    select name into nm from companies where id = cid;
    if nm is null then raise exception 'company not found'; end if;
    if exists (select 1 from jsonb_array_elements(rows) o where o->>'company_id' = cid::text) then raise exception 'a company appears twice'; end if;
    select count(*) into members_n from members where company_id = cid and active and not pending;
    select case when jsonb_typeof(overrides->'users') = 'number' then (overrides->>'users')::int end into users_cap from company_billing where company_id = cid;
    if users_cap is null and members_n > (plan->>'users')::int + extra then
      raise exception '"%" has % active people — choose a bigger plan or add % extra user(s) for it', nm, members_n,
        members_n - (plan->>'users')::int using errcode = '22023';
    end if;
    mods := '[]'::jsonb; addon := 0;
    if jsonb_typeof(x->'modules') = 'array' then
      for mod in select distinct jsonb_array_elements_text(x->'modules') loop
        if incl is null or incl ? mod then continue; end if;         -- already in the plan
        if not (cfg.module_prices ? mod) then raise exception 'the section "%" cannot be added on its own — choose a plan that includes it', mod; end if;
        mods := mods || to_jsonb(mod);
        addon := addon + coalesce((cfg.module_prices->>mod)::numeric, 0);
      end loop;
    end if;
    want_ai := coalesce((x->>'ai')::boolean, false) and cfg.ai_mode = 'plan' and not coalesce((plan->>'ai')::boolean, false);
    if want_ai and not cfg.ai_addon then raise exception 'the AI assistant is not sold separately — choose a plan that includes it'; end if;
    if want_ai then addon := addon + cfg.ai_price; end if;
    want_sup := coalesce((x->>'support')::boolean, false) and cfg.support_mode = 'plan' and not coalesce((plan->>'support')::boolean, true);
    if want_sup and not cfg.support_addon then raise exception 'online support is not sold separately — choose a plan that includes it'; end if;
    if want_sup then addon := addon + cfg.support_price; end if;
    base := ((plan->>'price')::numeric + cfg.extra_user * extra + addon) * per;
    rows := rows || jsonb_build_object('company_id', cid, 'name', nm, 'extra_users', extra, 'modules', mods, 'ai', want_ai, 'support', want_sup,
                                       'addons', round(addon * per, 2), 'base', round(base, 2));
  end loop;
  for r in select v from jsonb_array_elements(rows) v order by (v->>'base')::numeric desc, v->>'name' loop
    pct := case when i = 0 then 100 else cfg.extra_company_pct end;
    select coalesce(discount_pct, 0) into disc from company_billing where company_id = (r->>'company_id')::uuid;
    disc := coalesce(disc, 0);
    amt := round((r->>'base')::numeric * pct / 100 * (100 - disc) / 100, 2);
    out := out || (r || jsonb_build_object('pct', pct, 'discount', disc, 'amount', amt));
    total := total + amt;
    i := i + 1;
  end loop;
  return jsonb_build_object('plan', p_plan, 'plan_name', plan->>'name', 'months', m, 'lines', out, 'amount', total, 'currency', cfg.currency);
end $$;

create or replace function public.billing_quote(p_company uuid, p_plan text, p_months int, p_extra_users int) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare m members; members_n int; plan jsonb;
begin
  m := public.beyond_member(p_company);
  if m.email is null or not m.is_admin then raise exception 'only administrators of this company can pay for it' using errcode = '42501'; end if;
  plan := public.beyond_plan(p_plan);
  if plan is null then raise exception 'unknown plan %', p_plan; end if;
  if coalesce((plan->>'contact')::boolean, false) then
    raise exception 'contact_us: the % plan is arranged with us directly — send a request from Settings → Subscription', plan->>'name' using errcode = 'P0402';
  end if;
  select count(*) into members_n from members where company_id = p_company and active and not pending;
  if members_n > (plan->>'users')::int + coalesce(p_extra_users, 0) then
    raise exception 'this company has % active people — choose a bigger plan or add % extra user(s)', members_n,
      members_n - (plan->>'users')::int using errcode = '22023';
  end if;
  return jsonb_build_object('plan', p_plan, 'months', coalesce(p_months, 1), 'extra_users', coalesce(p_extra_users, 0),
    'amount', public.beyond_price(p_plan, p_months, p_extra_users),
    'currency', (select currency from billing_config where id = 1), 'email', public.beyond_caller());
end $$;

-- plans: a "contact us" plan may leave its users to each deal; what a plan switches off; starter sections by industry
create or replace function public.billing_set_config(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('settings');
  if p ? 'plans' then
    if jsonb_typeof(p->'plans') <> 'array' or jsonb_array_length(p->'plans') = 0 then raise exception 'add at least one plan'; end if;
    if exists (select 1 from jsonb_array_elements(p->'plans') x
               where coalesce(x->>'code', '') = '' or coalesce((x->>'price')::numeric, 0) < 0
                  or (not coalesce((x->>'contact')::boolean, false) and coalesce((x->>'users')::int, 0) < 1)
                  or (x ? 'users' and jsonb_typeof(x->'users') = 'number' and (x->>'users')::int < 1)) then
      raise exception 'every plan needs a code, a price and at least one user (a "contact us" plan may leave users to each deal)';
    end if;
    if (select count(distinct x->>'code') from jsonb_array_elements(p->'plans') x) <> jsonb_array_length(p->'plans') then
      raise exception 'two plans have the same code';
    end if;
    if exists (select 1 from jsonb_array_elements(p->'plans') x
               where x ? 'modules' and x->>'modules' <> 'all' and jsonb_typeof(x->'modules') <> 'array') then
      raise exception 'a plan''s sections must be "all" or a list';
    end if;
    if exists (select 1 from jsonb_array_elements(p->'plans') x where x ? 'deny' and jsonb_typeof(x->'deny') not in ('array', 'null')) then
      raise exception 'what a plan switches off must be a list';
    end if;
  end if;
  if p ? 'module_prices' and (jsonb_typeof(p->'module_prices') <> 'object'
      or exists (select 1 from jsonb_each(p->'module_prices') e where jsonb_typeof(e.value) <> 'number' or (e.value #>> '{}')::numeric < 0)) then
    raise exception 'section prices must be 0 or more';
  end if;
  if p ? 'core_by_industry' and (jsonb_typeof(p->'core_by_industry') <> 'object'
      or exists (select 1 from jsonb_each(p->'core_by_industry') e where jsonb_typeof(e.value) <> 'object' or jsonb_typeof(e.value->'modules') <> 'array')) then
    raise exception 'each industry needs its list of starter sections';
  end if;
  if p ? 'ai_mode' and p->>'ai_mode' not in ('off', 'free', 'plan') then raise exception 'unknown assistant mode'; end if;
  if p ? 'support_mode' and p->>'support_mode' not in ('free', 'plan') then raise exception 'unknown support mode'; end if;
  if p ? 'visits' and (jsonb_typeof(p->'visits') <> 'object' or coalesce((p->'visits'->>'price')::numeric, 0) < 0) then
    raise exception 'the visit price must be 0 or more';
  end if;
  if (p ? 'enabled' or p ? 'maintenance' or p ? 'accept_new') and not public.beyond_staff_can('approve') then
    raise exception 'switching billing, maintenance or new customers needs the approve permission' using errcode = '42501';
  end if;
  update billing_config set
    enabled       = coalesce((p->>'enabled')::boolean, enabled),
    enabled_at    = case when coalesce((p->>'enabled')::boolean, enabled) and not enabled then now() else enabled_at end,
    trial_days    = coalesce((p->>'trial_days')::int, trial_days),
    grace_days    = coalesce((p->>'grace_days')::int, grace_days),
    currency      = coalesce(nullif(p->>'currency', ''), currency),
    trial_plan    = coalesce(nullif(p->>'trial_plan', ''), trial_plan),
    yearly_months = coalesce((p->>'yearly_months')::int, yearly_months),
    extra_user    = coalesce((p->>'extra_user')::numeric, extra_user),
    extra_company_pct = coalesce((p->>'extra_company_pct')::numeric, extra_company_pct),
    support       = case when jsonb_typeof(p->'support') = 'object' then p->'support' else support end,
    plans         = coalesce(p->'plans', plans),
    payment_note  = coalesce(p->>'payment_note', payment_note),
    ai_mode       = coalesce(nullif(p->>'ai_mode', ''), ai_mode),
    ai_addon      = coalesce((p->>'ai_addon')::boolean, ai_addon),
    ai_price      = coalesce((p->>'ai_price')::numeric, ai_price),
    module_prices = case when jsonb_typeof(p->'module_prices') = 'object' then p->'module_prices' else module_prices end,
    core_modules  = case when jsonb_typeof(p->'core_modules') = 'array' then p->'core_modules' else core_modules end,
    core_by_industry = case when jsonb_typeof(p->'core_by_industry') = 'object' then p->'core_by_industry' else core_by_industry end,
    trial_full    = coalesce((p->>'trial_full')::boolean, trial_full),
    max_companies = coalesce((p->>'max_companies')::int, max_companies),
    accept_new    = coalesce((p->>'accept_new')::boolean, accept_new),
    maintenance   = case when jsonb_typeof(p->'maintenance') = 'object' then p->'maintenance' else maintenance end,
    page          = case when jsonb_typeof(p->'page') = 'object' then p->'page' else page end,
    notify        = case when jsonb_typeof(p->'notify') = 'object' then notify || p->'notify' else notify end,
    support_mode  = coalesce(nullif(p->>'support_mode', ''), support_mode),
    support_addon = coalesce((p->>'support_addon')::boolean, support_addon),
    support_price = coalesce((p->>'support_price')::numeric, support_price),
    visits        = case when jsonb_typeof(p->'visits') = 'object' then visits || p->'visits' else visits end,
    updated_at = now(), updated_by = public.beyond_caller()
  where id = 1;
  perform public.beyond_log('settings.save', null, p - 'plans' - 'page' || case when p ? 'plans' then '{"plans": "changed"}'::jsonb else '{}'::jsonb end
                                                       || case when p ? 'page' then '{"page": "changed"}'::jsonb else '{}'::jsonb end);
  return (select to_jsonb(c) from billing_config c where id = 1);
end $$;

-- per company (as before), plus: what is switched off or given back (deny / allow), and its industry
create or replace function public.billing_set_company(p_company uuid, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare days int; st jsonb; act text := coalesce(p->>'trial', ''); ov jsonb;
begin
  perform public.beyond_require_staff('approve');
  if not exists (select 1 from companies where id = p_company) then raise exception 'company not found'; end if;
  if act not in ('', 'restart', 'extend', 'stop') then raise exception 'unknown trial action'; end if;
  if p ? 'industry' and coalesce(p->>'industry', '') <> '' and p->>'industry' !~ '^[a-z_]{1,40}$' then raise exception 'unknown industry'; end if;
  days := coalesce(nullif(p->>'days', '')::int, (select trial_days from billing_config where id = 1), 15);
  if act in ('restart', 'extend') and (days < 1 or days > 3650) then raise exception 'days must be 1 to 3650'; end if;
  if p ? 'discount_pct' and coalesce(nullif(p->>'discount_pct', '')::numeric, 0) not between 0 and 100 then
    raise exception 'the discount must be 0 to 100 %%';
  end if;
  if p ? 'overrides' then
    ov := p->'overrides';
    if jsonb_typeof(ov) <> 'object' then raise exception 'overrides must be an object'; end if;
    if ov ? 'modules' and ov->>'modules' <> 'all' and jsonb_typeof(ov->'modules') not in ('array', 'null') then raise exception 'sections must be "all" or a list'; end if;
    if ov ? 'ai' and jsonb_typeof(ov->'ai') not in ('boolean', 'null') then raise exception 'the assistant override is on, off or as the plan'; end if;
    if ov ? 'support' and jsonb_typeof(ov->'support') not in ('boolean', 'null') then raise exception 'the support override is on, off or as the plan'; end if;
    if ov ? 'users' and jsonb_typeof(ov->'users') = 'number' and (ov->>'users')::int not between 1 and 100000 then raise exception 'users must be 1 or more'; end if;
    if (ov ? 'deny' and jsonb_typeof(ov->'deny') not in ('array', 'null')) or (ov ? 'allow' and jsonb_typeof(ov->'allow') not in ('array', 'null')) then
      raise exception 'switched off / given back must be lists';
    end if;
    if ov ? 'deny' and jsonb_typeof(ov->'deny') = 'array' then ov := jsonb_set(ov, '{deny}', public.beyond_keys(ov->'deny')); end if;
    if ov ? 'allow' and jsonb_typeof(ov->'allow') = 'array' then ov := jsonb_set(ov, '{allow}', public.beyond_keys(ov->'allow')); end if;
    ov := jsonb_strip_nulls(ov);
  end if;
  insert into company_billing(company_id) values (p_company) on conflict (company_id) do nothing;
  st := public.beyond_billing_state(p_company);
  if act = 'extend' and st->>'billing_status' in ('active', 'past_due') then
    update company_billing set paid_until = paid_until + make_interval(days => days), updated_at = now() where company_id = p_company;
    act := 'extend_paid';
  end if;
  update company_billing set
    comped      = coalesce((p->>'comped')::boolean, comped),
    trial_ends  = case act when 'restart' then now() + make_interval(days => days)
                           when 'extend' then greatest(now(), coalesce((st->>'trial_ends')::timestamptz, now())) + make_interval(days => days)
                           when 'stop' then now()
                           else case when p ? 'trial_ends' then nullif(p->>'trial_ends', '')::timestamptz else trial_ends end end,
    paid_until  = case when p ? 'paid_until' then nullif(p->>'paid_until', '')::timestamptz else paid_until end,
    plan        = coalesce(nullif(p->>'plan', ''), plan),
    extra_users = coalesce((p->>'extra_users')::int, extra_users),
    cancel_note = case when coalesce((p->>'clear_cancel')::boolean, false) then null else cancel_note end,
    discount_pct = case when p ? 'discount_pct' then coalesce(nullif(p->>'discount_pct', '')::numeric, 0) else discount_pct end,
    overrides   = coalesce(ov, overrides),
    suspended   = coalesce((p->>'suspended')::boolean, suspended),
    industry    = case when p ? 'industry' then nullif(p->>'industry', '') else industry end,
    suspend_note = case when p ? 'suspend_note' then nullif(trim(p->>'suspend_note'), '') else suspend_note end,
    updated_at = now()
  where company_id = p_company;
  perform public.beyond_log(case act when 'restart' then 'company.trial_restart' when 'extend' then 'company.trial_extend'
                                     when 'extend_paid' then 'company.paid_extend' when 'stop' then 'company.trial_stop'
                                     else case when p ? 'suspended' then case when (p->>'suspended')::boolean then 'company.suspend' else 'company.unsuspend' end
                                               when p ? 'overrides' then 'company.overrides' else 'company.set' end end,
                            p_company::text, p);
  return public.beyond_billing_state(p_company);
end $$;

-- the console's arrangement (menu names, order, hidden items, columns), shared by the team
create or replace function public.console_setting_save(p_key text, p_value jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('settings');
  if p_key not in ('console_look', 'console_layout') then raise exception 'unknown setting'; end if;
  if length(p_value::text) > 400000 then raise exception 'this setting is too large (keep pictures under 250 KB)'; end if;
  insert into platform_settings(key, value, updated_by, updated_at) values (p_key, p_value, public.beyond_caller(), now())
  on conflict (key) do update set value = excluded.value, updated_by = excluded.updated_by, updated_at = now();
  perform public.beyond_log('setting.save', p_key, null);
  return p_value;
end $$;

create or replace function public.billing_public() returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object('enabled', enabled, 'trial_days', trial_days, 'currency', currency,
    'plans', (select coalesce(jsonb_agg(p order by o), '[]'::jsonb) from jsonb_array_elements(plans) with ordinality x(p, o)
              where not coalesce((p->>'hidden')::boolean, false)),
    'extra_user', extra_user, 'yearly_months', yearly_months, 'payment_note', payment_note,
    'extra_company_pct', extra_company_pct, 'support', support, 'trial_full', trial_full,
    'ai_mode', ai_mode, 'ai_addon', ai_addon, 'ai_price', ai_price, 'module_prices', module_prices, 'core_modules', core_modules, 'core_by_industry', core_by_industry,
    'accept_new', accept_new, 'max_companies', max_companies, 'page', page,
    'support_mode', support_mode, 'support_addon', support_addon, 'support_price', support_price,
    'visits', visits,
    'maintenance', case when coalesce((maintenance->>'on')::boolean, false) then maintenance else '{"on": false}'::jsonb end,
    'methods', (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'kind', kind, 'name', name, 'name_ar', name_ar, 'details', details,
                  'instructions', instructions, 'instructions_ar', instructions_ar, 'needs_proof', needs_proof) order by sort, id), '[]'::jsonb)
                from billing_methods where active),
    'notices', (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'title', title, 'title_ar', title_ar, 'body', body, 'body_ar', body_ar,
                  'level', level, 'audience', audience, 'starts_at', starts_at, 'ends_at', ends_at) order by starts_at desc), '[]'::jsonb)
                from platform_notices where active and starts_at <= now() and (ends_at is null or ends_at > now())))
  from billing_config where id = 1
$$;

-- an Enterprise request is always welcome (like questions about the subscription)
create or replace function public.support_open(p_company uuid, p_kind text, p_subject text, p_body text, p_diag jsonb,
  p_log jsonb, p_files jsonb, p_app_user text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare tid bigint; nm text;
begin
  if public.beyond_caller() = '' then raise exception 'sign in first' using errcode = '42501'; end if;
  if p_company is not null and not public.beyond_in(p_company) then raise exception 'not a member of this company' using errcode = '42501'; end if;
  if coalesce(trim(p_subject), '') = '' then raise exception 'write a short summary'; end if;
  if p_company is not null and coalesce(p_kind, '') not in ('billing', 'enterprise')
     and not coalesce((public.beyond_billing_state(p_company)->>'support')::boolean, true) then
    raise exception 'support_not_included: online support is not included in this company''s plan — an administrator can add it in Subscription. Questions about the subscription itself are always welcome (choose "Subscription and billing").'
      using errcode = 'P0402';
  end if;
  if (select count(*) from support_tickets where lower(email) = public.beyond_caller() and created_at > now() - interval '1 hour') >= 20 then
    raise exception 'too many reports in one hour — add to an open one instead';
  end if;
  select name into nm from companies where id = p_company;
  insert into support_tickets(company_id, company_name, email, app_user, kind, subject, body, diag, log, files)
  values (p_company, nm, public.beyond_caller(), left(coalesce(p_app_user, ''), 120), left(coalesce(p_kind, 'issue'), 30),
          left(trim(p_subject), 200), left(coalesce(p_body, ''), 20000),
          case when jsonb_typeof(p_diag) = 'object' and length(p_diag::text) < 20000 then p_diag else '{}' end,
          case when jsonb_typeof(p_log) = 'array' and length(p_log::text) < 200000 then p_log else '[]' end,
          public.beyond_clean_files(p_files, public.beyond_caller() || '/'))
  returning id into tid;
  update support_tickets set ref = 'BE-' || lpad(tid::text, 5, '0') where id = tid;
  return public.support_get(tid);
end $$;

-- the console's arrangement comes with who is signed in
create or replace function public.console_me() returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare s platform_admins;
begin
  s := public.beyond_staff();
  if s.email is null then raise exception 'not a member of the platform team' using errcode = '42501'; end if;
  return jsonb_build_object('email', s.email, 'name', coalesce(s.name, split_part(s.email, '@', 1)), 'full', s.perms is null,
    'perms', coalesce(s.perms, '{}'::jsonb), 'notify', coalesce(s.notify, '{}'::jsonb),
    'look', (select value from platform_settings where key = 'console_look'),
    'layout', (select value from platform_settings where key = 'console_layout'),
    'billing_enabled', (select enabled from billing_config where id = 1),
    'badges', jsonb_build_object(
      'orders', case when public.beyond_staff_can('billing') then (select count(*) from billing_orders where status = 'submitted') end,
      'tickets', case when public.beyond_staff_can('support') then (select count(*) from support_tickets where staff_unread and status not in ('closed')) end,
      'visits', case when public.beyond_staff_can('support') then (select count(*) from support_visits where staff_unread and status not in ('done', 'cancelled', 'rejected')) end));
end $$;

-- ================================================================== asking for the Enterprise plan
-- p: {users, companies, sections, phone, note}. Becomes a request in the console's support inbox (kind "enterprise").
create or replace function public.enterprise_request(p_company uuid, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare body text;
begin
  if jsonb_typeof(p) <> 'object' then raise exception 'say what you need'; end if;
  body := concat_ws(E'\n',
    'Users: ' || nullif(trim(p->>'users'), ''),
    'Companies: ' || nullif(trim(p->>'companies'), ''),
    'Sections: ' || nullif(trim(p->>'sections'), ''),
    'Phone: ' || nullif(trim(p->>'phone'), ''),
    nullif(trim(coalesce(p->>'note', '')), ''));
  return public.support_open(p_company, 'enterprise', 'Enterprise plan request', left(body, 5000),
                             jsonb_build_object('enterprise', p), '[]'::jsonb, '[]'::jsonb, coalesce(p->>'app_user', ''));
end $$;

revoke all on function public.beyond_entitle(public.billing_config, jsonb, text, public.company_billing) from public, anon, authenticated;
grant execute on function public.enterprise_request(uuid, jsonb) to authenticated;
