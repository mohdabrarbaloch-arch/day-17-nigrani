# Architecture

## Shape of the system

```
                    +------------------------------+
                    |  Browser (vanilla JS SPA)    |
                    |  index.html . app.js . css   |
                    +--------------+---------------+
                                   |  JSON over HTTPS
                                   |  Bearer JWT
                    +--------------v---------------+
                    |  FastAPI                     |
                    |  --------------------------  |
                    |  middleware: security hdrs   |
                    |              CORS            |
                    |              rate limit      |
                    |  --------------------------  |
                    |  routers/                    |
                    |    auth      register/login  |
                    |    machines  CRUD + ingest   |
                    |    alerts    inbox + fleet   |
                    |    demo      seed + simulate |
                    +-------+--------------+-------+
                            |              |
              +-------------v----+   +-----v----------------+
              |  engine.py       |   |  SQLAlchemy 2.0      |
              |  pure functions  |   |  ------------------  |
              |  no I/O          |   |  users               |
              |  no framework    |   |  machines            |
              |                  |   |  readings            |
              |  score -> blend  |   |  alerts              |
              |  -> slope -> RUL |   |  maintenance_logs    |
              +------------------+   +----------+-----------+
                                                |
                                   +------------v-------------+
                                   |  SQLite (default)        |
                                   |  PostgreSQL (prod)       |
                                   +--------------------------+
```

## The one rule that shapes everything

**`engine.py` imports nothing from FastAPI or SQLAlchemy.**

It takes tuples of numbers and returns frozen dataclasses. That single constraint is why:

- the model can be tested in milliseconds without a database or a client
- the same code could run inside a PLC gateway, a notebook, or a batch job
- there is no way for a request object to leak into the maths

Everything else in the app is plumbing around that module.

## Data flow: one reading

```
POST /api/machines/{id}/readings
  |
  |- 1. auth          current_user() -> User
  |- 2. ownership     _owned() -> 404 if not yours
  |- 3. validate      Pydantic bounds (vibration >= 0, etc.)
  |- 4. score         engine.evaluate([single reading]) -> health for the row
  |- 5. persist       INSERT reading (with its own health)
  |- 6. window        SELECT last 12 readings, oldest-first
  |- 7. verdict       engine.evaluate(window) -> health, rate, RUL, severity
  |- 8. alert sync    _sync_alert() -> raise / update / auto-resolve
  |- 9. respond       HealthOut with channel breakdown
```

Steps 4 and 7 both call the same function. The stored per-row health is the
single-sample score; the returned verdict is the windowed one. They differ on
purpose: the row records what that sample looked like, the verdict records
where the machine is heading.

## Alert state machine

```
   healthy  --health < 70-->  warning  --health < 50-->  critical  --health < 30-->  emergency

   any state --health recovers above 70--> healthy (all open alerts auto-resolved)
```

Invariants:

- At most one open alert per `(machine, severity)`.
- Escalating severity closes the lower one with a note saying it was superseded.
- Recovery closes everything with a note recording the recovered health.
- A human resolve requires a note - an alert closed with no explanation teaches
  the next person nothing.

## Why the health blend is 70/30

A plain weighted mean is the obvious choice and it is wrong here.

Consider a machine with vibration at critical (score 0) and temperature and
current perfect (score 1). With weights .5/.3/.2:

```
weighted mean = .5x0 + .3x1 + .2x1 = 0.50  ->  health 50  ->  "critical"
```

That is already serious, but it is the same score as a machine that is mildly
bad on all three channels - and those are very different situations. The
bearing is gone in the first case.

Blending 30% toward the worst channel:

```
blended = .7x0.50 + .3x0.00 = 0.35  ->  health 35  ->  still "critical", but lower
```

And a machine dead on all three channels:

```
weighted = 0, worst = 0  ->  blended = 0  ->  health 0  ->  "emergency"
```

The distinction between "one channel failed" and "everything failed" survives.
That is the whole reason for the blend, and it is pinned by two tests.

## Scaling notes

**Readings table growth.** One machine at 1 Hz is 86,400 rows/day. At that rate
you want:

- a retention policy (raw for 30 days, hourly aggregates forever)
- `recorded_at` partitioning in Postgres
- the trend window query already uses `ORDER BY recorded_at DESC LIMIT 12` with
  an index on `(machine_id, recorded_at)` - that stays fast at any table size

**Ingestion throughput.** The current path does a synchronous insert plus a
12-row window query per reading. That is fine to a few hundred readings/second.
Beyond that, batch the inserts and compute the verdict on a schedule rather
than per-request.

**Multi-tenancy.** Ownership is enforced at the query level (`owner_id ==
user.id`) and failures return 404. Adding a plant/line hierarchy means adding
`plant_id` to `Machine` and threading it through `_owned()` - the pattern is
already there.

**Statelessness.** The app holds no session state; JWT carries identity. Any
number of instances can sit behind a load balancer as long as they share a
database.

## Security posture

| Concern | Handling |
|---|---|
| Password storage | bcrypt, cost 12 |
| Token | HS256 JWT, 12h default, secret from env |
| Production secret | app refuses to start without `JWT_SECRET` when `ENVIRONMENT=production` |
| Rate limiting | slowapi, 240/min default |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy` |
| CORS | explicit origins in prod, `*` only in dev |
| Ownership leaks | 404 not 403; identical login error for unknown email and wrong password |
| Input bounds | every numeric field bounded in Pydantic before it reaches the engine |
| Secrets in code | none - `.env.example` documents every variable, `.env` is gitignored |

## What is deliberately missing

- **No Alembic.** `create_all()` is enough for a single-deploy project. Adding
  migrations is the first thing to do before a second environment exists.
- **No sensor adapter.** Readings arrive over HTTP. Modbus/OPC-UA/MQTT is the
  obvious next integration and the reason the engine is framework-free.
- **No background scheduler.** Alerts are raised on ingestion, not on a timer.
  A machine that stops sending readings goes quiet rather than alarming - that
  is a real gap, and a "stale telemetry" alert is the fix.
