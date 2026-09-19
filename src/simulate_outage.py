"""Outage simulation script.

Steps:
1. Record current stock levels
2. Stop the stock worker (send SIGTERM to stock API process)
3. Submit 3 new orders via the Orders API
4. Wait to confirm orders are queued but not processed
5. Restart the worker
6. Wait for catch-up
7. Verify stock levels match expected values
"""

import asyncio
import logging
import os
import sys
from multiprocessing import Process

import httpx2
from uvicorn import run

# Add project root to path for uvicorn.run
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ORDERS_API_PORT = 8001
STOCK_API_PORT = 8002
API_HOST = "localhost"
ORDERS_API_URL = f"http://{API_HOST}:{ORDERS_API_PORT}"
STOCK_API_URL = f"http://{API_HOST}:{STOCK_API_PORT}"

logger = logging.getLogger("simulate_outage")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))


def start_api_server(app: str, port: int):
    """Start a uvicorn API server in a subprocess.

    Args:
        app: Uvicorn app string (e.g. "src.stock_api.main:app").
        port: Port to bind the server to.
    """
    run(app, host=API_HOST, port=port, log_level="warning")


async def check_server_status(server_url: str):
    """Poll the health endpoint until the server responds with 200.

    Args:
        server_url: Base URL of the API server to check.

    Raises:
        Exception: If the server does not become healthy after 10 seconds.
    """
    async with httpx2.AsyncClient(timeout=10) as client:
        for _ in range(20):
            try:
                resp = await client.get(f"{server_url}/health")
                if resp.status_code == 200:
                    logger.info("API started and healthy.")
                    return
            except Exception:
                logger.warning(f"[check_server_status] Error checking server: {server_url}")
            await asyncio.sleep(0.5)
        raise Exception("WARNING: API did not become healthy in time.")


async def record_stock_levels() -> dict[str, int]:
    """Fetch current stock levels for all products via the daily report endpoint.

    Returns:
        Dict mapping SKU to current stock level.
    """
    async with httpx2.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{STOCK_API_URL}/report/daily", params={"date": "2024-01-01"})
        data = resp.json()
        return {item["sku"]: item["stock"] for item in data["current_stock"]}


async def submit_order(order_ref: str, customer_id: str, items: list[dict]) -> dict:
    """Submit an order via the Orders API POST /orders endpoint.

    Args:
        order_ref: Unique order reference.
        customer_id: Customer identifier.
        items: List of {"sku": str, "qty": int} dicts.

    Returns:
        Parsed JSON response from the API.
    """
    async with httpx2.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{ORDERS_API_URL}/orders",
            json={
                "order_ref": order_ref,
                "customer_id": customer_id,
                "items": items,
            },
        )
        return resp.json()


async def check_queue_status() -> dict:
    """Check the status of outage test orders via the Orders API.

    Returns:
        Dict mapping order_ref to order status string.
    """
    async with httpx2.AsyncClient(timeout=10) as client:
        results = {}
        for ref in ["outage-001", "outage-002", "outage-003"]:
            try:
                resp = await client.get(f"{ORDERS_API_URL}/orders/{ref}")
                results[ref] = resp.json()["status"]
            except KeyError:
                results[ref] = "unknown"
        return results


async def verify_stock(expected: dict[str, int]):
    """Compare actual stock levels against expected values and log results.

    Args:
        expected: Dict mapping SKU to expected stock level.
    """
    async with httpx2.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{STOCK_API_URL}/report/daily", params={"date": "2024-01-01"})
        data = resp.json()
        actual = {item["sku"]: item["stock"] for item in data["current_stock"]}

        logger.info("\n=== Stock Verification ===")
        all_match = True
        for sku, exp_stock in expected.items():
            act_stock = actual.get(sku, -1)
            status = "OK" if act_stock == exp_stock else "MISMATCH"
            if status != "OK":
                all_match = False
            logger.info(f"  {sku}: expected={exp_stock}, actual={act_stock} [{status}]")

        if all_match:
            logger.info("\nAll stock levels match expected values. Outage catch-up verified!")
        else:
            logger.info("\nWARNING: Some stock levels do not match. Check the worker logs.")


