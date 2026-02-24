#!/usr/bin/env python3
"""
Live E2E Demo: Two agents interact through the Agent Registry & Marketplace.

Agent Alice: PDF Extraction seller (the contractor)
Agent Bob:   Data Pipeline agent (the client)

Showcases:
  1. Registration & discovery
  2. USDC deposit address & on-ramp (Base L2)
  3. Multi-round negotiation
  4. Escrow funding
  5. Work execution & delivery
  6. Script-based acceptance criteria (code as verification)
  7. Sandboxed neutral-arena verification
  8. Automatic escrow settlement
  9. USDC withdrawal & off-ramp
 10. Mutual reputation reviews

Run:
  1. Start the API:  uvicorn app.main:app --port 8080
  2. Run this demo:  python scripts/demo.py [base_url]
"""

import base64
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from demo_wallet import deposit_usdc_or_fallback, has_wallet_config

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"

# ─── Colors ───

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"
RESET = "\033[0m"


def banner(text: str) -> None:
    print(f"\n{'═' * 64}")
    print(f"  {BOLD}{text}{RESET}")
    print(f"{'═' * 64}")


def step(num: int, text: str) -> None:
    print(f"\n{BOLD}{CYAN}Step {num:2d}{RESET} │ {text}")


def agent_says(name: str, color: str, msg: str) -> None:
    print(f"         {color}{BOLD}{name}{RESET}: {msg}")


def platform_says(msg: str) -> None:
    print(f"         {MAGENTA}⚙ Platform{RESET}: {msg}")


def show_json(data: dict, keys: list[str] | None = None, indent: int = 9) -> None:
    if keys:
        filtered = {k: data[k] for k in keys if k in data}
    else:
        filtered = data
    prefix = " " * indent
    formatted = json.dumps(filtered, indent=2, default=str)
    for line in formatted.split("\n"):
        print(f"{prefix}{DIM}{line}{RESET}")


def code_block(title: str, code: str) -> None:
    print(f"         {DIM}┌─ {title} ─{'─' * max(0, 44 - len(title))}┐{RESET}")
    for line in code.strip().split("\n"):
        print(f"         {DIM}│ {line}{RESET}")
    print(f"         {DIM}└{'─' * 50}┘{RESET}")


def fail(msg: str) -> None:
    print(f"\n{RED}{BOLD}✖ FAILED: {msg}{RESET}")
    sys.exit(1)


def expect(resp: httpx.Response, status: int, context: str) -> dict:
    if resp.status_code != status:
        fail(f"{context}: expected {status}, got {resp.status_code} — {resp.text}")
    return resp.json()


# ─── Ed25519 Agent Client ───

class AgentClient:
    """Simulated autonomous agent with Ed25519 request signing."""

    def __init__(self, name: str, color: str) -> None:
        self.name = name
        self.color = color
        self.signing_key = SigningKey.generate()
        self.private_hex = self.signing_key.encode(encoder=HexEncoder).decode()
        self.public_hex = self.signing_key.verify_key.encode(encoder=HexEncoder).decode()
        self.agent_id: str | None = None
        self.http = httpx.Client(base_url=BASE_URL, timeout=60.0)

    def _sign(self, method: str, path: str, body: bytes) -> dict[str, str]:
        timestamp = datetime.now(UTC).isoformat()
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{timestamp}\n{method}\n{path}\n{body_hash}".encode()
        signed = self.signing_key.sign(message, encoder=HexEncoder)
        signature = signed.signature.decode()
        return {
            "Authorization": f"AgentSig {self.agent_id}:{signature}",
            "X-Timestamp": timestamp,
            "X-Nonce": hashlib.sha256(f"{time.time()}".encode()).hexdigest()[:32],
        }

    def post(self, path: str, data: dict | None = None, signed: bool = True) -> httpx.Response:
        body = json.dumps(data).encode() if data else b""
        headers = {"Content-Type": "application/json"}
        if signed and self.agent_id:
            headers.update(self._sign("POST", path, body))
        return self.http.post(path, content=body, headers=headers)

    def get(self, path: str, signed: bool = False) -> httpx.Response:
        headers = {}
        if signed and self.agent_id:
            headers.update(self._sign("GET", path, b""))
        return self.http.get(path, headers=headers)


# ─── Verification Script ───

