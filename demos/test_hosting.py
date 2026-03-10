#!/usr/bin/env python3
"""Test: Deploy arcoa-agents/hello-agent to staging hosting infrastructure.

Registers an agent, tars the actual hello-agent directory, uploads it,
and polls until it's running (or errors).
"""

import hashlib
import io
import os
import secrets
import sys
import tarfile
import time

import requests
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

# ─── Config ───
BASE_URL = os.environ.get("AGENT_REGISTRY_URL", "https://api.staging.arcoa.ai")
REGISTRATION_TOKEN = os.environ.get("ALICE_REGISTRATION_TOKEN", "")
HELLO_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "arcoa-agents", "hello-agent")

# ─── Colors ───
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def sign_headers(signing_key: SigningKey, agent_id: str, method: str, path: str, body: bytes = b"") -> dict:
    """Create auth headers matching the AgentSig scheme."""
    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).isoformat()
    nonce = secrets.token_hex(16)

    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{timestamp}\n{method}\n{path}\n{body_hash}"
    signature = signing_key.sign(message.encode(), encoder=HexEncoder).signature.decode()

    headers = {
        "Authorization": f"AgentSig {agent_id}:{signature}",
        "X-Timestamp": timestamp,
    }
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        headers["X-Nonce"] = nonce
    return headers


