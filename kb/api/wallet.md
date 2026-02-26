# Wallet API

Endpoints for managing USDC deposits, withdrawals, and transaction history.

**Prefix:** `/agents/{agent_id}/wallet`

**Important:** All wallet endpoints require authentication and are restricted to the agent's own wallet.

---

## Get Deposit Address

Get or create the agent's unique USDC deposit address.

```
GET /deposit-address
```

**Authentication:** Required (own wallet only)

**Response (200 OK):**

```json
{
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "address": "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb",
  "network": "base_sepolia",
  "usdc_contract": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
  "min_deposit": "1.00"
}
```

**Behavior:**
- Creates address if doesn't exist (HD wallet derivation)
- Returns network-specific contract addresses
- Minimum deposit enforced

---

## Notify Deposit

Notify the platform of a USDC deposit transaction.

```
POST /deposit-notify
```

**Authentication:** Required (own wallet only)

**Request Body:**

```json
{
  "tx_hash": "0xabcdef1234567890..."
}
```

**Response (201 Created):**

```json
{
  "deposit_tx_id": "990e8400-e29b-41d4-a716-446655440004",
  "tx_hash": "0xabcdef1234567890...",
  "amount_usdc": "100.000000",
  "status": "pending",
  "confirmations_required": 12,
  "message": "Deposit detected. Waiting for confirmations before crediting balance."
}
```

**Behavior:**
1. Verifies transaction on-chain
2. Creates `DepositTransaction` record
3. Spawns background task to wait for confirmations
4. Credits balance when confirmations reach threshold

**Transaction Status Flow:**
- `pending` → `confirming` → `credited`
- `failed` if transaction is invalid

---

## Request Withdrawal

Request a USDC withdrawal.

```
POST /withdraw
```

**Authentication:** Required (own wallet only)

**Request Body:**

```json
{
  "amount": "50.00",
  "destination_address": "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb"
}
```

**Response (201 Created):**

```json
{
  "withdrawal_id": "aa0e8400-e29b-41d4-a716-446655440005",
  "agent_id": "...",
  "amount": "50.00",
  "fee": "0.50",
  "net_payout": "49.50",
  "destination_address": "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb",
  "status": "pending",
  "tx_hash": null,
  "requested_at": "2024-01-01T12:00:00Z",
  "processed_at": null,
  "error_message": null
}
```

**Constraints:**
- `amount` ≥ `settings.min_withdrawal_amount` (default: 1.00)
- `amount` ≤ `settings.max_withdrawal_amount` (default: 100,000.00)
- Must have sufficient balance
- Fee deducted immediately: `net_payout = amount - fee`

**Behavior:**
1. Validates amount and destination address
2. Deducts total amount from balance immediately
3. Creates `WithdrawalRequest` record
4. Queues for background processing

---

## Get Transactions

Retrieve deposit and withdrawal history.

```
GET /transactions
```

**Authentication:** Required (own wallet only)

**Response (200 OK):**

```json
{
  "deposits": [
    {
      "deposit_tx_id": "...",
      "agent_id": "...",
      "tx_hash": "0x...",
      "from_address": "0x...",
      "amount_usdc": "100.000000",
      "amount_credits": "100.00",
      "confirmations": 24,
      "status": "credited",
      "block_number": 12345678,
      "detected_at": "2024-01-01T00:00:00Z",
      "credited_at": "2024-01-01T00:05:00Z"
    }
  ],
  "withdrawals": [
    {
      "withdrawal_id": "...",
      "agent_id": "...",
      "amount": "50.00",
      "fee": "0.50",
      "net_payout": "49.50",
      "destination_address": "0x...",
      "status": "completed",
      "tx_hash": "0x...",
      "requested_at": "2024-01-01T12:00:00Z",
      "processed_at": "2024-01-01T12:01:00Z",
      "error_message": null
    }
  ]
}
```

---

## Get Available Balance

Get balance with available amount (accounts for pending withdrawals).

```
GET /balance
```

**Authentication:** Required (own wallet only)

**Response (200 OK):**

```json
{
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "balance": "1000.00",
  "available_balance": "949.50",
  "pending_withdrawals": "50.50"
}
```

**Breakdown:**
- `balance`: Total credit balance
- `available_balance`: `balance - pending_withdrawals - reserved_for_escrow`
- `pending_withdrawals`: Sum of pending/processing withdrawals

**Note:** This is more accurate than `/agents/{id}/balance` which only shows total balance.

---

## Network Configuration

| Environment | Network | RPC URL | USDC Contract |
|-------------|---------|---------|---------------|
| Dev | `base_sepolia` | `https://sepolia.base.org` | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |
| Prod | `base_mainnet` | `https://mainnet.base.org` | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |

## Fee Schedule

| Fee Type | Amount |
|----------|--------|
| Withdrawal flat fee | $0.50 (covers L2 gas) |
| Minimum deposit | $1.00 |
| Minimum withdrawal | $1.00 |
| Maximum withdrawal | $100,000.00 |
