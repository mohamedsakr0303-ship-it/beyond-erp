-- Owner control: what each plan includes, what is sold on top, and switches over every customer.
--
--  * Plans (billing_config.plans, edited in the owner console) may carry, besides code / name / price / users / features:
--      name_ar, tagline, tagline_ar, features_ar, badge, badge_ar, color, highlight, hidden (not offered to customers,
--      still assignable by staff), modules ("all" or a list of menu sections) and ai (the AI assistant included).
--  * Add-ons, per company and month: a menu section (module_prices: {section: price}), the AI assistant (ai_price when
--    ai_addon is on) and extra users (extra_user, as before). Sections in core_modules are always included.
--  * AI assistant availability: ai_mode = 'off' (nobody), 'free' (everyone), 'plan' (the plan's ai flag or the add-on).
--  * trial_full: a company on its free trial sees every section and the assistant (default on).
--  * Per company: overrides {modules: "all" | [...], ai: true | false, users: n} beat the plan; suspended (with a note)
--    makes it read only whatever its subscription.
--  * Per person (platform_people): blocked (no access to the workspace at all), max_companies (beats the setting).
--  * Settings: max_companies (per person, 0 = no limit), accept_new (new customers may create a first company),
--    maintenance {on, message, message_ar} (read only for everyone but the platform team), page (texts of the
--    customers' Subscription page), notify (when the console warns).
--  * The console's notifications: computed from the data (tickets, payments to verify, trials and paid periods ending,
--    people who stopped paying, cancellations, new companies…), with read / dismissed per team member and the kinds
--    each one wants.
--  * platform_settings: the console's own look (shared by the team).
--  * Looking after customers' data: archive a company (read only, out of the default lists, reversible), download a
--    full backup (its books, people, billing), restore a backup, and — full-access owners only, for an archived company
--    with a backup taken in the last 24 hours, typing its name — delete it for good. Payments stay in the platform's
--    records after a company is deleted (with the company's name), so revenue figures do not change.
--  * Online support (tickets) for each customer: support_mode 'free' (everyone) or 'plan' (the plan's support flag, the
--    support add-on at support_price, or the company's override). Questions about the subscription are always open.
--  * Support visits: a customer asks for someone to come (for what cannot be fixed online); the team quotes a price and
--    proposes a date, the customer accepts and pays (with a picture of the transfer, or after the visit if allowed),
--    the team schedules it and marks it done.
--  * Remote account care: rename a company, hand it to another owner, change a person's role, deactivate or remove them.

-- ================================================================== settings and columns
alter table public.billing_config add column if not exists ai_mode       text not null default 'plan';
alter table public.billing_config add column if not exists ai_addon      boolean not null default false;
alter table public.billing_config add column if not exists ai_price      numeric not null default 0;
alter table public.billing_config add column if not exists module_prices jsonb not null default '{}'::jsonb;
alter table public.billing_config add column if not exists core_modules  jsonb not null
  default '["dashboard", "chat", "team", "settings", "users", "import_export", "accounting", "reports", "tax"]'::jsonb;
alter table public.billing_config add column if not exists trial_full    boolean not null default true;
alter table public.billing_config add column if not exists max_companies int not null default 0;
alter table public.billing_config add column if not exists accept_new    boolean not null default true;
alter table public.billing_config add column if not exists maintenance   jsonb not null default '{"on": false}'::jsonb;
alter table public.billing_config add column if not exists page          jsonb not null default '{}'::jsonb;
alter table public.billing_config add column if not exists notify        jsonb not null default '{"trial_days": 3, "renewal_days": 7}'::jsonb;
alter table public.billing_config add column if not exists support_mode  text not null default 'free';
alter table public.billing_config add column if not exists support_addon boolean not null default false;
alter table public.billing_config add column if not exists support_price numeric not null default 0;
alter table public.billing_config add column if not exists visits        jsonb not null
  default '{"on": true, "price": 0, "pay_when": "before", "terms": "", "terms_ar": ""}'::jsonb;
do $$ begin
  alter table public.billing_config add constraint billing_config_support_mode check (support_mode in ('free', 'plan'));
exception when duplicate_object then null; end $$;
do $$ begin
  alter table public.billing_config add constraint billing_config_ai_mode check (ai_mode in ('off', 'free', 'plan'));
exception when duplicate_object then null; end $$;

alter table public.company_billing add column if not exists addons       jsonb not null default '{}'::jsonb;   -- {modules: [], ai: bool} as paid
alter table public.company_billing add column if not exists overrides    jsonb not null default '{}'::jsonb;   -- {modules, ai, users}
alter table public.company_billing add column if not exists suspended    boolean not null default false;
alter table public.company_billing add column if not exists suspend_note text;
alter table public.billing_payments add column if not exists addons      jsonb not null default '{}'::jsonb;
alter table public.company_billing add column if not exists archived     boolean not null default false;
alter table public.company_billing add column if not exists archived_at  timestamptz;

-- payments outlive a deleted company: they keep its name, and the link is cleared instead of the row being removed
alter table public.billing_payments add column if not exists company_name text;
update public.billing_payments p set company_name = c.name from public.companies c where c.id = p.company_id and p.company_name is null;
alter table public.billing_payments alter column company_id drop not null;
do $$ declare k text; begin
  for k in select conname from pg_constraint where conrelid = 'public.billing_payments'::regclass and contype = 'f'
             and confrelid = 'public.companies'::regclass loop
    execute format('alter table public.billing_payments drop constraint %I', k);
  end loop;
  alter table public.billing_payments add constraint billing_payments_company_id_fkey
    foreign key (company_id) references public.companies(id) on delete set null;
end $$;

-- what was deleted, by whom, and when (the backup itself is the file the owner downloaded)
create table if not exists public.platform_deleted (
  company_id uuid primary key,
  name       text not null,
  created_by text,
  summary    jsonb,
  deleted_by text not null,
  deleted_at timestamptz not null default now()
);
alter table public.platform_deleted enable row level security;
revoke all on public.platform_deleted from anon, authenticated;

create table if not exists public.platform_people (
  email         text primary key,               -- lower case
  blocked       boolean not null default false,
  max_companies int,                            -- null: the setting
  note          text,
  updated_by    text,
  updated_at    timestamptz not null default now()
);
alter table public.platform_people enable row level security;
revoke all on public.platform_people from anon, authenticated;

create table if not exists public.platform_settings (
  key        text primary key,
  value      jsonb not null,
  updated_by text,
  updated_at timestamptz not null default now()
);
alter table public.platform_settings enable row level security;
revoke all on public.platform_settings from anon, authenticated;

alter table public.platform_admins add column if not exists notify jsonb not null default '{}'::jsonb;   -- {kind: false} mutes it

create table if not exists public.platform_alert_state (
  email text not null,
  key   text not null,
  state text not null check (state in ('read', 'dismissed')),
  at    timestamptz not null default now(),
  primary key (email, key)
);
alter table public.platform_alert_state enable row level security;
revoke all on public.platform_alert_state from anon, authenticated;

-- ================================================================== who is calling: a blocked person is nobody
create or replace function public.beyond_caller() returns text
language sql stable security definer set search_path = public, auth as $$
  select case
    when (coalesce(auth.jwt() ->> 'aal', 'aal1') = 'aal2'
          or not exists (select 1 from auth.mfa_factors f where f.user_id = auth.uid() and f.status = 'verified'))
     and not exists (select 1 from public.platform_people pp where pp.email = lower(coalesce(auth.jwt() ->> 'email', '')) and pp.blocked)
    then lower(coalesce(auth.jwt() ->> 'email', ''))
    else '' end
$$;

-- ================================================================== what a company may use
-- modules: null = every section; otherwise the sections it may open. ai: the assistant is available.
create or replace function public.beyond_entitle(cfg public.billing_config, plan jsonb, status text, b public.company_billing) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare full_access boolean; mods jsonb; ai boolean; sup boolean; o jsonb := coalesce(b.overrides, '{}'::jsonb); a jsonb := coalesce(b.addons, '{}'::jsonb);
begin
  full_access := cfg.id is null or not cfg.enabled or status = 'comped' or (status = 'trial' and cfg.trial_full);
  if full_access or o->>'modules' = 'all' or coalesce(plan->>'modules', 'all') = 'all' or jsonb_typeof(plan->'modules') <> 'array' then
    mods := null;
  else
    select coalesce(jsonb_agg(distinct m), '[]'::jsonb) into mods
      from (select jsonb_array_elements_text(coalesce(cfg.core_modules, '[]'::jsonb)) m
            union select jsonb_array_elements_text(plan->'modules')
            union select jsonb_array_elements_text(case when jsonb_typeof(a->'modules') = 'array' then a->'modules' else '[]'::jsonb end)
            union select jsonb_array_elements_text(case when jsonb_typeof(o->'modules') = 'array' then o->'modules' else '[]'::jsonb end)) x;
  end if;
  ai := case when cfg.id is not null and cfg.ai_mode = 'off' then false
             when o ? 'ai' and jsonb_typeof(o->'ai') = 'boolean' then (o->>'ai')::boolean
             when cfg.id is null or cfg.ai_mode = 'free' or full_access then true
             else coalesce((plan->>'ai')::boolean, false) or coalesce((a->>'ai')::boolean, false) end;
  sup := case when o ? 'support' and jsonb_typeof(o->'support') = 'boolean' then (o->>'support')::boolean
              when cfg.id is null or cfg.support_mode = 'free' or full_access then true
              else coalesce((plan->>'support')::boolean, true) or coalesce((a->>'support')::boolean, false) end;
  return jsonb_build_object('modules', mods, 'ai', ai, 'support', sup);
end $$;

-- Where a company stands (as before), plus: suspended, the users override, and what it may use.
create or replace function public.beyond_billing_state(p_company uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare cfg billing_config; co companies; b company_billing; trial_end timestamptz; status text; ends timestamptz;
        plan jsonb; users int; members_n int; first_one boolean; ent jsonb;
begin
  select * into cfg from billing_config where id = 1;
  select * into b from company_billing where company_id = p_company;
  select count(*) into members_n from members where company_id = p_company and active and not pending;
  if cfg.id is null or not cfg.enabled then
    -- billing off: free for everyone; how many people it has is still shown (no limit applies)
    ent := public.beyond_entitle(cfg, null, 'free', b) || jsonb_build_object('users', members_n, 'users_allowed', null,
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
  users := coalesce((plan->>'users')::int, 1) + case when status in ('trial', 'unpaid') then 0 else coalesce(b.extra_users, 0) end;
  if jsonb_typeof(b.overrides->'users') = 'number' then users := (b.overrides->>'users')::int; end if;
  ent := public.beyond_entitle(cfg, plan, status, b);
  return jsonb_build_object(
    'enabled', true, 'status', case when coalesce(b.archived, false) then 'archived' when coalesce(b.suspended, false) then 'suspended' else status end,
    'billing_status', status,
    'can_write', status not in ('expired', 'unpaid') and not coalesce(b.suspended, false) and not coalesce(b.archived, false),
    'suspend_note', case when coalesce(b.suspended, false) then b.suspend_note end,
    'ends', ends, 'days_left', case when ends is null then null else greatest(0, ceil(extract(epoch from ends - now()) / 86400))::int end,
    'trial_ends', trial_end, 'paid_until', b.paid_until, 'plan', plan->>'code', 'plan_name', plan->>'name',
    'users_allowed', case when status = 'comped' and jsonb_typeof(b.overrides->'users') <> 'number' then null else users end, 'users', members_n,
    'extra_users', coalesce(b.extra_users, 0), 'cancel_note', b.cancel_note,
    'addons', coalesce(b.addons, '{}'::jsonb), 'discount_pct', coalesce(b.discount_pct, 0),
    'currency', cfg.currency, 'grace_days', cfg.grace_days) || ent;
end $$;

-- Changes are refused while the company is suspended, while maintenance is on (except for the platform team), and as
-- before when it is not in a paid period.
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

-- ================================================================== new companies: limits per person, new customers
create or replace function public.beyond_company_rules() returns trigger
language plpgsql security definer set search_path = public as $$
declare cfg billing_config; who text := lower(coalesce(new.created_by, '')); have int; lim int;
begin
  if who = '' or exists (select 1 from platform_admins where lower(email) = who and active) then return new; end if;
  select * into cfg from billing_config where id = 1;
  select count(*) into have from companies where lower(coalesce(created_by, '')) = who;
  if have = 0 and cfg.id is not null and not cfg.accept_new then
    raise exception 'BeyondERP is not taking new customers at the moment — please contact us' using errcode = 'P0402';
  end if;
  select coalesce(pp.max_companies, cfg.max_companies, 0) into lim from (select 1) one left join platform_people pp on pp.email = who;
  lim := coalesce(lim, cfg.max_companies, 0);
  if lim > 0 and have >= lim then
    raise exception 'You can have up to % compan% — contact us to add more', lim, case when lim = 1 then 'y' else 'ies' end using errcode = 'P0402';
  end if;
  return new;
end $$;
drop trigger if exists beyond_company_rules on public.companies;
create trigger beyond_company_rules before insert on public.companies for each row execute function public.beyond_company_rules();

-- ================================================================== prices with add-ons
-- p_lines: [{company_id, extra_users, modules: [section…], ai: bool}]. Each company: (plan + its extra users + the
-- sections and assistant added to it) × months; the most expensive line counts 100 %, every other extra_company_pct %,
-- then that company's standing discount.
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
revoke all on function public.beyond_order_price(text, int, jsonb) from public, anon, authenticated;

-- a paid period carries the add-ons paid for with it
create or replace function public.beyond_apply_payment(p_payment bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare p billing_payments; b company_billing; start_at timestamptz;
begin
  select * into p from billing_payments where id = p_payment for update;
  if p.id is null then raise exception 'payment not found'; end if;
  if p.status = 'paid' then return public.beyond_billing_state(p.company_id); end if;
  insert into company_billing(company_id) values (p.company_id) on conflict (company_id) do nothing;
  select * into b from company_billing where company_id = p.company_id for update;
  start_at := greatest(now(), coalesce(b.paid_until, now()),
                       coalesce((public.beyond_billing_state(p.company_id)->>'trial_ends')::timestamptz, now()));
  update company_billing set plan = p.plan, extra_users = p.extra_users, addons = coalesce(p.addons, '{}'::jsonb),
         cancel_note = null, cancel_at = null, paid_until = start_at + make_interval(months => p.months), updated_at = now()
   where company_id = p.company_id;
  update billing_payments set status = 'paid', paid_at = now(), period_start = start_at,
         period_end = start_at + make_interval(months => p.months) where id = p.id;
  return public.beyond_billing_state(p.company_id);
end $$;

create or replace function public.beyond_order_approve(p_order bigint, p_note text, p_txn text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare o billing_orders; x jsonb; i int := 0; pid bigint;
begin
  select * into o from billing_orders where id = p_order for update;
  if o.id is null then raise exception 'order not found'; end if;
  if o.status = 'approved' then return public.beyond_order_row(p_order, true); end if;
  if o.status = 'cancelled' then raise exception 'this order was cancelled'; end if;
  for x in select v from jsonb_array_elements(o.lines) v loop
    i := i + 1;
    insert into billing_payments(company_id, company_name, plan, months, extra_users, amount, currency, provider, reference, provider_txn, note, created_by, order_id, addons)
    values ((x->>'company_id')::uuid, x->>'name', o.plan, o.months, coalesce((x->>'extra_users')::int, 0), (x->>'amount')::numeric, o.currency,
            case when o.method_kind = 'card' then 'paymob' else 'manual' end, o.ref || '-' || i, p_txn,
            left(coalesce(o.method_name, '') || coalesce(' · ' || o.proof_ref, ''), 300), o.email, o.id,
            jsonb_build_object('modules', coalesce(x->'modules', '[]'::jsonb), 'ai', coalesce((x->>'ai')::boolean, false),
                               'support', coalesce((x->>'support')::boolean, false)))
    returning id into pid;
    perform public.beyond_apply_payment(pid);
  end loop;
  update billing_orders set status = 'approved', review_note = nullif(trim(coalesce(p_note, '')), ''), reviewed_by = nullif(public.beyond_caller(), ''),
         reviewed_at = now(), updated_at = now()
   where id = p_order;
  return public.beyond_order_row(p_order, true);
end $$;

-- Record a payment received outside the app (cash, a transfer straight to you): any months, any amount, add-ons.
create or replace function public.console_record_payment(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare pid bigint; cid uuid := (p->>'company')::uuid; plan text := p->>'plan'; months int := coalesce(nullif(p->>'months', '')::int, 1);
        extra int := coalesce(nullif(p->>'extra_users', '')::int, 0); amt numeric := nullif(p->>'amount', '')::numeric;
begin
  perform public.beyond_require_staff('approve');
  if public.beyond_plan(plan) is null then raise exception 'unknown plan %', plan; end if;
  if months < 1 or months > 120 then raise exception 'months must be 1 to 120'; end if;
  if amt is not null and amt < 0 then raise exception 'the amount must be 0 or more'; end if;
  insert into billing_payments(company_id, company_name, plan, months, extra_users, amount, currency, provider, reference, note, created_by, addons)
  values (cid, (select name from companies where id = cid), plan, months, extra, coalesce(amt, public.beyond_price(plan, months, extra)),
          (select currency from billing_config where id = 1), 'manual', nullif(trim(coalesce(p->>'reference', '')), ''), p->>'note',
          public.beyond_caller(),
          jsonb_build_object('modules', case when jsonb_typeof(p->'modules') = 'array' then p->'modules' else '[]'::jsonb end,
                             'ai', coalesce((p->>'ai')::boolean, false), 'support', coalesce((p->>'support')::boolean, false)))
  returning id into pid;
  perform public.beyond_log('payment.record', cid::text, p);
  return public.beyond_apply_payment(pid);
end $$;

-- ================================================================== settings, per company, per person
create or replace function public.billing_set_config(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('settings');
  if p ? 'plans' then
    if jsonb_typeof(p->'plans') <> 'array' or jsonb_array_length(p->'plans') = 0 then raise exception 'add at least one plan'; end if;
    if exists (select 1 from jsonb_array_elements(p->'plans') x
               where coalesce(x->>'code', '') = '' or (x->>'price')::numeric < 0 or coalesce((x->>'users')::int, 0) < 1) then
      raise exception 'every plan needs a code, a price and at least one user';
    end if;
    if (select count(distinct x->>'code') from jsonb_array_elements(p->'plans') x) <> jsonb_array_length(p->'plans') then
      raise exception 'two plans have the same code';
    end if;
    if exists (select 1 from jsonb_array_elements(p->'plans') x
               where x ? 'modules' and x->>'modules' <> 'all' and jsonb_typeof(x->'modules') <> 'array') then
      raise exception 'a plan''s sections must be "all" or a list';
    end if;
  end if;
  if p ? 'module_prices' and (jsonb_typeof(p->'module_prices') <> 'object'
      or exists (select 1 from jsonb_each(p->'module_prices') e where jsonb_typeof(e.value) <> 'number' or (e.value #>> '{}')::numeric < 0)) then
    raise exception 'section prices must be 0 or more';
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

-- billing_set_company (as before) also takes overrides {modules, ai, users}, suspended and suspend_note.
create or replace function public.billing_set_company(p_company uuid, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare days int; st jsonb; act text := coalesce(p->>'trial', ''); ov jsonb;
begin
  perform public.beyond_require_staff('approve');
  if not exists (select 1 from companies where id = p_company) then raise exception 'company not found'; end if;
  if act not in ('', 'restart', 'extend', 'stop') then raise exception 'unknown trial action'; end if;
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

-- Everyone who uses the workspace, with what they own and use.
create or replace function public.console_people() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('customers');
  return (select coalesce(jsonb_agg(x order by x->>'last' desc nulls last, x->>'email'), '[]'::jsonb) from (
    select jsonb_build_object('email', e.email,
      'owns', (select count(*) from companies c where lower(coalesce(c.created_by, '')) = e.email),
      'member_of', (select count(*) from members m where lower(m.email) = e.email and m.active and not m.pending),
      'companies', (select coalesce(jsonb_agg(jsonb_build_object('id', c.id, 'name', c.name, 'owner', lower(coalesce(c.created_by, '')) = e.email,
                                                                 'admin', m.is_admin) order by c.name), '[]'::jsonb)
                    from members m join companies c on c.id = m.company_id where lower(m.email) = e.email and m.active and not m.pending),
      'last', (select max(o.created_at) from ops o where lower(o.actor) = e.email),
      'blocked', coalesce(pp.blocked, false), 'max_companies', pp.max_companies, 'note', pp.note,
      'staff', exists (select 1 from platform_admins a where lower(a.email) = e.email and a.active)) x
    from (select distinct lower(email) email from members union select email from platform_people) e
    left join platform_people pp on pp.email = e.email) q);
end $$;

create or replace function public.console_person_save(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare e text := lower(trim(coalesce(p->>'email', '')));
begin
  perform public.beyond_require_staff('approve');
  if e = '' then raise exception 'say whose settings these are'; end if;
  if coalesce((p->>'blocked')::boolean, false) and exists (select 1 from platform_admins where lower(email) = e and active) then
    raise exception 'a member of the platform team cannot be blocked — remove them from the team first';
  end if;
  if p ? 'max_companies' and nullif(p->>'max_companies', '') is not null and (p->>'max_companies')::int < 0 then
    raise exception 'the company limit must be 0 or more';
  end if;
  insert into platform_people(email) values (e) on conflict (email) do nothing;
  update platform_people set
    blocked = coalesce((p->>'blocked')::boolean, blocked),
    max_companies = case when p ? 'max_companies' then nullif(p->>'max_companies', '')::int else max_companies end,
    note = case when p ? 'note' then nullif(trim(p->>'note'), '') else note end,
    updated_by = public.beyond_caller(), updated_at = now()
  where email = e;
  perform public.beyond_log(case when (p->>'blocked')::boolean then 'person.block' when p ? 'blocked' then 'person.unblock' else 'person.set' end, e, p);
  return (select to_jsonb(x) from platform_people x where email = e);
end $$;

-- the console's own look and other shared settings of the team
create or replace function public.console_setting_save(p_key text, p_value jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('settings');
  if p_key not in ('console_look') then raise exception 'unknown setting'; end if;
  if length(p_value::text) > 400000 then raise exception 'this setting is too large (keep pictures under 250 KB)'; end if;
  insert into platform_settings(key, value, updated_by, updated_at) values (p_key, p_value, public.beyond_caller(), now())
  on conflict (key) do update set value = excluded.value, updated_by = excluded.updated_by, updated_at = now();
  perform public.beyond_log('setting.save', p_key, null);
  return p_value;
end $$;

-- a team member's own choice of notifications {kind: false}
create or replace function public.console_my_notify(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare s platform_admins;
begin
  s := public.beyond_staff();
  if s.email is null then raise exception 'not a member of the platform team' using errcode = '42501'; end if;
  if jsonb_typeof(p) <> 'object' then raise exception 'choose what to be told about'; end if;
  update platform_admins set notify = p where lower(email) = lower(s.email);
  return p;
end $$;

-- ================================================================== notifications in the console
-- Worked out from the data each time: [{key, kind, area, level, at, title, detail, link, read}].
create or replace function public.console_alerts() returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare s platform_admins; cfg billing_config; out jsonb := '[]'::jsonb; r record; st jsonb; tdays int; rdays int;
        mute jsonb; can_cust boolean; can_bill boolean; can_sup boolean;
begin
  s := public.beyond_staff();
  if s.email is null then raise exception 'not a member of the platform team' using errcode = '42501'; end if;
  select * into cfg from billing_config where id = 1;
  mute := coalesce(s.notify, '{}'::jsonb);
  can_cust := public.beyond_staff_can('customers'); can_bill := public.beyond_staff_can('billing'); can_sup := public.beyond_staff_can('support');
  tdays := coalesce((cfg.notify->>'trial_days')::int, 3); rdays := coalesce((cfg.notify->>'renewal_days')::int, 7);
  if can_sup then
    for r in select * from support_tickets where staff_unread and status <> 'closed' order by updated_at desc limit 100 loop
      out := out || jsonb_build_object('key', 't:' || r.id || ':' || floor(extract(epoch from r.updated_at)), 'kind', 'ticket', 'area', 'support',
        'level', case when r.priority in ('high', 'urgent') then 'warn' else 'info' end, 'at', r.updated_at,
        'title', r.ref || ' — ' || r.subject, 'detail', coalesce(r.company_name, '') || ' · ' || r.email, 'link', 'ticket/' || r.id);
    end loop;
  end if;
  if can_sup then
    for r in select * from support_visits where staff_unread and status not in ('done', 'cancelled', 'rejected') order by updated_at desc limit 100 loop
      out := out || jsonb_build_object('key', 'v:' || r.id || ':' || floor(extract(epoch from r.updated_at)),
        'kind', case when r.pay_status = 'submitted' then 'visit_paid' when r.status = 'accepted' then 'visit_accepted' else 'visit' end,
        'area', 'support', 'level', 'warn', 'at', r.updated_at,
        'title', r.ref || ' — ' || left(r.reason, 80), 'detail', coalesce(r.company_name, '') || ' · ' || r.email, 'link', 'visit/' || r.id);
    end loop;
    for r in select * from support_visits where status = 'scheduled' and scheduled_at between now() and now() + interval '2 days' loop
      out := out || jsonb_build_object('key', 'vs:' || r.id || ':' || floor(extract(epoch from r.scheduled_at)), 'kind', 'visit_soon', 'area', 'support',
        'level', 'info', 'at', r.scheduled_at - interval '2 days', 'title', r.ref || ' — ' || coalesce(r.company_name, ''),
        'detail', coalesce(r.technician, '') || ' · ' || r.address, 'link', 'visit/' || r.id);
    end loop;
  end if;
  if can_bill then
    for r in select * from billing_orders where status = 'submitted' order by submitted_at desc limit 100 loop
      out := out || jsonb_build_object('key', 'o:' || r.id || ':' || floor(extract(epoch from r.submitted_at)), 'kind', 'order', 'area', 'billing',
        'level', 'warn', 'at', r.submitted_at, 'title', r.ref, 'detail', r.email, 'amount', r.amount, 'link', 'payments');
    end loop;
    for r in select * from billing_orders where status = 'open' and created_at < now() - interval '3 days' and created_at > now() - interval '30 days'
             order by created_at desc limit 50 loop
      out := out || jsonb_build_object('key', 'os:' || r.id, 'kind', 'order_stale', 'area', 'billing', 'level', 'info', 'at', r.created_at + interval '3 days',
        'title', r.ref, 'detail', r.email, 'amount', r.amount, 'link', 'payments?s=open');
    end loop;
  end if;
  if can_cust and cfg.id is not null then
    for r in select c.id, c.name, c.created_at, c.created_by, b.cancel_note, b.cancel_at, b.paid_until,
                    exists (select 1 from billing_payments p where p.company_id = c.id and p.status = 'paid' and p.amount > 0) paid_before,
                    exists (select 1 from billing_orders o where o.status in ('open', 'submitted') and o.lines @> jsonb_build_array(jsonb_build_object('company_id', c.id))) ordering,
                    (select count(*) from members m where m.company_id = c.id and m.active and not m.pending) people
             from companies c left join company_billing b on b.company_id = c.id loop
      if r.created_at > now() - interval '7 days' then
        out := out || jsonb_build_object('key', 'nc:' || r.id, 'kind', 'new_company', 'area', 'customers', 'level', 'info', 'at', r.created_at,
          'title', r.name, 'detail', r.created_by, 'link', 'company/' || r.id);
      end if;
      if r.cancel_at is not null and r.cancel_at > now() - interval '30 days' then
        out := out || jsonb_build_object('key', 'cx:' || r.id || ':' || floor(extract(epoch from r.cancel_at)), 'kind', 'cancel', 'area', 'customers', 'level', 'warn',
          'at', r.cancel_at, 'title', r.name, 'detail', r.cancel_note, 'link', 'company/' || r.id);
      end if;
      if not cfg.enabled then continue; end if;
      st := public.beyond_billing_state(r.id);
      if st->>'billing_status' = 'trial' and (st->>'ends')::timestamptz < now() + make_interval(days => tdays) and not r.ordering then
        out := out || jsonb_build_object('key', 'te:' || r.id || ':' || ((st->>'ends')::timestamptz)::date, 'kind', 'trial_ending', 'area', 'customers', 'level', 'warn',
          'at', (st->>'ends')::timestamptz - make_interval(days => tdays), 'title', r.name, 'detail', r.created_by, 'ends', st->'ends', 'link', 'company/' || r.id);
      elsif st->>'billing_status' = 'expired' and not r.paid_before and (st->>'ends')::timestamptz > now() - interval '14 days' then
        out := out || jsonb_build_object('key', 'tx:' || r.id || ':' || ((st->>'ends')::timestamptz)::date, 'kind', 'trial_ended', 'area', 'customers', 'level', 'warn',
          'at', st->'ends', 'title', r.name, 'detail', r.created_by, 'link', 'company/' || r.id);
      elsif st->>'billing_status' = 'past_due' then
        out := out || jsonb_build_object('key', 'pd:' || r.id || ':' || r.paid_until::date, 'kind', 'payment_due', 'area', 'customers', 'level', 'warn',
          'at', r.paid_until, 'title', r.name, 'detail', r.created_by, 'ends', st->'ends', 'link', 'company/' || r.id);
      elsif st->>'billing_status' = 'expired' and r.paid_before and (st->>'ends')::timestamptz > now() - interval '30 days' then
        out := out || jsonb_build_object('key', 'lp:' || r.id || ':' || ((st->>'ends')::timestamptz)::date, 'kind', 'lapsed', 'area', 'customers', 'level', 'critical',
          'at', st->'ends', 'title', r.name, 'detail', r.created_by, 'link', 'company/' || r.id);
      elsif st->>'billing_status' = 'active' and (st->>'ends')::timestamptz < now() + make_interval(days => rdays) and not r.ordering and r.cancel_note is null then
        out := out || jsonb_build_object('key', 'rs:' || r.id || ':' || r.paid_until::date, 'kind', 'renewal_soon', 'area', 'customers', 'level', 'info',
          'at', (st->>'ends')::timestamptz - make_interval(days => rdays), 'title', r.name, 'detail', r.created_by, 'ends', st->'ends', 'link', 'company/' || r.id);
      elsif st->>'billing_status' = 'unpaid' and r.created_at > now() - interval '14 days' and not r.ordering then
        out := out || jsonb_build_object('key', 'up:' || r.id, 'kind', 'unpaid_company', 'area', 'customers', 'level', 'info',
          'at', r.created_at, 'title', r.name, 'detail', r.created_by, 'link', 'company/' || r.id);
      end if;
      if st->>'users_allowed' is not null and r.people > (st->>'users_allowed')::int then
        out := out || jsonb_build_object('key', 'ol:' || r.id || ':' || r.people, 'kind', 'over_limit', 'area', 'customers', 'level', 'warn',
          'at', now(), 'title', r.name, 'detail', r.people || ' / ' || (st->>'users_allowed'), 'link', 'company/' || r.id);
      end if;
    end loop;
  end if;
  return (select coalesce(jsonb_agg(a || jsonb_build_object('read', st2.state = 'read') order by (a->>'at')::timestamptz desc), '[]'::jsonb)
          from jsonb_array_elements(out) a
          left join platform_alert_state st2 on st2.email = lower(s.email) and st2.key = a->>'key'
          where coalesce(st2.state, '') <> 'dismissed' and coalesce((mute->>(a->>'kind'))::boolean, true));
end $$;

create or replace function public.console_alert_mark(p_keys jsonb, p_state text) returns void
language plpgsql security definer set search_path = public as $$
declare s platform_admins;
begin
  s := public.beyond_staff();
  if s.email is null then raise exception 'not a member of the platform team' using errcode = '42501'; end if;
  if p_state = 'unread' then
    delete from platform_alert_state where email = lower(s.email) and key in (select jsonb_array_elements_text(p_keys));
  elsif p_state in ('read', 'dismissed') then
    insert into platform_alert_state(email, key, state) select lower(s.email), k, p_state from jsonb_array_elements_text(p_keys) k
    on conflict (email, key) do update set state = excluded.state, at = now();
  else raise exception 'unknown state';
  end if;
  delete from platform_alert_state where at < now() - interval '120 days';
end $$;

-- the console's start: who I am, my access, the team's look, what waits
create or replace function public.console_me() returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare s platform_admins;
begin
  s := public.beyond_staff();
  if s.email is null then raise exception 'not a member of the platform team' using errcode = '42501'; end if;
  return jsonb_build_object('email', s.email, 'name', coalesce(s.name, split_part(s.email, '@', 1)), 'full', s.perms is null,
    'perms', coalesce(s.perms, '{}'::jsonb), 'notify', coalesce(s.notify, '{}'::jsonb),
    'look', (select value from platform_settings where key = 'console_look'),
    'billing_enabled', (select enabled from billing_config where id = 1),
    'badges', jsonb_build_object(
      'orders', case when public.beyond_staff_can('billing') then (select count(*) from billing_orders where status = 'submitted') end,
      'tickets', case when public.beyond_staff_can('support') then (select count(*) from support_tickets where staff_unread and status not in ('closed')) end,
      'visits', case when public.beyond_staff_can('support') then (select count(*) from support_visits where staff_unread and status not in ('done', 'cancelled', 'rejected')) end));
end $$;

-- what everyone sees (before signing in, too): hidden plans stay out; add-on prices, assistant, page texts, maintenance
create or replace function public.billing_public() returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object('enabled', enabled, 'trial_days', trial_days, 'currency', currency,
    'plans', (select coalesce(jsonb_agg(p order by o), '[]'::jsonb) from jsonb_array_elements(plans) with ordinality x(p, o)
              where not coalesce((p->>'hidden')::boolean, false)),
    'extra_user', extra_user, 'yearly_months', yearly_months, 'payment_note', payment_note,
    'extra_company_pct', extra_company_pct, 'support', support, 'trial_full', trial_full,
    'ai_mode', ai_mode, 'ai_addon', ai_addon, 'ai_price', ai_price, 'module_prices', module_prices, 'core_modules', core_modules,
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

-- For the AI function (service key only): may this person use the assistant in this company?
create or replace function public.beyond_ai_allowed(p_company uuid, p_email text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare st jsonb;
begin
  if exists (select 1 from platform_people where email = lower(coalesce(p_email, '')) and blocked) then
    return jsonb_build_object('ok', false, 'reason', 'This account is blocked — please contact BeyondERP');
  end if;
  st := public.beyond_billing_state(p_company);
  if not coalesce((st->>'ai')::boolean, true) then
    return jsonb_build_object('ok', false, 'reason', case when (select ai_mode from billing_config where id = 1) = 'off'
      then 'The AI assistant is not available at the moment'
      else 'The AI assistant is not included in this company''s plan — an administrator can add it in Subscription' end);
  end if;
  return jsonb_build_object('ok', true);
end $$;

-- ================================================================== looking after a customer's company
create or replace function public.console_company_archive(p_company uuid, p_archived boolean) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('approve');
  if not exists (select 1 from companies where id = p_company) then raise exception 'company not found'; end if;
  insert into company_billing(company_id) values (p_company) on conflict (company_id) do nothing;
  update company_billing set archived = coalesce(p_archived, true), archived_at = case when coalesce(p_archived, true) then now() end,
         updated_at = now() where company_id = p_company;
  perform public.beyond_log(case when coalesce(p_archived, true) then 'company.archive' else 'company.unarchive' end, p_company::text, null);
  return public.beyond_billing_state(p_company);
end $$;

-- Everything needed to bring a company back: the company, its people, billing, the latest copy of its books and every
-- change after it. Full-access owners only (it holds the customer's books).
create or replace function public.console_company_backup(p_company uuid) returns jsonb
language plpgsql security definer set search_path = public as $$
declare c companies; snap snapshots; out jsonb;
begin
  perform public.beyond_require_owner();
  select * into c from companies where id = p_company;
  if c.id is null then raise exception 'company not found'; end if;
  select * into snap from snapshots where company_id = p_company order by seq desc limit 1;
  out := jsonb_build_object('format', 'beyond-company-backup', 'version', 1, 'taken_at', now(), 'taken_by', public.beyond_caller(),
    'company', to_jsonb(c),
    'members', (select coalesce(jsonb_agg(to_jsonb(m)), '[]'::jsonb) from members m where m.company_id = p_company),
    'people', (select coalesce(jsonb_agg(to_jsonb(p)), '[]'::jsonb) from people p where lower(p.email) in (select lower(email) from members where company_id = p_company)),
    'billing', (select to_jsonb(b) from company_billing b where b.company_id = p_company),
    'payments', (select coalesce(jsonb_agg(to_jsonb(p) order by p.created_at), '[]'::jsonb) from billing_payments p where p.company_id = p_company),
    'snapshot', case when snap.seq is null then null else to_jsonb(snap) end,
    'ops', (select coalesce(jsonb_agg(to_jsonb(o) order by o.seq), '[]'::jsonb) from ops o where o.company_id = p_company and o.seq > coalesce(snap.seq, 0)),
    'last_seq', (select coalesce(max(seq), 0) from ops where company_id = p_company));
  perform public.beyond_log('company.backup', p_company::text, jsonb_build_object('name', c.name, 'last_seq', out->'last_seq'));
  return out;
end $$;

-- Bring back a company from its backup (when it no longer exists here). People keep the access the backup recorded.
create or replace function public.console_company_restore(p_backup jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare cid uuid := (p_backup->'company'->>'id')::uuid;
begin
  perform public.beyond_require_owner();
  if coalesce(p_backup->>'format', '') <> 'beyond-company-backup' then raise exception 'this is not a company backup'; end if;
  if cid is null then raise exception 'the backup has no company'; end if;
  if exists (select 1 from companies where id = cid) then raise exception 'this company still exists — nothing to restore'; end if;
  insert into companies select * from jsonb_populate_record(null::companies, p_backup->'company');
  insert into members select * from jsonb_populate_recordset(null::members, coalesce(p_backup->'members', '[]'::jsonb)) on conflict do nothing;
  insert into people select * from jsonb_populate_recordset(null::people, coalesce(p_backup->'people', '[]'::jsonb)) on conflict do nothing;
  if jsonb_typeof(p_backup->'billing') = 'object' then
    insert into company_billing select * from jsonb_populate_record(null::company_billing, p_backup->'billing') on conflict do nothing;
  end if;
  update billing_payments set company_id = cid
   where company_id is null and id in (select (x->>'id')::bigint from jsonb_array_elements(coalesce(p_backup->'payments', '[]'::jsonb)) x);
  if jsonb_typeof(p_backup->'snapshot') = 'object' then
    insert into snapshots select * from jsonb_populate_record(null::snapshots, p_backup->'snapshot') on conflict do nothing;
  end if;
  insert into ops select * from jsonb_populate_recordset(null::ops, coalesce(p_backup->'ops', '[]'::jsonb)) on conflict do nothing;
  delete from platform_deleted where company_id = cid;
  perform public.beyond_log('company.restore', cid::text, jsonb_build_object('name', p_backup->'company'->>'name'));
  return jsonb_build_object('id', cid, 'name', p_backup->'company'->>'name');
end $$;

-- Delete for good: full-access owners, an archived company, a backup taken in the last day, its name typed.
create or replace function public.console_company_delete(p_company uuid, p_name text) returns void
language plpgsql security definer set search_path = public as $$
declare c companies;
begin
  perform public.beyond_require_owner();
  select * into c from companies where id = p_company;
  if c.id is null then raise exception 'company not found'; end if;
  if not exists (select 1 from company_billing where company_id = p_company and archived) then
    raise exception 'archive the company first';
  end if;
  if not exists (select 1 from platform_log where action = 'company.backup' and target = p_company::text and at > now() - interval '24 hours') then
    raise exception 'download a backup of this company first (within the last 24 hours)';
  end if;
  if public.beyond_norm_name(p_name) <> public.beyond_norm_name(c.name) then
    raise exception 'type the company''s name exactly to delete it';
  end if;
  insert into platform_deleted(company_id, name, created_by, summary, deleted_by)
  values (c.id, c.name, c.created_by, jsonb_build_object(
            'people', (select count(*) from members where company_id = c.id),
            'changes', (select coalesce(max(seq), 0) from ops where company_id = c.id),
            'paid_total', (select coalesce(sum(amount), 0) from billing_payments where company_id = c.id and status = 'paid')),
          public.beyond_caller())
  on conflict (company_id) do update set deleted_at = now(), deleted_by = excluded.deleted_by;
  update billing_payments set company_name = coalesce(company_name, c.name) where company_id = c.id;
  perform public.beyond_log('company.delete', c.id::text, jsonb_build_object('name', c.name, 'owner', c.created_by));
  delete from companies where id = p_company;
end $$;

create or replace function public.console_deleted() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('customers');
  return (select coalesce(jsonb_agg(to_jsonb(d) order by d.deleted_at desc), '[]'::jsonb) from platform_deleted d);
end $$;

-- Rename, hand to another owner.
create or replace function public.console_company_edit(p_company uuid, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare c companies; nm text; owner text;
begin
  perform public.beyond_require_staff('approve');
  select * into c from companies where id = p_company;
  if c.id is null then raise exception 'company not found'; end if;
  if p ? 'name' then
    nm := regexp_replace(trim(coalesce(p->>'name', '')), '\s+', ' ', 'g');
    if nm = '' then raise exception 'type a name'; end if;
    update companies set name = nm where id = p_company;
  end if;
  if p ? 'owner' then
    owner := lower(trim(coalesce(p->>'owner', '')));
    if not exists (select 1 from members where company_id = p_company and lower(email) = owner and active and not pending) then
      raise exception 'the new owner must be an active member of the company';
    end if;
    update companies set created_by = owner where id = p_company;
    update members set is_admin = true where company_id = p_company and lower(email) = owner;
  end if;
  perform public.beyond_log('company.edit', p_company::text, p);
  return (select to_jsonb(x) from companies x where id = p_company);
end $$;

-- A member of a customer's company: role, on/off, or removed from it (their sign-in account is not deleted here).
create or replace function public.console_member_set(p_company uuid, p_email text, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare e text := lower(trim(coalesce(p_email, ''))); c companies; admins int;
begin
  perform public.beyond_require_staff('approve');
  select * into c from companies where id = p_company;
  if c.id is null then raise exception 'company not found'; end if;
  if not exists (select 1 from members where company_id = p_company and lower(email) = e) then raise exception 'this person is not in the company'; end if;
  if (coalesce((p->>'remove')::boolean, false) or (p ? 'active' and not (p->>'active')::boolean) or (p ? 'is_admin' and not (p->>'is_admin')::boolean))
     and lower(coalesce(c.created_by, '')) = e then
    raise exception 'this is the company''s owner — hand the company to someone else first';
  end if;
  if coalesce((p->>'remove')::boolean, false) then
    delete from members where company_id = p_company and lower(email) = e;
  else
    update members set
      active   = coalesce((p->>'active')::boolean, active),
      is_admin = coalesce((p->>'is_admin')::boolean, is_admin),
      is_super = coalesce((p->>'is_super')::boolean, is_super),
      pending  = case when coalesce((p->>'approve')::boolean, false) then false else pending end
    where company_id = p_company and lower(email) = e;
  end if;
  select count(*) into admins from members where company_id = p_company and is_admin and active and not pending;
  if admins = 0 then raise exception 'at least one active administrator must remain'; end if;
  perform public.beyond_log('member.set', p_company::text, p || jsonb_build_object('email', e));
  return (select coalesce(jsonb_agg(to_jsonb(m) order by m.email), '[]'::jsonb) from members m where m.company_id = p_company);
end $$;

-- Payments with their company, for the console's payments list (filters and sorting happen on the page).
create or replace function public.console_payments() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('billing');
  return (select coalesce(jsonb_agg(to_jsonb(p) || jsonb_build_object('company', coalesce(c.name, p.company_name), 'owner', c.created_by,
                                                                       'deleted', p.company_id is null) order by p.created_at desc), '[]'::jsonb)
          from (select * from billing_payments order by created_at desc limit 5000) p left join companies c on c.id = p.company_id);
end $$;

-- ================================================================== online support for those it is included for
create or replace function public.support_open(p_company uuid, p_kind text, p_subject text, p_body text, p_diag jsonb,
  p_log jsonb, p_files jsonb, p_app_user text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare tid bigint; nm text;
begin
  if public.beyond_caller() = '' then raise exception 'sign in first' using errcode = '42501'; end if;
  if p_company is not null and not public.beyond_in(p_company) then raise exception 'not a member of this company' using errcode = '42501'; end if;
  if coalesce(trim(p_subject), '') = '' then raise exception 'write a short summary'; end if;
  if p_company is not null and coalesce(p_kind, '') <> 'billing'
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

-- ================================================================== support visits
create table if not exists public.support_visits (
  id            bigserial primary key,
  ref           text unique,
  company_id    uuid references public.companies(id) on delete set null,
  company_name  text,
  email         text not null,                 -- who asked
  ticket_id     bigint references public.support_tickets(id) on delete set null,
  reason        text not null,
  address       text not null default '',
  phone         text not null default '',
  preferred     text not null default '',      -- the days / times that suit them
  files         jsonb not null default '[]'::jsonb,
  status        text not null default 'requested'
                check (status in ('requested', 'quoted', 'accepted', 'scheduled', 'done', 'declined', 'cancelled', 'rejected')),
  price         numeric,
  currency      text,
  pay_when      text not null default 'before' check (pay_when in ('before', 'after', 'free')),
  scheduled_at  timestamptz,
  technician    text,
  note          text,                          -- what the team tells the customer
  staff_note    text,                          -- between the team only
  pay_status    text not null default 'none' check (pay_status in ('none', 'submitted', 'paid', 'waived')),
  method_id     bigint references public.billing_methods(id) on delete set null,
  method_name   text,
  proof         jsonb not null default '[]'::jsonb,
  proof_ref     text,
  proof_amount  numeric,
  paid_at       timestamptz,
  staff_unread  boolean not null default true,
  customer_unread boolean not null default false,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists support_visits_status on public.support_visits(status, updated_at desc);
create index if not exists support_visits_company on public.support_visits(company_id, updated_at desc);
alter table public.support_visits enable row level security;
revoke all on public.support_visits from anon, authenticated;

-- may the caller see this visit: the team (support), the person who asked, or an administrator of its company
create or replace function public.beyond_visit_access(p_visit bigint) returns text
language plpgsql stable security definer set search_path = public as $$
declare v support_visits;
begin
  select * into v from support_visits where id = p_visit;
  if v.id is null then return null; end if;
  if public.beyond_staff_can('support') then return 'staff'; end if;
  if lower(v.email) = public.beyond_caller() then return 'customer'; end if;
  if v.company_id is not null and exists (select 1 from members where company_id = v.company_id and lower(email) = public.beyond_caller()
                                          and active and not pending and is_admin) then return 'customer'; end if;
  return null;
end $$;

create or replace function public.beyond_visit_row(p_visit bigint, p_staff boolean) returns jsonb
language sql stable security definer set search_path = public as $$
  select to_jsonb(v) - case when p_staff then '' else 'staff_note' end
         || jsonb_build_object('method', (select jsonb_build_object('id', m.id, 'kind', m.kind, 'name', m.name, 'name_ar', m.name_ar,
                  'details', m.details, 'instructions', m.instructions, 'instructions_ar', m.instructions_ar, 'needs_proof', m.needs_proof)
                  from billing_methods m where m.id = v.method_id))
  from support_visits v where v.id = p_visit
$$;

create or replace function public.visit_request(p_company uuid, p_reason text, p_address text, p_phone text, p_preferred text,
  p_files jsonb, p_ticket bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare vid bigint; cfg billing_config; nm text;
begin
  if public.beyond_caller() = '' then raise exception 'sign in first' using errcode = '42501'; end if;
  if not public.beyond_in(p_company) then raise exception 'not a member of this company' using errcode = '42501'; end if;
  select * into cfg from billing_config where id = 1;
  if cfg.id is not null and not coalesce((cfg.visits->>'on')::boolean, true) then raise exception 'support visits are not offered at the moment'; end if;
  if coalesce(trim(p_reason), '') = '' then raise exception 'say what the visit is for'; end if;
  if coalesce(trim(p_phone), '') = '' then raise exception 'add a phone number to arrange the visit'; end if;
  if (select count(*) from support_visits where lower(email) = public.beyond_caller() and created_at > now() - interval '1 day') >= 5 then
    raise exception 'too many visit requests today — the team will contact you about the ones you sent';
  end if;
  select name into nm from companies where id = p_company;
  insert into support_visits(company_id, company_name, email, ticket_id, reason, address, phone, preferred, files, price, currency, pay_when)
  values (p_company, nm, public.beyond_caller(),
          case when p_ticket is not null and public.beyond_ticket_access(p_ticket) is not null then p_ticket end,
          left(trim(p_reason), 4000), left(coalesce(p_address, ''), 1000), left(trim(p_phone), 60), left(coalesce(p_preferred, ''), 500),
          public.beyond_clean_files(p_files, public.beyond_caller() || '/'),
          nullif(coalesce((cfg.visits->>'price')::numeric, 0), 0), coalesce(cfg.currency, 'EGP'),
          coalesce(nullif(cfg.visits->>'pay_when', ''), 'before'))
  returning id into vid;
  update support_visits set ref = 'SV-' || lpad(vid::text, 5, '0') where id = vid;
  return public.beyond_visit_row(vid, false);
end $$;

create or replace function public.visit_mine(p_company uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare adm boolean;
begin
  if not public.beyond_in(p_company) then raise exception 'not a member of this company' using errcode = '42501'; end if;
  adm := exists (select 1 from members where company_id = p_company and lower(email) = public.beyond_caller() and active and not pending and is_admin);
  return (select coalesce(jsonb_agg(public.beyond_visit_row(id, false) order by created_at desc), '[]'::jsonb)
          from support_visits where company_id = p_company and (adm or lower(email) = public.beyond_caller()));
end $$;

-- the customer: accept or decline the quote, pay (with a picture of the transfer), or cancel
create or replace function public.visit_customer(p_visit bigint, p_action text, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v support_visits; needs boolean; mt billing_methods;
begin
  if public.beyond_visit_access(p_visit) is null then raise exception 'visit not found' using errcode = '42501'; end if;
  select * into v from support_visits where id = p_visit for update;
  if p_action = 'accept' then
    if v.status <> 'quoted' then raise exception 'there is no quote to accept'; end if;
    update support_visits set status = 'accepted', staff_unread = true, updated_at = now() where id = p_visit;
  elsif p_action = 'decline' then
    if v.status not in ('quoted', 'requested') then raise exception 'this visit can no longer be declined'; end if;
    update support_visits set status = 'declined', note = coalesce(nullif(trim(coalesce(p->>'note', '')), ''), note), staff_unread = true, updated_at = now() where id = p_visit;
  elsif p_action = 'cancel' then
    if v.status in ('done', 'cancelled', 'rejected', 'declined') then raise exception 'this visit is already closed'; end if;
    update support_visits set status = 'cancelled', staff_unread = true, updated_at = now() where id = p_visit;
  elsif p_action = 'pay' then
    if v.pay_status in ('paid', 'waived') then raise exception 'this visit is already paid'; end if;
    if v.status not in ('quoted', 'accepted', 'scheduled', 'done') or v.price is null then raise exception 'wait for the team''s price first'; end if;
    select * into mt from billing_methods where id = nullif(p->>'method', '')::bigint and active;
    if mt.id is null then raise exception 'choose a payment method'; end if;
    needs := coalesce(mt.needs_proof, true);
    if needs and jsonb_array_length(coalesce(public.beyond_clean_files(p->'proof', public.beyond_caller() || '/'), '[]'::jsonb)) = 0 then
      raise exception 'add a picture of the transfer';
    end if;
    update support_visits set pay_status = 'submitted', method_id = mt.id, method_name = mt.name,
           proof = public.beyond_clean_files(p->'proof', public.beyond_caller() || '/'), proof_ref = left(nullif(trim(coalesce(p->>'ref', '')), ''), 120),
           proof_amount = nullif(p->>'amount', '')::numeric, status = case when status = 'quoted' then 'accepted' else status end,
           staff_unread = true, updated_at = now()
     where id = p_visit;
  else raise exception 'unknown action';
  end if;
  update support_visits set customer_unread = false where id = p_visit;
  return public.beyond_visit_row(p_visit, false);
end $$;

-- the team
create or replace function public.console_visits(p_status text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('support');
  return (select coalesce(jsonb_agg(public.beyond_visit_row(id, true) order by created_at desc), '[]'::jsonb)
          from (select id, created_at from support_visits where coalesce(p_status, '') = '' or status = p_status
                order by created_at desc limit 1000) x);
end $$;

create or replace function public.console_visit_set(p_visit bigint, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v support_visits;
begin
  perform public.beyond_require_staff('support');
  select * into v from support_visits where id = p_visit for update;
  if v.id is null then raise exception 'visit not found'; end if;
  if p ? 'status' and p->>'status' not in ('requested', 'quoted', 'accepted', 'scheduled', 'done', 'declined', 'cancelled', 'rejected') then
    raise exception 'unknown status';
  end if;
  if p->>'status' = 'rejected' and coalesce(trim(p->>'note'), '') = '' then raise exception 'write the reason (the customer sees it)'; end if;
  if p ? 'pay_status' and p->>'pay_status' not in ('none', 'submitted', 'paid', 'waived') then raise exception 'unknown payment state'; end if;
  if p ? 'price' and nullif(p->>'price', '') is not null and (p->>'price')::numeric < 0 then raise exception 'the price must be 0 or more'; end if;
  if p ? 'pay_status' and p->>'pay_status' = 'paid' and not public.beyond_staff_can('approve') then
    raise exception 'confirming a payment needs the approve permission' using errcode = '42501';
  end if;
  update support_visits set
    price        = case when p ? 'price' then nullif(p->>'price', '')::numeric else price end,
    pay_when     = coalesce(nullif(p->>'pay_when', ''), pay_when),
    scheduled_at = case when p ? 'scheduled_at' then nullif(p->>'scheduled_at', '')::timestamptz else scheduled_at end,
    technician   = case when p ? 'technician' then nullif(trim(p->>'technician'), '') else technician end,
    note         = case when p ? 'note' then nullif(trim(p->>'note'), '') else note end,
    staff_note   = case when p ? 'staff_note' then nullif(trim(p->>'staff_note'), '') else staff_note end,
    status       = coalesce(nullif(p->>'status', ''), status),
    pay_status   = coalesce(nullif(p->>'pay_status', ''), pay_status),
    paid_at      = case when p->>'pay_status' = 'paid' then now() else paid_at end,
    customer_unread = customer_unread or p ? 'status' or p ? 'price' or p ? 'scheduled_at' or p ? 'note' or p ? 'pay_status',
    staff_unread = false, updated_at = now()
  where id = p_visit;
  perform public.beyond_log('visit.set', v.ref, p);
  return public.beyond_visit_row(p_visit, true);
end $$;

-- tickets for the console's list: 'all' (closed too, for filtering on the page), '' (not closed) or one status
create or replace function public.console_tickets(p_status text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('support');
  return (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'ref', ref, 'company_id', company_id, 'company_name', company_name,
            'email', email, 'app_user', app_user, 'kind', kind, 'subject', subject, 'priority', priority, 'status', status,
            'assignee', assignee, 'unread', staff_unread, 'files', jsonb_array_length(files), 'created_at', created_at, 'updated_at', updated_at,
            'messages', (select count(*) from support_messages m where m.ticket_id = t.id)) order by staff_unread desc, updated_at desc), '[]'::jsonb)
          from (select * from support_tickets where p_status = 'all' or (coalesce(p_status, '') = '' and status not in ('closed'))
                  or status = p_status order by updated_at desc limit 3000) t);
end $$;

-- ================================================================== who may call what
revoke all on function public.beyond_entitle(public.billing_config, jsonb, text, public.company_billing),
  public.beyond_company_rules(), public.beyond_ai_allowed(uuid, text) from public, anon, authenticated;
grant execute on function public.beyond_ai_allowed(uuid, text) to service_role;
grant execute on function public.console_record_payment(jsonb), public.console_people(), public.console_person_save(jsonb),
  public.console_setting_save(text, jsonb), public.console_my_notify(jsonb), public.console_alerts(), public.console_alert_mark(jsonb, text),
  public.console_me(), public.billing_set_config(jsonb), public.billing_set_company(uuid, jsonb),
  public.console_company_archive(uuid, boolean), public.console_company_backup(uuid), public.console_company_restore(jsonb),
  public.console_company_delete(uuid, text), public.console_deleted(), public.console_company_edit(uuid, jsonb),
  public.console_member_set(uuid, text, jsonb), public.console_payments(),
  public.support_open(uuid, text, text, text, jsonb, jsonb, jsonb, text), public.visit_request(uuid, text, text, text, text, jsonb, bigint),
  public.visit_mine(uuid), public.visit_customer(bigint, text, jsonb), public.console_visits(text), public.console_visit_set(bigint, jsonb) to authenticated;
revoke all on function public.beyond_visit_access(bigint), public.beyond_visit_row(bigint, boolean) from public, anon, authenticated;
grant execute on function public.billing_public() to anon, authenticated;
