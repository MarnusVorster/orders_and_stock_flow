# Orders & Stock Flow — How It Works

## The Setup

Two FastAPI services, one PostgreSQL database. That's the whole architecture. No message brokers, no Redis, no Celery. Just two processes that happen to share a database.

The **Orders API** (port 8001) takes orders and hands them off. It doesn't check stock, it doesn't care about fulfillment — it writes the order, writes the line items, pushes a queue entry, and returns. Done.

The **Stock API + Worker** (port 8002) manages products, tracks stock levels, serves reports. It also runs a background asyncio task that quietly drains the queue table, deducting stock one batch at a time. The worker starts when the API boots and shuts down cleanly when the process dies. It's wired in via FastAPI's `lifespan` context manager — standard pattern for lightweight background work.

## The Database

Four tables. That's it.

### [`products`](src/models.py:18)

The SKU is the primary key — a `VARCHAR(20)`, not an auto-incrementing integer. That's deliberate. SKUs are the natural identifier here, and using them as PKs means every join is direct, no lookups through a surrogate key.

| Column | Type | Notes |
|---|---|---|
| `sku` | VARCHAR(20) | Primary key |
| `name` | VARCHAR(200) | Human-readable |
| `price_cents` | INTEGER | Price in cents |
| `stock` | INTEGER | Mutable — the worker deducts from this |
| `created_at` | TIMESTAMPTZ | Server-managed via `func.now()` |
| `updated_at` | TIMESTAMPTZ | Server-managed, auto-updates on row change |

### [`orders`](src/models.py:44)

The `order_ref` is the primary key here too — it doubles as the idempotency token. When a client sends `order_ref: "web-100045"`, that value becomes the PK. If the same ref comes in again, we find the row by PK and return it. Simple.

| Column | Type | Notes |
|---|---|---|
| `order_ref` | VARCHAR(50) | Primary key, also idempotency key |
| `customer_id` | VARCHAR(100) | Customer identifier |
| `total_cents` | INTEGER | Calculated at creation time |
| `status` | VARCHAR(20) | `accepted` → `completed` or `failed` |
| `created_at` | TIMESTAMPTZ | Server-managed |
| `updated_at` | TIMESTAMPTZ | Server-managed |

### [`order_items`](src/models.py:71)

Standard line items table. The `unit_price_cents` is a snapshot — it captures the price at order time so that if the product price changes later, historical orders aren't affected.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL | Primary key |
| `order_ref` | VARCHAR(50) | FK → `orders.order_ref` |
| `sku` | VARCHAR(20) | FK → `products.sku` |
| `qty` | INTEGER | Quantity ordered |
| `unit_price_cents` | INTEGER | Price snapshot (immutable) |

### [`stock_queue`](src/models.py:93)

This is the glue between the two services. When an order is created, the Orders API writes a row here. The Stock Worker reads it and processes it. It's a table-backed queue — not a message broker, but sufficient for this scope.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL | Primary key |
| `order_ref` | VARCHAR(50) | FK → `orders.order_ref` |
| `status` | VARCHAR(20) | `pending` → `processed` or `failed` |
| `created_at` | TIMESTAMPTZ | Server-managed |
| `processed_at` | TIMESTAMPTZ | Nullable — set when processed |

Two table-level constraints:
- [`UNIQUE(order_ref)`](src/models.py:111) — prevents duplicate queue entries per order
- [`INDEX(status)`](src/models.py:112) — the worker queries `WHERE status = 'pending'`, this index makes that fast

The queue is order-level, not per-SKU. Each order gets one queue entry. The background worker fetches `OrderItem` rows from the `order_items` table to determine which SKUs and quantities to deduct. This simplifies the queue model and avoids stale data issues.

## Design Decisions

### Why a DB-backed queue instead of RabbitMQ or Redis?

Because the requirements explicitly allow "one process or split into two" and there's no mention of high throughput or complex routing. A PostgreSQL table with [`FOR UPDATE SKIP LOCKED`](src/worker/stock_worker.py:93) gives us ACID guarantees, concurrent-safe consumption, zero extra infrastructure, and simple deployment.

The trade-off is lower throughput and no dead-letter queue or retry semantics. Neither matters for this scope. Polling introduces slight latency — the worker checks every 1 second by default — but that's acceptable.

### Why two processes instead of one?

Demonstrates independent evolution and deployment. The Orders API can be scaled, deployed, or taken down independently of the Stock API. It also enables the outage pattern — you can simulate a worker outage by stopping just the Stock API process.

The trade-off is managing two processes. But that's realistic and mirrors how microservice-like architectures actually work in the wild.

### Why return 200 for duplicates instead of 409?

Because it's friendlier. The client doesn't need to distinguish between "I haven't sent this order yet" and "I sent this order twice, here it is again." They just get the order back with a `duplicate` flag. No error handling, no retry logic. Consistent with REST idempotency best practices.

### Why asyncio instead of Celery?

Keeps deployment to two processes. Celery adds a broker (Redis/RabbitMQ), a worker process, and configuration overhead. For a system where the worker polls a single table at 1-second intervals, asyncio is the right tool. It shares the DB connection pool with the Stock API, doesn't block API requests, and shuts down gracefully via the lifespan teardown.

### Why async SQLAlchemy 2.0?

