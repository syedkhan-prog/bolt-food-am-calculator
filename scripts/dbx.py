"""
Databricks Connection Class
Supports Personal Access Token (preferred — no browser popups) and OAuth fallback.

Usage:
    from dbx import DBX

    dbx = DBX()
    df = dbx.query("SELECT * FROM table LIMIT 10")
    dbx.close()

Or as context manager:
    with DBX() as dbx:
        df = dbx.query("SELECT * FROM table")

Authentication priority:
    1. access_token kwarg passed directly
    2. DATABRICKS_TOKEN env var
    3. ~/.databricks_token file
    4. Falls back to OAuth (opens browser on first run)
"""

from databricks import sql
import pandas as pd
import os
import time

SERVER_HOSTNAME = "bolt-incentives.cloud.databricks.com"
HTTP_PATH = "sql/protocolv1/o/2472566184436351/0221-081903-9ag4bh69"
TOKEN_FILE = os.path.expanduser("~/.databricks_token")
QUERY_RETRIES = 3
QUERY_RETRY_DELAY_SEC = 5


def _resolve_token(explicit_token=None):
    """Return a PAT if available from any source, else None (fall back to OAuth)."""
    if explicit_token:
        return explicit_token
    env_token = os.environ.get("DATABRICKS_TOKEN")
    if env_token:
        return env_token
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            tok = f.read().strip()
            if tok:
                return tok
    return None


class DBX:
    """Databricks connection wrapper that returns pandas DataFrames."""

    def __init__(self, http_path=None, access_token=None):
        token = _resolve_token(access_token)
        connect_args = dict(
            server_hostname=SERVER_HOSTNAME,
            http_path=http_path or HTTP_PATH,
        )
        if token:
            connect_args["access_token"] = token
        else:
            connect_args["auth_type"] = "databricks-oauth"
        self.conn = sql.connect(**connect_args)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def query(self, q, params=None, retries=QUERY_RETRIES):
        """Execute SQL query and return pandas DataFrame. Retries transient Spark errors."""
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                with self.conn.cursor() as cur:
                    cur.execute(q, params or None)
                    columns = [desc[0] for desc in cur.description]
                    return pd.DataFrame(cur.fetchall(), columns=columns)
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                transient = any(
                    x in msg for x in (
                        "indeterminate", "failed and retried", "temporarily unavailable",
                        "timeout", "connection", "session", "operation error",
                    )
                )
                if attempt < retries and transient:
                    print(f"  [dbx] Query failed (attempt {attempt}/{retries}), reconnecting...")
                    time.sleep(QUERY_RETRY_DELAY_SEC * attempt)
                    try:
                        self.conn.close()
                    except Exception:
                        pass
                    token = _resolve_token()
                    connect_args = dict(
                        server_hostname=SERVER_HOSTNAME,
                        http_path=HTTP_PATH,
                    )
                    if token:
                        connect_args["access_token"] = token
                    else:
                        connect_args["auth_type"] = "databricks-oauth"
                    self.conn = sql.connect(**connect_args)
                    continue
                raise
        raise last_err

    def query_to_csv(self, q, filepath, params=None):
        """Execute query and save directly to CSV."""
        df = self.query(q, params)
        df.to_csv(filepath, index=False)
        print(f"Saved {len(df)} rows to {filepath}")
        return df

    def close(self):
        self.conn.close()


if __name__ == "__main__":
    print("Testing connection...")
    with DBX() as dbx:
        df = dbx.query("SELECT 1 AS test")
        print("Connected successfully!" if len(df) else "Something went wrong")
    print("Done.")
