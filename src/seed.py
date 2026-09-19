"""Seed script: creates 5 products via the Stock API and 10 orders (3 duplicates) via the Orders API."""

import asyncio

import httpx2

STOCK_API_URL = "http://localhost:8002"
ORDERS_API_URL = "http://localhost:8001"

PRODUCTS = [
    {"sku": "BAN-001", "name": "Bananas 1kg", "price_cents": 199, "stock": 50},
    {"sku": "APL-003", "name": "Apples 1kg", "price_cents": 249, "stock": 100},
    {"sku": "MLK-007", "name": "Whole Milk 1L", "price_cents": 159, "stock": 80},
    {"sku": "BRD-012", "name": "Sourdough Bread", "price_cents": 399, "stock": 30},
    {"sku": "EGG-005", "name": "Eggs (6 pack)", "price_cents": 299, "stock": 60},
]

ORDERS = [
    {
        "order_ref": "web-100041",
        "customer_id": "cust-01",
        "items": [{"sku": "BAN-001", "qty": 2}, {"sku": "MLK-007", "qty": 1}],
    },
    {
        "order_ref": "web-100042",
        "customer_id": "cust-02",
        "items": [{"sku": "APL-003", "qty": 3}, {"sku": "EGG-005", "qty": 1}],
    },
    {
        "order_ref": "web-100043",
        "customer_id": "cust-03",
        "items": [{"sku": "BRD-012", "qty": 1}],
    },
    {
        "order_ref": "web-100044",
        "customer_id": "cust-04",
        "items": [{"sku": "BAN-001", "qty": 5}, {"sku": "MLK-007", "qty": 2}, {"sku": "APL-003", "qty": 1}],
    },
    {
        "order_ref": "web-100045",
        "customer_id": "cust-42",
        "items": [{"sku": "BAN-001", "qty": 2}, {"sku": "APL-003", "qty": 1}],
    },
    {
        "order_ref": "web-100046",
        "customer_id": "cust-06",
        "items": [{"sku": "EGG-005", "qty": 2}, {"sku": "BRD-012", "qty": 1}],
    },
    {
        "order_ref": "web-100047",
        "customer_id": "cust-07",
        "items": [{"sku": "MLK-007", "qty": 4}],
    },
    # Duplicates (same refs as above)
    {
        "order_ref": "web-100041",  # duplicate
        "customer_id": "cust-01",
        "items": [{"sku": "BAN-001", "qty": 2}, {"sku": "MLK-007", "qty": 1}],
    },
    {
        "order_ref": "web-100045",  # duplicate
        "customer_id": "cust-42",
        "items": [{"sku": "BAN-001", "qty": 2}, {"sku": "APL-003", "qty": 1}],
    },
    {
        "order_ref": "web-100047",  # duplicate
        "customer_id": "cust-07",
        "items": [{"sku": "MLK-007", "qty": 4}],
    },
]


async def seed_products():
    """Create products via the Stock API POST /stock endpoint.

    Sends each product from PRODUCTS list. Skips any that already
    exist (409 Conflict). Prints a summary on completion.
    """
    async with httpx2.AsyncClient(timeout=10) as client:
        # Verify Stock API is reachable
        resp = await client.get(f"{STOCK_API_URL}/health")
        if resp.status_code == 200:
            print("Stock API is reachable.")
        else:
            print(f"Stock API health check failed: {resp.status_code}")
            return

        created_count = 0
        skipped_count = 0

        for p in PRODUCTS:
            resp = await client.post(f"{STOCK_API_URL}/stock", json=p)
            if resp.status_code == 201:
                created_count += 1
                data = resp.json()
                print(f"  CREATED: {data['sku']} - {data['name']} (stock: {data['stock']})")
            elif resp.status_code == 409:
                skipped_count += 1
                print(f"  SKIPPED: {p['sku']} - already exists (409 Conflict)")
            else:
                print(f"  ERROR: {p['sku']} - {resp.status_code} {resp.text}")

        print(f"\nSeeded {created_count} new products, {skipped_count} already existed.")


async def seed_orders():
    """Submit orders via the Orders API, including duplicates.

    Sends each order from ORDERS list. Reports CREATED vs DUPLICATE
    responses and prints a summary on completion.
    """
    async with httpx2.AsyncClient(timeout=30) as client:
        new_count = 0
        dup_count = 0
        for i, order in enumerate(ORDERS):
            resp = await client.post(f"{ORDERS_API_URL}/orders", json=order)
            data = resp.json()
            is_dup = data.get("duplicate", False)
            if is_dup:
                dup_count += 1
                print(f"  [{i + 1}] DUPLICATE: {order['order_ref']} (customer: {order['customer_id']})")
            else:
                new_count += 1
                print(f"  [{i + 1}] CREATED: {order['order_ref']} (total: {data['total_cents']} cents)")

        print(f"\nSeeded {new_count} new orders + {dup_count} duplicates = {new_count + dup_count} total submissions.")


async def main():
    """Entry point: seed products first, then orders."""
    print("=== Seeding Products (Stock API) ===")
    await seed_products()

    print("\n=== Seeding Orders (Orders API) ===")
    await seed_orders()

    print("\n=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
