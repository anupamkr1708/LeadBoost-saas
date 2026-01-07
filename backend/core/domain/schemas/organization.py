"""
Pydantic schemas for Organization model
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class OrganizationBase(BaseModel):
    name: str
    description: Optional[str] = None


class OrganizationCreate(OrganizationBase):
    pass


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    plan_tier: Optional[str] = None
    max_users: Optional[int] = None
    max_leads: Optional[int] = None


class OrganizationInDBBase(OrganizationBase):
    id: int
    plan_tier: str
    max_users: int
    max_leads: int
    usage_count: int
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Organization(OrganizationInDBBase):
    pass


class OrganizationInDB(OrganizationInDBBase):
    pass
