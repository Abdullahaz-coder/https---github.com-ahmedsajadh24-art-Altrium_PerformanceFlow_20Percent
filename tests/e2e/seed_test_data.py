"""Seed only the temporary Playwright database, never the live application."""

import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash


database_path = Path.cwd() / "database.db"
if not Path.cwd().name.startswith("performanceflow-e2e-"):
    raise RuntimeError("E2E seed must run inside an isolated temporary directory")

connection = sqlite3.connect(database_path)
connection.execute("PRAGMA foreign_keys = ON")
supervisor_id = connection.execute(
    "SELECT id FROM users WHERE email = 'supervisor@altrium.com'"
).fetchone()[0]
connection.execute(
    """INSERT INTO employees
       (user_id, employee_code, department, job_title, hire_date, supervisor_id, status)
       VALUES (?, 'E2E-SUP-001', 'Operations', 'Supervisor', '2026-01-01', NULL, 'Active')""",
    (supervisor_id,),
)
employee_id = connection.execute(
    """INSERT INTO users (full_name, email, password, role)
       VALUES ('E2E Employee', 'employee.e2e@altrium.com', ?, 'Employee')""",
    (generate_password_hash('TestOnly-Employee-2026!'),),
).lastrowid
connection.execute(
    """INSERT INTO employees
       (user_id, employee_code, department, job_title, hire_date, supervisor_id, status)
       VALUES (?, 'E2E-EMP-001', 'Operations', 'Analyst', '2026-01-15', ?, 'Active')""",
    (employee_id, supervisor_id),
)
connection.commit()
connection.close()
