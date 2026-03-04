"""Shared database connection for demo scripts.

Uses direct psycopg2 connection via Cloud SQL Proxy.
"""

import os

import psycopg2


def get_connection():
    """Return a DB-API 2.0 connection to the staging database."""
    return psycopg2.connect(
        host=os.environ.get("DEMO_DB_HOST", "127.0.0.1"),
        port=os.environ.get("DEMO_DB_PORT", "5433"),
        dbname=os.environ.get("DEMO_DB_NAME", "agent_registry"),
        user=os.environ.get("DEMO_DB_USER", "api_user"),
        password=os.environ["DEMO_DB_PASSWORD"],
    )
