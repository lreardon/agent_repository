#!/usr/bin/env python3
"""
Load test setup: register test agents and pre-generate signed request data.

Creates N agents, deposits funds, and writes a JSON file with agent credentials
and pre-signed headers that k6 can consume.

Usage:
  python3 load-tests/setup.py [base_url] [num_agents]
  
  base_url defaults to http://localhost:8080
  num_agents defaults to 20
"""

import hashlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime

import httpx
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "LOAD_TEST_URL", "http://localhost:8080"
)
NUM_AGENTS = int(sys.argv[2]) if len(sys.argv) > 2 else 20

# Amount to deposit per agent (enough for many transactions)
DEPOSIT_AMOUNT = "100.00"


def sign_request(private_key_hex: str, timestamp: str, method: str, path: str, body: bytes) -> str:
    """Sign a request with Ed25519."""
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{timestamp}\n{method}\n{path}\n{body_hash}".encode()
    signing_key = SigningKey(private_key_hex.encode(), encoder=HexEncoder)
    signed = signing_key.sign(message, encoder=HexEncoder)
    return signed.signature.decode()


def make_headers(agent_id: str, private_key: str, method: str, path: str, body: bytes = b"") -> dict:
    """Build authenticated request headers."""
    timestamp = datetime.now(UTC).isoformat()
    nonce = uuid.uuid4().hex
    signature = sign_request(private_key, timestamp, method, path, body)
    return {
        "Authorization": f"AgentSig {agent_id}:{signature}",
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "Content-Type": "application/json",
    }


def _get_registration_token(client: httpx.Client, email: str) -> str:
    """Get a registration token via signup flow.

    For local/no-email-verification: skips signup, no token needed.
    For staging: signs up, then queries the DB for the verification token
    (requires Cloud SQL Proxy or LOAD_TEST_DB_URL env var).
    """
    # Try signup
    resp = client.post(
        f"{BASE_URL}/v1/auth/signup",
        content=json.dumps({"email": email}).encode(),
        headers={"Content-Type": "application/json"},
    )

    if resp.status_code == 429:
        print(f"    Rate limited on signup for {email}, waiting...")
        import time
        time.sleep(12)
        resp = client.post(
            f"{BASE_URL}/v1/auth/signup",
            content=json.dumps({"email": email}).encode(),
            headers={"Content-Type": "application/json"},
        )

    if resp.status_code not in (200, 201, 409):
        print(f"    Signup failed for {email}: {resp.status_code} {resp.text[:200]}")

    # Get the verification token from the DB
    db_url = os.environ.get("LOAD_TEST_DB_URL")
    if not db_url:
        raise RuntimeError(
            "LOAD_TEST_DB_URL required for staging setup. "
            "Start Cloud SQL Proxy and set e.g. "
            "LOAD_TEST_DB_URL='postgresql://api_user@127.0.0.1:5433/agent_registry'"
        )

    import psycopg2
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    # Get the latest unused verification token for this email
    cur.execute(
        "SELECT token FROM email_verifications "
        "WHERE email = %s AND used = false "
        "ORDER BY created_at DESC LIMIT 1",
        (email,),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"No verification token found for {email}")

    verify_token = row[0]

    # Hit the verify endpoint to get a registration token
    verify_resp = client.get(f"{BASE_URL}/v1/auth/verify-email?token={verify_token}")
    if verify_resp.status_code != 200:
        raise RuntimeError(f"Verify failed: {verify_resp.status_code} {verify_resp.text[:200]}")

    # Extract registration token from DB (verify endpoint sets it)
    cur.execute(
        "SELECT registration_token FROM email_verifications "
        "WHERE token = %s",
        (verify_token,),
    )
    row = cur.fetchone()
    conn.close()

    if not row or not row[0]:
        raise RuntimeError(f"Registration token not found after verification for {email}")

    return row[0]


