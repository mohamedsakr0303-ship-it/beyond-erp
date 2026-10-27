-- The owner console: running the hosted service from inside the app.
--
--  * Platform staff: platform_admins gains a name, an on/off switch and permissions per area. perms = null means full
--    access (the owner). Areas: overview, customers, billing (see orders and payments), approve (approve / reject
--    payments, record payments, trials and free use), support, notices, settings (plans, prices, payment methods,
--    trial, support contacts). Only full-access staff manage staff; at least one full-access person always remains.
--  * One subscription can cover several companies: every company is a full copy of the plan with its own extra users;
--    the first company in an order costs 100 %, each further one extra_company_pct % (default 100: each doubles it).
--  * The free trial covers a person's first company only (and every company that existed when billing was switched on,
--    or when this arrived with billing already on).
--    A later company starts "unpaid": it can be opened but not changed until it is in a paid order.
--  * Payment methods (InstaPay, wallet, bank transfer, card…) are set up from the console. For a manual method the
--    customer uploads a picture of the transfer; staff approve it (the companies are extended at once) or reject it
--    with a reason the customer sees.
--  * Support tickets reach the console centrally, with the customer's activity log, pictures and screen recordings,
--    and a conversation with the team.
--  * Announcements shown inside every customer's app; a log of what staff did.
-- Files (payment proofs, support attachments) live in two private Storage buckets.

-- ================================================================== staff
alter table public.platform_admins add column if not exists name     text;
alter table public.platform_admins add column if not exists perms    jsonb;           -- null = full access
alter table public.platform_admins add column if not exists active   boolean not null default true;
alter table public.platform_admins add column if not exists added_by text;
alter table public.platform_admins add column if not exists added_at timestamptz not null default now();

create table if not exists public.platform_log (
  id      bigserial primary key,
  at      timestamptz not null default now(),
  email   text not null,
  action  text not null,
  target  text,
  detail  jsonb
);
create index if not exists platform_log_at on public.platform_log(at desc);
alter table public.platform_log enable row level security;
revoke all on public.platform_log from anon, authenticated;

create or replace function public.beyond_staff() returns public.platform_admins
language sql stable security definer set search_path = public as $$
  select * from platform_admins where public.beyond_caller() <> '' and lower(email) = public.beyond_caller() and active
$$;

create or replace function public.beyond_staff_can(p_area text) returns boolean
language plpgsql stable security definer set search_path = public as $$
declare s platform_admins;
begin
  s := public.beyond_staff();
  if s.email is null then return false; end if;
  return s.perms is null or coalesce((s.perms ->> p_area)::boolean, false);
end $$;

create or replace function public.beyond_require_staff(p_area text) returns void
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.beyond_staff_can(p_area) then
    raise exception 'your team account does not have access to this (%)', p_area using errcode = '42501';
  end if;
end $$;

