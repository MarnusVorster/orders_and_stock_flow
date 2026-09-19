# Orders & Stock Flow

A minimal two-process system for accepting orders and managing stock. Orders come in through one service, stock gets deducted asynchronously by a background worker that drains a PostgreSQL-backed queue. Built with FastAPI, SQLAlchemy 2.0 async, and asyncpg.

## What's Running

```
┌─────────────────────────────────────────────────────────────────────┐
│                        PostgreSQL Database                          │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌───────────────┐    │
│  │ products │  │  orders  │  │ order_items  │  │ stock_queue   │    │
│  └──────────┘  └──────────┘  └──────────────┘  └───────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
           ▲                       ▲                      ▲
           │                       │                      │
     ┌─────┴──────┐          ┌─────┴──────┐         ┌─────┴────────┐
     │ Orders API │          │ Stock API  │         │ Stock        │
     │ (FastAPI)  │          │ (FastAPI)  │         │ Worker       │
     │ Port 8001  │          │ Port 8002  │         │ (background) │
     └────────────┘          └────────────┘         └──────────────┘
```

**Orders API** (port 8001) — accepts orders, stores them, returns them. Idempotent via `order_ref`. Doesn't check stock.

**Stock API + Worker** (port 8002) — manages products, tracks stock levels, serves reports. Runs the background worker that drains the order queue. The worker starts automatically when the API boots and shuts down cleanly when the process dies.

## Prerequisites

- Python 3.11+
- PostgreSQL 14+
- pip / venv

## Getting Started

### 1. Configure

```bash
cp .env.example .env
# Edit .env with your PostgreSQL credentials
```

### 2. Setup python virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install

```bash
pip install -r requirements.txt
```

### 4. Run Migrations

```bash
ALEMBIC_CONFIG=alembic.ini alembic upgrade head
```

### 5. Start the Services

You need two terminals (or two processes):

```bash
# Terminal 1 — Orders API
uvicorn src.orders_api.main:app --host 0.0.0.0 --port 8001

# Terminal 2 — Stock API (worker starts automatically)
uvicorn src.stock_api.main:app --host 0.0.0.0 --port 8002
```

### 6. Seed Data

```bash
python -m src.seed
```

Creates 5 products and 10 orders (including 3 duplicate submissions to demonstrate idempotency).

## API Endpoints

### Orders API (http://localhost:8001)

| Method | Endpoint | What it does |
|--------|----------|-------------|
| POST | `/orders` | Create/accept an order (idempotent) |
| GET | `/orders/{order_ref}` | Fetch order details |
| GET | `/health` | Health check |

### Stock API (http://localhost:8002)

| Method | Endpoint | What it does |
|--------|----------|-------------|
| POST | `/stock` | Create a new product |
| GET | `/stock/{sku}` | Get current stock level |
| PUT | `/stock/{sku}` | Update stock level |
| GET | `/report/daily?date=YYYY-MM-DD` | Get daily report |
| GET | `/health` | Health check |

## Example: Creating an Order

```bash
curl -X POST http://localhost:8001/orders \
  -H "Content-Type: application/json" \
  -d '{
    "order_ref": "web-100045",
    "customer_id": "cust-42",
    "items": [
      {"sku": "BAN-001", "qty": 2},
      {"sku": "APL-003", "qty": 1}
    ]
  }'
```

Response (new order):

```json
{
  "order_ref": "web-100045",
  "customer_id": "cust-42",
  "status": "accepted",
  "total_cents": 597,
  "items": [
    {"sku": "BAN-001", "qty": 2, "unit_price_cents": 199},
    {"sku": "APL-003", "qty": 1, "unit_price_cents": 249}
  ],
  "created_at": "2024-01-15T10:30:00Z",
  "duplicate": false
}
```

Submit the same `order_ref` again and you'll get `duplicate: true` — same data, no error, no second queue entry.

## Example: Daily Report

```bash
curl "http://localhost:8002/report/daily?date=2026-01-01"
```

Response:

```json
{
  "date": "2026-01-01",
  "total_orders": 8,
  "revenue_cents": 4500,
  "units_sold": [
    {"sku": "BAN-001", "qty": 10},
    {"sku": "APL-003", "qty": 5}
  ],
  "current_stock": [
    {"sku": "BAN-001", "stock": 40},
    {"sku": "APL-003", "stock": 95}
  ]
}
```

Revenue and units sold come from completed orders only. Current stock is the live value from the database.

## How Idempotency Works

The system handles duplicate order submissions gracefully:

- **First submission** with a given `order_ref`: creates the order, creates the stock queue entry, returns `duplicate: false`
- **Second submission** with the same `order_ref`: returns the existing order, returns `duplicate: true`
- One stock queue entry per order, enforced by the [`UNIQUE(order_ref)`](src/models.py:111) constraint

The client doesn't need to handle errors or retries. They just get the order back, with a flag telling them whether it was new or existing.

## How the Outage/Catch-Up Pattern Works

This is the thing the whole queue design exists for.

When the stock worker is down:
1. Orders are still accepted by the Orders API — they write to the database normally
2. Stock queue entries accumulate in the database
3. Stock levels don't change (the worker isn't running)

When the worker comes back up:
1. It polls `stock_queue`, finds all the pending entries
2. Processes them using [`FOR UPDATE SKIP LOCKED`](src/worker/stock_worker.py:93) — safe even if multiple workers are running
3. Orders are processed atomically — all SKUs in an order are fulfilled together, or none at all
4. Stock levels catch up automatically

No data loss. No manual intervention. The queue table is the buffer.

### Simulating an Outage

```bash
python -m src.simulate_outage
```

This script demonstrates the full pattern:
1. Records current stock levels
2. Stops the stock worker
3. Submits 3 new orders (they get accepted and queued)
4. Restarts the worker
5. Waits for catch-up and verifies stock levels

## How Stock Deduction Works

When the worker processes an order, it does so atomically — all or nothing:

1. **Fetch** the order's line items from `order_items`
2. **Lock** every product involved (`SELECT ... FOR UPDATE`) — no other worker can modify these rows
3. **Check** stock levels against the locked rows — race-free since we hold exclusive locks
4. **Commit** — if all checks pass, deduct all stock and mark the order `completed`. If any check fails, deduct nothing and mark the order `failed`

This prevents the race condition where stock is checked, then consumed by another worker before the deduction happens. The row-level locks hold until the transaction commits.
