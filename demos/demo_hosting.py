#!/usr/bin/env python3
"""Demo: Deploy a hello-world agent to Arcoa hosting.

Usage:
    # Ensure demo accounts exist first:
    ./run_staging.sh setup

    # Then run:
    python3 demo_hosting.py

Requires .env.staging and .env.demo to be present.
"""

import io
import os
import sys
import tarfile
import time

import requests
from nacl.signing import SigningKey

# ─── Config ───
BASE_URL = os.environ.get("AGENT_REGISTRY_URL", "https://api.staging.arcoa.ai")
ALICE_REGISTRATION_TOKEN = os.environ.get("ALICE_REGISTRATION_TOKEN")

# ─── Colors ───
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def sign_request(private_key_hex: str, method: str, path: str, body: bytes = b"") -> dict:
    """Create Ed25519-signed auth headers."""
    import hashlib
    import time as _time

    signing_key = SigningKey(bytes.fromhex(private_key_hex))
    public_key = signing_key.verify_key
    timestamp = str(int(_time.time()))

    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{method}\n{path}\n{timestamp}\n{body_hash}"
    signature = signing_key.sign(message.encode()).signature

    return {
        "X-Agent-Public-Key": public_key.encode().hex(),
        "X-Timestamp": timestamp,
        "X-Signature": signature.hex(),
    }


def create_hello_world_archive() -> bytes:
    """Create a tar.gz archive with a minimal hello-world agent."""

    # arcoa.yaml manifest
    manifest = """\
name: hello-world
runtime: python:3.13
entrypoint: handler.py
cpu: "0.25"
memory_mb: 256
skills:
  - id: hello
    description: "Says hello — a minimal test agent"
    base_price: "0.001"
env: {}
"""

    # handler.py — the simplest possible agent handler
    handler = """\
\"\"\"Hello World agent handler.\"\"\"

import logging

logger = logging.getLogger(__name__)


async def handle(requirements: dict) -> dict:
    \"\"\"Handle a job request.\"\"\"
    name = requirements.get("name", "World")
    greeting = f"Hello, {name}! 👋 I'm a hosted Arcoa agent."
    logger.info("Generated greeting for %s", name)
    return {
        "greeting": greeting,
        "status": "success",
    }
"""

    # Build tar.gz in memory
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in [("arcoa.yaml", manifest), ("handler.py", handler)]:
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    return buf.getvalue()


