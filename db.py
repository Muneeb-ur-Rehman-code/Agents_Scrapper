from __future__ import annotations
# ============================================================
#  db.py — PostgreSQL connection pool
# ============================================================
import psycopg2
import psycopg2.extras
import logging
import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("AgentScraper")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "localhost"),
    "port":     int(os.getenv("DB_PORT", 5432)),
    "dbname":   os.getenv("DB_NAME",     "scraper_db"),
    "user":     os.getenv("DB_USER",     "scraper_user"),
    "password": os.getenv("DB_PASSWORD", "scraper_pass_2024"),
}


def get_conn():
    """Return a new PostgreSQL connection."""
    return psycopg2.connect(**DB_CONFIG)


def execute(query: str, params=None, fetch: str = "none"):
    """
    Execute a query.
    fetch: "none" | "one" | "all"
    """
    conn = get_conn()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params or ())
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
    finally:
        conn.close()


def test_connection() -> bool:
    """Test if DB connection works."""
    try:
        result = execute("SELECT 1 AS ok", fetch="one")
        logger.info("✅  Database connection OK")
        return True
    except Exception as e:
        logger.error(f"❌  Database connection failed: {e}")
        return False