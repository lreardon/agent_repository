"""Wallet models: deposit addresses, deposit transactions, withdrawal requests."""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DepositStatus(enum.Enum):
    PENDING = "pending"
    CONFIRMING = "confirming"
    CREDITED = "credited"
    FAILED = "failed"


class WithdrawalStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class DepositAddress(Base):
    __tablename__ = "deposit_addresses"

    deposit_address_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    address: Mapped[str] = mapped_column(String(42), unique=True, nullable=False)
    derivation_index: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class DepositTransaction(Base):
    __tablename__ = "deposit_transactions"

    deposit_tx_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False
    )
    tx_hash: Mapped[str] = mapped_column(String(66), unique=True, nullable=False)
    from_address: Mapped[str] = mapped_column(String(42), nullable=False)
    amount_usdc: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, doc="USDC amount (6 decimals)"
    )
    amount_credits: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, doc="Platform credits (1 USDC = 1 credit)"
    )
    confirmations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[DepositStatus] = mapped_column(
        Enum(DepositStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=DepositStatus.PENDING,
    )
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    credited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"

    withdrawal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, doc="Total amount deducted from balance"
    )
    fee: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, doc="Flat fee covering gas"
    )
    net_payout: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, doc="Amount sent as USDC (amount - fee)"
    )
    destination_address: Mapped[str] = mapped_column(String(42), nullable=False)
    status: Mapped[WithdrawalStatus] = mapped_column(
        Enum(WithdrawalStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=WithdrawalStatus.PENDING,
    )
    tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
