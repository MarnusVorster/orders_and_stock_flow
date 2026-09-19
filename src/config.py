"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from sqlalchemy import URL


class Settings(BaseSettings):
    """Application settings loaded from .env file and environment variables.

    Required env vars:
        POSTGRES_DB_HOST, POSTGRES_DB_PORT, POSTGRES_DB_NAME,
        POSTGRES_DB_USER, POSTGRES_DB_PASS
    """

    # Database (PostgreSQL)
    POSTGRES_DB_HOST: str
    POSTGRES_DB_PORT: int
    POSTGRES_DB_NAME: str
    POSTGRES_DB_USER: str
    POSTGRES_DB_PASS: str

    orders_api_port: int = 8001
    stock_api_port: int = 8002
    worker_poll_interval_seconds: float = 1.0

    @property
    def database_url_sqlalchemy(self) -> URL:
        """Build PostgreSQL async database URL as a SQLAlchemy URL object."""
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.POSTGRES_DB_USER,
            password=self.POSTGRES_DB_PASS,
            host=self.POSTGRES_DB_HOST,
            port=self.POSTGRES_DB_PORT,
            database=self.POSTGRES_DB_NAME,
        )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def get_settings() -> Settings:
    """Return a singleton Settings instance populated from environment."""
    return Settings()
