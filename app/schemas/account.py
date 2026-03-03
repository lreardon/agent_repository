"""Pydantic schemas for signup / email verification."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_EMAIL_PATTERN = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
)


class SignupRequest(BaseModel):
    email: str = Field(..., max_length=320)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_PATTERN.match(v):
            raise ValueError("Invalid email address")
        return v


class SignupResponse(BaseModel):
    message: str = "Verification email sent. Check your inbox."


class VerifyEmailResponse(BaseModel):
    message: str = "Email verified."
    registration_token: str = Field(
        ..., description="One-time token to use in POST /agents registration"
    )
    expires_in_seconds: int = Field(
        ..., description="Seconds until the registration token expires"
    )


# --- Key Recovery ---


class RecoveryRequest(BaseModel):
    email: str = Field(..., max_length=320)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_PATTERN.match(v):
            raise ValueError("Invalid email address")
        return v


class RecoveryResponse(BaseModel):
    message: str = (
        "If an account with that email exists, a recovery link has been sent."
    )


class VerifyRecoveryResponse(BaseModel):
    message: str = "Recovery verified."
    recovery_token: str = Field(
        ..., description="One-time token to use in POST /auth/rotate-key"
    )
    expires_in_seconds: int = Field(
        ..., description="Seconds until the recovery token expires"
    )


class RotateKeyRequest(BaseModel):
    recovery_token: str = Field(..., max_length=128)
    new_public_key: str = Field(..., max_length=128, description="Ed25519 public key (hex)")


class RotateKeyResponse(BaseModel):
    message: str = "Public key rotated successfully."
