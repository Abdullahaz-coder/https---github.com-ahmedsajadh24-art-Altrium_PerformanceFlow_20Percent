"""Disposable browser QA server. Never imports or runs during normal startup.

Run with: python -m tests.manual_audit_server
Uses an isolated database and evidence directory; all accounts are synthetic.
"""
from pathlib import Path

from werkzeug.security import generate_password_hash

import app as application
from tests.test_regressions import WorkflowRegressionTests


def main():
    fixture = WorkflowRegressionTests()
    fixture.setUp()
    try:
        fixture.test_complete_workflow_from_setup_to_closed_cycle()
        fixture.doCleanups()
        connection = fixture.get_test_connection()
        try:
            accounts = connection.execute("SELECT id, role FROM users ORDER BY id").fetchall()
            counts = {}
            for account in accounts:
                role = account['role'].lower()
                counts[role] = counts.get(role, 0) + 1
                alias = role if counts[role] == 1 else f"{role}{counts[role]}"
                connection.execute(
                    "UPDATE users SET full_name=?, email=?, password=? WHERE id=?",
                    (f"QA {alias.title()}", f"{alias}@altrium.com",
                     generate_password_hash("Audit-Workspace-2026!"), account['id']),
                )
            application.ensure_pdp_schema(connection)
            review = connection.execute("SELECT * FROM employee_reviews LIMIT 1").fetchone()
            # Keep one synthetic cycle open at a mid-workflow point so the
            # browser audit can inspect the live cohort journey display.
            connection.execute("UPDATE review_cycles SET status='Active' WHERE id=?", (review['review_cycle_id'],))
            connection.execute("UPDATE employee_reviews SET status='Peer Review In Progress' WHERE id=?", (review['id'],))
            plan_id = connection.execute(
                """INSERT INTO pdp_plans (employee_review_id, created_by, title, focus_area,
                   overall_goal, success_measure, target_date, status)
                   VALUES (?, ?, 'Reporting and delivery confidence', 'Communication and analysis',
                   'Lead a clear monthly delivery review.', 'Present two accurate reports.',
                   '2026-12-15', 'Active')""",
                (review['id'], fixture.supervisor_user_id),
            ).lastrowid
            connection.execute(
                """INSERT INTO pdp_activities (pdp_plan_id, activity, support_needed, target_date,
                   sort_order, status, employee_progress_note)
                   VALUES (?, 'Complete reporting workshop', 'Weekly coaching', '2026-11-30',
                   1, 'In Progress', 'Completed the first two modules.')""", (plan_id,)
            )
            connection.commit()
        finally:
            connection.close()
        application.app.config.update(
            TESTING=False,
            TEMPLATES_AUTO_RELOAD=True,
            SESSION_COOKIE_NAME='performanceflow_qa',
            EVIDENCE_UPLOAD_FOLDER=str(Path(fixture.temporary_directory.name, 'evidence')),
        )
        Path(application.app.config['EVIDENCE_UPLOAD_FOLDER']).mkdir(exist_ok=True)
        application.app.jinja_env.auto_reload = True
        print('QA server: http://127.0.0.1:5001 | synthetic roles: hr, supervisor, employee, employee2, manager', flush=True)
        application.app.run(host='127.0.0.1', port=5001, debug=False, use_reloader=False)
    finally:
        fixture.tearDown()


if __name__ == '__main__':
    main()
