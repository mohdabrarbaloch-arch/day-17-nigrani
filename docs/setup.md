# Setup

## Requirements

- Python 3.11 or newer
- pip
- (optional) Docker + Docker Compose, if you want Postgres

## Local, SQLite, zero config

```bash
git clone https://github.com/mohdabrarbaloch-arch/day-17-nigrani.git
cd day-17-nigrani

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
cp .env.example .env
```

Open `.env` and set a real secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Paste the output into `JWT_SECRET`. Everything else can stay as-is for local work.

Run it:

```bash
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. Create an account — the demo fleet seeds itself on
first sign-up, so you land on a populated dashboard rather than an empty one.

## Local, PostgreSQL

```bash
docker compose up -d db
```

Then in `.env`:

```
DATABASE_URL=postgresql+psycopg://nigrani:nigrani_dev_password@localhost:5432/nigrani
```

Tables are created on startup. There are no migrations to run.

## Docker, everything

```bash
docker compose up --build
```

App on <http://127.0.0.1:8000>, Postgres on 5432. The app container waits for
the database healthcheck before starting.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./nigrani.db` | `postgres://` and `postgresql://` are rewritten to the psycopg driver automatically |
| `JWT_SECRET` | dev fallback | **Required** when `ENVIRONMENT=production`; the app raises on startup without it |
| `JWT_TTL_MINUTES` | `720` | 12 hours |
| `ENVIRONMENT` | `development` | `production` disables the dev secret fallback |
| `CORS_ORIGINS` | `*` | Comma-separated list in production |
| `PORT` | `8000` | Used by docker-compose |

## Verifying the install

```bash
pytest                    # 72 tests, all should pass
ruff check .              # no output = clean
black --check .           # "All done" = clean
curl localhost:8000/api/health
# {"status":"ok","version":"1.0.0","service":"nigrani"}
```

## Common problems

**`RuntimeError: JWT_SECRET must be set in production`**
You set `ENVIRONMENT=production` without a secret. Either set `JWT_SECRET` or
drop back to `development`.

**`ModuleNotFoundError: No module named 'app'`**
You are not in the project root, or the venv is not active. `pip install -e .`
from the root fixes the import path.

**`sqlite3.OperationalError: unable to open database file`**
The working directory is not writable, or `DATABASE_URL` points at a path that
does not exist. Use an absolute path or run from the project root.

**Port 8000 already in use**
`uvicorn app.main:app --port 8001`.

**Tests fail with a database error**
`tests/test_api.py` creates its own temporary SQLite file and sets
`DATABASE_URL` before importing the app. If you have a stale `nigrani.db` in the
root it is not used by the tests — but delete it if you want a clean local run.