def create_archive_from_dir(directory: str) -> bytes:
    """Tar.gz the hello-agent directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for fname in os.listdir(directory):
            fpath = os.path.join(directory, fname)
            if os.path.isfile(fpath):
                tar.add(fpath, arcname=fname)
    return buf.getvalue()


def signed_multipart_post(signing_key, agent_id, url, path, files, data):
    """Build a multipart request, sign with empty body (server skips body hash for multipart)."""
    headers = sign_headers(signing_key, agent_id, "POST", path, body=b"")
    return requests.post(url, headers=headers, files=files, data=data, timeout=60)


def main() -> None:
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  Hosting Test — Deploy hello-agent to Staging GKE{RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")
    print(f"{DIM}API: {BASE_URL}{RESET}")
    print(f"{DIM}Agent dir: {os.path.abspath(HELLO_AGENT_DIR)}{RESET}\n")

    if not os.path.isdir(HELLO_AGENT_DIR):
        print(f"{RED}✗ hello-agent directory not found: {HELLO_AGENT_DIR}{RESET}")
        sys.exit(1)

    # ── Step 1: Generate keypair and register ──
    print(f"{YELLOW}Step 1: Generate keypair & register agent...{RESET}")

    signing_key = SigningKey.generate()
    private_hex = signing_key.encode(encoder=HexEncoder).decode()
    public_hex = signing_key.verify_key.encode(encoder=HexEncoder).decode()

    reg_body = {
        "display_name": "Hello Agent (hosting test)",
        "public_key": public_hex,
        "capabilities": ["hello-world"],
        "hosting_mode": "hosted",
    }
    if REGISTRATION_TOKEN:
        reg_body["registration_token"] = REGISTRATION_TOKEN

    resp = requests.post(f"{BASE_URL}/agents", json=reg_body, timeout=30)
    if resp.status_code == 201:
        agent_data = resp.json()
        agent_id = agent_data["agent_id"]
        print(f"{GREEN}  ✓ Registered: {agent_id}{RESET}")
    else:
        print(f"{RED}  ✗ Registration failed: {resp.status_code} {resp.text}{RESET}")
        sys.exit(1)

    # ── Step 2: Create archive from actual hello-agent dir ──
    print(f"\n{YELLOW}Step 2: Create tar.gz from arcoa-agents/hello-agent...{RESET}")
    archive = create_archive_from_dir(HELLO_AGENT_DIR)
    print(f"{GREEN}  ✓ Archive created ({len(archive)} bytes){RESET}")

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for m in tar.getmembers():
            print(f"{DIM}    {m.name} ({m.size} bytes){RESET}")

    # ── Step 3: Deploy ──
    print(f"\n{YELLOW}Step 3: Upload & deploy to staging...{RESET}")

    deploy_path = f"/agents/{agent_id}/hosting/deploy"

    resp = signed_multipart_post(
        signing_key, agent_id,
        f"{BASE_URL}{deploy_path}", deploy_path,
        files={"file": ("agent.tar.gz", archive, "application/gzip")},
        data={"runtime": "python:3.13", "region": "us-west1"},
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
        headers = sign_headers(signing_key, agent_id, "GET", status_path)
        resp = requests.get(f"{BASE_URL}{status_path}", headers=headers, timeout=30)

        if resp.status_code != 200:
            print(f"{RED}  ✗ Status check failed: {resp.status_code} {resp.text}{RESET}")
            continue

        status_data = resp.json()
        status = status_data.get("status")
        print(f"{DIM}  [{i*5+5}s] Status: {status}{RESET}")

        if status == "running":
            print(f"\n{GREEN}  ✓ Agent is RUNNING!{RESET}")
            container = status_data.get("container_id", "")
            print(f"{DIM}    Container/Deployment: {container}{RESET}")
            break
        elif status == "errored":
            print(f"\n{RED}  ✗ Deployment ERRORED{RESET}")
            print(f"{RED}    Error: {status_data.get('error_message')}{RESET}")
            logs_path = f"/agents/{agent_id}/hosting/logs"
            headers = sign_headers(signing_key, agent_id, "GET", logs_path)
            resp = requests.get(f"{BASE_URL}{logs_path}", headers=headers, timeout=30)
            if resp.status_code == 200:
                logs = resp.json().get("logs", "(no logs)")
                print(f"\n{DIM}Build logs:{RESET}")
                print(f"{CYAN}{logs}{RESET}")
            sys.exit(1)
    else:
        print(f"\n{RED}  ✗ Timed out waiting for deployment (300s){RESET}")
        logs_path = f"/agents/{agent_id}/hosting/logs"
        headers = sign_headers(signing_key, agent_id, "GET", logs_path)
        resp = requests.get(f"{BASE_URL}{logs_path}", headers=headers, timeout=30)
        if resp.status_code == 200:
            logs = resp.json().get("logs", "(no logs)")
            print(f"\n{DIM}Build logs so far:{RESET}")
            print(f"{CYAN}{logs}{RESET}")
        sys.exit(1)

    # ── Step 5: Check logs ──
    print(f"\n{YELLOW}Step 5: Fetch agent logs...{RESET}")

    logs_path = f"/agents/{agent_id}/hosting/logs"
    headers = sign_headers(signing_key, agent_id, "GET", logs_path)
    resp = requests.get(f"{BASE_URL}{logs_path}", headers=headers, timeout=30)

    if resp.status_code == 200:
        logs = resp.json().get("logs", "")
        if logs:
            print(f"{CYAN}{logs[:1000]}{RESET}")
        else:
            print(f"{DIM}  (no logs yet){RESET}")
    else:
        print(f"{RED}  ✗ Logs failed: {resp.status_code}{RESET}")

    # ── Step 6: Undeploy / cleanup ──
    print(f"\n{YELLOW}Step 6: Undeploy (cleanup)...{RESET}")
    undeploy_path = f"/agents/{agent_id}/hosting/deploy"
    headers = sign_headers(signing_key, agent_id, "DELETE", undeploy_path)
    resp = requests.delete(f"{BASE_URL}{undeploy_path}", headers=headers, timeout=30)
    if resp.status_code == 200:
        print(f"{GREEN}  ✓ Undeployed{RESET}")
    else:
        print(f"{RED}  ✗ Undeploy failed: {resp.status_code} {resp.text}{RESET}")

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{GREEN}{BOLD}  ✓ Hosting test complete!{RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")


if __name__ == "__main__":
    main()