Matches FastAPI's async patterns. The `asyncpg` driver is fast. The 2.0 API (`select()` style queries) is cleaner than the legacy ORM. And critically, it's non-blocking — both the API handlers and the worker use the same async session factory without blocking each other.

## How Order Creation Works

When [`POST /orders`](src/orders_api/routes.py:15) hits the Orders API, here's the sequence:

1. **Check for existing order** — query [`orders`](src/orders_api/service.py:64) by `order_ref`. If found, load the items and return the existing order with `duplicate: true`. No error, no 409. Just the same data you'd get from the first call.

2. **Validate items** — look up each SKU in [`products`](src/orders_api/service.py:23). If any SKU doesn't exist, raise a 422. Calculate `total_cents` by summing `price_cents * qty` for each line item.

3. **Write everything in one transaction** — insert the order row, insert the order item rows, insert the stock queue entry. Then [`commit()`](src/orders_api/service.py:114). If anything fails, the whole thing rolls back.

4. **Return 200 with `duplicate: false`** — the client gets the full order back with items, no error handling needed on their side.

The key insight: the Orders API doesn't touch stock at all. It creates the queue entry and walks away. The worker handles the actual stock deduction asynchronously.

## How the Worker Drains the Queue

The [`StockWorker`](src/worker/stock_worker.py:31) class runs inside the Stock API process. It's started in the [`lifespan`](src/stock_api/main.py:16) context manager and runs an infinite asyncio task:

```
_poll_loop() → repeat forever:
    1. SELECT pending rows with FOR UPDATE SKIP LOCKED (batch of 10)
    2. If no rows, sleep 1 second and try again
    3. For each row, call _process_order()
    4. Commit the transaction
    5. If any exception, log it, sleep 1 second, retry
```

The [`FOR UPDATE SKIP LOCKED`](src/worker/stock_worker.py:93) is the critical part. It means:
- Multiple worker instances can run simultaneously without stepping on each other
- If one worker locks a row, another worker skips it and takes the next available one
- This is safe for horizontal scaling, even though we only run one instance in practice

### Processing a Single Order

For each pending queue entry, the worker processes the entire order atomically — all or nothing, no partial fulfillment:

1. **Fetch phase** — [`OrderItem`](src/worker/stock_worker.py:139) rows for the order from the `order_items` table to determine SKUs and quantities.

2. **Lock phase** — [`SELECT ... FOR UPDATE`](src/worker/stock_worker.py:151) on every product in the order. This prevents concurrent stock mutations. The locks are held until the session commits.

3. **Check phase** — stock levels are verified against the locked rows. No other worker can modify these products now — we hold exclusive row-level locks. Safe to check.

4. **Commit phase** — if all checks pass, all stock is deducted and the queue entry is marked `processed`, order status → `completed`. If any check fails, the queue entry is marked `failed`, order status → `failed`, with zero stock deducted.

If an unexpected error occurs (database connection loss, etc.), the order is **not** marked as failed. It remains `pending` and will be retried on the next poll cycle. The session transaction rolls back automatically.

## The Unhappy Paths

### Duplicate Order Submission

Handled at the very top of [`create_order()`](src/orders_api/service.py:63). We check for the `order_ref` before doing any validation or computation. If it exists, we return the existing order immediately. The [`UNIQUE(order_ref)`](src/models.py:111) constraint on `stock_queue` is a safety net — it would catch duplicates if the application-level check somehow failed, but in practice the application-level check handles it cleanly.

### Worker Downtime (Outage)

This is the whole point of the queue design. If the Stock API process dies:

1. Orders keep getting accepted by the Orders API — they write to `orders`, `order_items`, and `stock_queue` normally
2. Stock levels don't change because the worker isn't running
3. When the Stock API comes back up, the worker starts, polls `stock_queue`, finds all the pending entries, and processes them
4. Stock catches up

No data loss. No manual intervention. The queue table is the buffer.

### Insufficient Stock

When the worker tries to deduct stock and there isn't enough, it marks the queue entry as [`failed`](src/worker/stock_worker.py:176) and the order as `failed`. That's it. No retry, no backorder, no complex state machine. For this scope, it's the right call — adding retry semantics would require tracking retry counts, implementing exponential backoff, and handling dead-letter scenarios. All of that is overkill until you actually need it.

## File Structure

```
src/
├── config.py              # Settings from .env — database URL, ports, poll interval
├── database.py            # Engine, session factory, get_session dependency
├── models.py              # ORM models — Product, Order, OrderItem, StockQueue
├── seed.py                # Populates initial products and orders via HTTP
├── simulate_outage.py     # Demonstrates the outage → catch-up pattern
├── orders_api/
│   ├── main.py            # FastAPI app, /health endpoint
│   ├── routes.py          # POST /orders, GET /orders/{ref}
│   ├── schemas.py         # Pydantic models for request/response
│   └── service.py         # create_order(), get_order() — the actual logic
├── stock_api/
│   ├── main.py            # FastAPI app, lifespan for worker lifecycle
│   ├── routes.py          # POST/GET/PUT /stock, GET /report/daily
│   ├── schemas.py         # Pydantic models
│   └── service.py         # create_product(), update_product_stock(), get_daily_report()
└── worker/
    └── stock_worker.py    # StockWorker class — _poll_loop(), _process_order()
```

The separation is clean: `main.py` wires things up, `routes.py` maps HTTP to service calls, `service.py` contains the business logic, and `schemas.py` defines the contract. No circular dependencies, no tangled imports.
