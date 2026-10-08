# Shared workspace on Supabase — setup (all free tiers)

What you get: everyone in the company works on the same books at the same time, from the web app, from the
desktop app, or from the offline web copy — each person with their own sign-in and permissions.

| Piece | Service | Cost |
|---|---|---|
| Shared data, sign-in, access rules | **Supabase** (database + auth) | free plan |
| Code, automatic deployment | **GitHub** (this repository + Actions) | free |
| The web app itself | **Cloudflare Pages** (private repo) or **GitHub Pages** (public repo) | free |

Supabase does not host web apps (its file storage serves HTML as plain text), so the app's page is published by
Cloudflare or GitHub Pages; everything shared lives in Supabase.

## 1 · Create the Supabase project (5 minutes)

1. supabase.com → **New project** (free). Choose a region close to you (e.g. Frankfurt / eu-central-1) and write
   down the **database password**.
2. **Project Settings → API**: copy the **Project URL** (`https://<ref>.supabase.co`) and the **anon public** key.
   The `<ref>` part is the **project ID**.
3. **Authentication → Sign In / Providers → Email**: keep *Email* enabled. "Confirm email" on (safer) means each
   person clicks a link from Supabase once after creating their account.
4. **Account (top-right) → Access Tokens**: create a token for GitHub.
5. **Authentication → URL Configuration** (after step 3, once you know the app's address):
   set **Site URL** to your app's address, e.g. `https://beyond-erp.pages.dev`, and add the same address under
   **Redirect URLs**. This is where the "confirm your email" and "reset password" links bring people back to —
   without it they are sent to `localhost`. (Add the address of every other copy you use, e.g. a static.app site.)

## 2 · Connect GitHub (once)

In this repository: **Settings → Secrets and variables → Actions**

| Kind | Name | Value |
|---|---|---|
| Variable | `SUPABASE_URL` | `https://<ref>.supabase.co` |
| Variable | `SUPABASE_ANON_KEY` | the anon public key (it is public by design; access is protected by sign-in + row-level security) |
| Variable | `SUPABASE_PROJECT_ID` | `<ref>` |
| Secret | `SUPABASE_ACCESS_TOKEN` | the access token from step 1.4 |
| Secret | `SUPABASE_DB_PASSWORD` | the database password from step 1.1 |

Then **Actions → deploy → Run workflow** (or push any change). The workflow runs the tests, creates the workspace
tables and functions in Supabase (`supabase/migrations/`), and builds the web app pre-linked to your project.

Without the two secrets the workflow skips this step (the log says "Supabase secrets not set yet"). Everything else
keeps working; only the newest workspace features wait for it — **chat between companies** and the **AI assistant's
key in the workspace** (`supabase/migrations/20261006000000_chat_links_ai.sql` and the `ai` function). To apply them
by hand instead: Supabase dashboard → SQL Editor → paste that migration file → Run; then deploy the function with the
Supabase CLI (`supabase functions deploy ai --project-ref <ref> --no-verify-jwt`). Until then the assistant uses a key
installed on the device itself.

## 3 · Publish the web app (choose one)

**Cloudflare Pages — free, keeps the repository private (recommended)**
1. dash.cloudflare.com → Workers & Pages → Create → Pages → *Upload assets* → name it `beyond-erp` (upload any file
   once to create it).
2. My Profile → API Tokens → Create token → template *Edit Cloudflare Workers*… or a custom token with
   *Account · Cloudflare Pages · Edit*.
3. In GitHub add secrets `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` (shown on the Cloudflare dashboard's right
   side) and, if you chose another name, variable `CLOUDFLARE_PAGES_PROJECT`.
4. Every push now publishes to `https://beyond-erp.pages.dev` (you can add your own domain there).

**GitHub Pages — free for public repositories**
Make the repository public (it contains no company data — books live in Supabase), set **Settings → Pages →
Source: GitHub Actions**, and add variable `USE_GITHUB_PAGES` = `true`.

Every build is also attached to the workflow run as `beyond-erp-web.zip` (the same app, to upload anywhere or to
open from disk).

## 4 · Create your company

1. Open the published app → **Create an account** with your email → confirm the email → sign in.
2. **Create a new company**: give it a name — you become its administrator. Tick *Start from the books on this
   device* to bring data you imported on that device first ("Use this device without the workspace" → Import & Export),
   otherwise it starts with empty books.
3. **Settings → Workspace (cloud)** shows the company's **invite code**. Colleagues create an account, choose *Join a
   company with an invite code*, and you **Approve** them in the member list — or add them yourself under **Users**
   (username + password, no email needed). Set what each person may see and change under **Users**.
4. More companies: top bar → your name → **Companies** → *New company*. Each company has its own books, members and
   invite code; people can belong to several and switch between them there.

## Adding people (all from inside BeyondERP)

**Users → New**: username, full name, a **sign-in password** (typed twice) and their permissions → **Save**. That
creates their sign-in account on the spot — already confirmed, no emails — and they can sign in with
**username + password** straight away. Email is optional: add it if they want **Sign in with Google** or to reset
their own password by email. Without an email they get an internal placeholder address that is never used for mail.

* Change a password: open the user, type the new one twice, save.
* Block someone: untick **Active** — their sign-in stops working (their history stays).
* Only workspace administrators can do this; it runs through the small `manage-user` function, which holds
  Supabase's secret key on Supabase's side — the key is never in the web page.

People can still create their own account on the sign-in page ("No account? Create one") — an administrator then
adds that email under Users so the app knows who they are.

**Access token expiry**: GitHub needs `SUPABASE_ACCESS_TOKEN` only when something under `supabase/` changes (the
workflow skips the Supabase step otherwise). When you create the token pick the longest expiry offered; if it has
expired, the run says so plainly — create a new one, update the secret, and re-run.

## Sign in with Google (optional, free)

The app shows a **Sign in with Google** button as soon as Google is switched on in Supabase.

1. **Google Cloud Console** (console.cloud.google.com) → create a project (any name).
2. **APIs & Services → OAuth consent screen**: *External*; app name (e.g. "BeyondERP"), your support email,
   developer email. Scopes: leave the defaults (email, profile, openid). Under **Audience / Publishing status**
   choose **Publish app** — with only these basic scopes Google does not require a review. (While it stays in
   *Testing*, only the Google accounts you list as test users can sign in.)
3. **APIs & Services → Credentials → Create credentials → OAuth client ID** → *Web application*:
   * **Authorized JavaScript origins**: `https://beyond-erp.pages.dev`
   * **Authorized redirect URIs**: `https://<ref>.supabase.co/auth/v1/callback` — Supabase shows this exact address
     on its Google provider page (next step); copy it from there.
   Create, then copy the **Client ID** and **Client secret**.
4. **Supabase → Authentication → Sign In / Providers → Google**: switch on, paste the Client ID and Client secret, save.
5. Make sure **Authentication → URL Configuration** has your app address as Site URL and under Redirect URLs.

People who sign in with Google are matched to the workspace by their Google email address: add that address under
Settings → Workspace (cloud) → Members, exactly as for email accounts. A Google account has no password in the app —
"Forgot password?" does not apply to it.

**Remember me**: ticked, the browser stays signed in until you sign out; unticked, closing the browser (or tab) signs
you out — use that on shared computers.

## Signing in — if it says "Invalid login credentials"

* **No account yet** → use the **Create an account** tab (type the password twice).
* **Just created one** → open the confirmation email from Supabase first (check spam). The link brings you back to
  the app signed in. No email? "Resend confirmation email". Supabase's built-in mailer only sends a few emails per
  hour on the free plan.
* **Forgot the password / not sure which one you used** → "Forgot password?" on the sign-in tab.
* **Quickest way around email altogether**: Supabase dashboard → **Authentication → Users → Add user → Create new
  user**, enter the email and a password, tick **Auto Confirm User** — then sign in with those. (Or switch off
  *Confirm email* under Authentication → Sign In / Providers → Email.)
* See every account and its status in **Authentication → Users**; you can delete a half-finished one there and
  start again.

## Desktop app and the offline web copy

Both connect the same way: **Settings → Workspace (cloud)** → paste the Project URL and anon key → sign in →
*Join* (the device's books are replaced by the workspace's; a backup is taken first on the desktop).

* A **desktop server** that several people use through its own local user names: add its account as a member with
  **Gateway** ticked, so each change is recorded under the local user who made it.
* Without a connection a connected device stays **view-only** until it is back online — changes are never kept
  on one device only, so the books cannot fork.

## How it stays consistent

Every change is appended to one ordered log in Supabase (`ops`); every device replays the log in order with the
same engine, clock and user, so all copies are identical. If two people save at the same moment, one gets the next
number and the other device automatically pulls that change, re-checks its own, and saves right after. The app
saves a full copy (`snapshots`) every 300 changes so new devices start fast.

* Only members can read or write; writes go only through the database functions in the migration file, which keep
  the log gap-free and stamp who wrote each change. Each user's permission matrix is re-checked on every device.
* Passwords never go to Supabase's tables: people sign in with Supabase accounts; local desktop passwords stay on the
  device where they were set.
* After an app update, reload the page; a device with an older version stops writing until updated.
* Supabase's free plan pauses a project after about a week without any activity — open the app (or the Supabase
  dashboard) to wake it; nothing is lost.
