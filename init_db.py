import os
import secrets
import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash


DATABASE_PATH = Path(__file__).resolve().with_name("database.db")

connection = sqlite3.connect(DATABASE_PATH, timeout=10)


# Enable foreign key support

connection.execute("PRAGMA foreign_keys = ON")

connection.execute("PRAGMA journal_mode = WAL")

connection.execute("PRAGMA busy_timeout = 10000")

def add_column_if_missing(
    connection,
    table_name,
    column_name,
    column_definition
):

    columns = connection.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    existing_columns = [
        column[1]
        for column in columns
    ]

    if column_name not in existing_columns:

        connection.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN {column_name}
            {column_definition}
            """
        )


# ==========================================
# USERS TABLE
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS users (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        full_name TEXT NOT NULL,

        email TEXT NOT NULL UNIQUE,

        password TEXT NOT NULL,

        role TEXT NOT NULL

    )
    """
)


# ==========================================
# EMPLOYEES TABLE
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS employees (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        user_id INTEGER NOT NULL UNIQUE,

        employee_code TEXT NOT NULL UNIQUE,

        department TEXT NOT NULL,

        job_title TEXT NOT NULL,

        hire_date TEXT NOT NULL,

        supervisor_id INTEGER,

        status TEXT NOT NULL DEFAULT 'Active',

        FOREIGN KEY (user_id)
            REFERENCES users(id),

        FOREIGN KEY (supervisor_id)
            REFERENCES users(id)

    )
    """
)

# ==========================================
# PERFORMANCE ITEMS TABLE
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS performance_items (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        employee_id INTEGER NOT NULL,

        item_type TEXT NOT NULL,

        title TEXT NOT NULL,

        description TEXT,

        target TEXT,

        due_date TEXT,

        created_by INTEGER NOT NULL,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (employee_id)
            REFERENCES employees(id),

        FOREIGN KEY (created_by)
            REFERENCES users(id)

    )
    """
)

# ==========================================
# PERFORMANCE ITEM LIFECYCLE FIELDS
# ==========================================

add_column_if_missing(
    connection,
    "performance_items",
    "status",
    "TEXT NOT NULL DEFAULT 'Active'"
)


add_column_if_missing(
    connection,
    "performance_items",
    "updated_by",
    "INTEGER"
)


add_column_if_missing(
    connection,
    "performance_items",
    "archived_at",
    "TEXT"
)


add_column_if_missing(
    connection,
    "performance_items",
    "archived_by",
    "INTEGER"
)

# ==========================================
# PERFORMANCE ITEM AUDIT HISTORY
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS performance_item_history (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        performance_item_id INTEGER NOT NULL,

        employee_id INTEGER NOT NULL,

        action TEXT NOT NULL,

        item_type TEXT NOT NULL,

        title TEXT NOT NULL,

        description TEXT,

        target TEXT,

        due_date TEXT,

        performed_by INTEGER NOT NULL,

        performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (performance_item_id)
            REFERENCES performance_items(id),

        FOREIGN KEY (employee_id)
            REFERENCES employees(id),

        FOREIGN KEY (performed_by)
            REFERENCES users(id)

    )
    """
)

# ==========================================
# REVIEW CYCLES
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS review_cycles (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        cycle_name TEXT NOT NULL,

        cycle_year INTEGER NOT NULL,

        cycle_number INTEGER NOT NULL,

        start_date TEXT NOT NULL,

        end_date TEXT NOT NULL,

        status TEXT NOT NULL DEFAULT 'Draft',

        created_by INTEGER NOT NULL,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        closed_at TIMESTAMP,

        FOREIGN KEY (created_by)
            REFERENCES users(id),

        UNIQUE (
            cycle_year,
            cycle_number
        )

    )
    """
)

# ==========================================
# REVIEW CYCLE SCHEDULING FIELDS
# ==========================================

add_column_if_missing(
    connection,
    "review_cycles",
    "scheduled_at",
    "TIMESTAMP"
)


