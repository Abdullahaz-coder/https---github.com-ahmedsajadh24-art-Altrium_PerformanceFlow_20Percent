import sqlite3
from pathlib import Path


DATABASE_PATH = Path(__file__).resolve().with_name("database.db")


def get_db_connection():
    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=10
    )

    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA foreign_keys = ON")

    connection.execute("PRAGMA busy_timeout = 10000")

    return connection
