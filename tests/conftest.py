import asyncio
from typing import AsyncGenerator
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token, hash_password
from app.domain.enums import PlatformType
from app.domain.models import GlobalFilterConfig, User
from app.main import create_app

# Isolated async in-memory SQLite database for testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    future=True,
)

test_session_maker = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provides a clean database session for each test with freshly created tables."""
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with test_session_maker() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Test client overriding the get_session dependency with the test session."""
    app = create_app()

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture(scope="function")
async def user_a(db_session: AsyncSession) -> User:
    """Fixture providing User A with default filter configs."""
    user = User(
        email="operator_a@quant.com",
        hashed_password=hash_password("PasswordA123!"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    for p in [PlatformType.TELEGRAM, PlatformType.THREADS]:
        cfg = GlobalFilterConfig(
            user_id=user.id,
            platform=p,
            allow_any_sender=False,
            strict_whitelist=False,
        )
        db_session.add(cfg)
    await db_session.commit()
    return user


@pytest_asyncio.fixture(scope="function")
async def user_b(db_session: AsyncSession) -> User:
    """Fixture providing User B with default filter configs."""
    user = User(
        email="operator_b@quant.com",
        hashed_password=hash_password("PasswordB123!"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    for p in [PlatformType.TELEGRAM, PlatformType.THREADS]:
        cfg = GlobalFilterConfig(
            user_id=user.id,
            platform=p,
            allow_any_sender=False,
            strict_whitelist=False,
        )
        db_session.add(cfg)
    await db_session.commit()
    return user


@pytest.fixture
def auth_headers_user_a(user_a: User) -> dict:
    token = create_access_token(subject=user_a.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_headers_user_b(user_b: User) -> dict:
    token = create_access_token(subject=user_b.id)
    return {"Authorization": f"Bearer {token}"}
