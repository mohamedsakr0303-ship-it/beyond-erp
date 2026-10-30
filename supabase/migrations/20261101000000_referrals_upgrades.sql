-- Referrals and upgrades.
--
-- Referrals: every person has a share link (beyonderp.org/?ref=CODE). Someone who opens it and signs up is linked to
-- whoever shared it ("joined"), unless they paid before, already came through another link, or it is their own link.
--   * The invited person gets friend_pct % off their first payment (once).
--   * When the invited person completes a payment, the person who shared earns one reward: first_pct % off their own
--     first subscription, or upgrade_pct % off an upgrade of a company that is already subscribed.
--   * Discounts never add up: a payment gets the largest of the company's standing discount, the invited person's
--     discount and one reward. Rewards wait for the next payment they apply to (renewals keep them for later).
-- Upgrades: a company in a paid period can move to a bigger plan, add sections, the assistant, support or users at any
-- time and pays only the difference for the days left in its current period (yearly periods at the yearly rate). The
-- renewal then runs at the new price.

alter table public.billing_config add column if not exists referral jsonb not null
  default '{"on": true, "friend_pct": 20, "first_pct": 20, "upgrade_pct": 30}'::jsonb;
alter table public.billing_orders add column if not exists kind text not null default 'subscribe';
alter table public.billing_orders add column if not exists discount jsonb;          -- {source, pct, reward_id}
alter table public.billing_payments add column if not exists kind text not null default 'period';
do $$ begin
  alter table public.billing_orders add constraint billing_orders_kind check (kind in ('subscribe', 'upgrade'));
exception when duplicate_object then null; end $$;

create table if not exists public.referral_codes (
  email      text primary key,
  code       text unique not null,
  created_at timestamptz not null default now()
);
create table if not exists public.referrals (
  id         bigserial primary key,
  referrer   text not null,
  referred   text unique not null,                     -- one link per invited person: the first one counts
  code       text not null,
  status     text not null default 'joined' check (status in ('joined', 'paid', 'void')),
  created_at timestamptz not null default now(),
  paid_at    timestamptz,
  order_ref  text,
  seen       boolean not null default false            -- the person who shared has seen that it paid off
);
create index if not exists referrals_referrer on public.referrals(referrer);
create table if not exists public.referral_rewards (
  id          bigserial primary key,
  email       text not null,                           -- who earned it
  referral_id bigint unique references public.referrals(id) on delete set null,
  status      text not null default 'available' check (status in ('available', 'used', 'void')),
  created_at  timestamptz not null default now(),
  used_at     timestamptz,
  order_id    bigint,
  pct         numeric                                  -- what it gave when used
);
create index if not exists referral_rewards_email on public.referral_rewards(email, status);
alter table public.referral_codes enable row level security;
alter table public.referrals enable row level security;
alter table public.referral_rewards enable row level security;

-- the person's own code (made the first time)
create or replace function public.beyond_ref_code(p_email text) returns text
language plpgsql security definer set search_path = public as $$
declare c text; e text := lower(trim(coalesce(p_email, '')));
begin
  if e = '' then return null; end if;
  select code into c from referral_codes where email = e;
  if c is not null then return c; end if;
  loop
    c := upper(substr(translate(md5(random()::text || clock_timestamp()::text || e), '01', ''), 1, 8));   -- no 0 / 1 (read as O / I)
    exit when length(c) = 8 and not exists (select 1 from referral_codes where code = c);
  end loop;
  insert into referral_codes(email, code) values (e, c) on conflict (email) do nothing;
  return (select code from referral_codes where email = e);
end $$;

-- has this person ever completed a payment (an approved order of theirs)?
create or replace function public.beyond_has_paid(p_email text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from billing_orders where lower(email) = lower(p_email) and status = 'approved' and amount > 0)
$$;

