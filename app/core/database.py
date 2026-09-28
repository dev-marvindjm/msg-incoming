import logging
from typing import Annotated, AsyncGenerator
from fastapi import Depends
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.domain.enums import PlatformType
from app.domain.models import GlobalFilterConfig, User

logger = logging.getLogger("msg_ingestor.database")

# Create asynchronous database engine
async_engine = create_async_engine(
    settings.async_database_url,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    future=True,
)

# Async session factory
async_session_maker = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency yielding an async SQLModel database session
    with automatic commit on completion and rollback on exception.
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def init_db() -> None:
    """
    Initializes database tables and bootstraps initial seed data.
    """
    from app.core.security import hash_password

    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        logger.info("Database schemas and tables verified/created successfully.")

        # Ensure default operator exists
        async with async_session_maker() as session:
            stmt = select(User).where(User.email == settings.DEFAULT_ADMIN_EMAIL)
            result = await session.exec(stmt)
            admin_user = result.first()

            if not admin_user:
                logger.info(f"Bootstrapping default admin user: {settings.DEFAULT_ADMIN_EMAIL}")
                admin_user = User(
                    email=settings.DEFAULT_ADMIN_EMAIL,
                    hashed_password=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
                    is_active=True,
                )
                session.add(admin_user)
                await session.commit()
                await session.refresh(admin_user)

                # Initialize default GlobalFilterConfig for admin
                for platform in [PlatformType.TELEGRAM, PlatformType.THREADS]:
                    cfg = GlobalFilterConfig(
                        user_id=admin_user.id,
                        platform=platform,
                        allow_any_sender=False,
                        strict_whitelist=False,
                    )
                    session.add(cfg)
                await session.commit()
                logger.info(f"Initialized default filter configs for user {admin_user.id}")

    except Exception as exc:
        logger.error(f"Database bootstrap notice: {exc}", exc_info=True)
