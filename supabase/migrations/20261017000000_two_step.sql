-- Two-step sign-in (Supabase Auth MFA, authenticator app). An account that has a verified authenticator reaches the
-- company's books only with a session that passed the second step (aal2): every membership check (beyond_in,
-- beyond_member, beyond_is_member …) compares against beyond_caller(), so returning '' for a one-step session closes
-- them all. Accounts without an authenticator are unchanged.
create or replace function public.beyond_caller() returns text
language sql stable security definer set search_path = public, auth as $$
  select case
    when coalesce(auth.jwt() ->> 'aal', 'aal1') = 'aal2'
      or not exists (select 1 from auth.mfa_factors f where f.user_id = auth.uid() and f.status = 'verified')
    then lower(coalesce(auth.jwt() ->> 'email', ''))
    else '' end
$$;