def main() -> None:
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  Arcoa Hosting Demo — Hello World Agent{RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")
    print(f"{DIM}API: {BASE_URL}{RESET}\n")

    if not ALICE_PRIVKEY:
        print(f"{RED}✗ ALICE_PRIVATE_KEY not set. Run ./run_staging.sh setup first.{RESET}")
        sys.exit(1)

    # ── Step 1: Register Alice (if needed) ──
    print(f"{YELLOW}Step 1: Register agent...{RESET}")

    signing_key = SigningKey(bytes.fromhex(ALICE_PRIVKEY))
    public_key_hex = signing_key.verify_key.encode().hex()

    reg_body = {
        "name": "Hello World Agent",
        "public_key": public_key_hex,
        "endpoint_url": None,
        "capabilities": ["hello"],
        "hosting_mode": "hosted",
    }
    if ALICE_REGISTRATION_TOKEN:
        reg_body["registration_token"] = ALICE_REGISTRATION_TOKEN

    resp = requests.post(f"{BASE_URL}/agents/register", json=reg_body, timeout=30)
    if resp.status_code == 201:
        agent_data = resp.json()
        agent_id = agent_data["agent_id"]
        print(f"{GREEN}  ✓ Registered: {agent_id}{RESET}")
    elif resp.status_code == 409:
        # Already registered — look up by public key
        headers = sign_request(ALICE_PRIVKEY, "GET", "/agents/me")
        resp = requests.get(f"{BASE_URL}/agents/me", headers=headers, timeout=30)
        resp.raise_for_status()
        agent_data = resp.json()
        agent_id = agent_data["agent_id"]
        print(f"{GREEN}  ✓ Already registered: {agent_id}{RESET}")
    else:
        print(f"{RED}  ✗ Registration failed: {resp.status_code} {resp.text}{RESET}")
        sys.exit(1)

    # ── Step 2: Create hello-world archive ──
    print(f"\n{YELLOW}Step 2: Create hello-world agent archive...{RESET}")
    archive = create_hello_world_archive()
    print(f"{GREEN}  ✓ Archive created ({len(archive)} bytes){RESET}")

    # ── Step 3: Deploy ──
    print(f"\n{YELLOW}Step 3: Deploy to Arcoa hosting...{RESET}")

    deploy_path = f"/agents/{agent_id}/hosting/deploy"
    headers = sign_request(ALICE_PRIVKEY, "POST", deploy_path, archive)

    resp = requests.post(
        f"{BASE_URL}{deploy_path}",
        headers=headers,
        files={"file": ("agent.tar.gz", archive, "application/gzip")},
        data={"runtime": "python:3.13", "region": "us-west1"},
        timeout=60,
    )

    if resp.status_code == 201:
        deploy_data = resp.json()
        print(f"{GREEN}  ✓ Deployment initiated{RESET}")
        print(f"{DIM}    Status: {deploy_data.get('status')}{RESET}")
        print(f"{DIM}    ID: {deploy_data.get('id')}{RESET}")
    else:
        print(f"{RED}  ✗ Deploy failed: {resp.status_code} {resp.text}{RESET}")
        sys.exit(1)

    # ── Step 4: Poll status ──
    print(f"\n{YELLOW}Step 4: Waiting for build & deploy...{RESET}")

    status_path = f"/agents/{agent_id}/hosting/deploy"
    for i in range(60):
        time.sleep(5)
        headers = sign_request(ALICE_PRIVKEY, "GET", status_path)
        resp = requests.get(f"{BASE_URL}{status_path}", headers=headers, timeout=30)

        if resp.status_code != 200:
            print(f"{RED}  ✗ Status check failed: {resp.status_code}{RESET}")
            continue

        status_data = resp.json()
        status = status_data.get("status")
        print(f"{DIM}  [{i*5}s] Status: {status}{RESET}")

        if status == "running":
            print(f"\n{GREEN}  ✓ Agent is RUNNING!{RESET}")
            container = status_data.get("container_id", "")
            print(f"{DIM}    Container: {container}{RESET}")
            break
        elif status == "errored":
            print(f"\n{RED}  ✗ Deployment ERRORED{RESET}")
            print(f"{RED}    Error: {status_data.get('error_message')}{RESET}")
            # Get logs
            logs_path = f"/agents/{agent_id}/hosting/logs"
            headers = sign_request(ALICE_PRIVKEY, "GET", logs_path)
            resp = requests.get(f"{BASE_URL}{logs_path}", headers=headers, timeout=30)
            if resp.status_code == 200:
                print(f"\n{DIM}Build logs:{RESET}")
                print(resp.json().get("logs", "(no logs)"))
            sys.exit(1)
    else:
        print(f"\n{RED}  ✗ Timed out waiting for deployment{RESET}")
        sys.exit(1)

    # ── Step 5: Check logs ──
    print(f"\n{YELLOW}Step 5: Fetch agent logs...{RESET}")

    logs_path = f"/agents/{agent_id}/hosting/logs"
    headers = sign_request(ALICE_PRIVKEY, "GET", logs_path)
    resp = requests.get(f"{BASE_URL}{logs_path}", headers=headers, timeout=30)

    if resp.status_code == 200:
        logs = resp.json().get("logs", "")
        if logs:
            print(f"{CYAN}{logs[:500]}{RESET}")
        else:
            print(f"{DIM}  (no logs yet){RESET}")
    else:
        print(f"{RED}  ✗ Logs failed: {resp.status_code}{RESET}")

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{GREEN}{BOLD}  ✓ Hosting demo complete!{RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")


if __name__ == "__main__":
    main()
