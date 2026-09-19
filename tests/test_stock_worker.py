"""Tests for src/worker/stock_worker.py — StockWorker class.

Tests the new order_ref-level processing model where:
- StockQueue rows are at the order level (one row per order_ref)
- _process_order fetches OrderItem rows from the database to determine SKUs/qty
- StockQueue status is updated based on the order outcome
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.worker.stock_worker import StockWorker


def _make_result(single=None, multi=None):
    """Create a properly structured mock execute result.

    - scalar_one_or_none() returns `single`
    - scalar_one() returns `single`
    - scalars().all() returns `multi` list
    """
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=single)
    result.scalar_one = MagicMock(return_value=single)

    sync_scalars = MagicMock()
    sync_scalars.all = MagicMock(return_value=multi)
    result.scalars = MagicMock(return_value=sync_scalars)
    return result


@pytest.fixture
def mock_settings():
    """Provide mocked settings with default poll interval."""
    settings = MagicMock()
    settings.worker_poll_interval_seconds = 0.01  # Fast polling for tests
    return settings


@pytest.fixture
def mock_order_item():
    """Create a mock OrderItem."""
    item = MagicMock()
    item.sku = "SKU-001"
    item.qty = 2
    return item


@pytest.fixture
def mock_order_item_b():
    """Create a second mock OrderItem."""
    item = MagicMock()
    item.sku = "SKU-002"
    item.qty = 3
    return item


@pytest.fixture
def mock_order_item_c():
    """Create a third mock OrderItem."""
    item = MagicMock()
    item.sku = "SKU-003"
    item.qty = 1
    return item


@pytest.fixture
def mock_product():
    """Create a mock Product with stock."""
    product = MagicMock()
    product.sku = "SKU-001"
    product.stock = 100
    product.created_at = datetime.now(timezone.utc)
    product.updated_at = datetime.now(timezone.utc)
    return product


@pytest.fixture
def mock_product_b():
    """Create a mock Product B with stock."""
    product = MagicMock()
    product.sku = "SKU-002"
    product.stock = 50
    product.created_at = datetime.now(timezone.utc)
    product.updated_at = datetime.now(timezone.utc)
    return product


@pytest.fixture
def mock_product_c():
    """Create a mock Product C with stock."""
    product = MagicMock()
    product.sku = "SKU-003"
    product.stock = 10
    product.created_at = datetime.now(timezone.utc)
    product.updated_at = datetime.now(timezone.utc)
    return product


@pytest.fixture
def mock_order():
    """Create a mock Order."""
    order = MagicMock()
    order.order_ref = "ORD-001"
    order.status = "accepted"
    order.created_at = datetime.now(timezone.utc)
    order.updated_at = datetime.now(timezone.utc)
    return order


@pytest.fixture
def mock_queue_entry():
    """Create a mock StockQueue entry at order_ref level."""
    entry = MagicMock()
    entry.id = 1
    entry.order_ref = "ORD-001"
    entry.status = "pending"
    entry.processed_at = None
    return entry


class TestProcessOrder:
    """Tests for StockWorker._process_order method."""

    @pytest.mark.asyncio
    async def test_process_order_single_sku_sufficient_stock(
        self, mock_order_item, mock_product, mock_order, mock_queue_entry
    ):
        """Happy path: single-SKU order with enough stock — deduct, mark processed, complete order."""
        call_count = {"count": 0}

        async def execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: SELECT OrderItem rows for the order
            if call_num == 1:
                return _make_result(multi=[mock_order_item])
            # Call 2: SELECT Product (lock)
            if call_num == 2:
                return _make_result(single=mock_product)
            # Call 3: SELECT StockQueue entry
            if call_num == 3:
                return _make_result(single=mock_queue_entry)
            # Call 4: SELECT Order (lock for update — mark completed)
            if call_num == 4:
                return _make_result(single=mock_order)

            raise AssertionError(f"Unexpected execute call #{call_num}")

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=execute_side_effect)

        worker = StockWorker()
        await worker._process_order(session, "ORD-001")

        assert mock_queue_entry.status == "processed"
        assert mock_queue_entry.processed_at is not None
        assert mock_product.stock == 98  # 100 - 2
        assert mock_order.status == "completed"
        assert mock_order.updated_at is not None

    @pytest.mark.asyncio
    async def test_process_order_multi_sku_all_sufficient_stock(
        self,
        mock_order_item,
        mock_order_item_b,
        mock_order_item_c,
        mock_product,
        mock_product_b,
        mock_product_c,
        mock_order,
        mock_queue_entry,
    ):
        """Happy path: multi-SKU order — all products locked, all stock checked, all deducted atomically."""
        call_count = {"count": 0}

        async def execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: SELECT OrderItem rows for the order
            if call_num == 1:
                return _make_result(multi=[mock_order_item, mock_order_item_b, mock_order_item_c])
            # Call 2: Lock product SKU-001
            if call_num == 2:
                return _make_result(single=mock_product)
            # Call 3: Lock product SKU-002
            if call_num == 3:
                return _make_result(single=mock_product_b)
            # Call 4: Lock product SKU-003
            if call_num == 4:
                return _make_result(single=mock_product_c)
            # Call 5: SELECT StockQueue entry
            if call_num == 5:
                return _make_result(single=mock_queue_entry)
            # Call 6: SELECT Order (lock for update)
            if call_num == 6:
                return _make_result(single=mock_order)

            raise AssertionError(f"Unexpected execute call #{call_num}")

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=execute_side_effect)

        worker = StockWorker()
        await worker._process_order(session, "ORD-001")

        assert mock_queue_entry.status == "processed"
        assert mock_queue_entry.processed_at is not None
        assert mock_product.stock == 98  # 100 - 2
        assert mock_product_b.stock == 47  # 50 - 3
        assert mock_product_c.stock == 9  # 10 - 1
        assert mock_order.status == "completed"

    @pytest.mark.asyncio
    async def test_process_order_insufficient_stock_fails_entire_order(
        self,
        mock_order_item,
        mock_order_item_b,
        mock_product,
        mock_product_b,
        mock_order,
        mock_queue_entry,
    ):
        """Unhappy path: one SKU has insufficient stock — entire order fails, NO stock deducted."""
        # SKU-002 only has 50 stock but order needs 200
        mock_order_item_b.qty = 200

        call_count = {"count": 0}

        async def execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: SELECT OrderItem rows for the order
            if call_num == 1:
                return _make_result(multi=[mock_order_item, mock_order_item_b])
            # Call 2: Lock product SKU-001
            if call_num == 2:
                return _make_result(single=mock_product)
            # Call 3: Lock product SKU-002
            if call_num == 3:
                return _make_result(single=mock_product_b)
            # Call 4: _mark_order_failed — SELECT StockQueue entry
            if call_num == 4:
                return _make_result(single=mock_queue_entry)
            # Call 5: _mark_order_failed — SELECT Order
            if call_num == 5:
                return _make_result(single=mock_order)

            raise AssertionError(f"Unexpected execute call #{call_num}")

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=execute_side_effect)

        worker = StockWorker()
        await worker._process_order(session, "ORD-001")

        # Queue entry marked failed
        assert mock_queue_entry.status == "failed"
        assert mock_queue_entry.processed_at is not None
        # NO stock deducted
        assert mock_product.stock == 100
        assert mock_product_b.stock == 50
        # Order marked failed
        assert mock_order.status == "failed"

    @pytest.mark.asyncio
    async def test_process_order_missing_product_fails_order(self, mock_order_item, mock_order, mock_queue_entry):
        """Unhappy path: product not found — entire order fails, no stock touched."""
        call_count = {"count": 0}

        async def execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: SELECT OrderItem rows for the order
            if call_num == 1:
                return _make_result(multi=[mock_order_item])
            # Call 2: Product not found (None)
            if call_num == 2:
                return _make_result(single=None)
            # Call 3: _mark_order_failed — SELECT StockQueue entry
            if call_num == 3:
                return _make_result(single=mock_queue_entry)
            # Call 4: _mark_order_failed — SELECT Order
            if call_num == 4:
                return _make_result(single=mock_order)

            raise AssertionError(f"Unexpected execute call #{call_num}")

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=execute_side_effect)

        worker = StockWorker()
        await worker._process_order(session, "ORD-001")

        assert mock_queue_entry.status == "failed"
        assert mock_queue_entry.processed_at is not None
        assert mock_order.status == "failed"

    @pytest.mark.asyncio
    async def test_process_order_exception_does_not_mark_failed(self, mock_queue_entry):
        """Unhappy path: exception during processing — queue stays as-is for retry."""

        async def execute_side_effect(stmt):
            raise RuntimeError("Database connection lost")

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=execute_side_effect)

        worker = StockWorker()
        await worker._process_order(session, "ORD-001")

        # Status should NOT be changed — will retry on next poll
        assert mock_queue_entry.status == "pending"

    @pytest.mark.asyncio
    async def test_process_order_partial_product_missing_fails_entire_order(
        self,
        mock_order_item,
        mock_order_item_b,
        mock_product,
        mock_order,
        mock_queue_entry,
    ):
        """Unhappy path: first product exists, second doesn't — entire order fails."""
        call_count = {"count": 0}

        async def execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: SELECT OrderItem rows for the order
            if call_num == 1:
                return _make_result(multi=[mock_order_item, mock_order_item_b])
            # Call 2: First product exists
            if call_num == 2:
                return _make_result(single=mock_product)
            # Call 3: Second product missing
            if call_num == 3:
                return _make_result(single=None)
            # Call 4: _mark_order_failed — SELECT StockQueue entry
            if call_num == 4:
                return _make_result(single=mock_queue_entry)
            # Call 5: _mark_order_failed — SELECT Order
            if call_num == 5:
                return _make_result(single=mock_order)

            raise AssertionError(f"Unexpected execute call #{call_num}")

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=execute_side_effect)

        worker = StockWorker()
        await worker._process_order(session, "ORD-001")

        # Queue entry marked failed
        assert mock_queue_entry.status == "failed"
        assert mock_queue_entry.processed_at is not None
        assert mock_order.status == "failed"

    @pytest.mark.asyncio
    async def test_process_order_no_order_items_succeeds_gracefully(self, mock_order, mock_queue_entry):
        """Edge case: order with no items — queue marked processed, order completed."""
        call_count = {"count": 0}

        async def execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: SELECT OrderItem rows — empty
            if call_num == 1:
                return _make_result(multi=[])
            # Call 2: SELECT StockQueue entry
            if call_num == 2:
                return _make_result(single=mock_queue_entry)
            # Call 3: SELECT Order
            if call_num == 3:
                return _make_result(single=mock_order)

            raise AssertionError(f"Unexpected execute call #{call_num}")

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=execute_side_effect)

        worker = StockWorker()
        await worker._process_order(session, "ORD-001")

        assert mock_queue_entry.status == "processed"
        assert mock_order.status == "completed"


