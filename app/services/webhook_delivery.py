"""Async HTTP webhook delivery worker.

Periodically delivers PENDING webhooks via HTTP POST to agents that have
an endpoint_url configured and are NOT currently connected via WebSocket.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.webhook import WebhookDelivery, WebhookStatus
from app.services.connection_manager import manager
from app.services.webhooks import sign_webhook_payload

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
HTTP_TIMEOUT = 10.0
POLL_INTERVAL = 30  # seconds


async def deliver_pending_http_webhooks(db: AsyncSession) -> int:
    """Deliver PENDING webhooks via HTTP to agents with endpoint_url (not WS-connected).

    Returns the number of deliveries processed.
    """
    # Find PENDING deliveries where target agent has endpoint_url and is NOT online via WS
    result = await db.execute(
        select(WebhookDelivery, Agent)
        .join(Agent, WebhookDelivery.target_agent_id == Agent.agent_id)
        .where(
            WebhookDelivery.status == WebhookStatus.PENDING,
            Agent.endpoint_url.isnot(None),
        )
        .order_by(WebhookDelivery.created_at.asc())
    )
    rows = list(result.all())

    if not rows:
        return 0

    processed = 0

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for delivery, agent in rows:
            # Skip agents currently connected via WebSocket
            if manager.is_connected(agent.agent_id):
                continue

            timestamp = datetime.now(UTC).isoformat()
            body = json.dumps(delivery.payload, separators=(",", ":"), ensure_ascii=False)
            signature = sign_webhook_payload(agent.webhook_secret, timestamp, body)

            try:
                response = await client.post(
                    agent.endpoint_url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Timestamp": timestamp,
                        "X-Webhook-Signature": signature,
                    },
                )

                delivery.attempts += 1

                if 200 <= response.status_code < 300:
                    delivery.status = WebhookStatus.DELIVERED
                    logger.info(
                        "HTTP webhook delivered: %s → %s (%d)",
                        delivery.event_type, agent.endpoint_url, response.status_code,
                    )
                else:
                    delivery.last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    if delivery.attempts >= MAX_ATTEMPTS:
                        delivery.status = WebhookStatus.FAILED
                        logger.warning(
                            "HTTP webhook failed permanently after %d attempts: %s → %s",
                            delivery.attempts, delivery.event_type, agent.endpoint_url,
                        )

            except Exception as exc:
                delivery.attempts += 1
                delivery.last_error = str(exc)[:500]
                if delivery.attempts >= MAX_ATTEMPTS:
                    delivery.status = WebhookStatus.FAILED
                    logger.warning(
                        "HTTP webhook failed permanently after %d attempts: %s → %s: %s",
                        delivery.attempts, delivery.event_type, agent.endpoint_url, exc,
                    )

            processed += 1

    await db.commit()
    return processed


async def run_webhook_delivery_loop() -> None:
    """Background loop that periodically delivers pending HTTP webhooks."""
    from app.database import async_session_factory

    logger.info("Webhook HTTP delivery worker started (interval=%ds)", POLL_INTERVAL)

    while True:
        try:
            async with async_session_factory() as db:
                count = await deliver_pending_http_webhooks(db)
                if count:
                    logger.info("Webhook delivery cycle processed %d deliveries", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Webhook delivery cycle failed")

        await asyncio.sleep(POLL_INTERVAL)