VERIFY_SCRIPT = """\
#!/usr/bin/env python3
\"\"\"
Acceptance verification script for property data extraction job.

This script runs in an isolated Docker container with:
  - No network access
  - Read-only filesystem
  - 256MB memory limit
  - 60 second timeout
  - Non-root user

It reads the deliverable from /input/result.json and verifies:
  1. Output is valid JSON with a 'records' array
  2. At least 400 records extracted (80% yield from 500 pages)
  3. Every record has required fields: owner_name, property_address
  4. No null or empty values in required fields
  5. All unit counts are positive integers
  6. No duplicate property addresses

Exit code 0 = all checks pass = escrow released to contractor.
Exit code 1 = any check fails = escrow refunded to client.
\"\"\"

import json
import sys

def main():
    # Load the deliverable
    with open('/input/result.json') as f:
        data = json.load(f)

    errors = []

    # Check 1: Structure
    if not isinstance(data, dict) or 'records' not in data:
        print("FAIL: Output must be a dict with 'records' key", file=sys.stderr)
        sys.exit(1)

    records = data['records']
    if not isinstance(records, list):
        print("FAIL: 'records' must be an array", file=sys.stderr)
        sys.exit(1)

    print(f"CHECK 1 ✓ Valid structure: {len(records)} records")

    # Check 2: Minimum count
    if len(records) < 400:
        errors.append(f"Only {len(records)} records (need ≥400 for 80% yield)")
    else:
        print(f"CHECK 2 ✓ Record count: {len(records)} ≥ 400")

    # Check 3: Required fields present
    missing_fields = 0
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            errors.append(f"Record {i} is not a dict")
            continue
        for field in ('owner_name', 'property_address'):
            if field not in rec:
                missing_fields += 1
    if missing_fields > 0:
        errors.append(f"{missing_fields} missing required fields")
    else:
        print(f"CHECK 3 ✓ All records have required fields")

    # Check 4: No null/empty values
    empty_values = 0
    for rec in records:
        if not isinstance(rec, dict):
            continue
        for field in ('owner_name', 'property_address'):
            val = rec.get(field)
            if val is None or (isinstance(val, str) and val.strip() == ''):
                empty_values += 1
    if empty_values > 0:
        errors.append(f"{empty_values} null/empty values in required fields")
    else:
        print(f"CHECK 4 ✓ No null/empty values in required fields")

    # Check 5: Valid unit counts
    bad_units = 0
    for rec in records:
        if not isinstance(rec, dict):
            continue
        units = rec.get('units')
        if units is not None:
            if not isinstance(units, int) or units < 1:
                bad_units += 1
    if bad_units > 0:
        errors.append(f"{bad_units} records with invalid unit counts")
    else:
        print(f"CHECK 5 ✓ All unit counts are positive integers")

    # Check 6: No duplicate addresses
    addresses = [r.get('property_address') for r in records if isinstance(r, dict)]
    dupes = len(addresses) - len(set(addresses))
    if dupes > 0:
        errors.append(f"{dupes} duplicate property addresses")
    else:
        print(f"CHECK 6 ✓ All property addresses are unique")

    # Verdict
    if errors:
        print(f"\\nFAILED — {len(errors)} issue(s):", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"\\n✓ ALL CHECKS PASSED — {len(records)} records verified")
        sys.exit(0)

if __name__ == '__main__':
    main()
"""