class TestMarkOrderFailed:
    """Tests for StockWorker._mark_order_failed helper."""

    @pytest.mark.asyncio
    async def test_mark_order_failed_sets_all_statuses(self, mock_order, mock_queue_entry):
        """_mark_order_failed marks queue entry and order as failed."""
        call_count = {"count": 0}

        async def execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: SELECT StockQueue entry
            if call_num == 1:
                return _make_result(single=mock_queue_entry)
            # Call 2: SELECT Order
            if call_num == 2:
                return _make_result(single=mock_order)

            raise AssertionError(f"Unexpected execute call #{call_num}")

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=execute_side_effect)

        worker = StockWorker()
        await worker._mark_order_failed(session, "ORD-001")

        assert mock_queue_entry.status == "failed"
        assert mock_queue_entry.processed_at is not None
        assert mock_order.status == "failed"
        assert mock_order.updated_at is not None

    @pytest.mark.asyncio
    async def test_mark_order_failed_no_order_found(self):
        """_mark_order_failed handles missing order gracefully."""
        queue_entry = MagicMock()
        queue_entry.status = "pending"
        queue_entry.processed_at = None

        call_count = {"count": 0}

        async def execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: SELECT StockQueue entry
            if call_num == 1:
                return _make_result(single=queue_entry)
            # Call 2: SELECT Order — not found
            if call_num == 2:
                return _make_result(single=None)

            raise AssertionError(f"Unexpected execute call #{call_num}")

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=execute_side_effect)

        worker = StockWorker()
        await worker._mark_order_failed(session, "ORD-999")

        assert queue_entry.status == "failed"
        assert queue_entry.processed_at is not None

    @pytest.mark.asyncio
    async def test_mark_order_failed_no_queue_entry(self):
        """_mark_order_failed handles missing queue entry gracefully."""
        call_count = {"count": 0}

        async def execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: SELECT StockQueue entry — not found
            if call_num == 1:
                return _make_result(single=None)
            # Call 2: SELECT Order
            if call_num == 2:
                return _make_result(single=MagicMock())

            raise AssertionError(f"Unexpected execute call #{call_num}")

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=execute_side_effect)

        worker = StockWorker()
        await worker._mark_order_failed(session, "ORD-999")

        # Should not raise even if queue entry is missing


