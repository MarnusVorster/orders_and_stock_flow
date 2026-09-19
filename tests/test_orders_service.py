"""Tests for src/orders_api/service.py — create_order and get_order."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.orders_api.schemas import OrderCreate, OrderItemCreate
from src.orders_api.service import create_order, get_order


def _make_result(single=None, multi=None):
    """Create a properly structured mock execute result.

    - scalar_one_or_none() returns `single`
    - scalars().all() returns `multi` list
    """
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=single)

    sync_scalars = MagicMock()
    sync_scalars.all = MagicMock(return_value=multi)
    result.scalars = MagicMock(return_value=sync_scalars)
    return result


class TestCreateOrder:
    """Tests for create_order service function."""

    @pytest.mark.asyncio
    async def test_create_order_success_single_item(self, mock_session):
        """Happy path: create a valid order with one item."""
        product = MagicMock()
        product.sku = "SKU-001"
        product.price_cents = 999

        order_data = OrderCreate(
            order_ref="ORD-001",
            customer_id="CUST-1",
            items=[OrderItemCreate(sku="SKU-001", qty=2)],
        )

        # First execute call: check existing order -> None
        # Second execute call: query products -> [product]
        mock_session.execute.side_effect = [
            _make_result(single=None),
            _make_result(multi=[product]),
        ]

        # Patch Order.__init__ to set created_at
        from src.models import Order as RealOrder

        original_init = RealOrder.__init__

        def patched_init(self, **kwargs):
            original_init(self, **kwargs)
            if not self.created_at:
                self.created_at = datetime.now(timezone.utc)

        with patch.object(RealOrder, "__init__", patched_init):
            result = await create_order(mock_session, order_data)

        assert result.order_ref == "ORD-001"
        assert result.customer_id == "CUST-1"
        assert result.status == "accepted"
        assert result.total_cents == 1998  # 999 * 2
        assert len(result.items) == 1
        assert result.items[0].sku == "SKU-001"
        assert result.items[0].qty == 2
        assert result.items[0].unit_price_cents == 999
        assert result.duplicate is False
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_order_success_multiple_items(self, mock_session):
        """Happy path: create an order with multiple items, verify total."""
        product_a = MagicMock()
        product_a.sku = "A"
        product_a.price_cents = 100

        product_b = MagicMock()
        product_b.sku = "B"
        product_b.price_cents = 250

        order_data = OrderCreate(
            order_ref="ORD-MULTI",
            customer_id="CUST-2",
            items=[
                OrderItemCreate(sku="A", qty=3),
                OrderItemCreate(sku="B", qty=2),
            ],
        )

        mock_session.execute.side_effect = [
            _make_result(single=None),
            _make_result(multi=[product_a, product_b]),
        ]

        from src.models import Order as RealOrder

        original_init = RealOrder.__init__

        def patched_init(self, **kwargs):
            original_init(self, **kwargs)
            if not self.created_at:
                self.created_at = datetime.now(timezone.utc)

        with patch.object(RealOrder, "__init__", patched_init):
            result = await create_order(mock_session, order_data)

        assert result.total_cents == 800  # (100*3) + (250*2)
        assert len(result.items) == 2

    @pytest.mark.asyncio
    async def test_create_order_duplicate_ref_returns_existing(self, mock_session):
        """Happy path: duplicate order_ref returns existing order with duplicate=True."""
        existing_order = MagicMock()
        existing_order.order_ref = "ORD-DUP"
        existing_order.customer_id = "CUST-1"
        existing_order.status = "accepted"
        existing_order.total_cents = 500
        existing_order.created_at = datetime.now(timezone.utc)

        existing_item = MagicMock()
        existing_item.sku = "SKU-001"
        existing_item.qty = 1
        existing_item.unit_price_cents = 500

        order_data = OrderCreate(
            order_ref="ORD-DUP",
            customer_id="CUST-1",
            items=[OrderItemCreate(sku="SKU-001", qty=1)],
        )

        # Call 1 (line 64): find existing order -> existing_order
        # Call 2 (line 69): select(OrderItem) — result discarded
        # Call 3 (line 70): select(OrderItem) -> scalars().all() -> [existing_item]
        mock_session.execute.side_effect = [
            _make_result(single=existing_order),
            _make_result(multi=[]),  # discarded result
            _make_result(multi=[existing_item]),
        ]

        result = await create_order(mock_session, order_data)

        assert result.order_ref == "ORD-DUP"
        assert result.duplicate is True
        assert len(result.items) == 1
        mock_session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_order_empty_items_raises_value_error(self, mock_session):
        """Unhappy path: order with no items raises ValueError.

        Note: The service calls execute once (line 64) to check for existing order
        before validating items, so we provide one result.
        """
        order_data = OrderCreate(
            order_ref="ORD-EMPTY",
            customer_id="CUST-1",
            items=[],
        )

        # Line 64: check existing order — no existing order found
        mock_session.execute.return_value = _make_result(single=None)

        with pytest.raises(ValueError, match="Order must have at least one item"):
            await create_order(mock_session, order_data)

        mock_session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_order_missing_sku_raises_value_error(self, mock_session):
        """Unhappy path: referencing a non-existent SKU raises ValueError."""
        order_data = OrderCreate(
            order_ref="ORD-MISSING",
            customer_id="CUST-1",
            items=[OrderItemCreate(sku="NONEXISTENT", qty=1)],
        )

        mock_session.execute.side_effect = [
            _make_result(single=None),
            _make_result(multi=[]),  # No products found
        ]

        with pytest.raises(ValueError, match="Products not found"):
            await create_order(mock_session, order_data)

        mock_session.commit.assert_not_called()


class TestGetOrder:
    """Tests for get_order service function."""

    @pytest.mark.asyncio
    async def test_get_order_success(self, mock_session):
        """Happy path: fetch an existing order by ref."""
        order = MagicMock()
        order.order_ref = "ORD-FOUND"
        order.customer_id = "CUST-1"
        order.status = "accepted"
        order.total_cents = 1500
        order.created_at = datetime.now(timezone.utc)

        item1 = MagicMock()
        item1.sku = "A"
        item1.qty = 1
        item1.unit_price_cents = 500

        item2 = MagicMock()
        item2.sku = "B"
        item2.qty = 1
        item2.unit_price_cents = 1000

        order.items = [item1, item2]

        mock_session.execute.return_value = _make_result(single=order)

        result = await get_order(mock_session, "ORD-FOUND")

        assert result.order_ref == "ORD-FOUND"
        assert result.customer_id == "CUST-1"
        assert result.status == "accepted"
        assert result.total_cents == 1500
        assert len(result.items) == 2
        assert result.duplicate is False

    @pytest.mark.asyncio
    async def test_get_order_not_found_raises_value_error(self, mock_session):
        """Unhappy path: order with non-existent ref raises ValueError."""
        mock_session.execute.return_value = _make_result(single=None)

        with pytest.raises(ValueError, match="Order not found: ORD-GHOST"):
            await get_order(mock_session, "ORD-GHOST")
