"""A2A push notification delivery service.

Uses A2A-compliant JSON-RPC push notification format.
In production, delivery is via Google Cloud Tasks with exponential backoff.
"""

import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.webhook import WebhookDelivery, WebhookStatus

logger = logging.getLogger(__name__)


def sign_webhook_payload(secret: str, timestamp: str, body: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload."""
    message = f"{timestamp}.{body}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def build_a2a_push_notification(
    task_id: str,
    context_id: str | None,
    state: str,
    event_type: str,
    details: dict,
) -> dict:
    """Build an A2A-compliant push notification payload (JSON-RPC 2.0)."""
    return {
        "jsonrpc": "2.0",
        "method": "tasks/pushNotification",
        "params": {
            "taskId": task_id,
            "contextId": context_id,
            "status": {
                "state": state,
                "message": {
                    "role": "agent",
                    "parts": [
                        {
                            "kind": "data",
                            "data": {
                                "event": event_type,
                                "timestamp": datetime.now(UTC).isoformat(),
                                **details,
                            },
                        }
                    ],
                },
            },
        },
    }


# Map marketplace events to A2A task states
_EVENT_STATE_MAP = {
    "job.proposed": "submitted",
    "job.counter_received": "submitted",
    "job.accepted": "submitted",
    "job.funded": "submitted",
    "job.started": "working",
    "job.delivered": "working",
    "job.completed": "completed",
    "job.failed": "failed",
    "job.disputed": "failed",
    "job.resolved": "completed",
    "job.deadline_warning": "working",
}


async def enqueue_webhook(
    db: AsyncSession,
    target_agent_id: uuid.UUID,
    event_type: str,
    payload: dict,
) -> WebhookDelivery:
    """Create a webhook delivery record. In production, enqueues to Cloud Tasks."""
    delivery = WebhookDelivery(
        delivery_id=uuid.uuid4(),
        target_agent_id=target_agent_id,
        event_type=event_type,
        payload=payload,
        status=WebhookStatus.PENDING,
    )
    db.add(delivery)
    await db.commit()
    await db.refresh(delivery)

    logger.info(f"A2A push notification enqueued: {event_type} → {target_agent_id}")
    return delivery


async def notify_job_event(
    db: AsyncSession,
    job_id: uuid.UUID,
    event_type: str,
    details: dict,
) -> list[WebhookDelivery]:
    """Send A2A push notification to both parties of a job."""
    from app.models.job import Job

    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        return []

    a2a_state = _EVENT_STATE_MAP.get(event_type, "submitted")
    task_id = job.a2a_task_id or str(job.job_id)
    context_id = job.a2a_context_id

    payload = build_a2a_push_notification(
        task_id=task_id,
        context_id=context_id,
        state=a2a_state,
        event_type=event_type,
        details={"job_id": str(job_id), **details},
    )

    deliveries = []
    for agent_id in (job.client_agent_id, job.seller_agent_id):
        delivery = await enqueue_webhook(db, agent_id, event_type, payload)
        deliveries.append(delivery)

    return deliveries