add_column_if_missing(
    connection,
    "review_cycles",
    "scheduled_by",
    "INTEGER"
)

# ==========================================
# REVIEW CYCLE HISTORY
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS review_cycle_history (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        review_cycle_id INTEGER NOT NULL,

        action TEXT NOT NULL,

        from_status TEXT,

        to_status TEXT,

        performed_by INTEGER NOT NULL,

        performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        note TEXT,

        FOREIGN KEY (review_cycle_id)
            REFERENCES review_cycles(id),

        FOREIGN KEY (performed_by)
            REFERENCES users(id)

    )
    """
)

# ==========================================
# REVIEW CYCLE EMPLOYEE ASSIGNMENTS
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS review_cycle_employees (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        review_cycle_id INTEGER NOT NULL,

        employee_id INTEGER NOT NULL,

        assigned_by INTEGER NOT NULL,

        assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        participation_status TEXT NOT NULL DEFAULT 'Assigned',

        removed_at TIMESTAMP,

        removed_by INTEGER,

        FOREIGN KEY (review_cycle_id)
            REFERENCES review_cycles(id),

        FOREIGN KEY (employee_id)
            REFERENCES employees(id),

        FOREIGN KEY (assigned_by)
            REFERENCES users(id),

        FOREIGN KEY (removed_by)
            REFERENCES users(id),

        UNIQUE (
            review_cycle_id,
            employee_id
        )

    )
    """
)

# ==========================================
# REVIEW CYCLE ACTIVATION FIELDS
# ==========================================

add_column_if_missing(
    connection,
    "review_cycles",
    "activated_at",
    "TIMESTAMP"
)


add_column_if_missing(
    connection,
    "review_cycles",
    "activated_by",
    "INTEGER"
)

# ==========================================
# EMPLOYEE REVIEW CASES
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS employee_reviews (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        assignment_id INTEGER NOT NULL UNIQUE,

        review_cycle_id INTEGER NOT NULL,

        employee_id INTEGER NOT NULL,

        supervisor_id INTEGER NOT NULL,

        employee_name_snapshot TEXT NOT NULL,

        employee_code_snapshot TEXT NOT NULL,

        department_snapshot TEXT NOT NULL,

        job_title_snapshot TEXT NOT NULL,

        status TEXT NOT NULL DEFAULT 'Not Started',

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (assignment_id)
            REFERENCES review_cycle_employees(id),

        FOREIGN KEY (review_cycle_id)
            REFERENCES review_cycles(id),

        FOREIGN KEY (employee_id)
            REFERENCES employees(id),

        FOREIGN KEY (supervisor_id)
            REFERENCES users(id),

        UNIQUE (
            review_cycle_id,
            employee_id
        )

    )
    """
)


# ==========================================
# SELF ASSESSMENTS
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS self_assessments (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        employee_review_id INTEGER NOT NULL UNIQUE,

        status TEXT NOT NULL DEFAULT 'Draft',

        overall_summary TEXT,

        key_achievements TEXT,

        challenges TEXT,

        support_needed TEXT,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        submitted_at TIMESTAMP,

        FOREIGN KEY (employee_review_id)
            REFERENCES employee_reviews(id),

        CHECK (
            status IN (
                'Draft',
                'Submitted'
            )
        )

    )
    """
)


# ==========================================
# SELF ASSESSMENT ITEM RESPONSES
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS self_assessment_items (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        self_assessment_id INTEGER NOT NULL,

        review_plan_item_id INTEGER NOT NULL,

        rating INTEGER,

        response_text TEXT,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (self_assessment_id)
            REFERENCES self_assessments(id),

        FOREIGN KEY (review_plan_item_id)
            REFERENCES review_plan_items(id),

        UNIQUE (
            self_assessment_id,
            review_plan_item_id
        ),

        CHECK (
            rating IS NULL
            OR
            rating BETWEEN 1 AND 5
        )

    )
    """
)


# ==========================================
# SELF-ASSESSMENT EVIDENCE
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS self_assessment_evidence (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        self_assessment_id INTEGER NOT NULL,

        review_plan_item_id INTEGER,

        original_file_name TEXT NOT NULL,

        stored_file_name TEXT NOT NULL UNIQUE,

        mime_type TEXT,

        file_size INTEGER NOT NULL,

        uploaded_by INTEGER NOT NULL,

        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (self_assessment_id)
            REFERENCES self_assessments(id),

        FOREIGN KEY (review_plan_item_id)
            REFERENCES review_plan_items(id),

        FOREIGN KEY (uploaded_by)
            REFERENCES users(id)

    )
    """
)


