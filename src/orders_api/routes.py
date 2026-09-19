"""Order API route handlers."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_session
from src.orders_api.schemas import OrderCreate, OrderResponse
from src.orders_api.service import create_order, get_order

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderResponse, status_code=200)
async def post_order(
    order: OrderCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrderResponse:
    """Create or accept an order.

    Idempotent: if order_ref already exists, returns the existing order
    with duplicate=True instead of raising an error.

    Raises:
        HTTPException 422: If order validation fails (e.g., missing products).
    """
    try:
        return await create_order(session, order)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/{order_ref}", response_model=OrderResponse, status_code=200)
async def get_order_route(
    order_ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrderResponse:
    """Fetch order details by reference.

    Raises:
        HTTPException 404: If no order with the given ref exists.
    """
    try:
        return await get_order(session, order_ref)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
