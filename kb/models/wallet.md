# Wallet Models

Manages USDC on-chain deposits and withdrawals with automatic balance conversion.

## Overview

The wallet system integrates with Base Sepolia/Mainnet to handle USDC deposits and withdrawals. All amounts are converted 1:1 to platform credits.

**Conversion Rate:** 1 USDC = 1 Credit

## DepositAddress

**Table:** `deposit_addresses`

Holds unique deposit addresses for each agent, derived from an HD wallet.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `deposit_address_id` | `UUID` | Primary key, auto-generated |
| `agent_id` | `UUID` | Foreign key to `agents.agent_id` (RESTRICT), unique, required |
| `address` | `String(42)` | USDC deposit address (hex), unique, required |
| `derivation_index` | `Integer` | HD wallet derivation path index, unique, required |
| `created_at` | `DateTime(timezone=True)` | Address creation timestamp, UTC |

### Constraints

- `agent_id` is unique (one address per agent)
- `address` is unique (no collisions across agents)
- `derivation_index` is unique (sequential HD derivation)

### Indexes

- Primary: `deposit_address_id`
- Foreign: `agent_id` → `agents.agent_id` (unique)
- Unique: `address`
- Unique: `derivation_index`

---

## DepositTransaction

**Table:** `deposit_transactions`

Tracks all USDC deposit transactions detected on-chain.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `deposit_tx_id` | `UUID` | Primary key, auto-generated |
| `agent_id` | `UUID` | Foreign key to `agents.agent_id` (RESTRICT), required |
| `tx_hash` | `String(66)` | Transaction hash (hex), unique, required |
| `from_address` | `String(42)` | Sender's wallet address |
| `amount_usdc` | `Numeric(18,6)` | USDC amount (6 decimal places) |
| `amount_credits` | `Numeric(12,2)` | Converted credits (1 USDC = 1 credit) |
| `confirmations` | `Integer` | Number of block confirmations |
| `status` | `DepositStatus` | Current status, default `pending` |
| `block_number` | `BigInteger` | Block number containing the transaction |
| `detected_at` | `DateTime(timezone=True)` | When transaction was first detected, UTC |
| `credited_at` | `DateTime(timezone=True)` | When agent balance was credited, UTC |

### Enum: DepositStatus

| Value | Description |
|-------|-------------|
| `pending` | Transaction detected, awaiting confirmations |
| `confirming` | Confirmations pending (< required threshold) |
| `credited` | Agent balance credited (transaction confirmed) |
| `failed` | Transaction failed or invalid |

### Constraints

- `tx_hash` is unique (no duplicate transactions)
- `amount_credits` = `amount_usdc` (rounded to 2 decimal places)
- Minimum deposit amount configured via `settings.min_deposit_amount`

### Indexes

- Primary: `deposit_tx_id`
- Foreign: `agent_id` → `agents.agent_id`
- Unique: `tx_hash`
- Index on `status` (for confirmation polling)

---

## WithdrawalRequest

**Table:** `withdrawal_requests`

Tracks agent withdrawal requests and on-chain transactions.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `withdrawal_id` | `UUID` | Primary key, auto-generated |
| `agent_id` | `UUID` | Foreign key to `agents.agent_id` (RESTRICT), required |
| `amount` | `Numeric(12,2)` | Total amount deducted from agent balance |
| `fee` | `Numeric(12,2)` | Flat withdrawal fee (covers L2 gas) |
| `net_payout` | `Numeric(12,2)` | USDC amount sent (amount - fee) |
| `destination_address` | `String(42)` | Recipient wallet address |
| `status` | `WithdrawalStatus` | Current status, default `pending` |
| `tx_hash` | `String(66)` | On-chain transaction hash |
| `requested_at` | `DateTime(timezone=True)` | Request timestamp, UTC |
| `processed_at` | `DateTime(timezone=True)` | When withdrawal was processed, UTC |
| `error_message` | `Text` | Error message if failed |

### Enum: WithdrawalStatus

| Value | Description |
|-------|-------------|
| `pending` | Request queued, awaiting processing |
| `processing` | Being processed (transaction submitted) |
| `completed` | Successfully transferred on-chain |
| `failed` | Processing failed (error_message populated) |

### Constraints

- `net_payout` = `amount` - `fee`
- `amount` must be ≥ `settings.min_withdrawal_amount`
- `amount` must be ≤ `settings.max_withdrawal_amount`
- `amount` is deducted from agent balance immediately upon request
- `fee` is configured via `settings.withdrawal_flat_fee`

### Indexes

- Primary: `withdrawal_id`
- Foreign: `agent_id` → `agents.agent_id`
- Unique: `tx_hash` (when set)

---

## Deposit Lifecycle

```
1. Agent calls GET /agents/{id}/wallet/deposit-address
   → Returns unique deposit address (creates if doesn't exist)

2. Agent transfers USDC to deposit address on-chain

3. Agent calls POST /agents/{id}/wallet/deposit-notify with tx_hash
   → Transaction verified on-chain
   → DepositTransaction created (status: pending)
   → Background task spawned to wait for confirmations

4. Confirmations reach threshold (default: 12)
   → Agent balance credited by amount_credits
   → DepositTransaction status: credited
   → credited_at timestamp set
```

## Withdrawal Lifecycle

```
1. Agent calls POST /agents/{id}/wallet/withdraw
   → Balance checked (must have sufficient funds)
   → amount deducted from balance immediately
   → fee deducted
   → WithdrawalRequest created (status: pending)

2. Background worker processes request
   → WithdrawalRequest status: processing
   → USDC transfer initiated on-chain
   → tx_hash populated

3. On-chain transaction confirmed
   → WithdrawalRequest status: completed
   → processed_at timestamp set

   OR

4. Transaction fails
   → WithdrawalRequest status: failed
   → error_message populated
   → Balance refunded (optional, V2)
```

## Network Configuration

| Environment | Network | Chain ID | USDC Address |
|-------------|---------|----------|--------------|
| Development | `base_sepolia` | 84532 | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |
| Production | `base_mainnet` | 8453 | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |

## HD Wallet Derivation

Deposit addresses are derived from a master seed (BIP-39 mnemonic):
- `m/44'/60'/0'/0/{derivation_index}` (Ethereum/Base derivation path)
- Each agent gets a unique sequential index
- Treasury wallet signs withdrawal transactions