def register_agent(client: httpx.Client, name: str, registration_token: str | None = None) -> dict:
    """Register an agent and return its credentials."""
    signing_key = SigningKey.generate()
    private_hex = signing_key.encode(encoder=HexEncoder).decode()
    public_hex = signing_key.verify_key.encode(encoder=HexEncoder).decode()

    payload = {
        "public_key": public_hex,
        "display_name": f"loadtest-{name}",
        "description": f"Load test agent {name}",
        "capabilities": ["load-test"],
        "hosting_mode": "client_only",
    }
    if registration_token:
        payload["registration_token"] = registration_token

    body = json.dumps(payload).encode()

    resp = client.post(
        f"{BASE_URL}/v1/agents",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "agent_id": data["agent_id"],
        "private_key": private_hex,
        "public_key": public_hex,
        "display_name": data["display_name"],
    }


def deposit_funds(client: httpx.Client, agent: dict, amount: str):
    """Admin-deposit funds for a test agent."""
    body = json.dumps({"amount": amount}).encode()
    path = f"/v1/agents/{agent['agent_id']}/deposit"
    headers = make_headers(agent["agent_id"], agent["private_key"], "POST", path, body)
    resp = client.post(f"{BASE_URL}{path}", content=body, headers=headers)
    resp.raise_for_status()
    print(f"  Deposited {amount} → {agent['display_name']} (balance: {resp.json()['balance']})")


def create_listing(client: httpx.Client, agent: dict) -> str:
    """Create a listing for a seller agent."""
    body = json.dumps({
        "skill_id": "load-test",
        "description": "Load test service",
        "price_model": "flat",
        "base_price": "1.00",
        "currency": "credits",
    }).encode()
    path = f"/v1/agents/{agent['agent_id']}/listings"
    headers = make_headers(agent["agent_id"], agent["private_key"], "POST", path, body)
    resp = client.post(f"{BASE_URL}{path}", content=body, headers=headers)
    if resp.status_code != 201:
        print(f"  ERROR creating listing: {resp.status_code} {resp.text[:200]}")
    resp.raise_for_status()
    listing_id = resp.json()["listing_id"]
    print(f"  Listing {listing_id} created by {agent['display_name']}")
    return listing_id


def main():
    print(f"Setting up load test: {NUM_AGENTS} agents against {BASE_URL}")
    client = httpx.Client(timeout=30)

    agents = []
    listings = []

    # Check if email verification is required
    needs_email = False
    test_resp = client.post(
        f"{BASE_URL}/v1/agents",
        content=json.dumps({"public_key": "a" * 64, "display_name": "probe", "hosting_mode": "client_only"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    if test_resp.status_code == 422 and "registration token" in test_resp.text.lower():
        needs_email = True
        print("  Email verification required — using signup flow")

    # Register agents
    print("\n--- Registering agents ---")
    for i in range(NUM_AGENTS):
        token = None
        if needs_email:
            email = f"loadtest-{i:03d}@loadtest.arcoa.ai"
            print(f"  Signing up {email}...")
            import time
            if i > 0 and i % 5 == 0:
                print("  (pausing to avoid rate limits...)")
                time.sleep(5)
            token = _get_registration_token(client, email)
        agent = register_agent(client, f"agent-{i:03d}", registration_token=token)
        print(f"  Registered {agent['display_name']} ({agent['agent_id'][:8]}...)")
        agents.append(agent)

    # Deposit funds
    print("\n--- Depositing funds ---")
    for agent in agents:
        deposit_funds(client, agent, DEPOSIT_AMOUNT)

    # Create listings (first half are sellers)
    print("\n--- Creating listings ---")
    sellers = agents[:NUM_AGENTS // 2]
    for seller in sellers:
        listing_id = create_listing(client, seller)
        listings.append({"listing_id": listing_id, "seller_id": seller["agent_id"]})

    # Write output
    output = {
        "base_url": BASE_URL,
        "agents": agents,
        "sellers": [a["agent_id"] for a in sellers],
        "buyers": [a["agent_id"] for a in agents[NUM_AGENTS // 2:]],
        "listings": listings,
    }

    out_path = os.path.join(os.path.dirname(__file__), "test-data.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n✅ Setup complete. {len(agents)} agents, {len(listings)} listings.")
    print(f"   Data written to {out_path}")


if __name__ == "__main__":
    main()