# ==========================================
# PEER REVIEW ASSIGNMENTS
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS peer_review_assignments (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        employee_review_id INTEGER NOT NULL,

        reviewer_user_id INTEGER NOT NULL,

        assigned_by INTEGER NOT NULL,

        status TEXT NOT NULL DEFAULT 'Assigned',

        assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        removed_at TIMESTAMP,

        removed_by INTEGER,

        FOREIGN KEY (employee_review_id)
            REFERENCES employee_reviews(id),

        FOREIGN KEY (reviewer_user_id)
            REFERENCES users(id),

        FOREIGN KEY (assigned_by)
            REFERENCES users(id),

        FOREIGN KEY (removed_by)
            REFERENCES users(id),

        UNIQUE (
            employee_review_id,
            reviewer_user_id
        ),

        CHECK (
            status IN (
                'Assigned',
                'In Progress',
                'Submitted',
                'Removed'
            )
        )

    )
    """
)


# ==========================================
# PEER REVIEWS
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS peer_reviews (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        peer_assignment_id INTEGER NOT NULL UNIQUE,

        status TEXT NOT NULL DEFAULT 'Draft',

        strengths TEXT,

        development_feedback TEXT,

        collaboration_feedback TEXT,

        overall_comment TEXT,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        submitted_at TIMESTAMP,

        FOREIGN KEY (peer_assignment_id)
            REFERENCES peer_review_assignments(id),

        CHECK (
            status IN (
                'Draft',
                'Submitted'
            )
        )

    )
    """
)


# ==========================================
# PEER REVIEW ITEM FEEDBACK
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS peer_review_items (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        peer_review_id INTEGER NOT NULL,

        review_plan_item_id INTEGER NOT NULL,

        rating INTEGER,

        feedback_text TEXT,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (peer_review_id)
            REFERENCES peer_reviews(id),

        FOREIGN KEY (review_plan_item_id)
            REFERENCES review_plan_items(id),

        UNIQUE (
            peer_review_id,
            review_plan_item_id
        ),

        CHECK (
            rating IS NULL
            OR
            rating BETWEEN 1 AND 5
        )

    )
    """
)


# ==========================================
# REVIEW PLAN SNAPSHOT
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS review_plan_items (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        employee_review_id INTEGER NOT NULL,

        source_performance_item_id INTEGER NOT NULL,

        item_type TEXT NOT NULL,

        title TEXT NOT NULL,

        description TEXT,

        target TEXT,

        due_date TEXT,

        snapshotted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (employee_review_id)
            REFERENCES employee_reviews(id),

        FOREIGN KEY (source_performance_item_id)
            REFERENCES performance_items(id),

        UNIQUE (
            employee_review_id,
            source_performance_item_id
        )

    )
    """
)


