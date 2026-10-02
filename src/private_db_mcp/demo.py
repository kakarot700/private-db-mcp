"""Deterministic synthetic data only. Never overwrites an existing file."""

import argparse
import os
import random
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path


def create_demo(path: Path, customers: int = 120) -> None:
    if not 1 <= customers <= 10_000:
        raise ValueError("Customer count must be between 1 and 10000.")
    path = path.absolute()
    # Exclusive creation avoids clobbering a user's database; permissions are private.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    rng = random.Random(42)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
            phone TEXT, password_hash TEXT, api_token TEXT, status TEXT NOT NULL,
            country_code TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
            amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
            currency TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
            card_last4 TEXT
        );
        CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id),
            event_type TEXT NOT NULL, severity TEXT NOT NULL, created_at TEXT NOT NULL,
            ip_address TEXT, message TEXT, auth_token TEXT
        );
        CREATE TABLE internal_secrets (id INTEGER PRIMARY KEY, credential TEXT);
        CREATE INDEX transactions_customer_idx ON transactions(customer_id);
        CREATE INDEX audit_logs_customer_idx ON audit_logs(customer_id);
        """)
        for i in range(1, customers + 1):
            stamp = (start + timedelta(hours=i)).isoformat()
            conn.execute(
                "INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    i,
                    f"Synthetic Person {i}",
                    f"person{i}@example.invalid",
                    f"+1-202-555-{i % 10000:04d}",
                    f"DEMO_HASH_{i}",
                    f"DEMO_TOKEN_{i}",
                    rng.choice(["active", "inactive"]),
                    rng.choice(["IN", "US", "GB"]),
                    stamp,
                ),
            )
            for j in range(3):
                n = (i - 1) * 3 + j + 1
                conn.execute(
                    "INSERT INTO transactions VALUES (?,?,?,?,?,?,?)",
                    (
                        n,
                        i,
                        rng.randrange(100, 50000),
                        "USD",
                        rng.choice(["settled", "pending", "refunded"]),
                        stamp,
                        "4242",
                    ),
                )
            for j in range(2):
                n = (i - 1) * 2 + j + 1
                conn.execute(
                    "INSERT INTO audit_logs VALUES (?,?,?,?,?,?,?,?)",
                    (
                        n,
                        i,
                        rng.choice(["login", "purchase", "logout"]),
                        "info",
                        stamp,
                        f"192.0.2.{i % 255}",
                        f"Private log for person{i}@example.invalid",
                        f"DEMO_AUTH_{i}",
                    ),
                )
        conn.execute("INSERT INTO internal_secrets VALUES (1, 'DEMO_ROOT_CREDENTIAL')")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--customers", type=int, default=120)
    args = parser.parse_args()
    create_demo(args.path, args.customers)
    print("Synthetic database created; no real customer data was used.")


if __name__ == "__main__":
    main()
