-- Plan cards lead with what each plan does for the customer: a one-line outcome under the name, in English and Arabic.
-- Only plans whose line is still empty get one (lines the owner wrote in the console are kept). The Enterprise line
-- added with the plan is replaced only while it is still the one shipped.
update public.billing_config c
   set plans = (select jsonb_agg(case
       when coalesce(p->>'tagline', '') = '' and t.en is not null then p || jsonb_build_object('tagline', t.en, 'tagline_ar', t.ar)
       when p->>'code' = 'enterprise' and p->>'tagline' = 'For larger teams and groups of companies'
         then p || jsonb_build_object('tagline', 'Built around your group, priced around your deal', 'tagline_ar', 'مصممة حول مجموعتك، وسعرها حسب اتفاقك')
       else p end order by o)
       from jsonb_array_elements(c.plans) with ordinality x(p, o)
       left join (values
         ('starter',  'Clean books and on-time invoices from day one', 'دفاتر منضبطة وفواتير في موعدها من اليوم الأول'),
         ('business', 'Know your profit on every project',             'اعرف ربحك في كل مشروع'),
         ('pro',      'Every branch, asset and contract under control', 'كل فرع وأصل وعقد تحت السيطرة')
       ) t(code, en, ar) on t.code = p->>'code'),
       updated_at = now(), updated_by = 'plan taglines'
 where id = 1;