# ==========================================
# SUPERVISOR EVALUATIONS
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS supervisor_evaluations (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        employee_review_id INTEGER NOT NULL,

        supervisor_id INTEGER NOT NULL,

        status TEXT NOT NULL DEFAULT 'Draft',

        overall_rating INTEGER,

        performance_summary TEXT,

        key_strengths TEXT,

        development_priorities TEXT,

        support_plan TEXT,

        recommendation TEXT,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        submitted_at TIMESTAMP,

        FOREIGN KEY (employee_review_id)
            REFERENCES employee_reviews(id),

        FOREIGN KEY (supervisor_id)
            REFERENCES users(id),

        CHECK (
            status IN (
                'Draft',
                'Submitted'
            )
        ),

        CHECK (
            overall_rating IS NULL
            OR
            overall_rating BETWEEN 1 AND 5
        )

    )
    """
)


# ==========================================
# SUPERVISOR ITEM EVALUATIONS
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS supervisor_evaluation_items (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        supervisor_evaluation_id INTEGER NOT NULL,

        review_plan_item_id INTEGER NOT NULL,

        rating INTEGER,

        evaluation_text TEXT,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (supervisor_evaluation_id)
            REFERENCES supervisor_evaluations(id),

        FOREIGN KEY (review_plan_item_id)
            REFERENCES review_plan_items(id),

        UNIQUE (
            supervisor_evaluation_id,
            review_plan_item_id
        ),

        CHECK (
            rating IS NULL
            OR
            rating BETWEEN 1 AND 5
        )

    )
    """
)


# ==========================================
# MANAGEMENT APPROVALS
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS manager_approvals (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        employee_review_id INTEGER NOT NULL UNIQUE,

        manager_id INTEGER NOT NULL,

        status TEXT NOT NULL DEFAULT 'Pending',

        decision_note TEXT,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        decided_at TIMESTAMP,

        FOREIGN KEY (employee_review_id)
            REFERENCES employee_reviews(id),

        FOREIGN KEY (manager_id)
            REFERENCES users(id),

        CHECK (
            status IN (
                'Pending',
                'Approved',
                'Changes Requested'
            )
        )

    )
    """
)


# ==========================================
# PRIVATE MANAGER CHANGE REQUESTS
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS manager_change_requests (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        employee_review_id INTEGER NOT NULL,

        recipient_user_id INTEGER NOT NULL,

        recipient_role TEXT NOT NULL,

        private_note TEXT NOT NULL,

        status TEXT NOT NULL DEFAULT 'Pending',

        requested_by INTEGER NOT NULL,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        completed_at TIMESTAMP,

        FOREIGN KEY (employee_review_id)
            REFERENCES employee_reviews(id),

        FOREIGN KEY (recipient_user_id)
            REFERENCES users(id),

        FOREIGN KEY (requested_by)
            REFERENCES users(id),

        CHECK (
            recipient_role IN (
                'Employee',
                'Peer Reviewer',
                'Supervisor'
            )
        ),

        CHECK (
            status IN (
                'Pending',
                'Completed'
            )
        )

    )
    """
)

connection.execute(
    """
    CREATE INDEX IF NOT EXISTS idx_manager_change_request_recipient
    ON manager_change_requests
    (employee_review_id, recipient_user_id, status)
    """
)


