"""Fee calculation and charging service.

Fee structure (all configurable via settings):

1. **Base marketplace fee** — % of agreed price, split 50/50 between client and seller.
   Charged at escrow release (job completion).

2. **Verification compute fee** — charged to the client when they trigger /verify.
   Scales with CPU-seconds consumed by the sandbox. Flat minimum for declarative tests.

3. **Deliverable storage fee** — charged to the seller when they call /deliver.
   Scales with the byte size of the JSON-serialized result.

Both parties should factor these fees into their negotiation.
Query GET /fees for the current fee schedule before proposing/accepting jobs.
"""

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_UP

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import Agent


@dataclass
class FeeBreakdown:
    """Itemized fee charged for an action."""
    fee_type: str  # "verification", "storage", "base_client", "base_seller"
    amount: Decimal
    detail: str  # Human-readable explanation

    def to_dict(self) -> dict:
        return {
            "fee_type": self.fee_type,
            "amount": str(self.amount),
            "detail": self.detail,
        }


def calculate_verification_fee(cpu_seconds: float) -> FeeBreakdown:
    """Calculate the fee for a verification run based on CPU time consumed.

    For declarative (in-process) tests where cpu_seconds is 0 or negligible,
    the minimum fee applies.
    """
    computed = (Decimal(str(cpu_seconds)) * settings.fee_verification_per_cpu_second).quantize(
        Decimal("0.01"), rounding=ROUND_UP,
    )
    amount = max(computed, settings.fee_verification_minimum)
    return FeeBreakdown(
        fee_type="verification",
        amount=amount,
        detail=f"Verification compute: {cpu_seconds:.1f}s × ${settings.fee_verification_per_cpu_second}/s "
               f"(min ${settings.fee_verification_minimum})",
    )


def calculate_storage_fee(result: dict | list) -> FeeBreakdown:
    """Calculate the fee for storing a deliverable based on its serialized size."""
    size_bytes = len(json.dumps(result, default=str).encode())
    size_kb = Decimal(str(size_bytes)) / Decimal("1024")
    computed = (size_kb * settings.fee_storage_per_kb).quantize(
        Decimal("0.01"), rounding=ROUND_UP,
    )
    amount = max(computed, settings.fee_storage_minimum)
    return FeeBreakdown(
        fee_type="storage",
        amount=amount,
        detail=f"Deliverable storage: {size_bytes:,} bytes ({float(size_kb):.1f} KB) "
               f"× ${settings.fee_storage_per_kb}/KB (min ${settings.fee_storage_minimum})",
    )


def calculate_base_fee(agreed_price: Decimal) -> tuple[FeeBreakdown, FeeBreakdown]:
    """Calculate the base marketplace fee split between client and seller.

    Returns (client_fee, seller_fee).
    """
    total = (agreed_price * settings.fee_base_percent).quantize(Decimal("0.01"), rounding=ROUND_UP)
    # Split: half to each party (round up client's share if odd cent)
    seller_share = (total / 2).quantize(Decimal("0.01"))
    client_share = total - seller_share

    pct = settings.fee_base_percent * 100
    return (
        FeeBreakdown(
            fee_type="base_client",
            amount=client_share,
            detail=f"Marketplace fee (client share): {pct/2}% of ${agreed_price}",
        ),
        FeeBreakdown(
            fee_type="base_seller",
            amount=seller_share,
            detail=f"Marketplace fee (seller share): {pct/2}% of ${agreed_price}",
        ),
    )


async def charge_fee(
    db: AsyncSession,
    agent_id: uuid.UUID,
    fee: FeeBreakdown,
) -> Decimal:
    """Deduct a fee from an agent's balance. Returns new balance.

    Raises 422 if insufficient balance.
    """
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id).with_for_update()
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.balance < fee.amount:
        raise HTTPException(
            status_code=422,
            detail=f"Insufficient balance for {fee.fee_type} fee: "
                   f"balance ${agent.balance}, fee ${fee.amount}. {fee.detail}",
        )

    agent.balance -= fee.amount
    return agent.balance


def get_fee_schedule() -> dict:
    """Return the current fee schedule for display to agents.

    Agents should query this before negotiating to factor fees into pricing.
    """
    return {
        "version": "2.0",
        "note": "Both parties pay fees proportional to the resources they consume. "
                "Factor these into your negotiation — the agreed price is not the total cost.",
        "base_marketplace_fee": {
            "rate_percent": str(settings.fee_base_percent * 100),
            "split": "50/50 between client and seller",
            "charged_at": "Job completion (deducted from escrow)",
            "example": f"On a $100 job: client pays ${(100 * float(settings.fee_base_percent) / 2):.2f}, "
                       f"seller pays ${(100 * float(settings.fee_base_percent) / 2):.2f}",
        },
        "verification_compute_fee": {
            "rate_per_cpu_second": str(settings.fee_verification_per_cpu_second),
            "minimum": str(settings.fee_verification_minimum),
            "charged_to": "Client (triggers verification)",
            "charged_at": "Each /verify call (even if verification fails)",
            "example": f"A 30s verification script costs ${float(settings.fee_verification_per_cpu_second) * 30:.2f}. "
                       f"Minimum charge: ${settings.fee_verification_minimum}",
        },
        "deliverable_storage_fee": {
            "rate_per_kb": str(settings.fee_storage_per_kb),
            "minimum": str(settings.fee_storage_minimum),
            "charged_to": "Seller (submits deliverable)",
            "charged_at": "Each /deliver call",
            "example": f"A 50KB deliverable costs ${float(settings.fee_storage_per_kb) * 50:.2f}. "
                       f"Minimum charge: ${settings.fee_storage_minimum}",
        },
        "tip": "Agents should query this endpoint during negotiation and factor fees into "
               "their proposed/accepted price. The seller's effective payout is the agreed "
               "price minus their base fee share and storage fee. The client's total cost "
               "is the agreed price plus their base fee share and verification fee(s).",
    }
