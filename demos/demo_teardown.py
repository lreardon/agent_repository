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

    agent_ids = [row[1] for row in accounts if row[1] is not None]
    account_emails = [row[0] for row in accounts]

    if not accounts:
        print(f"{YELLOW}No demo accounts found. Nothing to clean up.{RESET}")
        conn.close()
        return

    print(f"Found {len(accounts)} demo account(s), {len(agent_ids)} agent(s)")

    if agent_ids:
        agent_id_list = tuple(str(aid) for aid in agent_ids)
        placeholder = ",".join(["%s"] * len(agent_id_list))

        # Delete in dependency order
        tables_by_agent = [
            ("reviews", "reviewer_agent_id"),
            ("reviews", "reviewed_agent_id"),
            ("escrows", "job_id", "jobs", "job_id"),  # special: via jobs
            ("jobs", "buyer_agent_id"),
            ("jobs", "seller_agent_id"),
            ("listings", "agent_id"),
            ("withdrawals", "agent_id"),
            ("deposits", "agent_id"),
            ("wallet_balances", "agent_id"),
            ("deposit_addresses", "agent_id"),
        ]

        for entry in tables_by_agent:
            if len(entry) == 2:
                table, col = entry
                sql = f"DELETE FROM {table} WHERE {col} IN ({placeholder})"
                cur.execute(sql, agent_id_list)
            elif len(entry) == 4:
                # Join-based delete (e.g., escrows via jobs)
                table, fk_col, via_table, via_col = entry
                cur.execute(f"""
                    DELETE FROM {table} WHERE {fk_col} IN (
                        SELECT {via_col} FROM {via_table}
                        WHERE buyer_agent_id IN ({placeholder})
                           OR seller_agent_id IN ({placeholder})
                    )
                """, agent_id_list + agent_id_list)
            count = cur.rowcount
            if count > 0:
                print(f"  {GREEN}✓ Deleted {count} row(s) from {table}{RESET}")

        # Null out account agent references before deleting agents
        cur.execute("UPDATE accounts SET agent_id = NULL WHERE email = ANY(%s)", (DEMO_EMAILS,))

        # Delete agents
        cur.execute(f"DELETE FROM agents WHERE agent_id IN ({placeholder})", agent_id_list)
        print(f"  {GREEN}✓ Deleted {cur.rowcount} agent(s){RESET}")

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


if __name__ == "__main__":
    main()
