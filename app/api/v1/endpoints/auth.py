from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import select

from app.core.database import SessionDep
from app.core.security import (
    UserDep,
    create_access_token,
    hash_password,
    verify_password,
)
from app.domain.enums import PlatformType
from app.domain.models import GlobalFilterConfig, User
from app.domain.schemas import ApiResponse, TokenResponse, UserCreate, UserResponse

router = APIRouter(prefix="/auth", tags=["Authentication & User Management"])


@router.post(
    "/register",
    response_model=ApiResponse[UserResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register a new trading operator account",
)
async def register_user(
    user_in: UserCreate,
    session: SessionDep,
):
    """
    Registers a new operator user in the multi-tenant system and
    initializes their default global filter configurations.
    """
    existing_stmt = select(User).where(User.email == user_in.email)
    existing_res = await session.exec(existing_stmt)
    if existing_res.first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    user = User(
        email=user_in.email,
        hashed_password=hash_password(user_in.password),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    # Initialize default filter configs for the new operator
    for platform in [PlatformType.TELEGRAM, PlatformType.THREADS]:
        cfg = GlobalFilterConfig(
            user_id=user.id,
            platform=platform,
            allow_any_sender=False,
            strict_whitelist=False,
        )
        session.add(cfg)
    await session.commit()

    return ApiResponse(data=UserResponse.model_validate(user))


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Obtain JWT access token via OAuth2 password grant",
)
async def login_for_access_token(
    session: SessionDep,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """
    Authenticates operator with email and password, returning a JWT Bearer token.
    """
    stmt = select(User).where(User.email == form_data.username)
    result = await session.exec(stmt)
    user = result.first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated.",
        )

    access_token = create_access_token(subject=user.id)
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user=UserResponse.model_validate(user),
    )


@router.get(
    "/me",
    response_model=ApiResponse[UserResponse],
    summary="Get authenticated operator profile",
)
async def read_current_user_profile(
    current_user: UserDep,
):
    """
    Returns current operator details.
    """
    return ApiResponse(data=UserResponse.model_validate(current_user))
