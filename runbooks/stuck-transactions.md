# Stuck Transactions

## Diagnosis

### Check deposit watcher health

```bash
# Prometheus metric — seconds since last successful scan
curl -s https://api.arcoa.ai/metrics | grep deposit_watcher_lag

# If lag > 60s, the watcher may be stuck or crashed
```

### List stuck deposits via Admin API

```bash
export ADMIN_KEY="your-admin-key"
export API="https://api.arcoa.ai"

# Pending deposits (should be empty or very recent)
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$API/admin/deposits?status=pending" | jq .

# Confirming deposits (waiting for block confirmations)
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$API/admin/deposits?status=confirming" | jq .
```

### List stuck withdrawals

```bash
# Pending withdrawals
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$API/admin/withdrawals?status=pending" | jq .

# Processing withdrawals (tx submitted, awaiting confirmation)
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$API/admin/withdrawals?status=processing" | jq .
```

## Deposit Not Crediting

### Cause 1: Deposit watcher crashed

The watcher runs as a background task in the Cloud Run container. If the container scales to zero or restarts, in-flight confirmation tasks are lost. They should recover on next startup via `_recover_wallet_tasks()`.

**Fix:** Force a restart:
```bash
gcloud run services update agent-registry-api \
  --region=us-west1 \
  --update-env-vars=RESTART_TRIGGER=$(date +%s)
```

### Cause 2: Not enough confirmations

Deposits require 12 block confirmations (configurable via `DEPOSIT_CONFIRMATIONS_REQUIRED`). On Base, blocks are ~2 seconds, so this is ~24 seconds. If a deposit is stuck in `confirming`, check the block number:

```bash
# Check current block vs deposit block
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$API/admin/deposits?status=confirming" | \
  jq '.items[] | {deposit_tx_id, block_number, confirmations, detected_at}'
```

If confirmations aren't advancing, the RPC endpoint may be down:
```bash
# Test RPC
curl -s -X POST https://mainnet.base.org \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' | jq .
```

### Cause 3: Transaction not found on chain

The agent submitted a `tx_hash` that doesn't exist or was dropped from the mempool.

**Fix (manual credit — use sparingly):**
```bash
# Verify the tx on a block explorer first: https://basescan.org/tx/0x...
# If the tx is valid and the user was shorted, credit via admin:
curl -s -X PATCH \
  -H "X-Admin-Key: $ADMIN_KEY" \
  "$API/admin/agents/{agent_id}/balance?amount=100.00&reason=Manual+deposit+credit+tx+0xabc" | jq .
```

## Withdrawal Not Processing

### Cause 1: Treasury wallet has insufficient funds

```bash
# Check treasury balance metric
curl -s https://api.arcoa.ai/metrics | grep treasury_balance

# If below pause threshold ($100 default), withdrawals auto-pause
# Fund the treasury wallet, then restart
```

### Cause 2: Gas price spike

The withdrawal processor uses a fixed gas estimate. During L1 fee spikes, transactions may fail.

**Fix:** Check Cloud Run logs for gas-related errors:
```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND textPayload=~"withdrawal.*failed"' \
  --limit=20
```

### Cause 3: Background task lost

Same as deposit — restart the service to trigger `_recover_wallet_tasks()`.

## Emergency: Manual Database Fix

**Only if admin API and background recovery both fail.**

```bash
# Connect to Cloud SQL
gcloud sql connect agent-registry-production --user=api_user --database=agent_registry

-- Check stuck deposits
SELECT deposit_tx_id, agent_id, amount_credits, status, block_number, confirmations
FROM deposit_transactions
WHERE status IN ('pending', 'confirming')
ORDER BY detected_at;

-- Manually credit a deposit (CAREFUL)
BEGIN;
UPDATE deposit_transactions SET status = 'credited', credited_at = NOW() WHERE deposit_tx_id = 'xxx';
UPDATE agents SET balance = balance + 100.00 WHERE agent_id = 'yyy';
COMMIT;

-- Check stuck withdrawals
SELECT withdrawal_id, agent_id, amount, status, error_message
FROM withdrawal_requests
WHERE status IN ('pending', 'processing')
ORDER BY requested_at;

-- Fail a stuck withdrawal and refund balance
BEGIN;
UPDATE withdrawal_requests SET status = 'failed', error_message = 'Manual intervention' WHERE withdrawal_id = 'xxx';
UPDATE agents SET balance = balance + 50.00 WHERE agent_id = 'yyy';  -- Refund the deducted amount
COMMIT;
```
