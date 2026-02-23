"""Review endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.middleware import AuthenticatedAgent, verify_request
from app.auth.rate_limit import check_rate_limit
from app.database import get_db
from app.schemas.review import ReviewCreate, ReviewResponse
from app.services import review as review_service

router = APIRouter(tags=["reviews"])


@router.post("/jobs/{job_id}/reviews", response_model=ReviewResponse, status_code=201)
async def submit_review(
    job_id: uuid.UUID,
    data: ReviewCreate,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    """Submit a review for a completed job."""
    review = await review_service.submit_review(db, job_id, auth.agent_id, data)
    return ReviewResponse.model_validate(review)


@router.get(
    "/agents/{agent_id}/reviews",
    response_model=list[ReviewResponse],
    dependencies=[Depends(check_rate_limit)],
)
async def get_agent_reviews(
    agent_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[ReviewResponse]:
    """Get reviews for an agent."""
    reviews = await review_service.get_reviews_for_agent(db, agent_id, limit, offset)
    return [ReviewResponse.model_validate(r) for r in reviews]


@router.get(
    "/jobs/{job_id}/reviews",
    response_model=list[ReviewResponse],
    dependencies=[Depends(check_rate_limit)],
)
async def get_job_reviews(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[ReviewResponse]:
    """Get all reviews for a job."""
    reviews = await review_service.get_reviews_for_job(db, job_id)
    return [ReviewResponse.model_validate(r) for r in reviews]
