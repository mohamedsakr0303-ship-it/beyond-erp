-- Owner console: more control over each company and each order.
--   billing_set_company p.trial = 'restart' → a fresh trial from now: p.days days (default: the trial length setting)
--                       p.trial = 'extend'  → the trial (or, while paid, the paid period) lasts p.days days longer
--                       p.trial = 'stop'    → the trial ends now (a company without a paid period becomes read only)
--                       p.discount_pct      → a standing discount (0–100 %) on every order line for this company
--   console_order_amount → change an order's total before approving it (a one-off discount or an agreed price);
--                          the companies in it share the new total in proportion
alter table public.company_billing add column if not exists discount_pct numeric not null default 0;
do $$ begin
  alter table public.company_billing add constraint company_billing_discount_pct check (discount_pct between 0 and 100);
exception when duplicate_object then null; end $$;

-- Everything else billing_set_company already took (comped, trial_ends, paid_until, plan, extra_users, clear_cancel)
-- works as before.
create or replace function public.billing_set_company(p_company uuid, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare days int; st jsonb; act text := coalesce(p->>'trial', '');
begin
  perform public.beyond_require_staff('approve');
  if not exists (select 1 from companies where id = p_company) then raise exception 'company not found'; end if;
  if act not in ('', 'restart', 'extend', 'stop') then raise exception 'unknown trial action'; end if;
  days := coalesce(nullif(p->>'days', '')::int, (select trial_days from billing_config where id = 1), 15);
  if act in ('restart', 'extend') and (days < 1 or days > 3650) then raise exception 'days must be 1 to 3650'; end if;
  if p ? 'discount_pct' and coalesce(nullif(p->>'discount_pct', '')::numeric, 0) not between 0 and 100 then
    raise exception 'the discount must be 0 to 100 %%';
  end if;
  insert into company_billing(company_id) values (p_company) on conflict (company_id) do nothing;
  st := public.beyond_billing_state(p_company);
  -- extending while paid lengthens the paid period; otherwise the trial
  if act = 'extend' and st->>'status' in ('active', 'past_due') then
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
    updated_at = now()
  where company_id = p_company;
  perform public.beyond_log(case act when 'restart' then 'company.trial_restart' when 'extend' then 'company.trial_extend'
                                     when 'extend_paid' then 'company.paid_extend' when 'stop' then 'company.trial_stop' else 'company.set' end,
                            p_company::text, p);
  return public.beyond_billing_state(p_company);
end $$;
grant execute on function public.billing_set_company(uuid, jsonb) to authenticated;

-- order prices take each company's standing discount into account
create or replace function public.beyond_order_price(p_plan text, p_months int, p_lines jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare cfg billing_config; plan jsonb; m int := coalesce(p_months, 1); x jsonb; out jsonb := '[]'::jsonb; total numeric := 0;
        base numeric; extra int; cid uuid; nm text; members_n int; i int := 0; pct numeric; disc numeric; amt numeric;
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
    select coalesce(discount_pct, 0) into disc from company_billing where company_id = cid;
    disc := coalesce(disc, 0);
    amt := round(base * pct / 100 * (100 - disc) / 100, 2);
    out := out || jsonb_build_object('company_id', cid, 'name', nm, 'extra_users', extra, 'base', round(base, 2), 'pct', pct,
                                     'discount', disc, 'amount', amt);
    total := total + amt;
    i := i + 1;
  end loop;
  return jsonb_build_object('plan', p_plan, 'plan_name', plan->>'name', 'months', m, 'lines', out, 'amount', total, 'currency', cfg.currency);
end $$;


-- Change an order's total before it is approved (one-off discount, an agreed price, or what was actually received).
-- The companies in it share the new total in proportion to their list amounts (equally when those are all zero).
create or replace function public.console_order_amount(p_order bigint, p_amount numeric, p_note text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare o billing_orders; old_total numeric; n int; new_lines jsonb := '[]'::jsonb; x jsonb; i int := 0; share numeric; given numeric := 0;
begin
  perform public.beyond_require_staff('approve');
  select * into o from billing_orders where id = p_order for update;
  if o.id is null then raise exception 'order not found'; end if;
  if o.status not in ('open', 'submitted', 'rejected') then raise exception 'this order was already decided'; end if;
  if p_amount is null or p_amount < 0 then raise exception 'the amount must be 0 or more'; end if;
  old_total := o.amount; n := jsonb_array_length(o.lines);
  for x in select v from jsonb_array_elements(o.lines) v loop
    i := i + 1;
    share := case when i = n then round(p_amount, 2) - given
                  when coalesce(old_total, 0) = 0 then round(p_amount / n, 2)
                  else round(p_amount * coalesce((x->>'amount')::numeric, 0) / old_total, 2) end;
    given := given + share;
    new_lines := new_lines || (x || jsonb_build_object('amount', share, 'list_amount', coalesce(x->'list_amount', x->'amount')));
  end loop;
  update billing_orders set lines = new_lines, amount = round(p_amount, 2),
         review_note = coalesce(nullif(trim(coalesce(p_note, '')), ''), review_note), updated_at = now()
   where id = p_order;
  perform public.beyond_log('order.amount', o.ref, jsonb_build_object('from', old_total, 'to', p_amount, 'note', p_note));
  return public.beyond_order_row(p_order, true);
end $$;
revoke all on function public.beyond_order_price(text, int, jsonb) from public, anon, authenticated;
grant execute on function public.console_order_amount(bigint, numeric, text) to authenticated;
