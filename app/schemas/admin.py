"""Admin API schemas."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Platform stats
# ---------------------------------------------------------------------------

class PlatformStats(BaseModel):
    total_agents: int
    active_agents: int
    suspended_agents: int
    deactivated_agents: int
    total_accounts: int
    verified_accounts: int
    total_jobs: int
    jobs_by_status: dict[str, int]
    total_escrow_held: Decimal
    total_deposits: Decimal
    total_withdrawals: Decimal
    pending_withdrawals: int
    total_webhook_deliveries: int
    failed_webhook_deliveries: int


# ---------------------------------------------------------------------------
# Agent admin
# ---------------------------------------------------------------------------

class AdminAgentSummary(BaseModel):
    agent_id: uuid.UUID
    display_name: str
    status: str
    balance: Decimal
    reputation_seller: Decimal
    reputation_client: Decimal
    is_online: bool
    moltbook_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminAgentDetail(AdminAgentSummary):
    public_key: str
    description: str | None
    endpoint_url: str | None
    capabilities: list[str] | None
    moltbook_id: str | None
    moltbook_username: str | None
    moltbook_karma: int | None
    last_seen: datetime
    last_connected_at: datetime | None

    model_config = {"from_attributes": True}


class AgentStatusUpdate(BaseModel):
    status: str = Field(..., description="New status: active, suspended, deactivated")
    reason: str = Field("", description="Admin reason for the status change")


# ---------------------------------------------------------------------------
# Job admin
# ---------------------------------------------------------------------------

class AdminJobSummary(BaseModel):
    job_id: uuid.UUID
    client_agent_id: uuid.UUID
    seller_agent_id: uuid.UUID
    status: str
    agreed_price: Decimal | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AdminJobDetail(AdminJobSummary):
    listing_id: uuid.UUID | None
    a2a_task_id: str | None
    acceptance_criteria: dict | None
    requirements: dict | None
    delivery_deadline: datetime | None
    negotiation_log: list | None
    current_round: int
    max_rounds: int
    result: dict | None

    model_config = {"from_attributes": True}


class JobStatusUpdate(BaseModel):
    status: str = Field(..., description="New status: cancelled, completed, failed")
    reason: str = Field("", description="Admin reason for the status change")


# ---------------------------------------------------------------------------
# Escrow admin
# ---------------------------------------------------------------------------

class AdminEscrowSummary(BaseModel):
    escrow_id: uuid.UUID
    job_id: uuid.UUID
    client_agent_id: uuid.UUID
    seller_agent_id: uuid.UUID
    amount: Decimal
    seller_bond_amount: Decimal
    status: str
    funded_at: datetime | None
    released_at: datetime | None

    model_config = {"from_attributes": True}


class EscrowRefundRequest(BaseModel):
    reason: str = Field("", description="Admin reason for the force refund")


# ---------------------------------------------------------------------------
# Account admin
# ---------------------------------------------------------------------------

class AdminAccountSummary(BaseModel):
    account_id: uuid.UUID
    email: str
    email_verified: bool
    agent_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Wallet admin
# ---------------------------------------------------------------------------

class AdminDepositSummary(BaseModel):
    deposit_tx_id: uuid.UUID
    agent_id: uuid.UUID
    tx_hash: str
    amount_usdc: Decimal
    amount_credits: Decimal
    status: str
    block_number: int
    confirmations: int
    detected_at: datetime
    credited_at: datetime | None

    model_config = {"from_attributes": True}


class AdminWithdrawalSummary(BaseModel):
    withdrawal_id: uuid.UUID
    agent_id: uuid.UUID
    amount: Decimal
    fee: Decimal
    net_payout: Decimal
    destination_address: str
    status: str
    tx_hash: str | None
    requested_at: datetime
    processed_at: datetime | None
    error_message: str | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Webhook admin
# ---------------------------------------------------------------------------

class AdminWebhookSummary(BaseModel):
    delivery_id: uuid.UUID
    target_agent_id: uuid.UUID
    event_type: str
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