-- any active member of the platform team (the console's menu entry)
create or replace function public.beyond_is_owner() returns boolean
language sql stable security definer set search_path = public as $$
  select (public.beyond_staff()).email is not null
$$;

create or replace function public.beyond_require_owner() returns void
language plpgsql stable security definer set search_path = public as $$
declare s platform_admins;
begin
  s := public.beyond_staff();
  if s.email is null or s.perms is not null then
    raise exception 'only the platform owner can do this' using errcode = '42501';
  end if;
end $$;

create or replace function public.beyond_log(p_action text, p_target text, p_detail jsonb default null) returns void
language sql security definer set search_path = public as $$
  insert into platform_log(email, action, target, detail) values (public.beyond_caller(), p_action, p_target, p_detail)
$$;

-- ================================================================== settings
alter table public.billing_config add column if not exists extra_company_pct numeric not null default 100
  check (extra_company_pct between 0 and 1000);
alter table public.billing_config add column if not exists support jsonb not null default '{}'::jsonb;  -- whatsapp, email, phone, hours
-- If billing is already on when this arrives, companies that exist now keep the trial they had (one trial per person
-- applies to companies created from here on): nobody is cut off by the deploy.
alter table public.billing_config add column if not exists one_trial_from timestamptz;
update public.billing_config set one_trial_from = now() where id = 1 and enabled and one_trial_from is null;
alter table public.company_billing add column if not exists cancel_at timestamptz;
alter table public.billing_payments add column if not exists period_start timestamptz;
alter table public.billing_payments add column if not exists period_end   timestamptz;
alter table public.billing_payments add column if not exists order_id     bigint;

-- every plan now covers one company (more companies are priced per company): the plans lose their company count, and
-- the shipped feature line that promised several companies keeps only its support part (lines the owner wrote are kept)
update public.billing_config
   set plans = (select jsonb_agg(jsonb_set(p - 'companies', '{features}',
                  coalesce((select jsonb_agg(case f #>> '{}' when 'Up to 3 companies, priority support (same business day)' then to_jsonb('Priority support (same business day)'::text)
                                                             when 'Up to 3 companies, priority support' then to_jsonb('Priority support'::text) else f end order by fo)
                            from jsonb_array_elements(coalesce(p->'features', '[]'::jsonb)) with ordinality fe(f, fo)), '[]'::jsonb)) order by o)
                from jsonb_array_elements(plans) with ordinality pe(p, o))
 where id = 1 and exists (select 1 from jsonb_array_elements(plans) p where p ? 'companies' or p::text like '%Up to 3 companies%');

create table if not exists public.billing_methods (
  id              bigserial primary key,
  kind            text not null default 'instapay' check (kind in ('instapay', 'wallet', 'bank', 'card', 'cash', 'other')),
  name            text not null,
  name_ar         text,
  details         text not null default '',      -- the InstaPay address / number, account number, IBAN…
  instructions    text not null default '',
  instructions_ar text not null default '',
  needs_proof     boolean not null default true,  -- the customer uploads a picture of the transfer
  active          boolean not null default true,
  sort            int not null default 0,
  created_at      timestamptz not null default now()
);
alter table public.billing_methods enable row level security;
revoke all on public.billing_methods from anon, authenticated;

create table if not exists public.platform_notices (
  id         bigserial primary key,
  title      text not null,
  title_ar   text,
  body       text not null default '',
  body_ar    text,
  level      text not null default 'info' check (level in ('info', 'warn', 'critical')),
  audience   text not null default 'all' check (audience in ('all', 'trial', 'paying', 'unpaid')),
  starts_at  timestamptz not null default now(),
  ends_at    timestamptz,
  active     boolean not null default true,
  created_by text,
  created_at timestamptz not null default now()
);
alter table public.platform_notices enable row level security;
revoke all on public.platform_notices from anon, authenticated;

-- ================================================================== where a company stands
-- The trial: a person's first company, and every company that existed when billing was switched on. A later company
-- of the same person is "unpaid" until it is in a paid order (the owner can still give it a trial or free use).
create or replace function public.beyond_billing_state(p_company uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare cfg billing_config; co companies; b company_billing; trial_end timestamptz; status text; ends timestamptz;
        plan jsonb; users int; members_n int; first_one boolean;
begin
  select * into cfg from billing_config where id = 1;
  if cfg.id is null or not cfg.enabled then
    return jsonb_build_object('enabled', false, 'status', 'free', 'can_write', true);
  end if;
  select * into co from companies where id = p_company;
  select * into b from company_billing where company_id = p_company;
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
  select count(*) into members_n from members where company_id = p_company and active and not pending;
  return jsonb_build_object(
    'enabled', true, 'status', status, 'can_write', status not in ('expired', 'unpaid'),
    'ends', ends, 'days_left', case when ends is null then null else greatest(0, ceil(extract(epoch from ends - now()) / 86400))::int end,
    'trial_ends', trial_end, 'paid_until', b.paid_until, 'plan', plan->>'code', 'plan_name', plan->>'name',
    'users_allowed', case when status = 'comped' then null else users end, 'users', members_n,
    'extra_users', coalesce(b.extra_users, 0), 'cancel_note', b.cancel_note,
    'currency', cfg.currency, 'grace_days', cfg.grace_days);
end $$;

-- The refusal message names the reason (trial / paid period ended, or a company awaiting its subscription).
create or replace function public.append_op(p_company uuid, p_seq bigint, p_version text, p_app_user text, p_device text,
  p_method text, p_path text, p_body text, p_content_type text, p_clock timestamp) returns json
language plpgsql security definer set search_path = public as $$
declare m members; last bigint; who text; st jsonb;
begin
  m := public.beyond_member(p_company);
  if m.email is null then raise exception 'not a member of this company' using errcode = '42501'; end if;
  if p_method not in ('POST', 'PUT', 'DELETE') or left(p_path, 5) <> '/api/' then
    raise exception 'invalid change' using errcode = '22023';
  end if;
  st := public.beyond_billing_state(p_company);
  if not (st->>'can_write')::boolean then
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

-- a payment records the period it pays for (the console's "expected" figures come from when each period ends)
create or replace function public.beyond_apply_payment(p_payment bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare p billing_payments; b company_billing; start_at timestamptz;
begin
  select * into p from billing_payments where id = p_payment for update;
  if p.id is null then raise exception 'payment not found'; end if;
  if p.status = 'paid' then return public.beyond_billing_state(p.company_id); end if;
  insert into company_billing(company_id) values (p.company_id) on conflict (company_id) do nothing;
  select * into b from company_billing where company_id = p.company_id for update;
  -- paying during the trial keeps the trial days left; paying early keeps the paid days left
  start_at := greatest(now(), coalesce(b.paid_until, now()),
                       coalesce((public.beyond_billing_state(p.company_id)->>'trial_ends')::timestamptz, now()));
  update company_billing set plan = p.plan, extra_users = p.extra_users, cancel_note = null, cancel_at = null,
         paid_until = start_at + make_interval(months => p.months), updated_at = now()
   where company_id = p.company_id;
  update billing_payments set status = 'paid', paid_at = now(), period_start = start_at,
         period_end = start_at + make_interval(months => p.months) where id = p.id;
  return public.beyond_billing_state(p.company_id);
end $$;

create or replace function public.billing_cancel(p_company uuid, p_note text) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_admin(p_company);
  insert into company_billing(company_id, cancel_note, cancel_at) values (p_company, coalesce(nullif(trim(p_note), ''), 'cancelled'), now())
  on conflict (company_id) do update set cancel_note = excluded.cancel_note, cancel_at = now(), updated_at = now();
  return public.beyond_billing_state(p_company);
end $$;

-- ================================================================== what everyone sees (before signing in, too)
create or replace function public.billing_public() returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object('enabled', enabled, 'trial_days', trial_days, 'currency', currency, 'plans', plans,
    'extra_user', extra_user, 'yearly_months', yearly_months, 'payment_note', payment_note,
    'extra_company_pct', extra_company_pct, 'support', support,
    'methods', (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'kind', kind, 'name', name, 'name_ar', name_ar, 'details', details,
                  'instructions', instructions, 'instructions_ar', instructions_ar, 'needs_proof', needs_proof) order by sort, id), '[]'::jsonb)
                from billing_methods where active),
    'notices', (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'title', title, 'title_ar', title_ar, 'body', body, 'body_ar', body_ar,
                  'level', level, 'audience', audience, 'starts_at', starts_at, 'ends_at', ends_at) order by starts_at desc), '[]'::jsonb)
                from platform_notices where active and starts_at <= now() and (ends_at is null or ends_at > now())))
  from billing_config where id = 1
$$;
grant execute on function public.billing_public() to anon, authenticated;

