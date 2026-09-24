"""Login throttling checks using a disposable, minimal database."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

import app as application


class LoginThrottleTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name, "login-test.db")
        connection = sqlite3.connect(self.database_path)
        connection.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                role TEXT NOT NULL
            );
            CREATE TABLE employees (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL
            );
        """)
        for number, email in enumerate(
            ("it-supervisor@altrium.com", "fin-supervisor@altrium.com"), 1
        ):
            connection.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, 'Supervisor')",
                (number, f"Supervisor {number}", email,
                 generate_password_hash("TestOnly-Password-2026!")),
            )
            connection.execute(
                "INSERT INTO employees VALUES (?, ?, 'Active')", (number, number)
            )
        connection.commit()
        connection.close()

        self.original_connection_factory = application.get_db_connection
        self.original_testing = application.app.config["TESTING"]
        application.get_db_connection = self.get_test_connection
        application.app.config["TESTING"] = True
        application.login_attempts.clear()
        self.client = application.app.test_client()

    def tearDown(self):
        application.get_db_connection = self.original_connection_factory
        application.app.config["TESTING"] = self.original_testing
        application.login_attempts.clear()
        self.temporary_directory.cleanup()

    def get_test_connection(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def sign_in(self, email, password):
        return self.client.post(
            "/", data={"email": email, "password": password},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

    def test_failures_for_one_account_do_not_block_another_at_same_ip(self):
        for _ in range(application.LOGIN_ATTEMPT_LIMIT):
            response = self.sign_in("it-supervisor@altrium.com", "wrong")
            self.assertEqual(response.status_code, 200)
        locked = self.sign_in(
            "it-supervisor@altrium.com", "TestOnly-Password-2026!"
        )
        self.assertEqual(locked.status_code, 429)
        other = self.sign_in(
            "fin-supervisor@altrium.com", "TestOnly-Password-2026!"
        )
        self.assertEqual(other.status_code, 302)

    def test_success_clears_failed_attempts_for_that_account(self):
        email = "it-supervisor@altrium.com"
        for _ in range(2):
            self.assertEqual(self.sign_in(email, "wrong").status_code, 200)
        self.assertEqual(
            self.sign_in(email, "TestOnly-Password-2026!").status_code, 302
        )
        self.assertNotIn(("127.0.0.1", email), application.login_attempts)


if __name__ == "__main__":
    unittest.main()
