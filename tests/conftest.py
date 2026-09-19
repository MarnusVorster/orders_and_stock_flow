"""Shared pytest fixtures for all test modules."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def mock_session():
    """Provide a mocked AsyncSession with common methods pre-configured."""
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _make_product(sku="SKU-001", name="Widget", price_cents=999, stock=100):
    """Create a mock Product ORM instance."""
    product = MagicMock()
    product.sku = sku
    product.name = name
    product.price_cents = price_cents
    product.stock = stock
    product.created_at = datetime.now(timezone.utc)
    product.updated_at = datetime.now(timezone.utc)
    return product


def _make_order(order_ref="ORD-001", customer_id="CUST-1", status="accepted", total_cents=999):
    """Create a mock Order ORM instance."""
    order = MagicMock()
    order.order_ref = order_ref
    order.customer_id = customer_id
    order.status = status
    order.total_cents = total_cents
    order.created_at = datetime.now(timezone.utc)
    order.updated_at = datetime.now(timezone.utc)
    return order


def _make_order_item(sku="SKU-001", qty=2, unit_price_cents=999):
    """Create a mock OrderItem ORM instance."""
    item = MagicMock()
    item.sku = sku
    item.qty = qty
    item.unit_price_cents = unit_price_cents
    return item


def _make_stock_queue(id=1, order_ref="ORD-001", sku="SKU-001", qty=2, status="pending"):
    """Create a mock StockQueue ORM instance."""
    queue = MagicMock()
    queue.id = id
    queue.order_ref = order_ref
    queue.sku = sku
    queue.qty = qty
    queue.status = status
    queue.created_at = datetime.now(timezone.utc)
    queue.processed_at = None
    return queue


@pytest.fixture
def product():
    return _make_product()


@pytest.fixture
def order():
    return _make_order()


@pytest.fixture
def order_item():
    return _make_order_item()


@pytest.fixture
def stock_queue():
    return _make_stock_queue()