# ==========================================
# FINAL REVIEW ACKNOWLEDGEMENTS
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS final_review_acknowledgements (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        employee_review_id INTEGER NOT NULL UNIQUE,

        employee_user_id INTEGER NOT NULL,

        status TEXT NOT NULL DEFAULT 'Pending',

        employee_comment TEXT,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        acknowledged_at TIMESTAMP,

        FOREIGN KEY (employee_review_id)
            REFERENCES employee_reviews(id),

        FOREIGN KEY (employee_user_id)
            REFERENCES users(id),

        CHECK (
            status IN (
                'Pending',
                'Acknowledged'
            )
        )

    )
    """
)


# ==========================================
# PB11 - PAR MEETINGS AND AVAILABILITY
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS par_meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_review_id INTEGER NOT NULL UNIQUE,
        scheduled_by INTEGER NOT NULL,
        meeting_date TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        meeting_format TEXT NOT NULL,
        location TEXT NOT NULL,
        agenda TEXT,
        status TEXT NOT NULL DEFAULT 'Scheduled',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        held_at TIMESTAMP,
        FOREIGN KEY (employee_review_id) REFERENCES employee_reviews(id),
        FOREIGN KEY (scheduled_by) REFERENCES users(id),
        CHECK (meeting_format IN ('In person', 'Online', 'Hybrid')),
        CHECK (status IN ('Scheduled', 'Rescheduled', 'Held', 'Cancelled'))
    )
    """
)

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS par_meeting_attendees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        par_meeting_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        attendee_role TEXT NOT NULL,
        FOREIGN KEY (par_meeting_id) REFERENCES par_meetings(id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        UNIQUE(par_meeting_id, user_id),
        CHECK (attendee_role IN ('Employee', 'Supervisor', 'Manager'))
    )
    """
)

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS user_unavailability (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        start_at TEXT NOT NULL,
        end_at TEXT NOT NULL,
        reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        CHECK (end_at > start_at)
    )
    """
)

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS par_meeting_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        par_meeting_id INTEGER NOT NULL UNIQUE,
        recorded_by INTEGER NOT NULL,
        discussion_summary TEXT NOT NULL,
        confirmed_strengths TEXT,
        development_priorities TEXT,
        employee_comments TEXT,
        agreed_actions TEXT NOT NULL,
        outcome TEXT NOT NULL,
        recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (par_meeting_id) REFERENCES par_meetings(id),
        FOREIGN KEY (recorded_by) REFERENCES users(id),
        CHECK (outcome IN ('PDP Required', 'No PDP Required'))
    )
    """
)

connection.execute(
    """
    CREATE INDEX IF NOT EXISTS idx_par_meeting_availability
    ON par_meeting_attendees(user_id, par_meeting_id)
    """
)

connection.execute(
    """
    CREATE INDEX IF NOT EXISTS idx_user_unavailability_window
    ON user_unavailability(user_id, start_at, end_at)
    """
)


# ==========================================
# PERSONAL DEVELOPMENT PLANS (PB13)
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS pdp_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_review_id INTEGER NOT NULL UNIQUE,
        created_by INTEGER NOT NULL,
        title TEXT NOT NULL,
        focus_area TEXT NOT NULL,
        overall_goal TEXT NOT NULL,
        success_measure TEXT NOT NULL,
        target_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (employee_review_id) REFERENCES employee_reviews(id),
        FOREIGN KEY (created_by) REFERENCES users(id),
        CHECK (status IN ('Draft', 'Active', 'Completed'))
    )
    """
)

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS pdp_activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pdp_plan_id INTEGER NOT NULL,
        activity TEXT NOT NULL,
        support_needed TEXT,
        target_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Not Started',
        employee_progress_note TEXT,
        employee_updated_at TIMESTAMP,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (pdp_plan_id) REFERENCES pdp_plans(id) ON DELETE CASCADE,
        CHECK (status IN ('Not Started', 'In Progress', 'Completed'))
    )
    """
)


# ==========================================
# REVIEW ASSIGNMENT LIFECYCLE
# ==========================================

add_column_if_missing(
    connection,
    "review_cycle_employees",
    "removed_at",
    "TIMESTAMP"
)


add_column_if_missing(
    connection,
    "review_cycle_employees",
    "removed_by",
    "INTEGER"
)

# ==========================================
# DEFAULT HR ACCOUNT
# ==========================================

generated_bootstrap_passwords = {}


def bootstrap_password(environment_name, account_label):
    password = os.environ.get(environment_name)

    if password:
        return password

    password = secrets.token_urlsafe(18)
    generated_bootstrap_passwords[account_label] = password
    return password


hr_password = generate_password_hash(
    bootstrap_password("PERFORMANCEFLOW_HR_PASSWORD", "HR")
)


connection.execute(
    """
    INSERT OR IGNORE INTO users
    (
        full_name,
        email,
        password,
        role
    )

    VALUES (?, ?, ?, ?)
    """,

    (
        "Altrium HR Admin",
        "hr@altrium.com",
        hr_password,
        "HR"
    )
)



# ==========================================
# DEFAULT SUPERVISOR ACCOUNT
# ==========================================

supervisor_password = generate_password_hash(
    bootstrap_password(
        "PERFORMANCEFLOW_SUPERVISOR_PASSWORD",
        "Supervisor"
    )
)


connection.execute(
    """
    INSERT OR IGNORE INTO users
    (
        full_name,
        email,
        password,
        role
    )

    VALUES (?, ?, ?, ?)
    """,

    (
        "Sarah Perera",
        "supervisor@altrium.com",
        supervisor_password,
        "Supervisor"
    )
)

# ==========================================
# DEFAULT MANAGER ACCOUNT
# ==========================================

manager_password = generate_password_hash(
    bootstrap_password("PERFORMANCEFLOW_MANAGER_PASSWORD", "Manager")
)


connection.execute(
    """
    INSERT OR IGNORE INTO users
    (
        full_name,
        email,
        password,
        role
    )

    VALUES (?, ?, ?, ?)
    """,

    (
        "Nimal Jayasinghe",
        "manager@altrium.com",
        manager_password,
        "Manager"
    )
)

# ==========================================
# REVIEW ACTIONS
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS review_actions (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        review_cycle_id INTEGER NOT NULL,

        employee_review_id INTEGER,

        assigned_to INTEGER NOT NULL,

        action_type TEXT NOT NULL,

        title TEXT NOT NULL,

        description TEXT,

        status TEXT NOT NULL DEFAULT 'Pending',

        priority TEXT NOT NULL DEFAULT 'Normal',

        due_date TEXT,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        completed_at TIMESTAMP,

        FOREIGN KEY (review_cycle_id)
            REFERENCES review_cycles(id),

        FOREIGN KEY (employee_review_id)
            REFERENCES employee_reviews(id),

        FOREIGN KEY (assigned_to)
            REFERENCES users(id)

    )
    """
)