def _terminate_and_join(proc: Process | None, label: str = "process"):
    """Safely terminate a subprocess and join, swallowing all errors.

    Args:
        proc: Process instance to terminate, or None.
        label: Human-readable label for logging.
    """
    if proc is None:
        return
    try:
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
    except Exception as exc:
        logger.exception(f"[cleanup] Error terminating {label}", exc_info=exc)


async def main():
    """Run the full outage simulation: start APIs, stop worker, submit orders, restart, verify."""
    # Track all live processes for guaranteed cleanup
    stock_api_proc: Process | None = None
    orders_api_proc: Process | None = None
    restarted_stock_api: Process | None = None

    try:
        logger.info("=== Outage Simulation ===")

        # Step 1: Start API servers
        logger.info("\nStep 1: Start API servers")
        stock_api_proc = Process(
            target=start_api_server, kwargs={"app": "src.stock_api.main:app", "port": STOCK_API_PORT}
        )
        stock_api_proc.start()
        orders_api_proc = Process(
            target=start_api_server, kwargs={"app": "src.orders_api.main:app", "port": ORDERS_API_PORT}
        )
        orders_api_proc.start()

        await check_server_status(STOCK_API_URL)
        await check_server_status(ORDERS_API_URL)
        logger.info("Servers healthy")

        # Step 2: Record current stock
        logger.info("\nStep 2: Recording current stock levels...")
        initial_stock = await record_stock_levels()
        logger.info(f"  Initial stock: {initial_stock}")

        # Step 3: Stop the worker
        logger.info("\nStep 3: Stopping the worker...")
        _terminate_and_join(stock_api_proc, "stock_api")
        logger.info("Worker stopped.")

        # Step 4: Submit 3 new orders
        logger.info("\nStep 4: Submitting 3 orders during outage...")
        orders = [
            ("outage-001", "cust-outage-1", [{"sku": "BAN-001", "qty": 3}]),
            ("outage-002", "cust-outage-2", [{"sku": "APL-003", "qty": 5}, {"sku": "MLK-007", "qty": 2}]),
            ("outage-003", "cust-outage-3", [{"sku": "BRD-012", "qty": 1}, {"sku": "EGG-005", "qty": 2}]),
        ]
        results = []
        for ref, cid, items in orders:
            data = await submit_order(ref, cid, items)
            results.append(data)
            logger.info(f"  {ref}: status={data['status']}, total={data['total_cents']} cents")

        # Step 5: Wait and confirm orders are queued
        logger.info("\nStep 5: Waiting to confirm orders are queued...")
        await asyncio.sleep(3)
        queue_status = await check_queue_status()
        logger.info(f"  Order statuses during outage: {queue_status}")

        # Step 6: Restart the worker
        logger.info("\nStep 6: Restarting the worker...")
        restarted_stock_api = Process(
            target=start_api_server, kwargs={"app": "src.stock_api.main:app", "port": STOCK_API_PORT}
        )
        restarted_stock_api.start()

        # Step 7: Wait for catch-up
        logger.info("\nStep 7: Waiting for catch-up (5 seconds)...")
        await asyncio.sleep(5)
        await check_server_status(STOCK_API_URL)

        # Step 8: Verify stock
        logger.info("\nStep 8: Verifying stock levels...")
        expected = dict(initial_stock)
        expected["BAN-001"] = initial_stock.get("BAN-001", 0) - 3
        expected["APL-003"] = initial_stock.get("APL-003", 0) - 5
        expected["MLK-007"] = initial_stock.get("MLK-007", 0) - 2
        expected["BRD-012"] = initial_stock.get("BRD-012", 0) - 1
        expected["EGG-005"] = initial_stock.get("EGG-005", 0) - 2
        await verify_stock(expected)

    except Exception as exc:
        logger.exception("\nOutage simulation failed", exc_info=exc)
        raise
    finally:
        # Guaranteed cleanup: terminate ALL processes that might still be alive
        logger.info("\n[cleanup] Shutting down processes...")
        _terminate_and_join(restarted_stock_api, "restarted_stock_api")
        _terminate_and_join(stock_api_proc, "stock_api")
        _terminate_and_join(orders_api_proc, "orders_api")
        logger.info("[cleanup] Done.")


if __name__ == "__main__":
    asyncio.run(main())
