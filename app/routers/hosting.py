"""Hosting API — deploy, manage, and monitor hosted agents."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.middleware import AuthenticatedAgent, verify_request
from app.database import get_db
from app.schemas.hosting import (
    DeployResponse,
    DeployStatusResponse,
    LogsResponse,
    SecretCreate,
    SecretResponse,
    SecretsListResponse,
)
from app.services.hosting.deploy import (
    deploy_agent,
    get_deployment,
    get_logs,
    undeploy_agent,
)
from app.services.hosting.secrets import (
    delete_secret,
    list_secrets,
    set_secret,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/{agent_id}/hosting", tags=["hosting"])


def _check_owner(agent_id: str, auth: AuthenticatedAgent) -> uuid.UUID:
    """Verify the authenticated agent owns this resource."""
    try:
        target = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id")
    if target != auth.agent_id:
        raise HTTPException(status_code=403, detail="Not your agent")
    return target


# ------------------------------------------------------------------
# Deploy
# ------------------------------------------------------------------


@router.post("/deploy", response_model=DeployResponse, status_code=201)
async def create_deployment(
    agent_id: str,
    file: UploadFile = File(..., description="tar.gz archive containing agent code and arcoa.yaml"),
    runtime: str = Form(default="python:3.13"),
    region: str = Form(default="us-west1"),
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
):
    """Deploy agent code to Arcoa hosting.

    Upload a tar.gz archive containing your agent code and an arcoa.yaml manifest.
    The platform builds a container image and runs your agent.
    """
    target = _check_owner(agent_id, auth)

    archive_bytes = await file.read()
    if not archive_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        hosted = await deploy_agent(
            db, target, archive_bytes,
            deploy_params={"runtime": runtime, "region": region},
        )
        await db.commit()
        return DeployResponse.model_validate(hosted)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/deploy", response_model=DeployStatusResponse)
async def get_deploy_status(
    agent_id: str,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
):
    """Get the current deployment status."""
    target = _check_owner(agent_id, auth)

    hosted = await get_deployment(db, target)
    if not hosted:
        raise HTTPException(status_code=404, detail="No deployment found")

    return DeployStatusResponse(
        status=hosted.status.value,
        container_id=hosted.container_id,
        build_log=hosted.build_log,
        error_message=hosted.error_message,
        updated_at=hosted.updated_at,
    )


@router.delete("/deploy", status_code=200)
async def delete_deployment(
    agent_id: str,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
):
    """Stop and remove a hosted deployment."""
    target = _check_owner(agent_id, auth)

    success = await undeploy_agent(db, target)
    if not success:
        raise HTTPException(status_code=404, detail="No deployment found")

    await db.commit()
    return {"detail": "Deployment removed"}


@router.get("/logs", response_model=LogsResponse)
async def get_deploy_logs(
    agent_id: str,
    tail: int = 200,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
):
    """Get logs from a hosted agent."""
    target = _check_owner(agent_id, auth)

    try:
        logs = await get_logs(db, target, tail=tail)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    hosted = await get_deployment(db, target)
    return LogsResponse(
        logs=logs,
        container_id=hosted.container_id if hosted else None,
    )


# ------------------------------------------------------------------
# Secrets
# ------------------------------------------------------------------


@router.post("/secrets", response_model=SecretResponse, status_code=201)
async def create_secret(
    agent_id: str,
    body: SecretCreate,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
):
    """Set an encrypted secret for your hosted agent."""
    target = _check_owner(agent_id, auth)

    secret = await set_secret(db, target, body.key, body.value)
    await db.commit()
    return SecretResponse(key=secret.key, created_at=secret.created_at)


@router.get("/secrets", response_model=SecretsListResponse)
async def list_agent_secrets(
    agent_id: str,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
):
    """List all secrets (keys only, no values)."""
    target = _check_owner(agent_id, auth)

    secrets = await list_secrets(db, target)
    return SecretsListResponse(
        secrets=[
            SecretResponse(key=s.key, created_at=s.created_at)
            for s in secrets
        ]
    )


@router.delete("/secrets/{key}", status_code=200)
async def delete_agent_secret(
    agent_id: str,
    key: str,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
):
    """Delete a secret."""
    target = _check_owner(agent_id, auth)

    deleted = await delete_secret(db, target, key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Secret '{key}' not found")

    await db.commit()
    return {"detail": f"Secret '{key}' deleted"}
