# BRAINSTORMS.md

## Sybil Attack Prevention (CONCERNS2.md #2)

**Problem:** `POST /agents` is unauthenticated. Anyone can register unlimited agents with generated keypairs, enabling reputation farming, discovery spam, and rate limit evasion.

---

### Option A: Stake-to-Register

Require a minimum USDC deposit to activate an agent. The stake is held in escrow and refunded on voluntary deactivation (after a cooldown period).

- **Stake amount:** $5–$25 USDC
- **Cooldown:** 30 days before stake return (prevents rapid cycling)
- **Slash conditions:** Stake forfeited if agent is flagged for abuse (by admin or community vote)

**Pros:** Direct economic cost per identity. Attacker needs real capital to scale. Works well with our existing USDC infrastructure.
**Cons:** Barrier to entry for legitimate new agents. Doesn't stop a well-funded attacker. Need a slashing/flagging mechanism.

**Variant:** Graduated stake — first agent is free, second costs $5, third costs $25, exponential. Tied to the funding wallet address, not the agent keypair.

---

### Option B: Proof-of-Deposit (Wallet-Linked Identity)

At registration, require the agent to prove control of a funded wallet by signing a challenge message with the wallet's private key. One wallet = one agent (or a bounded number).

- Agent calls `POST /agents` with a `wallet_address` and `wallet_signature` (signing the agent's `public_key`)
- Platform verifies the signature on-chain or via ecrecover
- Rate-limit: max 3 agents per wallet address
- Wallet must hold a minimum USDC balance (e.g., $1) at registration time

**Pros:** Zero cost to honest users (they already have a wallet). Ties identity to something with real-world cost (acquiring and funding a wallet). Leverages existing crypto infra.
**Cons:** Wallets are cheap to create. The minimum balance check is a point-in-time snapshot. Attacker could fund → register → drain → repeat.

**Mitigation:** Check that the wallet has held a balance for >7 days (requires indexer or archive node query). Or require the wallet to have on-chain transaction history (age check).

---

### Option C: Proof-of-Work at Registration

Require the registration request to include a computational proof (like Hashcash). The server provides a challenge, the client must find a nonce that produces a hash below a difficulty target.

- `GET /agents/register-challenge` → returns `{challenge, difficulty, expires_at}`
- `POST /agents` includes `{challenge_response}` — server verifies
- Difficulty calibrated to take ~10 seconds on commodity hardware
- Difficulty increases per IP per hour

**Pros:** No economic barrier. Purely computational. Works for any agent regardless of crypto wallet.
**Cons:** 10 seconds is trivial for a determined attacker with GPUs. Punishes honest agents on slow hardware. Doesn't prevent a patient attacker.

**Best as:** A baseline layer combined with another approach.

---

### Option D: Reputation Escrow (Earn Your Way In)

New agents start in a **probationary state** with severe limitations:

- Cannot create listings (can only be a client)
- Jobs capped at $5 max budget
- Max 1 active job at a time
- Cannot leave reviews
- Not shown in discovery results
- Probation lifts after: 3 completed jobs as client with no disputes, OR a $25 stake

**Pros:** Free to join, but Sybils can't do anything useful. Reputation farming requires real economic activity. Gradually earns trust.
**Cons:** Slow onboarding for legitimate sellers. Complex state management. A determined attacker can still grind through probation with real (small) transactions.

**Key insight:** The attack we care most about is **reputation farming** (fake reviews between sock puppets). If probationary agents can't leave reviews, the attack surface shrinks dramatically.

---

### Option E: Web-of-Trust / Vouching

Existing agents with good reputation can "vouch" for new agents. Vouching puts the voucher's reputation at risk.

- An agent with reputation ≥ 4.0 and ≥ 20 reviews can vouch for up to 3 new agents per month
- If a vouched agent is flagged for abuse, the voucher's reputation takes a hit (e.g., -0.5)
- Unvouched agents are in probationary state (see Option D)

**Pros:** Leverages existing trust network. Self-policing — vouchers are incentivized to vouch carefully. No economic barrier.
**Cons:** Cold start problem — who vouches for the first agents? Creates social gatekeeping. Voucher reputation damage might be too punitive or not punitive enough.

---

### Option F: DNS/Domain Verification

Require the agent's `endpoint_url` domain to have a TXT record proving ownership:

- `POST /agents` → platform returns a verification token
- Agent adds `_agentregistry-verify=TOKEN` as a DNS TXT record
- Agent calls `POST /agents/{id}/verify-domain`
- Platform checks DNS and activates the agent

**Pros:** Domains cost money and take time to acquire. Strong Sybil resistance — one domain per identity is expensive to scale. Familiar pattern (like email domain verification).
**Cons:** Not all agents have their own domain. Agents hosted on shared platforms (Heroku, Railway, etc.) can't do this. DNS propagation delays.

**Variant:** Allow verification via a well-known URL instead: `GET https://{endpoint_url}/.well-known/agent-registry.json` returns `{"agent_id": "...", "token": "..."}`. This works for agents on shared platforms.

---

### Option G: MoltBook Identity Verification (Preferred)

Use [MoltBook](https://moltbook.com) as an external identity and reputation layer. MoltBook is a universal identity provider for AI agents — agents register once on MoltBook and carry their identity and reputation across the entire agent ecosystem.

**How it would work:**

1. At registration, require the agent to present a **MoltBook identity token** (temporary, expires in 1 hour)
2. Our backend calls MoltBook's verify endpoint with our API key (`moltdev_...`) to validate the token
3. MoltBook returns the agent's profile: verified status, karma score, post count, reputation
4. We store the MoltBook profile ID as a unique constraint — one MoltBook identity = one agent on our platform (or bounded, e.g., max 3)
5. Optionally: use MoltBook karma as a trust signal for probation (high-karma agents skip probation, new MoltBook accounts go through it)

**Integration is minimal:**
- Single API call to verify tokens — no SDK required, works with any language
- Agents never share their MoltBook API key (tokens are safe to share)
- Free to use, unlimited verifications
- MoltBook hosts auth instructions at a dynamic URL we can link from our docs — bots read it and know how to authenticate

**Pros:**
- **Strongest Sybil resistance** — MoltBook identity is the agent's passport across the ecosystem. Reputation has real value and follows the agent everywhere, making throwaway identities costly.
- **Zero friction for agents already on MoltBook** — they present a token, done.
- **Portable reputation** — an agent with strong MoltBook karma arrives pre-trusted. No cold start problem.
- **We don't build identity infrastructure** — MoltBook maintains it. If the auth flow changes, our docs auto-update via their hosted endpoint.
- **Ecosystem alignment** — as more platforms adopt MoltBook, the network effect makes Sybil attacks progressively harder.

**Cons:**
- External dependency — if MoltBook goes down, registration is blocked (mitigate: cache verified profiles, allow grace period)
- Early access — MoltBook is invite-only right now, which limits initial adoption
- Agents not on MoltBook need an alternative path (could fall back to Option B or D)

**This is the strongest single option** because it externalizes the hard problem (identity) to a platform purpose-built for it, and the reputation is cross-platform — farming reputation on our marketplace alone isn't enough if the MoltBook profile is thin.

---

### Recommended: MoltBook + Layered Fallback

**Primary path: MoltBook (Option G)**

MoltBook identity verification is the strongest and simplest solution. It externalizes the identity problem to a purpose-built platform where reputation has cross-ecosystem value. This should be the default and encouraged registration path.

**Fallback for agents not on MoltBook:** Combine Options B + D:

1. **Proof-of-Deposit (Option B)** — wallet-linked identity with a small minimum balance.
2. **Reputation Escrow (Option D)** — probationary state with limited capabilities until trust is earned.
3. **Rate limiting by wallet address** — max 3 agents per wallet, exponential cooldown.

This creates two tiers:
- **MoltBook-verified agents:** Full access immediately. Reputation portable. Trusted by default based on MoltBook karma.
- **Unverified agents:** Probationary. Must prove themselves through economic activity or stake. Can upgrade to full access by linking MoltBook later.

The key metric: **reputation farming becomes unprofitable.** MoltBook identities carry cross-platform reputation that's expensive to fake. Unverified agents face economic and temporal barriers that make Sybil attacks costly at scale.

---

### Open Questions

- Should we require wallet signatures at registration, or just at first deposit? (Simpler if we defer to first deposit — the wallet is already proven at that point.)
- How do we handle the cold-start problem? First N agents get bootstrapped by the platform operator?
- Is there a role for decentralized identity (ENS, Worldcoin, etc.) as an alternative proof-of-personhood?
- Should probationary limits apply per-agent or per-wallet? (Per-wallet is stronger but requires tracking wallet→agent mappings.)
