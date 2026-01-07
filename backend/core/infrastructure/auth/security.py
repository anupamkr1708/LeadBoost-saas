"""
Security utilities for authentication and authorization
"""

from datetime import datetime, timedelta
from typing import Optional
import jwt
from passlib.context import CryptContext
import warnings

warnings.filterwarnings("ignore")  # Suppress bcrypt warnings
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from core.infrastructure.database import get_db
from core.domain.models.user import User
from core.infrastructure.database.crud import get_user
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
SECRET_KEY = os.getenv("SECRET_KEY", "your-super-secret-key-change-in-production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 7))

# Security schemes
security = HTTPBearer()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # Check if this is a pbkdf2 hash
    if hashed_password.startswith("pbkdf2_$"):
        import hashlib

        parts = hashed_password.split("$")
        if len(parts) == 3:
            salt = parts[1]
            stored_hash = parts[2]
            pwdhash = hashlib.pbkdf2_hmac(
                "sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), 100000
            )
            return pwdhash.hex() == stored_hash
        return False
    else:
        # This is a bcrypt hash
        return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    # Bcrypt has a 72-byte limit, so truncate if necessary
    safe_password = password[:72] if len(password) > 72 else password
    try:
        return pwd_context.hash(safe_password)
    except Exception as e:
        # Fallback to a different approach if bcrypt fails
        import hashlib
        import secrets

        salt = secrets.token_hex(16)
        pwdhash = hashlib.pbkdf2_hmac(
            "sha256", safe_password.encode("utf-8"), salt.encode("utf-8"), 100000
        )
        return f"pbkdf2_${salt}${pwdhash.hex()}"


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        token_type: str = payload.get("type")

        if user_id is None or token_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return {"user_id": user_id, "token_type": token_type}
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Get current user from JWT token"""
    token = credentials.credentials
    token_data = verify_token(token)
    user_id = token_data["user_id"]

    user = get_user(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive user",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Get current active user"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def verify_api_key(api_key: str, db: Session) -> Optional[User]:
    """Verify an API key and return the associated user"""
    # Extract the prefix from the API key (first 8 characters after 'lb_')
    if not api_key.startswith("lb_") or len(api_key) < 11:  # 'lb_' + 8 chars
        return None

    key_prefix = api_key[3:11]  # Get the 8-character prefix
    from core.infrastructure.database.crud import get_api_key_by_prefix

    db_api_key = get_api_key_by_prefix(db, key_prefix)

    if db_api_key and db_api_key.is_active and not db_api_key.is_revoked:
        # In a real implementation, you would verify the full key against its hash
        # For now, we'll just return the user associated with the key
        from core.infrastructure.database.crud import get_user

        return get_user(db, db_api_key.user_id)

    return None
