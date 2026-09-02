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
        self.reset_test_data()

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

    def reset_test_data(self):
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            tables = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                AND name NOT LIKE 'sqlite_%'
                AND name != 'users'
                """
            ).fetchall()
            for (table_name,) in tables:
                connection.execute(f'DELETE FROM "{table_name}"')
            connection.execute("DELETE FROM users WHERE role != 'HR'")
            connection.commit()
            connection.execute("PRAGMA foreign_keys = ON")

            supervisor_cursor = connection.execute(
                """
                INSERT INTO users (full_name, email, password, role)
                VALUES (?, ?, ?, 'Supervisor')
                """,
                (
                    "Regression Supervisor",
                    "regression.supervisor@altrium.com",
                    "unused",
                ),
            )
            self.supervisor_user_id = supervisor_cursor.lastrowid
            supervisor_profile = connection.execute(
                """
                INSERT INTO employees
                    (user_id, employee_code, department, job_title,
                     hire_date, supervisor_id, status)
                VALUES (?, 'REG-SUP-001', 'Operations', 'Supervisor',
                        '2026-01-01', NULL, 'Active')
                """,
                (self.supervisor_user_id,),
            )
            self.supervisor_employee_id = supervisor_profile.lastrowid
            connection.commit()
        finally:
            connection.close()

    def create_user(self, role, suffix):
        connection = self.get_test_connection()
        try:
            cursor = connection.execute(
                """
                INSERT INTO users (full_name, email, password, role)
                VALUES (?, ?, 'unused', ?)
                """,
                (
                    f"Regression {suffix}",
                    f"regression.{suffix.lower()}@altrium.com",
                    role,
                ),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def create_employee(self, suffix, supervisor_id=None):
        user_id = self.create_user("Employee", suffix)
        connection = self.get_test_connection()
        try:
            cursor = connection.execute(
                """
                INSERT INTO employees
                    (user_id, employee_code, department, job_title,
                     hire_date, supervisor_id, status)
                VALUES (?, ?, 'Operations', 'Analyst', '2026-01-15', ?, 'Active')
                """,
                (
                    user_id,
                    f"REG-{suffix.upper()}-001",
                    supervisor_id or self.supervisor_user_id,
                ),
            )
            connection.commit()
            return user_id, cursor.lastrowid
        finally:
            connection.close()

    def create_cycle_review(self, review_status="Not Started", cycle_status="Active"):
        employee_user_id, employee_id = self.create_employee("Subject")
        connection = self.get_test_connection()
        try:
            hr = connection.execute(
                "SELECT id FROM users WHERE role = 'HR' LIMIT 1"
            ).fetchone()
            cycle_cursor = connection.execute(
                """
                INSERT INTO review_cycles
                    (cycle_name, cycle_year, cycle_number, start_date,
                     end_date, status, created_by)
                VALUES ('Regression Cycle', 2026, 1, '2026-08-01',
                        '2026-12-30', ?, ?)
                """,
                (cycle_status, hr["id"]),
            )
            cycle_id = cycle_cursor.lastrowid
            assignment_cursor = connection.execute(
                """
                INSERT INTO review_cycle_employees
                    (review_cycle_id, employee_id, assigned_by,
                     participation_status)
                VALUES (?, ?, ?, 'Assigned')
                """,
                (cycle_id, employee_id, hr["id"]),
            )
            review_cursor = connection.execute(
                """
                INSERT INTO employee_reviews
                    (assignment_id, review_cycle_id, employee_id,
                     supervisor_id, employee_name_snapshot,
                     employee_code_snapshot, department_snapshot,
                     job_title_snapshot, status)
                VALUES (?, ?, ?, ?, 'Regression Subject',
                        'REG-SUBJECT-001', 'Operations', 'Analyst', ?)
                """,
                (
                    assignment_cursor.lastrowid,
                    cycle_id,
                    employee_id,
                    self.supervisor_user_id,
                    review_status,
                ),
            )
            connection.commit()
            return {
                "hr_id": hr["id"],
                "cycle_id": cycle_id,
                "review_id": review_cursor.lastrowid,
                "employee_id": employee_id,
                "employee_user_id": employee_user_id,
            }
        finally:
            connection.close()

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
        employee_user_id, employee_id = self.create_employee("Activation")
        connection = self.get_test_connection()
        try:
            hr = connection.execute(
                "SELECT id, full_name FROM users WHERE role = 'HR' LIMIT 1"
            ).fetchone()
            cycle_cursor = connection.execute(
                """
                INSERT INTO review_cycles
                    (cycle_name, cycle_year, cycle_number, start_date,
                     end_date, status, created_by)
                VALUES ('Activation Cycle', 2026, 1, '2026-08-01',
                        '2026-12-30', 'Scheduled', ?)
                """,
                (hr["id"],),
            )
            cycle_id = cycle_cursor.lastrowid
            connection.execute(
                """
                INSERT INTO review_cycle_employees
                    (review_cycle_id, employee_id, assigned_by,
                     participation_status)
                VALUES (?, ?, ?, 'Assigned')
                """,
                (cycle_id, employee_id, hr["id"]),
            )
            connection.execute(
                """
                INSERT INTO performance_items
                    (employee_id, item_type, title, description, created_by)
                VALUES (?, 'Responsibility', 'Activation baseline',
                        'Regression coverage', ?)
                """,
                (employee_id, self.supervisor_user_id),
            )
            connection.commit()
        finally:
            connection.close()

        self.sign_in_as(hr["id"], "HR", hr["full_name"])
        response = self.client.post(f"/review-cycles/{cycle_id}/activate")
        self.assertEqual(response.status_code, 302)

        connection = self.get_test_connection()
        try:
            cycle = connection.execute(
                "SELECT status FROM review_cycles WHERE id = ?",
                (cycle_id,),
            ).fetchone()
            review = connection.execute(
                "SELECT id FROM employee_reviews WHERE review_cycle_id = ?",
                (cycle_id,),
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
        employee_user_id, _ = self.create_employee("Malformed")
        self.sign_in_as(employee_user_id, "Employee")
        invalid_payloads = (
            [],
            {"overall_summary": ["invalid"]}
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/reviews/999/self-assessment/save",
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

        invalid_domain_response = self.client.post(
            "/employees/add",
            data={
                "full_name": "External Domain User",
                "email": "external.user@example.com",
                "employee_code": "TEST-EXT-001",
                "hire_date": "2026-09-01",
                "department": "Operations",
                "job_title": "Analyst",
                "role": "Employee",
                "supervisor_id": "",
                "password": "Temporary123!"
            }
        )
        self.assertEqual(invalid_domain_response.status_code, 302)

        connection = self.get_test_connection()
        try:
            rejected_account = connection.execute(
                "SELECT id FROM users WHERE email = ?",
                ("external.user@example.com",)
            ).fetchone()
            self.assertIsNone(rejected_account)
        finally:
            connection.close()

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

    def test_hr_can_create_supervisor_account_from_employee_form(self):
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
                "full_name": "Workflow Test Supervisor",
                "email": "workflow.supervisor@altrium.com",
                "employee_code": "TEST-SUP-001",
                "hire_date": "2026-09-01",
                "department": "Operations",
                "job_title": "Operations Supervisor",
                "role": "Supervisor",
                "supervisor_id": "not-a-number",
                "password": "Temporary123!"
            }
        )
        self.assertEqual(response.status_code, 302)

        connection = self.get_test_connection()
        try:
            account = connection.execute(
                """
                SELECT
                    users.id AS user_id,
                    users.role,
                    employees.id,
                    employees.supervisor_id
                FROM users
                JOIN employees ON employees.user_id = users.id
                WHERE users.email = ?
                """,
                ("workflow.supervisor@altrium.com",)
            ).fetchone()
            self.assertIsNotNone(account)
            self.assertEqual(account["role"], "Supervisor")
            self.assertIsNone(account["supervisor_id"])
            supervisor_profile_id = account["id"]
            supervisor_user_id = account["user_id"]
        finally:
            connection.close()

        create_employee_response = self.client.post(
            "/employees/add",
            data={
                "full_name": "Workflow Test Employee",
                "email": "workflow.employee@altrium.com",
                "employee_code": "TEST-EMP-001",
                "hire_date": "2026-09-01",
                "department": "Operations",
                "job_title": "Operations Analyst",
                "role": "Employee",
                "supervisor_id": str(supervisor_user_id),
                "password": "Temporary123!"
            }
        )
        self.assertEqual(create_employee_response.status_code, 302)

        connection = self.get_test_connection()
        try:
            employee_profile_id = connection.execute(
                """
                SELECT employees.id
                FROM employees
                JOIN users ON users.id = employees.user_id
                WHERE users.email = 'workflow.employee@altrium.com'
                """
            ).fetchone()["id"]
        finally:
            connection.close()

        edit_employee_response = self.client.post(
            f"/employees/{employee_profile_id}/edit",
            data={
                "full_name": "Workflow Test Employee Updated",
                "email": "workflow.employee.updated@altrium.com",
                "employee_code": "TEST-EMP-001",
                "hire_date": "2026-09-01",
                "department": "Operations",
                "job_title": "Senior Operations Analyst",
                "supervisor_id": str(supervisor_user_id),
                "status": "Active"
            }
        )
        self.assertEqual(edit_employee_response.status_code, 302)

        profile_response = self.client.get(
            f"/employees/{supervisor_profile_id}"
        )
        self.assertEqual(profile_response.status_code, 200)
        profile_html = profile_response.get_data(as_text=True)
        self.assertIn("SUPERVISOR PROFILE", profile_html)
        self.assertIn("Supervisor workspace", profile_html)
        self.assertNotIn("Assigned Supervisor", profile_html)
        self.assertNotIn("A supervisor has not been assigned", profile_html)

        edit_response = self.client.post(
            f"/employees/{supervisor_profile_id}/edit",
            data={
                "full_name": "Workflow Test Supervisor Updated",
                "email": "workflow.supervisor.updated@altrium.com",
                "employee_code": "TEST-SUP-001",
                "hire_date": "2026-09-01",
                "department": "Operations",
                "job_title": "Senior Operations Supervisor",
                "supervisor_id": "",
                "status": "Active"
            }
        )
        self.assertEqual(edit_response.status_code, 302)

        connection = self.get_test_connection()
        try:
            updated_account = connection.execute(
                """
                SELECT users.full_name, users.role, employees.supervisor_id
                FROM users
                JOIN employees ON employees.user_id = users.id
                WHERE employees.id = ?
                """,
                (supervisor_profile_id,)
            ).fetchone()
            self.assertEqual(
                updated_account["full_name"],
                "Workflow Test Supervisor Updated"
            )
            self.assertEqual(updated_account["role"], "Supervisor")
            self.assertIsNone(updated_account["supervisor_id"])
        finally:
            connection.close()

    def test_cycle_closure_requires_completed_reviews(self):
        fixture = self.create_cycle_review(
            review_status="Completed",
            cycle_status="Active",
        )

        self.sign_in_as(fixture["hr_id"], "HR")
        response = self.client.post(
            f"/review-cycles/{fixture['cycle_id']}/close"
        )
        self.assertEqual(response.status_code, 302)

        connection = self.get_test_connection()
        try:
            cycle = connection.execute(
                "SELECT status FROM review_cycles WHERE id = ?",
                (fixture["cycle_id"],),
            ).fetchone()
            self.assertEqual(cycle["status"], "Closed")
        finally:
            connection.close()

    def test_hr_can_reassign_pending_manager_approval(self):
        fixture = self.create_cycle_review(
            review_status="Manager Approval Pending",
            cycle_status="Active",
        )
        connection = self.get_test_connection()
        try:
            hr = connection.execute(
                "SELECT id FROM users WHERE role = 'HR' LIMIT 1"
            ).fetchone()
            first_manager = connection.execute(
                """
                INSERT INTO users (full_name, email, password, role)
                VALUES ('First Manager', 'manager.one@altrium.com', 'unused', 'Manager')
                """
            )
            second_manager = connection.execute(
                """
                INSERT INTO users (full_name, email, password, role)
                VALUES ('Second Manager', 'manager.two@altrium.com', 'unused', 'Manager')
                """
            )
            second_manager_id = second_manager.lastrowid
            connection.execute(
                """
                INSERT INTO supervisor_evaluations
                    (employee_review_id, supervisor_id, status,
                     overall_rating, performance_summary, key_strengths,
                     development_priorities, support_plan, recommendation)
                VALUES (?, ?, 'Submitted', 4, 'Summary', 'Strengths',
                        'Priorities', 'Support', 'Meets Expectations')
                """,
                (fixture["review_id"], self.supervisor_user_id),
            )
            connection.execute(
                """
                INSERT INTO manager_approvals
                    (employee_review_id, manager_id, status)
                VALUES (?, ?, 'Pending')
                """,
                (fixture["review_id"], first_manager.lastrowid),
            )
            connection.commit()
        finally:
            connection.close()

        self.sign_in_as(hr["id"], "HR")
        response = self.client.post(
            f"/reviews/{fixture['review_id']}/manager-approval/assign",
            data={"manager_id": str(second_manager_id)}
        )
        self.assertEqual(response.status_code, 302)

        connection = self.get_test_connection()
        try:
            approval = connection.execute(
                """
                SELECT manager_id
                FROM manager_approvals
                WHERE employee_review_id = ?
                """,
                (fixture["review_id"],),
            ).fetchone()
            action = connection.execute(
                """
                SELECT status
                FROM review_actions
                WHERE employee_review_id = ?
                AND assigned_to = ?
                AND action_type = 'MANAGER_APPROVAL'
                """,
                (fixture["review_id"], second_manager_id),
            ).fetchone()

            self.assertEqual(approval["manager_id"], second_manager_id)
            self.assertEqual(action["status"], "Pending")
        finally:
            connection.close()

    def test_hr_can_change_employee_status_without_deleting_history(self):
        connection = self.get_test_connection()
        try:
            hr = connection.execute(
                "SELECT id FROM users WHERE role = 'HR' LIMIT 1"
            ).fetchone()
            supervisor = connection.execute(
                "SELECT id FROM users WHERE role = 'Supervisor' LIMIT 1"
            ).fetchone()
            user_cursor = connection.execute(
                """
                INSERT INTO users (full_name, email, password, role)
                VALUES ('Status Test Employee', 'status.test@example.com', 'unused', 'Employee')
                """
            )
            employee_user_id = user_cursor.lastrowid
            employee_cursor = connection.execute(
                """
                INSERT INTO employees
                    (user_id, employee_code, department, job_title,
                     hire_date, supervisor_id, status)
                VALUES (?, 'STATUS-001', 'Operations', 'Analyst',
                        '2026-01-15', ?, 'Active')
                """,
                (employee_user_id, supervisor["id"])
            )
            employee_id = employee_cursor.lastrowid
            connection.execute(
                """
                INSERT INTO performance_items
                    (employee_id, item_type, title, description, created_by)
                VALUES (?, 'Goal', 'Preserved goal', 'Historical record', ?)
                """,
                (employee_id, supervisor["id"])
            )
            connection.commit()
        finally:
            connection.close()

        self.sign_in_as(hr["id"], "HR")
        response = self.client.post(
            f"/employees/{employee_id}/edit",
            data={
                "full_name": "Status Test Employee",
                "employee_code": "STATUS-001",
                "email": "status.test@altrium.com",
                "department": "Operations",
                "job_title": "Analyst",
                "hire_date": "2026-01-15",
                "supervisor_id": str(supervisor["id"]),
                "status": "Inactive",
            },
        )
        self.assertEqual(response.status_code, 302)

        connection = self.get_test_connection()
        try:
            employee = connection.execute(
                "SELECT status FROM employees WHERE id = ?",
                (employee_id,)
            ).fetchone()
            history_count = connection.execute(
                "SELECT COUNT(*) FROM performance_items WHERE employee_id = ?",
                (employee_id,)
            ).fetchone()[0]
            self.assertEqual(employee["status"], "Inactive")
            self.assertEqual(history_count, 1)
        finally:
            connection.close()

    def test_inactive_workspace_uses_warning_status_and_one_flash_message(self):
        connection = self.get_test_connection()
        try:
            supervisor = connection.execute(
                "SELECT id, full_name FROM users WHERE role = 'Supervisor' LIMIT 1"
            ).fetchone()
            user_cursor = connection.execute(
                """
                INSERT INTO users (full_name, email, password, role)
                VALUES ('Inactive Display Test', 'inactive.display@example.com', 'unused', 'Employee')
                """
            )
            employee_cursor = connection.execute(
                """
                INSERT INTO employees
                    (user_id, employee_code, department, job_title,
                     hire_date, supervisor_id, status)
                VALUES (?, 'INACTIVE-001', 'Finance', 'Analyst',
                        '2026-01-15', ?, 'Inactive')
                """,
                (user_cursor.lastrowid, supervisor["id"])
            )
            employee_id = employee_cursor.lastrowid
            connection.commit()
        finally:
            connection.close()

        self.sign_in_as(
            supervisor["id"],
            "Supervisor",
            supervisor["full_name"]
        )
        with self.client.session_transaction() as session:
            session["_flashes"] = [
                ("success", "Expectation created successfully.")
            ]

        response = self.client.get(
            f"/my-team/{employee_id}/performance"
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertEqual(
            html.count("Expectation created successfully."),
            1
        )
        self.assertIn("inactive-blueprint-status", html)

    def test_complete_workflow_from_setup_to_closed_cycle(self):
        subject_user_id, subject_employee_id = self.create_employee("E2ESubject")
        peer_user_id, _ = self.create_employee("E2EPeer")
        manager_user_id = self.create_user("Manager", "E2EManager")

        connection = self.get_test_connection()
        try:
            hr = connection.execute(
                "SELECT id, full_name FROM users WHERE role = 'HR' LIMIT 1"
            ).fetchone()
        finally:
            connection.close()

        self.sign_in_as(
            self.supervisor_user_id,
            "Supervisor",
            "Regression Supervisor",
        )
        blueprint_items = (
            {
                "item_type": "Responsibility",
                "title": "Deliver reliable application features",
                "description": "Own implementation quality and delivery.",
                "target": "",
                "due_date": "",
            },
            {
                "item_type": "Expectation",
                "title": "Collaborate constructively",
                "description": "Communicate risks and support the team.",
                "target": "",
                "due_date": "",
            },
            {
                "item_type": "KPI",
                "title": "Complete agreed sprint work",
                "description": "Track reliable delivery across the sprint.",
                "target": "90%",
                "due_date": "",
            },
            {
                "item_type": "Goal",
                "title": "Improve deployment knowledge",
                "description": "Complete a deployment learning objective.",
                "target": "Demonstrate one deployment improvement",
                "due_date": "2026-12-15",
            },
        )
        for item in blueprint_items:
            response = self.client.post(
                f"/my-team/{subject_employee_id}/performance/add",
                data=item,
            )
            self.assertEqual(response.status_code, 302)

        self.sign_in_as(hr["id"], "HR", hr["full_name"])
        response = self.client.post(
            "/review-cycles/add",
            data={
                "cycle_name": "End-to-End Review Cycle",
                "cycle_year": "2026",
                "cycle_number": "1",
                "start_date": "2026-08-01",
                "end_date": "2026-12-30",
            },
        )
        self.assertEqual(response.status_code, 302)

        connection = self.get_test_connection()
        try:
            cycle_id = connection.execute(
                "SELECT id FROM review_cycles WHERE cycle_name = ?",
                ("End-to-End Review Cycle",),
            ).fetchone()["id"]
        finally:
            connection.close()

        self.assertEqual(
            self.client.post(
                f"/review-cycles/{cycle_id}/assign",
                data={"employee_ids": str(subject_employee_id)},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(f"/review-cycles/{cycle_id}/schedule").status_code,
            302,
        )
        self.assertEqual(
            self.client.post(f"/review-cycles/{cycle_id}/activate").status_code,
            302,
        )

        connection = self.get_test_connection()
        try:
            review = connection.execute(
                """
                SELECT id, status
                FROM employee_reviews
                WHERE review_cycle_id = ? AND employee_id = ?
                """,
                (cycle_id, subject_employee_id),
            ).fetchone()
            review_id = review["id"]
            self.assertEqual(review["status"], "Not Started")
            plan_item_ids = [
                row["id"]
                for row in connection.execute(
                    """
                    SELECT id FROM review_plan_items
                    WHERE employee_review_id = ? ORDER BY id
                    """,
                    (review_id,),
                ).fetchall()
            ]
            self.assertEqual(len(plan_item_ids), 4)
        finally:
            connection.close()

        self.sign_in_as(subject_user_id, "Employee", "Regression E2ESubject")
        self.assertEqual(
            self.client.get(
                f"/reviews/{review_id}/self-assessment"
            ).status_code,
            200,
        )

        connection = self.get_test_connection()
        try:
            assessment_id = connection.execute(
                """
                SELECT id FROM self_assessments
                WHERE employee_review_id = ?
                """,
                (review_id,),
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO self_assessment_evidence
                    (self_assessment_id, review_plan_item_id,
                     original_file_name, stored_file_name, mime_type,
                     file_size, uploaded_by)
                VALUES (?, ?, 'evidence.txt', ?, 'text/plain', 24, ?)
                """,
                (
                    assessment_id,
                    plan_item_ids[0],
                    f"e2e-evidence-{review_id}.txt",
                    subject_user_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        self_payload = {
            "responses": [
                {
                    "review_plan_item_id": item_id,
                    "rating": 4,
                    "response_text": "Delivered the expected outcome with evidence.",
                }
                for item_id in plan_item_ids
            ],
            "overall_summary": "Delivered reliable results across the review period.",
            "key_achievements": "Completed the agreed sprint outcomes.",
            "challenges": "Balanced delivery with competing priorities.",
            "support_needed": "Continued deployment mentoring would help.",
        }
        response = self.client.post(
            f"/reviews/{review_id}/self-assessment/submit",
            json=self_payload,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

        self.sign_in_as(hr["id"], "HR", hr["full_name"])
        response = self.client.post(
            f"/review-cycles/{cycle_id}/reviews/{review_id}/peers/assign",
            json={"reviewer_user_id": peer_user_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

        peer_payload = {
            "responses": [
                {
                    "review_plan_item_id": item_id,
                    "rating": 4,
                    "feedback_text": "Consistently demonstrated this in shared work.",
                }
                for item_id in plan_item_ids
            ],
            "strengths": "Reliable delivery and clear communication.",
            "development_feedback": "Continue strengthening deployment depth.",
            "collaboration_feedback": "Works constructively across the team.",
            "overall_comment": "A dependable contributor throughout the cycle.",
        }
        self.sign_in_as(peer_user_id, "Employee", "Regression E2EPeer")
        self.assertEqual(
            self.client.get(
                f"/reviews/{review_id}/peer-review"
            ).status_code,
            200,
        )
        response = self.client.post(
            f"/reviews/{review_id}/peer-review/submit",
            json=peer_payload,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["peer_phase_complete"])

        self.sign_in_as(
            self.supervisor_user_id,
            "Supervisor",
            "Regression Supervisor",
        )
        self.assertEqual(
            self.client.get(
                f"/reviews/{review_id}/supervisor-evaluation"
            ).status_code,
            200,
        )
        supervisor_payload = {
            "responses": [
                {
                    "review_plan_item_id": item_id,
                    "rating": 4,
                    "evaluation_text": "Performance met the expected standard.",
                }
                for item_id in plan_item_ids
            ],
            "overall_rating": 4,
            "performance_summary": "Strong performance across the full baseline.",
            "key_strengths": "Reliable execution and collaboration.",
            "development_priorities": "Deepen deployment and evaluation skills.",
            "support_plan": "Monthly mentoring and a practical deployment task.",
            "recommendation": "Meets Expectations",
        }
        response = self.client.post(
            f"/reviews/{review_id}/supervisor-evaluation/submit",
            json=supervisor_payload,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

        self.sign_in_as(manager_user_id, "Manager", "Regression E2EManager")
        response = self.client.post(
            f"/reviews/{review_id}/manager-approval/approve",
            json={
                "decision_note": (
                    "Approved based on the complete assessment, peer input, "
                    "and supervisor evaluation."
                )
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

        self.sign_in_as(subject_user_id, "Employee", "Regression E2ESubject")
        outcome = self.client.get(f"/reviews/{review_id}/final-outcome")
        self.assertEqual(outcome.status_code, 200)
        self.assertNotIn("Regression E2EPeer", outcome.get_data(as_text=True))
        response = self.client.post(
            f"/reviews/{review_id}/final-outcome/acknowledge",
            json={
                "confirmed": True,
                "employee_comment": "I acknowledge and understand the outcome.",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

        self.sign_in_as(hr["id"], "HR", hr["full_name"])
        self.assertEqual(
            self.client.post(f"/review-cycles/{cycle_id}/close").status_code,
            302,
        )

        connection = self.get_test_connection()
        try:
            final_review = connection.execute(
                "SELECT status FROM employee_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()["status"]
            final_cycle = connection.execute(
                "SELECT status FROM review_cycles WHERE id = ?",
                (cycle_id,),
            ).fetchone()["status"]
            pending_review_actions = connection.execute(
                """
                SELECT COUNT(*) FROM review_actions
                WHERE employee_review_id = ? AND status = 'Pending'
                """,
                (review_id,),
            ).fetchone()[0]
            self.assertEqual(final_review, "Completed")
            self.assertEqual(final_cycle, "Closed")
            self.assertEqual(pending_review_actions, 0)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