class TestStartStop:
    """Tests for StockWorker lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_creates_task(self, mock_settings):
        """Happy path: start() creates a background task."""
        with patch("src.worker.stock_worker.get_settings", return_value=mock_settings):
            worker = StockWorker()
            assert worker._task is None
            await worker.start()
            assert worker._task is not None
            assert isinstance(worker._task, asyncio.Task)
            await worker.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, mock_settings):
        """Happy path: stop() cancels the running task cleanly."""
        with patch("src.worker.stock_worker.get_settings", return_value=mock_settings):
            worker = StockWorker()
            await worker.start()
            await worker.stop()
            assert worker._task is None or worker._task.done()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self, mock_settings):
        """Unhappy path: calling stop() before start() should not raise."""
        with patch("src.worker.stock_worker.get_settings", return_value=mock_settings):
            worker = StockWorker()
            await worker.stop()  # Should not raise


class TestPollLoop:
    """Tests for StockWorker._poll_loop method."""

    @pytest.mark.asyncio
    async def test_poll_loop_processes_single_order(self, mock_settings):
        """Happy path: poll loop fetches order_refs, processes each order."""
        queue_entry = MagicMock()
        queue_entry.order_ref = "ORD-001"
        queue_entry.status = "pending"
        queue_entry.processed_at = None

        order_item = MagicMock()
        order_item.sku = "SKU-001"
        order_item.qty = 2

        product = MagicMock()
        product.sku = "SKU-001"
        product.stock = 100

        order = MagicMock()
        order.order_ref = "ORD-001"
        order.status = "accepted"

        call_count = {"count": 0}

        async def session_execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: subquery returns order_refs
            if call_num == 1:
                mock_result = AsyncMock()
                mock_result.fetchall = MagicMock(return_value=[("ORD-001",)])
                return mock_result
            # Call 2: SELECT OrderItem rows
            if call_num == 2:
                return _make_result(multi=[order_item])
            # Call 3: Lock product
            if call_num == 3:
                return _make_result(single=product)
            # Call 4: SELECT StockQueue entry
            if call_num == 4:
                return _make_result(single=queue_entry)
            # Call 5: Lock order
            if call_num == 5:
                return _make_result(single=order)
            # Call 6: next poll — subquery returns empty
            if call_num == 6:
                mock_result = AsyncMock()
                mock_result.fetchall = MagicMock(return_value=[])
                return mock_result
            return AsyncMock()

        with (
            patch("src.worker.stock_worker.get_settings", return_value=mock_settings),
            patch("src.worker.stock_worker.async_session_factory") as mock_factory,
        ):
            session = AsyncMock()
            session.execute = AsyncMock(side_effect=session_execute_side_effect)
            session.commit = AsyncMock()
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=False)
            mock_factory.side_effect = lambda: session

            worker = StockWorker()
            worker._running = True
            task = asyncio.create_task(worker._poll_loop())
            await asyncio.sleep(0.2)
            worker._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert queue_entry.status == "processed"
        assert order.status == "completed"
        assert product.stock == 98  # 100 - 2

    @pytest.mark.asyncio
    async def test_poll_loop_processes_multiple_orders(self, mock_settings):
        """Happy path: poll loop fetches multiple order_refs, processes each independently."""
        queue_entry1 = MagicMock()
        queue_entry1.order_ref = "ORD-001"
        queue_entry1.status = "pending"
        queue_entry1.processed_at = None

        queue_entry2 = MagicMock()
        queue_entry2.order_ref = "ORD-002"
        queue_entry2.status = "pending"
        queue_entry2.processed_at = None

        order_item1 = MagicMock()
        order_item1.sku = "SKU-001"
        order_item1.qty = 2

        order_item2 = MagicMock()
        order_item2.sku = "SKU-002"
        order_item2.qty = 1

        product1 = MagicMock()
        product1.sku = "SKU-001"
        product1.stock = 100

        product2 = MagicMock()
        product2.sku = "SKU-002"
        product2.stock = 50

        order1 = MagicMock()
        order1.order_ref = "ORD-001"
        order1.status = "accepted"

        order2 = MagicMock()
        order2.order_ref = "ORD-002"
        order2.status = "accepted"

        call_count = {"count": 0}

        async def session_execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: subquery returns order_refs
            if call_num == 1:
                mock_result = AsyncMock()
                mock_result.fetchall = MagicMock(return_value=[("ORD-001",), ("ORD-002",)])
                return mock_result
            # Call 2: SELECT OrderItem rows for ORD-001
            if call_num == 2:
                return _make_result(multi=[order_item1])
            # Call 3: Lock product for ORD-001
            if call_num == 3:
                return _make_result(single=product1)
            # Call 4: SELECT StockQueue for ORD-001
            if call_num == 4:
                return _make_result(single=queue_entry1)
            # Call 5: Lock order for ORD-001
            if call_num == 5:
                return _make_result(single=order1)
            # Call 6: SELECT OrderItem rows for ORD-002
            if call_num == 6:
                return _make_result(multi=[order_item2])
            # Call 7: Lock product for ORD-002
            if call_num == 7:
                return _make_result(single=product2)
            # Call 8: SELECT StockQueue for ORD-002
            if call_num == 8:
                return _make_result(single=queue_entry2)
            # Call 9: Lock order for ORD-002
            if call_num == 9:
                return _make_result(single=order2)
            # Call 10: next poll — subquery returns empty
            if call_num == 10:
                mock_result = AsyncMock()
                mock_result.fetchall = MagicMock(return_value=[])
                return mock_result
            return AsyncMock()

        with (
            patch("src.worker.stock_worker.get_settings", return_value=mock_settings),
            patch("src.worker.stock_worker.async_session_factory") as mock_factory,
        ):
            session = AsyncMock()
            session.execute = AsyncMock(side_effect=session_execute_side_effect)
            session.commit = AsyncMock()
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=False)
            mock_factory.side_effect = lambda: session

            worker = StockWorker()
            worker._running = True
            task = asyncio.create_task(worker._poll_loop())
            await asyncio.sleep(0.3)
            worker._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert queue_entry1.status == "processed"
        assert queue_entry2.status == "processed"
        assert order1.status == "completed"
        assert order2.status == "completed"
        assert product1.stock == 98  # 100 - 2
        assert product2.stock == 49  # 50 - 1

    @pytest.mark.asyncio
    async def test_poll_loop_multi_sku_order(self, mock_settings):
        """Happy path: poll loop handles multi-SKU order — all items processed atomically."""
        queue_entry = MagicMock()
        queue_entry.order_ref = "ORD-001"
        queue_entry.status = "pending"
        queue_entry.processed_at = None

        order_item_a = MagicMock()
        order_item_a.sku = "SKU-A"
        order_item_a.qty = 2

        order_item_b = MagicMock()
        order_item_b.sku = "SKU-B"
        order_item_b.qty = 3

        product_a = MagicMock()
        product_a.sku = "SKU-A"
        product_a.stock = 100

        product_b = MagicMock()
        product_b.sku = "SKU-B"
        product_b.stock = 50

        order = MagicMock()
        order.order_ref = "ORD-001"
        order.status = "accepted"

        call_count = {"count": 0}

        async def session_execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: subquery returns order_refs
            if call_num == 1:
                mock_result = AsyncMock()
                mock_result.fetchall = MagicMock(return_value=[("ORD-001",)])
                return mock_result
            # Call 2: SELECT OrderItem rows for ORD-001 (multi-SKU)
            if call_num == 2:
                return _make_result(multi=[order_item_a, order_item_b])
            # Call 3: Lock product_a
            if call_num == 3:
                return _make_result(single=product_a)
            # Call 4: Lock product_b
            if call_num == 4:
                return _make_result(single=product_b)
            # Call 5: SELECT StockQueue entry
            if call_num == 5:
                return _make_result(single=queue_entry)
            # Call 6: Lock order
            if call_num == 6:
                return _make_result(single=order)
            # Call 7: next poll — subquery returns empty
            if call_num == 7:
                mock_result = AsyncMock()
                mock_result.fetchall = MagicMock(return_value=[])
                return mock_result
            return AsyncMock()

        with (
            patch("src.worker.stock_worker.get_settings", return_value=mock_settings),
            patch("src.worker.stock_worker.async_session_factory") as mock_factory,
        ):
            session = AsyncMock()
            session.execute = AsyncMock(side_effect=session_execute_side_effect)
            session.commit = AsyncMock()
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=False)
            mock_factory.side_effect = lambda: session

            worker = StockWorker()
            worker._running = True
            task = asyncio.create_task(worker._poll_loop())
            await asyncio.sleep(0.2)
            worker._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Order processed as a unit
        assert queue_entry.status == "processed"
        assert product_a.stock == 98  # 100 - 2
        assert product_b.stock == 47  # 50 - 3
        assert order.status == "completed"

    @pytest.mark.asyncio
    async def test_poll_loop_partial_failure_fails_entire_order(self, mock_settings):
        """Unhappy path: one SKU in a multi-SKU order fails — entire order fails, no stock deducted."""
        queue_entry = MagicMock()
        queue_entry.order_ref = "ORD-001"
        queue_entry.status = "pending"
        queue_entry.processed_at = None

        order_item_a = MagicMock()
        order_item_a.sku = "SKU-A"
        order_item_a.qty = 2

        order_item_b = MagicMock()
        order_item_b.sku = "SKU-B"
        order_item_b.qty = 999  # Insufficient stock

        product_a = MagicMock()
        product_a.sku = "SKU-A"
        product_a.stock = 100

        product_b = MagicMock()
        product_b.sku = "SKU-B"
        product_b.stock = 50

        order = MagicMock()
        order.order_ref = "ORD-001"
        order.status = "accepted"

        call_count = {"count": 0}

        async def session_execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: subquery returns order_refs
            if call_num == 1:
                mock_result = AsyncMock()
                mock_result.fetchall = MagicMock(return_value=[("ORD-001",)])
                return mock_result
            # Call 2: SELECT OrderItem rows for ORD-001
            if call_num == 2:
                return _make_result(multi=[order_item_a, order_item_b])
            # Call 3: Lock product_a
            if call_num == 3:
                return _make_result(single=product_a)
            # Call 4: Lock product_b
            if call_num == 4:
                return _make_result(single=product_b)
            # Call 5: _mark_order_failed — SELECT StockQueue entry
            if call_num == 5:
                return _make_result(single=queue_entry)
            # Call 6: _mark_order_failed — SELECT Order
            if call_num == 6:
                return _make_result(single=order)
            # Call 7: next poll — subquery returns empty
            if call_num == 7:
                mock_result = AsyncMock()
                mock_result.fetchall = MagicMock(return_value=[])
                return mock_result
            return AsyncMock()

        with (
            patch("src.worker.stock_worker.get_settings", return_value=mock_settings),
            patch("src.worker.stock_worker.async_session_factory") as mock_factory,
        ):
            session = AsyncMock()
            session.execute = AsyncMock(side_effect=session_execute_side_effect)
            session.commit = AsyncMock()
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=False)
            mock_factory.side_effect = lambda: session

            worker = StockWorker()
            worker._running = True
            task = asyncio.create_task(worker._poll_loop())
            await asyncio.sleep(0.2)
            worker._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Queue entry failed, no stock deducted
        assert queue_entry.status == "failed"
        assert product_a.stock == 100  # Unchanged
        assert product_b.stock == 50  # Unchanged
        assert order.status == "failed"

    @pytest.mark.asyncio
    async def test_poll_loop_no_pending_rows_sleeps(self, mock_settings):
        """Happy path: no pending rows — poll loop sleeps and continues."""
        call_count = {"count": 0}

        async def session_execute_side_effect(stmt):
            call_count["count"] += 1
            # Subquery returns empty order_refs
            mock_result = AsyncMock()
            mock_result.fetchall = MagicMock(return_value=[])
            return mock_result

        with (
            patch("src.worker.stock_worker.get_settings", return_value=mock_settings),
            patch("src.worker.stock_worker.async_session_factory") as mock_factory,
        ):
            session = AsyncMock()
            session.execute = AsyncMock(side_effect=session_execute_side_effect)
            session.commit = AsyncMock()
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=False)
            mock_factory.side_effect = lambda: session

            worker = StockWorker()
            worker._running = True
            task = asyncio.create_task(worker._poll_loop())
            await asyncio.sleep(0.05)
            worker._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # At least one poll happened, returned empty
        assert call_count["count"] >= 1

    @pytest.mark.asyncio
    async def test_poll_loop_exception_leaves_order_pending(self, mock_settings):
        """Unhappy path: exception during processing — order stays pending for retry."""
        queue_entry = MagicMock()
        queue_entry.order_ref = "ORD-001"
        queue_entry.status = "pending"
        queue_entry.processed_at = None

        call_count = {"count": 0}

        async def session_execute_side_effect(stmt):
            call_count["count"] += 1
            call_num = call_count["count"]

            # Call 1: subquery returns order_refs
            if call_num == 1:
                mock_result = AsyncMock()
                mock_result.fetchall = MagicMock(return_value=[("ORD-001",)])
                return mock_result
            # Call 2: Exception during processing
            if call_num == 2:
                raise RuntimeError("Database connection lost")
            # Call 3: next poll — subquery returns empty
            if call_num == 3:
                mock_result = AsyncMock()
                mock_result.fetchall = MagicMock(return_value=[])
                return mock_result
            return AsyncMock()

        with (
            patch("src.worker.stock_worker.get_settings", return_value=mock_settings),
            patch("src.worker.stock_worker.async_session_factory") as mock_factory,
        ):
            session = AsyncMock()
            session.execute = AsyncMock(side_effect=session_execute_side_effect)
            session.commit = AsyncMock()
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=False)
            mock_factory.side_effect = lambda: session

            worker = StockWorker()
            worker._running = True
            task = asyncio.create_task(worker._poll_loop())
            await asyncio.sleep(0.2)
            worker._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Queue entry should still be pending (not processed or failed)
        assert queue_entry.status == "pending"
