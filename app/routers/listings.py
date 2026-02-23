"""Listing CRUD endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.middleware import AuthenticatedAgent, verify_request
from app.auth.rate_limit import check_rate_limit
from app.database import get_db
from app.schemas.listing import ListingCreate, ListingResponse, ListingUpdate
from app.services import listing as listing_service

router = APIRouter(tags=["listings"])


@router.post(
    "/agents/{agent_id}/listings",
    response_model=ListingResponse,
    status_code=201,
)
async def create_listing(
    agent_id: uuid.UUID,
    data: ListingCreate,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> ListingResponse:
    """Create a new listing. Own agent only."""
    if auth.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Can only create listings for own agent")
    listing = await listing_service.create_listing(db, agent_id, data)
    return ListingResponse.model_validate(listing)


@router.get(
    "/listings/{listing_id}",
    response_model=ListingResponse,
    dependencies=[Depends(check_rate_limit)],
)
async def get_listing(
    listing_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ListingResponse:
    """Get listing details."""
    listing = await listing_service.get_listing(db, listing_id)
    return ListingResponse.model_validate(listing)


@router.patch("/listings/{listing_id}", response_model=ListingResponse)
async def update_listing(
    listing_id: uuid.UUID,
    data: ListingUpdate,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> ListingResponse:
    """Update a listing. Seller only."""
    listing = await listing_service.update_listing(db, listing_id, auth.agent_id, data)
    return ListingResponse.model_validate(listing)


@router.get(
    "/listings",
    response_model=list[ListingResponse],
    dependencies=[Depends(check_rate_limit)],
)
async def browse_listings(
    skill_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[ListingResponse]:
    """Browse active listings."""
    listings = await listing_service.browse_listings(db, skill_id, limit, offset)
    return [ListingResponse.model_validate(l) for l in listings]
