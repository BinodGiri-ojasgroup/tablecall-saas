"""Request validation models."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class RestaurantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=80)
    owner_email: EmailStr
    owner_password: str = Field(min_length=10, max_length=128)
    plan: str = "trial"


class RestaurantUpdate(BaseModel):
    is_active: Optional[bool] = None
    plan: Optional[str] = None
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)


class PlanIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=30)
    name: str = Field(min_length=1, max_length=60)
    price_npr: int = Field(default=0, ge=0, le=10_000_000)
    max_tables: int = Field(default=0, ge=0, le=100_000)
    max_staff: int = Field(default=0, ge=0, le=100_000)
    max_menu_items: int = Field(default=0, ge=0, le=100_000)
    features: str = Field(default="", max_length=800)
    is_public: bool = True
    sort_order: int = Field(default=0, ge=-1000, le=1000)


class LeadIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    business: str = Field(default="", max_length=120)
    email: EmailStr
    phone: str = Field(default="", max_length=40)
    message: str = Field(default="", max_length=1000)
    website: str = Field(default="", max_length=200)  # honeypot: real visitors never see or fill this


class LeadUpdate(BaseModel):
    status: str = Field(pattern=r"^(new|contacted|won|dismissed)$")


class AdminCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class AdminUpdate(BaseModel):
    is_active: bool


class SiteSettingsIn(BaseModel):
    brand_name: str = Field(min_length=1, max_length=40)
    tagline: str = Field(default="", max_length=140)
    contact_email: str = Field(default="", max_length=120)
    contact_phone: str = Field(default="", max_length=40)


class SettingsIn(BaseModel):
    """Restaurant self-service settings. Only the fields sent are changed."""
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    brand_color: Optional[str] = Field(default=None, pattern=r"^(#[0-9a-fA-F]{6})?$")
    wifi_ssid: Optional[str] = Field(default=None, max_length=32)
    wifi_password: Optional[str] = Field(default=None, max_length=63)
    pin_mode: Optional[str] = Field(default=None, pattern=r"^(session|timed)$")
    pin_ttl_minutes: Optional[int] = Field(default=None, ge=1, le=10080)
    logo_media_id: Optional[int] = None
    remove_logo: bool = False


class PasswordIn(BaseModel):
    password: str = Field(min_length=10, max_length=128)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=10, max_length=128)


class StaffCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class StaffUpdate(BaseModel):
    is_active: bool


class TableCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sort_order: int = Field(default=0, ge=-10000, le=10000)


class MenuItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    price_npr: float = Field(ge=0, le=1000000)
    category_id: Optional[int] = None
    image_url: Optional[str] = Field(default=None, max_length=500)
    image_id: Optional[int] = None
    is_available: bool = True
    sort_order: int = Field(default=0, ge=-10000, le=10000)

    @field_validator("image_url")
    @classmethod
    def _http_only(cls, value: Optional[str]) -> Optional[str]:
        value = (value or "").strip()
        if not value:
            return None
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("Image URL must start with http:// or https://")
        return value


class AvailabilityIn(BaseModel):
    is_available: bool


class RequestIn(BaseModel):
    pin: str = Field(pattern=r"^\d{4,8}$")
    request_type: str = Field(pattern=r"^(call_waiter|request_bill|need_water|assistance)$")


class StatusIn(BaseModel):
    status: str = Field(pattern=r"^(accepted|completed|cancelled)$")
