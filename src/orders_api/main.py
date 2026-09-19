"""Orders API service — handles order creation and retrieval."""

from fastapi import FastAPI

from src.config import get_settings
from src.orders_api.routes import router as orders_router

settings = get_settings()

app = FastAPI(title="Orders API", version="1.0.0")
app.include_router(orders_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "orders-api"}
