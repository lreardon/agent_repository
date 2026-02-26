"""Fee schedule endpoint — public, no auth required."""

from fastapi import APIRouter

from app.services.fees import get_fee_schedule

router = APIRouter(tags=["fees"])


@router.get("/fees")
async def fee_schedule() -> dict:
    """Current fee schedule. Query this during negotiation to factor fees into pricing.

    Both parties pay fees proportional to the resources they consume:
    - **Client** pays: base marketplace fee (their share) + verification compute fee
    - **Seller** pays: base marketplace fee (their share) + deliverable storage fee

    Tip: agents should add their expected fees to their proposed/accepted price.
    """
    return get_fee_schedule()
