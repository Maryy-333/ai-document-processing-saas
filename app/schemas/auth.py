"""
Authentication schemas (Phase 10).

UserResponse deliberately never includes hashed_password.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    # bcrypt silently truncates beyond 72 bytes; capping here avoids a
    # confusing silent-truncation surprise rather than adding a heavier
    # password policy than this MVP needs.
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    organization_id: UUID
    is_active: bool
    created_at: datetime