-- a.b***@domain: who joined, without showing the whole address
create or replace function public.beyond_mask_email(p_email text) returns text
language sql immutable as $$
  select case when position('@' in coalesce(p_email, '')) = 0 then '***'
    else left(split_part(p_email, '@', 1), 2) || '***@' || split_part(p_email, '@', 2) end
$$;

-- The person opened a share link and is signed in: link them to whoever shared it (once).
create or replace function public.referral_claim(p_code text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare me text := public.beyond_caller(); cfg jsonb; who text; c text := upper(trim(coalesce(p_code, '')));
begin
  if me = '' then raise exception 'sign in first' using errcode = '42501'; end if;
  select referral into cfg from billing_config where id = 1;
  if not coalesce((cfg->>'on')::boolean, true) then return jsonb_build_object('ok', false, 'reason', 'off'); end if;
  select email into who from referral_codes where code = c;
  if who is null then return jsonb_build_object('ok', false, 'reason', 'unknown'); end if;
  if who = me then return jsonb_build_object('ok', false, 'reason', 'own'); end if;
  if exists (select 1 from referrals where referred = me) then return jsonb_build_object('ok', false, 'reason', 'already'); end if;
  if public.beyond_has_paid(me) then return jsonb_build_object('ok', false, 'reason', 'customer'); end if;
  -- the same people under another address: someone already in a company of the person who shared
  if exists (select 1 from members m join companies co on co.id = m.company_id
             where lower(m.email) = me and lower(coalesce(co.created_by, '')) = who) then
    return jsonb_build_object('ok', false, 'reason', 'team');
  end if;
  insert into referrals(referrer, referred, code) values (who, me, c) on conflict (referred) do nothing;
  return jsonb_build_object('ok', true, 'friend_pct', (cfg->>'friend_pct')::numeric);
end $$;

-- The discount a payment by this person gets from the programme (the largest one; never more than one):
-- {source: 'friend' | 'reward', pct, reward_id} or null.
create or replace function public.beyond_ref_discount(p_email text, p_kind text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare cfg jsonb; e text := lower(coalesce(p_email, '')); best jsonb; rid bigint; pct numeric; first_time boolean;
begin
  select referral into cfg from billing_config where id = 1;
  if cfg is null or not coalesce((cfg->>'on')::boolean, true) then return null; end if;
  first_time := not public.beyond_has_paid(e);
  if first_time and exists (select 1 from referrals where referred = e and status = 'joined') then
    best := jsonb_build_object('source', 'friend', 'pct', coalesce((cfg->>'friend_pct')::numeric, 0));
  end if;
  pct := case when p_kind = 'upgrade' then (cfg->>'upgrade_pct')::numeric when first_time then (cfg->>'first_pct')::numeric end;
  if coalesce(pct, 0) > 0 then
    select id into rid from referral_rewards r where r.email = e and r.status = 'available'
       and not exists (select 1 from billing_orders o where o.status in ('open', 'submitted') and (o.discount->>'reward_id')::bigint = r.id)
     order by created_at limit 1;
    if rid is not null and pct > coalesce((best->>'pct')::numeric, 0) then
      best := jsonb_build_object('source', 'reward', 'pct', pct, 'reward_id', rid);
    end if;
  end if;
  return best;
end $$;

-- Order lines at the larger of each company's standing discount and the programme's discount.
create or replace function public.beyond_apply_ref(p_lines jsonb, p_ref jsonb) returns jsonb
language plpgsql immutable as $$
declare x jsonb; out jsonb := '[]'::jsonb; pre numeric; pct numeric := coalesce((p_ref->>'pct')::numeric, 0);
begin
  for x in select v from jsonb_array_elements(p_lines) v loop
    if pct > coalesce((x->>'discount')::numeric, 0) then
      pre := coalesce((x->>'pre')::numeric, (x->>'base')::numeric * coalesce((x->>'pct')::numeric, 100) / 100);
      x := x || jsonb_build_object('list_amount', coalesce(x->'list_amount', to_jsonb(round(pre, 2))), 'discount', pct,
                                   'discount_source', p_ref->>'source', 'amount', round(pre * (100 - pct) / 100, 2));
    end if;
    out := out || x;
  end loop;
  return out;
end $$;

-- ================================================================== what a company pays each month as it stands
create or replace function public.beyond_monthly(p_plan text, p_extra int, p_addons jsonb) returns numeric
language plpgsql stable security definer set search_path = public as $$
declare cfg billing_config; plan jsonb; total numeric; m text;
begin
  select * into cfg from billing_config where id = 1;
  plan := public.beyond_plan(p_plan);
  if plan is null then return 0; end if;
  total := coalesce((plan->>'price')::numeric, 0) + cfg.extra_user * coalesce(p_extra, 0);
  if jsonb_typeof(p_addons->'modules') = 'array' then
    for m in select jsonb_array_elements_text(p_addons->'modules') loop
      total := total + coalesce((cfg.module_prices->>m)::numeric, 0);
    end loop;
  end if;
  if coalesce((p_addons->>'ai')::boolean, false) then total := total + cfg.ai_price; end if;
  if coalesce((p_addons->>'support')::boolean, false) then total := total + cfg.support_price; end if;
  return total;
end $$;

-- Upgrade price: p_lines [{company_id, plan, extra_users, modules: [...], ai, support}] = what each company should have
-- from now on. Each pays (new monthly − current monthly) for the days left in its paid period (÷ 30), at the yearly
-- rate when that period was bought by the year; then its standing discount.
create or replace function public.beyond_upgrade_price(p_lines jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare cfg billing_config; x jsonb; cid uuid; b company_billing; st jsonb; nm text; plan jsonb; cur numeric; nxt numeric; days numeric;
        yearly boolean; factor numeric; pre numeric; disc numeric; amt numeric; total numeric := 0; out jsonb := '[]'::jsonb;
        mods jsonb; mod text; cur_mods jsonb; members_n int; users_cap int;
begin
  select * into cfg from billing_config where id = 1;
  if jsonb_typeof(p_lines) <> 'array' or jsonb_array_length(p_lines) = 0 then raise exception 'choose at least one company'; end if;
  for x in select v from jsonb_array_elements(p_lines) v loop
    cid := (x->>'company_id')::uuid;
    select name into nm from companies where id = cid;
    if nm is null then raise exception 'company not found'; end if;
    st := public.beyond_billing_state(cid);
    select * into b from company_billing where company_id = cid;
    if st->>'billing_status' <> 'active' or b.paid_until is null or b.paid_until <= now() then
      raise exception '"%" is not in a paid period — subscribe it instead', nm using errcode = '22023';
    end if;
    plan := public.beyond_plan(coalesce(nullif(x->>'plan', ''), b.plan));
    if plan is null then raise exception 'unknown plan'; end if;
    if coalesce((plan->>'contact')::boolean, false) then
      raise exception 'contact_us: the % plan is arranged with us directly', plan->>'name' using errcode = 'P0402';
    end if;
    -- sections already paid for stay; new ones are added (a section the new plan includes needs no add-on)
    cur_mods := case when jsonb_typeof(b.addons->'modules') = 'array' then b.addons->'modules' else '[]'::jsonb end;
    mods := '[]'::jsonb;
    for mod in select distinct m from (select jsonb_array_elements_text(cur_mods) m
                                       union select jsonb_array_elements_text(case when jsonb_typeof(x->'modules') = 'array' then x->'modules' else '[]'::jsonb end)) q loop
      if coalesce(plan->>'modules', 'all') = 'all' or (plan->'modules') ? mod or coalesce(cfg.core_modules, '[]'::jsonb) ? mod then continue; end if;
      if not (cfg.module_prices ? mod) then raise exception 'the section "%" cannot be added on its own — choose a plan that includes it', mod; end if;
      mods := mods || to_jsonb(mod);
    end loop;
    select count(*) into members_n from members where company_id = cid and active and not pending;
    users_cap := case when jsonb_typeof(b.overrides->'users') = 'number' then (b.overrides->>'users')::int end;
    if users_cap is null and members_n > coalesce((plan->>'users')::int, 0) + greatest(coalesce((x->>'extra_users')::int, b.extra_users, 0), 0) then
      raise exception '"%" has % active people — add extra users for it', nm, members_n using errcode = '22023';
    end if;
    cur := public.beyond_monthly(b.plan, b.extra_users, b.addons);
    nxt := public.beyond_monthly(plan->>'code', greatest(coalesce((x->>'extra_users')::int, b.extra_users, 0), 0),
             jsonb_build_object('modules', mods,
               'ai', (coalesce((x->>'ai')::boolean, false) or coalesce((b.addons->>'ai')::boolean, false)) and not coalesce((plan->>'ai')::boolean, false),
               'support', (coalesce((x->>'support')::boolean, false) or coalesce((b.addons->>'support')::boolean, false)) and coalesce((plan->>'support')::boolean, true) = false));
    if nxt <= cur then raise exception '"%": choose something more than it has now', nm using errcode = '22023'; end if;
    days := greatest(1, ceil(extract(epoch from b.paid_until - now()) / 86400));
    yearly := coalesce((select months from billing_payments where company_id = cid and status = 'paid' and kind = 'period'
                        order by paid_at desc nulls last limit 1), 1) = 12;
    factor := case when yearly then cfg.yearly_months::numeric / 12 else 1 end;
    pre := round((nxt - cur) * factor * days / 30, 2);
    disc := coalesce(b.discount_pct, 0);
    amt := round(pre * (100 - disc) / 100, 2);
    out := out || jsonb_build_object('company_id', cid, 'name', nm, 'plan', plan->>'code', 'plan_name', plan->>'name',
      'from_plan', b.plan, 'extra_users', greatest(coalesce((x->>'extra_users')::int, b.extra_users, 0), 0), 'modules', mods,
      'ai', (coalesce((x->>'ai')::boolean, false) or coalesce((b.addons->>'ai')::boolean, false)) and not coalesce((plan->>'ai')::boolean, false),
      'support', (coalesce((x->>'support')::boolean, false) or coalesce((b.addons->>'support')::boolean, false)) and coalesce((plan->>'support')::boolean, true) = false,
      'monthly_now', cur, 'monthly_new', nxt, 'days', days, 'until', b.paid_until, 'yearly', yearly,
      'pre', pre, 'base', pre, 'pct', 100, 'discount', disc, 'amount', amt);
    total := total + amt;
  end loop;
  return jsonb_build_object('kind', 'upgrade', 'lines', out, 'amount', total, 'currency', cfg.currency);
end $$;

-- ================================================================== quotes and orders, with the programme's discount
create or replace function public.billing_order_quote(p_plan text, p_months int, p_lines jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare q jsonb; ref jsonb; lines jsonb;
begin
  perform public.beyond_require_admin_all(p_lines);
  q := public.beyond_order_price(p_plan, p_months, p_lines);
  ref := public.beyond_ref_discount(public.beyond_caller(), 'subscribe');
  if ref is null then return q; end if;
  lines := public.beyond_apply_ref(q->'lines', ref);
  -- a bigger standing discount on every company: the programme's discount (and a reward) is not used this time
  if not exists (select 1 from jsonb_array_elements(lines) v where v ? 'discount_source') then return q; end if;
  return q || jsonb_build_object('lines', lines, 'amount', (select coalesce(sum((v->>'amount')::numeric), 0) from jsonb_array_elements(lines) v),
                                 'referral', ref);
end $$;

create or replace function public.billing_upgrade_quote(p_lines jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare q jsonb; ref jsonb; lines jsonb;
begin
  perform public.beyond_require_admin_all(p_lines);
  q := public.beyond_upgrade_price(p_lines);
  ref := public.beyond_ref_discount(public.beyond_caller(), 'upgrade');
  if ref is null then return q; end if;
  lines := public.beyond_apply_ref(q->'lines', ref);
  -- a bigger standing discount on every company: the programme's discount (and a reward) is not used this time
  if not exists (select 1 from jsonb_array_elements(lines) v where v ? 'discount_source') then return q; end if;
  return q || jsonb_build_object('lines', lines, 'amount', (select coalesce(sum((v->>'amount')::numeric), 0) from jsonb_array_elements(lines) v),
                                 'referral', ref);
end $$;

create or replace function public.beyond_order_insert(p_kind text, p_plan text, p_months int, q jsonb, p_method bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare mt billing_methods; oid bigint; r text;
begin
  select * into mt from billing_methods where id = p_method and active;
  if mt.id is null then raise exception 'choose a payment method'; end if;
  update billing_orders set status = 'cancelled', updated_at = now()
   where lower(email) = public.beyond_caller() and status = 'open';
  r := 'BO-' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 8));
  insert into billing_orders(ref, email, plan, months, lines, amount, currency, method_id, method_kind, method_name, kind, discount)
  values (r, public.beyond_caller(), p_plan, p_months, q->'lines', (q->>'amount')::numeric, q->>'currency',
          mt.id, mt.kind, mt.name, p_kind, q->'referral')
  returning id into oid;
  return public.beyond_order_row(oid, false);
end $$;

create or replace function public.billing_order_open(p_plan text, p_months int, p_lines jsonb, p_method bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare q jsonb;
begin
  q := public.billing_order_quote(p_plan, p_months, p_lines);
  return public.beyond_order_insert('subscribe', p_plan, (q->>'months')::int, q, p_method);
end $$;

create or replace function public.billing_upgrade_open(p_lines jsonb, p_method bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare q jsonb;
begin
  q := public.billing_upgrade_quote(p_lines);
  return public.beyond_order_insert('upgrade', coalesce(q->'lines'->0->>'plan', ''), 1, q, p_method);
end $$;

-- ================================================================== approving: periods, upgrades and the programme
create or replace function public.beyond_order_approve(p_order bigint, p_note text, p_txn text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare o billing_orders; x jsonb; i int := 0; pid bigint; rf referrals; b company_billing;
begin
  select * into o from billing_orders where id = p_order for update;
  if o.id is null then raise exception 'order not found'; end if;
  if o.status = 'approved' then return public.beyond_order_row(p_order, true); end if;
  if o.status = 'cancelled' then raise exception 'this order was cancelled'; end if;
  for x in select v from jsonb_array_elements(o.lines) v loop
    i := i + 1;
    if o.kind = 'upgrade' then
      -- the company has the new plan / add-ons from now; its paid period stays as it was
      select * into b from company_billing where company_id = (x->>'company_id')::uuid for update;
      update company_billing set plan = coalesce(x->>'plan', plan), extra_users = coalesce((x->>'extra_users')::int, extra_users),
             addons = jsonb_build_object('modules', coalesce(x->'modules', '[]'::jsonb), 'ai', coalesce((x->>'ai')::boolean, false),
                                         'support', coalesce((x->>'support')::boolean, false)), updated_at = now()
       where company_id = (x->>'company_id')::uuid;
      insert into billing_payments(company_id, company_name, plan, months, extra_users, amount, currency, provider, reference, provider_txn, note,
                                   created_by, order_id, addons, kind, status, paid_at, period_start, period_end)
      values ((x->>'company_id')::uuid, x->>'name', coalesce(x->>'plan', o.plan), 1, coalesce((x->>'extra_users')::int, 0), (x->>'amount')::numeric,
              o.currency, case when o.method_kind = 'card' then 'paymob' else 'manual' end, o.ref || '-' || i, p_txn,
              left('Upgrade · ' || coalesce(o.method_name, '') || coalesce(' · ' || o.proof_ref, ''), 300), o.email, o.id,
              jsonb_build_object('modules', coalesce(x->'modules', '[]'::jsonb), 'ai', coalesce((x->>'ai')::boolean, false),
                                 'support', coalesce((x->>'support')::boolean, false)), 'upgrade', 'paid', now(), now(), b.paid_until);
    else
      insert into billing_payments(company_id, company_name, plan, months, extra_users, amount, currency, provider, reference, provider_txn, note, created_by, order_id, addons)
      values ((x->>'company_id')::uuid, x->>'name', o.plan, o.months, coalesce((x->>'extra_users')::int, 0), (x->>'amount')::numeric, o.currency,
              case when o.method_kind = 'card' then 'paymob' else 'manual' end, o.ref || '-' || i, p_txn,
              left(coalesce(o.method_name, '') || coalesce(' · ' || o.proof_ref, ''), 300), o.email, o.id,
              jsonb_build_object('modules', coalesce(x->'modules', '[]'::jsonb), 'ai', coalesce((x->>'ai')::boolean, false),
                                 'support', coalesce((x->>'support')::boolean, false)))
      returning id into pid;
      perform public.beyond_apply_payment(pid);
    end if;
  end loop;
  update billing_orders set status = 'approved', review_note = nullif(trim(coalesce(p_note, '')), ''), reviewed_by = nullif(public.beyond_caller(), ''),
         reviewed_at = now(), updated_at = now()
   where id = p_order;
  -- the reward it used is spent
  if o.discount->>'source' = 'reward' then
    update referral_rewards set status = 'used', used_at = now(), order_id = o.id, pct = (o.discount->>'pct')::numeric
     where id = (o.discount->>'reward_id')::bigint and status = 'available';
  end if;
  -- an invited person's first completed payment: the person who shared earns a reward
  if o.amount > 0 then
    select * into rf from referrals where referred = lower(o.email) and status = 'joined' for update;
    if rf.id is not null then
      update referrals set status = 'paid', paid_at = now(), order_ref = o.ref, seen = false where id = rf.id;
      insert into referral_rewards(email, referral_id) values (rf.referrer, rf.id) on conflict (referral_id) do nothing;
      perform public.beyond_log('referral.paid', rf.referrer, jsonb_build_object('referred', rf.referred, 'order', o.ref));
    end if;
  end if;
  return public.beyond_order_row(p_order, true);
end $$;

-- ================================================================== the person's own programme page
create or replace function public.referral_my() returns jsonb
language plpgsql security definer set search_path = public as $$
declare me text := public.beyond_caller(); cfg jsonb; r jsonb;
begin
  if me = '' then raise exception 'sign in first' using errcode = '42501'; end if;
  select referral into cfg from billing_config where id = 1;
  r := jsonb_build_object(
    'on', coalesce((cfg->>'on')::boolean, true), 'code', public.beyond_ref_code(me),
    'friend_pct', (cfg->>'friend_pct')::numeric, 'first_pct', (cfg->>'first_pct')::numeric, 'upgrade_pct', (cfg->>'upgrade_pct')::numeric,
    'joined', (select count(*) from referrals where referrer = me and status <> 'void'),
    'paid', (select count(*) from referrals where referrer = me and status = 'paid'),
    'available', (select count(*) from referral_rewards where email = me and status = 'available'),
    'used', (select count(*) from referral_rewards where email = me and status = 'used'),
    'unseen', (select count(*) from referrals where referrer = me and status = 'paid' and not seen),
    'people', (select coalesce(jsonb_agg(jsonb_build_object('who', public.beyond_mask_email(referred), 'status', status,
                 'joined_at', created_at, 'paid_at', paid_at) order by created_at desc), '[]'::jsonb)
               from (select * from referrals where referrer = me and status <> 'void' order by created_at desc limit 200) q),
    'rewards', (select coalesce(jsonb_agg(jsonb_build_object('id', w.id, 'status', w.status, 'earned_at', w.created_at, 'used_at', w.used_at,
                  'pct', w.pct, 'order_ref', (select ref from billing_orders where id = w.order_id),
                  'from', (select public.beyond_mask_email(referred) from referrals where id = w.referral_id)) order by w.created_at desc), '[]'::jsonb)
                from referral_rewards w where w.email = me and w.status <> 'void'),
    'invited', (select jsonb_build_object('by', public.beyond_mask_email(referrer), 'status', status,
                  'discount_left', status = 'joined' and not public.beyond_has_paid(me))
                from referrals where referred = me),
    'next', public.beyond_ref_discount(me, case when public.beyond_has_paid(me) then 'upgrade' else 'subscribe' end));
  update referrals set seen = true where referrer = me and status = 'paid' and not seen;
  return r;
end $$;

-- ================================================================== the owner console
create or replace function public.console_referrals() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('customers');
  return jsonb_build_object(
    'settings', (select referral from billing_config where id = 1),
    'rows', (select coalesce(jsonb_agg(jsonb_build_object('id', r.id, 'referrer', r.referrer, 'referred', r.referred, 'code', r.code,
               'status', r.status, 'joined_at', r.created_at, 'paid_at', r.paid_at, 'order_ref', r.order_ref,
               'reward', (select jsonb_build_object('id', w.id, 'status', w.status, 'pct', w.pct, 'used_at', w.used_at,
                                                    'order_ref', (select ref from billing_orders where id = w.order_id))
                          from referral_rewards w where w.referral_id = r.id)) order by r.created_at desc), '[]'::jsonb)
             from referrals r));
end $$;

-- void a referral (and its reward, unless already used) — for abuse; or bring it back
create or replace function public.console_referral_set(p_id bigint, p_void boolean) returns jsonb
language plpgsql security definer set search_path = public as $$
declare r referrals;
begin
  perform public.beyond_require_staff('approve');
  select * into r from referrals where id = p_id for update;
  if r.id is null then raise exception 'referral not found'; end if;
  if p_void then
    update referrals set status = 'void' where id = p_id;
    update referral_rewards set status = 'void' where referral_id = p_id and status = 'available';
  else
    update referrals set status = case when paid_at is null then 'joined' else 'paid' end where id = p_id;
    update referral_rewards set status = 'available' where referral_id = p_id and status = 'void';
  end if;
  perform public.beyond_log(case when p_void then 'referral.void' else 'referral.restore' end, r.referrer, jsonb_build_object('referred', r.referred));
  return public.console_referrals();
end $$;

create or replace function public.console_referral_settings(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('settings');
  if jsonb_typeof(p) <> 'object' then raise exception 'send the programme settings'; end if;
  if exists (select 1 from jsonb_each(p) e where e.key in ('friend_pct', 'first_pct', 'upgrade_pct')
             and (jsonb_typeof(e.value) <> 'number' or (e.value #>> '{}')::numeric not between 0 and 100)) then
    raise exception 'percentages must be 0 to 100';
  end if;
  update billing_config set referral = referral || (p - array(select k from jsonb_object_keys(p) k where k not in ('on', 'friend_pct', 'first_pct', 'upgrade_pct'))),
         updated_at = now(), updated_by = public.beyond_caller()
   where id = 1;
  perform public.beyond_log('referral.settings', null, p);
  return (select referral from billing_config where id = 1);
end $$;

revoke all on function public.beyond_ref_code(text), public.beyond_has_paid(text), public.beyond_ref_discount(text, text),
  public.beyond_apply_ref(jsonb, jsonb), public.beyond_monthly(text, int, jsonb), public.beyond_upgrade_price(jsonb),
  public.beyond_order_insert(text, text, int, jsonb, bigint) from public, anon, authenticated;
grant execute on function public.referral_claim(text), public.referral_my(), public.billing_upgrade_quote(jsonb),
  public.billing_upgrade_open(jsonb, bigint), public.console_referrals(), public.console_referral_set(bigint, boolean),
  public.console_referral_settings(jsonb) to authenticated;
