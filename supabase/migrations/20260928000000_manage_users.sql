-- Used by the "manage-user" edge function (service role only): find a sign-in account by email, so an
-- administrator can create people and set or reset their password from inside Beyond ERP.
create or replace function public.auth_user_id(p_email text) returns uuid
language sql stable security definer set search_path = public, auth as $$
  select id from auth.users where lower(email) = lower(trim(p_email)) limit 1
$$;
revoke execute on function public.auth_user_id(text) from public, anon, authenticated;
grant execute on function public.auth_user_id(text) to service_role;
