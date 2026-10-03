-- New list prices (value-based: a construction / rental / real-estate ERP, not a basic invoicing app).
-- Changes the defaults, and the live settings only while they still hold the first defaults and billing is off,
-- so prices the owner already set by hand are never overwritten.
do $$
declare
  old_plans jsonb := '[
    {"code": "starter",  "name": "Starter",  "name_ar": "البداية",  "price": 690,  "users": 2,  "companies": 1,
     "features": ["Accounting, sales, purchasing, payables & receivables", "ETA e-invoicing", "Excel import / export"]},
    {"code": "business", "name": "Business", "name_ar": "الأعمال",  "price": 1490, "users": 5,  "companies": 1,
     "features": ["Everything in Starter", "Projects / business units, BOQs, stock, purchase orders", "Budgets, fixed assets, loans, custody, bank reconciliation"]},
    {"code": "pro",      "name": "Pro",      "name_ar": "المحترف",  "price": 2990, "users": 15, "companies": 3,
     "features": ["Everything in Business", "Equipment rental, showroom & installments, subscriptions", "Up to 3 companies, priority support"]}
  ]'::jsonb;
  new_plans jsonb := '[
    {"code": "starter",  "name": "Essentials", "name_ar": "الأساسية", "price": 1900, "users": 3,  "companies": 1,
     "features": ["Accounting, sales, purchasing, payables & receivables", "ETA e-invoicing, VAT & withholding", "Statements of account, Excel import / export"]},
    {"code": "business", "name": "Business",   "name_ar": "الأعمال",  "price": 4500, "users": 8,  "companies": 1,
     "features": ["Everything in Essentials", "Projects / business units, BOQs, progress invoices, stock, purchase orders", "Budgets, fixed assets, loans, custody, bank reconciliation, recurring entries"]},
    {"code": "pro",      "name": "Pro",        "name_ar": "المحترف",  "price": 8900, "users": 20, "companies": 3,
     "features": ["Everything in Business", "Equipment rental & scaffold calculator, showroom & installments, service subscriptions", "Up to 3 companies, priority support (same business day)"]}
  ]'::jsonb;
begin
  execute format('alter table public.billing_config alter column plans set default %L::jsonb', new_plans);
  alter table public.billing_config alter column extra_user set default 350;
  update public.billing_config
     set plans = new_plans, extra_user = 350, updated_at = now(), updated_by = 'price update 2026-10'
   where id = 1 and not enabled and plans = old_plans and extra_user = 150;
end $$;
