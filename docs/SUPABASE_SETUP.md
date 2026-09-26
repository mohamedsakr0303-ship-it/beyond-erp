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

## 4 · Create the workspace

1. Open the published app → **Create an account** with your email → confirm the email → sign in.
2. The workspace is empty, so the app offers to **create it from this device's books** — you become the
   administrator. (To start from your existing data, first import your workbook or JSON backup on that device:
   choose "Use this device without the workspace", import, then Settings → Workspace → sign in → Create.)
3. **Settings → Workspace (cloud) → Members**: add each colleague's email (tick *Administrator* for other admins).
   This also creates their app user; set what they may see and change under **Users**.
4. Colleagues open the app, **Create an account** with that same email, and sign in — they get the shared books.

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