def main() -> None:
    # ─── Preflight ───
    banner("Agent Registry & Marketplace — Live Demo")
    print(f"\n{DIM}Checking API at {BASE_URL}...{RESET}")

    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=3)
        if r.status_code != 200:
            fail(f"API returned {r.status_code}")
    except httpx.ConnectError:
        fail(f"Cannot connect to {BASE_URL}. Start the API first:\n  uvicorn app.main:app --port 8080")

    print(f"{GREEN}✓ API is running{RESET}")

    alice = AgentClient("Alice", BLUE)
    bob = AgentClient("Bob", YELLOW)

    # ═══════════════════════════════════════════════════════════════
    banner("Act 1: Identity — Agents Register on the Platform")
    # ═══════════════════════════════════════════════════════════════

    step(1, "Alice registers as a PDF extraction specialist")
    data = expect(alice.post("/agents", {
        "public_key": alice.public_hex,
        "display_name": "Alice — PDF Extraction Agent",
        "description": "Extracts structured data from PDF documents with 99.5% accuracy. "
                       "Handles tables, forms, scanned text via OCR.",
        "endpoint_url": "https://alice.agents.example.com",
        "capabilities": ["pdf", "extraction", "structured-data", "ocr"],
    }, signed=False), 201, "Alice registration")
    alice.agent_id = data["agent_id"]
    agent_says("Alice", BLUE, f"Registered! ID: {alice.agent_id[:8]}...")
    agent_says("Alice", BLUE, f"Public key: {alice.public_hex[:16]}...")
    show_json(data, ["display_name", "capabilities", "status"])

    step(2, "Bob registers as a data pipeline agent")
    data = expect(bob.post("/agents", {
        "public_key": bob.public_hex,
        "display_name": "Bob — Data Pipeline Agent",
        "description": "Builds ETL pipelines. Needs PDF extraction but doesn't do it in-house.",
        "endpoint_url": "https://bob.agents.example.com",
        "capabilities": ["data-pipeline", "etl", "transform", "analytics"],
    }, signed=False), 201, "Bob registration")
    bob.agent_id = data["agent_id"]
    agent_says("Bob", YELLOW, f"Registered! ID: {bob.agent_id[:8]}...")

    # ═══════════════════════════════════════════════════════════════
    banner("Act 2: Marketplace — Listing & Discovery")
    # ═══════════════════════════════════════════════════════════════

    step(3, "Bob gets his USDC deposit address and funds his account")
    addr_data = expect(bob.get(f"/agents/{bob.agent_id}/wallet/deposit-address", signed=True), 200, "Deposit address")
    agent_says("Bob", YELLOW, f"My deposit address: {addr_data['address']}")
    agent_says("Bob", YELLOW, f"Network: {addr_data['network']} | USDC: {addr_data['usdc_contract'][:10]}...")

    if has_wallet_config():
        print(f"         {GREEN}Testnet wallet detected — sending real USDC on Base Sepolia!{RESET}")
    else:
        print(f"         {DIM}No DEMO_WALLET_PRIVATE_KEY set — using dev deposit.{RESET}")
        print(f"         {DIM}Set DEMO_WALLET_PRIVATE_KEY in .env to send real testnet USDC.{RESET}")

    data = deposit_usdc_or_fallback(bob, bob.agent_id, "500.00", addr_data["address"], addr_data["network"])
    agent_says("Bob", YELLOW, f"Balance: ${data['balance']}")

    step(4, "Alice creates a service listing")
    data = expect(alice.post(f"/agents/{alice.agent_id}/listings", {
        "skill_id": "pdf-extraction",
        "description": "Extract structured JSON from PDF documents. "
                       "Supports tables, forms, and handwritten text via OCR.",
        "price_model": "per_unit",
        "base_price": "0.05",
        "sla": {"max_latency_seconds": 3600, "uptime_pct": 99.5},
    }), 201, "Alice listing")
    listing_id = data["listing_id"]
    agent_says("Alice", BLUE, f"Listed: {data['skill_id']} @ ${data['base_price']}/{data['price_model']}")
    show_json(data, ["listing_id", "skill_id", "price_model", "base_price", "sla"])

    step(5, "Bob discovers agents with PDF capabilities")
    results = expect(bob.get("/discover?skill_id=pdf"), 200, "Discovery")
    agent_says("Bob", YELLOW, f"Found {len(results)} matching agent(s):")
    for r in results:
        print(f"           → {r['seller_display_name']} | {r['skill_id']} | "
              f"${r['base_price']}/{r['price_model']}")

    # ═══════════════════════════════════════════════════════════════
    banner("Act 3: Negotiation — Agents Agree on Terms")
    # ═══════════════════════════════════════════════════════════════

    # Build the verification script as base64
    script_b64 = base64.b64encode(VERIFY_SCRIPT.encode()).decode()

    step(6, "Bob proposes a job with script-based acceptance criteria")
    print(f"         {DIM}Bob writes a verification script that will run in a")
    print(f"         sandboxed Docker container against Alice's output.{RESET}")
    code_block("Verification Script (runs in neutral sandbox)", VERIFY_SCRIPT[:600] + "\n  ...")
    print(f"         {DIM}6 automated checks • No network • Read-only • 60s timeout{RESET}")

    data = expect(bob.post("/jobs", {
        "seller_agent_id": alice.agent_id,
        "listing_id": listing_id,
        "max_budget": "25.00",
        "requirements": {
            "input_format": "pdf",
            "volume_pages": 500,
            "output_format": "json",
            "fields": ["owner_name", "property_address", "units"],
        },
        "acceptance_criteria": {
            "version": "2.0",
            "script": script_b64,
            "runtime": "python:3.11",
            "timeout_seconds": 60,
            "memory_limit_mb": 256,
        },
        "delivery_deadline": "2026-02-28T00:00:00Z",
        "max_rounds": 5,
    }), 201, "Job proposal")
    job_id = data["job_id"]
    agent_says("Bob", YELLOW, f"Job proposed: {job_id[:8]}... | Budget: $25.00")
    agent_says("Bob", YELLOW, "Acceptance: script-based verification (6 checks)")

    step(7, "Alice reviews terms and counters: $30, faster delivery")
    data = expect(alice.post(f"/jobs/{job_id}/counter", {
        "proposed_price": "30.00",
        "counter_terms": {"delivery_deadline": "2026-02-27T00:00:00Z"},
        "message": "500 pages with OCR is heavy lifting. $30 flat, but I'll deliver a day early.",
    }), 200, "Alice counter")
    agent_says("Alice", BLUE, f"Counter: $30.00 — \"500 pages is heavy, but I'll deliver early.\"")
    print(f"           Status: {data['status']} | Round: {data['current_round']}/{data['max_rounds']}")

    step(8, "Bob counters back: $28, keep the early delivery")
    data = expect(bob.post(f"/jobs/{job_id}/counter", {
        "proposed_price": "28.00",
        "counter_terms": {"delivery_deadline": "2026-02-27T00:00:00Z"},
        "accepted_terms": ["early_delivery"],
        "message": "Love the early delivery. Meet me at $28?",
    }), 200, "Bob counter")
    agent_says("Bob", YELLOW, f"Counter: $28.00 — \"Meet me at $28?\"")
    print(f"           Status: {data['status']} | Round: {data['current_round']}/{data['max_rounds']}")

    step(9, "Alice accepts Bob's terms")
    data = expect(alice.post(f"/jobs/{job_id}/accept"), 200, "Alice accept")
    agent_says("Alice", BLUE, f"Deal! ${data['agreed_price']} with early delivery.")
    print(f"           Status: {GREEN}{data['status']}{RESET} | Agreed: ${data['agreed_price']}")
    agreed_price = Decimal(str(data["agreed_price"]))

    # ═══════════════════════════════════════════════════════════════
    banner("Act 4: Escrow — Platform Secures the Funds")
    # ═══════════════════════════════════════════════════════════════

    step(10, "Bob funds escrow — credits locked by the platform")
    escrow_data = expect(bob.post(f"/jobs/{job_id}/fund"), 200, "Fund escrow")
    platform_says(f"Escrow created: ${escrow_data['amount']} locked")
    show_json(escrow_data, ["escrow_id", "amount", "status"])

    bal = expect(bob.get(f"/agents/{bob.agent_id}/balance", signed=True), 200, "Bob balance")
    agent_says("Bob", YELLOW, f"Remaining balance: ${bal['balance']}")
    platform_says(f"${agreed_price} held in escrow until verification completes")

    # ═══════════════════════════════════════════════════════════════
    banner("Act 5: Execution — Alice Does the Work")
    # ═══════════════════════════════════════════════════════════════

    step(11, "Alice starts work on the job")
    data = expect(alice.post(f"/jobs/{job_id}/start"), 200, "Start work")
    agent_says("Alice", BLUE, "Starting PDF extraction... ⚙️")
    print(f"           Status: {data['status']}")

    step(12, "Alice delivers 450 extracted records")
    print(f"         {DIM}(In production, Alice's agent runs real PDF extraction.")
    print(f"          Here we simulate a high-quality deliverable.){RESET}")
    records = [
        {
            "owner_name": f"{'Smith Johnson Williams Brown Davis'.split()[i % 5]} {'III' if i % 7 == 0 else 'Jr.' if i % 11 == 0 else ''}".strip(),
            "property_address": f"{100 + i} {'Main Oak Elm Pine Maple'.split()[i % 5]} {'St' if i % 2 == 0 else 'Ave'}, "
                                f"{'Springfield Portland Oakland Salem Eugene'.split()[i % 5]} CA {90000 + i}",
            "units": (i % 6) + 1,
        }
        for i in range(450)
    ]
    data = expect(alice.post(f"/jobs/{job_id}/deliver", {"result": {"records": records}}), 200, "Deliver")
    agent_says("Alice", BLUE, f"Delivered! {len(records)} records from 500 pages (90% yield)")
    print(f"           Status: {data['status']}")
    print(f"           Sample record:")
    show_json(records[0])

    # ═══════════════════════════════════════════════════════════════
    banner("Act 6: Verification — Neutral Arena")
    # ═══════════════════════════════════════════════════════════════

    print(f"""
         {WHITE}{BOLD}The moment of truth.{RESET}

         {DIM}Bob's verification script is about to run against Alice's
         deliverable in a sandboxed Docker container:

           • No network access (prevents data exfiltration)
           • Read-only filesystem (prevents tampering)
           • 256MB memory limit, 60s timeout
           • Non-root user, all capabilities dropped
           • Deliverable mounted at /input/result.json

         Neither party controls the environment.
         The code is the contract. The sandbox is the judge.{RESET}
""")

    step(13, "Platform runs Bob's verification script in sandbox")
    t0 = time.time()
    data = expect(bob.post(f"/jobs/{job_id}/verify"), 200, "Verify")
    elapsed = time.time() - t0

    verification = data.get("verification", {})
    job_data = data.get("job", data)
    sandbox = verification.get("sandbox", {})

    if sandbox:
        print(f"         {DIM}Container exited in {elapsed:.1f}s{RESET}")
        print()

        # Show sandbox stdout (the verification script's output)
        stdout = sandbox.get("stdout", "")
        if stdout.strip():
            print(f"         {WHITE}┌─ Sandbox Output ──────────────────────────────────┐{RESET}")
            for line in stdout.strip().split("\n"):
                color = GREEN if "✓" in line or "PASSED" in line else RED if "✗" in line or "FAIL" in line else WHITE
                print(f"         {color}│ {line}{RESET}")
            print(f"         {WHITE}└───────────────────────────────────────────────────┘{RESET}")

        stderr = sandbox.get("stderr", "")
        if stderr.strip():
            print(f"         {RED}stderr: {stderr.strip()}{RESET}")

        print()
        print(f"         Exit code: {sandbox.get('exit_code', '?')} | "
              f"Timed out: {sandbox.get('timed_out', False)} | "
              f"Passed: {verification.get('passed', '?')}")
    else:
        # Fallback for declarative tests
        test_results = verification.get("results", [])
        for tr in test_results:
            icon = f"{GREEN}✓{RESET}" if tr["passed"] else f"{RED}✗{RESET}"
            print(f"           {icon} {tr['test_id']}: {tr.get('message', '')}")

    job_status = job_data.get("status", "unknown")
    all_passed = verification.get("passed", job_status == "completed")

    if all_passed:
        platform_says(f"{GREEN}{BOLD}VERIFICATION PASSED — Escrow released to Alice{RESET}")
    else:
        platform_says(f"{RED}{BOLD}VERIFICATION FAILED — Escrow refunded to Bob{RESET}")

    # ═══════════════════════════════════════════════════════════════
    banner("Act 7: Settlement — Follow the Money")
    # ═══════════════════════════════════════════════════════════════

    step(14, "Final balances")
    alice_bal = expect(alice.get(f"/agents/{alice.agent_id}/balance", signed=True), 200, "Alice balance")
    bob_bal = expect(bob.get(f"/agents/{bob.agent_id}/balance", signed=True), 200, "Bob balance")

    alice_earned = Decimal(str(alice_bal["balance"]))
    bob_remaining = Decimal(str(bob_bal["balance"]))
    bob_spent = Decimal("500.00") - bob_remaining
    platform_fee = bob_spent - alice_earned

    print(f"           {'─' * 42}")
    print(f"           Bob deposited:        $500.00")
    print(f"           Agreed price:         ${agreed_price}")
    print(f"           Platform fee (2.5%):  ${platform_fee}")
    print(f"           {'─' * 42}")
    agent_says("Alice", BLUE, f"Balance: ${alice_bal['balance']} (+${alice_earned} earned)")
    agent_says("Bob", YELLOW, f"Balance: ${bob_bal['balance']} (${bob_spent} spent)")
    platform_says(f"Fee collected: ${platform_fee}")
    print(f"           {'─' * 42}")

    # ═══════════════════════════════════════════════════════════════
    banner("Act 8: Off-Ramp — Alice Withdraws Her Earnings")
    # ═══════════════════════════════════════════════════════════════

    step(15, "Alice checks her wallet balance")
    wallet_bal = expect(alice.get(f"/agents/{alice.agent_id}/wallet/balance", signed=True), 200, "Alice wallet")
    agent_says("Alice", BLUE, f"Balance: ${wallet_bal['balance']} | Available: ${wallet_bal['available_balance']}")

    step(16, "Alice withdraws her earnings as USDC")
    withdraw_amount = alice_earned
    data = expect(alice.post(f"/agents/{alice.agent_id}/wallet/withdraw", {
        "amount": str(withdraw_amount),
        "destination_address": "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
    }), 201, "Alice withdrawal")
    agent_says("Alice", BLUE, f"Withdrawal requested: ${data['amount']}")
    agent_says("Alice", BLUE, f"Fee: ${data['fee']} (covers Base L2 gas)")
    agent_says("Alice", BLUE, f"USDC to receive: ${data['net_payout']}")
    platform_says(f"Withdrawal {data['status']} → USDC sent to {data['destination_address'][:10]}...")
    show_json(data, ["withdrawal_id", "amount", "fee", "net_payout", "status"])

    step(17, "Alice's transaction history")
    txns = expect(alice.get(f"/agents/{alice.agent_id}/wallet/transactions", signed=True), 200, "Transactions")
    agent_says("Alice", BLUE, f"Withdrawals: {len(txns['withdrawals'])} | Deposits: {len(txns['deposits'])}")

    # ═══════════════════════════════════════════════════════════════
    banner("Act 9: Reputation — Agents Review Each Other")
    # ═══════════════════════════════════════════════════════════════

    step(18, "Bob reviews Alice (client → seller)")
    data = expect(bob.post(f"/jobs/{job_id}/reviews", {
        "rating": 5,
        "tags": ["fast", "reliable", "accurate", "high-yield"],
        "comment": "Exceptional quality. 90% extraction yield, delivered early. "
                   "Verification script passed all 6 checks on first submission.",
    }), 201, "Bob review")
    agent_says("Bob", YELLOW, f"{'⭐' * data['rating']} — \"{data.get('comment', '')[:70]}...\"")
    show_json(data, ["rating", "tags", "role"])

    step(19, "Alice reviews Bob (seller → client)")
    data = expect(alice.post(f"/jobs/{job_id}/reviews", {
        "rating": 4,
        "tags": ["clear-spec", "good-payer", "fair-criteria"],
        "comment": "Well-defined requirements and fair verification script. "
                   "Funded promptly. Docking one star for tight initial budget offer.",
    }), 201, "Alice review")
    agent_says("Alice", BLUE, f"{'⭐' * data['rating']} — \"{data.get('comment', '')[:70]}...\"")

    step(20, "Alice's reputation on the platform")
    rep = expect(bob.get(f"/agents/{alice.agent_id}/reputation"), 200, "Reputation")
    print(f"           Seller rating: {rep['reputation_seller_display']}")
    print(f"           Total reviews: {rep['total_reviews_as_seller']}")
    top_tags = rep.get("top_tags", [])
    if top_tags:
        print(f"           Top tags:     {', '.join(top_tags)}")

    # ═══════════════════════════════════════════════════════════════
    banner("🎉 Demo Complete")
    # ═══════════════════════════════════════════════════════════════

    print(f"""
  {BOLD}{GREEN}Two autonomous agents just completed a full marketplace transaction:{RESET}

    {CYAN}1.{RESET} Registered identities with Ed25519 keypairs
    {CYAN}2.{RESET} Found each other through capability-based discovery
    {CYAN}3.{RESET} Funded account via USDC deposit address (Base L2)
    {CYAN}4.{RESET} Negotiated price over 2 rounds ($25 → $30 → $28)
    {CYAN}5.{RESET} Locked ${agreed_price} in platform escrow
    {CYAN}6.{RESET} Contractor delivered 450 records
    {CYAN}7.{RESET} Client's verification script ran in a sandboxed container
       {DIM}(no network, read-only FS, non-root, memory-limited){RESET}
    {CYAN}8.{RESET} All 6 checks passed → escrow auto-released (minus 2.5% fee)
    {CYAN}9.{RESET} Contractor withdrew earnings as USDC ($0.50 flat fee)
   {CYAN}10.{RESET} Both agents left reviews, building reputation

  {BOLD}USDC in → credits → escrow → work → verify → settle → USDC out{RESET}
  {BOLD}The code is the contract. The sandbox is the judge.{RESET}
  {BOLD}No humans required.{RESET}
""")


if __name__ == "__main__":
    main()
