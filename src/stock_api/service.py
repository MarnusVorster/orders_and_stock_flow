from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Order, OrderItem, Product
from src.stock_api.schemas import CurrentStock, UnitSold


async def create_product(session: AsyncSession, sku: str, name: str, price_cents: int, stock: int = 0) -> Product:
    """Create a new product.

    Args:
        session: Async database session.
        sku: Unique product SKU identifier.
        name: Product display name.
        price_cents: Price per unit in cents.
        stock: Initial stock level (default 0).

    Returns:
        The created Product ORM instance.

    Raises:
        ValueError: If a product with the same SKU already exists.
    """
    existing = await session.execute(select(Product).where(Product.sku == sku))
    if existing.scalar_one_or_none():
        raise ValueError(f"Product already exists: {sku}")

    product = Product(sku=sku, name=name, price_cents=price_cents, stock=stock)
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def update_product_stock(session: AsyncSession, sku: str, new_stock: int) -> Product:
    """Update the stock level for a product.

    Args:
        session: Async database session.
        sku: Product SKU identifier.
        new_stock: New stock level (must be >= 0).

    Returns:
        The updated Product ORM instance.

    Raises:
        ValueError: If no product with the given SKU exists.
    """
    result = await session.execute(select(Product).where(Product.sku == sku))
    product = result.scalar_one_or_none()

    if product is None:
        raise ValueError(f"Product not found: {sku}")

    product.stock = new_stock
    product.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(product)
    return product


async def get_product_stock(session: AsyncSession, sku: str) -> Product | None:
    """Fetch a product by SKU.

    Args:
        session: Async database session.
        sku: Product SKU identifier.

    Returns:
        Product ORM instance, or None if not found.
    """
    result = await session.execute(select(Product).where(Product.sku == sku))
    return result.scalar_one_or_none()


async def get_daily_report(session: AsyncSession, date_str: str) -> dict:
    """Aggregate daily report for a given date (YYYY-MM-DD).

    Aggregates from orders where created_at falls on the given date.
    Revenue and units sold come from completed orders only.
    Current stock is the live value from the products table.

    Args:
        session: Async database session.
        date_str: Date string in YYYY-MM-DD format.

    Returns:
        Dict with keys: date, total_orders, revenue_cents,
        units_sold (list[UnitSold]), current_stock (list[CurrentStock]).
    """
    # Total orders on this date (all statuses)
    orders_result = await session.execute(select(Order).where(func.to_char(Order.created_at, "YYYY-MM-DD") == date_str))
    orders = orders_result.scalars().all()
    total_orders = len(orders)

    # Revenue and units sold from completed orders
    completed_orders_result = await session.execute(
        select(OrderItem)
        .join(Order)
        .where(
            Order.status == "completed",
            func.to_char(Order.created_at, "YYYY-MM-DD") == date_str,
        )
    )
    order_items = completed_orders_result.scalars().all()

    revenue_cents = 0
    sku_units: dict[str, int] = {}
    for oi in order_items:
        revenue_cents += oi.unit_price_cents * oi.qty
        sku_units[oi.sku] = sku_units.get(oi.sku, 0) + oi.qty

    units_sold = [UnitSold(sku=sku, qty=qty) for sku, qty in sorted(sku_units.items())]

    # Current stock for all products
    products_result = await session.execute(select(Product))
    products = products_result.scalars().all()
    current_stock = [CurrentStock(sku=p.sku, stock=p.stock) for p in products]

    return {
        "date": date_str,
        "total_orders": total_orders,
        "revenue_cents": revenue_cents,
        "units_sold": units_sold,
        "current_stock": current_stock,
    }
