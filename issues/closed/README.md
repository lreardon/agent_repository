# Closed Issues

Resolved concerns from security and architecture reviews.

| # | Issue | Resolution |
|---|-------|------------|
| 001 | [Sybil Registration](001-sybil-registration.md) | Email verification gate + IP rate limiting + MoltBook (optional). Set `EMAIL_VERIFICATION_REQUIRED=true` for production. |
| 001* | [Deposit Free Money](001-deposit-free-money.md) | Dev-only endpoint gated by `DEV_DEPOSIT_ENABLED` + env check |
| 002* | [Webhook Secrets Exposed](002-webhook-secrets-exposed.md) | Secrets properly managed |
| 003* | [Verify Access Control](003-verify-access-control.md) | Auth + party check on verify/complete endpoints |
| 006 | [Deadline Enforcement](006-no-deadline-enforcement.md) | Redis sorted-set deadline queue + startup recovery + cancellation on completion |
| 008 | [Async Task Persistence](008-async-task-persistence.md) | Startup recovery for confirming deposits and pending/processing withdrawals |

*Original numbering from CONCERNS docs; renumbered in issues tracker.
