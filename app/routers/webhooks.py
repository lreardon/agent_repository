"""Webhook delivery listing and redelivery endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.middleware import AuthenticatedAgent, verify_request
from app.auth.rate_limit import check_rate_limit
from app.database import get_db
from app.models.webhook import WebhookDelivery, WebhookStatus
from app.schemas.errors import OWNER_ERRORS
from app.schemas.webhook import WebhookDeliveryResponse

router = APIRouter(prefix="/agents", tags=["webhooks"])


@router.get(
    "/{agent_id}/webhooks",
    response_model=list[WebhookDeliveryResponse],
    dependencies=[Depends(check_rate_limit)],
    responses=OWNER_ERRORS,
)
async def list_webhook_deliveries(
    agent_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(None, description="Filter by status: pending, delivered, failed"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[WebhookDeliveryResponse]:
    """List recent webhook deliveries for the authenticated agent."""
    if auth.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="Can only view own webhooks")

    query = select(WebhookDelivery).where(
        WebhookDelivery.target_agent_id == agent_id
    )

    if status is not None:
        try:
            status_enum = WebhookStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: pending, delivered, failed",
            )
        query = query.where(WebhookDelivery.status == status_enum)

    query = query.order_by(WebhookDelivery.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    deliveries = list(result.scalars().all())
    return [WebhookDeliveryResponse.model_validate(d) for d in deliveries]


@router.post(
    "/{agent_id}/webhooks/{delivery_id}/redeliver",
    response_model=WebhookDeliveryResponse,
    dependencies=[Depends(check_rate_limit)],
    responses={**OWNER_ERRORS, 409: {"description": "Delivery is already pending"}},
)
async def redeliver_webhook(
    agent_id: uuid.UUID,
    delivery_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> WebhookDeliveryResponse:
    """Reset a FAILED or DELIVERED webhook back to PENDING for re-attempt."""
    if auth.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="Can only redeliver own webhooks")

    result = await db.execute(
        select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
    )
    delivery = result.scalar_one_or_none()

    if delivery is None or delivery.target_agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Webhook delivery not found")

    if delivery.status == WebhookStatus.PENDING:
        raise HTTPException(status_code=409, detail="Delivery is already pending")

    delivery.status = WebhookStatus.PENDING
    delivery.attempts = 0
    delivery.last_error = None
    await db.commit()
    await db.refresh(delivery)

    return WebhookDeliveryResponse.model_validate(delivery)
