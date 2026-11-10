# Nigrani · نگہرانی

**Predictive maintenance for factory machinery — where every number can be argued with.**

A pump starts vibrating a little more each shift. By the time someone notices, the bearing is gone and the line is down for two days. Nigrani watches the three signals that actually predict that failure — vibration, temperature, current — and tells you how many hours you have left before it crosses the line.

The difference from most "AI maintenance" tools: there is no black box. Every health score is arithmetic you can reproduce on paper, and the app shows you the working.

```
health 41.65  ·  critical  ·  RUL 16.2 h  ·  worst channel: current
```

---

## Why this exists

Predictive maintenance is one of the few places where an unexplainable model is actively dangerous. If a system says "replace this bearing in 16 hours" and you cannot see why, you either ignore it or you shut down a line on faith. Both are expensive.

So Nigrani is built the other way round: a small, documented model that a maintenance engineer can check against their own experience, and a UI that shows the channel breakdown, the trend, and the projection — including the fact that the projection is a straight line, not a forecast.

---

## What it does

| | |
|---|---|
| **Health index** | Weighted blend of three channels, pulled 30% toward the worst one so a single dying bearing cannot hide behind two healthy readings |
| **Degradation rate** | Least-squares slope through the last 12 readings, in health points per hour |
| **Remaining useful life** | Hours until health crosses 30, at the current rate. `unknown` when the machine is not degrading — never "infinite" |
| **Alerts** | Four bands (healthy / warning / critical / emergency), one open alert per severity, auto-resolved when the machine recovers |
| **Forecast** | Straight-line projection for the next 72 hours, labelled as a projection |
| **Maintenance log** | What a human did, when, and how long the line was down |
| **Fleet view** | Every machine with its verdict, plus counts for the header |

---

## Screenshots

![Fleet dashboard](docs/screenshots/dashboard.jpg)

*Fleet view — five machines across all four health bands, with the KPI row and open alerts.*

![Machine detail](docs/screenshots/detail.jpg)

*Machine detail — channel breakdown, 72-hour projection with the failure threshold drawn, and the reading form.*

<img src="docs/screenshots/mobile.jpg" width="320" alt="Mobile view">

*Mobile — the same dashboard at 390px.*

---

## The model, in full

This is the whole thing. There is no hidden layer.

**1. Score each channel 0..1**

```
score = clamp(1 - (value - nominal) / (critical - nominal), 0, 1)
```

At nominal → 1.0. At critical → 0.0. Linear in between. If `critical <= nominal` the channel is misconfigured and scores 0 — a broken config should look broken, not healthy.

**2. Blend into a health index**

```
weighted = Σ(score × weight) / Σ(weight)      weights: vibration .5, temp .3, current .2
health   = 100 × (0.7 × weighted + 0.3 × min(score))
```

The 70/30 split is the important part. A plain weighted mean lets three healthy channels hide one dead one. Pulling 30% toward the worst channel means a single failed bearing always shows up.

**3. Degradation rate**

Least-squares slope through the last 12 `(timestamp, health)` pairs, negated so positive means degrading. Needs at least 3 points; returns `None` otherwise rather than inventing a trend.

**4. Remaining useful life**

```
RUL = (health - 30) / rate_per_hour
```

`None` when `rate <= 0`. A flat trend over twelve samples is not a promise about next month, so the app says "unknown" instead of a number.

**5. Alert bands**

| Health | Severity | Meaning |
|---|---|---|
| < 30 | emergency | At or past the failure threshold. Stop it. |
| < 50 | critical | Schedule intervention now |
| < 70 | warning | Plan maintenance this week |
| ≥ 70 | — | Healthy, unless degrading faster than 1.5 points/hour |

---

## Quick start

```bash
git clone https://github.com/mohdabrarbaloch-arch/day-17-nigrani.git
cd day-17-nigrani

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env               # then set JWT_SECRET
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>, create an account, and the demo fleet seeds itself — five machines with 24 hours of history, deliberately spread across all four health bands so you can see the engine work immediately.

**No database setup needed.** SQLite by default. Point `DATABASE_URL` at Postgres when you want one.

### With Docker

```bash
docker compose up --build
```

Brings up Postgres 16 and the app on <http://127.0.0.1:8000>.

---

## API

Full reference in [`docs/api.md`](docs/api.md). The short version:

```bash
# register
curl -X POST localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@plant.pk","password":"at-least-8-chars","display_name":"You"}'