-- the customer's own view also says what the console may show to staff
create or replace function public.billing_status(p_company uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare m members; s platform_admins;
begin
  m := public.beyond_member(p_company);
  if m.email is null then raise exception 'not a member of this company' using errcode = '42501'; end if;
  s := public.beyond_staff();
  return public.beyond_billing_state(p_company) || jsonb_build_object(
    'is_admin', m.is_admin, 'is_owner', s.email is not null, 'staff_perms', case when s.email is null then null else coalesce(s.perms, '"all"'::jsonb) end,
    'payments', case when m.is_admin then (select coalesce(jsonb_agg(jsonb_build_object(
        'id', id, 'date', coalesce(paid_at, created_at), 'plan', plan, 'months', months, 'amount', amount, 'currency', currency,
        'provider', provider, 'status', status, 'reference', reference) order by created_at desc), '[]'::jsonb)
      from (select * from billing_payments where company_id = p_company order by created_at desc limit 24) x) end);
end $$;

-- ================================================================== orders: several companies in one subscription
create table if not exists public.billing_orders (
  id            bigserial primary key,
  ref           text unique not null,
  email         text not null,                  -- who placed it
  plan          text not null,
  months        int not null check (months in (1, 12)),
  lines         jsonb not null,                 -- [{company_id, name, extra_users, base, pct, amount}]
  amount        numeric not null,
  currency      text not null default 'EGP',
  method_id     bigint references public.billing_methods(id) on delete set null,
  method_kind   text,
  method_name   text,
  status        text not null default 'open' check (status in ('open', 'submitted', 'approved', 'rejected', 'cancelled')),
  proof         jsonb not null default '[]'::jsonb,   -- [{path, name, type, size}] in the billing-proofs bucket
  proof_ref     text,
  proof_amount  numeric,
  customer_note text,
  submitted_at  timestamptz,
  review_note   text,
  reviewed_by   text,
  reviewed_at   timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists billing_orders_email on public.billing_orders(lower(email), created_at desc);
create index if not exists billing_orders_status on public.billing_orders(status, created_at desc);
alter table public.billing_orders enable row level security;
revoke all on public.billing_orders from anon, authenticated;

-- The price of an order (worked out here, never taken from the browser). p_lines: [{company_id, extra_users}].
-- Each company: (plan + its extra users) × months; the most expensive line counts 100 %, every other extra_company_pct %.
create or replace function public.beyond_order_price(p_plan text, p_months int, p_lines jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare cfg billing_config; plan jsonb; m int := coalesce(p_months, 1); x jsonb; out jsonb := '[]'::jsonb; total numeric := 0;
        base numeric; extra int; cid uuid; nm text; members_n int; i int := 0; pct numeric;
begin
  select * into cfg from billing_config where id = 1;
  plan := public.beyond_plan(p_plan);
  if plan is null then raise exception 'unknown plan %', p_plan; end if;
  if m not in (1, 12) then raise exception 'pay for 1 month or 12 months'; end if;
  if jsonb_typeof(p_lines) <> 'array' or jsonb_array_length(p_lines) = 0 then raise exception 'choose at least one company'; end if;
  if jsonb_array_length(p_lines) > 50 then raise exception 'up to 50 companies in one order'; end if;
  for x in select v from jsonb_array_elements(p_lines) v
             order by coalesce((v->>'extra_users')::int, 0) desc loop
    cid := (x->>'company_id')::uuid;
    extra := coalesce((x->>'extra_users')::int, 0);
    if extra < 0 or extra > 500 then raise exception 'extra users must be 0 to 500'; end if;
    select name into nm from companies where id = cid;
    if nm is null then raise exception 'company not found'; end if;
    if exists (select 1 from jsonb_array_elements(out) o where o->>'company_id' = cid::text) then raise exception 'a company appears twice'; end if;
    select count(*) into members_n from members where company_id = cid and active and not pending;
    if members_n > (plan->>'users')::int + extra then
      raise exception '"%" has % active people — choose a bigger plan or add % extra user(s) for it', nm, members_n,
        members_n - (plan->>'users')::int using errcode = '22023';
    end if;
    base := ((plan->>'price')::numeric + cfg.extra_user * extra) * case when m = 12 then cfg.yearly_months else 1 end;
    pct := case when i = 0 then 100 else cfg.extra_company_pct end;
    out := out || jsonb_build_object('company_id', cid, 'name', nm, 'extra_users', extra, 'base', round(base, 2), 'pct', pct,
                                     'amount', round(base * pct / 100, 2));
    total := total + round(base * pct / 100, 2);
    i := i + 1;
  end loop;
  return jsonb_build_object('plan', p_plan, 'plan_name', plan->>'name', 'months', m, 'lines', out, 'amount', total, 'currency', cfg.currency);
end $$;

-- the caller must be an administrator of every company in the order
create or replace function public.beyond_require_admin_all(p_lines jsonb) returns void
language plpgsql stable security definer set search_path = public as $$
declare x jsonb;
begin
  for x in select v from jsonb_array_elements(p_lines) v loop
    if not exists (select 1 from members where company_id = (x->>'company_id')::uuid and lower(email) = public.beyond_caller()
                   and active and not pending and is_admin) then
      raise exception 'only administrators of each company can subscribe for it' using errcode = '42501';
    end if;
  end loop;
end $$;

create or replace function public.billing_order_quote(p_plan text, p_months int, p_lines jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_admin_all(p_lines);
  return public.beyond_order_price(p_plan, p_months, p_lines);
end $$;

-- Place an order to pay with a method (a person's earlier unpaid order is replaced).
create or replace function public.billing_order_open(p_plan text, p_months int, p_lines jsonb, p_method bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare q jsonb; mt billing_methods; oid bigint; r text;
begin
  perform public.beyond_require_admin_all(p_lines);
  select * into mt from billing_methods where id = p_method and active;
  if mt.id is null then raise exception 'choose a payment method'; end if;
  q := public.beyond_order_price(p_plan, p_months, p_lines);
  update billing_orders set status = 'cancelled', updated_at = now()
   where lower(email) = public.beyond_caller() and status = 'open';
  r := 'BO-' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 8));
  insert into billing_orders(ref, email, plan, months, lines, amount, currency, method_id, method_kind, method_name)
  values (r, public.beyond_caller(), p_plan, (q->>'months')::int, q->'lines', (q->>'amount')::numeric, q->>'currency',
          mt.id, mt.kind, mt.name)
  returning id into oid;
  return public.beyond_order_row(oid, false);
end $$;

create or replace function public.beyond_order_row(p_order bigint, p_staff boolean) returns jsonb
language sql stable security definer set search_path = public as $$
  select to_jsonb(o) - case when p_staff then '' else 'reviewed_by' end
         || jsonb_build_object('method', (select jsonb_build_object('id', m.id, 'kind', m.kind, 'name', m.name, 'name_ar', m.name_ar,
                  'details', m.details, 'instructions', m.instructions, 'instructions_ar', m.instructions_ar, 'needs_proof', m.needs_proof)
                  from billing_methods m where m.id = o.method_id))
  from billing_orders o where o.id = p_order
$$;

-- The customer sent the money: the picture(s) of the transfer, its reference and amount.
create or replace function public.billing_order_submit(p_order bigint, p_proof jsonb, p_ref text, p_amount numeric, p_note text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare o billing_orders; needs boolean;
begin
  select * into o from billing_orders where id = p_order for update;
  if o.id is null or lower(o.email) <> public.beyond_caller() then raise exception 'order not found' using errcode = '42501'; end if;
  if o.status not in ('open', 'rejected') then raise exception 'this order was already sent'; end if;
  select coalesce(needs_proof, true) into needs from billing_methods where id = o.method_id;
  if jsonb_typeof(coalesce(p_proof, '[]')) <> 'array' or jsonb_array_length(coalesce(p_proof, '[]')) > 5 then
    raise exception 'up to 5 pictures';
  end if;
  if coalesce(needs, true) and jsonb_array_length(coalesce(p_proof, '[]')) = 0 then
    raise exception 'add a picture of the transfer';
  end if;
  if exists (select 1 from jsonb_array_elements(coalesce(p_proof, '[]')) f
             where coalesce(f->>'path', '') not like public.beyond_caller() || '/%') then
    raise exception 'pictures must be uploaded by you';
  end if;
  update billing_orders set status = 'submitted', proof = coalesce(p_proof, '[]'), proof_ref = left(nullif(trim(coalesce(p_ref, '')), ''), 120),
         proof_amount = p_amount, customer_note = left(nullif(trim(coalesce(p_note, '')), ''), 2000), submitted_at = now(),
         review_note = null, updated_at = now()
   where id = p_order;
  return public.beyond_order_row(p_order, false);
end $$;

create or replace function public.billing_order_cancel(p_order bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  update billing_orders set status = 'cancelled', updated_at = now()
   where id = p_order and lower(email) = public.beyond_caller() and status in ('open', 'submitted', 'rejected');
  if not found then raise exception 'order not found or already decided'; end if;
  return public.beyond_order_row(p_order, false);
end $$;

-- The caller's orders, newest first.
create or replace function public.billing_my_orders() returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(public.beyond_order_row(id, false) order by created_at desc), '[]'::jsonb)
  from (select id, created_at from billing_orders where lower(email) = public.beyond_caller() order by created_at desc limit 20) x
$$;

-- Approve: every company in the order is paid for (once).
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
    insert into billing_payments(company_id, plan, months, extra_users, amount, currency, provider, reference, provider_txn, note, created_by, order_id)
    values ((x->>'company_id')::uuid, o.plan, o.months, coalesce((x->>'extra_users')::int, 0), (x->>'amount')::numeric, o.currency,
            case when o.method_kind = 'card' then 'paymob' else 'manual' end, o.ref || '-' || i, p_txn,
            left(coalesce(o.method_name, '') || coalesce(' · ' || o.proof_ref, ''), 300), o.email, o.id)
    returning id into pid;
    perform public.beyond_apply_payment(pid);
  end loop;
  update billing_orders set status = 'approved', review_note = nullif(trim(coalesce(p_note, '')), ''), reviewed_by = nullif(public.beyond_caller(), ''),
         reviewed_at = now(), updated_at = now()
   where id = p_order;
  return public.beyond_order_row(p_order, true);
end $$;

-- ================================================================== card payments go through orders too
create or replace function public.billing_paid(p_reference text, p_txn text, p_amount_cents bigint, p_success boolean) returns jsonb
language plpgsql security definer set search_path = public as $$
declare p billing_payments; o billing_orders;
begin
  select * into o from billing_orders where ref = p_reference for update;
  if o.id is not null then
    if o.status = 'approved' then return jsonb_build_object('status', 'paid', 'already', true); end if;
    if not p_success then
      update billing_orders set review_note = 'card payment failed (' || coalesce(p_txn, '') || ')', updated_at = now() where id = o.id;
      return jsonb_build_object('status', 'failed');
    end if;
    if round(o.amount * 100) <> p_amount_cents then
      update billing_orders set review_note = 'amount mismatch: ' || p_amount_cents, updated_at = now() where id = o.id;
      raise exception 'amount does not match the order';
    end if;
    perform public.beyond_order_approve(o.id, 'paid by card', p_txn);
    return jsonb_build_object('status', 'paid');
  end if;
  select * into p from billing_payments where reference = p_reference for update;
  if p.id is null then raise exception 'unknown payment reference %', p_reference; end if;
  if p.status = 'paid' then return jsonb_build_object('status', 'paid', 'already', true); end if;
  if not p_success then
    update billing_payments set status = 'failed', provider_txn = p_txn where id = p.id;
    return jsonb_build_object('status', 'failed');
  end if;
  if round(p.amount * 100) <> p_amount_cents then
    update billing_payments set status = 'failed', provider_txn = p_txn, note = 'amount mismatch: ' || p_amount_cents where id = p.id;
    raise exception 'amount does not match the order';
  end if;
  update billing_payments set provider_txn = p_txn where id = p.id;
  return public.beyond_apply_payment(p.id) || jsonb_build_object('status', 'paid');
end $$;

-- ================================================================== support
create table if not exists public.support_tickets (
  id              bigserial primary key,
  ref             text unique,
  company_id      uuid references public.companies(id) on delete set null,
  company_name    text,
  email           text not null,
  app_user        text,
  kind            text not null default 'issue',
  subject         text not null,
  body            text not null default '',
  priority        text not null default 'normal' check (priority in ('low', 'normal', 'high', 'urgent')),
  status          text not null default 'open' check (status in ('open', 'answered', 'waiting', 'resolved', 'closed')),
  diag            jsonb not null default '{}'::jsonb,   -- device, browser, version, screen…
  log             jsonb not null default '[]'::jsonb,   -- what happened before the report (screens, requests, errors)
  files           jsonb not null default '[]'::jsonb,   -- [{path, name, type, size}] in the support-files bucket
  assignee        text,
  staff_unread    boolean not null default true,
  customer_unread boolean not null default false,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists support_tickets_status on public.support_tickets(status, updated_at desc);
create index if not exists support_tickets_email on public.support_tickets(lower(email), updated_at desc);
create index if not exists support_tickets_company on public.support_tickets(company_id, updated_at desc);

create table if not exists public.support_messages (
  id         bigserial primary key,
  ticket_id  bigint not null references public.support_tickets(id) on delete cascade,
  email      text not null,
  staff      boolean not null default false,
  internal   boolean not null default false,       -- a note between staff, never shown to the customer
  body       text not null default '',
  files      jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists support_messages_ticket on public.support_messages(ticket_id, id);
alter table public.support_tickets enable row level security;
alter table public.support_messages enable row level security;
revoke all on public.support_tickets, public.support_messages from anon, authenticated;

create or replace function public.beyond_clean_files(p_files jsonb, p_prefix text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare f jsonb; out jsonb := '[]'::jsonb;
begin
  if p_files is null or jsonb_typeof(p_files) <> 'array' then return out; end if;
  if jsonb_array_length(p_files) > 10 then raise exception 'up to 10 files at a time'; end if;
  for f in select v from jsonb_array_elements(p_files) v loop
    if coalesce(f->>'path', '') not like p_prefix || '%' or f->>'path' like '%..%' then raise exception 'files must be uploaded by you'; end if;
    out := out || jsonb_build_object('path', f->>'path', 'name', left(coalesce(f->>'name', ''), 200), 'type', left(coalesce(f->>'type', ''), 100),
                                     'size', coalesce((f->>'size')::bigint, 0));
  end loop;
  return out;
end $$;

-- May the caller see this ticket? Its reporter, an administrator of its company, or support staff.
create or replace function public.beyond_ticket_access(p_ticket bigint) returns text
language plpgsql stable security definer set search_path = public as $$
declare t support_tickets;
begin
  select * into t from support_tickets where id = p_ticket;
  if t.id is null then return null; end if;
  if public.beyond_staff_can('support') then return 'staff'; end if;
  if lower(t.email) = public.beyond_caller() then return 'customer'; end if;
  if t.company_id is not null and exists (select 1 from members where company_id = t.company_id and lower(email) = public.beyond_caller()
                                          and active and not pending and is_admin) then return 'customer'; end if;
  return null;
end $$;

create or replace function public.support_open(p_company uuid, p_kind text, p_subject text, p_body text, p_diag jsonb,
  p_log jsonb, p_files jsonb, p_app_user text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare tid bigint; nm text;
begin
  if public.beyond_caller() = '' then raise exception 'sign in first' using errcode = '42501'; end if;
  if p_company is not null and not public.beyond_in(p_company) then raise exception 'not a member of this company' using errcode = '42501'; end if;
  if coalesce(trim(p_subject), '') = '' then raise exception 'write a short summary'; end if;
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

create or replace function public.support_get(p_ticket bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare a text; t jsonb;
begin
  a := public.beyond_ticket_access(p_ticket);
  if a is null then raise exception 'ticket not found' using errcode = '42501'; end if;
  if a = 'staff' then update support_tickets set staff_unread = false where id = p_ticket and staff_unread;
  else update support_tickets set customer_unread = false where id = p_ticket and customer_unread; end if;
  select to_jsonb(s) - case when a = 'staff' then '' else 'assignee' end into t from support_tickets s where id = p_ticket;
  return t || jsonb_build_object('as', a, 'messages',
    (select coalesce(jsonb_agg(to_jsonb(m) order by m.id), '[]'::jsonb) from support_messages m
      where m.ticket_id = p_ticket and (a = 'staff' or not m.internal)));
end $$;

-- A message on a ticket: the customer's re-opens it; the team's marks it answered (or stays as an internal note).
create or replace function public.support_reply(p_ticket bigint, p_body text, p_files jsonb, p_internal boolean) returns jsonb
language plpgsql security definer set search_path = public as $$
declare a text; is_staff boolean;
begin
  a := public.beyond_ticket_access(p_ticket);
  if a is null then raise exception 'ticket not found' using errcode = '42501'; end if;
  is_staff := a = 'staff';
  if coalesce(trim(p_body), '') = '' and jsonb_array_length(coalesce(p_files, '[]')) = 0 then raise exception 'write a message or attach a file'; end if;
  insert into support_messages(ticket_id, email, staff, internal, body, files)
  values (p_ticket, public.beyond_caller(), is_staff, is_staff and coalesce(p_internal, false), left(coalesce(p_body, ''), 20000),
          public.beyond_clean_files(p_files, case when is_staff then 'staff/' else public.beyond_caller() || '/' end));
  if is_staff and not coalesce(p_internal, false) then
    update support_tickets set status = case when status in ('resolved', 'closed') then status else 'answered' end,
           customer_unread = true, updated_at = now() where id = p_ticket;
  elsif not is_staff then
    update support_tickets set status = 'open', staff_unread = true, updated_at = now() where id = p_ticket;
  end if;
  return public.support_get(p_ticket);
end $$;

-- The customer closes their ticket (solved), or re-opens it.
create or replace function public.support_close(p_ticket bigint, p_solved boolean) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  if public.beyond_ticket_access(p_ticket) is null then raise exception 'ticket not found' using errcode = '42501'; end if;
  update support_tickets set status = case when coalesce(p_solved, true) then 'resolved' else 'open' end,
         staff_unread = staff_unread or not coalesce(p_solved, true), updated_at = now() where id = p_ticket;
  return public.support_get(p_ticket);
end $$;

-- The caller's tickets (and, for a company administrator, their company's).
create or replace function public.support_mine(p_company uuid) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'ref', ref, 'kind', kind, 'subject', subject, 'status', status,
           'email', email, 'app_user', app_user, 'created_at', created_at, 'updated_at', updated_at, 'unread', customer_unread)
         order by updated_at desc), '[]'::jsonb)
  from (select * from support_tickets t
        where lower(t.email) = public.beyond_caller()
           or (t.company_id = p_company and exists (select 1 from members where company_id = p_company and lower(email) = public.beyond_caller()
                                                     and active and not pending and is_admin))
        order by updated_at desc limit 100) x
$$;

-- ================================================================== the console (platform staff)
create or replace function public.console_me() returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare s platform_admins;
begin
  s := public.beyond_staff();
  if s.email is null then raise exception 'not a member of the platform team' using errcode = '42501'; end if;
  return jsonb_build_object('email', s.email, 'name', coalesce(s.name, split_part(s.email, '@', 1)), 'full', s.perms is null,
    'perms', coalesce(s.perms, '{}'::jsonb),
    'badges', jsonb_build_object(
      'orders', case when public.beyond_staff_can('billing') then (select count(*) from billing_orders where status = 'submitted') end,
      'tickets', case when public.beyond_staff_can('support') then (select count(*) from support_tickets where staff_unread and status not in ('closed')) end));
end $$;

-- Companies with where each stands, its owner, people, activity and open support.
create or replace function public.console_companies() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('customers');
  return (select coalesce(jsonb_agg(jsonb_build_object(
      'id', c.id, 'name', c.name, 'created_at', c.created_at, 'created_by', c.created_by,
      'state', public.beyond_billing_state(c.id), 'comped', coalesce(b.comped, false), 'cancel_note', b.cancel_note, 'cancel_at', b.cancel_at,
      'people', (select count(*) from members m where m.company_id = c.id and m.active and not m.pending),
      'changes', (select coalesce(max(seq), 0) from ops o where o.company_id = c.id),
      'last_change', (select max(created_at) from ops o where o.company_id = c.id),
      'paid_total', (select coalesce(sum(amount), 0) from billing_payments p where p.company_id = c.id and p.status = 'paid'),
      'last_payment', (select jsonb_build_object('date', paid_at, 'amount', amount, 'months', months)
                       from billing_payments p where p.company_id = c.id and p.status = 'paid' order by paid_at desc limit 1),
      'tickets_open', (select count(*) from support_tickets t where t.company_id = c.id and t.status in ('open', 'answered', 'waiting')))
      order by c.created_at desc), '[]'::jsonb)
    from companies c left join company_billing b on b.company_id = c.id);
end $$;

-- One company in detail: its people, payments, orders, support and use.
create or replace function public.console_company(p_company uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('customers');
  return (select jsonb_build_object('id', c.id, 'name', c.name, 'created_at', c.created_at, 'created_by', c.created_by,
      'state', public.beyond_billing_state(c.id), 'billing', (select to_jsonb(b) from company_billing b where b.company_id = c.id),
      'people', (select coalesce(jsonb_agg(jsonb_build_object('email', m.email, 'is_admin', m.is_admin, 'active', m.active, 'pending', m.pending,
                   'super', m.is_super or lower(m.email) = lower(coalesce(c.created_by, ''))) order by m.is_admin desc, m.email), '[]'::jsonb)
                 from members m where m.company_id = c.id),
      'payments', (select coalesce(jsonb_agg(to_jsonb(p) order by p.created_at desc), '[]'::jsonb) from billing_payments p where p.company_id = c.id),
      'orders', (select coalesce(jsonb_agg(jsonb_build_object('id', o.id, 'ref', o.ref, 'status', o.status, 'amount', o.amount, 'created_at', o.created_at,
                   'months', o.months, 'plan', o.plan) order by o.created_at desc), '[]'::jsonb)
                 from billing_orders o where o.lines @> jsonb_build_array(jsonb_build_object('company_id', c.id))),
      'tickets', (select coalesce(jsonb_agg(jsonb_build_object('id', t.id, 'ref', t.ref, 'subject', t.subject, 'status', t.status,
                   'updated_at', t.updated_at) order by t.updated_at desc), '[]'::jsonb) from support_tickets t where t.company_id = c.id),
      'use', jsonb_build_object('changes', (select coalesce(max(seq), 0) from ops o where o.company_id = c.id),
               'changes_30d', (select count(*) from ops o where o.company_id = c.id and o.created_at > now() - interval '30 days'),
               'last_change', (select max(created_at) from ops o where o.company_id = c.id),
               'devices', (select count(distinct device) from ops o where o.company_id = c.id and o.created_at > now() - interval '30 days')))
    from companies c where c.id = p_company);
end $$;

-- The dashboard: money expected (renewals falling due, each at what it last cost) against money received, by month or
-- day; renewals and trials coming up; figures for each status, open orders and support.
create or replace function public.console_overview(p_from date, p_to date, p_by text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare unit text := case when p_by = 'day' then 'day' else 'month' end; f date := coalesce(p_from, (now() - interval '11 months')::date);
        t date := coalesce(p_to, now()::date);
begin
  perform public.beyond_require_staff('overview');
  return jsonb_build_object(
    'currency', (select currency from billing_config where id = 1),
    'by', unit,
    'series', (select coalesce(jsonb_agg(jsonb_build_object('period', to_char(g, case when unit = 'day' then 'YYYY-MM-DD' else 'YYYY-MM' end),
        'expected', (select coalesce(sum(p.amount), 0) from billing_payments p where p.status = 'paid'
                      and p.period_end >= g and p.period_end < g + ('1 ' || unit)::interval),
        'real', (select coalesce(sum(p.amount), 0) from billing_payments p where p.status = 'paid'
                      and p.paid_at >= g and p.paid_at < g + ('1 ' || unit)::interval)) order by g), '[]'::jsonb)
      from generate_series(date_trunc(unit, f::timestamptz), date_trunc(unit, t::timestamptz), ('1 ' || unit)::interval) g),
    'upcoming', (select coalesce(jsonb_agg(x order by x->>'ends'), '[]'::jsonb) from (
        select jsonb_build_object('company_id', c.id, 'name', c.name, 'owner', c.created_by, 'status', s->>'status', 'ends', s->>'ends',
               'plan', s->>'plan_name', 'amount', (select amount from billing_payments p where p.company_id = c.id and p.status = 'paid'
                                                   order by paid_at desc limit 1)) x
        from companies c, lateral (select public.beyond_billing_state(c.id) s) st
        where s->>'status' in ('trial', 'active', 'past_due') and (s->>'ends')::timestamptz < now() + interval '30 days') y),
    'counts', (select coalesce(jsonb_object_agg(k, n), '{}'::jsonb) from (
        select public.beyond_billing_state(id)->>'status' k, count(*) n from companies group by 1) z),
    'mrr', (select coalesce(sum(case when p.months = 12 then p.amount / 12 else p.amount end), 0) from billing_payments p
            where p.status = 'paid' and p.period_end > now() and p.period_start <= now()),
    'received_this_month', (select coalesce(sum(amount), 0) from billing_payments where status = 'paid' and paid_at >= date_trunc('month', now())),
    'orders_waiting', (select count(*) from billing_orders where status = 'submitted'),
    'tickets_open', (select count(*) from support_tickets where status in ('open', 'answered', 'waiting')),
    'tickets_unread', (select count(*) from support_tickets where staff_unread and status <> 'closed'),
    'cancellations', (select count(*) from company_billing where cancel_note is not null),
    'companies', (select count(*) from companies),
    'people', (select count(distinct lower(email)) from members where active and not pending),
    'new_companies_30d', (select count(*) from companies where created_at > now() - interval '30 days'));
end $$;

create or replace function public.console_orders(p_status text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('billing');
  return (select coalesce(jsonb_agg(public.beyond_order_row(id, true) order by created_at desc), '[]'::jsonb)
          from (select id, created_at from billing_orders where p_status is null or p_status = '' or status = p_status
                order by created_at desc limit 200) x);
end $$;

create or replace function public.console_order_decide(p_order bigint, p_approve boolean, p_note text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare o billing_orders; r jsonb;
begin
  perform public.beyond_require_staff('approve');
  select * into o from billing_orders where id = p_order for update;
  if o.id is null then raise exception 'order not found'; end if;
  if coalesce(p_approve, false) then
    r := public.beyond_order_approve(p_order, p_note, null);
  else
    if o.status not in ('open', 'submitted') then raise exception 'this order was already decided'; end if;
    if coalesce(trim(p_note), '') = '' then raise exception 'write the reason (the customer sees it)'; end if;
    update billing_orders set status = 'rejected', review_note = trim(p_note), reviewed_by = public.beyond_caller(), reviewed_at = now(),
           updated_at = now() where id = p_order;
    r := public.beyond_order_row(p_order, true);
  end if;
  perform public.beyond_log(case when p_approve then 'order.approve' else 'order.reject' end, o.ref,
                            jsonb_build_object('amount', o.amount, 'note', p_note));
  return r;
end $$;

create or replace function public.console_tickets(p_status text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('support');
  return (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'ref', ref, 'company_id', company_id, 'company_name', company_name,
            'email', email, 'app_user', app_user, 'kind', kind, 'subject', subject, 'priority', priority, 'status', status,
            'assignee', assignee, 'unread', staff_unread, 'files', jsonb_array_length(files), 'created_at', created_at, 'updated_at', updated_at,
            'messages', (select count(*) from support_messages m where m.ticket_id = t.id)) order by staff_unread desc, updated_at desc), '[]'::jsonb)
          from (select * from support_tickets where (coalesce(p_status, '') = '' and status not in ('closed'))
                  or status = p_status order by updated_at desc limit 300) t);
end $$;

create or replace function public.console_ticket_set(p_ticket bigint, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('support');
  update support_tickets set
    status   = coalesce(nullif(p->>'status', ''), status),
    priority = coalesce(nullif(p->>'priority', ''), priority),
    assignee = case when p ? 'assignee' then nullif(p->>'assignee', '') else assignee end,
    customer_unread = customer_unread or (p ? 'status' and p->>'status' in ('resolved', 'closed')),
    updated_at = now()
  where id = p_ticket;
  if not found then raise exception 'ticket not found'; end if;
  perform public.beyond_log('ticket.set', (select ref from support_tickets where id = p_ticket), p);
  return public.support_get(p_ticket);
end $$;

-- Payment methods
create or replace function public.console_methods() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('settings');
  return (select coalesce(jsonb_agg(to_jsonb(m) order by sort, id), '[]'::jsonb) from billing_methods m);
end $$;

create or replace function public.console_method_save(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare mid bigint := nullif(p->>'id', '')::bigint;
begin
  perform public.beyond_require_staff('settings');
  if coalesce(trim(p->>'name'), '') = '' then raise exception 'name the payment method'; end if;
  if mid is null then
    insert into billing_methods(kind, name, name_ar, details, instructions, instructions_ar, needs_proof, active, sort)
    values (coalesce(nullif(p->>'kind', ''), 'instapay'), trim(p->>'name'), nullif(trim(coalesce(p->>'name_ar', '')), ''),
            coalesce(p->>'details', ''), coalesce(p->>'instructions', ''), coalesce(p->>'instructions_ar', ''),
            coalesce((p->>'needs_proof')::boolean, true), coalesce((p->>'active')::boolean, true), coalesce((p->>'sort')::int, 0))
    returning id into mid;
  else
    update billing_methods set kind = coalesce(nullif(p->>'kind', ''), kind), name = trim(p->>'name'),
      name_ar = nullif(trim(coalesce(p->>'name_ar', '')), ''), details = coalesce(p->>'details', details),
      instructions = coalesce(p->>'instructions', instructions), instructions_ar = coalesce(p->>'instructions_ar', instructions_ar),
      needs_proof = coalesce((p->>'needs_proof')::boolean, needs_proof), active = coalesce((p->>'active')::boolean, active),
      sort = coalesce((p->>'sort')::int, sort)
    where id = mid;
  end if;
  perform public.beyond_log('method.save', p->>'name', p - 'details');
  return public.console_methods();
end $$;

create or replace function public.console_method_delete(p_id bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('settings');
  if exists (select 1 from billing_orders where method_id = p_id) then
    update billing_methods set active = false where id = p_id;           -- used by orders: kept, switched off
  else
    delete from billing_methods where id = p_id;
  end if;
  perform public.beyond_log('method.delete', p_id::text);
  return public.console_methods();
end $$;

-- Announcements
create or replace function public.console_notices() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('notices');
  return (select coalesce(jsonb_agg(to_jsonb(n) order by starts_at desc), '[]'::jsonb) from platform_notices n);
end $$;

create or replace function public.console_notice_save(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare nid bigint := nullif(p->>'id', '')::bigint;
begin
  perform public.beyond_require_staff('notices');
  if coalesce(trim(p->>'title'), '') = '' then raise exception 'write the title'; end if;
  if p ? 'delete' and (p->>'delete')::boolean and nid is not null then
    delete from platform_notices where id = nid;
  elsif nid is null then
    insert into platform_notices(title, title_ar, body, body_ar, level, audience, starts_at, ends_at, active, created_by)
    values (trim(p->>'title'), nullif(trim(coalesce(p->>'title_ar', '')), ''), coalesce(p->>'body', ''), nullif(trim(coalesce(p->>'body_ar', '')), ''),
            coalesce(nullif(p->>'level', ''), 'info'), coalesce(nullif(p->>'audience', ''), 'all'),
            coalesce(nullif(p->>'starts_at', '')::timestamptz, now()), nullif(p->>'ends_at', '')::timestamptz,
            coalesce((p->>'active')::boolean, true), public.beyond_caller());
  else
    update platform_notices set title = trim(p->>'title'), title_ar = nullif(trim(coalesce(p->>'title_ar', '')), ''),
      body = coalesce(p->>'body', ''), body_ar = nullif(trim(coalesce(p->>'body_ar', '')), ''),
      level = coalesce(nullif(p->>'level', ''), level), audience = coalesce(nullif(p->>'audience', ''), audience),
      starts_at = coalesce(nullif(p->>'starts_at', '')::timestamptz, starts_at), ends_at = nullif(p->>'ends_at', '')::timestamptz,
      active = coalesce((p->>'active')::boolean, active)
    where id = nid;
  end if;
  perform public.beyond_log('notice.save', p->>'title');
  return public.console_notices();
end $$;

-- Team: only full-access staff manage it; at least one full-access person remains
create or replace function public.console_staff() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_owner();
  return (select coalesce(jsonb_agg(to_jsonb(a) order by a.perms is not null, a.email), '[]'::jsonb) from platform_admins a);
end $$;

create or replace function public.console_staff_save(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare e text := lower(trim(coalesce(p->>'email', '')));
begin
  perform public.beyond_require_owner();
  if e !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' then raise exception 'enter the person''s sign-in email'; end if;
  if coalesce((p->>'delete')::boolean, false) then
    delete from platform_admins where lower(email) = e;
  else
    insert into platform_admins(email, name, perms, active, added_by)
    values (e, nullif(trim(coalesce(p->>'name', '')), ''), case when coalesce((p->>'full')::boolean, false) then null else coalesce(p->'perms', '{}'::jsonb) end,
            coalesce((p->>'active')::boolean, true), public.beyond_caller())
    on conflict (email) do update set name = excluded.name, perms = excluded.perms, active = excluded.active;
  end if;
  if not exists (select 1 from platform_admins where perms is null and active) then
    raise exception 'at least one person must keep full access';
  end if;
  perform public.beyond_log('staff.save', e, p - 'email');
  return public.console_staff();
end $$;

create or replace function public.console_log(p_limit int) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_owner();
  return (select coalesce(jsonb_agg(to_jsonb(l) order by id desc), '[]'::jsonb)
          from (select * from platform_log order by id desc limit least(coalesce(p_limit, 200), 1000)) l);
end $$;

-- the existing owner functions, by area
create or replace function public.billing_admin_overview() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('customers');
  return jsonb_build_object(
    'config', (select to_jsonb(c) from billing_config c where id = 1),
    'companies', public.console_companies(),
    'mrr', (select coalesce(sum(case when p.months = 12 then p.amount / 12 else p.amount end), 0) from billing_payments p
            where p.status = 'paid' and p.period_end > now() and p.period_start <= now()));
end $$;

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
  end if;
  if p ? 'enabled' and not public.beyond_staff_can('approve') then
    raise exception 'switching billing on or off needs the approve permission' using errcode = '42501';
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
    updated_at = now(), updated_by = public.beyond_caller()
  where id = 1;
  perform public.beyond_log('settings.save', null, p - 'plans' || case when p ? 'plans' then '{"plans": "changed"}'::jsonb else '{}'::jsonb end);
  return (select to_jsonb(c) from billing_config c where id = 1);
end $$;

create or replace function public.billing_record_payment(p_company uuid, p_plan text, p_months int, p_extra_users int,
  p_amount numeric, p_reference text, p_note text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare pid bigint;
begin
  perform public.beyond_require_staff('approve');
  if public.beyond_plan(p_plan) is null then raise exception 'unknown plan %', p_plan; end if;
  insert into billing_payments(company_id, plan, months, extra_users, amount, currency, provider, reference, note, created_by)
  values (p_company, p_plan, coalesce(p_months, 1), coalesce(p_extra_users, 0),
          coalesce(p_amount, public.beyond_price(p_plan, p_months, p_extra_users)),
          (select currency from billing_config where id = 1), 'manual', nullif(trim(coalesce(p_reference, '')), ''), p_note, public.beyond_caller())
  returning id into pid;
  perform public.beyond_log('payment.record', p_company::text, jsonb_build_object('plan', p_plan, 'months', p_months, 'amount', p_amount));
  return public.beyond_apply_payment(pid);
end $$;

create or replace function public.billing_set_company(p_company uuid, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('approve');
  insert into company_billing(company_id) values (p_company) on conflict (company_id) do nothing;
  update company_billing set
    comped      = coalesce((p->>'comped')::boolean, comped),
    trial_ends  = case when p ? 'trial_ends' then nullif(p->>'trial_ends', '')::timestamptz else trial_ends end,
    paid_until  = case when p ? 'paid_until' then nullif(p->>'paid_until', '')::timestamptz else paid_until end,
    plan        = coalesce(nullif(p->>'plan', ''), plan),
    extra_users = coalesce((p->>'extra_users')::int, extra_users),
    cancel_note = case when coalesce((p->>'clear_cancel')::boolean, false) then null else cancel_note end,
    updated_at = now()
  where company_id = p_company;
  perform public.beyond_log('company.set', p_company::text, p);
  return public.beyond_billing_state(p_company);
end $$;

-- ================================================================== who may call what
revoke all on function public.beyond_staff(), public.beyond_staff_can(text), public.beyond_require_staff(text), public.beyond_log(text, text, jsonb),
  public.beyond_order_price(text, int, jsonb), public.beyond_require_admin_all(jsonb), public.beyond_order_row(bigint, boolean),
  public.beyond_order_approve(bigint, text, text), public.beyond_clean_files(jsonb, text), public.beyond_ticket_access(bigint),
  public.beyond_apply_payment(bigint), public.beyond_billing_state(uuid)
  from public, anon, authenticated;
revoke all on function public.billing_paid(text, text, bigint, boolean) from public, anon, authenticated;
grant execute on function public.billing_paid(text, text, bigint, boolean) to service_role;
grant execute on function public.billing_order_quote(text, int, jsonb), public.billing_order_open(text, int, jsonb, bigint),
  public.billing_order_submit(bigint, jsonb, text, numeric, text), public.billing_order_cancel(bigint), public.billing_my_orders(),
  public.billing_status(uuid), public.billing_cancel(uuid, text),
  public.support_open(uuid, text, text, text, jsonb, jsonb, jsonb, text), public.support_get(bigint), public.support_reply(bigint, text, jsonb, boolean),
  public.support_close(bigint, boolean), public.support_mine(uuid),
  public.console_me(), public.console_companies(), public.console_company(uuid), public.console_overview(date, date, text),
  public.console_orders(text), public.console_order_decide(bigint, boolean, text), public.console_tickets(text), public.console_ticket_set(bigint, jsonb),
  public.console_methods(), public.console_method_save(jsonb), public.console_method_delete(bigint),
  public.console_notices(), public.console_notice_save(jsonb), public.console_staff(), public.console_staff_save(jsonb), public.console_log(int),
  public.beyond_is_owner(), public.billing_admin_overview(), public.billing_set_config(jsonb),
  public.billing_record_payment(uuid, text, int, int, numeric, text, text), public.billing_set_company(uuid, jsonb)
  to authenticated;
revoke all on function public.billing_order_quote(text, int, jsonb), public.billing_order_open(text, int, jsonb, bigint),
  public.billing_order_submit(bigint, jsonb, text, numeric, text), public.billing_order_cancel(bigint), public.billing_my_orders(),
  public.support_open(uuid, text, text, text, jsonb, jsonb, jsonb, text), public.support_get(bigint), public.support_reply(bigint, text, jsonb, boolean),
  public.support_close(bigint, boolean), public.support_mine(uuid),
  public.console_me(), public.console_companies(), public.console_company(uuid), public.console_overview(date, date, text),
  public.console_orders(text), public.console_order_decide(bigint, boolean, text), public.console_tickets(text), public.console_ticket_set(bigint, jsonb),
  public.console_methods(), public.console_method_save(jsonb), public.console_method_delete(bigint),
  public.console_notices(), public.console_notice_save(jsonb), public.console_staff(), public.console_staff_save(jsonb), public.console_log(int)
  from public, anon;

-- ================================================================== files (private Storage buckets)
-- billing-proofs/<customer email>/<name>   pictures of transfers: the customer who uploaded them, and billing staff
-- support-files/<customer email>/<name>    customers' attachments: their uploader and support staff
-- support-files/staff/<name>               the team's attachments: support staff, and the customer of the ticket they are on
insert into storage.buckets(id, name, public, file_size_limit, allowed_mime_types)
values ('billing-proofs', 'billing-proofs', false, 10485760, array['image/png', 'image/jpeg', 'image/webp', 'image/heic', 'application/pdf']),
       ('support-files', 'support-files', false, 52428800,
        array['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/heic', 'video/mp4', 'video/webm', 'video/quicktime',
              'application/pdf', 'text/plain', 'application/json', 'application/zip',
              'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'])
on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit, allowed_mime_types = excluded.allowed_mime_types;

-- is this staff file attached to a ticket the caller may see?
create or replace function public.beyond_staff_file_visible(p_path text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from support_messages m where not m.internal
                 and m.files @> jsonb_build_array(jsonb_build_object('path', p_path))
                 and public.beyond_ticket_access(m.ticket_id) is not null)
$$;
revoke all on function public.beyond_staff_file_visible(text) from public, anon;
grant execute on function public.beyond_staff_file_visible(text) to authenticated;
grant execute on function public.beyond_staff_can(text) to authenticated;   -- used inside the storage rules below

drop policy if exists "beyond proofs upload" on storage.objects;
create policy "beyond proofs upload" on storage.objects for insert to authenticated
  with check (bucket_id = 'billing-proofs' and (storage.foldername(name))[1] = public.beyond_caller());
drop policy if exists "beyond proofs read" on storage.objects;
create policy "beyond proofs read" on storage.objects for select to authenticated
  using (bucket_id = 'billing-proofs' and ((storage.foldername(name))[1] = public.beyond_caller() or public.beyond_staff_can('billing')));

drop policy if exists "beyond support upload" on storage.objects;
create policy "beyond support upload" on storage.objects for insert to authenticated
  with check (bucket_id = 'support-files' and ((storage.foldername(name))[1] = public.beyond_caller()
              or ((storage.foldername(name))[1] = 'staff' and public.beyond_staff_can('support'))));
drop policy if exists "beyond support read" on storage.objects;
create policy "beyond support read" on storage.objects for select to authenticated
  using (bucket_id = 'support-files' and ((storage.foldername(name))[1] = public.beyond_caller() or public.beyond_staff_can('support')
         or ((storage.foldername(name))[1] = 'staff' and public.beyond_staff_file_visible(name))));
