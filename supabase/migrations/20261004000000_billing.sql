-- Paid subscriptions for the hosted service: a free trial for every new company, then a monthly (or yearly) plan.
--
-- Nothing changes until the platform owner switches billing on (billing_set_config enabled = true). From then on:
--   * every company gets a trial of trial_days, counted from when it was created or from when billing was switched on,
--     whichever is later (so companies that already exist are not cut off on day one);
--   * a payment (card / wallet through Paymob, or recorded by the owner for InstaPay / bank transfer / cash) extends
--     paid_until by the months paid for;
--   * when the trial or the paid period has ended (plus grace_days), the company is "expired": its people can still
--     sign in, open, print and export everything, but no new change is accepted (append_op refuses it) until someone pays.
--     Nothing is deleted.
-- The platform owner(s) are listed in platform_admins (add yourself once, in the SQL editor:
--   insert into public.platform_admins(email) values ('you@example.com');  ).

-- ------------------------------------------------------------------ tables
create table if not exists public.platform_admins (
  email text primary key
);

create table if not exists public.billing_config (
  id             int primary key default 1 check (id = 1),
  enabled        boolean not null default false,
  enabled_at     timestamptz,
  trial_days     int not null default 15 check (trial_days between 0 and 365),
  grace_days     int not null default 3 check (grace_days between 0 and 60),
  currency       text not null default 'EGP',
  trial_plan     text not null default 'business',
  yearly_months  int not null default 10 check (yearly_months between 1 and 12),   -- a year costs this many months
  extra_user     numeric not null default 150,                                    -- per extra user per month
  plans          jsonb not null default '[
    {"code": "starter",  "name": "Starter",  "name_ar": "البداية",  "price": 690,  "users": 2,  "companies": 1,
     "features": ["Accounting, sales, purchasing, payables & receivables", "ETA e-invoicing", "Excel import / export"]},
    {"code": "business", "name": "Business", "name_ar": "الأعمال",  "price": 1490, "users": 5,  "companies": 1,
     "features": ["Everything in Starter", "Projects / business units, BOQs, stock, purchase orders", "Budgets, fixed assets, loans, custody, bank reconciliation"]},
    {"code": "pro",      "name": "Pro",      "name_ar": "المحترف",  "price": 2990, "users": 15, "companies": 3,
     "features": ["Everything in Business", "Equipment rental, showroom & installments, subscriptions", "Up to 3 companies, priority support"]}
  ]'::jsonb,
  payment_note   text not null default '',     -- shown to customers: InstaPay / bank transfer details for manual payment
  updated_at     timestamptz not null default now(),
  updated_by     text
);
insert into public.billing_config(id) values (1) on conflict (id) do nothing;

create table if not exists public.company_billing (
  company_id   uuid primary key references public.companies(id) on delete cascade,
  plan         text,
  extra_users  int not null default 0 check (extra_users >= 0),
  trial_ends   timestamptz,                 -- set by the owner to extend / shorten a trial (null = automatic)
  paid_until   timestamptz,
  comped       boolean not null default false,   -- free of charge (partners, staff, pilots)
  cancel_note  text,                         -- the customer asked to stop (no renewal)
  updated_at   timestamptz not null default now()
);

create table if not exists public.billing_payments (
  id           bigserial primary key,
  company_id   uuid not null references public.companies(id) on delete cascade,
  plan         text not null,
  months       int not null check (months between 1 and 36),
  extra_users  int not null default 0,
  amount       numeric not null,
  currency     text not null default 'EGP',
  provider     text not null default 'manual',        -- paymob | manual
  reference    text unique,                           -- our order reference (Paymob merchant_order_id) or the transfer ref
  provider_txn text,
  status       text not null default 'pending' check (status in ('pending', 'paid', 'failed', 'refunded')),
  note         text,
  created_by   text,
  created_at   timestamptz not null default now(),
  paid_at      timestamptz
);
create index if not exists billing_payments_company on public.billing_payments(company_id, created_at desc);

alter table public.platform_admins  enable row level security;
alter table public.billing_config   enable row level security;
alter table public.company_billing  enable row level security;
alter table public.billing_payments enable row level security;
revoke all on public.platform_admins, public.billing_config, public.company_billing, public.billing_payments from anon, authenticated;

-- ------------------------------------------------------------------ helpers
create or replace function public.beyond_is_owner() returns boolean
language sql stable security definer set search_path = public as $$
  select public.beyond_caller() <> '' and exists (select 1 from platform_admins where lower(email) = public.beyond_caller())
$$;

