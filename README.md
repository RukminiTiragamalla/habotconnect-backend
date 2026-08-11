# HabotConnect — LSA Service Booking Backend

A backend prototype connecting Parents with Learning Support Assistants
(LSAs), built with Django + Django REST Framework, backed by MySQL.

---

## 1. Setup Instructions

```bash
git clone https://github.com/RukminiTiragamalla/habotconnect-backend.git
cd habotconnect-backend
pip install -r requirements.txt
```

`mysqlclient` needs MySQL's dev headers to install correctly:
- **Windows:** install MySQL Server/Connector first (provides the required libs)
- **Ubuntu/Debian:** `sudo apt-get install default-libmysqlclient-dev pkg-config`
- **macOS:** `brew install mysql-client pkg-config`

**Create the database** (Django won't do this for you):

```sql
CREATE DATABASE habotconnect CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

**Configure environment variables** (or edit the defaults directly in `config/settings.py`):

```bash
set MYSQL_DB=habotconnect
set MYSQL_USER=root
set MYSQL_PASSWORD=your_password
set MYSQL_HOST=127.0.0.1
set MYSQL_PORT=3306
```

> **Note on `HOST`:** use `127.0.0.1`, not `localhost`. MySQL's client
> library treats the literal word `localhost` specially and tries to
> connect via a Unix socket file instead of a normal network connection —
> this works locally by coincidence but breaks in CI/containerized
> environments where that socket path doesn't exist. Using an IP address
> forces a standard TCP connection everywhere, consistently.

**Run migrations and start the server:**

```bash
python manage.py migrate
python manage.py runserver
```

### Running the tests

```bash
python manage.py test booking -v 2
```

---

## 2. Architecture: Why Django MVT (not Flask MVC)

Django was chosen over Flask for three reasons:

1. **Django's ORM + migrations** made it straightforward to build a
   normalized, indexed schema and evolve it safely — migrations are
   auto-generated and version-controlled (`booking/migrations/`).
2. **Django REST Framework's serializers** give validation exactly where
   the double-booking Poka-Yoke check needs to live — enforced
   structurally in `BookingCreateSerializer.validate()`, not relying on a
   developer remembering to check it in every view that touches bookings.
3. Django follows **MVT (Model–View–Template)**: the *Model* is
   `booking/models.py`; the *View* is `booking/views.py` (DRF's
   `generics`/`APIView` classes handling request/response logic); the
   *Template* layer is replaced by DRF **Serializers**, which render
   Python objects to JSON instead of HTML. The main difference from
   classic MVC is where the "Controller" responsibility sits — in Django
   it's split between the URL router (`urls.py`) and the View, rather
   than being a separate third layer.

---

## 3. Database Schema

```
Parent (1) ────< (N) BookingRequest (N) >──── (1) LSAProfile
                        │
                        │ (1:1)
                        ▼
                     Payment

LSAProfile (N) ────< (M2M) >──── Skill
```

| Model | Key Fields | Notes |
|---|---|---|
| `Parent` | `full_name`, `email` (unique) | |
| `Skill` | `name` (unique) | Normalized into its own table (not a comma-separated string on LSAProfile) so filtering by skill is an indexed join, not a `LIKE '%...%'` scan. |
| `LSAProfile` | `full_name`, `skills` (M2M), `hourly_rate`, `is_active` | |
| `BookingRequest` | `parent` FK, `lsa` FK, `start_time`, `end_time`, `status` | Composite index on `(lsa, start_time, end_time)` — see rationale below. |
| `Payment` | `booking` (1:1), `amount`, `status`, `transaction_ref` (unique) | Separate table so payment state (updated independently, via webhook) never collides with booking state updates. |

**Why the composite index on `(lsa, start_time, end_time)`?** Every
overlap check and every "is this LSA free" query filters on exactly those
three columns together. Without the index, checking for a clash requires
scanning every booking for that LSA; with it, the database can do a fast
indexed range lookup instead.

**Booking status lifecycle:** `pending` → `confirmed` (payment succeeds) or
`failed` (payment fails), or `cancelled` (manual cancellation). Cancelled
bookings are excluded from overlap checks, since a cancelled slot is free
again.

---

## 4. API Endpoints

### `GET /api/v1/lsas/search/?skill=<name>`

Returns active LSAs, optionally filtered by skill name.

```json
GET /api/v1/lsas/search/?skill=Dyslexia Support

200 OK
[
  {
    "id": 1,
    "full_name": "John LSA",
    "email": "john.lsa@example.com",
    "hourly_rate": "25.00",
    "skills": [{"id": 1, "name": "Dyslexia Support"}],
    "is_active": true
  }
]
```

### `POST /api/v1/bookings/`

Creates a booking, validating for double-booking, and kicks off a mock
payment.

```json
POST /api/v1/bookings/
{
  "parent": 1,
  "lsa": 1,
  "start_time": "2026-09-01T09:00:00Z",
  "end_time": "2026-09-01T10:00:00Z"
}
```

- **201 Created** — booking created with `status: "pending"`; a `Payment`
  row is created with a `transaction_ref` from the mock gateway.
- **400 Bad Request** — validation failure: `end_time <= start_time`, or
  an overlapping booking detected for that LSA.
- **409 Conflict** — reserved for the rare case where two requests race
  past validation concurrently (see §6).

### `POST /api/v1/payments/webhook/`

Receives payment events from the (mocked) external gateway and
transitions booking state.

```json
POST /api/v1/payments/webhook/
{
  "transaction_ref": "txn_abc123",
  "event": "payment.success",
  "secret": "<shared webhook secret>"
}
```

- `payment.success` → `Payment.status = success`, `Booking.status = confirmed`
- `payment.failed` → `Payment.status = failed`, `Booking.status = failed`
- **403** if `secret` doesn't match `PAYMENT_WEBHOOK_SECRET`.
- **404** if no `Payment` matches the given `transaction_ref`.

---

## 5. Query Optimization

`GET /api/v1/lsas/search/` originally used a naive queryset with no
`prefetch_related`. Measuring the actual SQL fired against a database with
3 LSAs (2 skills each) confirmed the N+1 problem directly:

- **Before:** 4 queries — 1 to fetch the LSA list, plus 1 *per LSA* to
  fetch that LSA's skills (the classic N+1 pattern).
- **After** adding `.prefetch_related("skills")`: **2 queries**, fixed —
  1 for the LSA list, 1 that fetches all related skills for the entire
  batch in a single `WHERE ... IN (...)` query.

Critically, this count of 2 stays **constant regardless of how many LSAs
exist** — 3 LSAs or 10,000 both cost exactly 2 queries. This is verified
automatically in `test_lsa_search_query_count_stays_low` using Django's
`assertNumQueries`, so a future change that accidentally reintroduces N+1
would fail CI immediately.

---

## 6. Double-Booking Prevention (Poka-Yoke Design)

Two deliberate layers:

1. **Serializer-level check** (`BookingCreateSerializer.validate`) — runs
   on every request, computes overlap using:
   ```
   existing.start_time < new.end_time  AND  existing.end_time > new.start_time
   ```
   and rejects clashes with a clear **400** error before anything touches
   the database further.

2. **Transaction-level recheck** (`BookingCreateView.create`) — wraps
   booking creation in `transaction.atomic()` with `select_for_update()`
   on that LSA's existing bookings. This closes a genuine race condition:
   two concurrent requests could both read "no conflict" before either
   commits. `select_for_update()` forces the second request to wait for
   the first to finish, then re-check against now-committed data,
   returning **409** if it now conflicts.

**Boundary case verified:** a booking ending exactly when another begins
(e.g. `09:00–10:00` followed by `10:00–11:00`) is correctly treated as
non-overlapping and allowed — confirmed both manually and in
`test_adjacent_booking_is_allowed`.

---

## 7. Testing

`booking/tests.py` — 7 automated tests covering:

- Successful booking creation
- Overlapping booking rejected
- Adjacent (boundary) bookings allowed
- Invalid time range rejected (`end_time <= start_time`)
- LSA search stays at a fixed, low query count (N+1 regression guard)
- Webhook confirms booking on `payment.success`
- Webhook fails booking on `payment.failed`

Run with `python manage.py test booking -v 2`.

## 8. CI/CD

`.github/workflows/ci.yml` runs on every push/PR to `main`: spins up a
real MySQL 8.0 service container, installs dependencies, checks for
missing migrations, and runs the full test suite — proving the project
works in a clean environment, not just on the developer's own machine.

---

## 9. Known Simplifications

- **No authentication/permissions** on the two required endpoints —
  intentionally out of scope to stay within the project's 4–6 hour
  target. In production, these would sit behind DRF permission classes
  or JWT auth.
- **Payment gateway is mocked** (`booking/services/payment_gateway.py`);
  the webhook uses a shared secret string rather than an HMAC signature,
  for the same reason.
- **MySQL doesn't support Postgres-style range-exclusion constraints**, so
  double-booking prevention is entirely application-layer (serializer +
  `select_for_update`), rather than also being enforced as a single
  database constraint.