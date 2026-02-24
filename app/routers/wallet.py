"""Wallet endpoints: deposit addresses, withdrawals, transaction history."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.middleware import AuthenticatedAgent, verify_request
from app.config import settings
from app.database import get_db
from app.schemas.wallet import (
    AvailableBalanceResponse,
    DepositAddressResponse,
    DepositTransactionResponse,
    TransactionHistoryResponse,
    WithdrawalCreateRequest,
    WithdrawalResponse,
)
from app.services import wallet as wallet_service

router = APIRouter(prefix="/agents/{agent_id}/wallet", tags=["wallet"])


def _assert_own_agent(auth: AuthenticatedAgent, agent_id: uuid.UUID) -> None:
    if auth.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Can only access own wallet")


@router.get("/deposit-address", response_model=DepositAddressResponse)
async def get_deposit_address(
    agent_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> DepositAddressResponse:
    """Get (or create) the agent's unique USDC deposit address."""
    _assert_own_agent(auth, agent_id)
    addr = await wallet_service.get_or_create_deposit_address(db, agent_id)
    return DepositAddressResponse(
        agent_id=agent_id,
        address=addr.address,
        network=settings.blockchain_network,
        usdc_contract=settings.resolved_usdc_address,
        min_deposit=settings.min_deposit_amount,
    )


@router.post("/withdraw", response_model=WithdrawalResponse, status_code=201)
async def request_withdrawal(
    agent_id: uuid.UUID,
    data: WithdrawalCreateRequest,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> WithdrawalResponse:
    """Request a USDC withdrawal. Amount is deducted from balance immediately."""
    _assert_own_agent(auth, agent_id)
    withdrawal = await wallet_service.request_withdrawal(
        db, agent_id, data.amount, data.destination_address,
    )
    return WithdrawalResponse.model_validate(withdrawal)


@router.get("/transactions", response_model=TransactionHistoryResponse)
async def get_transactions(
    agent_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> TransactionHistoryResponse:
    """Get deposit and withdrawal history."""
    _assert_own_agent(auth, agent_id)
    deposits = await wallet_service.get_deposit_history(db, agent_id)
    withdrawals = await wallet_service.get_withdrawal_history(db, agent_id)
    return TransactionHistoryResponse(
        deposits=[DepositTransactionResponse.model_validate(d) for d in deposits],
        withdrawals=[WithdrawalResponse.model_validate(w) for w in withdrawals],
    )


@router.get("/balance", response_model=AvailableBalanceResponse)
async def get_available_balance(
    agent_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> AvailableBalanceResponse:
    """Get balance with available amount (accounts for pending withdrawals)."""
    _assert_own_agent(auth, agent_id)
    balance, available, pending = await wallet_service.get_available_balance(db, agent_id)
    return AvailableBalanceResponse(
        agent_id=agent_id,
        balance=balance,
        available_balance=available,
        pending_withdrawals=pending,
    )
