import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import app as application
from database import DATABASE_PATH


class WorkflowRegressionTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(
            self.temporary_directory.name,
            "test.db"
        )
        shutil.copy2(DATABASE_PATH, self.database_path)

        self.original_connection_factory = application.get_db_connection
        application.get_db_connection = self.get_test_connection
        application.app.config.update(TESTING=True)
        self.client = application.app.test_client()

    def tearDown(self):
        application.get_db_connection = self.original_connection_factory
        self.temporary_directory.cleanup()

    def get_test_connection(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def sign_in_as(self, user_id, role, name="Test User"):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["user_role"] = role
            session["user_name"] = name

    def test_database_connections_enforce_foreign_keys(self):
        connection = self.get_test_connection()
        try:
            self.assertEqual(
                connection.execute("PRAGMA foreign_keys").fetchone()[0],
                1
            )
        finally:
            connection.close()

    def test_cycle_activation_creates_one_action_per_stage(self):
        connection = self.get_test_connection()
        try:
            hr = connection.execute(
                "SELECT id, full_name FROM users WHERE role = 'HR' LIMIT 1"
            ).fetchone()
            connection.execute(
                """
                UPDATE review_cycles
                SET start_date = '2026-08-01', end_date = '2026-12-30'
                WHERE id = 3
                """
            )
            connection.commit()
        finally:
            connection.close()

        self.sign_in_as(hr["id"], "HR", hr["full_name"])
        response = self.client.post("/review-cycles/3/activate")
        self.assertEqual(response.status_code, 302)

        connection = self.get_test_connection()
        try:
            cycle = connection.execute(
                "SELECT status FROM review_cycles WHERE id = 3"
            ).fetchone()
            review = connection.execute(
                "SELECT id FROM employee_reviews WHERE review_cycle_id = 3"
            ).fetchone()
            actions = connection.execute(
                """
                SELECT action_type, COUNT(*) AS total
                FROM review_actions
                WHERE employee_review_id = ?
                GROUP BY action_type
                """,
                (review["id"],)
            ).fetchall()

            self.assertEqual(cycle["status"], "Active")
            self.assertEqual(
                {row["action_type"]: row["total"] for row in actions},
                {
                    "SELF_ASSESSMENT": 1,
                    "SUPERVISOR_MONITORING": 1
                }
            )
        finally:
            connection.close()

    def test_malformed_self_assessment_returns_400(self):
        connection = self.get_test_connection()
        try:
            employee = connection.execute(
                """
                SELECT employees.user_id
                FROM employee_reviews
                JOIN employees
                    ON employees.id = employee_reviews.employee_id
                WHERE employee_reviews.id = 2
                """
            ).fetchone()
        finally:
            connection.close()

        self.sign_in_as(employee["user_id"], "Employee")
        invalid_payloads = (
            [],
            {"overall_summary": ["invalid"]}
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/reviews/2/self-assessment/save",
                    json=payload
                )
                self.assertEqual(response.status_code, 400)

    def test_invalid_supervisor_input_does_not_crash(self):
        connection = self.get_test_connection()
        try:
            hr = connection.execute(
                "SELECT id FROM users WHERE role = 'HR' LIMIT 1"
            ).fetchone()
        finally:
            connection.close()

        self.sign_in_as(hr["id"], "HR")
        response = self.client.post(
            "/employees/add",
            data={
                "full_name": "Test Employee",
                "email": "test.employee@example.com",
                "employee_code": "TEST-001",
                "department": "Quality",
                "job_title": "Tester",
                "supervisor_id": "not-a-number"
            }
        )
        self.assertEqual(response.status_code, 302)

    def test_cycle_closure_requires_completed_reviews(self):
        connection = self.get_test_connection()
        try:
            hr = connection.execute(
                "SELECT id FROM users WHERE role = 'HR' LIMIT 1"
            ).fetchone()
            connection.execute(
                """
                UPDATE employee_reviews
                SET status = 'Completed'
                WHERE review_cycle_id = 2
                """
            )
            connection.commit()
        finally:
            connection.close()

        self.sign_in_as(hr["id"], "HR")
        response = self.client.post("/review-cycles/2/close")
        self.assertEqual(response.status_code, 302)

        connection = self.get_test_connection()
        try:
            cycle = connection.execute(
                "SELECT status FROM review_cycles WHERE id = 2"
            ).fetchone()
            self.assertEqual(cycle["status"], "Closed")
        finally:
            connection.close()

    def test_hr_can_reassign_pending_manager_approval(self):
        connection = self.get_test_connection()
        try:
            hr = connection.execute(
                "SELECT id FROM users WHERE role = 'HR' LIMIT 1"
            ).fetchone()
            cursor = connection.execute(
                """
                INSERT INTO users (full_name, email, password, role)
                VALUES ('Second Manager', 'manager.two@example.com', 'unused', 'Manager')
                """
            )
            second_manager_id = cursor.lastrowid
            connection.execute(
                """
                UPDATE manager_approvals
                SET status = 'Pending', decision_note = NULL, decided_at = NULL
                WHERE employee_review_id = 1
                """
            )
            connection.execute(
                """
                UPDATE employee_reviews
                SET status = 'Manager Approval Pending'
                WHERE id = 1
                """
            )
            connection.commit()
        finally:
            connection.close()

        self.sign_in_as(hr["id"], "HR")
        response = self.client.post(
            "/reviews/1/manager-approval/assign",
            data={"manager_id": str(second_manager_id)}
        )
        self.assertEqual(response.status_code, 302)

        connection = self.get_test_connection()
        try:
            approval = connection.execute(
                """
                SELECT manager_id
                FROM manager_approvals
                WHERE employee_review_id = 1
                """
            ).fetchone()
            action = connection.execute(
                """
                SELECT status
                FROM review_actions
                WHERE employee_review_id = 1
                AND assigned_to = ?
                AND action_type = 'MANAGER_APPROVAL'
                """,
                (second_manager_id,)
            ).fetchone()

            self.assertEqual(approval["manager_id"], second_manager_id)
            self.assertEqual(action["status"], "Pending")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
