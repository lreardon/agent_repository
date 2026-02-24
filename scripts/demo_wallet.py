"""Shared USDC wallet utilities for demo scripts.

Handles sending testnet USDC and waiting for deposit confirmation.
Falls back to the dev deposit endpoint if wallet config is missing.
"""

import json
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


def has_wallet_config() -> bool:
    """Check if testnet wallet is configured for real USDC deposits."""
    return bool(os.environ.get("DEMO_WALLET_PRIVATE_KEY"))


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
    return tx_hash.hex()


def wait_for_deposit_credit(
    agent_client,  # AgentClient instance
    agent_id: str,
    expected_balance: str,
    timeout_seconds: int = 90,
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
                print()  # newline after dots
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


def deposit_usdc_or_fallback(
    agent_client,
    agent_id: str,
    amount: str,
    deposit_address: str,
    network: str,
) -> dict:
    """Send real testnet USDC if configured, otherwise use dev deposit endpoint.

    Returns the balance response dict.
    """
    if has_wallet_config():
        print(f"         {CYAN}Sending {amount} USDC on {network}...{RESET}")
        try:
            tx_hash = send_usdc_deposit(deposit_address, amount, network)
            net = NETWORKS.get(network, {})
            explorer = net.get("explorer", "")
            print(f"         {GREEN}✓ TX broadcast:{RESET} {tx_hash[:16]}...")
            if explorer:
                print(f"         {DIM}{explorer}/tx/{tx_hash}{RESET}")

            print(f"         {DIM}Waiting for chain monitor to detect and confirm deposit", end="", flush=True)
            bal = wait_for_deposit_credit(agent_client, agent_id, amount)

            if bal:
                print(f"         {GREEN}✓ Deposit confirmed!{RESET} Balance: ${bal['balance']}")
                return bal
            else:
                print(f"         {RED}✗ Timed out waiting for deposit confirmation{RESET}")
                print(f"         {DIM}The chain monitor may not be running or confirmations are pending.{RESET}")
                print(f"         {DIM}Falling back to dev deposit...{RESET}")

        except Exception as e:
            print(f"         {RED}✗ On-chain transfer failed: {e}{RESET}")
            print(f"         {DIM}Falling back to dev deposit...{RESET}")

    # Fallback: dev deposit
    print(f"         {DIM}(Using dev deposit endpoint){RESET}")
    resp = agent_client.post(f"/agents/{agent_id}/deposit", {"amount": amount})
    if resp.status_code != 200:
        print(f"         {RED}✗ Dev deposit failed: {resp.status_code} — {resp.text}{RESET}")
        sys.exit(1)
    return resp.json()
