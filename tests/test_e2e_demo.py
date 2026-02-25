"""End-to-end demo: Two agents interact through the full marketplace lifecycle.

Agent A: PDF Extraction seller
Agent B: Data pipeline client

Flow:
1. Agent A registers, creates listing for pdf_parse skill
2. Agent B registers, discovers Agent A via /discover
3. Agent B proposes a job with acceptance criteria
4. Agent A counters with higher price
5. Agent B accepts, funds escrow
6. Agent A starts work, delivers results
7. Platform verifies (acceptance tests pass)
8. Escrow released to Agent A (minus 2.5% fee)
9. Both agents review each other
10. Verify reputation scores updated
"""

import json

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair
from tests.conftest import make_auth_headers


@pytest.mark.asyncio
async def test_full_e2e_demo(client: AsyncClient) -> None:
    """Complete marketplace lifecycle with two agents."""

    # ── 1. Agent A registers (seller) ──
    priv_a, pub_a = generate_keypair()
    resp = await client.post("/agents", json={
        "public_key": pub_a,
        "display_name": "PDF Extraction Agent",
        "description": "Extracts structured data from PDF documents",
        "endpoint_url": "https://agent-a.example.com",
        "capabilities": ["pdf", "extraction", "structured-data"],
    })
    assert resp.status_code == 201
    agent_a = resp.json()
    agent_a_id = agent_a["agent_id"]
    print(f"\n✅ Agent A registered: {agent_a_id}")

    # ── 2. Agent A creates a listing ──
    listing_data = {
        "skill_id": "pdf-parse",
        "description": "Extract structured JSON from PDF documents. $0.05/page.",
        "price_model": "per_unit",
        "base_price": "0.05",
        "sla": {"max_latency_seconds": 3600, "uptime_pct": 99.5},
    }
    body = listing_data
    headers = make_auth_headers(agent_a_id, priv_a, "POST", f"/agents/{agent_a_id}/listings", body)
    resp = await client.post(f"/agents/{agent_a_id}/listings", json=listing_data, headers=headers)
    assert resp.status_code == 201
    listing = resp.json()
    listing_id = listing["listing_id"]
    print(f"✅ Agent A created listing: {listing_id} (pdf-parse @ $0.05/unit)")

    # ── 3. Agent B registers (client) ──
    priv_b, pub_b = generate_keypair()
    resp = await client.post("/agents", json={
        "public_key": pub_b,
        "display_name": "Data Pipeline Agent",
        "description": "Builds data pipelines from various sources",
        "endpoint_url": "https://agent-b.example.com",
        "capabilities": ["data-pipeline", "etl"],
    })
    assert resp.status_code == 201
    agent_b = resp.json()
    agent_b_id = agent_b["agent_id"]
    print(f"✅ Agent B registered: {agent_b_id}")

    # ── 4. Agent B deposits credits ──
    deposit = {"amount": "500.00"}
    body = deposit
    headers = make_auth_headers(agent_b_id, priv_b, "POST", f"/agents/{agent_b_id}/deposit", body)
    resp = await client.post(f"/agents/{agent_b_id}/deposit", json=deposit, headers=headers)
    assert resp.status_code == 200
    print(f"✅ Agent B deposited 500.00 credits")

    # ── 5. Agent B discovers Agent A ──
    resp = await client.get("/discover?skill_id=pdf")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) >= 1
    found = next(r for r in results if r["seller_agent_id"] == agent_a_id)
    assert found["skill_id"] == "pdf-parse"
    print(f"✅ Agent B discovered Agent A via /discover?skill_id=pdf")

    # ── 6. Agent B proposes a job ──
    job_data = {
        "seller_agent_id": agent_a_id,
        "listing_id": listing_id,
        "max_budget": "25.00",
        "requirements": {
            "input_format": "pdf",
            "volume": 500,
            "fields": ["owner_name", "property_address", "units"],
        },
        "acceptance_criteria": {
            "version": "1.0",
            "tests": [
                {
                    "test_id": "output_schema",
                    "type": "json_schema",
                    "params": {
                        "schema": {
                            "type": "object",
                            "required": ["records"],
                            "properties": {
                                "records": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": ["owner_name", "property_address"],
                                        "properties": {
                                            "owner_name": {"type": "string"},
                                            "property_address": {"type": "string"},
                                            "units": {"type": "integer"},
                                        },
                                    },
                                }
                            },
                        }
                    },
                },
                {
                    "test_id": "min_records",
                    "type": "count_gte",
                    "params": {"path": "$.records", "min_count": 400},
                },
                {
                    "test_id": "no_nulls",
                    "type": "assertion",
                    "params": {
                        "expression": "all(r['owner_name'] is not None for r in output['records'])"
                    },
                },
            ],
            "pass_threshold": "all",
        },
        "delivery_deadline": "2026-02-28T00:00:00Z",
        "max_rounds": 5,
    }
    body = job_data
    headers = make_auth_headers(agent_b_id, priv_b, "POST", "/jobs", body)
    resp = await client.post("/jobs", json=job_data, headers=headers)
    assert resp.status_code == 201
    job = resp.json()
    job_id = job["job_id"]
    assert job["status"] == "proposed"
    print(f"✅ Agent B proposed job: {job_id} (budget: $25.00, 3 acceptance tests)")

    # ── 7. Agent A counters with higher price ──
    counter = {
        "proposed_price": "30.00",
        "counter_terms": {"delivery_deadline": "2026-02-27T00:00:00Z"},
        "message": "500 pages is a lot — $0.06/page, delivered a day early.",
    }
    body = counter
    headers = make_auth_headers(agent_a_id, priv_a, "POST", f"/jobs/{job_id}/counter", body)
    resp = await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "negotiating"
    assert resp.json()["agreed_price"] == "30.00"
    print(f"✅ Agent A countered: $30.00, delivery by Feb 27")

    # ── 8. Agent B accepts ──
    headers = make_auth_headers(agent_b_id, priv_b, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "agreed"
    print(f"✅ Agent B accepted terms (agreed price: $30.00)")

    # ── 9. Agent B funds escrow ──
    headers = make_auth_headers(agent_b_id, priv_b, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "funded"
    print(f"✅ Agent B funded escrow ($30.00)")

    # Verify balance deducted
    headers = make_auth_headers(agent_b_id, priv_b, "GET", f"/agents/{agent_b_id}/balance")
    resp = await client.get(f"/agents/{agent_b_id}/balance", headers=headers)
    assert resp.json()["balance"] == "470.00"

    # ── 10. Agent A starts work ──
    headers = make_auth_headers(agent_a_id, priv_a, "POST", f"/jobs/{job_id}/start", b"")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "in_progress"
    print(f"✅ Agent A started work")

    # ── 11. Agent A delivers results ──
    records = [
        {"owner_name": f"Owner {i}", "property_address": f"{100+i} Main St", "units": (i % 4) + 1}
        for i in range(450)
    ]
    deliverable = {"result": {"records": records}}
    body = deliverable
    headers = make_auth_headers(agent_a_id, priv_a, "POST", f"/jobs/{job_id}/deliver", body)
    resp = await client.post(f"/jobs/{job_id}/deliver", json=deliverable, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "delivered"
    print(f"✅ Agent A delivered: 450 records")

    # ── 12. Platform verifies (runs acceptance tests) ──
    headers = make_auth_headers(agent_b_id, priv_b, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 200
    verify_body = resp.json()
    assert verify_body["job"]["status"] == "completed"
    assert verify_body["verification"]["passed"] is True
    print(f"✅ Acceptance tests passed ({verify_body['verification']['summary']}) → job completed, escrow released")

    # ── 13. Verify escrow payout ──
    # Seller: $30.00 - 2.5% fee = $29.25
    headers = make_auth_headers(agent_a_id, priv_a, "GET", f"/agents/{agent_a_id}/balance")
    resp = await client.get(f"/agents/{agent_a_id}/balance", headers=headers)
    assert resp.json()["balance"] == "29.25"
    print(f"✅ Agent A received $29.25 (30.00 - 2.5% platform fee)")

    # Client: $500 - $30 = $470 (unchanged)
    headers = make_auth_headers(agent_b_id, priv_b, "GET", f"/agents/{agent_b_id}/balance")
    resp = await client.get(f"/agents/{agent_b_id}/balance", headers=headers)
    assert resp.json()["balance"] == "470.00"
    print(f"✅ Agent B balance: $470.00")

    # ── 14. Both agents review each other ──
    # Client reviews seller
    review = {"rating": 5, "tags": ["fast", "reliable", "accurate"], "comment": "Excellent extraction quality, delivered early."}
    body = review
    headers = make_auth_headers(agent_b_id, priv_b, "POST", f"/jobs/{job_id}/reviews", body)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=review, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["role"] == "client_reviewing_seller"
    print(f"✅ Agent B reviewed Agent A: ⭐⭐⭐⭐⭐")

    # Seller reviews client
    review = {"rating": 4, "tags": ["clear-spec", "good-payer"], "comment": "Clear requirements, funded quickly."}
    body = review
    headers = make_auth_headers(agent_a_id, priv_a, "POST", f"/jobs/{job_id}/reviews", body)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=review, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["role"] == "seller_reviewing_client"
    print(f"✅ Agent A reviewed Agent B: ⭐⭐⭐⭐")

    # ── 15. Verify reputation endpoint ──
    resp = await client.get(f"/agents/{agent_a_id}/reputation")
    assert resp.status_code == 200
    rep = resp.json()
    assert rep["total_reviews_as_seller"] == 1
    assert rep["reputation_seller_display"] == "New"  # <3 reviews
    assert "fast" in rep["top_tags"]
    print(f"✅ Agent A reputation: {rep['reputation_seller_display']} (1 review, tags: {rep['top_tags']})")

    # ── 16. Verify security headers ──
    resp = await client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert "Strict-Transport-Security" in resp.headers
    print(f"✅ Security headers present (HSTS, nosniff, DENY)")

    # ── 17. Verify job history is complete ──
    headers = make_auth_headers(agent_b_id, priv_b, "GET", f"/jobs/{job_id}")
    resp = await client.get(f"/jobs/{job_id}", headers=headers)
    job_final = resp.json()
    assert job_final["status"] == "completed"
    assert len(job_final["negotiation_log"]) == 3  # propose, counter, accept
    assert job_final["agreed_price"] == "30.00"
    print(f"✅ Job history: {len(job_final['negotiation_log'])} negotiation entries, final price $30.00")

    print(f"\n🎉 E2E DEMO COMPLETE — Full agent-to-agent marketplace lifecycle verified")
