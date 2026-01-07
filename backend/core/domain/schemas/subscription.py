"""
Subscription schemas for LeadBoost SaaS
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class PlanBase(BaseModel):
    name: str
    max_leads_per_day: int
    can_export: bool
    can_use_ai: bool


class PlanCreate(PlanBase):
    pass


class PlanUpdate(BaseModel):
    max_leads_per_day: Optional[int] = None
    can_export: Optional[bool] = None
    can_use_ai: Optional[bool] = None


class Plan(PlanBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SubscriptionBase(BaseModel):
    organization_id: int
    plan_id: int
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    is_active: bool = True


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionUpdate(BaseModel):
    plan_id: Optional[int] = None
    end_date: Optional[datetime] = None
    is_active: Optional[bool] = None


class Subscription(SubscriptionBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PlanUsage(BaseModel):
    plan_name: str
    max_leads_per_day: int
    can_export: bool
    can_use_ai: bool
    current_usage: int
    remaining_daily_leads: int
    can_process_more_today: bool
