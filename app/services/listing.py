"""Listing and discovery business logic."""

import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentStatus
from app.models.listing import Listing, ListingStatus, PriceModel
from app.schemas.listing import ListingCreate, ListingUpdate
from app.services.agent_card import get_skill_ids_from_card


async def create_listing(
    db: AsyncSession, seller_agent_id: uuid.UUID, data: ListingCreate
) -> Listing:
    """Create a new service listing. skill_id must exist in Agent Card."""
    # Verify seller exists and is active
    result = await db.execute(select(Agent).where(Agent.agent_id == seller_agent_id))
    agent = result.scalar_one_or_none()
    if agent is None or agent.status != AgentStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="Agent not found or not active")

    # Validate skill_id against Agent Card (if card exists)
    if agent.a2a_agent_card:
        valid_skills = get_skill_ids_from_card(agent.a2a_agent_card)
        if data.skill_id not in valid_skills:
            raise HTTPException(
                status_code=422,
                detail=f"skill_id '{data.skill_id}' not found in agent's A2A Agent Card skills",
            )

    listing = Listing(
        listing_id=uuid.uuid4(),
        seller_agent_id=seller_agent_id,
        skill_id=data.skill_id,
        description=data.description,
        price_model=PriceModel(data.price_model),
        base_price=data.base_price,
        currency=data.currency,
        sla=data.sla,
    )
    db.add(listing)
    await db.commit()
    await db.refresh(listing)
    return listing


async def get_listing(db: AsyncSession, listing_id: uuid.UUID) -> Listing:
    """Get listing by ID."""
    result = await db.execute(select(Listing).where(Listing.listing_id == listing_id))
    listing = result.scalar_one_or_none()
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return listing


async def update_listing(
    db: AsyncSession,
    listing_id: uuid.UUID,
    seller_agent_id: uuid.UUID,
    data: ListingUpdate,
) -> Listing:
    """Update a listing. Seller only."""
    listing = await get_listing(db, listing_id)
    if listing.seller_agent_id != seller_agent_id:
        raise HTTPException(status_code=403, detail="Can only update own listings")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "price_model" and value is not None:
            value = PriceModel(value)
        if field == "status" and value is not None:
            value = ListingStatus(value)
        setattr(listing, field, value)

    await db.commit()
    await db.refresh(listing)
    return listing


async def browse_listings(
    db: AsyncSession,
    skill_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Listing]:
    """Browse active listings, optionally filtered by skill_id."""
    query = select(Listing).where(Listing.status == ListingStatus.ACTIVE)
    if skill_id:
        query = query.where(Listing.skill_id.ilike(f"%{skill_id}%"))
    query = query.order_by(Listing.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())


async def discover(
    db: AsyncSession,
    skill_id: str | None = None,
    min_rating: Decimal | None = None,
    max_price: Decimal | None = None,
    price_model: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Discover listings with seller reputation, ranked by reputation."""
    query = (
        select(Listing, Agent.display_name, Agent.reputation_seller, Agent.a2a_agent_card)
        .join(Agent, Listing.seller_agent_id == Agent.agent_id)
        .where(Listing.status == ListingStatus.ACTIVE)
        .where(Agent.status == AgentStatus.ACTIVE)
    )

    if skill_id:
        query = query.where(Listing.skill_id.ilike(f"%{skill_id}%"))
    if min_rating is not None:
        query = query.where(Agent.reputation_seller >= min_rating)
    if max_price is not None:
        query = query.where(Listing.base_price <= max_price)
    if price_model:
        query = query.where(Listing.price_model == PriceModel(price_model))

    # Rank by seller reputation descending, then by price ascending
    query = (
        query.order_by(Agent.reputation_seller.desc(), Listing.base_price.asc())
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(query)
    rows = result.all()

    results = []
    for row in rows:
        # Extract A2A skill metadata if card is available
        a2a_skill = None
        if row.a2a_agent_card:
            for skill in row.a2a_agent_card.get("skills", []):
                if skill.get("id") == row.Listing.skill_id:
                    a2a_skill = {
                        "name": skill.get("name"),
                        "description": skill.get("description"),
                        "tags": skill.get("tags", []),
                        "examples": skill.get("examples", []),
                    }
                    break

        results.append({
            "listing_id": row.Listing.listing_id,
            "seller_agent_id": row.Listing.seller_agent_id,
            "seller_display_name": row.display_name,
            "seller_reputation": row.reputation_seller,
            "skill_id": row.Listing.skill_id,
            "description": row.Listing.description,
            "price_model": row.Listing.price_model.value,
            "base_price": row.Listing.base_price,
            "currency": row.Listing.currency,
            "sla": row.Listing.sla,
            "a2a_skill": a2a_skill,
        })
    return results
