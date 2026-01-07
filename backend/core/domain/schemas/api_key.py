"""
Pydantic schemas for API Key model
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class APIKeyBase(BaseModel):
    name: str
    organization_id: Optional[int] = None
    user_id: Optional[int] = None
    rate_limit: int = 100


class APIKeyCreate(APIKeyBase):
    pass


class APIKeyUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    rate_limit: Optional[int] = None


class APIKeyInDBBase(APIKeyBase):
    id: int
    key_prefix: str
    is_active: bool
    is_revoked: bool
    created_at: datetime
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class APIKey(APIKeyInDBBase):
    pass


class APIKeyInDB(APIKeyInDBBase):
    key_hash: str
