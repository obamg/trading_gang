"""Auth request/response schemas."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshRequest(BaseModel):
    refresh_token: str = Field(max_length=512)


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str | None
    avatar_url: str | None
    is_active: bool
    is_verified: bool
    auth_provider: str
    created_at: datetime

    model_config = {"from_attributes": True}


class VerifyEmailRequest(BaseModel):
    token: str = Field(max_length=512)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(max_length=512)
    new_password: str = Field(min_length=8, max_length=128)


class MessageResponse(BaseModel):
    message: str
