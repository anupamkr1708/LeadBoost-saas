"""
Pydantic schemas for User model
"""

from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime


class UserBase(BaseModel):
    email: EmailStr
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    is_active: Optional[bool] = None
    # P1.2 (Sender Profile): minimal extension, see
    # core/domain/models/user.py for why sender identity lives on User
    # rather than a new table.
    job_title: Optional[str] = None
    signature: Optional[str] = None


class UserInDBBase(UserBase):
    id: int
    is_active: bool
    is_verified: bool
    organization_id: Optional[int] = None
    job_title: Optional[str] = None
    signature: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class User(UserInDBBase):
    pass


class UserInDB(UserInDBBase):
    hashed_password: str
