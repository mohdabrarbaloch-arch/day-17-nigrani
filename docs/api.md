# API reference

Base URL: `http://127.0.0.1:8000`

All endpoints except `/api/health` and the auth pair require
`Authorization: Bearer <token>`.

Interactive docs: `/docs` (Swagger UI) and `/redoc`.

---

## Meta

### `GET /api/health`

No auth. Liveness probe.

```json
{"status": "ok", "version": "1.0.0", "service": "nigrani"}
```

---

## Auth

### `POST /api/auth/register`

| Field | Type | Rules |
|---|---|---|
| `email` | string | valid email, unique |
| `password` | string | 8-128 chars |
| `display_name` | string | 1-120 chars, default `"Engineer"` |

**201** -> `{"access_token": "...", "token_type": "bearer", "expires_in_minutes": 720}`
**409** -> email already registered
**422** -> validation failure

### `POST /api/auth/login`

**200** -> same token shape.
**401** -> `"Email or password is incorrect"` - identical for unknown email and
wrong password, deliberately.

### `GET /api/auth/me`

**200** -> `{"id": 1, "email": "...", "display_name": "...", "role": "engineer"}`
**401** -> missing, expired, or malformed token

---

## Machines

### `POST /api/machines`

```json
{
  "asset_tag": "PMP-104",
  "name": "Cooling water pump",
  "line": "Line A",
  "machine_type": "pump",
  "nominal_vibration": 2.0,
  "critical_vibration": 11.0,
  "nominal_temperature": 45.0,
  "critical_temperature": 95.0,
  "nominal_current": 12.0,
  "critical_current": 22.0
}
```

**201** -> the machine.
**409** -> `asset_tag` already used by this account.
**422** -> any `critical_*` not greater than its `nominal_*`.

### `GET /api/machines`

**200** -> array of machines, ordered by `asset_tag`. Scoped to the caller.

### `GET /api/machines/{id}`

**200** -> the machine. **404** if it does not exist *or belongs to someone else*.

### `DELETE /api/machines/{id}`

**204** -> deleted, along with its readings, alerts and maintenance logs.
**404** -> not found or not yours.

### `POST /api/machines/{id}/readings`

```json
{"vibration_rms": 6.4, "temperature_c": 72.1, "current_a": 16.8, "recorded_at": null}
```

`recorded_at` is optional; omit it and the server uses now (UTC). Naive
timestamps are treated as UTC.

**201** -> a full `HealthOut` (see below), computed over the last 12 readings
including this one.
**404** -> machine not found or not yours.
**422** -> any value out of bounds (`vibration_rms` >= 0, `temperature_c` -50..500,
`current_a` >= 0).

### `GET /api/machines/{id}/health`

**200** -> `HealthOut`. With no readings, returns `health: 100.0`,
`reading_count: 0`, and a `reason` explaining that health is unknown.

### `GET /api/machines/{id}/readings?limit=100`

`limit` 1-1000, default 100. **200** -> array of readings, oldest-first.

### `GET /api/machines/{id}/forecast?hours=72`

`hours` 1-720, default 72.

```json
{
  "machine_id": 1,
  "asset_tag": "FAN-402",
  "current_health": 41.65,
  "degradation_per_hour": 1.42,
  "rul_hours": 8.2,
  "points": [{"hours_ahead": 0, "health": 41.65}, {"hours_ahead": 3, "health": 37.39}],
  "note": "Straight-line projection at the current rate. Not a trained forecast."
}
```

### `POST /api/machines/{id}/maintenance`

```json
{"action": "Replaced bearing", "notes": "Wear ring visible", "downtime_minutes": 45}
```

**201** -> the log entry, with `performed_by` set from the caller's display name.

### `GET /api/machines/{id}/maintenance`

**200** -> array of maintenance logs, newest first.

---

## HealthOut

The shape returned by reading ingestion and the health endpoint.

```json
{
  "machine_id": 1,
  "asset_tag": "PMP-101",
  "name": "Cooling water pump",
  "line": "Line A",
  "health": 41.65,
  "worst_channel": "current",
  "degradation_per_hour": 1.42,
  "rul_hours": 8.2,
  "severity": "critical",
  "reason": "Health is in the critical band. Schedule intervention now.",
  "channels": [
    {"name": "vibration", "value": 6.4, "nominal": 2.0, "critical": 11.0, "score": 0.511, "weight": 0.5}
  ],
  "reading_count": 12
}
```

| Field | Meaning |
|---|---|
| `health` | 0-100 blended index |
| `worst_channel` | `vibration` / `temperature` / `current` / `none` |
| `degradation_per_hour` | health points lost per hour; `null` under 3 samples |
| `rul_hours` | hours to the 30-point floor; `null` when not degrading |
| `severity` | `warning` / `critical` / `emergency` / `null` |
| `reason` | human sentence explaining the severity |
| `channels[].score` | 0..1 sub-score for that channel |

---

## Alerts

### `GET /api/alerts?open_only=true&limit=100`

`open_only` default `true`. **200** -> array of alerts, newest first, scoped to
the caller's machines.

### `POST /api/alerts/{id}/resolve`

```json
{"note": "Replaced bearing, vibration back to 2.1"}
```

**200** -> the resolved alert.
**404** -> not found or not yours.
**409** -> already resolved.
**422** -> empty note.

---

## Fleet

### `GET /api/fleet`

```json
{
  "total": 5,
  "healthy": 2,
  "warning": 1,
  "critical": 1,
  "emergency": 1,
  "open_alerts": 3,
  "machines": [ /* HealthOut[] */ ]
}
```

Only active machines are counted. `open_alerts` counts unresolved alerts across
all of the caller's machines.

---

## Demo

### `POST /api/seed-demo`

Creates the five-machine demo fleet with 24 hours of history. **Idempotent** -
if the caller already has any machine, returns `{"created": 0}` and does nothing.

**201** -> `{"created": 5, "note": "Demo fleet seeded with 24 hours of history."}`

### `POST /api/simulate`

```json
{"hours": 24}
```

`hours` 1-168, default 24. Appends synthetic readings to every machine the
caller owns, drifting each toward critical, then re-runs the alert sync.

**201** -> `{"machines": 5, "readings_created": 120, "note": "Synthetic telemetry. Not a physics model - for demo only."}`

---

## Errors

Every error is `{"detail": "..."}`.

| Status | When |
|---|---|
| 401 | missing / expired / invalid token, or bad credentials |
| 404 | resource missing **or owned by someone else** |
| 409 | duplicate email, duplicate asset tag, already-resolved alert |
| 422 | validation failure (Pydantic or a `critical <= nominal` check) |
| 429 | rate limit exceeded (240/min default) |

Ownership failures return 404 rather than 403 on purpose: a 403 confirms the
resource exists, which is a leak in a multi-plant deployment.
