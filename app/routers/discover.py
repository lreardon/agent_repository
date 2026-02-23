"""Discovery endpoint."""

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rate_limit import check_rate_limit
from app.database import get_db
from app.schemas.listing import DiscoverResult
from app.services import listing as listing_service

router = APIRouter(tags=["discovery"])


@router.get(
    "/discover",
    response_model=list[DiscoverResult],
    dependencies=[Depends(check_rate_limit)],
)
async def discover(
    skill_id: str | None = Query(None),
    min_rating: Decimal | None = Query(None, ge=0, le=5),
    max_price: Decimal | None = Query(None, gt=0),
    price_model: str | None = Query(None, pattern=r"^(per_call|per_unit|per_hour|flat)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[DiscoverResult]:
    """Discover listings ranked by seller reputation with filters."""
    results = await listing_service.discover(
        db,
        skill_id=skill_id,
        min_rating=min_rating,
        max_price=max_price,
        price_model=price_model,
        limit=limit,
        offset=offset,
    )
    return [DiscoverResult(**r) for r in results]
