"""Tests for src/stock_api/service.py — create_product, update_product_stock, get_product_stock, get_daily_report."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.stock_api.service import (
    create_product,
    get_daily_report,
    get_product_stock,
    update_product_stock,
)


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


class TestCreateProduct:
    """Tests for create_product service function."""

    @pytest.mark.asyncio
    async def test_create_product_success(self, mock_session):
        """Happy path: create a new product."""
        mock_session.execute.return_value = _make_result(single=None)

        result = await create_product(mock_session, sku="NEW-001", name="New Widget", price_cents=500, stock=10)

        assert result.sku == "NEW-001"
        assert result.name == "New Widget"
        assert result.price_cents == 500
        assert result.stock == 10
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_product_duplicate_sku_raises_value_error(self, mock_session):
        """Unhappy path: creating a product with existing SKU raises ValueError."""
        existing = MagicMock()
        existing.sku = "EXISTING"

        mock_session.execute.return_value = _make_result(single=existing)

        with pytest.raises(ValueError, match="Product already exists: EXISTING"):
            await create_product(mock_session, sku="EXISTING", name="Duplicate", price_cents=100, stock=0)

        mock_session.commit.assert_not_called()


class TestUpdateProductStock:
    """Tests for update_product_stock service function."""

    @pytest.mark.asyncio
    async def test_update_product_stock_success(self, mock_session):
        """Happy path: update stock level for existing product."""
        product = MagicMock()
        product.sku = "SKU-001"
        product.stock = 100
        product.updated_at = datetime.now(timezone.utc)

        mock_session.execute.return_value = _make_result(single=product)

        result = await update_product_stock(mock_session, sku="SKU-001", new_stock=50)

        assert result.stock == 50
        assert result.sku == "SKU-001"
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_product_stock_not_found_raises_value_error(self, mock_session):
        """Unhappy path: updating stock for non-existent SKU raises ValueError."""
        mock_session.execute.return_value = _make_result(single=None)

        with pytest.raises(ValueError, match="Product not found: GHOST-SKU"):
            await update_product_stock(mock_session, sku="GHOST-SKU", new_stock=0)

        mock_session.commit.assert_not_called()


class TestGetProductStock:
    """Tests for get_product_stock service function."""

    @pytest.mark.asyncio
    async def test_get_product_stock_found(self, mock_session):
        """Happy path: fetch existing product by SKU."""
        product = MagicMock()
        product.sku = "FOUND"
        product.stock = 42

        mock_session.execute.return_value = _make_result(single=product)

        result = await get_product_stock(mock_session, "FOUND")

        assert result is not None
        assert result.sku == "FOUND"
        assert result.stock == 42

    @pytest.mark.asyncio
    async def test_get_product_stock_not_found_returns_none(self, mock_session):
        """Happy path: non-existent SKU returns None (not an error)."""
        mock_session.execute.return_value = _make_result(single=None)

        result = await get_product_stock(mock_session, "NOPE")

        assert result is None


class TestGetDailyReport:
    """Tests for get_daily_report service function."""

    @pytest.mark.asyncio
    async def test_get_daily_report_with_completed_orders(self, mock_session):
        """Happy path: report aggregates completed orders correctly."""
        completed_order = MagicMock()
        completed_order.status = "completed"
        completed_order.created_at = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)

        accepted_order = MagicMock()
        accepted_order.status = "accepted"
        accepted_order.created_at = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

        oi1 = MagicMock()
        oi1.unit_price_cents = 500
        oi1.qty = 2
        oi1.sku = "A"

        oi2 = MagicMock()
        oi2.unit_price_cents = 300
        oi2.qty = 1
        oi2.sku = "B"

        prod_a = MagicMock()
        prod_a.sku = "A"
        prod_a.stock = 90

        prod_b = MagicMock()
        prod_b.sku = "B"
        prod_b.stock = 50

        # Three sequential execute calls:
        # 1. SELECT Order WHERE date -> [completed_order, accepted_order]
        # 2. SELECT OrderItem JOIN Order WHERE completed + date -> [oi1, oi2]
        # 3. SELECT Product -> [prod_a, prod_b]
        mock_session.execute.side_effect = [
            _make_result(multi=[completed_order, accepted_order]),
            _make_result(multi=[oi1, oi2]),
            _make_result(multi=[prod_a, prod_b]),
        ]

        result = await get_daily_report(mock_session, "2024-01-15")

        assert result["date"] == "2024-01-15"
        assert result["total_orders"] == 2
        assert result["revenue_cents"] == 1300  # (500*2) + (300*1)
        assert len(result["units_sold"]) == 2
        assert result["units_sold"][0].sku == "A"
        assert result["units_sold"][0].qty == 2
        assert result["units_sold"][1].sku == "B"
        assert result["units_sold"][1].qty == 1
        assert len(result["current_stock"]) == 2
        assert result["current_stock"][0].sku == "A"
        assert result["current_stock"][0].stock == 90

    @pytest.mark.asyncio
    async def test_get_daily_report_empty_date(self, mock_session):
        """Happy path: date with no orders returns zeroed report."""
        mock_session.execute.return_value = _make_result(multi=[])

        result = await get_daily_report(mock_session, "2099-01-01")

        assert result["date"] == "2099-01-01"
        assert result["total_orders"] == 0
        assert result["revenue_cents"] == 0
        assert result["units_sold"] == []
