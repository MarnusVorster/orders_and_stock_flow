"""Pydantic schemas for the Stock API."""

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    """Schema for creating a new product."""

    sku: str = Field(..., min_length=1, max_length=20, description="Product SKU identifier")
    name: str = Field(..., min_length=1, max_length=200, description="Product display name")
    price_cents: int = Field(..., ge=0, description="Price per unit in cents")
    stock: int = Field(..., ge=0, description="Initial stock level")


class StockUpdate(BaseModel):
    """Schema for updating product stock level."""

    stock: int = Field(..., ge=0, description="New stock level (must be >= 0)")


class StockResponse(BaseModel):
    """Schema for product stock response."""

    sku: str = Field(description="Product SKU identifier")
    name: str = Field(description="Product display name")
    price_cents: int = Field(description="Price per unit in cents")
    stock: int = Field(description="Current stock level")


class UnitSold(BaseModel):
    """Schema for units sold in a daily report."""

    sku: str = Field(description="Product SKU identifier")
    qty: int = Field(description="Total quantity sold")


class CurrentStock(BaseModel):
    """Schema for current stock level in a daily report."""

    sku: str = Field(description="Product SKU identifier")
    stock: int = Field(description="Current stock level")


class DailyReportResponse(BaseModel):
    """Schema for the daily aggregated report."""

    date: str = Field(description="Report date (YYYY-MM-DD)")
    total_orders: int = Field(description="Total number of orders on this date")
    revenue_cents: int = Field(description="Total revenue from completed orders in cents")
    units_sold: list[UnitSold] = Field(description="Quantity sold per SKU")
    current_stock: list[CurrentStock] = Field(description="Current stock level per SKU")
