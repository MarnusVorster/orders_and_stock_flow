"""SQLAlchemy ORM models for the orders and stock domain."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import VARCHAR
from sqlalchemy.orm import relationship

from src.database import Base


class Product(Base):
    """Product catalog table.

    Stores product SKU, name, price, and current stock level.
    """

    __tablename__ = "products"

    sku = Column(VARCHAR(20), primary_key=True, nullable=False)
    name = Column(VARCHAR(200), nullable=False)
    price_cents = Column(Integer, nullable=False)
    stock = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    orders = relationship("OrderItem", back_populates="product", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Product(sku={self.sku}, name={self.name}, stock={self.stock})>"


class Order(Base):
    """Orders table.

    Stores order header data including reference, customer, total, and status.
    """

    __tablename__ = "orders"

    order_ref = Column(VARCHAR(50), primary_key=True, nullable=False)
    customer_id = Column(VARCHAR(100), nullable=False)
    total_cents = Column(Integer, nullable=False)
    status = Column(VARCHAR(20), nullable=False, default="accepted")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    queue_entries = relationship("StockQueue", back_populates="order", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Order(order_ref={self.order_ref}, status={self.status})>"


class OrderItem(Base):
    """Order line items table.

    Each row represents a single product line within an order,
    capturing the SKU, quantity, and price at time of order.
    """

    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_ref = Column(VARCHAR(50), ForeignKey("orders.order_ref"), nullable=False)
    sku = Column(VARCHAR(20), ForeignKey("products.sku"), nullable=False)
    qty = Column(Integer, nullable=False)
    unit_price_cents = Column(Integer, nullable=False)

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="orders")

    def __repr__(self) -> str:
        return f"<OrderItem(order_ref={self.order_ref}, sku={self.sku}, qty={self.qty})>"


class StockQueue(Base):
    """Stock processing queue table.

    Pending stock deductions are queued here by the background worker.
    Uses SELECT FOR UPDATE SKIP LOCKED for safe concurrent processing.
    """

    __tablename__ = "stock_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_ref = Column(VARCHAR(50), ForeignKey("orders.order_ref"), nullable=False)
    status = Column(VARCHAR(20), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)

    order = relationship("Order", back_populates="queue_entries")

    __table_args__ = (
        UniqueConstraint("order_ref", name="uq_stock_queue_order_ref"),
        Index("ix_stock_queue_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<StockQueue(id={self.id}, order_ref={self.order_ref}, status={self.status})>"
