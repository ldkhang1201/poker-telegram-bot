from __future__ import annotations

import sqlite3
from typing import List


class SqliteTableRepository:
    """
    Table registry and membership management for poker tables.

    - `tables` stores table names.
    - `table_memberships` links users (by internal user_id) to tables.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_tables()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _ensure_tables(self) -> None:
        with self._get_connection() as conn:
            cur = conn.cursor()
            
            # Check if tables table exists and has correct schema
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tables'"
            )
            table_exists = cur.fetchone() is not None
            
            if table_exists:
                # Check if the table has the 'name' column
                cur.execute("PRAGMA table_info(tables)")
                columns = [row[1] for row in cur.fetchall()]
                if 'name' not in columns:
                    # Drop and recreate if schema is wrong
                    cur.execute("DROP TABLE IF EXISTS table_memberships")
                    cur.execute("DROP TABLE IF EXISTS tables")
                    table_exists = False
            
            if not table_exists:
                cur.execute(
                    """
                    CREATE TABLE tables (
                        name TEXT PRIMARY KEY
                    )
                    """
                )
            
            # Ensure table_memberships exists
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS table_memberships (
                    table_name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    PRIMARY KEY (table_name, user_id),
                    FOREIGN KEY (table_name) REFERENCES tables(name)
                )
                """
            )
            conn.commit()

    def create_table(self, name: str) -> bool:
        """
        Create a new table. Returns True if created, False if it already exists.
        """

        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO tables (name) VALUES (?)",
                    (name,),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Name already exists.
            return False

    def exists(self, name: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM tables WHERE name = ?", (name,))
            row = cur.fetchone()
            return row is not None

    def add_user_to_table(self, table_name: str, user_id: str) -> None:
        """
        Add a user to a table. No-op if already present.
        """

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO table_memberships (table_name, user_id)
                VALUES (?, ?)
                """,
                (table_name, user_id),
            )
            conn.commit()

    def get_user_ids_for_table(self, table_name: str) -> List[str]:
        """
        Return all user IDs that are members of the given table.
        """

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id FROM table_memberships WHERE table_name = ?",
                (table_name,),
            )
            rows = cur.fetchall()
            return [str(row[0]) for row in rows]

    def list_tables_for_user(self, user_id: str) -> List[str]:
        """
        Return all table names that the given user is a member of.
        """

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT table_name FROM table_memberships WHERE user_id = ? "
                "ORDER BY table_name",
                (user_id,),
            )
            rows = cur.fetchall()
            return [str(row[0]) for row in rows]

    def list_all_tables(self) -> List[str]:
        """
        Return all table names in the system.
        """
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM tables ORDER BY name")
            rows = cur.fetchall()
            return [str(row[0]) for row in rows]

