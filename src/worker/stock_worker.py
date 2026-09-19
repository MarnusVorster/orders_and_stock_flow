"""Background worker that polls stock_queue and deducts product stock.

Fetches pending StockQueue rows, extracts distinct order_refs, and processes
each order atomically: locks all products first, then checks stock for
every SKU. Either all items are fulfilled or none are — no partial
fulfillment. On exception, the order is left pending for retry on the next
poll cycle.
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.database import async_session_factory
from src.models import Order, OrderItem, Product, StockQueue

settings = get_settings()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))


class StockWorker:
    """Background worker that polls stock_queue and deducts product stock.

    Runs a continuous asyncio task that polls for pending StockQueue rows
    using SELECT FOR UPDATE SKIP LOCKED for safe concurrent processing
    across multiple worker instances. Each order is processed atomically:
    all products are locked, stock is checked, and if all pass, stock is
    deducted and the order is marked completed. If any check fails, the
    order is marked failed. On unexpected exceptions, the order is left
    pending for retry.
    """

    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self):
        """Start the worker polling loop as a background asyncio task."""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self):
        """Signal the worker to stop and wait for the task to finish."""
        self._running = False
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self):
        """Main polling loop: fetch pending queue rows, extract distinct order_refs, process each.

        Runs continuously while self._running is True. Sleeps between polls
        if no pending rows are found. Retries on errors without failing.

        Uses SKIP LOCKED so concurrent worker instances grab different rows.
        LIMIT 10 caps at 10 queue rows (not orders)

        For each batch of rows:
        1. SELECT pending StockQueue rows with SKIP LOCKED (limit 10, ordered by created_at newest first)
        2. Extract order_refs from the result
        3. Process each order via _process_order() within the same session
        4. Commit all changes at the end of the batch

        On any exception during batch processing, the error is logged and
        the worker sleeps before retrying — no partial commits occur.
        """
        while self._running:
            try:
                async with async_session_factory() as session:
                    # Fetch pending StockQueue rows with SKIP LOCKED.
                    # SKIP LOCKED ensures concurrent workers grab different rows.
                    # LIMIT 10 caps at 10 rows (pending orders)
                    order_refs_result = await session.execute(
                        select(StockQueue.order_ref)
                        .where(StockQueue.status == "pending")
                        .order_by(StockQueue.created_at.asc())
                        .limit(10)
                        .with_for_update(skip_locked=True)
                    )
                    # Extract distinct order_ref values from fetched rows
                    order_refs = [row[0] for row in order_refs_result.fetchall()]

                    # If no pending orders, sleep until next poll.
                    if not order_refs:
                        await asyncio.sleep(settings.worker_poll_interval_seconds)
                        continue

                    for order_ref in order_refs:
                        await self._process_order(session, order_ref)

                    await session.commit()
                    await asyncio.sleep(settings.worker_poll_interval_seconds)

            except Exception as ex:
                logger.exception("Error processing stock_queue rows", exc_info=ex)
                await asyncio.sleep(settings.worker_poll_interval_seconds)

    async def _process_order(self, session: AsyncSession, order_ref: str):
        """Process a single order atomically with all-or-nothing semantics.

        Workflow:
          1. Fetch OrderItem rows for the order
          2. Lock every Product involved (with_for_update, one at a time)
             — if a product is missing, mark order failed and return
          3. Check stock for every SKU against the locked rows
             — if any SKU has insufficient stock, mark order failed and return
          4. If ALL checks pass → deduct all stock, mark queue processed, set order completed

        On unexpected exceptions (database errors, etc.), the order is NOT
        marked as failed — it remains pending and will be retried on the next
        poll cycle.

        This prevents the race condition where stock is checked, then
        consumed by another worker before the deduction happens, by holding
        row-level locks (SELECT FOR UPDATE) throughout the check-and-deduct
        operation.

        Args:
            session: Async database session with active transaction.
            order_ref: Unique order reference.
        """
        try:
            # Fetch OrderItem rows for this order
            order_items_result = await session.execute(select(OrderItem).where(OrderItem.order_ref == order_ref))
            order_items = order_items_result.scalars().all()

            # Step 1: Lock ALL products first (one at a time)
            # By acquiring locks before checking stock, we prevent another
            # worker from consuming stock between our check and our
            # deduction. The locks are held until the session commits.
            products_map: dict[str, Product] = {}
            for order_item in order_items:
                product_result = await session.execute(
                    select(Product).where(Product.sku == order_item.sku).with_for_update()
                )
                product = product_result.scalar_one_or_none()

                if product is None:
                    logger.warning(
                        f"Order {order_ref} references unknown SKU {order_item.sku} — failing order",
                    )
                    await self._mark_order_failed(session, order_ref)
                    return

                products_map[order_item.sku] = product

            # Step 2: Check stock on already-locked rows
            # No other worker can modify these products now — we hold
            # exclusive row-level locks. Safe to check.
            for order_item in order_items:
                product = products_map[order_item.sku]
                if product.stock < order_item.qty:
                    logger.warning(
                        f"Order {order_ref} SKU {order_item.sku}: need {order_item.qty}, have {product.stock} — failing order",
                    )
                    await self._mark_order_failed(session, order_ref)
                    return

            # Step 3: All checks passed — deduct stock atomically
            for order_item in order_items:
                product = products_map[order_item.sku]
                product.stock -= order_item.qty

            # Mark queue entry as processed
            queue_entry_result = await session.execute(
                select(StockQueue).where(StockQueue.order_ref == order_ref, StockQueue.status == "pending")
            )
            queue_entry = queue_entry_result.scalar_one()
            queue_entry.status = "processed"
            queue_entry.processed_at = datetime.now(timezone.utc)

            # Mark order as completed (with lock to prevent concurrent updates)
            order_result = await session.execute(select(Order).where(Order.order_ref == order_ref).with_for_update())
            order = order_result.scalar_one_or_none()
            if order:
                order.status = "completed"
                order.updated_at = datetime.now(timezone.utc)

            logger.info("Order %s fulfilled", order_ref)

        except Exception as ex:
            # Unexpected error (e.g., database connection loss).
            # Do NOT mark as failed — leave order pending so it retries
            # on the next poll cycle. The session transaction is rolled
            # back automatically when the context exits.
            logger.exception(f"Error processing order {order_ref}", exc_info=ex)

    async def _mark_order_failed(
        self,
        session: AsyncSession,
        order_ref: str,
    ):
        """Mark the queue entry and the order as failed.

        Called when validation fails:
          - Missing product (SKU not found in products table)
          - Insufficient stock for any order item

        No stock is deducted — the order simply fails and remains in the
        database with status "failed" for manual review or reprocessing.

        Args:
            session: Async database session with active transaction.
            order_ref: Unique order reference.
        """
        # Update queue row: mark as failed
        queue_entry_result = await session.execute(
            select(StockQueue).where(StockQueue.order_ref == order_ref, StockQueue.status == "pending")
        )
        queue_entry = queue_entry_result.scalar_one_or_none()
        if queue_entry:
            queue_entry.status = "failed"
            queue_entry.processed_at = datetime.now(timezone.utc)

        order_result = await session.execute(select(Order).where(Order.order_ref == order_ref))
        order = order_result.scalar_one_or_none()
        if order:
            order.status = "failed"
            order.updated_at = datetime.now(timezone.utc)
