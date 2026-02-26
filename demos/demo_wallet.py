"""Shared USDC wallet utilities for demo scripts.

Handles sending testnet USDC and notifying the platform for confirmation.
"""

import os
import sys
import time

import httpx

# ─── Colors ───
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
RESET = "\033[0m"

# USDC has 6 decimals
USDC_DECIMALS = 6
USDC_SCALE = 10**USDC_DECIMALS

# Minimal ERC-20 ABI for transfer
ERC20_TRANSFER_ABI = [
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

# Network configs
NETWORKS = {
    "base_sepolia": {
        "rpc": "https://sepolia.base.org",
        "chain_id": 84532,
        "usdc": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "explorer": "https://sepolia.basescan.org",
    },
    "base_mainnet": {
        "rpc": "https://mainnet.base.org",
        "chain_id": 8453,
        "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "explorer": "https://basescan.org",
    },
}


def send_usdc_deposit(
    deposit_address: str,
    amount_credits: str,
    network: str = "base_sepolia",
) -> str:
    """Send USDC on-chain to a deposit address. Returns tx hash."""
    from eth_account import Account
    from web3 import Web3

    demo_key = os.environ["DEMO_WALLET_PRIVATE_KEY"]
    net = NETWORKS[network]

    w3 = Web3(Web3.HTTPProvider(net["rpc"]))
    wallet = Account.from_key(demo_key)
    usdc = w3.eth.contract(
        address=w3.to_checksum_address(net["usdc"]),
        abi=ERC20_TRANSFER_ABI,
    )

    raw_amount = int(float(amount_credits) * USDC_SCALE)
    dest = w3.to_checksum_address(deposit_address)

    nonce = w3.eth.get_transaction_count(wallet.address)
    gas_price = w3.eth.gas_price

    tx = usdc.functions.transfer(dest, raw_amount).build_transaction({
        "from": wallet.address,
        "nonce": nonce,
        "chainId": net["chain_id"],
        "gas": 100_000,
        "maxFeePerGas": gas_price * 2,
        "maxPriorityFeePerGas": w3.eth.max_priority_fee,
    })

    signed = wallet.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    h = tx_hash.hex()
    return h if h.startswith("0x") else f"0x{h}"


def wait_for_tx_confirmation(tx_hash: str, network: str = "base_sepolia", timeout: int = 60) -> None:
    """Wait for a transaction to be mined on-chain."""
    from web3 import Web3

    net = NETWORKS[network]
    w3 = Web3(Web3.HTTPProvider(net["rpc"]))

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt is not None and receipt.status == 1:
                return
        except Exception:
            pass
        time.sleep(2)

    print(f"         {RED}✗ Transaction not mined within {timeout}s{RESET}")
    sys.exit(1)


def wait_for_balance(
    agent_client,
    agent_id: str,
    expected_balance: str,
    timeout_seconds: int = 120,
    poll_interval: int = 3,
) -> dict:
    """Poll the balance endpoint until credits appear or timeout."""
    deadline = time.time() + timeout_seconds
    dots = 0

    while time.time() < deadline:
        resp = agent_client.get(f"/agents/{agent_id}/balance", signed=True)
        if resp.status_code == 200:
            bal = resp.json()
            if float(bal["balance"]) >= float(expected_balance):
                print()
                return bal

        dots += 1
        if dots % 10 == 0:
            elapsed = int(time.time() - (deadline - timeout_seconds))
            print(f" {DIM}({elapsed}s){RESET}", end="", flush=True)
        else:
            print(".", end="", flush=True)

        time.sleep(poll_interval)

    print()
    return None


def deposit_usdc(
    agent_client,
    agent_id: str,
    amount: str,
    deposit_address: str,
    network: str,
) -> dict:
    """Send USDC, notify the platform, and wait for balance credit.

    Requires DEMO_WALLET_PRIVATE_KEY in the environment.
    Returns the balance response dict or exits on failure.
    """
    demo_key = os.environ.get("DEMO_WALLET_PRIVATE_KEY")
    if not demo_key:
        print(f"         {RED}✗ DEMO_WALLET_PRIVATE_KEY not set in environment.{RESET}")
        print(f"         {DIM}Set it in .env and ensure direnv exports it (dotenv .env in .envrc).{RESET}")
        sys.exit(1)

    # 1. Send USDC on-chain
    print(f"         {CYAN}Sending {amount} USDC on {network}...{RESET}")
    try:
        tx_hash = send_usdc_deposit(deposit_address, amount, network)
    except Exception as e:
        print(f"         {RED}✗ On-chain transfer failed: {e}{RESET}")
        sys.exit(1)

    net = NETWORKS.get(network, {})
    explorer = net.get("explorer", "")
    print(f"         {GREEN}✓ TX broadcast:{RESET} {tx_hash[:16]}...")
    if explorer:
        print(f"         {DIM}{explorer}/tx/{tx_hash}{RESET}")

    # 2. Wait for tx to be mined
    print(f"         {DIM}Waiting for transaction to be mined...", end="", flush=True)
    wait_for_tx_confirmation(tx_hash, network)
    print(f" {GREEN}mined!{RESET}")

    # 3. Notify the platform
    print(f"         {CYAN}Notifying platform of deposit...{RESET}")
    resp = agent_client.post(
        f"/agents/{agent_id}/wallet/deposit-notify",
        {"tx_hash": tx_hash},
    )
    if resp.status_code != 201:
        print(f"         {RED}✗ Deposit notify failed: {resp.status_code} — {resp.text}{RESET}")
        sys.exit(1)

    notify_data = resp.json()
    print(f"         {GREEN}✓ Deposit registered:{RESET} {notify_data['amount_usdc']} USDC — {notify_data['message']}")

    # 4. Wait for confirmations and balance credit
    print(f"         {DIM}Waiting for {notify_data['confirmations_required']} confirmations", end="", flush=True)
    bal = wait_for_balance(agent_client, agent_id, amount)

    if bal:
        print(f"         {GREEN}✓ Deposit confirmed and credited!{RESET} Balance: ${bal['balance']}")
        return bal

    print(f"         {RED}✗ Timed out waiting for deposit credit (120s){RESET}")
    print(f"         {DIM}The confirmation watcher may still be running. Check logs.{RESET}")
    if explorer:
        print(f"         {DIM}Verify tx: {explorer}/tx/{tx_hash}{RESET}")
    sys.exit(1)
