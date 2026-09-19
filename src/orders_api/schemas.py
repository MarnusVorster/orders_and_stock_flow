"""Pydantic schemas for the Orders API."""

from datetime import datetime

from pydantic import BaseModel, Field


class OrderItemCreate(BaseModel):
    """Schema for a single line item in an order creation request."""

    sku: str = Field(..., min_length=1, max_length=20, description="Product SKU identifier")
    qty: int = Field(..., ge=1, description="Quantity ordered (must be >= 1)")


class OrderCreate(BaseModel):
    """Schema for an order creation request."""

    order_ref: str = Field(..., min_length=1, max_length=50, description="Unique order reference")
    customer_id: str = Field(..., min_length=1, max_length=100, description="Customer identifier")
    items: list[OrderItemCreate] = Field(..., description="List of line items")


class OrderItemResponse(BaseModel):
    """Schema for a single line item in an order response."""

    sku: str = Field(description="Product SKU identifier")
    qty: int = Field(description="Quantity ordered")
    unit_price_cents: int = Field(description="Price per unit in cents at time of order")


class OrderResponse(BaseModel):
    """Schema for an order response."""

    order_ref: str = Field(description="Unique order reference")
    customer_id: str = Field(description="Customer identifier")
    status: str = Field(description="Order status (accepted/completed/failed)")
    total_cents: int = Field(description="Total order amount in cents")
    items: list[OrderItemResponse] = Field(description="Order line items")
    created_at: datetime = Field(description="Order creation timestamp")
    duplicate: bool = Field(default=False, description="True if this order_ref was already present")
