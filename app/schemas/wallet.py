"""Pydantic v2 schemas for wallet/crypto endpoints."""

import re
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class DepositAddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_id: uuid.UUID
    address: str
    network: str
    usdc_contract: str
    min_deposit: Decimal


class DepositTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    deposit_tx_id: uuid.UUID
    tx_hash: str
    from_address: str
    amount_usdc: Decimal
    amount_credits: Decimal
    confirmations: int
    status: str
    detected_at: datetime
    credited_at: datetime | None

    @field_validator("status", mode="before")
    @classmethod
    def serialize_status(cls, v: object) -> str:
        return v.value if hasattr(v, "value") else str(v)


class WithdrawalCreateRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    destination_address: str = Field(..., min_length=42, max_length=42)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: Decimal) -> Decimal:
        if v < Decimal("1.00"):
            raise ValueError("Minimum withdrawal is $1.00")
        if v > Decimal("100000.00"):
            raise ValueError("Maximum withdrawal is $100,000.00")
        return v

    @field_validator("destination_address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if not _ETH_ADDRESS_RE.match(v):
            raise ValueError("Invalid Ethereum address format (expected 0x + 40 hex chars)")
        return v


class WithdrawalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    withdrawal_id: uuid.UUID
    amount: Decimal
    fee: Decimal
    net_payout: Decimal
    destination_address: str
    status: str
    tx_hash: str | None
    requested_at: datetime
    processed_at: datetime | None

    @field_validator("status", mode="before")
    @classmethod
    def serialize_status(cls, v: object) -> str:
        return v.value if hasattr(v, "value") else str(v)


class TransactionHistoryResponse(BaseModel):
    deposits: list[DepositTransactionResponse]
    withdrawals: list[WithdrawalResponse]


class AvailableBalanceResponse(BaseModel):
    agent_id: uuid.UUID
    balance: Decimal
    available_balance: Decimal
    pending_withdrawals: Decimal


class DepositNotifyRequest(BaseModel):
    tx_hash: str = Field(..., min_length=64, max_length=66)

    @field_validator("tx_hash")
    @classmethod
    def validate_tx_hash(cls, v: str) -> str:
        # Normalize: accept with or without 0x prefix
        if not v.startswith("0x"):
            v = f"0x{v}"
        if not re.match(r"^0x[0-9a-fA-F]{64}$", v):
            raise ValueError("Invalid transaction hash (expected 64 hex chars, optional 0x prefix)")
        return v


class DepositNotifyResponse(BaseModel):
    deposit_tx_id: uuid.UUID
    tx_hash: str
    amount_usdc: Decimal
    status: str
    confirmations_required: int
    message: str
