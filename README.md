# &Gifts — Relationship CRM for Real Estate Agents (MVP)

Flask + MySQL, built to deploy on DigitalOcean App Platform.

## What's in this MVP

- Multi-tenant data model: `orgs` → `users`, `contacts` (households) →
  `contact_people` → `contact_methods`, `interests`, `timeline_events`,
  `gift_catalog_items` / `gift_triggers`, `suggested_actions` / `action_log`.
- Auth (register/login) with tier-aware org creation (defaults to Free).
- Contact CRUD: household + head of household + optional spouse, each
  with their own email/phone, plus interest tags.
- Timeline: seeded automatically with a `first_contact` event on creation;
  agents can add more (showing, offer, closing, anniversaries, custom),
  with a recurring/annual flag for anniversaries and birthdays.
- **Daily dashboard / suggestion engine** (`app/services/suggestion_engine.py`):
  rule-based job that scans timeline events 14 days out, matches gift
  triggers by event type + contact interest, and produces a plain-English
  "why" card the agent can approve or skip. Idempotent — safe to run
  on-demand or nightly.
- Tier limits enforced in `Org.can_add_contact()` / `Org.feature_enabled()`,
  driven by `TIER_LIMITS` in `config.py` — one place to tune later.

## What's intentionally NOT built yet

- Actual email/SMS sending (SendGrid/Twilio) — `ActionLog` records intent,
  approve currently just logs rather than dispatching.
- Shopify B2B checkout / fulfillment call on gift approval.
- Stripe billing/webhooks (tier is just a column right now, no payment flow).
- The LLM-written reason text — `_build_reason_text()` in the suggestion
  engine is a template today. Swapping in an Anthropic API call there is
  a self-contained change; nothing else needs to know the difference.
- Multi-seat / brokerage rollup views for the Team tier.

## Local development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export FLASK_ENV=development
export SECRET_KEY=dev-secret
export LOCAL_SQLITE_URI="sqlite:///$(pwd)/dev.db"   # skip MySQL locally

python3 -c "from app import create_app; from app.extensions import db; app = create_app('development'); \
app.app_context().push(); db.create_all()"

python3 seed.py     # loads starter interests + default gift catalog/triggers

flask --app wsgi run --debug
```

Visit `http://localhost:5000/auth/register` to create your first account.

## Deploying to DigitalOcean

### 1. Create the managed MySQL database

In the DO control panel: **Databases → Create Database Cluster → MySQL 8**.
Pick the cheapest node size to start (you can resize later without downtime
issues for an app this size). Name it `andgifts-db` to match `.do/app.yaml`.

### 2. Push this repo to GitHub

App Platform deploys from a GitHub repo. Push this project, then update
`.do/app.yaml`: replace both `YOUR_GITHUB_USERNAME/andgifts` placeholders
with your actual repo path.

### 3. Create the App

**Apps → Create App → GitHub**, pick the repo, and when prompted "detect
existing app spec," point it at `.do/app.yaml`. This will provision:
- The `web` service (gunicorn, tier limits from env)
- A `nightly-suggestions` scheduled job (cron, runs daily at 5am UTC via
  DO's native scheduled-job feature — GA now, minimum interval 15 min)

If you'd rather click through the UI instead of using the spec file:
Databases → attach `andgifts-db` to the app, which auto-populates the
`DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` bindable env
vars referenced in `app.yaml` (`${andgifts-db.HOSTNAME}` etc.).

### 4. Set secrets

In the app's **Settings → App-Level Environment Variables**, fill in the
`SECRET_KEY` and (when you build those integrations) `STRIPE_SECRET_KEY`,
`SENDGRID_API_KEY`, `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`,
`ANTHROPIC_API_KEY`. Leave them blank for now if you're just demoing the
CRM/timeline/dashboard pieces.

### 5. Run migrations against the managed DB

App Platform doesn't auto-run `db.create_all()`. Easiest MVP path: add a
**deploy-time job** (After every successful deploy) with run command:

```
flask --app wsgi db upgrade
```

You'll need to `flask --app wsgi db init && flask --app wsgi db migrate -m "initial"`
locally first (against the sqlite or a local MySQL) and commit the generated
`migrations/` folder — Flask-Migrate is already wired into `app/__init__.py`
via `Migrate(app, db)`, it just needs its first migration generated.

### 6. Seed reference data

Run `python seed.py` once against production (via `doctl` exec into the
job, or temporarily as a one-off deploy-time job) to load starter interests
and default gift triggers so new orgs aren't starting from a blank catalog.

## Project layout

```
app/
  models/          # org.py, contact.py, timeline.py, gifting.py, actions.py
  routes/          # auth.py, contacts.py, dashboard.py
  services/
    suggestion_engine.py   # the daily-dashboard logic
  templates/
  static/css/main.css
jobs/
  generate_daily_suggestions.py   # entry point for the DO scheduled job
config.py          # env-driven, includes TIER_LIMITS
seed.py
wsgi.py
.do/app.yaml
```

## Suggested next build order

1. Wire Stripe (checkout for the 3 paid tiers + webhook to flip `Org.tier`).
2. Real email send (SendGrid) on "Approve" for `action_type == 'email'`.
3. Gift fulfillment: on "Approve" for `action_type == 'gift'`, call your
   Shopify B2B draft-order API using `suggested_gift.shopify_product_id`.
4. Swap `_build_reason_text()` to an Anthropic API call for natural,
   varied copy instead of the template string.
5. SMS via Twilio once email is proven out.
6. Team-tier brokerage rollup view (org-wide contact list across seats).
