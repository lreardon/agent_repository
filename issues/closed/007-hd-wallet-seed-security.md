# HD wallet seed in .env is a single point of compromise

**Severity:** 🔴 Critical
**Status:** ✅ Closed
**Source:** CONCERNS2.md #23, CONCERNS3-claude.md #23

## Description

The `HD_WALLET_MASTER_SEED` BIP-39 mnemonic is stored as a plain environment variable in `.env`. If `.env` is leaked or the machine is compromised:

- All deposit addresses can be derived from it
- An attacker could sweep all deposits before the platform detects them
- Every agent's funds are at risk simultaneously

## Impact

- Loss of all deposited funds across all agents
- Complete platform fund compromise
- No way to detect compromise until funds are gone

## Mitigation Status

**✅ Implemented — Pluggable secrets backend (Option 2: KMS).**

`app/services/secrets.py` provides three backends:
- `env` — reads from environment variables (development only)
- `aws_secrets` — fetches from AWS Secrets Manager
- `gcp_secrets` — fetches from GCP Secret Manager

Both `hd_wallet_master_seed` and `treasury_wallet_private_key` are accessed exclusively through `get_wallet_seed()` and `get_treasury_key()`. Results are cached in memory (`lru_cache`) for the process lifetime.

**Production configuration:**
```env
SECRETS_BACKEND=aws_secrets
SECRETS_PREFIX=agent-registry/prod
AWS_REGION=us-east-1
# No HD_WALLET_MASTER_SEED or TREASURY_WALLET_PRIVATE_KEY in .env
```

Test coverage in `tests/test_secrets.py` (9 tests).

**Remaining considerations for scale:**
- Upgrade to Option B (separate signing service) or Option C (HSM) when custodying significant funds
- Seed is still in process memory at runtime — acceptable for current threat model

## Fix Options

### Option 1: Hardware Security Module (HSM)
Store seed in a hardware security module (e.g., YubiHKey, AWS CloudHSM, GCP KMS).

**Pros:**
- Seed never in memory or disk in plaintext
- Hardware tamper resistance
- Best security practice for custodial funds

**Cons:**
- Cost and complexity
- Vendor lock-in
- Slower operations

### Option 2: Key Management Service (KMS)
Use cloud KMS (AWS KMS, GCP Secret Manager, Azure Key Vault) to encrypt the seed at rest.

**Pros:**
- Managed security
- Access logging and auditing
- Automatic key rotation
- Less complex than HSM

**Cons:**
- Still requires loading seed into memory at runtime
- Cloud provider lock-in

### Option 3: Encrypted .env with File Permissions
Encrypt `.env` with a passphrase, restrict file permissions, require passphrase on startup.

**Pros:**
- Simple to implement
- No external dependencies
- Low cost

**Cons:**
- Passphrase in another file or env var (just moves the problem)
- Manual unlock required on restart
- Not production-ready for automatic scaling

### Option 4: Shamir's Secret Sharing
Split seed into multiple parts stored in different places.

**Pros:**
- No single point of failure
- Requires compromising multiple locations

**Cons:**
- Complex implementation
- Rebuilding requires all parts
- Key ceremony overhead

**Recommendation:** Start with Option 2 (KMS) for production, add Option 1 (HSM) for scale. Option 3 (encrypted .env) for development only.

## Security Best Practices

```bash
# Restrict .env file permissions
chmod 600 .env

# Add .env to .gitignore
echo ".env" >> .gitignore

# Never commit .env
git rm --cached .env
```

## Related Issues

- #015: Platform signing key placeholder (similar credential management)
- #014: Treasury wallet (related fund security)

## References

- CONCERNS2.md #23
- CONCERNS3-claude.md #23
