"""
Authentication endpoints
"""

from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
import os
from typing import Any

from core.infrastructure.database import get_db
from core.infrastructure.auth.security import (
    create_access_token,
    create_refresh_token,
    verify_password,
    get_password_hash,
    get_current_user,
)
from core.domain.schemas.user import UserCreate, UserUpdate, User as UserSchema
from core.domain.schemas.organization import OrganizationCreate
from core.domain.models.user import User
from core.infrastructure.database.crud import (
    get_user_by_email,
    create_user as create_user_db,
    get_user,
    create_organization,
    create_user,
)
from core.observability.prometheus_metrics import auth_attempts_total

router = APIRouter()


@router.post("/register", response_model=UserSchema)
async def register_user(user: UserCreate, db: Session = Depends(get_db)) -> Any:
    """Register a new user with atomic transaction"""
    # Check if user already exists (outside transaction for performance)
    db_user = get_user_by_email(db, email=user.email)
    if db_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    try:
        # All operations in single transaction
        # Create organization for the new user (for multi-tenancy)
        org_data = OrganizationCreate(
            name=f"{user.first_name or 'User'}'s Organization",
            description=f"Organization for {user.email}",
        )
        db_org = create_organization(db, org_data)

        # Create user with the new organization ID
        db_user = User(
            email=user.email,
            hashed_password=get_password_hash(user.password),
            first_name=user.first_name,
            last_name=user.last_name,
            organization_id=db_org.id,
        )
        db.add(db_user)
        db.flush()  # Get IDs without committing

        # Assign default subscription plan
        from core.infrastructure.billing.subscription_service import SubscriptionService
        subscription_service = SubscriptionService(db)
        default_plan = os.getenv("DEFAULT_PLAN", "free")
        subscription_service.assign_plan_to_organization(db_org.id, default_plan)

        # Commit all changes atomically
        db.commit()
        db.refresh(db_user)

        # Log successful registration
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            "User registered successfully",
            extra={
                "user_id": db_user.id,
                "email": db_user.email,
                "organization_id": db_org.id,
                "plan": default_plan,
            }
        )

        return db_user

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        db.rollback()
        raise
    except Exception as e:
        # Rollback and log unexpected errors
        db.rollback()
        import logging
        logger = logging.getLogger(__name__)
        logger.error(
            "Registration failed",
            exc_info=True,
            extra={
                "email": user.email,
                "error_type": type(e).__name__,
            }
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed. Please try again.",
        )


@router.post("/login")
async def login_user(
    form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
) -> Any:
    """Login user and return access token"""
    import logging
    logger = logging.getLogger(__name__)
    
    user = get_user_by_email(db, email=form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        auth_attempts_total.labels(result="failure").inc()
        logger.warning(
            "Failed login attempt",
            extra={
                "email": form_data.username,
                "reason": "invalid_credentials",
            }
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        auth_attempts_total.labels(result="failure").inc()
        logger.warning(
            "Failed login attempt",
            extra={
                "email": form_data.username,
                "user_id": user.id,
                "reason": "inactive_user",
            }
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive user",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create access token
    access_token_expires = timedelta(minutes=30)
    access_token = create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )

    # Create refresh token
    refresh_token = create_refresh_token(data={"sub": str(user.id)})

    auth_attempts_total.labels(result="success").inc()
    logger.info(
        "User logged in successfully",
        extra={
            "user_id": user.id,
            "email": user.email,
            "organization_id": user.organization_id,
        }
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": user.id,
        "email": user.email,
    }


@router.post("/refresh")
async def refresh_token(refresh_token: str, db: Session = Depends(get_db)) -> Any:
    """Refresh access token using refresh token"""
    from core.infrastructure.auth.security import verify_refresh_token

    try:
        token_data = verify_refresh_token(refresh_token)
        if token_data.get("token_type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        user_id = token_data["user_id"]
        user = get_user(db, user_id)

        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive",
            )

        # Create new access token
        access_token_expires = timedelta(minutes=30)
        access_token = create_access_token(
            data={"sub": str(user.id)}, expires_delta=access_token_expires
        )

        return {"access_token": access_token, "token_type": "bearer"}
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )


@router.get("/me", response_model=UserSchema)
async def read_users_me(current_user: User = Depends(get_current_user)) -> Any:
    """Get current user info"""
    return current_user


@router.put("/me", response_model=UserSchema)
async def update_users_me(
    user_update: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Update current user info"""
    from core.infrastructure.database.crud import update_user

    updated_user = update_user(db, current_user.id, user_update)
    if not updated_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return updated_user
