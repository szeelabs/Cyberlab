# Real sign-in with Supabase

By default CyberLab runs in **development mode**: no sign-in, and it only listens on your own
machine (`./scripts/dev.sh`). To let other people use one CyberLab with their own accounts,
turn on real sign-in with a free [Supabase](https://supabase.com) project.

Each person signs up with an email and password. CyberLab never sees passwords: Supabase
checks them and gives the browser a short-lived token signed with Supabase's private key.
CyberLab's server checks that signature with the project's **public** key list, so a token
can't be forged, reused from another project, or used after it expires.

## 1. Get two public values from your Supabase project
In the Supabase dashboard: **Project Settings → API**.
* **Project URL**, like `https://abcdefghijklmnop.supabase.co`
* **Publishable key** (`sb_publishable_...`) or the legacy **anon** key

Both are public by design (they are sent to every browser). **Never** use the
`service_role` / secret key with CyberLab.

## 2. Tell CyberLab
Put them in `.env` (it is gitignored) or the server's environment:
```bash
CYBERLAB_AUTH_MODE=jwt
CYBERLAB_AUTH_SUPABASE_URL=https://abcdefghijklmnop.supabase.co
CYBERLAB_AUTH_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
```
Everything else is derived: the public key list
(`<url>/auth/v1/.well-known/jwks.json`), the expected issuer (`<url>/auth/v1`) and the
algorithms (ES256/RS256). Restart CyberLab: the web app now shows a **Sign in** screen with
**Create an account**.

## 3. Set where confirmation emails send people
New accounts get a confirmation email. In Supabase: **Authentication → URL Configuration**,
set **Site URL** to your CyberLab address followed by `/app/` (for example
`https://your-server.example.org/app/`). Without it, the link still confirms the account but
then opens Supabase's default page; people can simply come back and sign in.

## Good to know
* Supabase's built-in email sender is limited to a few emails per hour on the free plan.
  For more sign-ups, connect your own SMTP in **Authentication → Emails** (free options exist).
* Sessions last for the browser tab: closing the tab signs you out.
* Forgotten passwords: not in CyberLab's screen yet; reset them from the Supabase dashboard
  (**Authentication → Users**).
