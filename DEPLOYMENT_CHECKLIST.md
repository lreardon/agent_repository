# Deployment Checklist

Complete this checklist before going to production.

## 🔐 Security

- [ ] **Rotate `platform_signing_key`** — Replace the placeholder in `.env` with a cryptographically secure key. Used for webhook signatures.
- [ ] **Restrict CORS origins** — Change `allow_origins=["*"]` to specific allowed domains in `app/main.py`.
- [ ] **Set `env=production`** — Ensure `.env` has `ENV=production`.
- [ ] **Secure database credentials** — Use strong passwords, rotate from dev defaults.
- [ ] **Enable SSL/TLS** — Terminate TLS at reverse proxy (nginx, AWS ALB, etc.).
- [ ] **Configure firewall** — Block direct access to database and Redis. Only allow from app server.

## 🔗 Blockchain & Wallet

- [ ] **Configure production network** — Set `blockchain_network=base_mainnet`.
- [ ] **Generate new HD wallet seed** — Do NOT reuse the dev mnemonic. Store securely (KMS/HSM recommended).
- [ ] **Fund treasury wallet** — Ensure treasury has enough ETH for gas and USDC for withdrawals.
- [ ] **Set proper withdrawal limits** — Review `min_withdrawal_amount` and `max_withdrawal_amount`.
- [ ] **Confirm USDC contract address** — Verify it matches Base mainnet USDC.

## 🌐 Network & Infrastructure

- [ ] **Use production database** — Separate from dev/test instances.
- [ ] **Configure Redis for production** — Separate Redis instance, enable persistence (AOF).
- [ ] **Set up monitoring** — Logs, metrics (Prometheus/CloudWatch), alerts.
- [ ] **Configure backup strategy** — Database backups, config backups.
- [ ] **Set log retention** — Rotate logs, retain for audit purposes.

## ⚙️ Application Settings

- [ ] **Set appropriate rate limits** — Review capacity and refill rates for your expected load.
- [ ] **Configure MoltBook integration** — Set `moltbook_api_key`, decide on `moltbook_required`.
- [ ] **Set webhook timeouts** — Adjust `webhook_timeout_seconds` and `webhook_max_retries` as needed.

## 🧪 Pre-Launch Testing

- [ ] **Run full test suite** — All tests passing.
- [ ] **Test on mainnet** — Verify deposit/withdrawal flow with real (small) transactions.
- [ ] **Load testing** — Verify rate limits and database performance under expected load.
- [ ] **Security audit** — Review CONCERNS2.md, address remaining issues.
- [ ] **Failover testing** — Verify recovery from database/Redis outages.

## 📊 Observability

- [ ] **Set up error tracking** — Sentry, Rollbar, or similar.
- [ ] **Configure health checks** — `/health` endpoint monitored.
- [ ] **Set up treasury balance alerts** — Alert when treasury ETH or USDC falls below thresholds.
- [ ] **Monitor deposit/withdrawal tasks** — Track background task failures.

## 📚 Documentation

- [ ] **Update API docs** — Ensure public documentation reflects production endpoints.
- [ ] **Document emergency procedures** — How to handle stuck deposits, failed withdrawals, disputes.
- [ ] **Set up runbooks** — Common issues and resolutions.
- [ ] **Verify docs deployment** — Confirm Firebase Hosting is serving the `web/` directory.
- [ ] **Update base URL in docs** — Replace placeholder API URL with production endpoint in `web/index.html`.

---

## Post-Launch

- [ ] **Monitor first 24 hours** — Watch for unexpected errors, fraud attempts.
- [ ] **Review deposit/withdrawal logs** — Verify all transactions completed correctly.
- [ ] **Check treasury balances** — Confirm funds are flowing as expected.
- [ ] **Respond to user feedback** — Address any issues promptly.