# ==========================================
# REVIEW ACTION DUPLICATE PROTECTION
# ==========================================

connection.execute(
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    idx_unique_review_action

    ON review_actions
    (
        review_cycle_id,
        employee_review_id,
        assigned_to,
        action_type
    )
    """
)


# ==========================================
# USER NOTIFICATIONS
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS notifications (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        user_id INTEGER NOT NULL,

        review_cycle_id INTEGER,

        employee_review_id INTEGER,

        notification_type TEXT NOT NULL,

        title TEXT NOT NULL,

        message TEXT NOT NULL,

        is_read INTEGER NOT NULL DEFAULT 0,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        read_at TIMESTAMP,

        FOREIGN KEY (user_id)
            REFERENCES users(id),

        FOREIGN KEY (review_cycle_id)
            REFERENCES review_cycles(id),

        FOREIGN KEY (employee_review_id)
            REFERENCES employee_reviews(id)

    )
    """
)


# ==========================================
# PB16 - WORKFLOW REMINDER DELIVERY LOG
# ==========================================

connection.execute(
    """
    CREATE TABLE IF NOT EXISTS workflow_reminder_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        review_action_id INTEGER,
        pdp_activity_id INTEGER,
        reminder_kind TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (review_action_id) REFERENCES review_actions(id),
        FOREIGN KEY (pdp_activity_id) REFERENCES pdp_activities(id),
        UNIQUE(user_id, review_action_id, pdp_activity_id, reminder_kind)
    )
    """
)

connection.execute(
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_action_reminder_once
    ON workflow_reminder_log(user_id, review_action_id, reminder_kind)
    WHERE review_action_id IS NOT NULL
    """
)

connection.execute(
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_pdp_activity_reminder_once
    ON workflow_reminder_log(user_id, pdp_activity_id, reminder_kind)
    WHERE pdp_activity_id IS NOT NULL
    """
)

# ==========================================
# PB10 BACKFILL FOR SUBMITTED EVALUATIONS
# ==========================================

default_manager = connection.execute(
    """
    SELECT id
    FROM users
    WHERE role = 'Manager'
    ORDER BY id
    LIMIT 1
    """
).fetchone()


