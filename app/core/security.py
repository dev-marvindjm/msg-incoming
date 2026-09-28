from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID
import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import select

from app.core.config import settings
from app.core.database import SessionDep
from app.core.result import Err, Ok, Result
from app.domain.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


def hash_password(password: str) -> str:
    """Hashes a plaintext password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_access_token(subject: str | UUID, expires_delta: Optional[timedelta] = None) -> str:
    """Creates a signed JWT access token for authentication."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    payload = {
        "sub": str(subject),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> Result[UUID, str]:
    """Decodes and validates a JWT token, returning the user UUID."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        sub: Optional[str] = payload.get("sub")
        if not sub:
            return Err("Token payload missing subject ('sub').")
        return Ok(UUID(sub))
    except jwt.ExpiredSignatureError:
        return Err("Authentication token has expired.")
    except (jwt.InvalidTokenError, ValueError) as exc:
        return Err(f"Invalid authentication token: {exc}")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: SessionDep,
) -> User:
    """
    FastAPI dependency that extracts and validates JWT from Authorization header,
    retrieves the user from the database, and asserts account activity.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate authentication credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    decode_res = decode_access_token(token)
    if decode_res.is_err():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=decode_res.unwrap_err(),
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = decode_res.unwrap()
    result = await session.exec(select(User).where(User.id == user_id))
    user = result.first()

    if not user:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account.",
        )

    return user


UserDep = Annotated[User, Depends(get_current_user)]
