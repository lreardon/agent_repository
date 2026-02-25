"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.routers import agents, discover, jobs, listings, reviews, wallet


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown."""
    yield


app = FastAPI(
    title="Agent Registry & Marketplace",
    description="A2A-compatible agent-to-agent task marketplace",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS - allow browsers to call the API (e.g., for admin dashboards)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict to specific origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware (order matters — outermost first)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=1_048_576)

# Routers
app.include_router(agents.router)
app.include_router(listings.router)
app.include_router(discover.router)
app.include_router(jobs.router)
app.include_router(reviews.router)
app.include_router(wallet.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
