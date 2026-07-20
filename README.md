# campwatch — GoingToCamp cancellation watcher + auto-hold

A config-driven, self-hosted system that watches [GoingToCamp](https://washington.goingtocamp.com)
campgrounds (default: Washington State Parks) for cancellations, optionally
fires **one** hold request to lock an opening to your account for the
platform's ~15-minute hold window, and pings your phone with a direct link.

**It never touches payment.** You complete checkout by hand, inside the hold
window. That's the design, not a limitation.

```
config.yaml + .env
      │  loaded & validated (pydantic) by both services
      ▼
camply watcher(s)  ── one search per watch entry ──►  detects an opening
      │  webhook notification
      ▼
receiver (FastAPI) ── match → dedupe → [auto-hold, once] → notify
      ▼
"🔒 HELD — pay now"  or  "🏕️ OPEN — book now"  → your phone
      ▼
you pay, manually, within ~15 min
```

Availability watching is [camply](https://github.com/juftin/camply); the config
layer, webhook receiver, hold service, and notification routing are this repo.
Nothing about parks, dates, stay lengths, hold behavior, or notification
routing is hardcoded — it all lives in `config.yaml`.

> **Terms-of-service caveat:** GoingToCamp's terms constrain automated
> interaction with their booking system. This tool exists for single-family,
> personal use — one hold attempt per detected opening, a 5-minute polling
> floor, and no payment automation. Don't use it for bulk grabbing or
> scalping; that's on you, and it will likely get your account throttled or
> banned anyway.

## Quick start

Two ways to run it — same image, same config either way:

- **Unraid app** (single container, managed from the Docker tab): see
  [Installing on Unraid](#installing-on-unraid).
- **docker compose** (receiver + watcher as separate services):

```bash
cp config.example.yaml config.yaml   # edit: your parks, dates, sinks
cp .env.example .env                 # edit: your secrets
docker compose up -d --build
curl localhost:8000/status           # active watches + recent events
```

Both containers validate the config at startup and **fail fast with a list of
every problem** if something is off (unknown sink names, missing env vars,
`auto_hold` without its prerequisites, polling below the 5-minute floor, …).

`dry_run` defaults to `true` globally: even fully configured, no hold is sent
until you deliberately flip it. See [Arming auto-hold](#arming-auto-hold).

## Configuration

Two files. `config.yaml` carries no secrets (safe to commit if you want);
`.env` carries only secrets and is git-ignored.

### config.yaml

See [`config.example.yaml`](config.example.yaml) for a complete annotated example.

| Section | What it does |
|---|---|
| `provider` | `host` + `rec_area_id` select the GoingToCamp jurisdiction. Changing these retargets availability watching with **zero code changes**. |
| `defaults` | Fallbacks for any per-watch field left unset: `nights`, `polling_interval`, `auto_hold`, `dry_run`, `notify`. |
| `notifications` | Named sinks (`webhook` for Home Assistant, `pushover`, `ntfy`). Secrets referenced via `*_env` keys into `.env`. |
| `watches` | The list of things being watched. Adding one is a config edit, no code. |
| `receiver` | Optional: port, camply→receiver webhook URL, hold window, dedupe TTL. Defaults work on the compose network. |

Per-watch fields:

```yaml
watches:
  - name: kanaskat-palmer-aug        # unique; used in logs, dedupe keys, notifications
    campground_id: 123               # from the camply lookup (below)
    map_id: 456                      # optional; used by hold templates
    resource_location_id: 123        # optional; usually == campground_id
    start_date: 2026-08-14
    end_date: 2026-08-16             # checkout day; 1-night stays here = Fri AND Sat night
    nights: 1
    polling_interval: 5              # minutes; hard floor 5
    auto_hold: true                  # opt-in; defaults false
    dry_run: false                   # per-watch override of the global default
    hold_template: holds/kanaskat.json   # required if auto_hold
    session: wa_primary                  # required if auto_hold → .env keys
    notify: home_assistant           # must name a sink under notifications:
```

Validation rules enforced at startup:

- every `notify` must resolve to a defined sink, and that sink's env vars must be set;
- `auto_hold: true` requires an existing `hold_template` file **and** a
  resolvable `session` (both env vars set);
- `polling_interval` ≥ 5 minutes, `end_date` > `start_date`, unique watch names.

### Provider hosts

| Jurisdiction | `host` | `rec_area_id` |
|---|---|---|
| Washington State Parks | `washington.goingtocamp.com` | 3 |
| Tacoma Power Parks | `tacomapower.goingtocamp.com` | look up¹ |
| Wisconsin State Parks | `wisconsin.goingtocamp.com` | look up¹ |
| BC Parks | `camping.bcparks.ca` | look up¹ |

¹ `camply recreation-areas --provider GoingToCamp`

### .env

See [`.env.example`](.env.example). Notification secrets
(`HA_WEBHOOK_URL`, `PUSHOVER_PUSH_TOKEN`/`_USER`, `NTFY_TOPIC`) plus session
material keyed by session name: `session: wa_primary` in config.yaml reads
`SESSION_WA_PRIMARY_COOKIE` and `SESSION_WA_PRIMARY_CSRF`.

## Setup walkthrough

### 1. Find your campground IDs

```bash
pip install camply
camply campgrounds --provider GoingToCamp --rec-area 3 --search "Kanaskat"
```

Put the reported facility ID in the watch's `campground_id` (for GoingToCamp
this is the `resource_location_id`). `map_id` shows up in your hold capture
(below) or in the site's map API traffic.

### 2. Capturing a hold request (only for `auto_hold: true` watches)

The hold/cart endpoint is **not documented and differs per site — it is never
guessed**. Each `auto_hold` watch needs a one-time capture, done by you in a
browser:

1. Log in at the provider host (e.g. washington.goingtocamp.com).
2. Open DevTools → Network tab, filter to XHR/Fetch.
3. Manually walk a real booking up to the **hold / add-to-cart step. Stop
   before payment.**
4. Right-click the hold request → *Copy as cURL* (or export a `.har`).
5. **Scrub any card/payment data.**
6. Translate it into `holds/<watch>.json` — url, method, headers, body — and
   replace the live values with placeholders per
   [`holds/README.md`](holds/README.md) (`{{SESSION_COOKIE}}`,
   `{{CSRF_TOKEN}}`, `{{CAMPSITE_ID}}`, `{{START_DATE}}`, …).

Availability watching does **not** need any of this — a watch with
`auto_hold: false` just needs an ID and dates.

### 3. Session capture & refresh

Holds ride on your authenticated browser session, which **expires**. From the
same DevTools capture, copy the `Cookie` header value and the anti-CSRF /
request-verification token into `.env`
(`SESSION_<NAME>_COOKIE` / `SESSION_<NAME>_CSRF`), then
`docker compose restart receiver`.

When a hold comes back 401/403 the notification will say the session looks
stale — repeat this step. Session material sits behind a `get_session(name)`
interface (`src/campwatch/sessions.py`), so a future self-refreshing login
provider can be swapped in without touching the hold path.

### 4. Notification sinks

- **Home Assistant** (preferred): create a webhook-triggered automation; the
  receiver POSTs JSON `{title, message, url, watch, status}` — route it to a
  phone notification / TTS as you like. Put the webhook URL in `HA_WEBHOOK_URL`.
- **Pushover**: app token + user key in `.env`.
- **ntfy**: topic in `.env`; self-hosted server via `server_env` / `NTFY_SERVER`.

## Arming auto-hold

Deliberately staged, per §9 of the build brief:

1. **Dry-run first (default).** With `dry_run: true`, a detected opening logs
   the fully built hold request (session material redacted) and notifies with
   a "[DRY RUN]" title — nothing is sent. Point a temporary watch at a
   known-available date/park to exercise the whole chain; inspect the logged
   request shape (`docker compose logs receiver`).
2. **One live test** on a low-demand site: set `dry_run: false` on that watch
   only, let it hold something cheap, verify the "HELD — pay now" flow, let
   the hold lapse (or cancel in the site's cart).
3. Then arm the real watch: `dry_run: false`, restart.

Guardrails baked in regardless of config: **one** hold attempt per detected
opening (idempotency-keyed on watch + site + date, duplicates dropped), no
retry loops against the hold endpoint, 5-minute polling floor, and payment is
always manual. If a hold fails (taken already, stale session, network), you
still get an immediate "book manually NOW" notification with the direct link.

## Installing on Unraid

The repo ships an Unraid Docker template (`unraid/campwatch.xml`) that runs
both services in **one container** (`campwatch-combined`: the receiver plus
the camply watchers, with the webhook wired to localhost automatically).
Images are published to GHCR by CI on every push to `main`.

One-time prerequisite: after the first CI run, make the GHCR package public
(GitHub → your profile → Packages → `camp-checker` → Package settings →
Change visibility), or add registry credentials on Unraid.

1. **Prepare appdata** — on the Unraid box:

   ```bash
   mkdir -p /mnt/user/appdata/campwatch/holds
   # put your edited config.yaml in /mnt/user/appdata/campwatch/
   # put captured hold templates (auto_hold watches only) in holds/
   ```

2. **Add the template repository** — Unraid web UI → *Docker* tab → scroll to
   the bottom → **Template Repositories** → add:

   ```
   https://github.com/mosschief/camp-checker
   ```

   Save, then click **Add Container** and pick **campwatch** from the
   template dropdown (under "User templates").

3. **Fill in the template** — the appdata path and port are prefilled. Enter
   only the secrets your config.yaml actually references (HA webhook URL,
   Pushover token/user, ntfy topic, `SESSION_WA_PRIMARY_COOKIE`/`_CSRF`).
   Secret fields are masked. For additional sessions, click **Add another
   Path, Port, Variable…** and create `SESSION_<NAME>_COOKIE`/`_CSRF`
   variables matching the `session:` names in your config.

4. **Start it.** The **WebUI** button opens `/status` (active watches, arm
   state, recent events). If the container exits immediately, the log shows
   the exact config problems — fix and restart. If either internal service
   dies, the container exits and Unraid's restart policy brings the pair
   back together.

`.env` is not used on Unraid — the same variables come from the template's
container variables instead. Session refresh = edit the two session
variables on the container and hit Apply (which recreates and restarts it).

## Operations

- `GET /status` — active watches, arm/dry-run state, last webhook, recent events.
- `GET /healthz` — compose healthcheck.
- Logs are structured on stdout for both containers (`docker compose logs -f`).
- The dedupe store is in-memory; a receiver restart may re-notify a
  still-open site once. It errs toward never missing an opening.
- Unraid: point a Compose stack at this directory; `TZ` defaults to
  `America/Los_Angeles` via `.env`.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
pytest
```

The test suite covers the config loader/validator (bad configs must fail
loudly), camply yaml generation (validated against camply's own model), the
webhook payload parser, hold template injection + redaction + single-attempt
guarantee, the idempotency store, every sink type, and the receiver end-to-end
with fixture payloads. No test touches the network.