# seed a demo fleet
curl -X POST localhost:8000/api/seed-demo -H "Authorization: Bearer $TOKEN"

# push a reading — returns the verdict
curl -X POST localhost:8000/api/machines/1/readings \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"vibration_rms":6.4,"temperature_c":72.1,"current_a":16.8}'

# fleet summary
curl localhost:8000/api/fleet -H "Authorization: Bearer $TOKEN"
```

Interactive docs at `/docs` (Swagger) and `/redoc`.

---

## Tests

```bash
pytest              # 72 tests
ruff check .        # clean
black --check .     # clean
```

The suite is split deliberately:

- `tests/test_engine.py` — the maths. Channel scoring, the 70/30 blend, slope, RUL, band boundaries, determinism, and the naive/aware datetime case that SQLite forces on you.
- `tests/test_api.py` — auth, ownership isolation (another user's machine is 404, not 403), the alert lifecycle including auto-resolve, and two regression tests for a bug where seeding wrote readings without running the verdict — leaving a red machine next to an empty alert inbox.

---

## Project layout

```
app/
  engine.py          the model — pure functions, no I/O, no framework
  models.py          SQLAlchemy 2.0 typed models
  schemas.py         Pydantic v2 request/response contracts
  security.py        bcrypt + JWT
  db.py              engine, session factory, dependency
  main.py            app wiring, security headers, rate limiting
  routers/
    auth.py          register / login / me
    machines.py      CRUD, ingestion, health, forecast, maintenance
    alerts.py        alert inbox, resolve, fleet summary
    demo.py          seed + simulate
  static/            vanilla JS dashboard, no build step
tests/
docs/
```

---

## Design decisions worth knowing

**The engine has no imports from FastAPI or SQLAlchemy.** It takes tuples and returns dataclasses. That is why it can be tested in milliseconds and why you can port it to a PLC or a notebook without dragging a web framework along.

**Ownership failures return 404, not 403.** A 403 confirms that a machine with that ID exists. In a multi-plant deployment that is a leak.

**Login gives the same error for unknown email and wrong password.** Same reason.

**Alerts dedupe by severity.** A degrading bearing would otherwise page someone every minute. One open alert per severity, updated in place, and auto-resolved when health recovers — a stale red banner is worse than no banner.

**The forecast is labelled a projection.** It is a straight line at the current rate. Calling it a forecast would be a lie the UI tells on the model's behalf.

**Seeding runs the same verdict path as live ingestion.** This was a real bug: the demo fleet showed a red machine with an empty alert inbox. Fixed, and pinned with a regression test.

---

## Limitations — read these before trusting it

- **The demo data is synthetic.** `POST /api/simulate` drifts readings toward critical at a fixed rate. It is a demo aid, not a physics model, and the API response says so.
- **The model is linear.** Real bearings degrade non-linearly near failure. A straight-line RUL is optimistic in the last 20% of life. Treat it as a planning number, not a countdown.
- **No sensor integration.** There is no Modbus/OPC-UA/MQTT adapter yet. Readings come in over HTTP. Wiring a real PLC is the obvious next step.
- **No Alembic.** Tables are created with `create_all()`. Fine for this scope; a real deployment needs migrations.
- **Not a safety system.** This is a planning tool. It does not replace a maintenance engineer's judgement, and it should not be the only thing standing between a machine and a failure.

---

## Roadmap

- MQTT / OPC-UA ingestion adapter
- Per-machine learned baselines instead of hand-set nominal/critical
- Email and SMS alert delivery
- Alembic migrations
- Multi-tenant plant/line hierarchy

---

## License

MIT — see [LICENSE](LICENSE).

Built as Day 17 of a 30-day AI engineering challenge. Day 16 was skipped; this is the next one built.
