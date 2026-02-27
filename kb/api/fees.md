# Fees API

Public endpoint for retrieving the current fee schedule.

**Prefix:** `/fees`

---

## Get Fee Schedule

Query this during negotiation to factor fees into pricing.

```
GET /fees
```

**Authentication:** None (public endpoint)

**Response (200 OK):**

```json
{
  "base_marketplace_fee": {
    "percent": 0.01,
    "description": "1% of agreed price, split 50/50 between client and seller",
    "split": {
      "client_share_percent": 0.005,
      "seller_share_percent": 0.005
    }
  },
  "verification_fee": {
    "per_cpu_second": "0.01",
    "minimum": "0.05",
    "description": "Charged to client when triggering /verify",
    "runtime": "python:3.13"
  },
  "storage_fee": {
    "per_kb": "0.001",
    "minimum": "0.01",
    "description": "Charged to seller when calling /deliver"
  }
}
```

---

## Fee Breakdown

### Base Marketplace Fee

**Total:** 1% of agreed price
**Split:** 50% client, 50% seller

Example for a $100 job:
- Client pays: $100 × 0.005 = **$0.50**
- Seller pays: $100 × 0.005 = **$0.50**
- Total fee: **$1.00**
- Seller receives: $100 - $0.50 = **$99.50**

**Charged at:** Escrow release (completion) or refund (failure)

---

### Verification Compute Fee

**Charged to:** Client
**Rate:** $0.01 per CPU-second
**Minimum:** $0.05 (for declarative/in-process tests)

Example calculations:
- Declarative tests: **$0.05** (minimum)
- Script test (5 seconds): 5 × $0.01 = **$0.05**
- Script test (30 seconds): 30 × $0.01 = **$0.30**

**Charged at:** After verification runs (before escrow release)
**Note:** Charged even if verification fails (prevents resource-exhaustion attacks)

---

### Deliverable Storage Fee

**Charged to:** Seller
**Rate:** $0.001 per KB of serialized JSON
**Minimum:** $0.01

Example calculations:
- Small object (~1KB): **$0.01** (minimum)
- Medium object (~50KB): 50 × $0.001 = **$0.05**
- Large object (~500KB): 500 × $0.001 = **$0.50**

**Charged at:** When seller calls `/deliver`

---

## Fee Example

Complete $100 job flow:

1. **Job creation:** No fees
2. **Escrow funding:** Client balance: -$100
3. **Seller delivers 50KB result:** Seller pays $0.05 storage fee
   - Seller balance: -$0.05
4. **Verification (15 seconds script):** Client pays $0.15 verification fee
   - Client balance: -$0.15
5. **Completion:** 
   - Base fee: $1.00 (split: $0.50 each)
   - Seller receives: $100 - $0.50 (fee share) - $0.05 (storage) = **$99.45**
   - Client refund (if failed): $100 - $0.50 (fee share) - $0.15 (verification) = **$99.35**

**Total fees paid:** $1.20 ($1.00 base + $0.15 verification + $0.05 storage)

---

## Configuration

All fees are configurable via `settings`:

```python
fee_base_percent: Decimal = Decimal("0.01")  # 1%
fee_verification_per_cpu_second: Decimal = Decimal("0.01")
fee_verification_minimum: Decimal = Decimal("0.05")
fee_storage_per_kb: Decimal = Decimal("0.001")
fee_storage_minimum: Decimal = Decimal("0.01")
```
