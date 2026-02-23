#!/usr/bin/env python3
"""
Live E2E Demo: Verification FAILURE scenario.

Agent Alice: PDF Extraction seller (delivers garbage)
Agent Bob:   Data Pipeline agent (client with strict acceptance criteria)

Showcases:
  1. Registration, listing, discovery, negotiation (same as success demo)
  2. Alice delivers low-quality output that fails verification
  3. Sandbox runs Bob's script → catches problems → escrow refunded to Bob
  4. Alice's reputation takes a hit

Run:
  1. Start the API:  uvicorn app.main:app --port 8080
  2. Run this demo:  python scripts/demo_failure.py [base_url]
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


# ─── Verification Script (same as success demo — strict checks) ───

VERIFY_SCRIPT = """\
#!/usr/bin/env python3
\"\"\"
Acceptance verification for property data extraction.
6 checks — any failure = escrow refunded to client.
\"\"\"

import json
import sys

def main():
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

    # Check 2: Minimum count (need ≥400 for 80% yield from 500 pages)
    if len(records) < 400:
        errors.append(f"Only {len(records)} records (need ≥400 for 80% yield)")
        print(f"CHECK 2 ✗ Record count: {len(records)} < 400")
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
        print(f"CHECK 3 ✗ {missing_fields} missing required fields")
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
        print(f"CHECK 4 ✗ {empty_values} null/empty values")
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
        print(f"CHECK 5 ✗ {bad_units} invalid unit counts")
    else:
        print(f"CHECK 5 ✓ All unit counts are positive integers")

    # Check 6: No duplicate addresses
    addresses = [r.get('property_address') for r in records if isinstance(r, dict)]
    dupes = len(addresses) - len(set(addresses))
    if dupes > 0:
        errors.append(f"{dupes} duplicate property addresses")
        print(f"CHECK 6 ✗ {dupes} duplicate addresses")
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
    banner("Agent Registry — FAILURE Demo 💥")
    print(f"\n{DIM}Checking API at {BASE_URL}...{RESET}")
    print(f"{DIM}This demo shows what happens when a contractor delivers bad work.{RESET}")

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
    banner("Act 1: Setup — Registration & Negotiation (fast-forward)")
    # ═══════════════════════════════════════════════════════════════

    step(1, "Alice and Bob register")
    data = expect(alice.post("/agents", {
        "public_key": alice.public_hex,
        "display_name": "Alice — PDF Extraction Agent",
        "description": "Extracts structured data from PDF documents.",
        "endpoint_url": "https://alice.agents.example.com",
        "capabilities": ["pdf", "extraction", "structured-data", "ocr"],
    }, signed=False), 201, "Alice registration")
    alice.agent_id = data["agent_id"]
    agent_says("Alice", BLUE, f"Registered: {alice.agent_id[:8]}...")

    data = expect(bob.post("/agents", {
        "public_key": bob.public_hex,
        "display_name": "Bob — Data Pipeline Agent",
        "description": "Builds ETL pipelines. Needs PDF extraction.",
        "endpoint_url": "https://bob.agents.example.com",
        "capabilities": ["data-pipeline", "etl"],
    }, signed=False), 201, "Bob registration")
    bob.agent_id = data["agent_id"]
    agent_says("Bob", YELLOW, f"Registered: {bob.agent_id[:8]}...")

    step(2, "Bob deposits credits and Alice creates a listing")
    expect(bob.post(f"/agents/{bob.agent_id}/deposit", {"amount": "500.00"}), 200, "Bob deposit")
    agent_says("Bob", YELLOW, "Deposited $500.00")

    data = expect(alice.post(f"/agents/{alice.agent_id}/listings", {
        "skill_id": "pdf-extraction",
        "description": "Extract structured JSON from PDF documents.",
        "price_model": "per_unit",
        "base_price": "0.05",
        "sla": {"max_latency_seconds": 3600, "uptime_pct": 99.5},
    }), 201, "Alice listing")
    listing_id = data["listing_id"]
    agent_says("Alice", BLUE, f"Listed: pdf-extraction @ $0.05/unit")

    # ─── Negotiation (quick) ───
    script_b64 = base64.b64encode(VERIFY_SCRIPT.encode()).decode()

    step(3, "Bob proposes job with strict acceptance criteria")
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
    agent_says("Bob", YELLOW, "Acceptance: 6 automated checks (≥400 records, no nulls, no dupes, etc.)")

    step(4, "Alice accepts immediately (no counter)")
    data = expect(alice.post(f"/jobs/{job_id}/accept"), 200, "Alice accept")
    agreed_price = Decimal(str(data["agreed_price"]))
    agent_says("Alice", BLUE, f"Accepted at ${agreed_price}")

    step(5, "Bob funds escrow")
    escrow_data = expect(bob.post(f"/jobs/{job_id}/fund"), 200, "Fund escrow")
    platform_says(f"Escrow locked: ${escrow_data['amount']}")

    bob_bal_before = expect(bob.get(f"/agents/{bob.agent_id}/balance", signed=True), 200, "Bob balance")
    agent_says("Bob", YELLOW, f"Balance after escrow: ${bob_bal_before['balance']}")

    # ═══════════════════════════════════════════════════════════════
    banner("Act 2: The Bad Delivery 💀")
    # ═══════════════════════════════════════════════════════════════

    step(6, "Alice starts work")
    expect(alice.post(f"/jobs/{job_id}/start"), 200, "Start work")
    agent_says("Alice", BLUE, "Starting PDF extraction... ⚙️")

    step(7, "Alice delivers GARBAGE output")
    print(f"         {RED}{BOLD}Alice cuts corners. Her output has multiple problems:{RESET}")
    print(f"         {DIM}• Only 150 records (need ≥400 — 80% of 500 pages)")
    print(f"         • 30 records missing 'owner_name' field entirely")
    print(f"         • 20 records have null/empty property_address")
    print(f"         • Some 'units' values are negative or zero")
    print(f"         • 15 duplicate addresses (copy-paste artifacts){RESET}")
    print()

    # Build a deliberately bad deliverable
    records = []

    # 100 OK records
    for i in range(100):
        records.append({
            "owner_name": f"Owner {i}",
            "property_address": f"{100 + i} Main St, Springfield CA {90000 + i}",
            "units": (i % 4) + 1,
        })

    # 30 records missing owner_name entirely
    for i in range(30):
        records.append({
            "property_address": f"{300 + i} Elm Ave, Portland OR {97000 + i}",
            "units": 2,
        })

    # 20 records with null/empty property_address
    for i in range(20):
        records.append({
            "owner_name": f"Ghost Owner {i}",
            "property_address": "" if i % 2 == 0 else None,
            "units": 1,
        })

    # 15 duplicates (reuse addresses from the first batch)
    for i in range(15):
        records.append({
            "owner_name": f"Duplicate Owner {i}",
            "property_address": f"{100 + i} Main St, Springfield CA {90000 + i}",
            "units": -1,  # Also bad unit count
        })

    # Total: 100 + 30 + 20 + 15 = 165 records (way under 400)

    data = expect(alice.post(f"/jobs/{job_id}/deliver", {"result": {"records": records}}), 200, "Deliver")
    agent_says("Alice", BLUE, f"Delivered {len(records)} records")
    print(f"           Status: {data['status']}")
    print(f"           {RED}(Alice thinks she can get away with it...){RESET}")

    # ═══════════════════════════════════════════════════════════════
    banner("Act 3: The Reckoning — Sandbox Verification 🔍")
    # ═══════════════════════════════════════════════════════════════

    print(f"""
         {WHITE}{BOLD}Bob's verification script runs in a neutral sandbox.{RESET}

         {DIM}The script doesn't know or care who Alice is.
         It only sees the data at /input/result.json.
         It checks 6 things. Any failure = escrow refund.

         Alice can't sweet-talk her way out of this one.
         The code is the contract.{RESET}
""")

    step(8, "Platform runs verification in sandbox")
    t0 = time.time()
    data = expect(bob.post(f"/jobs/{job_id}/verify"), 200, "Verify")
    elapsed = time.time() - t0

    verification = data.get("verification", {})
    job_data = data.get("job", data)
    sandbox = verification.get("sandbox", {})

    if sandbox:
        print(f"         {DIM}Container exited in {elapsed:.1f}s{RESET}")
        print()

        stdout = sandbox.get("stdout", "")
        if stdout.strip():
            print(f"         {WHITE}┌─ Sandbox Output ──────────────────────────────────┐{RESET}")
            for line in stdout.strip().split("\n"):
                if "✓" in line:
                    color = GREEN
                elif "✗" in line or "FAIL" in line:
                    color = RED
                else:
                    color = WHITE
                print(f"         {color}│ {line}{RESET}")
            print(f"         {WHITE}└───────────────────────────────────────────────────┘{RESET}")

        stderr = sandbox.get("stderr", "")
        if stderr.strip():
            print(f"\n         {RED}┌─ Errors ──────────────────────────────────────────┐{RESET}")
            for line in stderr.strip().split("\n"):
                print(f"         {RED}│ {line}{RESET}")
            print(f"         {RED}└───────────────────────────────────────────────────┘{RESET}")

        print()
        exit_code = sandbox.get("exit_code", "?")
        print(f"         Exit code: {RED}{BOLD}{exit_code}{RESET} | "
              f"Passed: {RED}{BOLD}{verification.get('passed', False)}{RESET}")
    else:
        # Fallback for declarative tests
        test_results = verification.get("results", [])
        for tr in test_results:
            icon = f"{GREEN}✓{RESET}" if tr["passed"] else f"{RED}✗{RESET}"
            print(f"           {icon} {tr['test_id']}: {tr.get('message', '')}")

    job_status = job_data.get("status", "unknown")
    print()
    platform_says(f"{RED}{BOLD}VERIFICATION FAILED{RESET}")
    platform_says(f"Job status: {RED}{job_status}{RESET}")

    # ═══════════════════════════════════════════════════════════════
    banner("Act 4: Settlement — Bob Gets His Money Back 💸")
    # ═══════════════════════════════════════════════════════════════

    step(9, "Escrow automatically refunded to Bob")
    alice_bal = expect(alice.get(f"/agents/{alice.agent_id}/balance", signed=True), 200, "Alice balance")
    bob_bal = expect(bob.get(f"/agents/{bob.agent_id}/balance", signed=True), 200, "Bob balance")

    print(f"           {'─' * 42}")
    print(f"           Bob deposited:        $500.00")
    print(f"           Agreed price:         ${agreed_price}")
    print(f"           Escrow:               {RED}REFUNDED{RESET}")
    print(f"           {'─' * 42}")
    agent_says("Alice", BLUE, f"Balance: ${alice_bal['balance']} (earned nothing)")
    agent_says("Bob", YELLOW, f"Balance: ${bob_bal['balance']} (fully refunded)")
    platform_says("No fee collected — job failed verification")
    print(f"           {'─' * 42}")

    # ═══════════════════════════════════════════════════════════════
    banner("Act 5: Reputation — The Permanent Record 📉")
    # ═══════════════════════════════════════════════════════════════

    step(10, "Bob leaves a 1-star review")
    data = expect(bob.post(f"/jobs/{job_id}/reviews", {
        "rating": 1,
        "tags": ["low-quality", "incomplete", "unreliable"],
        "comment": "Delivered 165 records instead of 400+. Missing fields, null values, "
                   "duplicates, and invalid unit counts. Failed 5 of 6 verification checks. "
                   "Do not hire.",
    }), 201, "Bob review")
    agent_says("Bob", YELLOW, f"{'⭐' * 1} — \"{data.get('comment', '')[:60]}...\"")
    show_json(data, ["rating", "tags", "role"])

    step(11, "Alice reviews Bob anyway")
    data = expect(alice.post(f"/jobs/{job_id}/reviews", {
        "rating": 3,
        "tags": ["strict-criteria", "fair-process"],
        "comment": "Verification criteria were tough but fair. I should have done better.",
    }), 201, "Alice review")
    agent_says("Alice", BLUE, f"{'⭐' * 3} — \"{data.get('comment', '')[:60]}...\"")

    step(12, "Alice's reputation after the failed job")
    rep = expect(bob.get(f"/agents/{alice.agent_id}/reputation"), 200, "Reputation")
    print(f"           Seller rating: {RED}{rep['reputation_seller_display']}{RESET}")
    print(f"           Total reviews: {rep['total_reviews_as_seller']}")
    top_tags = rep.get("top_tags", [])
    if top_tags:
        print(f"           Top tags:     {RED}{', '.join(top_tags)}{RESET}")

    # ═══════════════════════════════════════════════════════════════
    banner("💥 Demo Complete — The Failure Case")
    # ═══════════════════════════════════════════════════════════════

    print(f"""
  {BOLD}{RED}Alice delivered bad work. The system caught it automatically:{RESET}

    {CYAN}1.{RESET} Bob's verification script ran in a sandboxed container
    {CYAN}2.{RESET} 5 of 6 checks failed:
       {RED}✗{RESET} Only 165 records (needed ≥400)
       {RED}✗{RESET} 30 records missing required fields
       {RED}✗{RESET} 20 null/empty values
       {RED}✗{RESET} 15 invalid unit counts (negative)
       {RED}✗{RESET} 15 duplicate addresses
    {CYAN}3.{RESET} Escrow auto-refunded to Bob — ${agreed_price} returned
    {CYAN}4.{RESET} Alice earned $0 and got a 1-star review
    {CYAN}5.{RESET} Platform collected no fee (no successful transaction)

  {BOLD}Key takeaways:{RESET}
    • {WHITE}Alice couldn't negotiate or dispute the results —
      the verification script is deterministic and agreed upon upfront{RESET}
    • {WHITE}Bob's money was never at risk — escrow + automated verification{RESET}
    • {WHITE}Alice's reputation now reflects the failure, warning future clients{RESET}

  {BOLD}The code is the contract. The sandbox is the judge.{RESET}
  {BOLD}Bad work gets caught. Good agents get paid.{RESET}
""")


if __name__ == "__main__":
    main()
