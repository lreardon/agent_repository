#!/usr/bin/env python3
"""Post-demo teardown: remove demo agents and accounts.

Cleans up all data created by the demo scripts (agents, jobs, listings,
reviews, wallet records, escrows, accounts, verifications).

Usage:
    python3 demo_teardown.py
"""

import os
import sys

from demo_db import get_connection

DEMO_EMAILS = ["alice-demo@arcoa.ai", "bob-demo@arcoa.ai"]

# ─── Colors ───
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def delete_where(cur, table: str, col: str, values: tuple) -> int:
    """Delete rows matching any of the given values. Returns count."""
    if not values:
        return 0
    placeholder = ",".join(["%s"] * len(values))
    cur.execute(f"DELETE FROM {table} WHERE {col} IN ({placeholder})", values)
    count = cur.rowcount
    if count > 0:
        print(f"  {GREEN}✓ Deleted {count} row(s) from {table}{RESET}")
    return count


def main() -> None:
    print(f"{BOLD}Demo Teardown — Cleaning up demo data{RESET}")
    print(f"{DIM}Connecting to database...{RESET}")

    conn = get_connection()
    conn.autocommit = False
    cur = conn.cursor()

    # Find demo agent IDs
    cur.execute("""
        SELECT acc.email, acc.agent_id, acc.account_id
        FROM accounts acc
        WHERE acc.email = ANY(%s)
    """, (DEMO_EMAILS,))
    accounts = cur.fetchall()

    agent_ids = tuple(str(row[1]) for row in accounts if row[1] is not None)

    if not accounts:
        print(f"{YELLOW}No demo accounts found. Nothing to clean up.{RESET}")
        conn.close()
        return

    print(f"Found {len(accounts)} demo account(s), {len(agent_ids)} agent(s)")

    if agent_ids:
        # Find job IDs for these agents (needed for escrow + review cleanup)
        placeholder = ",".join(["%s"] * len(agent_ids))
        cur.execute(f"""
            SELECT job_id FROM jobs
            WHERE client_agent_id IN ({placeholder})
               OR seller_agent_id IN ({placeholder})
        """, agent_ids + agent_ids)
        job_ids = tuple(str(row[0]) for row in cur.fetchall())

        # Delete in dependency order
        if job_ids:
            delete_where(cur, "escrow_audit_log", "escrow_id",
                         _select_ids(cur, "escrow_accounts", "escrow_id", "job_id", job_ids))
            delete_where(cur, "escrow_accounts", "job_id", job_ids)
            delete_where(cur, "reviews", "job_id", job_ids)

        delete_where(cur, "jobs", "client_agent_id", agent_ids)
        delete_where(cur, "jobs", "seller_agent_id", agent_ids)
        delete_where(cur, "listings", "seller_agent_id", agent_ids)
        delete_where(cur, "withdrawal_requests", "agent_id", agent_ids)
        delete_where(cur, "deposit_transactions", "agent_id", agent_ids)
        delete_where(cur, "deposit_addresses", "agent_id", agent_ids)

        # Null out account agent references before deleting agents
        cur.execute("UPDATE accounts SET agent_id = NULL WHERE email = ANY(%s)", (DEMO_EMAILS,))

        delete_where(cur, "agents", "agent_id", agent_ids)

    # Delete verifications and accounts
    cur.execute("DELETE FROM email_verifications WHERE email = ANY(%s)", (DEMO_EMAILS,))
    if cur.rowcount:
        print(f"  {GREEN}✓ Deleted {cur.rowcount} verification(s){RESET}")

    cur.execute("DELETE FROM accounts WHERE email = ANY(%s)", (DEMO_EMAILS,))
    if cur.rowcount:
        print(f"  {GREEN}✓ Deleted {cur.rowcount} account(s){RESET}")

    conn.commit()
    cur.close()
    conn.close()

    # Clean up .env.demo
    env_file = os.path.join(os.path.dirname(__file__), ".env.demo")
    if os.path.exists(env_file):
        os.remove(env_file)
        print(f"  {GREEN}✓ Removed .env.demo{RESET}")

    print(f"\n{GREEN}{BOLD}✓ Teardown complete!{RESET}")


def _select_ids(cur, table: str, id_col: str, match_col: str, values: tuple) -> tuple:
    """Helper: select IDs from a table matching given values."""
    if not values:
        return ()
    placeholder = ",".join(["%s"] * len(values))
    cur.execute(f"SELECT {id_col} FROM {table} WHERE {match_col} IN ({placeholder})", values)
    return tuple(str(row[0]) for row in cur.fetchall())


if __name__ == "__main__":
    main()
