"""Discovery endpoint."""

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rate_limit import check_rate_limit
from app.database import get_db
from app.schemas.listing import DiscoverResult
from app.schemas.pagination import PaginatedResponse
from app.services import listing as listing_service

router = APIRouter(tags=["discovery"])


@router.get(
    "/discover",
    response_model=PaginatedResponse[DiscoverResult],
    dependencies=[Depends(check_rate_limit)],
    responses={429: {"description": "Rate limit exceeded"}},
)
async def discover(
    skill_id: str | None = Query(None),
    min_rating: Decimal | None = Query(None, ge=0, le=5),
    max_price: Decimal | None = Query(None, gt=0),
    online: bool | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[DiscoverResult]:
    """Discover listings ranked by seller reputation with filters."""
    results, total = await listing_service.discover(
        db,
        skill_id=skill_id,
        min_rating=min_rating,
        max_price=max_price,

        online=online,
        limit=limit,
        offset=offset,
    )
    return PaginatedResponse(
        items=[DiscoverResult(**r) for r in results],
        total=total,
        limit=limit,
        offset=offset,
    )