create or replace function public.beyond_require_owner() returns void
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.beyond_is_owner() then raise exception 'only the platform owner can do this' using errcode = '42501'; end if;
end $$;

create or replace function public.beyond_plan(p_code text) returns jsonb
language sql stable security definer set search_path = public as $$
  select p from billing_config c, jsonb_array_elements(c.plans) p where p->>'code' = p_code limit 1
$$;

-- Where a company stands. can_write = false means: view / print / export only, until it pays.
create or replace function public.beyond_billing_state(p_company uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare cfg billing_config; co companies; b company_billing; trial_end timestamptz; status text; ends timestamptz;
        plan jsonb; users int; members_n int;
begin
  select * into cfg from billing_config where id = 1;
  if cfg.id is null or not cfg.enabled then
    return jsonb_build_object('enabled', false, 'status', 'free', 'can_write', true);
  end if;
  select * into co from companies where id = p_company;
  select * into b from company_billing where company_id = p_company;
  trial_end := coalesce(b.trial_ends, greatest(co.created_at, coalesce(cfg.enabled_at, co.created_at)) + make_interval(days => cfg.trial_days));
  if coalesce(b.comped, false) then status := 'comped'; ends := null;
  elsif b.paid_until is not null and b.paid_until > now() then status := 'active'; ends := b.paid_until;
  elsif b.paid_until is not null and b.paid_until + make_interval(days => cfg.grace_days) > now() and b.paid_until >= trial_end then
    status := 'past_due'; ends := b.paid_until + make_interval(days => cfg.grace_days);
  elsif trial_end > now() then status := 'trial'; ends := trial_end;
  else status := 'expired'; ends := greatest(trial_end, coalesce(b.paid_until, trial_end));
  end if;
  plan := public.beyond_plan(coalesce(case when status = 'trial' then null else b.plan end, cfg.trial_plan));
  if plan is null then plan := cfg.plans -> 0; end if;
  users := coalesce((plan->>'users')::int, 1) + case when status = 'trial' then 0 else coalesce(b.extra_users, 0) end;
  select count(*) into members_n from members where company_id = p_company and active and not pending;
  return jsonb_build_object(
    'enabled', true, 'status', status, 'can_write', status <> 'expired',
    'ends', ends, 'days_left', case when ends is null then null else greatest(0, ceil(extract(epoch from ends - now()) / 86400))::int end,
    'trial_ends', trial_end, 'paid_until', b.paid_until, 'plan', plan->>'code', 'plan_name', plan->>'name',
    'users_allowed', case when status = 'comped' then null else users end, 'users', members_n,
    'extra_users', coalesce(b.extra_users, 0), 'cancel_note', b.cancel_note,
    'currency', cfg.currency, 'grace_days', cfg.grace_days);
end $$;

-- The price of a plan for a number of months (yearly = yearly_months months' price) plus extra users.
create or replace function public.beyond_price(p_plan text, p_months int, p_extra_users int) returns numeric
language plpgsql stable security definer set search_path = public as $$
declare cfg billing_config; plan jsonb; m int := coalesce(p_months, 1); per numeric;
begin
  select * into cfg from billing_config where id = 1;
  plan := public.beyond_plan(p_plan);
  if plan is null then raise exception 'unknown plan %', p_plan; end if;
  if m not in (1, 12) then raise exception 'pay for 1 month or 12 months'; end if;
  if coalesce(p_extra_users, 0) < 0 or coalesce(p_extra_users, 0) > 500 then raise exception 'extra users must be 0 to 500'; end if;
  per := (plan->>'price')::numeric + cfg.extra_user * coalesce(p_extra_users, 0);
  return round(per * case when m = 12 then cfg.yearly_months else 1 end, 2);
end $$;

-- Extend a company's paid period by a payment (called once per paid payment; idempotent through billing_payments.status).
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
  update company_billing set plan = p.plan, extra_users = p.extra_users, cancel_note = null,
         paid_until = start_at + make_interval(months => p.months), updated_at = now()
   where company_id = p.company_id;
  update billing_payments set status = 'paid', paid_at = now() where id = p.id;
  return public.beyond_billing_state(p.company_id);
end $$;

-- ------------------------------------------------------------------ for everyone (before signing in, too)
create or replace function public.billing_public() returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object('enabled', enabled, 'trial_days', trial_days, 'currency', currency, 'plans', plans,
                            'extra_user', extra_user, 'yearly_months', yearly_months, 'payment_note', payment_note)
  from billing_config where id = 1
$$;
grant execute on function public.billing_public() to anon, authenticated;

-- ------------------------------------------------------------------ for a company's people
create or replace function public.billing_status(p_company uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare m members;
begin
  m := public.beyond_member(p_company);
  if m.email is null then raise exception 'not a member of this company' using errcode = '42501'; end if;
  return public.beyond_billing_state(p_company) || jsonb_build_object(
    'is_admin', m.is_admin, 'is_owner', public.beyond_is_owner(),
    'payments', case when m.is_admin then (select coalesce(jsonb_agg(jsonb_build_object(
        'id', id, 'date', coalesce(paid_at, created_at), 'plan', plan, 'months', months, 'amount', amount, 'currency', currency,
        'provider', provider, 'status', status, 'reference', reference) order by created_at desc), '[]'::jsonb)
      from (select * from billing_payments where company_id = p_company order by created_at desc limit 24) x) end);
end $$;

-- What the next payment would cost (the checkout uses the same function, so the price is decided here, not in the browser).
create or replace function public.billing_quote(p_company uuid, p_plan text, p_months int, p_extra_users int) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare m members; members_n int; plan jsonb;
begin
  m := public.beyond_member(p_company);
  if m.email is null or not m.is_admin then raise exception 'only administrators of this company can pay for it' using errcode = '42501'; end if;
  plan := public.beyond_plan(p_plan);
  if plan is null then raise exception 'unknown plan %', p_plan; end if;
  select count(*) into members_n from members where company_id = p_company and active and not pending;
  if members_n > (plan->>'users')::int + coalesce(p_extra_users, 0) then
    raise exception 'this company has % active people — choose a bigger plan or add % extra user(s)', members_n,
      members_n - (plan->>'users')::int using errcode = '22023';
  end if;
  return jsonb_build_object('plan', p_plan, 'months', coalesce(p_months, 1), 'extra_users', coalesce(p_extra_users, 0),
    'amount', public.beyond_price(p_plan, p_months, p_extra_users),
    'currency', (select currency from billing_config where id = 1), 'email', public.beyond_caller());
end $$;

-- The customer does not want to renew: recorded for the owner; access simply ends when the paid period does.
create or replace function public.billing_cancel(p_company uuid, p_note text) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_admin(p_company);
  insert into company_billing(company_id, cancel_note) values (p_company, coalesce(nullif(trim(p_note), ''), 'cancelled'))
  on conflict (company_id) do update set cancel_note = excluded.cancel_note, updated_at = now();
  return public.beyond_billing_state(p_company);
end $$;

grant execute on function public.billing_status(uuid), public.billing_quote(uuid, text, int, int), public.billing_cancel(uuid, text)
  to authenticated;

-- ------------------------------------------------------------------ for the platform owner
create or replace function public.billing_admin_overview() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_owner();
  return jsonb_build_object(
    'config', (select to_jsonb(c) from billing_config c where id = 1),
    'companies', (select coalesce(jsonb_agg(jsonb_build_object(
        'id', c.id, 'name', c.name, 'created_at', c.created_at, 'created_by', c.created_by,
        'state', public.beyond_billing_state(c.id), 'comped', coalesce(b.comped, false),
        'last_payment', (select jsonb_build_object('date', coalesce(paid_at, created_at), 'amount', amount, 'months', months, 'provider', provider)
                         from billing_payments p where p.company_id = c.id and p.status = 'paid' order by paid_at desc limit 1),
        'paid_total', (select coalesce(sum(amount), 0) from billing_payments p where p.company_id = c.id and p.status = 'paid'))
        order by c.created_at desc), '[]'::jsonb)
      from companies c left join company_billing b on b.company_id = c.id),
    'mrr', (select coalesce(sum(case when p.months = 12 then p.amount / 12 else p.amount end), 0) from billing_payments p
            join company_billing b on b.company_id = p.company_id
            where p.status = 'paid' and b.paid_until > now()
              and p.id = (select max(id) from billing_payments q where q.company_id = p.company_id and q.status = 'paid')));
end $$;

create or replace function public.billing_set_config(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare cfg billing_config;
begin
  perform public.beyond_require_owner();
  select * into cfg from billing_config where id = 1;
  if p ? 'plans' then
    if jsonb_typeof(p->'plans') <> 'array' or jsonb_array_length(p->'plans') = 0 then raise exception 'add at least one plan'; end if;
    if exists (select 1 from jsonb_array_elements(p->'plans') x
               where coalesce(x->>'code', '') = '' or (x->>'price')::numeric < 0 or coalesce((x->>'users')::int, 0) < 1) then
      raise exception 'every plan needs a code, a price and at least one user';
    end if;
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
    plans         = coalesce(p->'plans', plans),
    payment_note  = coalesce(p->>'payment_note', payment_note),
    updated_at = now(), updated_by = public.beyond_caller()
  where id = 1;
  return (select to_jsonb(c) from billing_config c where id = 1);
end $$;

-- Record a payment received outside the card checkout (InstaPay, bank transfer, cash, cheque) — extends the paid period.
create or replace function public.billing_record_payment(p_company uuid, p_plan text, p_months int, p_extra_users int,
  p_amount numeric, p_reference text, p_note text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare pid bigint;
begin
  perform public.beyond_require_owner();
  if public.beyond_plan(p_plan) is null then raise exception 'unknown plan %', p_plan; end if;
  insert into billing_payments(company_id, plan, months, extra_users, amount, currency, provider, reference, note, created_by)
  values (p_company, p_plan, coalesce(p_months, 1), coalesce(p_extra_users, 0),
          coalesce(p_amount, public.beyond_price(p_plan, p_months, p_extra_users)),
          (select currency from billing_config where id = 1), 'manual', nullif(trim(coalesce(p_reference, '')), ''), p_note, public.beyond_caller())
  returning id into pid;
  return public.beyond_apply_payment(pid);
end $$;

-- Extend a trial, give a company free use, or correct its paid date.
create or replace function public.billing_set_company(p_company uuid, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_owner();
  insert into company_billing(company_id) values (p_company) on conflict (company_id) do nothing;
  update company_billing set
    comped      = coalesce((p->>'comped')::boolean, comped),
    trial_ends  = case when p ? 'trial_ends' then nullif(p->>'trial_ends', '')::timestamptz else trial_ends end,
    paid_until  = case when p ? 'paid_until' then nullif(p->>'paid_until', '')::timestamptz else paid_until end,
    plan        = coalesce(nullif(p->>'plan', ''), plan),
    extra_users = coalesce((p->>'extra_users')::int, extra_users),
    updated_at = now()
  where company_id = p_company;
  return public.beyond_billing_state(p_company);
end $$;

grant execute on function public.beyond_is_owner(), public.billing_admin_overview(), public.billing_set_config(jsonb),
  public.billing_record_payment(uuid, text, int, int, numeric, text, text), public.billing_set_company(uuid, jsonb) to authenticated;

-- ------------------------------------------------------------------ for the checkout (edge function, service role only)
create or replace function public.billing_open_payment(p_company uuid, p_plan text, p_months int, p_extra_users int,
  p_reference text, p_email text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare pid bigint; amt numeric;
begin
  amt := public.beyond_price(p_plan, p_months, p_extra_users);
  insert into billing_payments(company_id, plan, months, extra_users, amount, currency, provider, reference, created_by)
  values (p_company, p_plan, p_months, coalesce(p_extra_users, 0), amt, (select currency from billing_config where id = 1),
          'paymob', p_reference, p_email)
  returning id into pid;
  return jsonb_build_object('id', pid, 'amount', amt);
end $$;

-- A confirmed card / wallet payment: amount must match what was quoted; applied once.
create or replace function public.billing_paid(p_reference text, p_txn text, p_amount_cents bigint, p_success boolean) returns jsonb
language plpgsql security definer set search_path = public as $$
declare p billing_payments;
begin
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

revoke all on function public.billing_open_payment(uuid, text, int, int, text, text), public.billing_paid(text, text, bigint, boolean),
  public.beyond_apply_payment(bigint), public.beyond_billing_state(uuid), public.beyond_price(text, int, int)
  from public, anon, authenticated;

-- ------------------------------------------------------------------ enforcement
-- Changes are refused once a company has expired (reading the log, snapshots and exports keep working).
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

-- A company cannot have more active people than its plan allows (adding, approving or re-activating someone).
create or replace function public.beyond_seat_check() returns trigger
language plpgsql security definer set search_path = public as $$
declare st jsonb; n int;
begin
  if not new.active or new.pending then return new; end if;
  if tg_op = 'UPDATE' and old.active and not old.pending then return new; end if;
  st := public.beyond_billing_state(new.company_id);
  if not (st->>'enabled')::boolean or st->>'users_allowed' is null then return new; end if;
  select count(*) into n from members where company_id = new.company_id and active and not pending and lower(email) <> lower(new.email);
  if n + 1 > (st->>'users_allowed')::int then
    raise exception 'the % plan includes % user(s) — add extra users in Settings → Subscription, or deactivate someone first',
      coalesce(st->>'plan_name', 'current'), st->>'users_allowed' using errcode = 'P0402';
  end if;
  return new;
end $$;
drop trigger if exists members_seat_check on public.members;
create trigger members_seat_check before insert or update of active, pending on public.members
  for each row execute function public.beyond_seat_check();
