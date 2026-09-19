"""Stock API route handlers."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_session
from src.stock_api.schemas import (
    DailyReportResponse,
    ProductCreate,
    StockResponse,
    StockUpdate,
)
from src.stock_api.service import (
    create_product,
    get_daily_report,
    get_product_stock,
    update_product_stock,
)

stock_router = APIRouter(prefix="/stock", tags=["stock"])
report_router = APIRouter(prefix="/report", tags=["reports"])


@stock_router.post("", response_model=StockResponse, status_code=201)
async def create_product_route(
    product_data: ProductCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StockResponse:
    """Create a new product.

    Raises:
        HTTPException 409: If a product with the same SKU already exists.
    """
    try:
        product = await create_product(
            session,
            sku=product_data.sku,
            name=product_data.name,
            price_cents=product_data.price_cents,
            stock=product_data.stock,
        )
        return StockResponse(
            sku=product.sku,
            name=product.name,
            price_cents=product.price_cents,
            stock=product.stock,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@stock_router.get("/{sku}", response_model=StockResponse, status_code=200)
async def get_stock(
    sku: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StockResponse:
    """Get current stock level for a product SKU.

    Raises:
        HTTPException 404: If no product with the given SKU exists.
    """
    product = await get_product_stock(session, sku)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product not found: {sku}")
    return StockResponse(
        sku=product.sku,
        name=product.name,
        price_cents=product.price_cents,
        stock=product.stock,
    )


@stock_router.put("/{sku}", response_model=StockResponse, status_code=200)
async def update_stock(
    sku: str,
    update_data: StockUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StockResponse:
    """Update the stock level for a product SKU.

    Raises:
        HTTPException 404: If no product with the given SKU exists.
    """
    try:
        product = await update_product_stock(session, sku, update_data.stock)
        return StockResponse(
            sku=product.sku,
            name=product.name,
            price_cents=product.price_cents,
            stock=product.stock,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@report_router.get("/daily", response_model=DailyReportResponse, status_code=200)
async def daily_report(
    session: Annotated[AsyncSession, Depends(get_session)],
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
) -> DailyReportResponse:
    """Get the daily aggregated report for a given date.

    Revenue and units sold are aggregated from completed orders only.
    Current stock reflects the live value from the products table.
    """
    report = await get_daily_report(session, date)
    return DailyReportResponse(**report)
