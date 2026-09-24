"""Role boundaries and post-review regression tests using disposable data."""
import unittest
from contextlib import contextmanager
from datetime import datetime
from html.parser import HTMLParser
from unittest.mock import patch

from werkzeug.security import generate_password_hash

import app as application
from tests import test_regressions as fixtures


class NavigationLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'nav-link' in attrs.get('class', '').split():
            self.links.append(attrs)


class RoleAuditTests(unittest.TestCase):
    setUp = fixtures.WorkflowRegressionTests.setUp
    tearDown = fixtures.WorkflowRegressionTests.tearDown
    get_test_connection = fixtures.WorkflowRegressionTests.get_test_connection
    reset_test_data = fixtures.WorkflowRegressionTests.reset_test_data
    create_user = fixtures.WorkflowRegressionTests.create_user
    create_employee = fixtures.WorkflowRegressionTests.create_employee
    create_cycle_review = fixtures.WorkflowRegressionTests.create_cycle_review
    sign_in_as = fixtures.WorkflowRegressionTests.sign_in_as

    @contextmanager
    def db(self):
        connection = self.get_test_connection()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def plan_fixture(self, closed=False):
        workflow = self.create_cycle_review('Completed', 'Closed' if closed else 'Active')
        with self.db() as connection:
            application.ensure_par_meeting_schema(connection)
            application.ensure_pdp_schema(connection)
            plan_id = connection.execute(
                """INSERT INTO pdp_plans (employee_review_id, created_by, title, focus_area,
                   overall_goal, success_measure, target_date, status)
                   VALUES (?, ?, 'Audit development plan', 'Analysis', 'Improve reporting',
                   'Two reports', '2027-06-30', 'Active')""",
                (workflow['review_id'], self.supervisor_user_id),
            ).lastrowid
            activity_id = connection.execute(
                """INSERT INTO pdp_activities (pdp_plan_id, activity, target_date, sort_order,
                   status, employee_progress_note) VALUES (?, 'Training', '2027-05-30', 1,
                   'In Progress', 'Finished module one')""", (plan_id,),
            ).lastrowid
        return dict(workflow, plan_id=plan_id, activity_id=activity_id)

    def plan_payload(self, workflow):
        return dict(title='Revised development plan', focus_area='Analysis',
                    overall_goal='Improve reporting', success_measure='Two reports',
                    target_date='2027-06-30', activity_id=[str(workflow['activity_id'])],
                    activity=['Training'], support_needed=['Coaching'],
                    activity_target_date=['2027-05-30'])

    def test_plan_edit_preserves_employee_progress_and_reminder_references(self):
        workflow = self.plan_fixture()
        with self.db() as connection:
            application.ensure_reminder_schema(connection)
            connection.execute(
                "INSERT INTO workflow_reminder_log (user_id,pdp_activity_id,reminder_kind) VALUES (?,?,'PDP_ACTIVITY_OVERDUE')",
                (workflow['employee_user_id'], workflow['activity_id']),
            )
        self.sign_in_as(self.supervisor_user_id, 'Supervisor')
        response = self.client.post(f"/reviews/{workflow['review_id']}/pdp", data=self.plan_payload(workflow))
        self.assertEqual(response.status_code, 302)
        with self.db() as connection:
            row = connection.execute('SELECT * FROM pdp_activities WHERE id=?', (workflow['activity_id'],)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['status'], 'In Progress')
            self.assertEqual(row['employee_progress_note'], 'Finished module one')

    def test_plan_rejects_invalid_dates_without_writing(self):
        workflow = self.plan_fixture()
        self.sign_in_as(self.supervisor_user_id, 'Supervisor')
        for date in ('not-a-date', '2027-02-30', '2027-07-01'):
            payload = self.plan_payload(workflow)
            payload['activity_target_date'] = [date]
            self.client.post(f"/reviews/{workflow['review_id']}/pdp", data=payload)
            with self.db() as connection:
                self.assertEqual(connection.execute('SELECT target_date FROM pdp_activities WHERE pdp_plan_id=?',
                                                    (workflow['plan_id'],)).fetchone()[0], '2027-05-30')

    def test_development_remains_reachable_after_cycle_closure(self):
        workflow = self.plan_fixture(closed=True)
        self.sign_in_as(self.supervisor_user_id, 'Supervisor')
        self.assertIn(b'Audit development plan', self.client.get('/development-pulse').data)
        self.sign_in_as(workflow['employee_user_id'], 'Employee')
        self.assertIn(b'Update PDP progress', self.client.get('/dashboard').data)
        self.assertEqual(self.client.get('/development-pulse').status_code, 200)
        self.assertIn(b'Audit development plan', self.client.get('/development-pulse').data)

    def test_hr_plan_heading_is_not_my_plan(self):
        workflow = self.plan_fixture()
        self.sign_in_as(workflow['hr_id'], 'HR')
        self.assertNotIn(b'My Personal Development Plan', self.client.get(f"/reviews/{workflow['review_id']}/pdp").data)

    def test_employee_cannot_view_another_employees_plan(self):
        workflow = self.plan_fixture()
        outsider, _ = self.create_employee('Outsider')
        self.sign_in_as(outsider, 'Employee')
        response = self.client.get(f"/reviews/{workflow['review_id']}/pdp")
        self.assertEqual(response.status_code, 302)

    def test_changed_account_role_takes_effect_on_existing_session(self):
        user_id, _ = self.create_employee('RoleChange')
        self.sign_in_as(user_id, 'HR')
        self.assertEqual(self.client.get('/employees').status_code, 302)

    def test_inactive_account_cannot_sign_in_or_keep_using_session(self):
        user_id, employee_id = self.create_employee('InactiveLogin')
        with self.db() as connection:
            connection.execute("UPDATE employees SET status='Inactive' WHERE id=?", (employee_id,))
            connection.execute("UPDATE users SET password=? WHERE id=?", (generate_password_hash('Test-Audit-2026!'), user_id))
        response = self.client.post('/', data=dict(email='regression.inactivelogin@altrium.com', password='Test-Audit-2026!'))
        self.assertNotEqual(response.status_code, 302)
        self.sign_in_as(user_id, 'Employee')
        self.assertEqual(self.client.get('/dashboard').status_code, 302)

    def test_availability_mixed_timezone_input_does_not_crash(self):
        self.sign_in_as(self.supervisor_user_id, 'Supervisor')
        response = self.client.post('/availability', data=dict(start_at='2027-01-11T10:00+05:30', end_at='2027-01-11T12:00'))
        self.assertEqual(response.status_code, 302)
        with self.db() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM user_unavailability').fetchone()[0], 0)

    def par_fixture(self):
        workflow = self.create_cycle_review('Approved')
        manager_id = self.create_user('Manager', 'ApprovalManager')
        with self.db() as connection:
            application.ensure_par_meeting_schema(connection)
            connection.execute("INSERT INTO manager_approvals (employee_review_id, manager_id, status) VALUES (?,?,'Approved')",
                               (workflow['review_id'], manager_id))
        return dict(workflow, manager_id=manager_id)

    def meeting_payload(self, start='10:00'):
        return dict(meeting_date='2026-09-22', start_time=start, duration='60',
                    meeting_format='In person', location='QA Meeting Room', agenda='Review development actions')

    def test_meeting_rejects_past_time_and_premature_completion(self):
        workflow = self.par_fixture()
        self.sign_in_as(self.supervisor_user_id, 'Supervisor')
        base = f"/reviews/{workflow['review_id']}/par-meeting"
        with patch('app.par_now', return_value=datetime(2026, 9, 22, 9, 30)):
            self.client.post(base + '/schedule', data=self.meeting_payload('09:00'))
            with self.db() as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM par_meetings').fetchone()[0], 0)
            self.client.post(base + '/schedule', data=self.meeting_payload())
            self.client.post(base + '/held')
            with self.db() as connection:
                self.assertEqual(connection.execute('SELECT status FROM par_meetings').fetchone()[0], 'Scheduled')
            slots = self.client.get(base + '/availability?date=2026-09-22&duration=60').get_json()['slots']
            self.assertNotIn('09:00', [slot['value'] for slot in slots])

    def test_demo_mode_allows_supervisor_to_mark_future_par_held_immediately(self):
        workflow = self.par_fixture()
        self.sign_in_as(self.supervisor_user_id, 'Supervisor')
        base = f"/reviews/{workflow['review_id']}/par-meeting"
        payload = self.meeting_payload()
        payload.update(meeting_date='2027-01-11', start_time='10:00')
        with patch.dict(application.app.config, {'DEMO_MODE': True}):
            self.client.post(base + '/schedule', data=payload)
            page = self.client.get(base)
            self.assertIn(b'Mark meeting as held now', page.data)
            self.assertIn(b'Demo mode is on', page.data)
            self.client.post(base + '/held')
        with self.db() as connection:
            self.assertEqual(connection.execute('SELECT status FROM par_meetings').fetchone()[0], 'Held')

    def test_meeting_rechecks_unavailability_and_sends_private_working_link(self):
        workflow = self.par_fixture()
        base = f"/reviews/{workflow['review_id']}/par-meeting"
        with self.db() as connection:
            connection.execute("INSERT INTO user_unavailability (user_id,start_at,end_at) VALUES (?,'2026-09-22T10:00','2026-09-22T11:00')",
                               (workflow['employee_user_id'],))
        self.sign_in_as(self.supervisor_user_id, 'Supervisor')
        with patch('app.par_now', return_value=datetime(2026, 9, 22, 9)):
            self.client.post(base + '/schedule', data=self.meeting_payload())
            with self.db() as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM par_meetings').fetchone()[0], 0)
            self.client.post(base + '/schedule', data=self.meeting_payload('11:00'))
        self.sign_in_as(workflow['employee_user_id'], 'Employee')
        feed = self.client.get('/notifications/feed').get_json()
        self.assertIn(base, str(feed))
        self.assertEqual(self.client.get(base).status_code, 200)
        self.sign_in_as(workflow['manager_id'], 'Manager')
        self.assertEqual(self.client.get(base).status_code, 302)
        self.assertNotIn('PAR meeting Scheduled', str(self.client.get('/notifications/feed').get_json()))

    def test_closed_review_cannot_cancel_retained_meeting(self):
        workflow = self.par_fixture()
        base = f"/reviews/{workflow['review_id']}/par-meeting"
        self.sign_in_as(self.supervisor_user_id, 'Supervisor')
        with patch('app.par_now', return_value=datetime(2026, 9, 22, 9)):
            self.client.post(base + '/schedule', data=self.meeting_payload())
        with self.db() as connection:
            connection.execute("UPDATE review_cycles SET status='Closed' WHERE id=?", (workflow['cycle_id'],))
        self.client.post(base + '/cancel')
        with self.db() as connection:
            self.assertEqual(connection.execute('SELECT status FROM par_meetings').fetchone()[0], 'Scheduled')

    def test_navigation_routes_are_reachable_and_selection_is_unique_for_every_role(self):
        workflow = self.plan_fixture()
        manager_id = self.create_user('Manager', 'NavManager')
        for user_id, role in ((workflow['hr_id'], 'HR'), (self.supervisor_user_id, 'Supervisor'),
                              (workflow['employee_user_id'], 'Employee'), (manager_id, 'Manager')):
            self.sign_in_as(user_id, role)
            parser = NavigationLinks()
            parser.feed(self.client.get('/dashboard').get_data(as_text=True))
            links = [link for link in parser.links if link['href'] != '/logout']
            self.assertEqual(len({link['href'] for link in links}), len(links), role)
            for link in links:
                with self.subTest(role=role, url=link['href']):
                    response = self.client.get(link['href'])
                    self.assertEqual(response.status_code, 200)
                    current = NavigationLinks()
                    current.feed(response.get_data(as_text=True))
                    self.assertEqual(sum('active' in row.get('class', '').split() for row in current.links), 1)

    def test_review_guide_only_returns_the_signed_in_users_actions(self):
        workflow = self.create_cycle_review()
        peer_user_id, _ = self.create_employee('GuidePeer')
        with self.db() as connection:
            connection.execute(
                """INSERT INTO review_actions
                   (review_cycle_id, employee_review_id, assigned_to, action_type,
                    title, description, status, priority)
                   VALUES (?, ?, ?, 'SELF_ASSESSMENT', 'Complete my assessment',
                           'Write your own review.', 'Pending', 'High')""",
                (workflow['cycle_id'], workflow['review_id'], workflow['employee_user_id']),
            )
            connection.execute(
                """INSERT INTO review_actions
                   (review_cycle_id, employee_review_id, assigned_to, action_type,
                    title, description, status, priority)
                   VALUES (?, ?, ?, 'PEER_REVIEW', 'Private peer assignment',
                           'Give confidential feedback.', 'Pending', 'High')""",
                (workflow['cycle_id'], workflow['review_id'], peer_user_id),
            )
        self.sign_in_as(workflow['employee_user_id'], 'Employee')
        employee_guide = self.client.get('/review-guide/context').get_json()
        self.assertEqual(employee_guide['next_step']['title'], 'Complete my assessment')
        self.assertEqual(employee_guide['next_step']['url'], f"/reviews/{workflow['review_id']}/self-assessment")
        self.assertNotIn('Private peer assignment', str(employee_guide))
        self.assertNotIn('Employees', [item['label'] for item in employee_guide['shortcuts']])

        self.sign_in_as(peer_user_id, 'Employee')
        peer_guide = self.client.get('/review-guide/context').get_json()
        self.assertEqual(peer_guide['next_step']['title'], 'Private peer assignment')
        self.assertEqual(peer_guide['next_step']['url'], f"/reviews/{workflow['review_id']}/peer-review")
        self.assertNotIn('Complete my assessment', str(peer_guide))

        with self.client.session_transaction() as browser_session:
            browser_session.clear()
        self.assertEqual(self.client.get('/review-guide/context').status_code, 401)

    def test_real_csrf_flow_rejects_missing_token_and_accepts_correct_token(self):
        self.sign_in_as(self.supervisor_user_id, 'Supervisor')
        original_testing = application.app.config['TESTING']
        application.app.config['TESTING'] = False
        try:
            self.client.get('/availability')
            payload = dict(start_at='2027-01-11T10:00', end_at='2027-01-11T11:00', reason='Training')
            self.client.post('/availability', data=payload)
            with self.db() as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM user_unavailability').fetchone()[0], 0)
            with self.client.session_transaction() as session:
                payload['csrf_token'] = session['_csrf_token']
            self.client.post('/availability', data=payload)
            with self.db() as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM user_unavailability').fetchone()[0], 1)
        finally:
            application.app.config['TESTING'] = original_testing

    def test_password_change_revokes_existing_sessions(self):
        user_id, _ = self.create_employee('PasswordAudit')
        with self.db() as connection:
            connection.execute('UPDATE users SET password=? WHERE id=?',
                               (generate_password_hash('Original-Audit-2026!'), user_id))
        self.sign_in_as(user_id, 'Employee')
        second_client = application.app.test_client()
        with self.client.session_transaction() as session:
            saved_session = dict(session)
        with second_client.session_transaction() as session:
            session.update(saved_session)
        self.assertEqual(second_client.get('/dashboard').status_code, 200)
        self.client.post('/account/password', data=dict(current_password='Original-Audit-2026!',
                         new_password='Updated-Audit-2026!', confirm_password='Updated-Audit-2026!'))
        self.assertEqual(second_client.get('/dashboard').status_code, 302)
        response = self.client.post('/', data=dict(email='regression.passwordaudit@altrium.com', password='Updated-Audit-2026!'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/dashboard').status_code, 200)

    def test_pdp_edit_rolls_back_if_action_sync_fails(self):
        workflow = self.plan_fixture()
        self.sign_in_as(self.supervisor_user_id, 'Supervisor')
        import sqlite3
        with patch('app.sync_pdp_progress_actions', side_effect=sqlite3.OperationalError('Simulated write failure')):
            with self.assertLogs(application.app.logger, level='ERROR'):
                self.client.post(f"/reviews/{workflow['review_id']}/pdp", data=self.plan_payload(workflow))
        with self.db() as connection:
            row = connection.execute('SELECT title FROM pdp_plans WHERE id=?', (workflow['plan_id'],)).fetchone()
            self.assertEqual(row['title'], 'Audit development plan')

    def test_hr_journey_tracks_the_active_cohort_stage(self):
        workflow = self.create_cycle_review('Peer Review In Progress')
        self.sign_in_as(workflow['hr_id'], 'HR')
        response = self.client.get('/dashboard')
        html = response.get_data(as_text=True)
        self.assertIn('journey-stage complete', html)
        self.assertIn('journey-stage live', html)
        self.assertIn('Peer Review', html)
        self.assertIn('1 review at this stage', html)

        with self.db() as connection:
            connection.execute("UPDATE employee_reviews SET status='Manager Approval Pending' WHERE id=?",
                               (workflow['review_id'],))
        html = self.client.get('/dashboard').get_data(as_text=True)
        self.assertIn('Approval', html)
        self.assertIn('1 review at this stage', html)


if __name__ == '__main__':
    unittest.main()
