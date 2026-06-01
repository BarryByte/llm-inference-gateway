"""Async Postgres connection pool. Single entry point for all DB access."""
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from gateway.config import settings

engine: AsyncEngine = create_async_engine(settings.database_url, pool_size=10)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