if default_manager is not None:

    connection.execute(
        """
        INSERT OR IGNORE INTO manager_approvals
        (
            employee_review_id,
            manager_id,
            status
        )
        SELECT
            employee_reviews.id,
            ?,
            CASE
                WHEN employee_reviews.status IN ('Approved', 'Completed')
                    THEN 'Approved'
                ELSE 'Pending'
            END
        FROM employee_reviews
        JOIN supervisor_evaluations
            ON supervisor_evaluations.employee_review_id
                = employee_reviews.id
        WHERE supervisor_evaluations.status = 'Submitted'
        AND employee_reviews.status IN (
            'Supervisor Evaluation Submitted',
            'Manager Approval Pending',
            'Approved',
            'Completed'
        )
        """,
        (default_manager[0],)
    )


    connection.execute(
        """
        INSERT OR IGNORE INTO review_actions
        (
            review_cycle_id,
            employee_review_id,
            assigned_to,
            action_type,
            title,
            description,
            status,
            priority
        )
        SELECT
            employee_reviews.review_cycle_id,
            employee_reviews.id,
            ?,
            'MANAGER_APPROVAL',
            'Approve ' || employee_reviews.employee_name_snapshot
                || '''s Review',
            'Review the submitted supervisor evaluation and record '
                || 'the final management decision.',
            CASE
                WHEN employee_reviews.status IN ('Approved', 'Completed')
                    THEN 'Completed'
                ELSE 'Pending'
            END,
            'High'
        FROM employee_reviews
        JOIN supervisor_evaluations
            ON supervisor_evaluations.employee_review_id
                = employee_reviews.id
        WHERE supervisor_evaluations.status = 'Submitted'
        AND employee_reviews.status IN (
            'Supervisor Evaluation Submitted',
            'Manager Approval Pending',
            'Approved',
            'Completed'
        )
        """,
        (default_manager[0],)
    )


# ==========================================
# PB11 BACKFILL FOR APPROVED REVIEWS
# ==========================================

connection.execute(
    """
    INSERT OR IGNORE INTO final_review_acknowledgements
    (
        employee_review_id,
        employee_user_id,
        status,
        acknowledged_at
    )
    SELECT
        employee_reviews.id,
        employees.user_id,
        CASE
            WHEN employee_reviews.status = 'Completed'
                THEN 'Acknowledged'
            ELSE 'Pending'
        END,
        CASE
            WHEN employee_reviews.status = 'Completed'
                THEN CURRENT_TIMESTAMP
            ELSE NULL
        END
    FROM employee_reviews
    JOIN employees
        ON employees.id = employee_reviews.employee_id
    JOIN manager_approvals
        ON manager_approvals.employee_review_id
            = employee_reviews.id
    WHERE manager_approvals.status = 'Approved'
    AND employee_reviews.status IN ('Approved', 'Completed')
    """
)


connection.execute(
    """
    INSERT OR IGNORE INTO review_actions
    (
        review_cycle_id,
        employee_review_id,
        assigned_to,
        action_type,
        title,
        description,
        status,
        priority
    )
    SELECT
        employee_reviews.review_cycle_id,
        employee_reviews.id,
        employees.user_id,
        'FINAL_REVIEW_ACKNOWLEDGEMENT',
        'Acknowledge Final Review Outcome',
        'Read the approved review outcome and confirm that it has been '
            || 'received. You may also add a final employee comment.',
        CASE
            WHEN employee_reviews.status = 'Completed'
                THEN 'Completed'
            ELSE 'Pending'
        END,
        'High'
    FROM employee_reviews
    JOIN employees
        ON employees.id = employee_reviews.employee_id
    JOIN manager_approvals
        ON manager_approvals.employee_review_id
            = employee_reviews.id
    WHERE manager_approvals.status = 'Approved'
    AND employee_reviews.status IN ('Approved', 'Completed')
    """
)

# ==========================================
# SAVE DATABASE CHANGES
# ==========================================

connection.commit()

connection.close()


print("Database updated successfully.")

if generated_bootstrap_passwords:
    print("Generated passwords for newly created local accounts:")

    for account_label, password in generated_bootstrap_passwords.items():
        print(f"  {account_label}: {password}")

    print("Save these locally; they will not be shown again.")

