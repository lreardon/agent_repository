"""Review and reputation business logic."""

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.job import Job, JobStatus
from app.models.review import Review, ReviewRole
from app.schemas.agent import ReputationResponse
from app.schemas.review import ReviewCreate


async def submit_review(
    db: AsyncSession,
    job_id: uuid.UUID,
    reviewer_agent_id: uuid.UUID,
    data: ReviewCreate,
) -> Review:
    """Submit a review for a completed job. Each party can review the other once."""
    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.RESOLVED):
        raise HTTPException(status_code=409, detail="Can only review completed, failed, or resolved jobs")

    # Determine reviewer/reviewee and role
    if reviewer_agent_id == job.client_agent_id:
        reviewee_id = job.seller_agent_id
        role = ReviewRole.CLIENT_REVIEWING_SELLER
    elif reviewer_agent_id == job.seller_agent_id:
        reviewee_id = job.client_agent_id
        role = ReviewRole.SELLER_REVIEWING_CLIENT
    else:
        raise HTTPException(status_code=403, detail="Only parties to the job can leave reviews")

    # Check for duplicate review
    existing = await db.execute(
        select(Review).where(
            Review.job_id == job_id,
            Review.reviewer_agent_id == reviewer_agent_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="You have already reviewed this job")

    review = Review(
        review_id=uuid.uuid4(),
        job_id=job_id,
        reviewer_agent_id=reviewer_agent_id,
        reviewee_agent_id=reviewee_id,
        role=role,
        rating=data.rating,
        tags=data.tags,
        comment=data.comment,
    )
    db.add(review)

    # Update reputation score with recency weighting
    await _update_reputation(db, reviewee_id, role)

    await db.commit()
    await db.refresh(review)
    return review


def _recency_weight(review_date: datetime) -> float:
    """Compute recency weight: 30d=2x, 90d=1.5x, older=1x."""
    now = datetime.now(UTC)
    age_days = (now - review_date).days
    if age_days <= 30:
        return 2.0
    if age_days <= 90:
        return 1.5
    return 1.0


async def _update_reputation(
    db: AsyncSession,
    agent_id: uuid.UUID,
    review_role: ReviewRole,
) -> None:
    """Recalculate agent's reputation with recency weighting and confidence."""
    # Determine which reviews to use and which field to update
    is_seller_rep = review_role == ReviewRole.CLIENT_REVIEWING_SELLER

    # Get all reviews for this agent in the appropriate role
    query = select(Review).where(Review.reviewee_agent_id == agent_id)
    if is_seller_rep:
        query = query.where(Review.role == ReviewRole.CLIENT_REVIEWING_SELLER)
    else:
        query = query.where(Review.role == ReviewRole.SELLER_REVIEWING_CLIENT)

    result = await db.execute(query)
    reviews = list(result.scalars().all())

    if not reviews:
        return

    # Recency-weighted average
    total_weight = 0.0
    weighted_sum = 0.0
    for review in reviews:
        w = _recency_weight(review.created_at)
        weighted_sum += review.rating * w
        total_weight += w

    raw_score = weighted_sum / total_weight if total_weight > 0 else 0.0

    # Confidence factor: min(1.0, num_reviews / 20)
    confidence = min(1.0, len(reviews) / 20.0)
    reputation = raw_score * confidence

    score = min(Decimal(str(round(reputation, 2))), Decimal("5.00"))

    agent_result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = agent_result.scalar_one()
    if is_seller_rep:
        agent.reputation_seller = score
    else:
        agent.reputation_client = score


async def get_reputation(
    db: AsyncSession, agent_id: uuid.UUID
) -> ReputationResponse:
    """Compute full reputation summary for an agent."""
    from app.services.agent import get_agent
    agent = await get_agent(db, agent_id)

    # Count reviews by role
    seller_count_result = await db.execute(
        select(func.count()).select_from(Review).where(
            Review.reviewee_agent_id == agent_id,
            Review.role == ReviewRole.CLIENT_REVIEWING_SELLER,
        )
    )
    seller_count = seller_count_result.scalar() or 0

    client_count_result = await db.execute(
        select(func.count()).select_from(Review).where(
            Review.reviewee_agent_id == agent_id,
            Review.role == ReviewRole.SELLER_REVIEWING_CLIENT,
        )
    )
    client_count = client_count_result.scalar() or 0

    # Display logic: "New" if < 3 reviews
    seller_display = str(agent.reputation_seller) if seller_count >= 3 else "New"
    client_display = str(agent.reputation_client) if client_count >= 3 else "New"

    # Aggregate tags
    tag_result = await db.execute(
        select(Review.tags).where(
            Review.reviewee_agent_id == agent_id,
            Review.tags.isnot(None),
        )
    )
    all_tags: list[str] = []
    for (tags,) in tag_result:
        if tags:
            all_tags.extend(tags)
    tag_counts = Counter(all_tags)
    top_tags = [tag for tag, _ in tag_counts.most_common(5)]

    return ReputationResponse(
        agent_id=agent_id,
        reputation_seller=agent.reputation_seller if seller_count >= 3 else None,
        reputation_seller_display=seller_display,
        reputation_client=agent.reputation_client if client_count >= 3 else None,
        reputation_client_display=client_display,
        total_reviews_as_seller=seller_count,
        total_reviews_as_client=client_count,
        top_tags=top_tags,
    )


async def get_reviews_for_agent(
    db: AsyncSession, agent_id: uuid.UUID, limit: int = 20, offset: int = 0
) -> list[Review]:
    """Get reviews where agent is the reviewee."""
    result = await db.execute(
        select(Review)
        .where(Review.reviewee_agent_id == agent_id)
        .order_by(Review.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def get_reviews_for_job(db: AsyncSession, job_id: uuid.UUID) -> list[Review]:
    """Get all reviews for a job."""
    result = await db.execute(
        select(Review)
        .where(Review.job_id == job_id)
        .order_by(Review.created_at.asc())
    )
    return list(result.scalars().all())
