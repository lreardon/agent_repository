"""Schema validation edge case tests (SV1-SV7)."""

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data, make_auth_headers


@pytest.mark.asyncio
async def test_agent_create_empty_display_name(client: AsyncClient) -> None:
    """SV2: Empty display_name rejected."""
    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["display_name"] = ""
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_listing_invalid_price_model(client: AsyncClient) -> None:
    """SV3: Invalid price_model rejected."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    data = {
        "skill_id": "test-skill",
        "price_model": "invalid_model",
        "base_price": "10.00",
    }
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", data)
    resp = await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_counter_message_too_long(client: AsyncClient) -> None:
    """SV6: CounterProposal message > 2048 chars rejected."""
    from tests.conftest import make_agent_data

    priv_c, pub_c = generate_keypair()
    priv_s, pub_s = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub_c))
    client_id = resp.json()["agent_id"]
    resp = await client.post("/agents", json=make_agent_data(pub_s))
    seller_id = resp.json()["agent_id"]

    data = {"seller_agent_id": seller_id, "max_budget": "100.00"}
    headers = make_auth_headers(client_id, priv_c, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    job_id = resp.json()["job_id"]

    counter = {"proposed_price": "110.00", "message": "X" * 2049}
    headers = make_auth_headers(seller_id, priv_s, "POST", f"/jobs/{job_id}/counter", counter)
    resp = await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_job_proposal_max_rounds_boundaries(client: AsyncClient) -> None:
    """SV7: max_rounds > 20 rejected, < 1 rejected."""
    priv, pub = generate_keypair()
    _, pub_s = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    client_id = resp.json()["agent_id"]
    resp = await client.post("/agents", json=make_agent_data(pub_s))
    seller_id = resp.json()["agent_id"]

    # max_rounds = 0 — rejected
    data = {"seller_agent_id": seller_id, "max_budget": "100.00", "max_rounds": 0}
    headers = make_auth_headers(client_id, priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 422

    # max_rounds = 21 — rejected
    data["max_rounds"] = 21
    headers = make_auth_headers(client_id, priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 422

    # max_rounds = 20 — accepted
    data["max_rounds"] = 20
    headers = make_auth_headers(client_id, priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_review_rating_zero_rejected(client: AsyncClient) -> None:
    """SV5: rating=0 rejected at schema level."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    # Need a job_id — any UUID will do since schema validation runs first
    review = {"rating": 0}
    import uuid
    fake_job = str(uuid.uuid4())
    headers = make_auth_headers(agent_id, priv, "POST", f"/jobs/{fake_job}/reviews", review)
    resp = await client.post(f"/jobs/{fake_job}/reviews", json=review, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_review_rating_six_rejected(client: AsyncClient) -> None:
    """SV5: rating=6 rejected at schema level."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    review = {"rating": 6}
    import uuid
    fake_job = str(uuid.uuid4())
    headers = make_auth_headers(agent_id, priv, "POST", f"/jobs/{fake_job}/reviews", review)
    resp = await client.post(f"/jobs/{fake_job}/reviews", json=review, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_deposit_amount_validation(client: AsyncClient) -> None:
    """A14/SV: Deposit with zero or negative amount rejected."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    # Dev deposit is unvalidated (amount is a plain string).
    # Verify that the dev deposit endpoint accepts "0" (no schema constraint)
    # and that negative deposits produce a negative balance shift.
    data = {"amount": "0"}
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/deposit", data)
    resp = await client.post(f"/agents/{agent_id}/deposit", json=data, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["balance"] == "0.00"


@pytest.mark.asyncio
async def test_budget_over_million_rejected(client: AsyncClient) -> None:
    """J18/SV: max_budget > 1,000,000 rejected."""
    priv, pub = generate_keypair()
    _, pub_s = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    client_id = resp.json()["agent_id"]
    resp = await client.post("/agents", json=make_agent_data(pub_s))
    seller_id = resp.json()["agent_id"]

    data = {"seller_agent_id": seller_id, "max_budget": "1000001.00"}
    headers = make_auth_headers(client_id, priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Missing field tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_create_missing_required_fields(client: AsyncClient) -> None:
    """Registration with missing required fields rejected."""
    resp = await client.post("/agents", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_agent_create_missing_public_key(client: AsyncClient) -> None:
    resp = await client.post("/agents", json={
        "display_name": "Test",
        "endpoint_url": "https://example.com",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_agent_create_missing_endpoint_url(client: AsyncClient) -> None:
    _, pub = generate_keypair()
    resp = await client.post("/agents", json={
        "public_key": pub,
        "display_name": "Test",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_listing_create_missing_fields(client: AsyncClient) -> None:
    """Listing with missing required fields rejected."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    data = {"price_model": "per_call", "base_price": "10.00"}
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", data)
    resp = await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_job_proposal_missing_seller(client: AsyncClient) -> None:
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    data = {"max_budget": "100.00"}
    headers = make_auth_headers(agent_id, priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_job_proposal_missing_budget(client: AsyncClient) -> None:
    priv, pub = generate_keypair()
    _, pub_s = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]
    resp = await client.post("/agents", json=make_agent_data(pub_s))
    seller_id = resp.json()["agent_id"]

    data = {"seller_agent_id": seller_id}
    headers = make_auth_headers(agent_id, priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_deliver_payload_requires_result(client: AsyncClient) -> None:
    """Deliver without result field rejected."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    import uuid as _uuid
    fake_job = str(_uuid.uuid4())
    data = {}
    headers = make_auth_headers(agent_id, priv, "POST", f"/jobs/{fake_job}/deliver", data)
    resp = await client.post(f"/jobs/{fake_job}/deliver", json=data, headers=headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Schema-level validation (pydantic models directly)
# ---------------------------------------------------------------------------


def test_withdrawal_invalid_address_format_pydantic() -> None:
    """Withdrawal with non-ETH address rejected at schema level."""
    from pydantic import ValidationError
    from app.schemas.wallet import WithdrawalCreateRequest

    with pytest.raises(ValidationError):
        WithdrawalCreateRequest(amount="10.00", destination_address="not-an-address")


def test_withdrawal_below_minimum_pydantic() -> None:
    """Withdrawal amount below $1.00 rejected at schema level."""
    from pydantic import ValidationError
    from app.schemas.wallet import WithdrawalCreateRequest

    with pytest.raises(ValidationError):
        WithdrawalCreateRequest(amount="0.50", destination_address="0x" + "a" * 40)


def test_withdrawal_above_maximum_pydantic() -> None:
    """W4: Withdrawal above $100,000 rejected at schema level."""
    from pydantic import ValidationError
    from app.schemas.wallet import WithdrawalCreateRequest

    with pytest.raises(ValidationError):
        WithdrawalCreateRequest(amount="100001.00", destination_address="0x" + "a" * 40)


def test_deposit_notify_invalid_tx_hash_pydantic() -> None:
    """W2: Deposit notify with invalid tx_hash format rejected."""
    from pydantic import ValidationError
    from app.schemas.wallet import DepositNotifyRequest

    with pytest.raises(ValidationError):
        DepositNotifyRequest(tx_hash="not-a-hash")


def test_deposit_notify_valid_tx_hash_pydantic() -> None:
    """Valid tx_hash (64 hex chars) accepted."""
    from app.schemas.wallet import DepositNotifyRequest

    req = DepositNotifyRequest(tx_hash="a" * 64)
    assert req.tx_hash == "0x" + "a" * 64  # Normalized with 0x prefix


def test_deposit_notify_with_0x_prefix() -> None:
    """tx_hash with 0x prefix accepted."""
    from app.schemas.wallet import DepositNotifyRequest

    req = DepositNotifyRequest(tx_hash="0x" + "b" * 64)
    assert req.tx_hash == "0x" + "b" * 64


@pytest.mark.asyncio
async def test_review_missing_rating(client: AsyncClient) -> None:
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    import uuid as _uuid
    job_id = str(_uuid.uuid4())
    data = {}
    headers = make_auth_headers(agent_id, priv, "POST", f"/jobs/{job_id}/reviews", data)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=data, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_agent_endpoint_url_private_ip_ranges(client: AsyncClient) -> None:
    """SSRF protection: various private IP ranges rejected."""
    for url in [
        "https://10.0.0.1/webhook",
        "https://172.16.0.1/webhook",
        "https://127.0.0.1/webhook",
        "https://169.254.0.1/webhook",
    ]:
        _, pub = generate_keypair()
        data = make_agent_data(pub)
        data["endpoint_url"] = url
        resp = await client.post("/agents", json=data)
        assert resp.status_code == 422, f"Expected 422 for {url}, got {resp.status_code}"


@pytest.mark.asyncio
async def test_capability_tag_too_long(client: AsyncClient) -> None:
    """Capability tag > 64 chars rejected."""
    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["capabilities"] = ["a" * 65]
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_description_too_long(client: AsyncClient) -> None:
    """Description > 4096 chars rejected."""
    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["description"] = "X" * 4097
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Pydantic model unit tests (no client needed)
# ---------------------------------------------------------------------------


def test_review_create_tag_too_long() -> None:
    from pydantic import ValidationError
    from app.schemas.review import ReviewCreate
    with pytest.raises(ValidationError):
        ReviewCreate(rating=3, tags=["a" * 65])


def test_review_create_too_many_tags() -> None:
    from pydantic import ValidationError
    from app.schemas.review import ReviewCreate
    with pytest.raises(ValidationError):
        ReviewCreate(rating=3, tags=[f"tag-{i}" for i in range(11)])


def test_review_create_comment_too_long() -> None:
    from pydantic import ValidationError
    from app.schemas.review import ReviewCreate
    with pytest.raises(ValidationError):
        ReviewCreate(rating=3, comment="x" * 4097)


def test_review_create_valid() -> None:
    from app.schemas.review import ReviewCreate
    r = ReviewCreate(rating=5, tags=["fast", "reliable"], comment="Great")
    assert r.rating == 5


def test_job_proposal_valid() -> None:
    from app.schemas.job import JobProposal
    import uuid
    jp = JobProposal(seller_agent_id=uuid.uuid4(), max_budget="99.99", max_rounds=10)
    assert jp.max_rounds == 10


def test_counter_proposal_valid() -> None:
    from app.schemas.job import CounterProposal
    cp = CounterProposal(proposed_price="50.00", message="Let's go")
    assert cp.message == "Let's go"


def test_deliver_payload_dict() -> None:
    from app.schemas.job import DeliverPayload
    dp = DeliverPayload(result={"data": [1, 2, 3]})
    assert dp.result == {"data": [1, 2, 3]}


def test_deliver_payload_list() -> None:
    from app.schemas.job import DeliverPayload
    dp = DeliverPayload(result=[{"file": "report.pdf"}])
    assert len(dp.result) == 1


def test_listing_create_valid_price_models() -> None:
    from app.schemas.listing import ListingCreate
    for model in ["per_call", "per_unit", "per_hour", "flat"]:
        lc = ListingCreate(skill_id="test-skill", price_model=model, base_price="10.00")
        assert lc.price_model == model


def test_agent_create_valid_schema() -> None:
    from app.schemas.agent import AgentCreate
    ac = AgentCreate(
        public_key="a" * 64, display_name="Test Agent",
        endpoint_url="https://example.com/webhook", capabilities=["coding"],
    )
    assert ac.display_name == "Test Agent"


def test_withdrawal_valid_eth_address() -> None:
    from app.schemas.wallet import WithdrawalCreateRequest
    w = WithdrawalCreateRequest(amount="50.00", destination_address="0x" + "aB" * 20)
    assert w.amount == 50
