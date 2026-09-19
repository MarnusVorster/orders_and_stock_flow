"""Business logic for order creation and retrieval."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models import Order, OrderItem, Product, StockQueue
from src.orders_api.schemas import OrderCreate, OrderItemCreate, OrderItemResponse, OrderResponse


async def _calculate_total_and_validate_items(
    session: AsyncSession, items: list[OrderItemCreate]
) -> tuple[int, list[dict[str, object]]]:
    """Look up product prices and validate all SKUs exist with sufficient stock.

    Returns (total_cents, list_of_dict_with_sku_qty_unit_price_cents).
    Raises ValueError on any problem.
    """
    if not items:
        raise ValueError("Order must have at least one item")

    skus = [item.sku for item in items]
    result = await session.execute(select(Product).where(Product.sku.in_(skus)))
    products = {p.sku: p for p in result.scalars().all()}

    if len(products) != len(skus):
        missing = set(skus) - set(products.keys())
        raise ValueError(f"Products not found: {missing}")

    total_cents = 0
    item_details = []
    for item in items:
        product = products[item.sku]
        line_total = product.price_cents * item.qty
        total_cents += line_total
        item_details.append(
            {
                "sku": item.sku,
                "qty": item.qty,
                "unit_price_cents": product.price_cents,
            }
        )

    return total_cents, item_details


async def create_order(session: AsyncSession, order_data: OrderCreate) -> OrderResponse:
    """Create an order idempotently.

    If order_ref already exists, return it with duplicate=True.
    Otherwise, creates the order, order items, and stock_queue entries.

    Args:
        session: Async database session.
        order_data: Validated order creation payload.

    Returns:
        OrderResponse with the created (or existing) order details.

    Raises:
        ValueError: If order has no items or any SKU is not found.
    """
    # Check for existing order — idempotency guard
    existing = await session.execute(select(Order).where(Order.order_ref == order_data.order_ref))
    existing_order = existing.scalar_one_or_none()

    if existing_order is not None:
        # Load items for response
        await session.execute(select(OrderItem).where(OrderItem.order_ref == order_data.order_ref))
        items_result = await session.execute(select(OrderItem).where(OrderItem.order_ref == order_data.order_ref))
        items = items_result.scalars().all()
        return OrderResponse(
            order_ref=existing_order.order_ref,
            customer_id=existing_order.customer_id,
            status=existing_order.status,
            total_cents=existing_order.total_cents,
            items=[OrderItemResponse(sku=i.sku, qty=i.qty, unit_price_cents=i.unit_price_cents) for i in items],
            created_at=existing_order.created_at,
            duplicate=True,
        )

    # Calculate total and validate
    total_cents, item_details = await _calculate_total_and_validate_items(session, order_data.items)

    # Create order
    order = Order(
        order_ref=order_data.order_ref,
        customer_id=order_data.customer_id,
        total_cents=total_cents,
        status="accepted",
    )
    session.add(order)
    await session.flush()

    # Create order items
    order_items = []
    for detail in item_details:
        oi = OrderItem(
            order_ref=order_data.order_ref,
            sku=detail["sku"],
            qty=detail["qty"],
            unit_price_cents=detail["unit_price_cents"],
        )
        session.add(oi)
        order_items.append(oi)

    # Create stock_queue entry
    sq = StockQueue(
        order_ref=order_data.order_ref,
        status="pending",
    )
    session.add(sq)

    await session.commit()

    # Build response
    return OrderResponse(
        order_ref=order.order_ref,
        customer_id=order.customer_id,
        status=order.status,
        total_cents=order.total_cents,
        items=[OrderItemResponse(sku=oi.sku, qty=oi.qty, unit_price_cents=oi.unit_price_cents) for oi in order_items],
        created_at=order.created_at,
        duplicate=False,
    )


async def get_order(session: AsyncSession, order_ref: str) -> OrderResponse:
    """Fetch an order by its reference, including items.

    Args:
        session: Async database session.
        order_ref: Unique order reference.

    Returns:
        OrderResponse with items loaded.

    Raises:
        ValueError: If no order with the given ref exists.
    """
    result = await session.execute(select(Order).where(Order.order_ref == order_ref).options(selectinload(Order.items)))
    order = result.scalar_one_or_none()

    if order is None:
        raise ValueError(f"Order not found: {order_ref}")

    return OrderResponse(
        order_ref=order.order_ref,
        customer_id=order.customer_id,
        status=order.status,
        total_cents=order.total_cents,
        items=[OrderItemResponse(sku=oi.sku, qty=oi.qty, unit_price_cents=oi.unit_price_cents) for oi in order.items],
        created_at=order.created_at,
        duplicate=False,
    )
