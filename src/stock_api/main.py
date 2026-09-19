"""Stock API service — manages products, stock levels, and reports."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.config import get_settings
from src.stock_api.routes import report_router, stock_router
from src.worker.stock_worker import StockWorker

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage StockWorker lifecycle — start on app init, stop on shutdown."""
    worker = StockWorker()
    app.state.worker = worker
    await worker.start()
    yield
    await worker.stop()


app = FastAPI(title="Stock API", version="1.0.0", lifespan=lifespan)
app.include_router(stock_router)
app.include_router(report_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "stock-api"}
