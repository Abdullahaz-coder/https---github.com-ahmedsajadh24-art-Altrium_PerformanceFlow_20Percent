import sqlite3
import os
import secrets
import time
import uuid
import hashlib
from datetime import datetime, timedelta

from flask import (
    Flask,
    render_template,
    request,
    session,
    redirect,
    url_for,
    flash,
    jsonify,
    send_from_directory
)

from werkzeug.utils import secure_filename

from werkzeug.security import (
    check_password_hash,
    generate_password_hash
)

from database import get_db_connection


app = Flask(__name__)

app.secret_key = os.environ.get(
    "PERFORMANCEFLOW_SECRET_KEY"
) or secrets.token_hex(32)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.environ.get("PERFORMANCEFLOW_SECURE_COOKIES", "0") == "1"
    )
)


LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60
login_attempts = {}


def is_altrium_email(email):
    if not isinstance(email, str):
        return False

    normalized_email = email.strip().lower()

    if normalized_email.count("@") != 1:
        return False

    local_part, domain = normalized_email.split("@", 1)

    return (
        bool(local_part)
        and domain == "altrium.com"
        and not any(character.isspace() for character in local_part)
    )


@app.before_request
def refresh_account_access():
    """Apply account changes immediately, including on existing sessions."""
    if 'user_id' not in session or request.endpoint == 'static':
        return None
    connection = get_db_connection()
    try:
        account = connection.execute(
            """SELECT users.id, users.full_name, users.role, users.password, employees.status
               FROM users LEFT JOIN employees ON employees.user_id = users.id
               WHERE users.id = ?""", (session['user_id'],),
        ).fetchone()
    finally:
        connection.close()
    if account is None or account['status'] == 'Inactive':
        session.clear()
        if request.is_json:
            return jsonify(success=False, message='Your account is no longer active. Contact HR.'), 401
        flash('Your account is no longer active. Contact HR.', 'error')
        return redirect(url_for('login'))
    if not secrets.compare_digest(session.get('_credential_version', ''), credential_version(account['password'])):
        session.clear()
        if request.is_json:
            return jsonify(success=False, message='Please sign in again. Your account credentials have changed.'), 401
        flash('Please sign in again to continue securely.', 'error')
        return redirect(url_for('login'))
    session['user_role'] = account['role']
    session['user_name'] = account['full_name']


def credential_version(password_hash):
    """Invalidate existing sessions after a password change without storing its hash in cookies."""
    return hashlib.sha256(password_hash.encode('utf-8')).hexdigest()


@app.before_request
def protect_unsafe_requests():

    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)

    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None

    if app.config.get("TESTING"):
        return None

    supplied_token = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRF-Token")
    )

    if supplied_token and secrets.compare_digest(
        supplied_token,
        session["_csrf_token"]
    ):
        return None

    if request.is_json:
        return jsonify({
            "success": False,
            "message": "Your session security token expired. Refresh and try again."
        }), 400

    flash(
        "Your session security token expired. Please try again.",
        "error"
    )
    return redirect(request.referrer or url_for("login"))


@app.context_processor
def inject_security_context():
    return {"csrf_token": session.get("_csrf_token", "")}


# =====================================
# EVIDENCE STORAGE
# =====================================

EVIDENCE_UPLOAD_FOLDER = os.path.join(
    app.root_path,
    "instance",
    "evidence"
)


os.makedirs(
    EVIDENCE_UPLOAD_FOLDER,
    exist_ok=True
)


app.config[
    "EVIDENCE_UPLOAD_FOLDER"
] = EVIDENCE_UPLOAD_FOLDER


# Maximum uploaded file size = 10 MB

app.config[
    "MAX_CONTENT_LENGTH"
] = 10 * 1024 * 1024


ALLOWED_EVIDENCE_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "docx",
    "xlsx"
}


def clean_json_text(
    data,
    field_name,
    label,
    max_length=10000
):

    value = data.get(field_name, "")

    if not isinstance(value, str):
        raise ValueError(f"Invalid {label}.")

    value = value.strip()

    if len(value) > max_length:
        raise ValueError(
            f"{label.capitalize()} must be {max_length:,} characters or fewer."
        )

    return value


def allowed_evidence_file(filename):

    return (
        "."
        in filename

        and

        filename
            .rsplit(".", 1)[1]
            .lower()

        in ALLOWED_EVIDENCE_EXTENSIONS
    )



@app.errorhandler(413)
def evidence_file_too_large(error):

    flash(
        "Evidence files must be 10 MB or smaller.",
        "error"
    )

    return redirect(
        request.referrer
        or
        url_for("dashboard")
    )


@app.route(
    "/reviews/<int:employee_review_id>/self-assessment/evidence",
    methods=["POST"]
)
def upload_self_assessment_evidence(employee_review_id):

    # =====================================
    # LOGIN + ROLE CHECK
    # =====================================

    if "user_id" not in session:
        return redirect(url_for("login"))


    if session["user_role"] != "Employee":
        return redirect(url_for("dashboard"))


    evidence_file = request.files.get(
        "evidence_file"
    )


    review_plan_item_raw = request.form.get(
        "review_plan_item_id",
        ""
    ).strip()


    # =====================================
    # BASIC FILE VALIDATION
    # =====================================

    if (
        evidence_file is None
        or
        evidence_file.filename == ""
    ):

        flash(
            "Please choose an evidence file.",
            "error"
        )

        return redirect(
            url_for(
                "self_assessment_studio",
                employee_review_id=employee_review_id
            )
        )


    if not allowed_evidence_file(
        evidence_file.filename
    ):

        flash(
            (
                "Unsupported evidence format. "
                "Use PDF, PNG, JPG, DOCX or XLSX."
            ),
            "error"
        )

        return redirect(
            url_for(
                "self_assessment_studio",
                employee_review_id=employee_review_id
            )
        )


    connection = get_db_connection()


    try:

        # =====================================
        # VERIFY ASSESSMENT OWNERSHIP
        # =====================================

        assessment = connection.execute(
            """
            SELECT

                self_assessments.id
                    AS self_assessment_id,

                self_assessments.status
                    AS assessment_status,

                review_cycles.status
                    AS cycle_status

            FROM self_assessments

            JOIN employee_reviews
                ON self_assessments.employee_review_id
                = employee_reviews.id

            JOIN employees
                ON employee_reviews.employee_id
                = employees.id

            JOIN review_cycles
                ON employee_reviews.review_cycle_id
                = review_cycles.id

            WHERE employee_reviews.id = ?

            AND employees.user_id = ?
            """,

            (
                employee_review_id,
                session["user_id"]
            )

        ).fetchone()


        if assessment is None:

            flash(
                "Self-assessment not found.",
                "error"
            )

            return redirect(
                url_for("dashboard")
            )


        # =====================================
        # MUST STILL BE DRAFT
        # =====================================

        if assessment["assessment_status"] != "Draft":

            flash(
                "Submitted assessments cannot be changed.",
                "error"
            )

            return redirect(
                url_for(
                    "self_assessment_studio",
                    employee_review_id=employee_review_id
                )
            )


        if assessment["cycle_status"] != "Active":

            flash(
                "Evidence cannot be changed because the review cycle is not active.",
                "error"
            )

            return redirect(
                url_for("dashboard")
            )


        # =====================================
        # OPTIONAL BASELINE ITEM LINK
        # =====================================

        review_plan_item_id = None


        if review_plan_item_raw:

            try:

                review_plan_item_id = int(
                    review_plan_item_raw
                )

            except ValueError:

                flash(
                    "Invalid performance item selected.",
                    "error"
                )

                return redirect(
                    url_for(
                        "self_assessment_studio",
                        employee_review_id=employee_review_id
                    )
                )


            valid_item = connection.execute(
                """
                SELECT id

                FROM review_plan_items

                WHERE id = ?

                AND employee_review_id = ?
                """,

                (
                    review_plan_item_id,
                    employee_review_id
                )

            ).fetchone()


            if valid_item is None:

                flash(
                    "The selected performance item is not part of this review.",
                    "error"
                )

                return redirect(
                    url_for(
                        "self_assessment_studio",
                        employee_review_id=employee_review_id
                    )
                )


        # =====================================
        # CREATE SAFE FILE NAME
        # =====================================

        original_file_name = secure_filename(
            evidence_file.filename
        )


        extension = (
            original_file_name
            .rsplit(".", 1)[1]
            .lower()
        )


        stored_file_name = (
            f"{uuid.uuid4().hex}.{extension}"
        )


        file_path = os.path.join(
            app.config[
                "EVIDENCE_UPLOAD_FOLDER"
            ],
            stored_file_name
        )


        # =====================================
        # SAVE FILE
        # =====================================

        evidence_file.save(
            file_path
        )


        file_size = os.path.getsize(
            file_path
        )


        # =====================================
        # SAVE DATABASE RECORD
        # =====================================

        connection.execute(
            """
            INSERT INTO self_assessment_evidence
            (
                self_assessment_id,
                review_plan_item_id,
                original_file_name,
                stored_file_name,
                mime_type,
                file_size,
                uploaded_by
            )

            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,

            (
                assessment[
                    "self_assessment_id"
                ],

                review_plan_item_id,

                original_file_name,

                stored_file_name,

                evidence_file.mimetype,

                file_size,

                session["user_id"]
            )
        )


        connection.commit()


        flash(
            "Evidence added to the assessment.",
            "success"
        )


    except (sqlite3.IntegrityError, OSError):

        connection.rollback()


        # Remove orphaned file if database
        # storage failed.

        if (
            "file_path" in locals()
            and
            os.path.exists(file_path)
        ):

            os.remove(
                file_path
            )


        flash(
            "The evidence file could not be uploaded.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for(
            "self_assessment_studio",
            employee_review_id=employee_review_id
        )
    )


@app.route(
    "/reviews/<int:employee_review_id>/self-assessment/evidence/<int:evidence_id>/remove",
    methods=["POST"]
)
def remove_self_assessment_evidence(
    employee_review_id,
    evidence_id
):

    if "user_id" not in session:
        return redirect(url_for("login"))


    if session["user_role"] != "Employee":
        return redirect(url_for("dashboard"))


    connection = get_db_connection()


    try:

        evidence = connection.execute(
            """
            SELECT

                self_assessment_evidence.id,

                self_assessment_evidence.stored_file_name,

                self_assessments.status
                    AS assessment_status

            FROM self_assessment_evidence

            JOIN self_assessments
                ON self_assessment_evidence.self_assessment_id
                = self_assessments.id

            JOIN employee_reviews
                ON self_assessments.employee_review_id
                = employee_reviews.id

            JOIN employees
                ON employee_reviews.employee_id
                = employees.id

            WHERE self_assessment_evidence.id = ?

            AND employee_reviews.id = ?

            AND employees.user_id = ?
            """,

            (
                evidence_id,
                employee_review_id,
                session["user_id"]
            )

        ).fetchone()


        if evidence is None:

            flash(
                "Evidence file not found.",
                "error"
            )

            return redirect(
                url_for(
                    "self_assessment_studio",
                    employee_review_id=employee_review_id
                )
            )


        if evidence["assessment_status"] != "Draft":

            flash(
                "Evidence cannot be removed after submission.",
                "error"
            )

            return redirect(
                url_for(
                    "self_assessment_studio",
                    employee_review_id=employee_review_id
                )
            )


        stored_file_name = evidence[
            "stored_file_name"
        ]


        connection.execute(
            """
            DELETE FROM self_assessment_evidence

            WHERE id = ?
            """,

            (
                evidence_id,
            )
        )


        connection.commit()


        file_path = os.path.join(
            app.config[
                "EVIDENCE_UPLOAD_FOLDER"
            ],
            stored_file_name
        )


        if os.path.exists(
            file_path
        ):

            os.remove(
                file_path
            )


        flash(
            "Evidence removed from the draft assessment.",
            "success"
        )


    except (sqlite3.IntegrityError, OSError):

        connection.rollback()


        flash(
            "The evidence file could not be removed.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for(
            "self_assessment_studio",
            employee_review_id=employee_review_id
        )
    )


@app.route(
    "/reviews/<int:employee_review_id>/self-assessment/evidence/<int:evidence_id>/download"
)
def download_self_assessment_evidence(
    employee_review_id,
    evidence_id
):

    if "user_id" not in session:
        return redirect(url_for("login"))


    connection = get_db_connection()


    evidence = connection.execute(
        """
        SELECT

            self_assessment_evidence.original_file_name,

            self_assessment_evidence.stored_file_name,

            employees.user_id
                AS employee_user_id,

            employee_reviews.supervisor_id,

            employee_reviews.status
                AS employee_review_status,

            self_assessments.status
                AS self_assessment_status,

            manager_approvals.manager_id

        FROM self_assessment_evidence

        JOIN self_assessments
            ON self_assessment_evidence.self_assessment_id
            = self_assessments.id

        JOIN employee_reviews
            ON self_assessments.employee_review_id
            = employee_reviews.id

        JOIN employees
            ON employee_reviews.employee_id
            = employees.id

        LEFT JOIN manager_approvals
            ON manager_approvals.employee_review_id
            = employee_reviews.id

        WHERE self_assessment_evidence.id = ?

        AND employee_reviews.id = ?
        """,

        (
            evidence_id,
            employee_review_id
        )

    ).fetchone()


    connection.close()


    if evidence is None:

        flash(
            "Evidence file not found.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )


    role = session["user_role"]

    is_owner = (
        role == "Employee"
        and evidence["employee_user_id"] == session["user_id"]
    )

    is_authorised_reviewer = (
        evidence["self_assessment_status"] == "Submitted"
        and (
            role == "HR"
            or (
                role == "Supervisor"
                and evidence["supervisor_id"] == session["user_id"]
            )
            or (
                role == "Manager"
                and evidence["manager_id"] == session["user_id"]
            )
        )
    )

    if not (is_owner or is_authorised_reviewer):

        flash(
            "You are not authorised to access this evidence.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )


    return send_from_directory(
        app.config[
            "EVIDENCE_UPLOAD_FOLDER"
        ],

        evidence[
            "stored_file_name"
        ],

        as_attachment=True,

        download_name=evidence[
            "original_file_name"
        ]
    )


@app.route(
    "/reviews/<int:employee_review_id>/self-assessment/save",
    methods=["POST"]
)
def save_self_assessment_draft(employee_review_id):

    # =====================================
    # AUTHENTICATION
    # =====================================

    if "user_id" not in session:

        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401


    if session["user_role"] != "Employee":

        return jsonify({
            "success": False,
            "message": "Only employees can save a self-assessment."
        }), 403


    # =====================================
    # READ JSON
    # =====================================

    data = request.get_json(
        silent=True
    )


    if not isinstance(data, dict):

        return jsonify({
            "success": False,
            "message": "Invalid assessment data."
        }), 400


    try:
        overall_summary = clean_json_text(
            data,
            "overall_summary",
            "overall summary"
        )
        key_achievements = clean_json_text(
            data,
            "key_achievements",
            "key achievements"
        )
        challenges = clean_json_text(
            data,
            "challenges",
            "challenges"
        )
        support_needed = clean_json_text(
            data,
            "support_needed",
            "support needed"
        )
    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error)
        }), 400


    responses = data.get(
        "responses",
        []
    )


    if not isinstance(
        responses,
        list
    ):

        return jsonify({
            "success": False,
            "message": "Invalid assessment responses."
        }), 400


    connection = get_db_connection()


    try:

        # =====================================
        # VERIFY REVIEW OWNERSHIP
        # =====================================

        assessment = connection.execute(
            """
            SELECT

                self_assessments.id
                    AS self_assessment_id,

                self_assessments.status
                    AS assessment_status,

                review_cycles.status
                    AS cycle_status

            FROM self_assessments

            JOIN employee_reviews
                ON self_assessments.employee_review_id
                = employee_reviews.id

            JOIN employees
                ON employee_reviews.employee_id
                = employees.id

            JOIN review_cycles
                ON employee_reviews.review_cycle_id
                = review_cycles.id

            WHERE employee_reviews.id = ?

            AND employees.user_id = ?
            """,

            (
                employee_review_id,
                session["user_id"]
            )

        ).fetchone()


        if assessment is None:

            return jsonify({
                "success": False,
                "message": "Self-assessment not found."
            }), 404


        # =====================================
        # ASSESSMENT MUST STILL BE DRAFT
        # =====================================

        if (
            assessment["assessment_status"]
            != "Draft"
        ):

            return jsonify({
                "success": False,
                "message":
                    "This assessment has already been submitted."
            }), 409


        if (
            assessment["cycle_status"]
            != "Active"
        ):

            return jsonify({
                "success": False,
                "message":
                    "This review cycle is no longer active."
            }), 409


        self_assessment_id = (
            assessment[
                "self_assessment_id"
            ]
        )


        # =====================================
        # UPDATE OVERALL REFLECTION
        # =====================================

        connection.execute(
            """
            UPDATE self_assessments

            SET
                overall_summary = ?,
                key_achievements = ?,
                challenges = ?,
                support_needed = ?,
                updated_at = CURRENT_TIMESTAMP

            WHERE id = ?
            """,

            (
                overall_summary,
                key_achievements,
                challenges,
                support_needed,
                self_assessment_id
            )
        )


        # =====================================
        # SAVE EACH BASELINE RESPONSE
        # =====================================

        for response in responses:

            if not isinstance(response, dict):
                raise ValueError("Invalid assessment response.")

            try:

                review_plan_item_id = int(
                    response.get(
                        "review_plan_item_id"
                    )
                )

            except (
                TypeError,
                ValueError
            ):

                raise ValueError(
                    "Invalid review baseline item."
                )


            rating = response.get(
                "rating"
            )


            response_text = clean_json_text(
                response,
                "response_text",
                "assessment response"
            )


            # ---------------------------------
            # RATING VALIDATION
            # ---------------------------------

            if (
                rating is not None
                and
                rating != ""
            ):

                try:

                    rating = int(
                        rating
                    )

                except (
                    TypeError,
                    ValueError
                ):

                    raise ValueError(
                        "Invalid rating."
                    )


                if rating < 1 or rating > 5:

                    raise ValueError(
                        "Ratings must be between 1 and 5."
                    )

            else:

                rating = None


            # ---------------------------------
            # VERIFY BASELINE OWNERSHIP
            # ---------------------------------

            valid_item = connection.execute(
                """
                SELECT id

                FROM review_plan_items

                WHERE id = ?

                AND employee_review_id = ?
                """,

                (
                    review_plan_item_id,
                    employee_review_id
                )

            ).fetchone()


            if valid_item is None:

                raise ValueError(
                    "A review item does not belong to this assessment."
                )


            # =================================
            # INSERT OR UPDATE RESPONSE
            # =================================

            connection.execute(
                """
                INSERT INTO self_assessment_items
                (
                    self_assessment_id,
                    review_plan_item_id,
                    rating,
                    response_text
                )

                VALUES (?, ?, ?, ?)

                ON CONFLICT(
                    self_assessment_id,
                    review_plan_item_id
                )

                DO UPDATE SET

                    rating =
                        excluded.rating,

                    response_text =
                        excluded.response_text,

                    updated_at =
                        CURRENT_TIMESTAMP
                """,

                (
                    self_assessment_id,
                    review_plan_item_id,
                    rating,
                    response_text
                )
            )


        connection.commit()


        return jsonify({
            "success": True,
            "message":
                "Your self-assessment draft has been saved."
        })


    except ValueError as error:

        connection.rollback()


        return jsonify({
            "success": False,
            "message": str(error)
        }), 400


    except sqlite3.Error:

        connection.rollback()


        return jsonify({
            "success": False,
            "message":
                "Your draft could not be saved."
        }), 500


    finally:

        connection.close()


@app.route(
    "/reviews/<int:employee_review_id>/self-assessment/submit",
    methods=["POST"]
)
def submit_self_assessment(employee_review_id):

    # =====================================
    # AUTHENTICATION
    # =====================================

    if "user_id" not in session:

        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401


    if session["user_role"] != "Employee":

        return jsonify({
            "success": False,
            "message":
                "Only employees can submit a self-assessment."
        }), 403


    data = request.get_json(
        silent=True
    )


    if not isinstance(data, dict):

        return jsonify({
            "success": False,
            "message": "Invalid assessment data."
        }), 400


    # =====================================
    # BASIC VALUES
    # =====================================

    try:
        overall_summary = clean_json_text(
            data,
            "overall_summary",
            "overall summary"
        )
        key_achievements = clean_json_text(
            data,
            "key_achievements",
            "key achievements"
        )
        challenges = clean_json_text(
            data,
            "challenges",
            "challenges"
        )
        support_needed = clean_json_text(
            data,
            "support_needed",
            "support needed"
        )
    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error)
        }), 400


    responses = data.get(
        "responses",
        []
    )


    if not isinstance(
        responses,
        list
    ):

        return jsonify({
            "success": False,
            "message":
                "Invalid assessment responses."
        }), 400


    connection = get_db_connection()


    try:

        # =====================================
        # VERIFY REVIEW OWNERSHIP
        # =====================================

        review = connection.execute(
            """
            SELECT

                self_assessments.id
                    AS self_assessment_id,

                self_assessments.status
                    AS assessment_status,

                employee_reviews.review_cycle_id,

                employee_reviews.supervisor_id,

                employee_reviews.status
                    AS employee_review_status,

                employee_reviews.employee_name_snapshot,

                review_cycles.cycle_name,

                review_cycles.status
                    AS cycle_status

            FROM self_assessments

            JOIN employee_reviews
                ON self_assessments.employee_review_id
                = employee_reviews.id

            JOIN employees
                ON employee_reviews.employee_id
                = employees.id

            JOIN review_cycles
                ON employee_reviews.review_cycle_id
                = review_cycles.id

            WHERE employee_reviews.id = ?

            AND employees.user_id = ?
            """,

            (
                employee_review_id,
                session["user_id"]
            )

        ).fetchone()


        if review is None:

            return jsonify({
                "success": False,
                "message":
                    "Self-assessment not found."
            }), 404


        if (
            review["assessment_status"]
            != "Draft"
        ):

            return jsonify({
                "success": False,
                "message":
                    "This assessment has already been submitted."
            }), 409


        if (
            review["cycle_status"]
            != "Active"
        ):

            return jsonify({
                "success": False,
                "message":
                    "This review cycle is no longer active."
            }), 409


        self_assessment_id = (
            review[
                "self_assessment_id"
            ]
        )


        private_change_request = get_private_manager_change_request(
            connection,
            employee_review_id,
            session["user_id"]
        )


        # =====================================
        # SAVE LATEST OVERALL RESPONSES
        # =====================================

        connection.execute(
            """
            UPDATE self_assessments

            SET
                overall_summary = ?,
                key_achievements = ?,
                challenges = ?,
                support_needed = ?,
                updated_at = CURRENT_TIMESTAMP

            WHERE id = ?
            """,

            (
                overall_summary,
                key_achievements,
                challenges,
                support_needed,
                self_assessment_id
            )
        )


        # =====================================
        # SAVE LATEST ITEM RESPONSES
        # =====================================

        for response in responses:

            if not isinstance(response, dict):
                raise ValueError("Invalid assessment response.")

            try:

                review_plan_item_id = int(
                    response.get(
                        "review_plan_item_id"
                    )
                )

            except (
                TypeError,
                ValueError
            ):

                raise ValueError(
                    "Invalid review baseline item."
                )


            rating = response.get(
                "rating"
            )


            response_text = clean_json_text(
                response,
                "response_text",
                "assessment response"
            )


            if (
                rating is not None
                and
                rating != ""
            ):

                try:

                    rating = int(
                        rating
                    )

                except (
                    TypeError,
                    ValueError
                ):

                    raise ValueError(
                        "Invalid rating."
                    )


                if rating < 1 or rating > 5:

                    raise ValueError(
                        "Ratings must be between 1 and 5."
                    )


            else:

                rating = None


            # ---------------------------------
            # VERIFY FROZEN ITEM
            # ---------------------------------

            valid_item = connection.execute(
                """
                SELECT id

                FROM review_plan_items

                WHERE id = ?

                AND employee_review_id = ?
                """,

                (
                    review_plan_item_id,
                    employee_review_id
                )

            ).fetchone()


            if valid_item is None:

                raise ValueError(
                    "A review item does not belong to this assessment."
                )


            # ---------------------------------
            # UPSERT
            # ---------------------------------

            connection.execute(
                """
                INSERT INTO self_assessment_items
                (
                    self_assessment_id,
                    review_plan_item_id,
                    rating,
                    response_text
                )

                VALUES (?, ?, ?, ?)

                ON CONFLICT(
                    self_assessment_id,
                    review_plan_item_id
                )

                DO UPDATE SET

                    rating =
                        excluded.rating,

                    response_text =
                        excluded.response_text,

                    updated_at =
                        CURRENT_TIMESTAMP
                """,

                (
                    self_assessment_id,
                    review_plan_item_id,
                    rating,
                    response_text
                )
            )


        # =====================================
        # SERVER-SIDE SUBMISSION VALIDATION
        # =====================================

        baseline_count = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM review_plan_items

            WHERE employee_review_id = ?
            """,

            (
                employee_review_id,
            )

        ).fetchone()["total"]


        complete_response_count = (
            connection.execute(
                """
                SELECT COUNT(*) AS total

                FROM self_assessment_items

                JOIN review_plan_items
                    ON self_assessment_items.review_plan_item_id
                    = review_plan_items.id

                WHERE self_assessment_items.self_assessment_id = ?

                AND review_plan_items.employee_review_id = ?

                AND self_assessment_items.rating
                    BETWEEN 1 AND 5

                AND TRIM(
                    COALESCE(
                        self_assessment_items.response_text,
                        ''
                    )
                ) <> ''
                """,

                (
                    self_assessment_id,
                    employee_review_id
                )

            ).fetchone()["total"]
        )


        evidence_count = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM self_assessment_evidence

            WHERE self_assessment_id = ?
            """,

            (
                self_assessment_id,
            )

        ).fetchone()["total"]


        # =====================================
        # BLOCKERS
        # =====================================

        if baseline_count == 0:

            raise ValueError(
                "This review has no assessment baseline."
            )


        if (
            complete_response_count
            != baseline_count
        ):

            raise ValueError(
                (
                    "Please provide a rating and reflection "
                    "for every performance item."
                )
            )


        if not overall_summary:

            raise ValueError(
                "Please complete your overall performance summary."
            )


        if evidence_count < 1:

            raise ValueError(
                (
                    "Please attach at least one piece "
                    "of supporting evidence."
                )
            )


        # =====================================
        # LOCK SELF-ASSESSMENT
        # =====================================

        connection.execute(
            """
            UPDATE self_assessments

            SET
                status = 'Submitted',
                submitted_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP

            WHERE id = ?
            """,

            (
                self_assessment_id,
            )
        )


        # =====================================
        # ADVANCE REVIEW WORKFLOW
        # =====================================

        if private_change_request is None:
            connection.execute(
                """
                UPDATE employee_reviews

                SET
                    status = 'Self Assessment Submitted',
                    updated_at = CURRENT_TIMESTAMP

                WHERE id = ?
                """,

                (
                    employee_review_id,
                )
            )
        else:
            complete_private_manager_change_request(
                connection,
                employee_review_id,
                session["user_id"]
            )


        # =====================================
        # COMPLETE EMPLOYEE ACTION
        # =====================================

        connection.execute(
            """
            UPDATE review_actions

            SET
                status = 'Completed',
                completed_at = CURRENT_TIMESTAMP

            WHERE employee_review_id = ?

            AND assigned_to = ?

            AND action_type = 'SELF_ASSESSMENT'

            AND status != 'Completed'
            """,

            (
                employee_review_id,
                session["user_id"]
            )
        )


        # =====================================
        # SIGNAL SUPERVISOR
        # =====================================

        if review["supervisor_id"]:

            connection.execute(
                """
                INSERT INTO notifications
                (
                    user_id,
                    review_cycle_id,
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )

                VALUES (?, ?, ?, ?, ?, ?)
                """,

                (
                    review["supervisor_id"],

                    review["review_cycle_id"],

                    employee_review_id,

                    "SELF_ASSESSMENT_SUBMITTED",

                    "Self Assessment Submitted",

                    (
                        f"{review['employee_name_snapshot']} "
                        f"submitted their self-assessment "
                        f"for {review['cycle_name']}."
                    )
                )
            )


        # =====================================
        # EMPLOYEE CONFIRMATION SIGNAL
        # =====================================

        connection.execute(
            """
            INSERT INTO notifications
            (
                user_id,
                review_cycle_id,
                employee_review_id,
                notification_type,
                title,
                message
            )

            VALUES (?, ?, ?, ?, ?, ?)
            """,

            (
                session["user_id"],

                review["review_cycle_id"],

                employee_review_id,

                "SELF_ASSESSMENT_CONFIRMED",

                "Assessment Submitted",

                (
                    f"Your self-assessment for "
                    f"{review['cycle_name']} "
                    f"has been submitted successfully."
                )
            )
        )


        # =====================================
        # COMMIT EVERYTHING TOGETHER
        # =====================================

        connection.commit()


        flash(
            "Your self-assessment has been submitted and locked.",
            "success"
        )


        return jsonify({
            "success": True,

            "message":
                "Self-assessment submitted successfully.",

            "redirect_url":
                url_for(
                    "self_assessment_studio",
                    employee_review_id=employee_review_id
                )
        })


    except ValueError as error:

        connection.rollback()


        return jsonify({
            "success": False,
            "message": str(error)
        }), 400


    except sqlite3.Error as error:

        connection.rollback()


        print(
            "Self-assessment submission error:",
            error
        )


        return jsonify({
            "success": False,

            "message":
                "The assessment could not be submitted."
        }), 500


    finally:

        connection.close()


# =====================================
# GLOBAL NOTIFICATION COUNT
# =====================================


@app.route("/notifications/feed")
def notification_feed():

    # =====================================
    # LOGIN CHECK
    # =====================================

    if "user_id" not in session:

        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401


    connection = get_db_connection()


    notifications = connection.execute(
        """
        SELECT

            notifications.id,

            notifications.notification_type,

            notifications.employee_review_id,

            notifications.title,

            notifications.message,

            notifications.is_read,

            notifications.created_at,

            review_cycles.cycle_name

        FROM notifications

        LEFT JOIN review_cycles
            ON notifications.review_cycle_id
            = review_cycles.id

        WHERE notifications.user_id = ?

        ORDER BY

            notifications.is_read ASC,

            notifications.created_at DESC,

            notifications.id DESC

        LIMIT 20
        """,

        (
            session["user_id"],
        )

    ).fetchall()


    unread_count = connection.execute(
        """
        SELECT COUNT(*) AS total

        FROM notifications

        WHERE user_id = ?

        AND is_read = 0
        """,

        (
            session["user_id"],
        )

    ).fetchone()["total"]


    notification_data = []


    for notification in notifications:

        target_url = url_for('dashboard')
        review_id = notification['employee_review_id']
        if review_id and notification['notification_type'].startswith('PAR_'):
            context = get_par_meeting_context(connection, review_id)
            if context and can_access_par_meeting(connection, context):
                target_url = url_for('par_meeting_workspace', employee_review_id=review_id)
        elif review_id and notification['notification_type'].startswith('PDP_'):
            context = get_pdp_context(connection, review_id)
            if context and (session['user_role'] == 'HR' or session['user_id'] in
                            (context['employee_user_id'], context['supervisor_id'])):
                target_url = url_for('pdp_workspace', employee_review_id=review_id)

        notification_data.append({

            'target_url': target_url,

            "id":
                notification["id"],

            "type":
                notification["notification_type"],

            "title":
                notification["title"],

            "message":
                notification["message"],

            "is_read":
                bool(notification["is_read"]),

            "created_at":
                notification["created_at"],

            "cycle_name":
                notification["cycle_name"]

        })


    connection.close()
    return jsonify({

        "success": True,

        "unread_count":
            unread_count,

        "notifications":
            notification_data

    })


@app.route(
    "/notifications/<int:notification_id>/read",
    methods=["POST"]
)
def mark_notification_read(notification_id):

    if "user_id" not in session:

        return jsonify({
            "success": False
        }), 401


    connection = get_db_connection()


    notification = connection.execute(
        """
        SELECT id

        FROM notifications

        WHERE id = ?

        AND user_id = ?
        """,

        (
            notification_id,
            session["user_id"]
        )

    ).fetchone()


    if notification is None:

        connection.close()

        return jsonify({
            "success": False,
            "message": "Notification not found."
        }), 404


    connection.execute(
        """
        UPDATE notifications

        SET
            is_read = 1,
            read_at = CURRENT_TIMESTAMP

        WHERE id = ?
        """,

        (
            notification_id,
        )
    )


    connection.commit()

    connection.close()


    return jsonify({
        "success": True
    })


@app.route(
    "/notifications/read-all",
    methods=["POST"]
)
def mark_all_notifications_read():

    if "user_id" not in session:

        return jsonify({
            "success": False
        }), 401


    connection = get_db_connection()


    connection.execute(
        """
        UPDATE notifications

        SET
            is_read = 1,
            read_at = CURRENT_TIMESTAMP

        WHERE user_id = ?

        AND is_read = 0
        """,

        (
            session["user_id"],
        )
    )


    connection.commit()

    connection.close()


    return jsonify({
        "success": True
    })


@app.context_processor
def inject_notification_count():

    if "user_id" not in session:

        return {
            "unread_notification_count": 0
        }


    connection = get_db_connection()


    unread_count = connection.execute(
        """
        SELECT COUNT(*) AS total

        FROM notifications

        WHERE user_id = ?

        AND is_read = 0
        """,

        (
            session["user_id"],
        )

    ).fetchone()["total"]


    connection.close()


    return {
        "unread_notification_count":
            unread_count
    }

@app.route("/", methods=["GET", "POST"])
def login():

    error = None

    if request.method == "POST":

        client_key = request.remote_addr or "unknown"

        now = time.monotonic()

        recent_attempts = [
            attempt
            for attempt in login_attempts.get(client_key, [])
            if now - attempt < LOGIN_ATTEMPT_WINDOW_SECONDS
        ]

        login_attempts[client_key] = recent_attempts

        if len(recent_attempts) >= LOGIN_ATTEMPT_LIMIT:
            return render_template(
                "login.html",
                error=(
                    "Too many unsuccessful sign-in attempts. "
                    "Please wait 15 minutes and try again."
                )
            ), 429

        email = request.form.get("email", "").strip().lower()

        password = request.form.get("password", "")

        if not is_altrium_email(email):
            error = "Please use your @altrium.com email address."
            recent_attempts.append(now)

            return render_template(
                "login.html",
                error=error
            ), 400

        connection = get_db_connection()

        user = connection.execute(
            """
            SELECT users.*, employees.status AS account_status FROM users
            LEFT JOIN employees ON employees.user_id = users.id
            WHERE lower(users.email) = ?
            """,
            (email,)
        ).fetchone()

        connection.close()

        if user is None:

            error = "Invalid email or password."

            recent_attempts.append(now)

        elif not check_password_hash(
            user["password"],
            password
        ):

            error = "Invalid email or password."

            recent_attempts.append(now)

        else:

            if user['account_status'] == 'Inactive':
                recent_attempts.append(now)
                return render_template('login.html', error='This account is inactive. Contact HR for assistance.'), 403
            login_attempts.pop(client_key, None)
            session.clear()
            session['_csrf_token'] = secrets.token_urlsafe(32)

            session["user_id"] = user["id"]

            session["user_name"] = user["full_name"]

            session["user_role"] = user["role"]
            session['_credential_version'] = credential_version(user['password'])

            return redirect(url_for("dashboard"))

    return render_template(
        "login.html",
        error=error
    )

@app.route("/dashboard")
def dashboard():

    # =====================================
    # LOGIN CHECK
    # =====================================

    if "user_id" not in session:
        return redirect(url_for("login"))


    connection = get_db_connection()

    # A few PAR meetings may have been marked as held before the outcome
    # workflow was introduced. Recreate their next, pending supervisor task
    # before the Action Stream is built.
    sync_par_workflow_actions(connection)
    ensure_pdp_schema(connection)
    sync_pdp_progress_actions(connection)
    dispatch_workflow_reminders(connection)
    connection.commit()


    # =====================================
    # DEFAULT DASHBOARD DATA
    # =====================================

    employee_count = 0

    current_active_cycle = None

    current_review = None

    supervisor_team_count = 0

    supervisor_active_reviews = 0

    manager_pending_approvals = 0

    hr_active_pdp_count = 0

    hr_pdp_attention_count = 0

    hr_pdp_in_progress_count = 0

    # The organisation journey is calculated from the active cohort, not
    # hard-coded to self-assessment. A cycle can contain reviews at different
    # points, so the earliest unfinished stage represents what must clear
    # before the organisation can move on as a whole.
    journey_stages = []


    # =====================================
    # HR FLOWBOARD DATA
    # =====================================

    if session["user_role"] == "HR":

        employee_count = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM employees

            JOIN users ON users.id = employees.user_id

            WHERE employees.status = 'Active'
            AND users.role = 'Employee'
            """
        ).fetchone()["total"]


        current_active_cycle = connection.execute(
            """
            SELECT

                review_cycles.id,
                review_cycles.cycle_name,
                review_cycles.cycle_year,
                review_cycles.cycle_number,
                review_cycles.start_date,
                review_cycles.end_date,
                review_cycles.status,

                (
                    SELECT COUNT(*)

                    FROM review_cycle_employees

                    WHERE review_cycle_employees.review_cycle_id
                        = review_cycles.id

                    AND review_cycle_employees.participation_status
                        = 'Assigned'

                ) AS assigned_count,

                (
                    SELECT COUNT(*)

                    FROM employee_reviews

                    WHERE employee_reviews.review_cycle_id
                        = review_cycles.id

                ) AS review_count

            FROM review_cycles

            WHERE status = 'Active'

            ORDER BY activated_at DESC

            LIMIT 1
            """
        ).fetchone()

        if current_active_cycle:
            status_rows = connection.execute(
                """
                SELECT status, COUNT(*) AS total
                FROM employee_reviews
                WHERE review_cycle_id = ?
                GROUP BY status
                """,
                (current_active_cycle["id"],),
            ).fetchall()
            status_counts = {
                row["status"]: row["total"] for row in status_rows
            }

            def review_stage(status):
                if status in ("Not Started", "Self Assessment In Progress", "Changes Requested"):
                    return 2
                if status in ("Self Assessment Submitted", "Peer Review In Progress"):
                    return 3
                if status in ("Peer Review Completed", "Supervisor Evaluation In Progress"):
                    return 4
                if status in ("Supervisor Evaluation Submitted", "Manager Approval Pending"):
                    return 5
                return 6

            stage_counts = {stage: 0 for stage in range(2, 7)}
            for status, total in status_counts.items():
                stage_counts[review_stage(status)] += total

            unfinished_stages = [
                stage for stage in range(2, 6) if stage_counts[stage]
            ]
            journey_current_stage = min(unfinished_stages) if unfinished_stages else 5
            journey_complete = (
                current_active_cycle["review_count"] > 0
                and stage_counts[6] == current_active_cycle["review_count"]
                and status_counts.get("Completed", 0) == current_active_cycle["review_count"]
            )

            stage_copy = {
                2: ("Self Assessment", "Employee reflection and evidence"),
                3: ("Peer Review", "Confidential colleague feedback"),
                4: ("Supervisor", "Evidence-based evaluation"),
                5: ("Approval", "Manager decision and outcome"),
            }
            for stage in range(2, 6):
                if journey_complete or stage < journey_current_stage:
                    state, state_label = "complete", "COMPLETE"
                elif stage == journey_current_stage:
                    state, state_label = "live", "CURRENT"
                else:
                    state = "queued"
                    state_label = "NEXT" if stage == journey_current_stage + 1 else "LATER"

                count = stage_counts[stage]
                if state == "complete":
                    detail = "Complete across the active review cohort"
                    metric, metric_label = "✓", "Clear"
                elif count:
                    detail = f"{count} review{'s' if count != 1 else ''} at this stage"
                    metric, metric_label = str(count), "active"
                elif stage == 3:
                    detail = "Waiting for peer assignments"
                    metric, metric_label = "0", "waiting"
                else:
                    detail = "Waiting for the preceding stage"
                    metric, metric_label = "0", "waiting"

                title, subtitle = stage_copy[stage]
                journey_stages.append({
                    "number": f"0{stage}", "title": title,
                    "subtitle": subtitle, "detail": detail,
                    "state": state, "state_label": state_label,
                    "metric": metric, "metric_label": metric_label,
                })

        pdp_signals = connection.execute(
                """
                SELECT
                    COUNT(DISTINCT pdp_plans.id) AS active_plans,
                    COUNT(DISTINCT CASE WHEN pdp_activities.status = 'In Progress'
                        THEN pdp_plans.id END) AS moving_plans,
                    COUNT(DISTINCT CASE WHEN pdp_activities.status != 'Completed'
                         AND date(pdp_activities.target_date) < date('now')
                        THEN pdp_plans.id END) AS overdue_plans
                FROM pdp_plans
                JOIN employee_reviews
                    ON employee_reviews.id = pdp_plans.employee_review_id
                LEFT JOIN pdp_activities
                    ON pdp_activities.pdp_plan_id = pdp_plans.id
                JOIN review_cycles ON review_cycles.id = employee_reviews.review_cycle_id
                WHERE review_cycles.status IN ('Active', 'Closed')
                  AND pdp_plans.status = 'Active'
                """
            ).fetchone()
        hr_active_pdp_count = pdp_signals["active_plans"] or 0
        hr_pdp_in_progress_count = pdp_signals["moving_plans"] or 0
        hr_pdp_attention_count = pdp_signals["overdue_plans"] or 0


    # =====================================
    # SUPERVISOR FLOWBOARD DATA
    # =====================================

    elif session["user_role"] == "Supervisor":

        supervisor_team_count = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM employees

            WHERE supervisor_id = ?

            AND status = 'Active'
            """,

            (
                session["user_id"],
            )

        ).fetchone()["total"]


        supervisor_active_reviews = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM employee_reviews

            JOIN review_cycles
                ON employee_reviews.review_cycle_id
                = review_cycles.id

            WHERE employee_reviews.supervisor_id = ?

            AND review_cycles.status = 'Active'
            """,

            (
                session["user_id"],
            )

        ).fetchone()["total"]


    # =====================================
    # EMPLOYEE FLOWBOARD DATA
    # =====================================

    elif session["user_role"] == "Employee":

        current_review = connection.execute(
            """
            SELECT

                employee_reviews.id
                    AS employee_review_id,

                employee_reviews.status
                    AS review_status,

                review_cycles.id
                    AS cycle_id,

                review_cycles.cycle_name,

                review_cycles.start_date,

                review_cycles.end_date,

                review_cycles.status
                    AS cycle_status,

                (
                    SELECT COUNT(*)

                    FROM review_plan_items

                    WHERE review_plan_items.employee_review_id
                        = employee_reviews.id

                ) AS baseline_item_count

            FROM employee_reviews

            JOIN review_cycles
                ON employee_reviews.review_cycle_id
                = review_cycles.id

            JOIN employees
                ON employee_reviews.employee_id
                = employees.id

            WHERE employees.user_id = ?

            AND review_cycles.status = 'Active'

            ORDER BY review_cycles.activated_at DESC

            LIMIT 1
            """,

            (
                session["user_id"],
            )

        ).fetchone()


    # =====================================
    # MANAGER FLOWBOARD DATA
    # =====================================

    elif session["user_role"] == "Manager":

        manager_pending_approvals = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM manager_approvals
            JOIN employee_reviews
                ON employee_reviews.id
                    = manager_approvals.employee_review_id
            JOIN review_cycles
                ON review_cycles.id
                    = employee_reviews.review_cycle_id
            WHERE manager_approvals.manager_id = ?
            AND manager_approvals.status = 'Pending'
            AND review_cycles.status = 'Active'
            """,
            (session["user_id"],)
        ).fetchone()["total"]


    # =====================================
    # ACTION STREAM
    # =====================================

    user_actions = connection.execute(
        """
        SELECT

            review_actions.id,

            review_actions.review_cycle_id,

            review_actions.employee_review_id,

            review_actions.action_type,

            review_actions.title,

            review_actions.description,

            review_actions.status,

            review_actions.priority,

            review_actions.due_date,

            review_actions.created_at,

            review_cycles.cycle_name,

            review_cycles.status
                AS cycle_status,

            employee_reviews.employee_name_snapshot
                AS review_employee_name

        FROM review_actions

        JOIN review_cycles
            ON review_actions.review_cycle_id
            = review_cycles.id

        LEFT JOIN employee_reviews
            ON review_actions.employee_review_id
            = employee_reviews.id

        WHERE review_actions.assigned_to = ?

        AND review_actions.status != 'Completed'

        AND (review_cycles.status = 'Active'
             OR (review_cycles.status = 'Closed' AND review_actions.action_type = 'PDP_PROGRESS'))

        ORDER BY

            CASE review_actions.priority

                WHEN 'High' THEN 1
                WHEN 'Normal' THEN 2
                WHEN 'Low' THEN 3
                ELSE 4

            END,

            review_actions.created_at DESC
        """,

        (
            session["user_id"],
        )

    ).fetchall()


    # =====================================
    # NOTIFICATION COUNT
    # =====================================

    unread_notification_count = connection.execute(
        """
        SELECT COUNT(*) AS total

        FROM notifications

        WHERE user_id = ?

        AND is_read = 0
        """,

        (
            session["user_id"],
        )

    ).fetchone()["total"]


    connection.close()


    # =====================================
    # RENDER FLOWBOARD
    # =====================================

    return render_template(
        "dashboard.html",

        employee_count=employee_count,

        current_active_cycle=current_active_cycle,

        current_review=current_review,

        supervisor_team_count=supervisor_team_count,

        supervisor_active_reviews=supervisor_active_reviews,

        manager_pending_approvals=manager_pending_approvals,

        hr_active_pdp_count=hr_active_pdp_count,

        hr_pdp_attention_count=hr_pdp_attention_count,

        hr_pdp_in_progress_count=hr_pdp_in_progress_count,

        journey_stages=journey_stages,

        user_actions=user_actions,

        unread_notification_count=unread_notification_count,

        user_name=session["user_name"],

        user_role=session["user_role"]
    )


@app.route(
    "/reviews/<int:employee_review_id>/self-assessment"
)
def self_assessment_studio(employee_review_id):

    # =====================================
    # LOGIN CHECK
    # =====================================

    if "user_id" not in session:
        return redirect(url_for("login"))


    # =====================================
    # EMPLOYEE ONLY
    # =====================================

    if session["user_role"] != "Employee":
        return redirect(url_for("dashboard"))


    connection = get_db_connection()


    try:

        # =====================================
        # GET REVIEW + VERIFY OWNERSHIP
        # =====================================

        review = connection.execute(
            """
            SELECT

                employee_reviews.id,
                employee_reviews.employee_id,
                employee_reviews.status
                    AS review_status,

                employee_reviews.employee_name_snapshot,
                employee_reviews.employee_code_snapshot,
                employee_reviews.department_snapshot,
                employee_reviews.job_title_snapshot,

                review_cycles.id
                    AS cycle_id,

                review_cycles.cycle_name,
                review_cycles.start_date,
                review_cycles.end_date,
                review_cycles.status
                    AS cycle_status,

                employees.user_id

            FROM employee_reviews

            JOIN review_cycles
                ON employee_reviews.review_cycle_id
                = review_cycles.id

            JOIN employees
                ON employee_reviews.employee_id
                = employees.id

            WHERE employee_reviews.id = ?

            AND employees.user_id = ?
            """,

            (
                employee_review_id,
                session["user_id"]
            )

        ).fetchone()


        if review is None:

            flash(
                "Self-assessment review not found.",
                "error"
            )

            return redirect(
                url_for("dashboard")
            )


        # =====================================
        # CYCLE MUST BE ACTIVE
        # =====================================

        if review["cycle_status"] != "Active":

            flash(
                "This review cycle is not currently active.",
                "error"
            )

            return redirect(
                url_for("dashboard")
            )


        # =====================================
        # FIND EXISTING SELF ASSESSMENT
        # =====================================

        self_assessment = connection.execute(
            """
            SELECT *

            FROM self_assessments

            WHERE employee_review_id = ?
            """,

            (
                employee_review_id,
            )

        ).fetchone()


        # =====================================
        # CREATE DRAFT ON FIRST OPEN
        # =====================================

        if self_assessment is None:

            assessment_cursor = connection.execute(
                """
                INSERT INTO self_assessments
                (
                    employee_review_id,
                    status
                )

                VALUES (?, ?)
                """,

                (
                    employee_review_id,
                    "Draft"
                )
            )


            self_assessment_id = (
                assessment_cursor.lastrowid
            )


            # ---------------------------------
            # UPDATE REVIEW WORKFLOW STATUS
            # ---------------------------------

            connection.execute(
                """
                UPDATE employee_reviews

                SET
                    status = 'Self Assessment In Progress',
                    updated_at = CURRENT_TIMESTAMP

                WHERE id = ?
                """,

                (
                    employee_review_id,
                )
            )


            connection.commit()


            self_assessment = connection.execute(
                """
                SELECT *

                FROM self_assessments

                WHERE id = ?
                """,

                (
                    self_assessment_id,
                )

            ).fetchone()


        # =====================================
        # GET FROZEN BASELINE + RESPONSES
        # =====================================

        baseline_items = connection.execute(
            """
            SELECT

                review_plan_items.id
                    AS review_plan_item_id,

                review_plan_items.item_type,

                review_plan_items.title,

                review_plan_items.description,

                review_plan_items.target,

                review_plan_items.due_date,

                self_assessment_items.id
                    AS response_id,

                self_assessment_items.rating,

                self_assessment_items.response_text

            FROM review_plan_items

            LEFT JOIN self_assessment_items
                ON self_assessment_items.review_plan_item_id
                    = review_plan_items.id

                AND self_assessment_items.self_assessment_id
                    = ?

            WHERE review_plan_items.employee_review_id = ?

            ORDER BY

                CASE review_plan_items.item_type

                    WHEN 'Responsibility' THEN 1
                    WHEN 'Expectation' THEN 2
                    WHEN 'KPI' THEN 3
                    WHEN 'Goal' THEN 4
                    ELSE 5

                END,

                review_plan_items.id
            """,

            (
                self_assessment["id"],
                employee_review_id
            )

        ).fetchall()


        # =====================================
        # ASSESSMENT EVIDENCE
        # =====================================

        evidence_files = connection.execute(
            """
            SELECT

                self_assessment_evidence.id,

                self_assessment_evidence.review_plan_item_id,

                self_assessment_evidence.original_file_name,

                self_assessment_evidence.mime_type,

                self_assessment_evidence.file_size,

                self_assessment_evidence.uploaded_at,

                review_plan_items.title
                    AS linked_item_title,

                review_plan_items.item_type
                    AS linked_item_type

            FROM self_assessment_evidence

            LEFT JOIN review_plan_items
                ON self_assessment_evidence.review_plan_item_id
                = review_plan_items.id

            WHERE self_assessment_evidence.self_assessment_id = ?

            ORDER BY
                self_assessment_evidence.uploaded_at DESC
            """,

            (
                self_assessment["id"],
            )

        ).fetchall()


        private_change_request = get_private_manager_change_request(
            connection,
            employee_review_id,
            session["user_id"]
        )


        return render_template(
            "self_assessment.html",

            review=review,

            self_assessment=self_assessment,

            baseline_items=baseline_items,

            evidence_files=evidence_files,

            private_change_request=private_change_request,

            user_name=session["user_name"],

            user_role=session["user_role"]
        )


    finally:

        connection.close()


@app.route("/employees")
def employees():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))

    connection = get_db_connection()


    employee_list = connection.execute(
        """
        SELECT
            employees.id,
            employees.employee_code,
            employees.department,
            employees.job_title,
            employees.hire_date,
            employees.status,
            users.full_name,
            users.email,
            users.role

        FROM employees

        JOIN users
            ON employees.user_id = users.id

        ORDER BY users.full_name
        """
    ).fetchall()


    supervisors = connection.execute(
        """
        SELECT
            users.id,
            users.full_name,
            employees.department

        FROM users
        JOIN employees ON employees.user_id = users.id

        WHERE users.role = 'Supervisor' AND employees.status = 'Active'

        ORDER BY full_name
        """
    ).fetchall()


    connection.close()


    return render_template(
        "employees.html",
        employees=employee_list,
        supervisors=supervisors,
        user_name=session["user_name"],
        user_role=session["user_role"]
    )

@app.route("/employees/<int:employee_id>")
def employee_profile(employee_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))


    connection = get_db_connection()


    employee = connection.execute(
        """
        SELECT

            employees.id,
            employees.user_id,
            employees.employee_code,
            employees.department,
            employees.job_title,
            employees.hire_date,
            employees.supervisor_id,
            employees.status,

            users.full_name,
            users.email,
            users.role,

            supervisor.full_name AS supervisor_name

        FROM employees

        JOIN users
            ON employees.user_id = users.id

        LEFT JOIN users AS supervisor
            ON employees.supervisor_id = supervisor.id

        WHERE employees.id = ?
        """,

        (employee_id,)

    ).fetchone()


    supervisors = connection.execute(
        """
        SELECT
            users.id,
            users.full_name,
            employees.department

        FROM users
        JOIN employees ON employees.user_id = users.id

        WHERE users.role = 'Supervisor' AND employees.status = 'Active'

        ORDER BY full_name
        """
    ).fetchall()


    connection.close()


    if employee is None:

        flash(
            "Employee profile could not be found.",
            "error"
        )

        return redirect(
            url_for("employees")
        )


    return render_template(
        "employee_profile.html",
        employee=employee,
        supervisors=supervisors,
        user_name=session["user_name"],
        user_role=session["user_role"]
    )

@app.route(
    "/employees/<int:employee_id>/edit",
    methods=["POST"]
)
def edit_employee(employee_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))


    full_name = request.form.get(
        "full_name",
        ""
    ).strip()


    employee_code = request.form.get(
        "employee_code",
        ""
    ).strip().upper()


    email = request.form.get(
        "email",
        ""
    ).strip().lower()


    department = request.form.get(
        "department",
        ""
    ).strip()


    job_title = request.form.get(
        "job_title",
        ""
    ).strip()


    hire_date = request.form.get(
        "hire_date",
        ""
    ).strip()


    supervisor_id = request.form.get(
        "supervisor_id",
        ""
    ).strip()


    status = request.form.get(
        "status",
        ""
    ).strip()


    # =====================================
    # VALIDATION
    # =====================================

    if (
        not full_name
        or not employee_code
        or not email
        or not department
        or not job_title
        or not hire_date
        or not status
    ):

        flash(
            "Please complete all required fields.",
            "error"
        )

        return redirect(
            url_for(
                "employee_profile",
                employee_id=employee_id
            )
        )


    if not is_altrium_email(email):

        flash(
            "Please enter a valid @altrium.com email address.",
            "error"
        )

        return redirect(
            url_for(
                "employee_profile",
                employee_id=employee_id
            )
        )


    if status not in {"Active", "Inactive"}:

        flash(
            "Please select a valid employee status.",
            "error"
        )

        return redirect(
            url_for(
                "employee_profile",
                employee_id=employee_id
            )
        )


    if supervisor_id:

        try:

            supervisor_id = int(
                supervisor_id
            )

        except ValueError:

            flash(
                "Invalid supervisor selection.",
                "error"
            )

            return redirect(
                url_for(
                    "employee_profile",
                    employee_id=employee_id
                )
            )

    else:

        supervisor_id = None


    connection = get_db_connection()


    try:

        # =====================================
        # FIND CURRENT EMPLOYEE
        # =====================================

        current_employee = connection.execute(
            """
            SELECT
                employees.id,
                employees.user_id,
                users.role

            FROM employees

            JOIN users ON users.id = employees.user_id

            WHERE employees.id = ?
            """,

            (employee_id,)

        ).fetchone()


        if current_employee is None:

            flash(
                "Employee profile could not be found.",
                "error"
            )

            connection.close()

            return redirect(
                url_for("employees")
            )


        user_id = current_employee["user_id"]

        if current_employee["role"] != "Employee":
            supervisor_id = None


        # =====================================
        # CHECK EMPLOYEE ID DUPLICATE
        # =====================================

        duplicate_code = connection.execute(
            """
            SELECT id

            FROM employees

            WHERE employee_code = ?
            AND id != ?
            """,

            (
                employee_code,
                employee_id
            )

        ).fetchone()


        if duplicate_code:

            flash(
                "That Employee ID already belongs to another employee.",
                "error"
            )

            connection.close()

            return redirect(
                url_for(
                    "employee_profile",
                    employee_id=employee_id
                )
            )


        # =====================================
        # CHECK EMAIL DUPLICATE
        # =====================================

        duplicate_email = connection.execute(
            """
            SELECT id

            FROM users

            WHERE email = ?
            AND id != ?
            """,

            (
                email,
                user_id
            )

        ).fetchone()


        if duplicate_email:

            flash(
                "That email address already belongs to another account.",
                "error"
            )

            connection.close()

            return redirect(
                url_for(
                    "employee_profile",
                    employee_id=employee_id
                )
            )


        # =====================================
        # VERIFY SUPERVISOR
        # =====================================

        if supervisor_id is not None:

            supervisor = connection.execute(
                """
                SELECT users.id
                FROM users JOIN employees ON employees.user_id = users.id
                WHERE users.id = ? AND users.role = 'Supervisor'
                AND employees.department = ? AND employees.status = 'Active'
                """,

                (supervisor_id, department)

            ).fetchone()


            if supervisor is None:

                flash(
                    "Select an active supervisor from the employee's department.",
                    "error"
                )

                connection.close()

                return redirect(
                    url_for(
                        "employee_profile",
                        employee_id=employee_id
                    )
                )


        # =====================================
        # UPDATE USER ACCOUNT
        # =====================================

        connection.execute(
            """
            UPDATE users

            SET
                full_name = ?,
                email = ?

            WHERE id = ?
            """,

            (
                full_name,
                email,
                user_id
            )
        )


        # =====================================
        # UPDATE EMPLOYEE PROFILE
        # =====================================

        connection.execute(
            """
            UPDATE employees

            SET
                employee_code = ?,
                department = ?,
                job_title = ?,
                hire_date = ?,
                supervisor_id = ?,
                status = ?

            WHERE id = ?
            """,

            (
                employee_code,
                department,
                job_title,
                hire_date,
                supervisor_id,
                status,
                employee_id
            )
        )


        connection.commit()


        flash(
            f"{full_name}'s profile was updated successfully.",
            "success"
        )


    except sqlite3.IntegrityError:

        connection.rollback()

        flash(
            "The employee profile could not be updated.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for(
            "employee_profile",
            employee_id=employee_id
        )
    )

@app.route("/employees/add", methods=["POST"])
def add_employee():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))


    full_name = request.form.get(
        "full_name",
        ""
    ).strip()


    employee_code = request.form.get(
        "employee_code",
        ""
    ).strip().upper()


    email = request.form.get(
        "email",
        ""
    ).strip().lower()


    hire_date = request.form.get(
        "hire_date",
        ""
    ).strip()


    department = request.form.get(
        "department",
        ""
    ).strip()


    job_title = request.form.get(
        "job_title",
        ""
    ).strip()


    account_role = request.form.get(
        "role",
        "Employee"
    ).strip()


    supervisor_id = request.form.get(
        "supervisor_id",
        ""
    ).strip()


    password = request.form.get(
        "password",
        ""
    )


    # =====================================
    # BASIC VALIDATION
    # =====================================

    allowed_account_roles = {
        "Employee",
        "Supervisor",
        "Manager"
    }

    if account_role not in allowed_account_roles:

        flash(
            "Please select a valid account role.",
            "error"
        )

        return redirect(
            url_for("employees")
        )

    if (
        not full_name
        or not employee_code
        or not email
        or not hire_date
        or not department
        or not job_title
        or not password
    ):

        flash(
            "Please complete all required fields.",
            "error"
        )

        return redirect(
            url_for("employees")
        )


    if not is_altrium_email(email):

        flash(
            "Please enter a valid @altrium.com email address.",
            "error"
        )

        return redirect(
            url_for("employees")
        )


    if len(password) < 12:

        flash(
            "Temporary password must contain at least 12 characters.",
            "error"
        )

        return redirect(
            url_for("employees")
        )


    if account_role != "Employee":

        supervisor_id = None

    elif supervisor_id:

        try:
            supervisor_id = int(
                supervisor_id
            )
        except ValueError:
            flash(
                "Invalid supervisor selection.",
                "error"
            )
            return redirect(url_for("employees"))

    else:

        supervisor_id = None


    connection = get_db_connection()


    try:

        # =====================================
        # CHECK EMPLOYEE CODE
        # =====================================

        existing_code = connection.execute(
            """
            SELECT id

            FROM employees

            WHERE employee_code = ?
            """,
            (employee_code,)
        ).fetchone()


        if existing_code:

            flash(
                "That Employee ID already exists.",
                "error"
            )

            connection.close()

            return redirect(
                url_for("employees")
            )


        # =====================================
        # CHECK EMAIL
        # =====================================

        existing_email = connection.execute(
            """
            SELECT id

            FROM users

            WHERE email = ?
            """,
            (email,)
        ).fetchone()


        if existing_email:

            flash(
                "An account with that email already exists.",
                "error"
            )

            connection.close()

            return redirect(
                url_for("employees")
            )


        # =====================================
        # VERIFY SUPERVISOR
        # =====================================

        if supervisor_id is not None:

            supervisor = connection.execute(
                """
                SELECT users.id
                FROM users JOIN employees ON employees.user_id = users.id
                WHERE users.id = ? AND users.role = 'Supervisor'
                AND employees.department = ? AND employees.status = 'Active'
                """,
                (supervisor_id, department)
            ).fetchone()


            if supervisor is None:

                flash(
                    "Select an active supervisor from the employee's department.",
                    "error"
                )

                connection.close()

                return redirect(
                    url_for("employees")
                )


        # =====================================
        # HASH PASSWORD
        # =====================================

        hashed_password = generate_password_hash(
            password
        )


        # =====================================
        # CREATE USER ACCOUNT
        # =====================================

        user_cursor = connection.execute(
            """
            INSERT INTO users
            (
                full_name,
                email,
                password,
                role
            )

            VALUES (?, ?, ?, ?)
            """,
            (
                full_name,
                email,
                hashed_password,
                account_role
            )
        )


        new_user_id = user_cursor.lastrowid


        # =====================================
        # CREATE EMPLOYEE PROFILE
        # =====================================

        connection.execute(
            """
            INSERT INTO employees
            (
                user_id,
                employee_code,
                department,
                job_title,
                hire_date,
                supervisor_id,
                status
            )

            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_user_id,
                employee_code,
                department,
                job_title,
                hire_date,
                supervisor_id,
                "Active"
            )
        )


        connection.commit()


        flash(
            (
                f"{full_name}'s {account_role.lower()} account "
                "and performance profile were created successfully."
            ),
            "success"
        )


    except sqlite3.IntegrityError:

        connection.rollback()

        flash(
            "The employee could not be created because some information already exists.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for("employees")
    )

@app.route("/my-team")
def my_team():

    if "user_id" not in session:
        return redirect(url_for("login"))


    if session["user_role"] != "Supervisor":
        return redirect(url_for("dashboard"))


    connection = get_db_connection()


    team_members = connection.execute(
        """
        SELECT
            employees.id,
            employees.employee_code,
            employees.department,
            employees.job_title,
            employees.status,

            users.full_name,
            users.email,


            (
                SELECT COUNT(*)

                FROM performance_items

                WHERE performance_items.employee_id = employees.id

                AND performance_items.item_type = 'Responsibility'

                AND performance_items.status = 'Active'

            ) AS responsibility_count,


            (
                SELECT COUNT(*)

                FROM performance_items

                WHERE performance_items.employee_id = employees.id

                AND performance_items.item_type = 'Expectation'

                AND performance_items.status = 'Active'

            ) AS expectation_count,


            (
                SELECT COUNT(*)

                FROM performance_items

                WHERE performance_items.employee_id = employees.id

                AND performance_items.item_type = 'KPI'

                AND performance_items.status = 'Active'

            ) AS kpi_count,


            (
                SELECT COUNT(*)

                FROM performance_items

                WHERE performance_items.employee_id = employees.id

                AND performance_items.item_type = 'Goal'

                AND performance_items.status = 'Active'

            ) AS goal_count


        FROM employees


        JOIN users
            ON employees.user_id = users.id


        WHERE employees.supervisor_id = ?


        ORDER BY users.full_name
        """,

        (
            session["user_id"],
        )

    ).fetchall()


    connection.close()


    return render_template(
        "my_team.html",

        team_members=team_members,

        user_name=session["user_name"],

        user_role=session["user_role"]
    )

@app.route("/my-team/<int:employee_id>/performance")
def manage_performance(employee_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "Supervisor":
        return redirect(url_for("dashboard"))


    connection = get_db_connection()


    employee = connection.execute(
        """
        SELECT

            employees.id,
            employees.employee_code,
            employees.department,
            employees.job_title,
            employees.status,

            users.full_name,
            users.email

        FROM employees

        JOIN users
            ON employees.user_id = users.id

        WHERE employees.id = ?

        AND employees.supervisor_id = ?
        """,

        (
            employee_id,
            session["user_id"]
        )

    ).fetchone()


    if employee is None:

        connection.close()

        flash(
            "You are not authorised to manage this employee.",
            "error"
        )

        return redirect(
            url_for("my_team")
        )


    performance_items = connection.execute(
        """
        SELECT *

        FROM performance_items

        WHERE employee_id = ?

        AND status = 'Active'

        ORDER BY created_at DESC
        """,

        (employee_id,)

    ).fetchall()

    # =====================================
    # PERFORMANCE PLAN SUMMARY
    # =====================================

    plan_counts = {
        "Responsibility": 0,
        "Expectation": 0,
        "KPI": 0,
        "Goal": 0
    }


    for item in performance_items:

        item_type = item["item_type"]

        if item_type in plan_counts:

            plan_counts[item_type] += 1


    total_items = sum(
        plan_counts.values()
    )


    has_role_definition = (
        plan_counts["Responsibility"] > 0
        or
        plan_counts["Expectation"] > 0
    )


    has_performance_outcome = (
        plan_counts["KPI"] > 0
        or
        plan_counts["Goal"] > 0
    )

    # =====================================
    # PERFORMANCE PLAN COVERAGE
    # =====================================

    has_responsibility = (
        plan_counts["Responsibility"] > 0
    )

    has_expectation = (
        plan_counts["Expectation"] > 0
    )

    has_kpi = (
        plan_counts["KPI"] > 0
    )

    has_goal = (
        plan_counts["Goal"] > 0
    )


    has_role_definition = (
        has_responsibility
        or
        has_expectation
    )

    has_performance_outcome = (
        has_kpi
        or
        has_goal
    )


    # =====================================
    # COVERAGE STATUS
    # =====================================

    if total_items == 0:

        coverage_status = "Not Started"

        coverage_message = (
            "No performance requirements have been "
            "defined for this employee yet."
        )


    elif (
        has_role_definition
        and
        has_performance_outcome
    ):

        coverage_status = "Balanced"


        # ALL FOUR ARE PRESENT

        if (
            has_responsibility
            and has_expectation
            and has_kpi
            and has_goal
        ):

            coverage_message = (
                "The plan has broad coverage across role "
                "responsibilities, behavioural expectations, "
                "measurable performance indicators and "
                "individual goals."
            )


        # RESPONSIBILITY + EXPECTATION + KPI

        elif (
            has_responsibility
            and has_expectation
            and has_kpi
        ):

            coverage_message = (
                "The plan covers role responsibilities, "
                "behavioural expectations and measurable "
                "performance indicators."
            )


        # RESPONSIBILITY + EXPECTATION + GOAL

        elif (
            has_responsibility
            and has_expectation
            and has_goal
        ):

            coverage_message = (
                "The plan defines role responsibilities, "
                "behavioural expectations and clear "
                "performance goals."
            )


        # RESPONSIBILITY + KPI + GOAL

        elif (
            has_responsibility
            and has_kpi
            and has_goal
        ):

            coverage_message = (
                "Core responsibilities are defined alongside "
                "measurable performance indicators and "
                "future goals."
            )


        # EXPECTATION + KPI + GOAL

        elif (
            has_expectation
            and has_kpi
            and has_goal
        ):

            coverage_message = (
                "Behavioural expectations are supported by "
                "measurable performance indicators and "
                "future performance goals."
            )


        # RESPONSIBILITY + KPI

        elif (
            has_responsibility
            and has_kpi
        ):

            coverage_message = (
                "Core responsibilities are defined and "
                "measurable performance indicators are "
                "in place."
            )


        # RESPONSIBILITY + GOAL

        elif (
            has_responsibility
            and has_goal
        ):

            coverage_message = (
                "Core responsibilities are defined alongside "
                "clear performance goals."
            )


        # EXPECTATION + KPI

        elif (
            has_expectation
            and has_kpi
        ):

            coverage_message = (
                "Behavioural expectations are defined and "
                "supported by measurable performance "
                "indicators."
            )


        # EXPECTATION + GOAL

        elif (
            has_expectation
            and has_goal
        ):

            coverage_message = (
                "Behavioural expectations and future "
                "performance goals are clearly defined."
            )


        else:

            coverage_message = (
                "The plan contains both role guidance "
                "and performance outcomes."
            )


    # =====================================
    # PARTIAL COVERAGE
    # =====================================

    else:

        coverage_status = "Partial"


        if (
            has_responsibility
            and has_expectation
        ):

            coverage_message = (
                "Responsibilities and behavioural expectations "
                "are defined, but no KPI or performance goal "
                "is currently included."
            )


        elif has_responsibility:

            coverage_message = (
                "Core responsibilities are defined, but no "
                "measurable KPI or performance goal is "
                "currently included."
            )


        elif has_expectation:

            coverage_message = (
                "Behavioural expectations are defined, but no "
                "measurable KPI or performance goal is "
                "currently included."
            )


        elif (
            has_kpi
            and has_goal
        ):

            coverage_message = (
                "Measurable performance indicators and goals "
                "are defined, but role responsibilities or "
                "behavioural expectations are not documented."
            )


        elif has_kpi:

            coverage_message = (
                "Measurable performance indicators are defined, "
                "but role responsibilities or behavioural "
                "expectations are not documented."
            )


        elif has_goal:

            coverage_message = (
                "Performance goals are defined, but role "
                "responsibilities or behavioural expectations "
                "are not documented."
            )


        else:

            coverage_message = (
                "The performance plan currently has "
                "limited coverage."
            )

    plan_summary = {

        "coverage_status":
            coverage_status,

        "message":
            coverage_message,

        "total":
            total_items,

        "responsibilities":
            plan_counts["Responsibility"],

        "expectations":
            plan_counts["Expectation"],

        "kpis":
            plan_counts["KPI"],

        "goals":
            plan_counts["Goal"]
    }


    connection.close()


    return render_template(
        "performance_workspace.html",
        employee=employee,
        performance_items=performance_items,
        plan_summary=plan_summary,
        user_name=session["user_name"],
        user_role=session["user_role"]
    )

@app.route(
    "/my-team/<int:employee_id>/performance/add",
    methods=["POST"]
)
def add_performance_item(employee_id):

    # =====================================
    # LOGIN CHECK
    # =====================================

    if "user_id" not in session:
        return redirect(url_for("login"))


    # =====================================
    # ROLE CHECK
    # =====================================

    if session["user_role"] != "Supervisor":
        return redirect(url_for("dashboard"))


    # =====================================
    # GET FORM DATA
    # =====================================

    item_type = request.form.get(
        "item_type",
        ""
    ).strip()


    title = request.form.get(
        "title",
        ""
    ).strip()


    description = request.form.get(
        "description",
        ""
    ).strip()


    target = request.form.get(
        "target",
        ""
    ).strip()


    due_date = request.form.get(
        "due_date",
        ""
    ).strip()


    # =====================================
    # ALLOWED PERFORMANCE TYPES
    # =====================================

    allowed_types = [
        "Responsibility",
        "Expectation",
        "KPI",
        "Goal"
    ]


    if item_type not in allowed_types:

        flash(
            "Invalid performance item type.",
            "error"
        )

        return redirect(
            url_for(
                "manage_performance",
                employee_id=employee_id
            )
        )


    # =====================================
    # TITLE VALIDATION
    # =====================================

    if not title:

        flash(
            "Please enter a title.",
            "error"
        )

        return redirect(
            url_for(
                "manage_performance",
                employee_id=employee_id
            )
        )


    # =====================================
    # KPI VALIDATION
    # =====================================

    if item_type == "KPI" and not target:

        flash(
            "A KPI must include a performance target.",
            "error"
        )

        return redirect(
            url_for(
                "manage_performance",
                employee_id=employee_id
            )
        )


    # =====================================
    # CLEAN UNUSED FIELDS
    # =====================================

    if item_type in [
        "Responsibility",
        "Expectation"
    ]:

        target = None
        due_date = None


    elif item_type == "KPI":

        due_date = None


    elif item_type == "Goal":

        target = target or None
        due_date = due_date or None


    # =====================================
    # DATABASE CONNECTION
    # =====================================

    connection = get_db_connection()


    try:

        # =====================================
        # VERIFY SUPERVISOR OWNS EMPLOYEE
        # =====================================

        employee = connection.execute(
            """
            SELECT
                id

            FROM employees

            WHERE id = ?

            AND supervisor_id = ?
            """,

            (
                employee_id,
                session["user_id"]
            )

        ).fetchone()


        if employee is None:

            flash(
                "You are not authorised to manage this employee.",
                "error"
            )

            return redirect(
                url_for("my_team")
            )


        # =====================================
        # CREATE PERFORMANCE ITEM
        # =====================================

        item_cursor = connection.execute(
            """
            INSERT INTO performance_items
            (
                employee_id,
                item_type,
                title,
                description,
                target,
                due_date,
                created_by,
                status,
                updated_by
            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,

            (
                employee_id,
                item_type,
                title,
                description or None,
                target,
                due_date,
                session["user_id"],
                "Active",
                session["user_id"]
            )
        )


        # =====================================
        # GET NEW PERFORMANCE ITEM ID
        # =====================================

        new_item_id = item_cursor.lastrowid


        # =====================================
        # CREATE AUDIT HISTORY RECORD
        # =====================================

        connection.execute(
            """
            INSERT INTO performance_item_history
            (
                performance_item_id,
                employee_id,
                action,
                item_type,
                title,
                description,
                target,
                due_date,
                performed_by
            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,

            (
                new_item_id,
                employee_id,
                "Created",
                item_type,
                title,
                description or None,
                target,
                due_date,
                session["user_id"]
            )
        )


        # =====================================
        # SAVE BOTH RECORDS
        # =====================================

        connection.commit()


        flash(
            f"{item_type} created successfully.",
            "success"
        )


    except sqlite3.IntegrityError:

        connection.rollback()


        flash(
            "The performance item could not be created.",
            "error"
        )


    finally:

        connection.close()


    # =====================================
    # RETURN TO PERFORMANCE BLUEPRINT
    # =====================================

    return redirect(
        url_for(
            "manage_performance",
            employee_id=employee_id
        )
    )

@app.route(
    "/my-team/<int:employee_id>/performance/<int:item_id>/edit",
    methods=["POST"]
)
def edit_performance_item(employee_id, item_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "Supervisor":
        return redirect(url_for("dashboard"))


    title = request.form.get(
        "title",
        ""
    ).strip()

    description = request.form.get(
        "description",
        ""
    ).strip()

    target = request.form.get(
        "target",
        ""
    ).strip()

    due_date = request.form.get(
        "due_date",
        ""
    ).strip()


    if not title:

        flash(
            "Please enter a title.",
            "error"
        )

        return redirect(
            url_for(
                "manage_performance",
                employee_id=employee_id
            )
        )


    connection = get_db_connection()


    try:

        # Find item and verify supervisor ownership

        item = connection.execute(
            """
            SELECT
                performance_items.*

            FROM performance_items

            JOIN employees
                ON performance_items.employee_id = employees.id

            WHERE performance_items.id = ?

            AND performance_items.employee_id = ?

            AND employees.supervisor_id = ?

            AND performance_items.status = 'Active'
            """,

            (
                item_id,
                employee_id,
                session["user_id"]
            )

        ).fetchone()


        if item is None:

            flash(
                "You are not authorised to edit this performance item.",
                "error"
            )

            return redirect(
                url_for(
                    "manage_performance",
                    employee_id=employee_id
                )
            )


        item_type = item["item_type"]


        # =====================================
        # TYPE-SPECIFIC RULES
        # =====================================

        if item_type == "KPI" and not target:

            flash(
                "A KPI must include a performance target.",
                "error"
            )

            return redirect(
                url_for(
                    "manage_performance",
                    employee_id=employee_id
                )
            )


        if item_type in [
            "Responsibility",
            "Expectation"
        ]:

            target = None
            due_date = None


        elif item_type == "KPI":

            due_date = None


        elif item_type == "Goal":

            target = target or None
            due_date = due_date or None


        # =====================================
        # UPDATE CURRENT VERSION
        # =====================================

        connection.execute(
            """
            UPDATE performance_items

            SET
                title = ?,
                description = ?,
                target = ?,
                due_date = ?,
                updated_by = ?,
                updated_at = CURRENT_TIMESTAMP

            WHERE id = ?
            """,

            (
                title,
                description or None,
                target,
                due_date,
                session["user_id"],
                item_id
            )
        )


        # =====================================
        # AUDIT HISTORY
        # =====================================

        connection.execute(
            """
            INSERT INTO performance_item_history
            (
                performance_item_id,
                employee_id,
                action,
                item_type,
                title,
                description,
                target,
                due_date,
                performed_by
            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,

            (
                item_id,
                employee_id,
                "Updated",
                item_type,
                title,
                description or None,
                target,
                due_date,
                session["user_id"]
            )
        )


        connection.commit()


        flash(
            f"{item_type} updated successfully.",
            "success"
        )


    except sqlite3.IntegrityError:

        connection.rollback()

        flash(
            "The performance item could not be updated.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for(
            "manage_performance",
            employee_id=employee_id
        )
    )

@app.route(
    "/my-team/<int:employee_id>/performance/<int:item_id>/archive",
    methods=["POST"]
)
def archive_performance_item(employee_id, item_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "Supervisor":
        return redirect(url_for("dashboard"))


    connection = get_db_connection()


    try:

        # =====================================
        # FIND + AUTHORISE ITEM
        # =====================================

        item = connection.execute(
            """
            SELECT
                performance_items.*

            FROM performance_items

            JOIN employees
                ON performance_items.employee_id = employees.id

            WHERE performance_items.id = ?

            AND performance_items.employee_id = ?

            AND employees.supervisor_id = ?

            AND performance_items.status = 'Active'
            """,

            (
                item_id,
                employee_id,
                session["user_id"]
            )

        ).fetchone()


        if item is None:

            flash(
                "You are not authorised to archive this performance item.",
                "error"
            )

            return redirect(
                url_for(
                    "manage_performance",
                    employee_id=employee_id
                )
            )


        # =====================================
        # ARCHIVE
        # =====================================

        connection.execute(
            """
            UPDATE performance_items

            SET
                status = 'Archived',
                archived_at = CURRENT_TIMESTAMP,
                archived_by = ?,
                updated_by = ?,
                updated_at = CURRENT_TIMESTAMP

            WHERE id = ?
            """,

            (
                session["user_id"],
                session["user_id"],
                item_id
            )
        )


        # =====================================
        # AUDIT HISTORY
        # =====================================

        connection.execute(
            """
            INSERT INTO performance_item_history
            (
                performance_item_id,
                employee_id,
                action,
                item_type,
                title,
                description,
                target,
                due_date,
                performed_by
            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,

            (
                item_id,
                employee_id,
                "Archived",
                item["item_type"],
                item["title"],
                item["description"],
                item["target"],
                item["due_date"],
                session["user_id"]
            )
        )


        connection.commit()


        flash(
            f"{item['item_type']} archived successfully.",
            "success"
        )


    except sqlite3.IntegrityError:

        connection.rollback()

        flash(
            "The performance item could not be archived.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for(
            "manage_performance",
            employee_id=employee_id
        )
    )

@app.route(
    "/my-team/<int:employee_id>/performance/<int:item_id>/history"
)
def performance_item_history(employee_id, item_id):

    # =====================================
    # LOGIN CHECK
    # =====================================

    if "user_id" not in session:

        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401


    # =====================================
    # ROLE CHECK
    # =====================================

    if session["user_role"] != "Supervisor":

        return jsonify({
            "success": False,
            "message": "Access denied."
        }), 403


    connection = get_db_connection()


    try:

        # =====================================
        # AUTHORISATION CHECK
        # =====================================

        item = connection.execute(
            """
            SELECT
                performance_items.id

            FROM performance_items

            JOIN employees
                ON performance_items.employee_id = employees.id

            WHERE performance_items.id = ?

            AND performance_items.employee_id = ?

            AND employees.supervisor_id = ?
            """,

            (
                item_id,
                employee_id,
                session["user_id"]
            )

        ).fetchone()


        if item is None:

            return jsonify({
                "success": False,
                "message": "Performance item not found or access denied."
            }), 404


        # =====================================
        # GET AUDIT HISTORY
        # =====================================

        history = connection.execute(
            """
            SELECT
                performance_item_history.id,
                performance_item_history.action,
                performance_item_history.item_type,
                performance_item_history.title,
                performance_item_history.description,
                performance_item_history.target,
                performance_item_history.due_date,
                performance_item_history.performed_at,

                users.full_name AS performed_by_name

            FROM performance_item_history

            JOIN users
                ON performance_item_history.performed_by = users.id

            WHERE performance_item_history.performance_item_id = ?

            ORDER BY
                performance_item_history.performed_at DESC,
                performance_item_history.id DESC
            """,

            (item_id,)

        ).fetchall()


        history_data = []


        for record in history:

            history_data.append({

                "id":
                    record["id"],

                "action":
                    record["action"],

                "item_type":
                    record["item_type"],

                "title":
                    record["title"],

                "description":
                    record["description"],

                "target":
                    record["target"],

                "due_date":
                    record["due_date"],

                "performed_at":
                    record["performed_at"],

                "performed_by":
                    record["performed_by_name"]

            })


        return jsonify({
            "success": True,
            "history": history_data
        })


    finally:

        connection.close()

@app.route("/review-cycles")
def review_cycles():

    # =====================================
    # LOGIN CHECK
    # =====================================

    if "user_id" not in session:
        return redirect(url_for("login"))


    # =====================================
    # HR ONLY
    # =====================================

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))


    # =====================================
    # SELECTED PROGRAM YEAR
    # =====================================

    current_year = datetime.now().year


    try:

        selected_year = int(
            request.args.get(
                "year",
                current_year
            )
        )


    except ValueError:

        selected_year = current_year


    connection = get_db_connection()


    # =====================================
    # GET REVIEW CYCLES + EMPLOYEE COUNTS
    # =====================================

    cycles = connection.execute(
        """
        SELECT

            review_cycles.id,
            review_cycles.cycle_name,
            review_cycles.cycle_year,
            review_cycles.cycle_number,
            review_cycles.start_date,
            review_cycles.end_date,
            review_cycles.status,

            COUNT(
                review_cycle_employees.id
            ) AS employee_count

        FROM review_cycles

        LEFT JOIN review_cycle_employees
            ON review_cycle_employees.review_cycle_id
            = review_cycles.id

            AND review_cycle_employees.participation_status
            = 'Assigned'

        WHERE review_cycles.cycle_year = ?

        GROUP BY review_cycles.id

        ORDER BY review_cycles.cycle_number
        """,

        (selected_year,)

    ).fetchall()


    # =====================================
    # TURN DATABASE RESULTS INTO 3 SLOTS
    # =====================================

    cycle_map = {

        cycle["cycle_number"]: cycle

        for cycle in cycles

    }


    # =====================================
    # AVAILABLE YEARS
    # =====================================

    year_rows = connection.execute(
        """
        SELECT DISTINCT cycle_year

        FROM review_cycles

        ORDER BY cycle_year DESC
        """
    ).fetchall()


    available_years = [

        row["cycle_year"]

        for row in year_rows

    ]


    # Always allow current/selected year

    if current_year not in available_years:
        available_years.append(current_year)


    if selected_year not in available_years:
        available_years.append(selected_year)


    available_years.sort(
        reverse=True
    )


    connection.close()


    return render_template(
        "review_cycles.html",

        selected_year=selected_year,

        current_year=current_year,

        cycle_map=cycle_map,

        available_years=available_years,

        user_name=session["user_name"],

        user_role=session["user_role"]
    )

@app.route(
    "/review-cycles/add",
    methods=["POST"]
)
def add_review_cycle():

    # =====================================
    # LOGIN CHECK
    # =====================================

    if "user_id" not in session:
        return redirect(url_for("login"))


    # =====================================
    # HR ONLY
    # =====================================

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))


    # =====================================
    # GET FORM DATA
    # =====================================

    cycle_name = request.form.get(
        "cycle_name",
        ""
    ).strip()


    cycle_year_raw = request.form.get(
        "cycle_year",
        ""
    ).strip()


    cycle_number_raw = request.form.get(
        "cycle_number",
        ""
    ).strip()


    start_date = request.form.get(
        "start_date",
        ""
    ).strip()


    end_date = request.form.get(
        "end_date",
        ""
    ).strip()


    # =====================================
    # VALIDATE YEAR + CYCLE NUMBER
    # =====================================

    try:

        cycle_year = int(
            cycle_year_raw
        )

        cycle_number = int(
            cycle_number_raw
        )


    except ValueError:

        flash(
            "Invalid review cycle information.",
            "error"
        )

        return redirect(
            url_for("review_cycles")
        )


    # Only three review windows per year

    if cycle_number not in [1, 2, 3]:

        flash(
            "Review cycle number must be between 1 and 3.",
            "error"
        )

        return redirect(
            url_for(
                "review_cycles",
                year=cycle_year
            )
        )


    # =====================================
    # REQUIRED FIELDS
    # =====================================

    if not cycle_name:

        flash(
            "Please enter a cycle name.",
            "error"
        )

        return redirect(
            url_for(
                "review_cycles",
                year=cycle_year
            )
        )


    if not start_date or not end_date:

        flash(
            "Please select both the start and end dates.",
            "error"
        )

        return redirect(
            url_for(
                "review_cycles",
                year=cycle_year
            )
        )


    # =====================================
    # DATE VALIDATION
    # =====================================

    try:

        parsed_start_date = datetime.strptime(
            start_date,
            "%Y-%m-%d"
        ).date()


        parsed_end_date = datetime.strptime(
            end_date,
            "%Y-%m-%d"
        ).date()


    except ValueError:

        flash(
            "Please enter valid review cycle dates.",
            "error"
        )

        return redirect(
            url_for(
                "review_cycles",
                year=cycle_year
            )
        )


    # Start date must come before end date

    if parsed_start_date >= parsed_end_date:

        flash(
            "The review cycle end date must be after the start date.",
            "error"
        )

        return redirect(
            url_for(
                "review_cycles",
                year=cycle_year
            )
        )


    # For this annual programme,
    # both dates must belong to the selected year

    if (
        parsed_start_date.year != cycle_year
        or
        parsed_end_date.year != cycle_year
    ):

        flash(
            f"Cycle dates must fall within the {cycle_year} review year.",
            "error"
        )

        return redirect(
            url_for(
                "review_cycles",
                year=cycle_year
            )
        )


    connection = get_db_connection()


    try:

        # =====================================
        # CHECK IF THIS CYCLE SLOT EXISTS
        # =====================================

        existing_cycle = connection.execute(
            """
            SELECT id

            FROM review_cycles

            WHERE cycle_year = ?

            AND cycle_number = ?
            """,

            (
                cycle_year,
                cycle_number
            )

        ).fetchone()


        if existing_cycle:

            flash(
                f"Cycle {cycle_number} is already configured for {cycle_year}.",
                "error"
            )

            return redirect(
                url_for(
                    "review_cycles",
                    year=cycle_year
                )
            )


        # =====================================
        # CHECK FOR DATE OVERLAP
        # =====================================

        overlapping_cycle = connection.execute(
            """
            SELECT
                id,
                cycle_name,
                start_date,
                end_date

            FROM review_cycles

            WHERE cycle_year = ?

            AND NOT (
                end_date < ?
                OR
                start_date > ?
            )
            """,

            (
                cycle_year,
                start_date,
                end_date
            )

        ).fetchone()


        if overlapping_cycle:

            flash(
                (
                    f"The selected dates overlap with "
                    f"{overlapping_cycle['cycle_name']} "
                    f"({overlapping_cycle['start_date']} "
                    f"to {overlapping_cycle['end_date']})."
                ),
                "error"
            )

            return redirect(
                url_for(
                    "review_cycles",
                    year=cycle_year
                )
            )


        # =====================================
        # CREATE CYCLE AS DRAFT
        # =====================================

        connection.execute(
            """
            INSERT INTO review_cycles
            (
                cycle_name,
                cycle_year,
                cycle_number,
                start_date,
                end_date,
                status,
                created_by
            )

            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,

            (
                cycle_name,
                cycle_year,
                cycle_number,
                start_date,
                end_date,
                "Draft",
                session["user_id"]
            )
        )


        connection.commit()


        flash(
            f"{cycle_name} created as a draft review cycle.",
            "success"
        )


    except sqlite3.IntegrityError:

        connection.rollback()


        flash(
            "The review cycle could not be created.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for(
            "review_cycles",
            year=cycle_year
        )
    )

@app.route("/review-cycles/<int:cycle_id>")
def review_cycle_workspace(cycle_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))


    connection = get_db_connection()


    # =====================================
    # GET CYCLE
    # =====================================

    cycle = connection.execute(
        """
        SELECT
            id,
            cycle_name,
            cycle_year,
            cycle_number,
            start_date,
            end_date,
            status

        FROM review_cycles

        WHERE id = ?
        """,

        (cycle_id,)

    ).fetchone()


    if cycle is None:

        connection.close()

        flash(
            "Review cycle not found.",
            "error"
        )

        return redirect(
            url_for("review_cycles")
        )


    # =====================================
    # ASSIGNED EMPLOYEES
    # =====================================

    assigned_employees = connection.execute(
        """
        SELECT

            review_cycle_employees.id
                AS assignment_id,

            review_cycle_employees.participation_status,

            employees.id
                AS employee_id,

            employees.employee_code,
            employees.department,
            employees.job_title,
            employees.status
                AS employee_status,

            users.full_name,
            users.email,

            supervisor.full_name
                AS supervisor_name,

            employee_reviews.id
                AS employee_review_id,

            employee_reviews.status
                AS employee_review_status,

            (
                SELECT COUNT(*)

                FROM performance_items

                WHERE performance_items.employee_id
                    = employees.id

                AND performance_items.status
                    = 'Active'

            ) AS performance_item_count

        FROM review_cycle_employees

        JOIN employees
            ON review_cycle_employees.employee_id
            = employees.id

        JOIN users
            ON employees.user_id
            = users.id

        LEFT JOIN users AS supervisor
            ON employees.supervisor_id
            = supervisor.id

        LEFT JOIN employee_reviews
            ON employee_reviews.review_cycle_id
                = review_cycle_employees.review_cycle_id
            AND employee_reviews.employee_id
                = review_cycle_employees.employee_id

        WHERE review_cycle_employees.review_cycle_id = ?

        AND review_cycle_employees.participation_status
            = 'Assigned'

        ORDER BY users.full_name
        """,

        (cycle_id,)

    ).fetchall()

        # =====================================
    # CYCLE READINESS
    # =====================================

    readiness_blockers = []

    readiness_warnings = []


    assigned_count = len(
        assigned_employees
    )


    missing_supervisor_count = sum(
        1
        for employee in assigned_employees
        if not employee["supervisor_name"]
    )


    inactive_employee_count = sum(
        1
        for employee in assigned_employees
        if employee["employee_status"] != "Active"
    )


    missing_blueprint_count = sum(
        1
        for employee in assigned_employees
        if employee["performance_item_count"] == 0
    )


    # =====================================
    # BLOCKERS
    # =====================================

    if assigned_count == 0:

        readiness_blockers.append(
            "At least one employee must be assigned "
            "before this review cycle can be scheduled."
        )


    if missing_supervisor_count > 0:

        readiness_blockers.append(
            f"{missing_supervisor_count} assigned employee(s) "
            "do not have a Supervisor."
        )


    if inactive_employee_count > 0:

        readiness_blockers.append(
            f"{inactive_employee_count} assigned employee(s) "
            "are no longer active."
        )


    # =====================================
    # WARNINGS
    # =====================================

    if missing_blueprint_count > 0:

        readiness_warnings.append(
            f"{missing_blueprint_count} assigned employee(s) "
            "do not currently have an active performance Blueprint."
        )


    # =====================================
    # FINAL READINESS
    # =====================================

    can_schedule = (
        len(readiness_blockers) == 0
    )


    cycle_readiness = {

        "can_schedule":
            can_schedule,

        "blockers":
            readiness_blockers,

        "warnings":
            readiness_warnings,

        "missing_supervisors":
            missing_supervisor_count,

        "missing_blueprints":
            missing_blueprint_count
    }


    # =====================================
    # ACTIVATION READINESS
    # =====================================

    activation_blockers = []


    if cycle["status"] == "Scheduled":

        today = datetime.now().date()


        cycle_start = datetime.strptime(
            cycle["start_date"],
            "%Y-%m-%d"
        ).date()


        cycle_end = datetime.strptime(
            cycle["end_date"],
            "%Y-%m-%d"
        ).date()


        # Cycle cannot begin before its start date

        if today < cycle_start:

            activation_blockers.append(
                (
                    "This review cycle is scheduled to begin "
                    f"on {cycle['start_date']}."
                )
            )


        # Expired scheduled cycle should not be activated

        if today > cycle_end:

            activation_blockers.append(
                (
                    "The configured review period has already ended. "
                    "HR must correct the cycle dates before activation."
                )
            )


        # No employees

        if len(assigned_employees) == 0:

            activation_blockers.append(
                "The cycle has no assigned employees."
            )


        # Missing Supervisor

        activation_missing_supervisors = sum(
            1
            for employee in assigned_employees
            if not employee["supervisor_name"]
        )


        if activation_missing_supervisors > 0:

            activation_blockers.append(
                (
                    f"{activation_missing_supervisors} employee(s) "
                    "do not have an assigned Supervisor."
                )
            )


        # Missing Performance Blueprint

        activation_missing_blueprints = sum(
            1
            for employee in assigned_employees
            if employee["performance_item_count"] == 0
        )


        if activation_missing_blueprints > 0:

            activation_blockers.append(
                (
                    f"{activation_missing_blueprints} employee(s) "
                    "do not have an active Performance Blueprint."
                )
            )


    activation_readiness = {

        "can_activate":
            (
                cycle["status"] == "Scheduled"
                and len(activation_blockers) == 0
            ),

        "blockers":
            activation_blockers
    }


    completed_review_count = sum(
        1
        for employee in assigned_employees
        if employee["employee_review_status"] == "Completed"
    )

    post_review_pending = pending_post_review_count(connection, cycle_id)
    closure_readiness = {
        "completed": completed_review_count,
        "total": assigned_count,
        "post_review_pending": post_review_pending,
        "can_close": (
            cycle["status"] == "Active"
            and assigned_count > 0
            and completed_review_count == assigned_count
            and post_review_pending == 0
        )
    }


    # =====================================
    # ELIGIBLE EMPLOYEE POOL
    #
    # Active employees who are NOT already
    # assigned to another cycle this year.
    # =====================================

    eligible_employees = connection.execute(
        """
        SELECT
            employees.id,
            employees.employee_code,
            employees.department,
            employees.job_title,

            users.full_name,
            users.email,

            supervisor.full_name
                AS supervisor_name

        FROM employees

        JOIN users
            ON employees.user_id
            = users.id

        LEFT JOIN users AS supervisor
            ON employees.supervisor_id
            = supervisor.id

        WHERE employees.status = 'Active'

        AND users.role = 'Employee'

        AND EXISTS (
            SELECT 1 FROM performance_items
            WHERE performance_items.employee_id = employees.id
            AND performance_items.status = 'Active'
        )

        AND employees.id NOT IN (

            SELECT
                review_cycle_employees.employee_id

            FROM review_cycle_employees

            JOIN review_cycles
                ON review_cycle_employees.review_cycle_id
                = review_cycles.id

            WHERE review_cycles.cycle_year = ?

            AND review_cycle_employees.participation_status
                = 'Assigned'
        )

        ORDER BY users.full_name
        """,

        (cycle["cycle_year"],)

    ).fetchall()


    connection.close()


    return render_template(
        "review_cycle_workspace.html",

        cycle=cycle,

        assigned_employees=assigned_employees,

        eligible_employees=eligible_employees,

        cycle_readiness=cycle_readiness,

        activation_readiness=activation_readiness,

        closure_readiness=closure_readiness,

        user_name=session["user_name"],

        user_role=session["user_role"]
    )

@app.route(
    "/review-cycles/<int:cycle_id>/activate",
    methods=["POST"]
)
def activate_review_cycle(cycle_id):

    # =====================================
    # AUTHENTICATION
    # =====================================

    if "user_id" not in session:
        return redirect(url_for("login"))


    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))


    connection = get_db_connection()


    try:

        # =====================================
        # GET CYCLE
        # =====================================

        cycle = connection.execute(
            """
            SELECT
                id,
                cycle_name,
                start_date,
                end_date,
                status

            FROM review_cycles

            WHERE id = ?
            """,

            (cycle_id,)

        ).fetchone()


        if cycle is None:

            flash(
                "Review cycle not found.",
                "error"
            )

            return redirect(
                url_for("review_cycles")
            )


        # =====================================
        # ONLY SCHEDULED → ACTIVE
        # =====================================

        if cycle["status"] != "Scheduled":

            flash(
                "Only Scheduled review cycles can be activated.",
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        # =====================================
        # DATE VALIDATION
        # =====================================

        today = datetime.now().date()


        cycle_start = datetime.strptime(
            cycle["start_date"],
            "%Y-%m-%d"
        ).date()


        cycle_end = datetime.strptime(
            cycle["end_date"],
            "%Y-%m-%d"
        ).date()


        if today < cycle_start:

            flash(
                (
                    "This review cycle cannot be activated "
                    f"before {cycle['start_date']}."
                ),
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        if today > cycle_end:

            flash(
                (
                    "This review cycle cannot be activated "
                    "because its review period has already ended."
                ),
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        # =====================================
        # GET COHORT
        # =====================================

        assignments = connection.execute(
            """
            SELECT

                review_cycle_employees.id
                    AS assignment_id,

                employees.id
                    AS employee_id,

                employees.user_id 
                    AS employee_user_id,

                employees.employee_code,
                employees.department,
                employees.job_title,
                employees.status
                    AS employee_status,

                employees.supervisor_id,

                users.full_name

            FROM review_cycle_employees

            JOIN employees
                ON review_cycle_employees.employee_id
                = employees.id

            JOIN users
                ON employees.user_id
                = users.id

            WHERE review_cycle_employees.review_cycle_id = ?

            AND review_cycle_employees.participation_status
                = 'Assigned'

            ORDER BY users.full_name
            """,

            (cycle_id,)

        ).fetchall()


        if not assignments:

            flash(
                "The review cycle has no assigned employees.",
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        # =====================================
        # VALIDATE ENTIRE COHORT FIRST
        # =====================================

        blueprint_cache = {}


        for assignment in assignments:


            if assignment["employee_status"] != "Active":

                flash(
                    (
                        f"{assignment['full_name']} is not active. "
                        "Review the cohort before activation."
                    ),
                    "error"
                )

                return redirect(
                    url_for(
                        "review_cycle_workspace",
                        cycle_id=cycle_id
                    )
                )


            if assignment["supervisor_id"] is None:

                flash(
                    (
                        f"{assignment['full_name']} does not "
                        "have an assigned Supervisor."
                    ),
                    "error"
                )

                return redirect(
                    url_for(
                        "review_cycle_workspace",
                        cycle_id=cycle_id
                    )
                )


            performance_items = connection.execute(
                """
                SELECT
                    id,
                    item_type,
                    title,
                    description,
                    target,
                    due_date

                FROM performance_items

                WHERE employee_id = ?

                AND status = 'Active'

                ORDER BY id
                """,

                (
                    assignment["employee_id"],
                )

            ).fetchall()


            if not performance_items:

                flash(
                    (
                        f"{assignment['full_name']} does not "
                        "have an active Performance Blueprint."
                    ),
                    "error"
                )

                return redirect(
                    url_for(
                        "review_cycle_workspace",
                        cycle_id=cycle_id
                    )
                )


            blueprint_cache[
                assignment["employee_id"]
            ] = performance_items


        # =====================================
        # CREATE EMPLOYEE REVIEW CASES
        # =====================================

        for assignment in assignments:


            review_cursor = connection.execute(
                """
                INSERT INTO employee_reviews
                (
                    assignment_id,
                    review_cycle_id,
                    employee_id,
                    supervisor_id,
                    employee_name_snapshot,
                    employee_code_snapshot,
                    department_snapshot,
                    job_title_snapshot,
                    status
                )

                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,

                (
                    assignment["assignment_id"],
                    cycle_id,
                    assignment["employee_id"],
                    assignment["supervisor_id"],
                    assignment["full_name"],
                    assignment["employee_code"],
                    assignment["department"],
                    assignment["job_title"],
                    "Not Started"
                )
            )


            employee_review_id = review_cursor.lastrowid


            # =====================================
            # FREEZE PERFORMANCE BLUEPRINT
            # =====================================

            performance_items = blueprint_cache[
                assignment["employee_id"]
            ]


            for item in performance_items:

                connection.execute(
                    """
                    INSERT INTO review_plan_items
                    (
                        employee_review_id,
                        source_performance_item_id,
                        item_type,
                        title,
                        description,
                        target,
                        due_date
                    )

                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,

                    (
                        employee_review_id,
                        item["id"],
                        item["item_type"],
                        item["title"],
                        item["description"],
                        item["target"],
                        item["due_date"]
                    )
                )


            # =====================================
            # EMPLOYEE ACTION
            # =====================================

            connection.execute(
                """
                INSERT INTO review_actions
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

                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,

                (
                    cycle_id,
                    employee_review_id,
                    assignment["employee_user_id"],
                    "SELF_ASSESSMENT",
                    "Complete Self Assessment",
                    (
                        f"Complete your self-assessment for "
                        f"{cycle['cycle_name']}."
                    ),
                    "Pending",
                    "High"
                )
            )


            # =====================================
            # SUPERVISOR ACTION
            # =====================================

            connection.execute(
                """
                INSERT INTO review_actions
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

                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,

                (
                    cycle_id,
                    employee_review_id,
                    assignment["supervisor_id"],
                    "SUPERVISOR_MONITORING",
                    f"Monitor {assignment['full_name']}'s Review",
                    (
                        f"Monitor review progress for "
                        f"{assignment['full_name']} during "
                        f"{cycle['cycle_name']}."
                    ),
                    "Pending",
                    "Normal"
                )
            )


            # =====================================
            # SUPERVISOR NOTIFICATION
            # =====================================

            connection.execute(
                """
                INSERT INTO notifications
                (
                    user_id,
                    review_cycle_id,
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )

                VALUES (?, ?, ?, ?, ?, ?)
                """,

                (
                    assignment["supervisor_id"],
                    cycle_id,
                    employee_review_id,
                    "REVIEW_ASSIGNED",
                    "Employee Review Assigned",
                    (
                        f"{assignment['full_name']} has entered "
                        f"{cycle['cycle_name']} under your supervision."
                    )
                )
            )


        # =====================================
        # HR CYCLE MONITORING ACTION
        # =====================================

        connection.execute(
            """
            INSERT INTO review_actions
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

            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,

            (
                cycle_id,
                None,
                session["user_id"],
                "CYCLE_MONITORING",
                f"Monitor {cycle['cycle_name']}",
                (
                    "Monitor participation, outstanding actions "
                    "and review progress across the cycle."
                ),
                "Pending",
                "Normal"
            )
        )

        # =====================================
        # HR ACTIVATION NOTIFICATION
        # =====================================

        connection.execute(
            """
            INSERT INTO notifications
            (
                user_id,
                review_cycle_id,
                employee_review_id,
                notification_type,
                title,
                message
            )

            VALUES (?, ?, ?, ?, ?, ?)
            """,

            (
                session["user_id"],
                cycle_id,
                None,
                "CYCLE_ACTIVATED",
                "Review Cycle Activated",
                (
                    f"{cycle['cycle_name']} is now active "
                    f"with {len(assignments)} employee review(s)."
                )
            )
        )

        # =====================================
        # ACTIVATE CYCLE
        # =====================================

        connection.execute(
            """
            UPDATE review_cycles

            SET
                status = 'Active',
                activated_at = CURRENT_TIMESTAMP,
                activated_by = ?,
                updated_at = CURRENT_TIMESTAMP

            WHERE id = ?
            """,

            (
                session["user_id"],
                cycle_id
            )
        )


        # =====================================
        # CYCLE AUDIT HISTORY
        # =====================================

        connection.execute(
            """
            INSERT INTO review_cycle_history
            (
                review_cycle_id,
                action,
                from_status,
                to_status,
                performed_by,
                note
            )

            VALUES (?, ?, ?, ?, ?, ?)
            """,

            (
                cycle_id,
                "Activated",
                "Scheduled",
                "Active",
                session["user_id"],
                (
                    "Review cycle activated and employee "
                    "performance baselines were snapshotted."
                )
            )
        )


        connection.commit()


        flash(
            (
                f"{cycle['cycle_name']} is now active. "
                "Employee review baselines have been locked."
            ),
            "success"
        )


    except sqlite3.IntegrityError:

        connection.rollback()


        flash(
            "The review cycle could not be activated.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for(
            "review_cycle_workspace",
            cycle_id=cycle_id
        )
    )


def pending_post_review_count(connection, cycle_id):
    """Closure retains completed PAR records; PDP activities may continue later."""
    ensure_par_meeting_schema(connection)
    ensure_pdp_schema(connection)
    return connection.execute(
        """SELECT COUNT(*) FROM employee_reviews AS review
           LEFT JOIN par_meetings AS meeting ON meeting.id = (
               SELECT id FROM par_meetings WHERE employee_review_id=review.id ORDER BY id DESC LIMIT 1)
           LEFT JOIN par_meeting_outcomes AS outcome ON outcome.par_meeting_id=meeting.id
           LEFT JOIN pdp_plans AS plan ON plan.employee_review_id=review.id
           WHERE review.review_cycle_id=? AND (
               meeting.id IS NULL OR meeting.status!='Held' OR outcome.id IS NULL
               OR (outcome.outcome='PDP Required' AND plan.id IS NULL))""", (cycle_id,),
    ).fetchone()[0]


@app.route(
    "/review-cycles/<int:cycle_id>/close",
    methods=["POST"]
)
def close_review_cycle(cycle_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))

    connection = get_db_connection()

    try:
        cycle = connection.execute(
            """
            SELECT id, cycle_name, status
            FROM review_cycles
            WHERE review_cycles.id = ?
            """,
            (cycle_id,)
        ).fetchone()

        if cycle is None:
            flash("Review cycle not found.", "error")
            return redirect(url_for("review_cycles"))

        review_progress = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'Completed' THEN 1 ELSE 0 END)
                    AS completed
            FROM employee_reviews
            WHERE review_cycle_id = ?
            """,
            (cycle_id,)
        ).fetchone()

        total = review_progress["total"] or 0
        completed = review_progress["completed"] or 0

        if cycle["status"] != "Active":
            flash("Only an active review cycle can be closed.", "error")
            return redirect(url_for(
                "review_cycle_workspace",
                cycle_id=cycle_id
            ))

        if total == 0 or completed != total:
            flash(
                "Every employee must acknowledge their final outcome "
                "before this cycle can be closed.",
                "error"
            )
            return redirect(url_for(
                "review_cycle_workspace",
                cycle_id=cycle_id
            ))

        if pending_post_review_count(connection, cycle_id):
            flash('Complete every PAR meeting and outcome, and create any required PDP before closing the cycle. Development activities can continue after closure.', 'error')
            return redirect(url_for('review_cycle_workspace', cycle_id=cycle_id))

        updated = connection.execute(
            """
            UPDATE review_cycles
            SET
                status = 'Closed',
                closed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            AND status = 'Active'
            """,
            (cycle_id,)
        )

        if not updated.rowcount:
            raise ValueError("The cycle state changed before it was closed.")

        connection.execute(
            """
            UPDATE review_actions
            SET
                status = 'Completed',
                completed_at = CURRENT_TIMESTAMP
            WHERE review_cycle_id = ?
            AND action_type = 'CYCLE_MONITORING'
            AND status = 'Pending'
            """,
            (cycle_id,)
        )

        connection.execute(
            """
            INSERT INTO review_cycle_history
            (
                review_cycle_id,
                action,
                from_status,
                to_status,
                performed_by,
                note
            )
            VALUES (?, 'Closed', 'Active', 'Closed', ?, ?)
            """,
            (
                cycle_id,
                session["user_id"],
                f"All {total} employee review(s) were completed."
            )
        )

        connection.execute(
            """
            INSERT INTO notifications
            (
                user_id,
                review_cycle_id,
                employee_review_id,
                notification_type,
                title,
                message
            )
            SELECT DISTINCT
                recipients.user_id,
                ?,
                NULL,
                'CYCLE_CLOSED',
                'Review Cycle Closed',
                ?
            FROM (
                SELECT employees.user_id
                FROM employee_reviews
                JOIN employees
                    ON employees.id = employee_reviews.employee_id
                WHERE employee_reviews.review_cycle_id = ?

                UNION

                SELECT employee_reviews.supervisor_id
                FROM employee_reviews
                WHERE employee_reviews.review_cycle_id = ?

                UNION

                SELECT manager_approvals.manager_id
                FROM manager_approvals
                JOIN employee_reviews
                    ON employee_reviews.id
                        = manager_approvals.employee_review_id
                WHERE employee_reviews.review_cycle_id = ?
            ) AS recipients
            WHERE recipients.user_id IS NOT NULL
            """,
            (
                cycle_id,
                f"{cycle['cycle_name']} has been closed and archived.",
                cycle_id,
                cycle_id,
                cycle_id
            )
        )

        connection.commit()
        flash(
            f"{cycle['cycle_name']} has been closed successfully.",
            "success"
        )

    except (sqlite3.Error, ValueError) as error:
        connection.rollback()
        print("Review cycle closure error:", error)
        flash("The review cycle could not be closed.", "error")

    finally:
        connection.close()

    return redirect(url_for(
        "review_cycle_workspace",
        cycle_id=cycle_id
    ))


@app.route(
    "/review-cycles/<int:cycle_id>/schedule",
    methods=["POST"]
)
def schedule_review_cycle(cycle_id):

    # =====================================
    # AUTHENTICATION
    # =====================================

    if "user_id" not in session:
        return redirect(url_for("login"))


    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))


    connection = get_db_connection()


    try:

        # =====================================
        # GET CYCLE
        # =====================================

        cycle = connection.execute(
            """
            SELECT
                id,
                cycle_name,
                status

            FROM review_cycles

            WHERE id = ?
            """,

            (cycle_id,)

        ).fetchone()


        if cycle is None:

            flash(
                "Review cycle not found.",
                "error"
            )

            return redirect(
                url_for("review_cycles")
            )


        # =====================================
        # ONLY DRAFT CAN BE SCHEDULED
        # =====================================

        if cycle["status"] != "Draft":

            flash(
                "Only Draft review cycles can be scheduled.",
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        # =====================================
        # ASSIGNED EMPLOYEE COUNT
        # =====================================

        assigned_count = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM review_cycle_employees

            WHERE review_cycle_id = ?

            AND participation_status = 'Assigned'
            """,

            (cycle_id,)

        ).fetchone()["total"]


        if assigned_count == 0:

            flash(
                "Assign at least one employee before scheduling the cycle.",
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        # =====================================
        # CHECK MISSING SUPERVISORS
        # =====================================

        missing_supervisors = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM review_cycle_employees

            JOIN employees
                ON review_cycle_employees.employee_id
                = employees.id

            WHERE review_cycle_employees.review_cycle_id = ?

            AND review_cycle_employees.participation_status
                = 'Assigned'

            AND employees.supervisor_id IS NULL
            """,

            (cycle_id,)

        ).fetchone()["total"]


        if missing_supervisors > 0:

            flash(
                (
                    f"{missing_supervisors} assigned employee(s) "
                    f"do not have a Supervisor. "
                    f"Resolve this before scheduling."
                ),
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        # =====================================
        # CHECK INACTIVE EMPLOYEES
        # =====================================

        inactive_employees = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM review_cycle_employees

            JOIN employees
                ON review_cycle_employees.employee_id
                = employees.id

            WHERE review_cycle_employees.review_cycle_id = ?

            AND review_cycle_employees.participation_status
                = 'Assigned'

            AND employees.status != 'Active'
            """,

            (cycle_id,)

        ).fetchone()["total"]


        if inactive_employees > 0:

            flash(
                (
                    f"{inactive_employees} assigned employee(s) "
                    f"are inactive. Review the cohort "
                    f"before scheduling."
                ),
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        # =====================================
        # MOVE DRAFT → SCHEDULED
        # =====================================

        connection.execute(
            """
            UPDATE review_cycles

            SET
                status = 'Scheduled',
                scheduled_at = CURRENT_TIMESTAMP,
                scheduled_by = ?,
                updated_at = CURRENT_TIMESTAMP

            WHERE id = ?
            """,

            (
                session["user_id"],
                cycle_id
            )
        )

        # =====================================
        # AUDIT HISTORY
        # =====================================

        connection.execute(
            """
            INSERT INTO review_cycle_history
            (
                review_cycle_id,
                action,
                from_status,
                to_status,
                performed_by,
                note
            )

            VALUES (?, ?, ?, ?, ?, ?)
            """,

            (
                cycle_id,
                "Scheduled",
                "Draft",
                "Scheduled",
                session["user_id"],
                "Review cycle configuration completed and scheduled."
            )
        )


        connection.commit()


        flash(
            f"{cycle['cycle_name']} has been scheduled successfully.",
            "success"
        )


    except sqlite3.IntegrityError:

        connection.rollback()


        flash(
            "The review cycle could not be scheduled.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for(
            "review_cycle_workspace",
            cycle_id=cycle_id
        )
    )


@app.route(
    "/review-cycles/<int:cycle_id>/assign",
    methods=["POST"]
)
def assign_cycle_employees(cycle_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))


    employee_ids = request.form.getlist(
        "employee_ids"
    )


    if not employee_ids:

        flash(
            "Please select at least one employee.",
            "error"
        )

        return redirect(
            url_for(
                "review_cycle_workspace",
                cycle_id=cycle_id
            )
        )


    connection = get_db_connection()


    try:

        # =====================================
        # GET CYCLE
        # =====================================

        cycle = connection.execute(
            """
            SELECT
                id,
                cycle_year,
                status

            FROM review_cycles

            WHERE id = ?
            """,

            (cycle_id,)

        ).fetchone()


        if cycle is None:

            flash(
                "Review cycle not found.",
                "error"
            )

            return redirect(
                url_for("review_cycles")
            )


        # =====================================
        # ONLY DRAFT CYCLES CAN BE CONFIGURED
        # =====================================

        if cycle["status"] != "Draft":

            flash(
                "Employee assignments can only be changed while the cycle is in Draft.",
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        assigned_count = 0

        skipped_count = 0


        for employee_id_raw in employee_ids:

            try:

                employee_id = int(
                    employee_id_raw
                )

            except ValueError:

                skipped_count += 1
                continue


            # =====================================
            # CHECK EMPLOYEE IS ACTIVE
            # =====================================

            employee = connection.execute(
                """
                SELECT employees.id

                FROM employees

                JOIN users ON users.id = employees.user_id

                WHERE employees.id = ?

                AND employees.status = 'Active'

                AND users.role = 'Employee'
                AND EXISTS (
                    SELECT 1 FROM performance_items
                    WHERE performance_items.employee_id = employees.id
                    AND performance_items.status = 'Active'
                )
                """,

                (employee_id,)

            ).fetchone()


            if employee is None:

                skipped_count += 1
                continue


            # =====================================
            # CHECK ANNUAL ASSIGNMENT
            # =====================================

            existing_year_assignment = connection.execute(
                """
                SELECT
                    review_cycle_employees.id

                FROM review_cycle_employees

                JOIN review_cycles
                    ON review_cycle_employees.review_cycle_id
                    = review_cycles.id

                WHERE review_cycle_employees.employee_id = ?

                AND review_cycles.cycle_year = ?

                AND review_cycle_employees.participation_status
                    = 'Assigned'
                """,

                (
                    employee_id,
                    cycle["cycle_year"]
                )

            ).fetchone()


            if existing_year_assignment:

                skipped_count += 1
                continue


            # =====================================
            # CHECK OLD REMOVED ASSIGNMENT
            # =====================================

            previous_assignment = connection.execute(
                """
                SELECT id

                FROM review_cycle_employees

                WHERE review_cycle_id = ?

                AND employee_id = ?
                """,

                (
                    cycle_id,
                    employee_id
                )

            ).fetchone()


            if previous_assignment:

                connection.execute(
                    """
                    UPDATE review_cycle_employees

                    SET
                        participation_status = 'Assigned',
                        assigned_by = ?,
                        assigned_at = CURRENT_TIMESTAMP,
                        removed_at = NULL,
                        removed_by = NULL

                    WHERE id = ?
                    """,

                    (
                        session["user_id"],
                        previous_assignment["id"]
                    )
                )


            else:

                connection.execute(
                    """
                    INSERT INTO review_cycle_employees
                    (
                        review_cycle_id,
                        employee_id,
                        assigned_by,
                        participation_status
                    )

                    VALUES (?, ?, ?, ?)
                    """,

                    (
                        cycle_id,
                        employee_id,
                        session["user_id"],
                        "Assigned"
                    )
                )


            assigned_count += 1


        connection.commit()


        if assigned_count > 0:

            flash(
                f"{assigned_count} employee(s) assigned to the review cycle.",
                "success"
            )


        if skipped_count > 0:

            flash(
                f"{skipped_count} employee(s) could not be assigned because they have no active blueprint, are unavailable, or are already assigned for this review year.",
                "error"
            )


    except sqlite3.IntegrityError:

        connection.rollback()

        flash(
            "Employee assignments could not be completed.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for(
            "review_cycle_workspace",
            cycle_id=cycle_id
        )
    )



@app.route(
    "/review-cycles/<int:cycle_id>/assignments/<int:assignment_id>/remove",
    methods=["POST"]
)
def remove_cycle_employee(
    cycle_id,
    assignment_id
):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))


    connection = get_db_connection()


    try:

        cycle = connection.execute(
            """
            SELECT status

            FROM review_cycles

            WHERE id = ?
            """,

            (cycle_id,)

        ).fetchone()


        if cycle is None:

            flash(
                "Review cycle not found.",
                "error"
            )

            return redirect(
                url_for("review_cycles")
            )


        if cycle["status"] != "Draft":

            flash(
                "Employees can only be removed while the cycle is in Draft.",
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        assignment = connection.execute(
            """
            SELECT id

            FROM review_cycle_employees

            WHERE id = ?

            AND review_cycle_id = ?

            AND participation_status = 'Assigned'
            """,

            (
                assignment_id,
                cycle_id
            )

        ).fetchone()


        if assignment is None:

            flash(
                "Employee assignment not found.",
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        connection.execute(
            """
            UPDATE review_cycle_employees

            SET
                participation_status = 'Removed',
                removed_at = CURRENT_TIMESTAMP,
                removed_by = ?

            WHERE id = ?
            """,

            (
                session["user_id"],
                assignment_id
            )
        )


        connection.commit()


        flash(
            "Employee removed from the draft cycle.",
            "success"
        )


    except sqlite3.IntegrityError:

        connection.rollback()

        flash(
            "The employee could not be removed from the cycle.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for(
            "review_cycle_workspace",
            cycle_id=cycle_id
        )
    )

@app.route(
    "/review-cycles/<int:cycle_id>/return-to-draft",
    methods=["POST"]
)
def return_cycle_to_draft(cycle_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))


    connection = get_db_connection()


    try:

        cycle = connection.execute(
            """
            SELECT
                id,
                cycle_name,
                status

            FROM review_cycles

            WHERE id = ?
            """,

            (cycle_id,)

        ).fetchone()


        if cycle is None:

            flash(
                "Review cycle not found.",
                "error"
            )

            return redirect(
                url_for("review_cycles")
            )


        if cycle["status"] != "Scheduled":

            flash(
                "Only Scheduled cycles can be returned to Draft.",
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        # =====================================
        # RETURN TO DRAFT
        # =====================================

        connection.execute(
            """
            UPDATE review_cycles

            SET
                status = 'Draft',
                scheduled_at = NULL,
                scheduled_by = NULL,
                updated_at = CURRENT_TIMESTAMP

            WHERE id = ?
            """,

            (cycle_id,)
        )


        # =====================================
        # AUDIT HISTORY
        # =====================================

        connection.execute(
            """
            INSERT INTO review_cycle_history
            (
                review_cycle_id,
                action,
                from_status,
                to_status,
                performed_by,
                note
            )

            VALUES (?, ?, ?, ?, ?, ?)
            """,

            (
                cycle_id,
                "Returned to Draft",
                "Scheduled",
                "Draft",
                session["user_id"],
                "HR reopened the cycle for configuration changes."
            )
        )


        connection.commit()


        flash(
            f"{cycle['cycle_name']} has been returned to Draft.",
            "success"
        )


    except sqlite3.IntegrityError:

        connection.rollback()

        flash(
            "The review cycle could not be returned to Draft.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for(
            "review_cycle_workspace",
            cycle_id=cycle_id
        )
    )


@app.route(
    "/review-cycles/<int:cycle_id>/edit",
    methods=["POST"]
)
def edit_review_cycle(cycle_id):

    # =====================================
    # AUTHENTICATION
    # =====================================

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))


    # =====================================
    # GET FORM DATA
    # =====================================

    cycle_name = request.form.get(
        "cycle_name",
        ""
    ).strip()

    start_date = request.form.get(
        "start_date",
        ""
    ).strip()

    end_date = request.form.get(
        "end_date",
        ""
    ).strip()


    if not cycle_name:

        flash(
            "Please enter a cycle name.",
            "error"
        )

        return redirect(
            url_for(
                "review_cycle_workspace",
                cycle_id=cycle_id
            )
        )


    if not start_date or not end_date:

        flash(
            "Please provide both the start and end dates.",
            "error"
        )

        return redirect(
            url_for(
                "review_cycle_workspace",
                cycle_id=cycle_id
            )
        )


    connection = get_db_connection()


    try:

        # =====================================
        # GET EXISTING CYCLE
        # =====================================

        cycle = connection.execute(
            """
            SELECT
                id,
                cycle_name,
                cycle_year,
                cycle_number,
                start_date,
                end_date,
                status

            FROM review_cycles

            WHERE id = ?
            """,

            (cycle_id,)

        ).fetchone()


        if cycle is None:

            flash(
                "Review cycle not found.",
                "error"
            )

            return redirect(
                url_for("review_cycles")
            )


        # =====================================
        # ONLY DRAFT CYCLES CAN BE EDITED
        # =====================================

        if cycle["status"] != "Draft":

            flash(
                "Only Draft review cycles can be edited.",
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        # =====================================
        # PARSE DATES
        # =====================================

        try:

            parsed_start = datetime.strptime(
                start_date,
                "%Y-%m-%d"
            ).date()

            parsed_end = datetime.strptime(
                end_date,
                "%Y-%m-%d"
            ).date()

        except ValueError:

            flash(
                "Please enter valid review cycle dates.",
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        # =====================================
        # DATE RULES
        # =====================================

        if parsed_start >= parsed_end:

            flash(
                "The end date must be after the start date.",
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        if (
            parsed_start.year != cycle["cycle_year"]
            or
            parsed_end.year != cycle["cycle_year"]
        ):

            flash(
                (
                    f"Cycle dates must remain within "
                    f"the {cycle['cycle_year']} review year."
                ),
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        # =====================================
        # CHECK OVERLAP WITH OTHER CYCLES
        # =====================================

        overlapping_cycle = connection.execute(
            """
            SELECT
                id,
                cycle_name,
                start_date,
                end_date

            FROM review_cycles

            WHERE cycle_year = ?

            AND id != ?

            AND NOT (
                end_date < ?
                OR
                start_date > ?
            )
            """,

            (
                cycle["cycle_year"],
                cycle_id,
                start_date,
                end_date
            )

        ).fetchone()


        if overlapping_cycle:

            flash(
                (
                    f"The selected dates overlap with "
                    f"{overlapping_cycle['cycle_name']} "
                    f"({overlapping_cycle['start_date']} "
                    f"to {overlapping_cycle['end_date']})."
                ),
                "error"
            )

            return redirect(
                url_for(
                    "review_cycle_workspace",
                    cycle_id=cycle_id
                )
            )


        # =====================================
        # KEEP OLD VALUES FOR AUDIT
        # =====================================

        old_name = cycle["cycle_name"]
        old_start = cycle["start_date"]
        old_end = cycle["end_date"]


        # =====================================
        # UPDATE CYCLE
        # =====================================

        connection.execute(
            """
            UPDATE review_cycles

            SET
                cycle_name = ?,
                start_date = ?,
                end_date = ?,
                updated_at = CURRENT_TIMESTAMP

            WHERE id = ?
            """,

            (
                cycle_name,
                start_date,
                end_date,
                cycle_id
            )
        )


        # =====================================
        # AUDIT HISTORY
        # =====================================

        change_note = (
            f"Cycle configuration updated. "
            f"Name: '{old_name}' → '{cycle_name}'. "
            f"Dates: {old_start} to {old_end} → "
            f"{start_date} to {end_date}."
        )


        connection.execute(
            """
            INSERT INTO review_cycle_history
            (
                review_cycle_id,
                action,
                from_status,
                to_status,
                performed_by,
                note
            )

            VALUES (?, ?, ?, ?, ?, ?)
            """,

            (
                cycle_id,
                "Configuration Updated",
                "Draft",
                "Draft",
                session["user_id"],
                change_note
            )
        )


        connection.commit()


        flash(
            "Review cycle configuration updated successfully.",
            "success"
        )


    except sqlite3.IntegrityError:

        connection.rollback()

        flash(
            "The review cycle could not be updated.",
            "error"
        )


    finally:

        connection.close()


    return redirect(
        url_for(
            "review_cycle_workspace",
            cycle_id=cycle_id
        )
    )


# =========================================================
# PB07 - PEER MATCHBOARD
# =========================================================

@app.route(
    "/review-cycles/<int:cycle_id>/peer-matchboard"
)
def peer_matchboard(cycle_id):

    # =====================================
    # AUTHENTICATION
    # =====================================

    if "user_id" not in session:
        return redirect(
            url_for("login")
        )


    # =====================================
    # HR ONLY
    # =====================================

    if session["user_role"] != "HR":

        flash(
            "Only HR can manage peer reviewer assignments.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )


    connection = get_db_connection()


    try:

        # =====================================
        # REVIEW CYCLE
        # =====================================

        cycle = connection.execute(
            """
            SELECT *

            FROM review_cycles

            WHERE id = ?
            """,

            (
                cycle_id,
            )

        ).fetchone()


        if cycle is None:

            flash(
                "Review cycle not found.",
                "error"
            )

            return redirect(
                url_for("review_cycles")
            )


        # =====================================
        # REVIEW SUBJECTS
        # =====================================

        subjects = connection.execute(
            """
            SELECT

                employee_reviews.id
                    AS employee_review_id,

                employee_reviews.employee_id,

                employee_reviews.employee_name_snapshot,

                employee_reviews.employee_code_snapshot,

                employee_reviews.department_snapshot,

                employee_reviews.job_title_snapshot,

                employee_reviews.status
                    AS review_status,

                employees.user_id
                    AS employee_user_id,

                users.email
                    AS employee_email,

                self_assessments.status
                    AS self_assessment_status,

                (
                    SELECT COUNT(*)

                    FROM peer_review_assignments

                    WHERE
                        peer_review_assignments.employee_review_id
                        = employee_reviews.id

                    AND peer_review_assignments.status
                        != 'Removed'

                ) AS peer_count

            FROM employee_reviews

            JOIN employees
                ON employee_reviews.employee_id
                = employees.id

            JOIN users
                ON employees.user_id
                = users.id

            LEFT JOIN self_assessments
                ON self_assessments.employee_review_id
                = employee_reviews.id

            WHERE employee_reviews.review_cycle_id = ?

            ORDER BY
                employee_reviews.employee_name_snapshot
            """,

            (
                cycle_id,
            )

        ).fetchall()


        # =====================================
        # ACTIVE PEER ASSIGNMENTS
        # =====================================

        assignments = connection.execute(
            """
            SELECT

                peer_review_assignments.id,

                peer_review_assignments.employee_review_id,

                peer_review_assignments.reviewer_user_id,

                peer_review_assignments.status,

                peer_review_assignments.assigned_at,

                users.full_name
                    AS reviewer_name,

                users.email
                    AS reviewer_email,

                employees.employee_code
                    AS reviewer_employee_code,

                employees.department
                    AS reviewer_department,

                employees.job_title
                    AS reviewer_job_title

            FROM peer_review_assignments

            JOIN users
                ON peer_review_assignments.reviewer_user_id
                = users.id

            LEFT JOIN employees
                ON employees.user_id
                = users.id

            JOIN employee_reviews
                ON peer_review_assignments.employee_review_id
                = employee_reviews.id

            WHERE employee_reviews.review_cycle_id = ?

            AND peer_review_assignments.status
                != 'Removed'

            ORDER BY
                peer_review_assignments.assigned_at
            """,

            (
                cycle_id,
            )

        ).fetchall()


        # =====================================
        # BUILD ASSIGNMENT MAP
        # =====================================

        assignment_map = {}


        for assignment in assignments:

            review_id = assignment[
                "employee_review_id"
            ]


            if review_id not in assignment_map:

                assignment_map[
                    review_id
                ] = []


            assignment_map[
                review_id
            ].append(
                assignment
            )


        # =====================================
        # REVIEWER CANDIDATE POOL
        # =====================================

        candidates = connection.execute(
            """
            SELECT

                users.id
                    AS user_id,

                users.full_name,

                users.email,

                users.role,

                employees.id
                    AS employee_id,

                employees.employee_code,

                employees.department,

                employees.job_title

            FROM employees

            JOIN users
                ON employees.user_id
                = users.id

            WHERE employees.status = 'Active'

            ORDER BY
                users.full_name
            """
        ).fetchall()


        return render_template(
            "peer_matchboard.html",

            cycle=cycle,

            subjects=subjects,

            candidates=candidates,

            assignment_map=assignment_map,

            user_name=session["user_name"],

            user_role=session["user_role"]
        )


    finally:

        connection.close()



@app.route(
    "/review-cycles/<int:cycle_id>/reviews/<int:employee_review_id>/peers/assign",
    methods=["POST"]
)
def assign_peer_reviewer(
    cycle_id,
    employee_review_id
):

    # =====================================
    # AUTHENTICATION
    # =====================================

    if "user_id" not in session:

        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401


    if session["user_role"] != "HR":

        return jsonify({
            "success": False,
            "message":
                "Only HR can assign peer reviewers."
        }), 403


    data = request.get_json(
        silent=True
    )


    if not isinstance(data, dict):

        return jsonify({
            "success": False,
            "message":
                "Invalid reviewer information."
        }), 400


    try:

        reviewer_user_id = int(
            data.get(
                "reviewer_user_id"
            )
        )

    except (
        TypeError,
        ValueError
    ):

        return jsonify({
            "success": False,
            "message":
                "Please select a valid reviewer."
        }), 400


    connection = get_db_connection()


    try:

        # =====================================
        # VERIFY REVIEW + CYCLE
        # =====================================

        review = connection.execute(
            """
            SELECT

                employee_reviews.id,

                employee_reviews.employee_id,

                employee_reviews.employee_name_snapshot,

                employee_reviews.status
                    AS review_status,

                employees.user_id
                    AS employee_user_id,

                review_cycles.cycle_name,

                review_cycles.status
                    AS cycle_status

            FROM employee_reviews

            JOIN employees
                ON employee_reviews.employee_id
                = employees.id

            JOIN review_cycles
                ON employee_reviews.review_cycle_id
                = review_cycles.id

            WHERE employee_reviews.id = ?

            AND review_cycles.id = ?
            """,

            (
                employee_review_id,
                cycle_id
            )

        ).fetchone()


        if review is None:

            return jsonify({
                "success": False,
                "message":
                    "Employee review not found."
            }), 404


        # =====================================
        # CYCLE MUST BE ACTIVE
        # =====================================

        if review["cycle_status"] != "Active":

            return jsonify({
                "success": False,
                "message":
                    "Peer reviewers can only be assigned during an active review cycle."
            }), 409


        # =====================================
        # SELF-ASSESSMENT MUST BE SUBMITTED
        # =====================================

        assessment = connection.execute(
            """
            SELECT status

            FROM self_assessments

            WHERE employee_review_id = ?
            """,

            (
                employee_review_id,
            )

        ).fetchone()


        if (
            assessment is None
            or
            assessment["status"] != "Submitted"
        ):

            return jsonify({
                "success": False,
                "message":
                    "The employee must submit their self-assessment before peer reviewers can be assigned."
            }), 409


        # =====================================
        # VERIFY REVIEWER
        # =====================================

        reviewer = connection.execute(
            """
            SELECT

                users.id,

                users.full_name,

                users.email,

                employees.status,

                employees.department,

                employees.job_title

            FROM users

            JOIN employees
                ON employees.user_id
                = users.id

            WHERE users.id = ?
            """,

            (
                reviewer_user_id,
            )

        ).fetchone()


        if reviewer is None:

            return jsonify({
                "success": False,
                "message":
                    "Selected reviewer does not have an employee profile."
            }), 404


        if reviewer["status"] != "Active":

            return jsonify({
                "success": False,
                "message":
                    "Only active employees can act as peer reviewers."
            }), 409


        # =====================================
        # CANNOT REVIEW YOURSELF
        # =====================================

        if (
            reviewer_user_id
            ==
            review["employee_user_id"]
        ):

            return jsonify({
                "success": False,
                "message":
                    "An employee cannot be assigned to review themselves."
            }), 409


        # =====================================
        # MAXIMUM 2 ACTIVE REVIEWERS
        # =====================================

        active_count = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM peer_review_assignments

            WHERE employee_review_id = ?

            AND status != 'Removed'
            """,

            (
                employee_review_id,
            )

        ).fetchone()["total"]


        if active_count >= 2:

            return jsonify({
                "success": False,
                "message":
                    "This employee already has the maximum of 2 peer reviewers."
            }), 409


        # =====================================
        # EXISTING ASSIGNMENT?
        # =====================================

        existing_assignment = (
            connection.execute(
                """
                SELECT *

                FROM peer_review_assignments

                WHERE employee_review_id = ?

                AND reviewer_user_id = ?
                """,

                (
                    employee_review_id,
                    reviewer_user_id
                )

            ).fetchone()
        )


        # =====================================
        # CREATE OR REVIVE ASSIGNMENT
        # =====================================

        if existing_assignment:


            if (
                existing_assignment["status"]
                != "Removed"
            ):

                return jsonify({
                    "success": False,
                    "message":
                        "This colleague is already assigned as a peer reviewer."
                }), 409


            connection.execute(
                """
                UPDATE peer_review_assignments

                SET
                    status = 'Assigned',
                    assigned_by = ?,
                    assigned_at = CURRENT_TIMESTAMP,
                    removed_at = NULL,
                    removed_by = NULL

                WHERE id = ?
                """,

                (
                    session["user_id"],

                    existing_assignment[
                        "id"
                    ]
                )
            )


            assignment_id = (
                existing_assignment[
                    "id"
                ]
            )


        else:


            cursor = connection.execute(
                """
                INSERT INTO peer_review_assignments
                (
                    employee_review_id,
                    reviewer_user_id,
                    assigned_by,
                    status
                )

                VALUES (?, ?, ?, 'Assigned')
                """,

                (
                    employee_review_id,
                    reviewer_user_id,
                    session["user_id"]
                )
            )


            assignment_id = (
                cursor.lastrowid
            )


        # =====================================
        # CREATE REVIEWER ACTION
        # =====================================

        existing_action = connection.execute(
            """
            SELECT id

            FROM review_actions

            WHERE employee_review_id = ?

            AND assigned_to = ?

            AND action_type = 'PEER_REVIEW'
            """,

            (
                employee_review_id,
                reviewer_user_id
            )

        ).fetchone()


        if existing_action:

            connection.execute(
                """
                UPDATE review_actions

                SET
                    status = 'Pending',
                    completed_at = NULL,
                    priority = 'Normal',
                    title = ?,
                    description = ?

                WHERE id = ?
                """,

                (
                    (
                        "Provide Peer Feedback for "
                        f"{review['employee_name_snapshot']}"
                    ),

                    (
                        "Complete the confidential peer review "
                        f"for {review['employee_name_snapshot']} "
                        f"during {review['cycle_name']}."
                    ),

                    existing_action[
                        "id"
                    ]
                )
            )


        else:

            connection.execute(
                """
                INSERT INTO review_actions
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

                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,

                (
                    cycle_id,

                    employee_review_id,

                    reviewer_user_id,

                    "PEER_REVIEW",

                    (
                        "Provide Peer Feedback for "
                        f"{review['employee_name_snapshot']}"
                    ),

                    (
                        "Complete the confidential peer review "
                        f"for {review['employee_name_snapshot']} "
                        f"during {review['cycle_name']}."
                    ),

                    "Pending",

                    "Normal"
                )
            )


        # =====================================
        # REVIEWER SIGNAL
        # =====================================

        connection.execute(
            """
            INSERT INTO notifications
            (
                user_id,
                review_cycle_id,
                employee_review_id,
                notification_type,
                title,
                message
            )

            VALUES (?, ?, ?, ?, ?, ?)
            """,

            (
                reviewer_user_id,

                cycle_id,

                employee_review_id,

                "PEER_REVIEW_ASSIGNED",

                "Peer Review Assigned",

                (
                    f"You have been selected to provide "
                    f"confidential peer feedback for "
                    f"{review['employee_name_snapshot']} "
                    f"during {review['cycle_name']}."
                )
            )
        )


        # =====================================
        # FIRST ASSIGNMENT STARTS PEER PHASE
        # =====================================

        if active_count == 0:

            connection.execute(
                """
                UPDATE employee_reviews

                SET
                    status = 'Peer Review In Progress',
                    updated_at = CURRENT_TIMESTAMP

                WHERE id = ?
                """,

                (
                    employee_review_id,
                )
            )


            # Employee gets a generic signal.
            # Reviewer identities are NOT exposed.

            connection.execute(
                """
                INSERT INTO notifications
                (
                    user_id,
                    review_cycle_id,
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )

                VALUES (?, ?, ?, ?, ?, ?)
                """,

                (
                    review[
                        "employee_user_id"
                    ],

                    cycle_id,

                    employee_review_id,

                    "PEER_REVIEW_PHASE_STARTED",

                    "Peer Review Stage Started",

                    (
                        f"Your {review['cycle_name']} review "
                        f"has moved into the peer feedback stage."
                    )
                )
            )


        connection.commit()


        return jsonify({
            "success": True,

            "message":
                f"{reviewer['full_name']} assigned successfully.",

            "assignment_id":
                assignment_id
        })


    except sqlite3.Error as error:

        connection.rollback()


        print(
            "Peer assignment error:",
            error
        )


        return jsonify({
            "success": False,

            "message":
                "The peer reviewer could not be assigned."
        }), 500


    finally:

        connection.close()


@app.route(
    "/review-cycles/<int:cycle_id>/reviews/<int:employee_review_id>/peers/<int:assignment_id>/remove",
    methods=["POST"]
)
def remove_peer_reviewer(
    cycle_id,
    employee_review_id,
    assignment_id
):

    if "user_id" not in session:

        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401


    if session["user_role"] != "HR":

        return jsonify({
            "success": False,
            "message":
                "Only HR can remove peer reviewer assignments."
        }), 403


    connection = get_db_connection()


    try:

        assignment = connection.execute(
            """
            SELECT

                peer_review_assignments.id,

                peer_review_assignments.status,

                peer_review_assignments.reviewer_user_id,

                users.full_name
                    AS reviewer_name,

                employee_reviews.employee_name_snapshot,

                employee_reviews.status
                    AS review_status,

                review_cycles.status
                    AS cycle_status

            FROM peer_review_assignments

            JOIN users
                ON peer_review_assignments.reviewer_user_id
                = users.id

            JOIN employee_reviews
                ON peer_review_assignments.employee_review_id
                = employee_reviews.id

            JOIN review_cycles
                ON employee_reviews.review_cycle_id
                = review_cycles.id

            WHERE peer_review_assignments.id = ?

            AND employee_reviews.id = ?

            AND review_cycles.id = ?
            """,

            (
                assignment_id,
                employee_review_id,
                cycle_id
            )

        ).fetchone()


        if assignment is None:

            return jsonify({
                "success": False,
                "message":
                    "Peer assignment not found."
            }), 404


        if (
            assignment["cycle_status"]
            != "Active"
        ):

            return jsonify({
                "success": False,
                "message":
                    "Peer assignments cannot be changed because the cycle is not active."
            }), 409


        # Once reviewer starts/submits,
        # HR should not casually remove them.

        if (
            assignment["status"]
            != "Assigned"
        ):

            return jsonify({
                "success": False,
                "message":
                    "This reviewer has already started the peer review and cannot be removed."
            }), 409


        # =====================================
        # REMOVE ASSIGNMENT
        # =====================================

        connection.execute(
            """
            UPDATE peer_review_assignments

            SET
                status = 'Removed',
                removed_at = CURRENT_TIMESTAMP,
                removed_by = ?

            WHERE id = ?
            """,

            (
                session["user_id"],
                assignment_id
            )
        )


        # =====================================
        # REMOVE OPEN ACTION
        # =====================================

        connection.execute(
            """
            DELETE FROM review_actions

            WHERE employee_review_id = ?

            AND assigned_to = ?

            AND action_type = 'PEER_REVIEW'

            AND status = 'Pending'
            """,

            (
                employee_review_id,

                assignment[
                    "reviewer_user_id"
                ]
            )
        )


        # =====================================
        # INFORM REVIEWER
        # =====================================

        connection.execute(
            """
            INSERT INTO notifications
            (
                user_id,
                review_cycle_id,
                employee_review_id,
                notification_type,
                title,
                message
            )

            VALUES (?, ?, ?, ?, ?, ?)
            """,

            (
                assignment[
                    "reviewer_user_id"
                ],

                cycle_id,

                employee_review_id,

                "PEER_REVIEW_REMOVED",

                "Peer Review Assignment Updated",

                (
                    "You are no longer required to provide "
                    f"peer feedback for "
                    f"{assignment['employee_name_snapshot']}."
                )
            )
        )


        # =====================================
        # CHECK REMAINING COVERAGE
        # =====================================

        remaining_count = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM peer_review_assignments

            WHERE employee_review_id = ?

            AND status != 'Removed'
            """,

            (
                employee_review_id,
            )

        ).fetchone()["total"]


        if remaining_count == 0:

            connection.execute(
                """
                UPDATE employee_reviews

                SET
                    status = 'Self Assessment Submitted',
                    updated_at = CURRENT_TIMESTAMP

                WHERE id = ?

                AND status = 'Peer Review In Progress'
                """,

                (
                    employee_review_id,
                )
            )


        connection.commit()


        return jsonify({
            "success": True,

            "message":
                f"{assignment['reviewer_name']} removed from the peer review.",

            "remaining_count":
                remaining_count
        })


    except sqlite3.Error as error:

        connection.rollback()


        print(
            "Peer removal error:",
            error
        )


        return jsonify({
            "success": False,
            "message":
                "The peer reviewer could not be removed."
        }), 500


    finally:

        connection.close()


# =========================================================
# PB08 - PEER REVIEW STUDIO
# =========================================================

@app.route(
    "/reviews/<int:employee_review_id>/peer-review"
)
def peer_review_studio(employee_review_id):

    # =====================================
    # AUTHENTICATION
    # =====================================

    if "user_id" not in session:

        return redirect(
            url_for("login")
        )


    connection = get_db_connection()


    try:

        # =====================================
        # VERIFY THIS USER IS THE REVIEWER
        # =====================================

        assignment = connection.execute(
            """
            SELECT

                peer_review_assignments.id
                    AS peer_assignment_id,

                peer_review_assignments.status
                    AS assignment_status,

                peer_review_assignments.reviewer_user_id,

                peer_review_assignments.assigned_at,

                employee_reviews.id
                    AS employee_review_id,

                employee_reviews.employee_id,

                employee_reviews.employee_name_snapshot,

                employee_reviews.employee_code_snapshot,

                employee_reviews.department_snapshot,

                employee_reviews.job_title_snapshot,

                employee_reviews.status
                    AS employee_review_status,

                review_cycles.id
                    AS cycle_id,

                review_cycles.cycle_name,

                review_cycles.start_date,

                review_cycles.end_date,

                review_cycles.status
                    AS cycle_status

            FROM peer_review_assignments

            JOIN employee_reviews
                ON peer_review_assignments.employee_review_id
                = employee_reviews.id

            JOIN review_cycles
                ON employee_reviews.review_cycle_id
                = review_cycles.id

            WHERE peer_review_assignments.employee_review_id = ?

            AND peer_review_assignments.reviewer_user_id = ?

            AND peer_review_assignments.status != 'Removed'
            """,

            (
                employee_review_id,
                session["user_id"]
            )

        ).fetchone()


        # =====================================
        # NOT ASSIGNED
        # =====================================

        if assignment is None:

            flash(
                "You are not assigned to this peer review.",
                "error"
            )

            return redirect(
                url_for("dashboard")
            )


        # =====================================
        # CYCLE MUST STILL BE ACTIVE
        # =====================================

        if assignment["cycle_status"] != "Active":

            flash(
                "This review cycle is not currently active.",
                "error"
            )

            return redirect(
                url_for("dashboard")
            )


        # =====================================
        # FIND EXISTING PEER REVIEW
        # =====================================

        peer_review = connection.execute(
            """
            SELECT *

            FROM peer_reviews

            WHERE peer_assignment_id = ?
            """,

            (
                assignment[
                    "peer_assignment_id"
                ],
            )

        ).fetchone()


        # =====================================
        # FIRST OPEN
        # CREATE DRAFT
        # =====================================

        if peer_review is None:

            cursor = connection.execute(
                """
                INSERT INTO peer_reviews
                (
                    peer_assignment_id,
                    status
                )

                VALUES (?, 'Draft')
                """,

                (
                    assignment[
                        "peer_assignment_id"
                    ],
                )
            )


            peer_review_id = (
                cursor.lastrowid
            )


            # ---------------------------------
            # ASSIGNMENT IS NOW IN PROGRESS
            # ---------------------------------

            connection.execute(
                """
                UPDATE peer_review_assignments

                SET
                    status = 'In Progress'

                WHERE id = ?

                AND status = 'Assigned'
                """,

                (
                    assignment[
                        "peer_assignment_id"
                    ],
                )
            )


            connection.commit()


            peer_review = connection.execute(
                """
                SELECT *

                FROM peer_reviews

                WHERE id = ?
                """,

                (
                    peer_review_id,
                )

            ).fetchone()


        # =====================================
        # FROZEN PERFORMANCE BASELINE
        # =====================================

        baseline_items = connection.execute(
            """
            SELECT

                review_plan_items.id
                    AS review_plan_item_id,

                review_plan_items.item_type,

                review_plan_items.title,

                review_plan_items.description,

                review_plan_items.target,

                review_plan_items.due_date,

                peer_review_items.id
                    AS peer_item_id,

                peer_review_items.rating,

                peer_review_items.feedback_text

            FROM review_plan_items

            LEFT JOIN peer_review_items
                ON peer_review_items.review_plan_item_id
                    = review_plan_items.id

                AND peer_review_items.peer_review_id
                    = ?

            WHERE review_plan_items.employee_review_id = ?

            ORDER BY

                CASE review_plan_items.item_type

                    WHEN 'Responsibility' THEN 1
                    WHEN 'Expectation' THEN 2
                    WHEN 'KPI' THEN 3
                    WHEN 'Goal' THEN 4
                    ELSE 5

                END,

                review_plan_items.id
            """,

            (
                peer_review["id"],
                employee_review_id
            )

        ).fetchall()


        # =====================================
        # REVIEWER PROGRESS
        # =====================================

        reviewer_progress = connection.execute(
            """
            SELECT

                COUNT(*) AS total_reviewers,

                SUM(
                    CASE
                        WHEN status = 'Submitted'
                        THEN 1
                        ELSE 0
                    END
                ) AS submitted_reviewers

            FROM peer_review_assignments

            WHERE employee_review_id = ?

            AND status != 'Removed'
            """,

            (
                employee_review_id,
            )

        ).fetchone()


        private_change_request = get_private_manager_change_request(
            connection,
            employee_review_id,
            session["user_id"]
        )


        return render_template(
            "peer_review.html",

            assignment=assignment,

            peer_review=peer_review,

            baseline_items=baseline_items,

            reviewer_progress=reviewer_progress,

            private_change_request=private_change_request,

            user_name=session["user_name"],

            user_role=session["user_role"]
        )


    finally:

        connection.close()


# =========================================================
# PB08 - SAVE PEER REVIEW DRAFT
# =========================================================

@app.route(
    "/reviews/<int:employee_review_id>/peer-review/save",
    methods=["POST"]
)
def save_peer_review_draft(employee_review_id):

    # =====================================
    # AUTHENTICATION
    # =====================================

    if "user_id" not in session:

        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401


    # =====================================
    # READ JSON
    # =====================================

    data = request.get_json(
        silent=True
    )


    if not isinstance(data, dict):

        return jsonify({
            "success": False,
            "message": "Invalid peer review data."
        }), 400


    responses = data.get(
        "responses",
        []
    )


    if not isinstance(
        responses,
        list
    ):

        return jsonify({
            "success": False,
            "message": "Invalid peer review responses."
        }), 400


    overall_fields = {
        "strengths": data.get(
            "strengths",
            ""
        ),
        "development_feedback": data.get(
            "development_feedback",
            ""
        ),
        "collaboration_feedback": data.get(
            "collaboration_feedback",
            ""
        ),
        "overall_comment": data.get(
            "overall_comment",
            ""
        )
    }


    for field_value in overall_fields.values():

        if not isinstance(
            field_value,
            str
        ):

            return jsonify({
                "success": False,
                "message": "Invalid overall feedback."
            }), 400


    connection = get_db_connection()


    try:

        # =====================================
        # VERIFY REVIEWER OWNERSHIP
        # =====================================

        review = connection.execute(
            """
            SELECT

                peer_reviews.id
                    AS peer_review_id,

                peer_reviews.status
                    AS peer_review_status,

                peer_review_assignments.id
                    AS peer_assignment_id,

                peer_review_assignments.status
                    AS assignment_status,

                review_cycles.status
                    AS cycle_status

            FROM peer_review_assignments

            JOIN peer_reviews
                ON peer_reviews.peer_assignment_id
                    = peer_review_assignments.id

            JOIN employee_reviews
                ON peer_review_assignments.employee_review_id
                    = employee_reviews.id

            JOIN review_cycles
                ON employee_reviews.review_cycle_id
                    = review_cycles.id

            WHERE peer_review_assignments.employee_review_id = ?

            AND peer_review_assignments.reviewer_user_id = ?

            AND peer_review_assignments.status != 'Removed'
            """,

            (
                employee_review_id,
                session["user_id"]
            )

        ).fetchone()


        if review is None:

            return jsonify({
                "success": False,
                "message": "Peer review not found."
            }), 404


        private_change_request = get_private_manager_change_request(
            connection,
            employee_review_id,
            session["user_id"]
        )


        if (
            review["peer_review_status"]
            != "Draft"
            or
            review["assignment_status"]
            == "Submitted"
        ):

            return jsonify({
                "success": False,
                "message":
                    "This peer review has already been submitted."
            }), 409


        if review["cycle_status"] != "Active":

            return jsonify({
                "success": False,
                "message":
                    "This review cycle is no longer active."
            }), 409


        private_change_request = get_private_manager_change_request(
            connection,
            employee_review_id,
            session["user_id"]
        )


        peer_review_id = review[
            "peer_review_id"
        ]


        # =====================================
        # VALIDATE ITEM RESPONSES
        # =====================================

        validated_responses = []

        seen_item_ids = set()


        for response in responses:

            if not isinstance(
                response,
                dict
            ):

                raise ValueError(
                    "Invalid peer review response."
                )


            try:

                review_plan_item_id = int(
                    response.get(
                        "review_plan_item_id"
                    )
                )

            except (
                TypeError,
                ValueError
            ):

                raise ValueError(
                    "Invalid review baseline item."
                )


            if review_plan_item_id in seen_item_ids:

                raise ValueError(
                    "A review item was included more than once."
                )


            seen_item_ids.add(
                review_plan_item_id
            )


            rating = response.get(
                "rating"
            )


            if (
                rating is not None
                and
                rating != ""
            ):

                try:

                    rating = int(
                        rating
                    )

                except (
                    TypeError,
                    ValueError
                ):

                    raise ValueError(
                        "Invalid rating."
                    )


                if rating < 1 or rating > 5:

                    raise ValueError(
                        "Ratings must be between 1 and 5."
                    )

            else:

                rating = None


            feedback_text = response.get(
                "feedback_text",
                ""
            )


            if not isinstance(
                feedback_text,
                str
            ):

                raise ValueError(
                    "Invalid item feedback."
                )


            valid_item = connection.execute(
                """
                SELECT id

                FROM review_plan_items

                WHERE id = ?

                AND employee_review_id = ?
                """,

                (
                    review_plan_item_id,
                    employee_review_id
                )

            ).fetchone()


            if valid_item is None:

                raise ValueError(
                    "A review item does not belong to this peer review."
                )


            validated_responses.append((
                review_plan_item_id,
                rating,
                feedback_text.strip()
            ))


        # =====================================
        # SAVE OVERALL FEEDBACK
        # =====================================

        connection.execute(
            """
            UPDATE peer_reviews

            SET
                strengths = ?,
                development_feedback = ?,
                collaboration_feedback = ?,
                overall_comment = ?,
                updated_at = CURRENT_TIMESTAMP

            WHERE id = ?
            """,

            (
                overall_fields[
                    "strengths"
                ].strip(),
                overall_fields[
                    "development_feedback"
                ].strip(),
                overall_fields[
                    "collaboration_feedback"
                ].strip(),
                overall_fields[
                    "overall_comment"
                ].strip(),
                peer_review_id
            )
        )


        # =====================================
        # SAVE ITEM RATINGS + FEEDBACK
        # =====================================

        for (
            review_plan_item_id,
            rating,
            feedback_text
        ) in validated_responses:

            connection.execute(
                """
                INSERT INTO peer_review_items
                (
                    peer_review_id,
                    review_plan_item_id,
                    rating,
                    feedback_text
                )

                VALUES (?, ?, ?, ?)

                ON CONFLICT(
                    peer_review_id,
                    review_plan_item_id
                )

                DO UPDATE SET
                    rating = excluded.rating,
                    feedback_text = excluded.feedback_text,
                    updated_at = CURRENT_TIMESTAMP
                """,

                (
                    peer_review_id,
                    review_plan_item_id,
                    rating,
                    feedback_text
                )
            )


        connection.execute(
            """
            UPDATE peer_review_assignments

            SET status = 'In Progress'

            WHERE id = ?

            AND status = 'Assigned'
            """,

            (
                review[
                    "peer_assignment_id"
                ],
            )
        )


        connection.commit()


        return jsonify({
            "success": True,
            "message":
                "Your peer review draft has been saved."
        })


    except ValueError as error:

        connection.rollback()


        return jsonify({
            "success": False,
            "message": str(error)
        }), 400


    except sqlite3.Error as error:

        connection.rollback()


        print(
            "Peer review draft save error:",
            error
        )


        return jsonify({
            "success": False,
            "message":
                "Your peer review draft could not be saved."
        }), 500


    finally:

        connection.close()


@app.route(
    "/reviews/<int:employee_review_id>/peer-review/submit",
    methods=["POST"]
)
def submit_peer_review(employee_review_id):

    # =====================================
    # AUTHENTICATION + PAYLOAD
    # =====================================

    if "user_id" not in session:

        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401


    data = request.get_json(
        silent=True
    )


    if not isinstance(data, dict):

        return jsonify({
            "success": False,
            "message": "Invalid peer review data."
        }), 400


    responses = data.get(
        "responses",
        []
    )


    if not isinstance(
        responses,
        list
    ):

        return jsonify({
            "success": False,
            "message": "Invalid peer review responses."
        }), 400


    overall_fields = {
        "strengths": data.get(
            "strengths",
            ""
        ),
        "development_feedback": data.get(
            "development_feedback",
            ""
        ),
        "collaboration_feedback": data.get(
            "collaboration_feedback",
            ""
        ),
        "overall_comment": data.get(
            "overall_comment",
            ""
        )
    }


    for field_value in overall_fields.values():

        if not isinstance(
            field_value,
            str
        ):

            return jsonify({
                "success": False,
                "message": "Invalid overall feedback."
            }), 400


    connection = get_db_connection()


    try:

        # =====================================
        # VERIFY REVIEWER OWNERSHIP
        # =====================================

        review = connection.execute(
            """
            SELECT

                peer_reviews.id
                    AS peer_review_id,

                peer_reviews.status
                    AS peer_review_status,

                peer_review_assignments.id
                    AS peer_assignment_id,

                peer_review_assignments.status
                    AS assignment_status,

                employee_reviews.review_cycle_id,

                employee_reviews.supervisor_id,

                employee_reviews.employee_name_snapshot,

                employee_reviews.status
                    AS employee_review_status,

                employees.user_id
                    AS employee_user_id,

                review_cycles.cycle_name,

                review_cycles.status
                    AS cycle_status

            FROM peer_review_assignments

            JOIN peer_reviews
                ON peer_reviews.peer_assignment_id
                    = peer_review_assignments.id

            JOIN employee_reviews
                ON peer_review_assignments.employee_review_id
                    = employee_reviews.id

            JOIN employees
                ON employee_reviews.employee_id
                    = employees.id

            JOIN review_cycles
                ON employee_reviews.review_cycle_id
                    = review_cycles.id

            WHERE peer_review_assignments.employee_review_id = ?

            AND peer_review_assignments.reviewer_user_id = ?

            AND peer_review_assignments.status != 'Removed'
            """,

            (
                employee_review_id,
                session["user_id"]
            )

        ).fetchone()


        if review is None:

            return jsonify({
                "success": False,
                "message": "Peer review not found."
            }), 404


        if (
            review["peer_review_status"]
            != "Draft"
            or
            review["assignment_status"]
            == "Submitted"
        ):

            return jsonify({
                "success": False,
                "message":
                    "This peer review has already been submitted."
            }), 409


        if review["cycle_status"] != "Active":

            return jsonify({
                "success": False,
                "message":
                    "This review cycle is no longer active."
            }), 409


        private_change_request = get_private_manager_change_request(
            connection,
            employee_review_id,
            session["user_id"]
        )


        if (
            review["employee_review_status"]
            != "Peer Review In Progress"
            and private_change_request is None
        ):

            return jsonify({
                "success": False,
                "message":
                    "This review is not in the peer feedback stage."
            }), 409


        peer_review_id = review[
            "peer_review_id"
        ]


        # =====================================
        # SAVE THE LATEST ITEM RESPONSES
        # =====================================

        seen_item_ids = set()


        for response in responses:

            if not isinstance(
                response,
                dict
            ):

                raise ValueError(
                    "Invalid peer review response."
                )


            try:

                review_plan_item_id = int(
                    response.get(
                        "review_plan_item_id"
                    )
                )

            except (
                TypeError,
                ValueError
            ):

                raise ValueError(
                    "Invalid review baseline item."
                )


            if review_plan_item_id in seen_item_ids:

                raise ValueError(
                    "A review item was included more than once."
                )


            seen_item_ids.add(
                review_plan_item_id
            )


            rating = response.get(
                "rating"
            )


            if (
                rating is not None
                and
                rating != ""
            ):

                try:

                    rating = int(
                        rating
                    )

                except (
                    TypeError,
                    ValueError
                ):

                    raise ValueError(
                        "Invalid rating."
                    )


                if rating < 1 or rating > 5:

                    raise ValueError(
                        "Ratings must be between 1 and 5."
                    )

            else:

                rating = None


            feedback_text = response.get(
                "feedback_text",
                ""
            )


            if not isinstance(
                feedback_text,
                str
            ):

                raise ValueError(
                    "Invalid item feedback."
                )


            valid_item = connection.execute(
                """
                SELECT id

                FROM review_plan_items

                WHERE id = ?

                AND employee_review_id = ?
                """,

                (
                    review_plan_item_id,
                    employee_review_id
                )

            ).fetchone()


            if valid_item is None:

                raise ValueError(
                    "A review item does not belong to this peer review."
                )


            connection.execute(
                """
                INSERT INTO peer_review_items
                (
                    peer_review_id,
                    review_plan_item_id,
                    rating,
                    feedback_text
                )

                VALUES (?, ?, ?, ?)

                ON CONFLICT(
                    peer_review_id,
                    review_plan_item_id
                )

                DO UPDATE SET
                    rating = excluded.rating,
                    feedback_text = excluded.feedback_text,
                    updated_at = CURRENT_TIMESTAMP
                """,

                (
                    peer_review_id,
                    review_plan_item_id,
                    rating,
                    feedback_text.strip()
                )
            )


        # =====================================
        # SAVE THE LATEST OVERALL FEEDBACK
        # =====================================

        connection.execute(
            """
            UPDATE peer_reviews

            SET
                strengths = ?,
                development_feedback = ?,
                collaboration_feedback = ?,
                overall_comment = ?,
                updated_at = CURRENT_TIMESTAMP

            WHERE id = ?
            """,

            (
                overall_fields[
                    "strengths"
                ].strip(),
                overall_fields[
                    "development_feedback"
                ].strip(),
                overall_fields[
                    "collaboration_feedback"
                ].strip(),
                overall_fields[
                    "overall_comment"
                ].strip(),
                peer_review_id
            )
        )


        # =====================================
        # SERVER-SIDE SUBMISSION GATE
        # =====================================

        baseline_count = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM review_plan_items

            WHERE employee_review_id = ?
            """,

            (
                employee_review_id,
            )

        ).fetchone()["total"]


        complete_item_count = connection.execute(
            """
            SELECT COUNT(*) AS total

            FROM peer_review_items

            JOIN review_plan_items
                ON peer_review_items.review_plan_item_id
                    = review_plan_items.id

            WHERE peer_review_items.peer_review_id = ?

            AND review_plan_items.employee_review_id = ?

            AND peer_review_items.rating
                BETWEEN 1 AND 5

            AND TRIM(
                COALESCE(
                    peer_review_items.feedback_text,
                    ''
                )
            ) <> ''
            """,

            (
                peer_review_id,
                employee_review_id
            )

        ).fetchone()["total"]


        if baseline_count == 0:

            raise ValueError(
                "This peer review has no performance baseline."
            )


        if complete_item_count != baseline_count:

            raise ValueError(
                (
                    "Please provide a rating and written "
                    "feedback for every performance item."
                )
            )


        missing_overall_fields = [
            field_name
            for field_name, field_value
            in overall_fields.items()
            if not field_value.strip()
        ]


        if missing_overall_fields:

            raise ValueError(
                (
                    "Please complete all four overall "
                    "feedback sections."
                )
            )


        # =====================================
        # LOCK THIS REVIEW + COMPLETE ACTION
        # =====================================

        connection.execute(
            """
            UPDATE peer_reviews

            SET
                status = 'Submitted',
                submitted_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP

            WHERE id = ?
            """,

            (
                peer_review_id,
            )
        )


        connection.execute(
            """
            UPDATE peer_review_assignments

            SET status = 'Submitted'

            WHERE id = ?
            """,

            (
                review[
                    "peer_assignment_id"
                ],
            )
        )


        connection.execute(
            """
            UPDATE review_actions

            SET
                status = 'Completed',
                completed_at = CURRENT_TIMESTAMP

            WHERE employee_review_id = ?

            AND assigned_to = ?

            AND action_type = 'PEER_REVIEW'

            AND status != 'Completed'
            """,

            (
                employee_review_id,
                session["user_id"]
            )
        )


        if private_change_request is not None:
            complete_private_manager_change_request(
                connection,
                employee_review_id,
                session["user_id"]
            )


        # =====================================
        # REVIEWER CONFIRMATION
        # =====================================

        connection.execute(
            """
            INSERT INTO notifications
            (
                user_id,
                review_cycle_id,
                employee_review_id,
                notification_type,
                title,
                message
            )

            VALUES (?, ?, ?, ?, ?, ?)
            """,

            (
                session["user_id"],
                review["review_cycle_id"],
                employee_review_id,
                "PEER_REVIEW_CONFIRMED",
                "Peer Review Submitted",
                (
                    "Your confidential peer feedback for "
                    f"{review['employee_name_snapshot']} "
                    "has been submitted and locked."
                )
            )
        )


        # =====================================
        # CHECK COLLECTIVE PEER PROGRESS
        # =====================================

        peer_progress = connection.execute(
            """
            SELECT

                COUNT(*) AS total_reviewers,

                SUM(
                    CASE
                        WHEN status = 'Submitted'
                        THEN 1
                        ELSE 0
                    END
                ) AS submitted_reviewers

            FROM peer_review_assignments

            WHERE employee_review_id = ?

            AND status != 'Removed'
            """,

            (
                employee_review_id,
            )

        ).fetchone()


        total_reviewers = (
            peer_progress[
                "total_reviewers"
            ]
            or 0
        )


        submitted_reviewers = (
            peer_progress[
                "submitted_reviewers"
            ]
            or 0
        )


        peer_phase_complete = (
            total_reviewers > 0
            and
            submitted_reviewers
            == total_reviewers
        )


        # =====================================
        # SUPERVISOR WORKFLOW SIGNAL
        # =====================================

        if review["supervisor_id"]:

            supervisor_title = (
                "Peer Review Stage Complete"
                if peer_phase_complete
                else "Peer Feedback Submitted"
            )


            supervisor_message = (
                (
                    f"All {total_reviewers} confidential "
                    "peer review(s) for "
                    f"{review['employee_name_snapshot']} "
                    "have been submitted. Supervisor "
                    "evaluation can now begin."
                )
                if peer_phase_complete
                else
                (
                    f"{submitted_reviewers} of "
                    f"{total_reviewers} confidential "
                    "peer review(s) for "
                    f"{review['employee_name_snapshot']} "
                    "have been submitted."
                )
            )


            connection.execute(
                """
                INSERT INTO notifications
                (
                    user_id,
                    review_cycle_id,
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )

                VALUES (?, ?, ?, ?, ?, ?)
                """,

                (
                    review["supervisor_id"],
                    review["review_cycle_id"],
                    employee_review_id,
                    (
                        "PEER_REVIEW_STAGE_COMPLETED"
                        if peer_phase_complete
                        else "PEER_REVIEW_SUBMITTED"
                    ),
                    supervisor_title,
                    supervisor_message
                )
            )


        # =====================================
        # ALL REVIEWERS COMPLETE: ADVANCE CASE
        # =====================================

        if peer_phase_complete and private_change_request is None:

            connection.execute(
                """
                UPDATE employee_reviews

                SET
                    status = 'Peer Review Completed',
                    updated_at = CURRENT_TIMESTAMP

                WHERE id = ?

                AND status = 'Peer Review In Progress'
                """,

                (
                    employee_review_id,
                )
            )


            if review["supervisor_id"]:

                connection.execute(
                    """
                    UPDATE review_actions

                    SET
                        status = 'Completed',
                        completed_at = CURRENT_TIMESTAMP

                    WHERE employee_review_id = ?

                    AND assigned_to = ?

                    AND action_type = 'SUPERVISOR_MONITORING'

                    AND status != 'Completed'
                    """,

                    (
                        employee_review_id,
                        review["supervisor_id"]
                    )
                )


                connection.execute(
                    """
                    INSERT INTO review_actions
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

                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)

                    ON CONFLICT(
                        review_cycle_id,
                        employee_review_id,
                        assigned_to,
                        action_type
                    )

                    DO UPDATE SET
                        title = excluded.title,
                        description = excluded.description,
                        status = 'Pending',
                        priority = 'High',
                        completed_at = NULL
                    """,

                    (
                        review["review_cycle_id"],
                        employee_review_id,
                        review["supervisor_id"],
                        "SUPERVISOR_EVALUATION",
                        (
                            "Evaluate "
                            f"{review['employee_name_snapshot']}"
                        ),
                        (
                            "Complete the supervisor evaluation "
                            "after reviewing the self-assessment "
                            "and confidential peer feedback."
                        ),
                        "Pending",
                        "High"
                    )
                )


            connection.execute(
                """
                INSERT INTO notifications
                (
                    user_id,
                    review_cycle_id,
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )

                VALUES (?, ?, ?, ?, ?, ?)
                """,

                (
                    review["employee_user_id"],
                    review["review_cycle_id"],
                    employee_review_id,
                    "PEER_REVIEW_STAGE_COMPLETED",
                    "Peer Review Stage Complete",
                    (
                        f"The confidential peer feedback stage for "
                        f"your {review['cycle_name']} review is "
                        "complete. Your supervisor evaluation is next."
                    )
                )
            )


        connection.commit()


        flash(
            "Your peer review has been submitted and locked.",
            "success"
        )


        return jsonify({
            "success": True,
            "message":
                "Peer review submitted successfully.",
            "submitted_reviewers":
                submitted_reviewers,
            "total_reviewers":
                total_reviewers,
            "peer_phase_complete":
                peer_phase_complete,
            "redirect_url":
                url_for(
                    "peer_review_studio",
                    employee_review_id=employee_review_id
                )
        })


    except ValueError as error:

        connection.rollback()


        return jsonify({
            "success": False,
            "message": str(error)
        }), 400


    except sqlite3.Error as error:

        connection.rollback()


        print(
            "Peer review submission error:",
            error
        )


        return jsonify({
            "success": False,
            "message":
                "The peer review could not be submitted."
        }), 500


    finally:

        connection.close()


# =========================================================
# PB09 - SUPERVISOR EVALUATION
# =========================================================

SUPERVISOR_RECOMMENDATIONS = (
    "Exceptional Performance",
    "Exceeds Expectations",
    "Meets Expectations",
    "Development Required"
)


def parse_supervisor_evaluation_payload(data):

    if not isinstance(data, dict):
        raise ValueError("Invalid supervisor evaluation data.")

    responses = data.get("responses", [])

    if not isinstance(responses, list):
        raise ValueError("Invalid evaluation responses.")

    overall_text_fields = {
        "performance_summary": data.get("performance_summary", ""),
        "key_strengths": data.get("key_strengths", ""),
        "development_priorities": data.get("development_priorities", ""),
        "support_plan": data.get("support_plan", "")
    }

    for value in overall_text_fields.values():
        if not isinstance(value, str):
            raise ValueError("Invalid overall evaluation feedback.")

    overall_rating = data.get("overall_rating")

    if overall_rating not in (None, ""):
        try:
            overall_rating = int(overall_rating)
        except (TypeError, ValueError):
            raise ValueError("Invalid overall rating.")

        if overall_rating < 1 or overall_rating > 5:
            raise ValueError("Ratings must be between 1 and 5.")
    else:
        overall_rating = None

    recommendation = data.get("recommendation", "")

    if not isinstance(recommendation, str):
        raise ValueError("Invalid performance recommendation.")

    recommendation = recommendation.strip()

    if (
        recommendation
        and
        recommendation not in SUPERVISOR_RECOMMENDATIONS
    ):
        raise ValueError("Please select a valid recommendation.")

    validated_responses = []
    seen_item_ids = set()

    for response in responses:
        if not isinstance(response, dict):
            raise ValueError("Invalid evaluation response.")

        try:
            review_plan_item_id = int(
                response.get("review_plan_item_id")
            )
        except (TypeError, ValueError):
            raise ValueError("Invalid review baseline item.")

        if review_plan_item_id in seen_item_ids:
            raise ValueError(
                "A review item was included more than once."
            )

        seen_item_ids.add(review_plan_item_id)

        rating = response.get("rating")

        if rating not in (None, ""):
            try:
                rating = int(rating)
            except (TypeError, ValueError):
                raise ValueError("Invalid item rating.")

            if rating < 1 or rating > 5:
                raise ValueError("Ratings must be between 1 and 5.")
        else:
            rating = None

        evaluation_text = response.get("evaluation_text", "")

        if not isinstance(evaluation_text, str):
            raise ValueError("Invalid item evaluation feedback.")

        validated_responses.append({
            "review_plan_item_id": review_plan_item_id,
            "rating": rating,
            "evaluation_text": evaluation_text.strip()
        })

    return {
        "responses": validated_responses,
        "overall_rating": overall_rating,
        "performance_summary":
            overall_text_fields["performance_summary"].strip(),
        "key_strengths":
            overall_text_fields["key_strengths"].strip(),
        "development_priorities":
            overall_text_fields["development_priorities"].strip(),
        "support_plan":
            overall_text_fields["support_plan"].strip(),
        "recommendation": recommendation
    }


def get_supervisor_evaluation_context(
    connection,
    employee_review_id,
    supervisor_id
):

    return connection.execute(
        """
        SELECT
            employee_reviews.id AS employee_review_id,
            employee_reviews.employee_id,
            employee_reviews.review_cycle_id,
            employee_reviews.supervisor_id,
            employee_reviews.employee_name_snapshot,
            employee_reviews.employee_code_snapshot,
            employee_reviews.department_snapshot,
            employee_reviews.job_title_snapshot,
            employee_reviews.status AS employee_review_status,
            employees.user_id AS employee_user_id,
            review_cycles.cycle_name,
            review_cycles.start_date,
            review_cycles.end_date,
            review_cycles.status AS cycle_status,
            supervisor_evaluations.id AS supervisor_evaluation_id,
            supervisor_evaluations.status AS evaluation_status,
            supervisor_evaluations.overall_rating,
            supervisor_evaluations.performance_summary,
            supervisor_evaluations.key_strengths,
            supervisor_evaluations.development_priorities,
            supervisor_evaluations.support_plan,
            supervisor_evaluations.recommendation,
            supervisor_evaluations.submitted_at

        FROM employee_reviews

        JOIN employees
            ON employees.id = employee_reviews.employee_id

        JOIN review_cycles
            ON review_cycles.id = employee_reviews.review_cycle_id

        LEFT JOIN supervisor_evaluations
            ON supervisor_evaluations.employee_review_id
                = employee_reviews.id
            AND supervisor_evaluations.supervisor_id = ?

        WHERE employee_reviews.id = ?
        AND employee_reviews.supervisor_id = ?
        """,
        (
            supervisor_id,
            employee_review_id,
            supervisor_id
        )
    ).fetchone()


def save_supervisor_evaluation_payload(
    connection,
    employee_review_id,
    supervisor_evaluation_id,
    payload
):

    connection.execute(
        """
        UPDATE supervisor_evaluations
        SET
            overall_rating = ?,
            performance_summary = ?,
            key_strengths = ?,
            development_priorities = ?,
            support_plan = ?,
            recommendation = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            payload["overall_rating"],
            payload["performance_summary"],
            payload["key_strengths"],
            payload["development_priorities"],
            payload["support_plan"],
            payload["recommendation"],
            supervisor_evaluation_id
        )
    )

    for response in payload["responses"]:
        valid_item = connection.execute(
            """
            SELECT id
            FROM review_plan_items
            WHERE id = ?
            AND employee_review_id = ?
            """,
            (
                response["review_plan_item_id"],
                employee_review_id
            )
        ).fetchone()

        if valid_item is None:
            raise ValueError(
                "A review item does not belong to this evaluation."
            )

        connection.execute(
            """
            INSERT INTO supervisor_evaluation_items
            (
                supervisor_evaluation_id,
                review_plan_item_id,
                rating,
                evaluation_text
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(
                supervisor_evaluation_id,
                review_plan_item_id
            )
            DO UPDATE SET
                rating = excluded.rating,
                evaluation_text = excluded.evaluation_text,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                supervisor_evaluation_id,
                response["review_plan_item_id"],
                response["rating"],
                response["evaluation_text"]
            )
        )


@app.route(
    "/reviews/<int:employee_review_id>/supervisor-evaluation"
)
def supervisor_evaluation_workspace(employee_review_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "Supervisor":
        return redirect(url_for("dashboard"))

    connection = get_db_connection()

    try:
        review = get_supervisor_evaluation_context(
            connection,
            employee_review_id,
            session["user_id"]
        )

        if review is None:
            flash(
                "You are not assigned to supervise this review.",
                "error"
            )
            return redirect(url_for("dashboard"))

        if review["cycle_status"] != "Active":
            flash(
                "This review cycle is not currently active.",
                "error"
            )
            return redirect(url_for("dashboard"))

        allowed_statuses = (
            "Peer Review Completed",
            "Supervisor Evaluation In Progress",
            "Supervisor Evaluation Submitted",
            "Manager Approval Pending",
            "Changes Requested",
            "Approved",
            "Completed"
        )

        if review["employee_review_status"] not in allowed_statuses:
            flash(
                "Peer feedback must be completed before evaluation.",
                "error"
            )
            return redirect(url_for("dashboard"))

        peer_progress = connection.execute(
            """
            SELECT
                COUNT(*) AS total_reviewers,
                SUM(
                    CASE WHEN status = 'Submitted' THEN 1 ELSE 0 END
                ) AS submitted_reviewers
            FROM peer_review_assignments
            WHERE employee_review_id = ?
            AND status != 'Removed'
            """,
            (employee_review_id,)
        ).fetchone()

        if (
            not peer_progress["total_reviewers"]
            or
            (peer_progress["submitted_reviewers"] or 0)
                != peer_progress["total_reviewers"]
        ):
            flash(
                "All assigned peer reviews must be submitted first.",
                "error"
            )
            return redirect(url_for("dashboard"))

        if review["supervisor_evaluation_id"] is None:
            cursor = connection.execute(
                """
                INSERT INTO supervisor_evaluations
                (
                    employee_review_id,
                    supervisor_id,
                    status
                )
                VALUES (?, ?, 'Draft')
                """,
                (
                    employee_review_id,
                    session["user_id"]
                )
            )

            transition = connection.execute(
                """
                UPDATE employee_reviews
                SET
                    status = 'Supervisor Evaluation In Progress',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                AND status = 'Peer Review Completed'
                """,
                (employee_review_id,)
            )

            if transition.rowcount:
                connection.execute(
                    """
                    INSERT INTO notifications
                    (
                        user_id,
                        review_cycle_id,
                        employee_review_id,
                        notification_type,
                        title,
                        message
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review["employee_user_id"],
                        review["review_cycle_id"],
                        employee_review_id,
                        "SUPERVISOR_EVALUATION_STARTED",
                        "Supervisor Evaluation Started",
                        (
                            f"Your supervisor has started the evaluation "
                            f"for {review['cycle_name']}."
                        )
                    )
                )

            connection.commit()

            review = get_supervisor_evaluation_context(
                connection,
                employee_review_id,
                session["user_id"]
            )

        baseline_items = connection.execute(
            """
            SELECT
                review_plan_items.id AS review_plan_item_id,
                review_plan_items.item_type,
                review_plan_items.title,
                review_plan_items.description,
                review_plan_items.target,
                review_plan_items.due_date,
                self_assessment_items.rating AS self_rating,
                self_assessment_items.response_text AS self_reflection,
                supervisor_evaluation_items.rating
                    AS supervisor_rating,
                supervisor_evaluation_items.evaluation_text,
                (
                    SELECT ROUND(AVG(peer_review_items.rating), 1)
                    FROM peer_review_items
                    JOIN peer_reviews
                        ON peer_reviews.id
                            = peer_review_items.peer_review_id
                    JOIN peer_review_assignments
                        ON peer_review_assignments.id
                            = peer_reviews.peer_assignment_id
                    WHERE peer_review_items.review_plan_item_id
                        = review_plan_items.id
                    AND peer_review_assignments.employee_review_id
                        = review_plan_items.employee_review_id
                    AND peer_reviews.status = 'Submitted'
                    AND peer_review_assignments.status = 'Submitted'
                ) AS peer_average_rating,
                (
                    SELECT COUNT(*)
                    FROM peer_review_items
                    JOIN peer_reviews
                        ON peer_reviews.id
                            = peer_review_items.peer_review_id
                    JOIN peer_review_assignments
                        ON peer_review_assignments.id
                            = peer_reviews.peer_assignment_id
                    WHERE peer_review_items.review_plan_item_id
                        = review_plan_items.id
                    AND peer_review_assignments.employee_review_id
                        = review_plan_items.employee_review_id
                    AND peer_reviews.status = 'Submitted'
                    AND peer_review_assignments.status = 'Submitted'
                ) AS peer_rating_count

            FROM review_plan_items

            LEFT JOIN self_assessments
                ON self_assessments.employee_review_id
                    = review_plan_items.employee_review_id

            LEFT JOIN self_assessment_items
                ON self_assessment_items.self_assessment_id
                    = self_assessments.id
                AND self_assessment_items.review_plan_item_id
                    = review_plan_items.id

            LEFT JOIN supervisor_evaluation_items
                ON supervisor_evaluation_items.supervisor_evaluation_id = ?
                AND supervisor_evaluation_items.review_plan_item_id
                    = review_plan_items.id

            WHERE review_plan_items.employee_review_id = ?

            ORDER BY
                CASE review_plan_items.item_type
                    WHEN 'Responsibility' THEN 1
                    WHEN 'Expectation' THEN 2
                    WHEN 'KPI' THEN 3
                    WHEN 'Goal' THEN 4
                    ELSE 5
                END,
                review_plan_items.id
            """,
            (
                review["supervisor_evaluation_id"],
                employee_review_id
            )
        ).fetchall()

        self_assessment = connection.execute(
            """
            SELECT
                self_assessments.*,
                (
                    SELECT COUNT(*)
                    FROM self_assessment_evidence
                    WHERE self_assessment_evidence.self_assessment_id
                        = self_assessments.id
                ) AS evidence_count
            FROM self_assessments
            WHERE employee_review_id = ?
            """,
            (employee_review_id,)
        ).fetchone()

        evidence_files = connection.execute(
            """
            SELECT
                self_assessment_evidence.id,
                self_assessment_evidence.original_file_name,
                self_assessment_evidence.file_size,
                self_assessment_evidence.uploaded_at
            FROM self_assessment_evidence
            JOIN self_assessments
                ON self_assessments.id
                    = self_assessment_evidence.self_assessment_id
            WHERE self_assessments.employee_review_id = ?
            AND self_assessments.status = 'Submitted'
            ORDER BY self_assessment_evidence.uploaded_at DESC
            """,
            (employee_review_id,)
        ).fetchall()

        peer_overviews = connection.execute(
            """
            SELECT
                peer_reviews.id,
                peer_reviews.strengths,
                peer_reviews.development_feedback,
                peer_reviews.collaboration_feedback,
                peer_reviews.overall_comment
            FROM peer_reviews
            JOIN peer_review_assignments
                ON peer_review_assignments.id
                    = peer_reviews.peer_assignment_id
            WHERE peer_review_assignments.employee_review_id = ?
            AND peer_review_assignments.status = 'Submitted'
            AND peer_reviews.status = 'Submitted'
            ORDER BY peer_reviews.id
            """,
            (employee_review_id,)
        ).fetchall()

        peer_feedback_rows = connection.execute(
            """
            SELECT
                peer_review_items.review_plan_item_id,
                peer_review_items.rating,
                peer_review_items.feedback_text,
                peer_reviews.id AS peer_review_id
            FROM peer_review_items
            JOIN peer_reviews
                ON peer_reviews.id = peer_review_items.peer_review_id
            JOIN peer_review_assignments
                ON peer_review_assignments.id
                    = peer_reviews.peer_assignment_id
            WHERE peer_review_assignments.employee_review_id = ?
            AND peer_review_assignments.status = 'Submitted'
            AND peer_reviews.status = 'Submitted'
            ORDER BY peer_reviews.id
            """,
            (employee_review_id,)
        ).fetchall()

        peer_feedback_map = {}

        for feedback in peer_feedback_rows:
            peer_feedback_map.setdefault(
                feedback["review_plan_item_id"],
                []
            ).append(feedback)

        manager_feedback = get_private_manager_change_request(
            connection,
            employee_review_id,
            session["user_id"]
        )

        return render_template(
            "supervisor_evaluation.html",
            review=review,
            baseline_items=baseline_items,
            self_assessment=self_assessment,
            evidence_files=evidence_files,
            peer_overviews=peer_overviews,
            peer_feedback_map=peer_feedback_map,
            peer_progress=peer_progress,
            manager_feedback=manager_feedback,
            recommendation_options=SUPERVISOR_RECOMMENDATIONS,
            user_name=session["user_name"],
            user_role=session["user_role"]
        )

    except sqlite3.Error as error:
        connection.rollback()
        print("Supervisor workspace error:", error)
        flash(
            "The supervisor evaluation workspace could not be opened.",
            "error"
        )
        return redirect(url_for("dashboard"))

    finally:
        connection.close()


@app.route(
    "/reviews/<int:employee_review_id>/supervisor-evaluation/save",
    methods=["POST"]
)
def save_supervisor_evaluation_draft(employee_review_id):

    if "user_id" not in session:
        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401

    if session["user_role"] != "Supervisor":
        return jsonify({
            "success": False,
            "message": "Only the assigned supervisor can save this evaluation."
        }), 403

    try:
        payload = parse_supervisor_evaluation_payload(
            request.get_json(silent=True)
        )
    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error)
        }), 400

    connection = get_db_connection()

    try:
        review = get_supervisor_evaluation_context(
            connection,
            employee_review_id,
            session["user_id"]
        )

        if (
            review is None
            or
            review["supervisor_evaluation_id"] is None
        ):
            return jsonify({
                "success": False,
                "message": "Supervisor evaluation not found."
            }), 404


        private_change_request = get_private_manager_change_request(
            connection,
            employee_review_id,
            session["user_id"]
        )

        if review["evaluation_status"] != "Draft":
            return jsonify({
                "success": False,
                "message": "This evaluation has already been submitted."
            }), 409

        if review["cycle_status"] != "Active":
            return jsonify({
                "success": False,
                "message": "This review cycle is no longer active."
            }), 409

        if (
            review["employee_review_status"]
            != "Supervisor Evaluation In Progress"
            and private_change_request is None
        ):
            return jsonify({
                "success": False,
                "message": "This review is not in the supervisor evaluation stage."
            }), 409

        save_supervisor_evaluation_payload(
            connection,
            employee_review_id,
            review["supervisor_evaluation_id"],
            payload
        )

        connection.commit()

        return jsonify({
            "success": True,
            "message": "Your supervisor evaluation draft has been saved."
        })

    except ValueError as error:
        connection.rollback()
        return jsonify({
            "success": False,
            "message": str(error)
        }), 400

    except sqlite3.Error as error:
        connection.rollback()
        print("Supervisor evaluation draft error:", error)
        return jsonify({
            "success": False,
            "message": "The evaluation draft could not be saved."
        }), 500

    finally:
        connection.close()


@app.route(
    "/reviews/<int:employee_review_id>/supervisor-evaluation/submit",
    methods=["POST"]
)
def submit_supervisor_evaluation(employee_review_id):

    if "user_id" not in session:
        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401

    if session["user_role"] != "Supervisor":
        return jsonify({
            "success": False,
            "message": "Only the assigned supervisor can submit this evaluation."
        }), 403

    try:
        payload = parse_supervisor_evaluation_payload(
            request.get_json(silent=True)
        )
    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error)
        }), 400

    connection = get_db_connection()

    try:
        review = get_supervisor_evaluation_context(
            connection,
            employee_review_id,
            session["user_id"]
        )

        if (
            review is None
            or
            review["supervisor_evaluation_id"] is None
        ):
            return jsonify({
                "success": False,
                "message": "Supervisor evaluation not found."
            }), 404

        if review["evaluation_status"] != "Draft":
            return jsonify({
                "success": False,
                "message": "This evaluation has already been submitted."
            }), 409

        if review["cycle_status"] != "Active":
            return jsonify({
                "success": False,
                "message": "This review cycle is no longer active."
            }), 409

        private_change_request = get_private_manager_change_request(
            connection,
            employee_review_id,
            session["user_id"]
        )


        if (
            review["employee_review_status"]
            != "Supervisor Evaluation In Progress"
            and private_change_request is None
        ):
            return jsonify({
                "success": False,
                "message": "This review is not ready for supervisor submission."
            }), 409

        save_supervisor_evaluation_payload(
            connection,
            employee_review_id,
            review["supervisor_evaluation_id"],
            payload
        )

        baseline_count = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM review_plan_items
            WHERE employee_review_id = ?
            """,
            (employee_review_id,)
        ).fetchone()["total"]

        complete_item_count = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM supervisor_evaluation_items
            JOIN review_plan_items
                ON review_plan_items.id
                    = supervisor_evaluation_items.review_plan_item_id
            WHERE supervisor_evaluation_items.supervisor_evaluation_id = ?
            AND review_plan_items.employee_review_id = ?
            AND supervisor_evaluation_items.rating BETWEEN 1 AND 5
            AND TRIM(
                COALESCE(
                    supervisor_evaluation_items.evaluation_text,
                    ''
                )
            ) <> ''
            """,
            (
                review["supervisor_evaluation_id"],
                employee_review_id
            )
        ).fetchone()["total"]

        if baseline_count == 0:
            raise ValueError(
                "This evaluation has no performance baseline."
            )

        if complete_item_count != baseline_count:
            raise ValueError(
                "Please provide a rating and evaluation for every performance item."
            )

        if payload["overall_rating"] is None:
            raise ValueError("Please provide an overall performance rating.")

        required_text_fields = (
            payload["performance_summary"],
            payload["key_strengths"],
            payload["development_priorities"],
            payload["support_plan"]
        )

        if any(not value for value in required_text_fields):
            raise ValueError(
                "Please complete all four overall evaluation sections."
            )

        if not payload["recommendation"]:
            raise ValueError("Please select a performance recommendation.")

        connection.execute(
            """
            UPDATE supervisor_evaluations
            SET
                status = 'Submitted',
                submitted_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (review["supervisor_evaluation_id"],)
        )

        if private_change_request is None:
            connection.execute(
                """
                UPDATE employee_reviews
                SET
                    status = 'Supervisor Evaluation Submitted',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                AND status = 'Supervisor Evaluation In Progress'
                """,
                (employee_review_id,)
            )

        connection.execute(
            """
            UPDATE review_actions
            SET
                status = 'Completed',
                completed_at = CURRENT_TIMESTAMP
            WHERE employee_review_id = ?
            AND assigned_to = ?
            AND action_type = 'SUPERVISOR_EVALUATION'
            AND status != 'Completed'
            """,
            (
                employee_review_id,
                session["user_id"]
            )
        )


        if private_change_request is not None:
            complete_private_manager_change_request(
                connection,
                employee_review_id,
                session["user_id"]
            )

        connection.execute(
            """
            INSERT INTO notifications
            (
                user_id,
                review_cycle_id,
                employee_review_id,
                notification_type,
                title,
                message
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session["user_id"],
                review["review_cycle_id"],
                employee_review_id,
                "SUPERVISOR_EVALUATION_CONFIRMED",
                "Evaluation Submitted",
                (
                    f"Your evaluation for "
                    f"{review['employee_name_snapshot']} "
                    "has been submitted and locked."
                )
            )
        )

        connection.execute(
            """
            INSERT INTO notifications
            (
                user_id,
                review_cycle_id,
                employee_review_id,
                notification_type,
                title,
                message
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                review["employee_user_id"],
                review["review_cycle_id"],
                employee_review_id,
                "SUPERVISOR_EVALUATION_SUBMITTED",
                "Supervisor Evaluation Complete",
                (
                    f"Your supervisor evaluation for "
                    f"{review['cycle_name']} is complete. "
                    "Management approval is the next stage."
                )
            )
        )

        manager = connection.execute(
            """
            SELECT
                users.id,
                COUNT(manager_approvals.id) AS pending_workload
            FROM users
            LEFT JOIN manager_approvals
                ON manager_approvals.manager_id = users.id
                AND manager_approvals.status = 'Pending'
            WHERE users.role = 'Manager'
            GROUP BY users.id
            ORDER BY pending_workload, users.full_name
            LIMIT 1
            """
        ).fetchone()

        if manager is not None and private_change_request is None:
            connection.execute(
                """
                INSERT INTO manager_approvals
                (
                    employee_review_id,
                    manager_id,
                    status
                )
                VALUES (?, ?, 'Pending')
                ON CONFLICT(employee_review_id)
                DO UPDATE SET
                    manager_id = excluded.manager_id,
                    status = 'Pending',
                    decision_note = NULL,
                    decided_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    employee_review_id,
                    manager["id"]
                )
            )

            connection.execute(
                """
                INSERT INTO review_actions
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
                VALUES (?, ?, ?, ?, ?, ?, 'Pending', 'High')
                ON CONFLICT(
                    review_cycle_id,
                    employee_review_id,
                    assigned_to,
                    action_type
                )
                DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    status = 'Pending',
                    priority = 'High',
                    completed_at = NULL
                """,
                (
                    review["review_cycle_id"],
                    employee_review_id,
                    manager["id"],
                    "MANAGER_APPROVAL",
                    (
                        f"Approve {review['employee_name_snapshot']}'s "
                        "Review"
                    ),
                    (
                        "Review the submitted supervisor evaluation "
                        "and record the final management decision."
                    )
                )
            )

            connection.execute(
                """
                INSERT INTO notifications
                (
                    user_id,
                    review_cycle_id,
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    manager["id"],
                    review["review_cycle_id"],
                    employee_review_id,
                    "MANAGER_APPROVAL_READY",
                    "Review Ready for Approval",
                    (
                        f"{review['employee_name_snapshot']}'s "
                        "supervisor evaluation is ready for your decision."
                    )
                )
            )

        hr_users = []

        if private_change_request is None:
            hr_users = connection.execute(
                """
                SELECT id
                FROM users
                WHERE role = 'HR'
                """
            ).fetchall()

        for hr_user in hr_users:
            connection.execute(
                """
                INSERT INTO review_actions
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
                VALUES (?, ?, ?, ?, ?, ?, 'Pending', 'High')
                ON CONFLICT(
                    review_cycle_id,
                    employee_review_id,
                    assigned_to,
                    action_type
                )
                DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    status = 'Pending',
                    priority = 'High',
                    completed_at = NULL
                """,
                (
                    review["review_cycle_id"],
                    employee_review_id,
                    hr_user["id"],
                    "MANAGER_APPROVAL_COORDINATION",
                    (
                        "Prepare Manager Approval for "
                        f"{review['employee_name_snapshot']}"
                    ),
                    (
                        "The supervisor evaluation is complete. "
                        "Prepare the review for management approval."
                    )
                )
            )

            connection.execute(
                """
                INSERT INTO notifications
                (
                    user_id,
                    review_cycle_id,
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    hr_user["id"],
                    review["review_cycle_id"],
                    employee_review_id,
                    "SUPERVISOR_EVALUATION_SUBMITTED",
                    "Supervisor Evaluation Submitted",
                    (
                        f"The evaluation for "
                        f"{review['employee_name_snapshot']} "
                        "is ready for the management approval stage."
                    )
                )
            )

        connection.commit()

        flash(
            "The supervisor evaluation has been submitted and locked.",
            "success"
        )

        return jsonify({
            "success": True,
            "message": "Supervisor evaluation submitted successfully.",
            "redirect_url": url_for(
                "supervisor_evaluation_workspace",
                employee_review_id=employee_review_id
            )
        })

    except ValueError as error:
        connection.rollback()
        return jsonify({
            "success": False,
            "message": str(error)
        }), 400

    except sqlite3.Error as error:
        connection.rollback()
        print("Supervisor evaluation submission error:", error)
        return jsonify({
            "success": False,
            "message": "The supervisor evaluation could not be submitted."
        }), 500

    finally:
        connection.close()


# =========================================================
# PB10 - MANAGEMENT APPROVAL
# =========================================================

def get_manager_approval_context(connection, employee_review_id):

    return connection.execute(
        """
        SELECT
            employee_reviews.id AS employee_review_id,
            employee_reviews.employee_id,
            employee_reviews.review_cycle_id,
            employee_reviews.supervisor_id,
            employee_reviews.employee_name_snapshot,
            employee_reviews.employee_code_snapshot,
            employee_reviews.department_snapshot,
            employee_reviews.job_title_snapshot,
            employee_reviews.status AS employee_review_status,
            employees.user_id AS employee_user_id,
            review_cycles.cycle_name,
            review_cycles.start_date,
            review_cycles.end_date,
            review_cycles.status AS cycle_status,
            supervisor_users.full_name AS supervisor_name,
            supervisor_evaluations.id AS supervisor_evaluation_id,
            supervisor_evaluations.status AS supervisor_evaluation_status,
            supervisor_evaluations.overall_rating,
            supervisor_evaluations.performance_summary,
            supervisor_evaluations.key_strengths,
            supervisor_evaluations.development_priorities,
            supervisor_evaluations.support_plan,
            supervisor_evaluations.recommendation,
            supervisor_evaluations.submitted_at
                AS supervisor_submitted_at,
            manager_approvals.id AS manager_approval_id,
            manager_approvals.manager_id,
            manager_approvals.status AS approval_status,
            manager_approvals.decision_note,
            manager_approvals.decided_at,
            manager_users.full_name AS manager_name

        FROM employee_reviews

        JOIN employees
            ON employees.id = employee_reviews.employee_id

        JOIN review_cycles
            ON review_cycles.id = employee_reviews.review_cycle_id

        JOIN users AS supervisor_users
            ON supervisor_users.id = employee_reviews.supervisor_id

        JOIN supervisor_evaluations
            ON supervisor_evaluations.employee_review_id
                = employee_reviews.id

        JOIN manager_approvals
            ON manager_approvals.employee_review_id
                = employee_reviews.id

        JOIN users AS manager_users
            ON manager_users.id = manager_approvals.manager_id

        WHERE employee_reviews.id = ?
        """,
        (employee_review_id,)
    ).fetchone()


def parse_manager_decision_note(data):

    if not isinstance(data, dict):
        raise ValueError("Invalid management decision data.")

    decision_note = data.get("decision_note", "")

    if not isinstance(decision_note, str):
        raise ValueError("Invalid management decision note.")

    decision_note = decision_note.strip()

    if not decision_note:
        raise ValueError(
            "Please record a decision note before continuing."
        )

    if len(decision_note) > 3000:
        raise ValueError(
            "The decision note must be 3,000 characters or fewer."
        )

    return decision_note


def ensure_manager_change_request_schema(connection):

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
            FOREIGN KEY (employee_review_id) REFERENCES employee_reviews(id),
            FOREIGN KEY (recipient_user_id) REFERENCES users(id),
            FOREIGN KEY (requested_by) REFERENCES users(id),
            CHECK (recipient_role IN ('Employee', 'Peer Reviewer', 'Supervisor')),
            CHECK (status IN ('Pending', 'Completed'))
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


def ensure_par_meeting_schema(connection):
    """Keep PB11 available for existing databases without a reset."""
    existing_meetings = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'par_meetings'"
    ).fetchone()
    if existing_meetings and "employee_review_id INTEGER NOT NULL UNIQUE" in existing_meetings["sql"]:
        # Databases created before PB12 do not yet have the outcome table.
        connection.execute(
            """CREATE TABLE IF NOT EXISTS par_meeting_outcomes (
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
            )"""
        )
        # PB11 originally allowed one meeting per review. Preserve those records
        # while upgrading to a meeting history that supports follow-up PARs.
        connection.executescript(
            """
            PRAGMA foreign_keys = OFF;
            BEGIN;
            ALTER TABLE par_meeting_outcomes RENAME TO par_meeting_outcomes_legacy;
            ALTER TABLE par_meeting_attendees RENAME TO par_meeting_attendees_legacy;
            ALTER TABLE par_meetings RENAME TO par_meetings_legacy;
            CREATE TABLE par_meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_review_id INTEGER NOT NULL,
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
            );
            CREATE TABLE par_meeting_attendees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                par_meeting_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                attendee_role TEXT NOT NULL,
                FOREIGN KEY (par_meeting_id) REFERENCES par_meetings(id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE(par_meeting_id, user_id),
                CHECK (attendee_role IN ('Employee', 'Supervisor', 'Manager'))
            );
            CREATE TABLE par_meeting_outcomes (
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
            );
            INSERT INTO par_meetings SELECT * FROM par_meetings_legacy;
            INSERT INTO par_meeting_attendees SELECT * FROM par_meeting_attendees_legacy;
            INSERT INTO par_meeting_outcomes SELECT * FROM par_meeting_outcomes_legacy;
            DROP TABLE par_meeting_outcomes_legacy;
            DROP TABLE par_meeting_attendees_legacy;
            DROP TABLE par_meetings_legacy;
            COMMIT;
            PRAGMA foreign_keys = ON;
            """
        )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS par_meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_review_id INTEGER NOT NULL,
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
        );
        CREATE TABLE IF NOT EXISTS par_meeting_attendees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            par_meeting_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            attendee_role TEXT NOT NULL,
            FOREIGN KEY (par_meeting_id) REFERENCES par_meetings(id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(par_meeting_id, user_id),
            CHECK (attendee_role IN ('Employee', 'Supervisor', 'Manager'))
        );
        CREATE TABLE IF NOT EXISTS user_unavailability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            CHECK (end_at > start_at)
        );
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
        );
        CREATE INDEX IF NOT EXISTS idx_par_meeting_availability
        ON par_meeting_attendees(user_id, par_meeting_id);
        CREATE INDEX IF NOT EXISTS idx_user_unavailability_window
        ON user_unavailability(user_id, start_at, end_at);
        """
    )


def ensure_pdp_schema(connection):
    """Create PB13 tables for existing project databases without a reset."""
    connection.executescript(
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
        );
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
        );
        CREATE INDEX IF NOT EXISTS idx_pdp_activities_plan
        ON pdp_activities(pdp_plan_id, sort_order);
        """
    )
    activity_columns = {
        column["name"]
        for column in connection.execute("PRAGMA table_info(pdp_activities)").fetchall()
    }
    if "employee_progress_note" not in activity_columns:
        connection.execute("ALTER TABLE pdp_activities ADD COLUMN employee_progress_note TEXT")
    if "employee_updated_at" not in activity_columns:
        connection.execute("ALTER TABLE pdp_activities ADD COLUMN employee_updated_at TIMESTAMP")


def ensure_reminder_schema(connection):
    """Keep PB16 reminder delivery safe and duplicate-free."""
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


def dispatch_workflow_reminders(connection):
    """Create one useful in-app reminder per outstanding workflow signal.

    This runs safely during normal use. The delivery log prevents duplicate
    messages whenever a dashboard is refreshed.
    """
    ensure_pdp_schema(connection)
    ensure_reminder_schema(connection)

    pending_actions = connection.execute(
        """
        SELECT review_actions.id, review_actions.assigned_to,
               review_actions.review_cycle_id, review_actions.employee_review_id,
               review_actions.title, review_actions.due_date,
               review_actions.created_at, review_cycles.cycle_name
        FROM review_actions
        JOIN review_cycles ON review_cycles.id = review_actions.review_cycle_id
        WHERE review_actions.status = 'Pending'
          AND review_cycles.status = 'Active'
          AND (
              (review_actions.due_date IS NOT NULL
               AND date(review_actions.due_date) <= date('now', '+3 day'))
              OR (review_actions.due_date IS NULL
                  AND datetime(review_actions.created_at) <= datetime('now', '-1 day'))
          )
        """
    ).fetchall()
    for action in pending_actions:
        is_overdue = action["due_date"] and action["due_date"] < datetime.now().date().isoformat()
        reminder_kind = "ACTION_OVERDUE" if is_overdue else "ACTION_REMINDER"
        inserted = connection.execute(
            """
            INSERT OR IGNORE INTO workflow_reminder_log
                (user_id, review_action_id, pdp_activity_id, reminder_kind)
            VALUES (?, ?, NULL, ?)
            """,
            (action["assigned_to"], action["id"], reminder_kind),
        )
        if inserted.rowcount:
            timing = "is overdue" if is_overdue else "is ready for your attention"
            connection.execute(
                """
                INSERT INTO notifications
                    (user_id, review_cycle_id, employee_review_id, notification_type, title, message)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    action["assigned_to"], action["review_cycle_id"],
                    action["employee_review_id"], reminder_kind,
                    "Workflow reminder",
                    f"{action['title']} {timing} in {action['cycle_name']}.",
                ),
            )

    overdue_activities = connection.execute(
        """
        SELECT pdp_activities.id, pdp_activities.activity, pdp_activities.target_date,
               pdp_plans.employee_review_id, employee_reviews.review_cycle_id,
               employees.user_id AS employee_user_id, employee_reviews.supervisor_id
        FROM pdp_activities
        JOIN pdp_plans ON pdp_plans.id = pdp_activities.pdp_plan_id
        JOIN employee_reviews ON employee_reviews.id = pdp_plans.employee_review_id
        JOIN employees ON employees.id = employee_reviews.employee_id
        JOIN review_cycles ON review_cycles.id = employee_reviews.review_cycle_id
        WHERE pdp_activities.status != 'Completed'
          AND date(pdp_activities.target_date) < date('now')
          AND pdp_plans.status = 'Active'
          AND review_cycles.status IN ('Active', 'Closed')
        """
    ).fetchall()
    for activity in overdue_activities:
        for user_id, recipient_label in (
            (activity["employee_user_id"], "Your"),
            (activity["supervisor_id"], "An employee's"),
        ):
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO workflow_reminder_log
                    (user_id, review_action_id, pdp_activity_id, reminder_kind)
                VALUES (?, NULL, ?, 'PDP_ACTIVITY_OVERDUE')
                """,
                (user_id, activity["id"]),
            )
            if inserted.rowcount:
                connection.execute(
                    """
                    INSERT INTO notifications
                        (user_id, review_cycle_id, employee_review_id, notification_type, title, message)
                    VALUES (?, ?, ?, 'PDP_ACTIVITY_OVERDUE', 'Development activity needs attention', ?)
                    """,
                    (
                        user_id, activity["review_cycle_id"], activity["employee_review_id"],
                        f"{recipient_label} PDP activity ‘{activity['activity']}’ was due on {activity['target_date']}.",
                    ),
                )


def sync_par_workflow_actions(connection):
    """Make sure every held PAR meeting has its next supervisor action.

    This also safely repairs meetings that were completed before PB12
    (recording PAR outcomes) was added to the product.
    """
    ensure_par_meeting_schema(connection)
    pending_outcomes = connection.execute(
        """
        SELECT employee_reviews.id AS employee_review_id,
               employee_reviews.review_cycle_id,
               employee_reviews.supervisor_id
        FROM employee_reviews
        JOIN review_cycles
            ON review_cycles.id = employee_reviews.review_cycle_id
        JOIN par_meetings
            ON par_meetings.id = (
                SELECT latest_meeting.id
                FROM par_meetings AS latest_meeting
                WHERE latest_meeting.employee_review_id = employee_reviews.id
                ORDER BY latest_meeting.id DESC
                LIMIT 1
            )
        LEFT JOIN par_meeting_outcomes
            ON par_meeting_outcomes.par_meeting_id = par_meetings.id
        WHERE review_cycles.status = 'Active'
          AND par_meetings.status = 'Held'
          AND par_meeting_outcomes.id IS NULL
        """
    ).fetchall()

    for review in pending_outcomes:
        connection.execute(
            """
            INSERT INTO review_actions
                (review_cycle_id, employee_review_id, assigned_to, action_type,
                 title, description, status, priority)
            VALUES (?, ?, ?, 'PAR_OUTCOME', 'Record PAR meeting outcome',
                    'Document the PAR discussion and agreed development actions.',
                    'Pending', 'High')
            ON CONFLICT(review_cycle_id, employee_review_id, assigned_to, action_type)
            DO UPDATE SET
                title = excluded.title,
                description = excluded.description,
                status = 'Pending',
                priority = 'High',
                completed_at = NULL
            """,
            (
                review["review_cycle_id"],
                review["employee_review_id"],
                review["supervisor_id"],
            ),
        )


def sync_pdp_progress_actions(connection, initialize_schema=True):
    """Keep the employee progress task aligned with every active PDP."""
    if initialize_schema:
        ensure_pdp_schema(connection)
    plans = connection.execute(
        """
        SELECT pdp_plans.id AS pdp_plan_id,
               pdp_plans.employee_review_id,
               employee_reviews.review_cycle_id,
               employees.user_id AS employee_user_id,
               NOT EXISTS (
                   SELECT 1 FROM pdp_activities
                   WHERE pdp_activities.pdp_plan_id = pdp_plans.id
                     AND pdp_activities.status != 'Completed'
               ) AS all_activities_complete
        FROM pdp_plans
        JOIN employee_reviews ON employee_reviews.id = pdp_plans.employee_review_id
        JOIN employees ON employees.id = employee_reviews.employee_id
        JOIN review_cycles ON review_cycles.id = employee_reviews.review_cycle_id
        WHERE pdp_plans.status = 'Active'
          AND review_cycles.status IN ('Active', 'Closed')
        """
    ).fetchall()
    for plan in plans:
        complete = bool(plan["all_activities_complete"])
        connection.execute(
            """
            INSERT INTO review_actions
                (review_cycle_id, employee_review_id, assigned_to, action_type,
                 title, description, status, priority, completed_at)
            VALUES (?, ?, ?, 'PDP_PROGRESS', 'Update PDP progress',
                    'Review your development activities and share your latest progress.',
                    ?, 'Normal', CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END)
            ON CONFLICT(review_cycle_id, employee_review_id, assigned_to, action_type)
            DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                status=excluded.status,
                priority='Normal',
                completed_at=excluded.completed_at
            """,
            (plan["review_cycle_id"], plan["employee_review_id"], plan["employee_user_id"],
             "Completed" if complete else "Pending", int(complete)),
        )


def get_par_meeting_context(connection, employee_review_id, initialize_schema=True):
    if initialize_schema:
        ensure_par_meeting_schema(connection)
    return connection.execute(
        """
        SELECT employee_reviews.id AS employee_review_id,
               employee_reviews.review_cycle_id,
               employee_reviews.status AS review_status,
               employee_reviews.employee_name_snapshot,
               employee_reviews.employee_code_snapshot,
               employee_reviews.department_snapshot,
               employees.user_id AS employee_user_id,
               employee_reviews.supervisor_id,
               supervisor.full_name AS supervisor_name,
               manager_approvals.manager_id,
               manager.full_name AS manager_name,
               review_cycles.cycle_name,
               review_cycles.status AS cycle_status,
               par_meetings.id AS par_meeting_id,
               par_meetings.meeting_date,
               par_meetings.start_time,
               par_meetings.end_time,
               par_meetings.meeting_format,
               par_meetings.location,
               par_meetings.agenda,
               par_meetings.status AS par_status
        FROM employee_reviews
        JOIN employees ON employees.id = employee_reviews.employee_id
        JOIN review_cycles ON review_cycles.id = employee_reviews.review_cycle_id
        JOIN users AS supervisor ON supervisor.id = employee_reviews.supervisor_id
        JOIN manager_approvals ON manager_approvals.employee_review_id = employee_reviews.id
            AND manager_approvals.status = 'Approved'
        JOIN users AS manager ON manager.id = manager_approvals.manager_id
        LEFT JOIN par_meetings ON par_meetings.id = (
            SELECT latest_meeting.id
            FROM par_meetings AS latest_meeting
            WHERE latest_meeting.employee_review_id = employee_reviews.id
            ORDER BY latest_meeting.id DESC
            LIMIT 1
        )
        WHERE employee_reviews.id = ?
        """,
        (employee_review_id,)
    ).fetchone()


def can_access_par_meeting(connection, review):
    """PAR details are private to people actually attending the meeting."""
    if session["user_id"] in {
        review["employee_user_id"], review["supervisor_id"]
    }:
        return True
    if session["user_role"] != "Manager" or not review["par_meeting_id"]:
        return False
    return connection.execute(
        """SELECT 1 FROM par_meeting_attendees
           WHERE par_meeting_id = ? AND user_id = ? AND attendee_role = 'Manager'""",
        (review["par_meeting_id"], session["user_id"])
    ).fetchone() is not None


def par_attendees(review, include_manager):
    attendees = [
        (review["employee_user_id"], "Employee"),
        (review["supervisor_id"], "Supervisor")
    ]
    if include_manager:
        attendees.append((review["manager_id"], "Manager"))
    return attendees


def par_now():
    """Central clock for meeting validation and deterministic workflow tests."""
    return datetime.now()


def valid_par_slot(connection, attendee_ids, meeting_date, start_time, end_time,
                   excluding_meeting_id=None):
    start_at = f"{meeting_date}T{start_time}"
    end_at = f"{meeting_date}T{end_time}"
    marks = ",".join("?" for _ in attendee_ids)
    unavailable = connection.execute(
        f"""SELECT 1 FROM user_unavailability
            WHERE user_id IN ({marks}) AND start_at < ? AND end_at > ? LIMIT 1""",
        (*attendee_ids, end_at, start_at)
    ).fetchone()
    if unavailable:
        return False
    query = f"""SELECT 1 FROM par_meetings
        JOIN par_meeting_attendees ON par_meeting_attendees.par_meeting_id = par_meetings.id
        WHERE par_meeting_attendees.user_id IN ({marks})
        AND par_meetings.meeting_date = ?
        AND par_meetings.status IN ('Scheduled', 'Rescheduled')
        AND par_meetings.start_time < ? AND par_meetings.end_time > ?"""
    parameters = [*attendee_ids, meeting_date, end_time, start_time]
    if excluding_meeting_id:
        query += " AND par_meetings.id != ?"
        parameters.append(excluding_meeting_id)
    return connection.execute(query + " LIMIT 1", parameters).fetchone() is None


def get_private_manager_change_request(
    connection,
    employee_review_id,
    recipient_user_id
):

    ensure_manager_change_request_schema(connection)

    return connection.execute(
        """
        SELECT
            manager_change_requests.id,
            'Changes Requested' AS status,
            manager_change_requests.recipient_role,
            manager_change_requests.private_note,
            manager_change_requests.private_note AS decision_note,
            manager_change_requests.created_at,
            users.full_name AS manager_name
        FROM manager_change_requests
        JOIN users
            ON users.id = manager_change_requests.requested_by
        WHERE manager_change_requests.employee_review_id = ?
        AND manager_change_requests.recipient_user_id = ?
        AND manager_change_requests.status = 'Pending'
        ORDER BY manager_change_requests.id DESC
        LIMIT 1
        """,
        (employee_review_id, recipient_user_id)
    ).fetchone()


def complete_private_manager_change_request(
    connection,
    employee_review_id,
    recipient_user_id
):

    ensure_manager_change_request_schema(connection)

    completion = connection.execute(
        """
        UPDATE manager_change_requests
        SET
            status = 'Completed',
            completed_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE employee_review_id = ?
        AND recipient_user_id = ?
        AND status = 'Pending'
        """,
        (employee_review_id, recipient_user_id)
    )

    if not completion.rowcount:
        return False

    pending_count = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM manager_change_requests
        WHERE employee_review_id = ?
        AND status = 'Pending'
        """,
        (employee_review_id,)
    ).fetchone()["total"]

    if pending_count:
        return False

    approval = connection.execute(
        """
        SELECT
            manager_approvals.manager_id,
            employee_reviews.review_cycle_id,
            employee_reviews.employee_name_snapshot
        FROM manager_approvals
        JOIN employee_reviews
            ON employee_reviews.id = manager_approvals.employee_review_id
        WHERE manager_approvals.employee_review_id = ?
        AND manager_approvals.status = 'Changes Requested'
        """,
        (employee_review_id,)
    ).fetchone()

    if approval is None:
        return False

    connection.execute(
        """
        UPDATE manager_approvals
        SET
            status = 'Pending',
            decision_note = NULL,
            decided_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE employee_review_id = ?
        """,
        (employee_review_id,)
    )
    connection.execute(
        """
        UPDATE employee_reviews
        SET
            status = 'Supervisor Evaluation Submitted',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (employee_review_id,)
    )
    connection.execute(
        """
        INSERT INTO review_actions
        (
            review_cycle_id, employee_review_id, assigned_to, action_type,
            title, description, status, priority
        )
        VALUES (?, ?, ?, 'MANAGER_APPROVAL', ?, ?, 'Pending', 'High')
        ON CONFLICT(
            review_cycle_id, employee_review_id, assigned_to, action_type
        )
        DO UPDATE SET
            title = excluded.title,
            description = excluded.description,
            status = 'Pending',
            priority = 'High',
            completed_at = NULL
        """,
        (
            approval["review_cycle_id"],
            employee_review_id,
            approval["manager_id"],
            f"Approve {approval['employee_name_snapshot']}'s Review",
            "All requested contributor updates are complete. Record the final decision."
        )
    )
    connection.execute(
        """
        INSERT INTO notifications
        (
            user_id, review_cycle_id, employee_review_id,
            notification_type, title, message
        )
        VALUES (?, ?, ?, 'MANAGER_CHANGES_COMPLETED', ?, ?)
        """,
        (
            approval["manager_id"],
            approval["review_cycle_id"],
            employee_review_id,
            "Requested changes completed",
            f"All requested updates for {approval['employee_name_snapshot']}'s review are ready for your final decision."
        )
    )

    return True


@app.route(
    "/reviews/<int:employee_review_id>/manager-approval"
)
def manager_approval_workspace(employee_review_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] not in ("Manager", "HR"):
        return redirect(url_for("dashboard"))

    connection = get_db_connection()

    try:
        review = get_manager_approval_context(
            connection,
            employee_review_id
        )

        if review is None:
            flash("Management approval record not found.", "error")
            return redirect(url_for("dashboard"))

        ensure_manager_change_request_schema(connection)

        if (
            session["user_role"] == "Manager"
            and review["manager_id"] != session["user_id"]
        ):
            flash(
                "This approval is assigned to another manager.",
                "error"
            )
            return redirect(url_for("dashboard"))

        if review["cycle_status"] != "Active":
            flash("This review cycle is no longer active.", "error")
            return redirect(url_for("dashboard"))

        allowed_statuses = (
            "Supervisor Evaluation Submitted",
            "Manager Approval Pending",
            "Supervisor Evaluation In Progress",
            "Changes Requested",
            "Approved",
            "Completed"
        )

        if review["employee_review_status"] not in allowed_statuses:
            flash(
                "The supervisor evaluation is not ready for approval.",
                "error"
            )
            return redirect(url_for("dashboard"))

        if (
            session["user_role"] == "Manager"
            and review["approval_status"] == "Pending"
            and review["employee_review_status"]
                == "Supervisor Evaluation Submitted"
        ):
            transition = connection.execute(
                """
                UPDATE employee_reviews
                SET
                    status = 'Manager Approval Pending',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                AND status = 'Supervisor Evaluation Submitted'
                """,
                (employee_review_id,)
            )

            if transition.rowcount:
                connection.execute(
                    """
                    INSERT INTO notifications
                    (
                        user_id,
                        review_cycle_id,
                        employee_review_id,
                        notification_type,
                        title,
                        message
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review["employee_user_id"],
                        review["review_cycle_id"],
                        employee_review_id,
                        "MANAGER_APPROVAL_STARTED",
                        "Management Approval Started",
                        (
                            f"Management is reviewing your "
                            f"{review['cycle_name']} outcome."
                        )
                    )
                )

            connection.commit()
            review = get_manager_approval_context(
                connection,
                employee_review_id
            )

        baseline_items = connection.execute(
            """
            SELECT
                review_plan_items.id AS review_plan_item_id,
                review_plan_items.item_type,
                review_plan_items.title,
                review_plan_items.description,
                review_plan_items.target,
                review_plan_items.due_date,
                self_assessment_items.rating AS self_rating,
                supervisor_evaluation_items.rating
                    AS supervisor_rating,
                supervisor_evaluation_items.evaluation_text,
                (
                    SELECT ROUND(AVG(peer_review_items.rating), 1)
                    FROM peer_review_items
                    JOIN peer_reviews
                        ON peer_reviews.id
                            = peer_review_items.peer_review_id
                    JOIN peer_review_assignments
                        ON peer_review_assignments.id
                            = peer_reviews.peer_assignment_id
                    WHERE peer_review_items.review_plan_item_id
                        = review_plan_items.id
                    AND peer_review_assignments.employee_review_id
                        = review_plan_items.employee_review_id
                    AND peer_reviews.status = 'Submitted'
                    AND peer_review_assignments.status = 'Submitted'
                ) AS peer_average_rating,
                (
                    SELECT COUNT(*)
                    FROM peer_review_items
                    JOIN peer_reviews
                        ON peer_reviews.id
                            = peer_review_items.peer_review_id
                    JOIN peer_review_assignments
                        ON peer_review_assignments.id
                            = peer_reviews.peer_assignment_id
                    WHERE peer_review_items.review_plan_item_id
                        = review_plan_items.id
                    AND peer_review_assignments.employee_review_id
                        = review_plan_items.employee_review_id
                    AND peer_reviews.status = 'Submitted'
                    AND peer_review_assignments.status = 'Submitted'
                ) AS peer_rating_count

            FROM review_plan_items

            LEFT JOIN self_assessments
                ON self_assessments.employee_review_id
                    = review_plan_items.employee_review_id

            LEFT JOIN self_assessment_items
                ON self_assessment_items.self_assessment_id
                    = self_assessments.id
                AND self_assessment_items.review_plan_item_id
                    = review_plan_items.id

            LEFT JOIN supervisor_evaluation_items
                ON supervisor_evaluation_items.supervisor_evaluation_id
                    = ?
                AND supervisor_evaluation_items.review_plan_item_id
                    = review_plan_items.id

            WHERE review_plan_items.employee_review_id = ?
            ORDER BY review_plan_items.id
            """,
            (
                review["supervisor_evaluation_id"],
                employee_review_id
            )
        ).fetchall()

        peer_comments = connection.execute(
            """
            SELECT
                peer_review_items.review_plan_item_id,
                peer_review_items.feedback_text
            FROM peer_review_items
            JOIN peer_reviews
                ON peer_reviews.id = peer_review_items.peer_review_id
            JOIN peer_review_assignments
                ON peer_review_assignments.id
                    = peer_reviews.peer_assignment_id
            WHERE peer_review_assignments.employee_review_id = ?
            AND peer_review_assignments.status = 'Submitted'
            AND peer_reviews.status = 'Submitted'
            AND TRIM(COALESCE(peer_review_items.feedback_text, '')) <> ''
            ORDER BY peer_review_items.review_plan_item_id,
                peer_review_items.id
            """,
            (employee_review_id,)
        ).fetchall()

        comments_by_item = {}

        for comment in peer_comments:
            comments_by_item.setdefault(
                comment["review_plan_item_id"],
                []
            ).append(comment["feedback_text"])

        evidence_files = connection.execute(
            """
            SELECT
                self_assessment_evidence.id,
                self_assessment_evidence.original_file_name,
                self_assessment_evidence.file_size,
                self_assessment_evidence.uploaded_at
            FROM self_assessment_evidence
            JOIN self_assessments
                ON self_assessments.id
                    = self_assessment_evidence.self_assessment_id
            WHERE self_assessments.employee_review_id = ?
            AND self_assessments.status = 'Submitted'
            ORDER BY self_assessment_evidence.uploaded_at DESC
            """,
            (employee_review_id,)
        ).fetchall()

        readonly = (
            session["user_role"] != "Manager"
            or review["approval_status"] != "Pending"
            or review["employee_review_status"] not in (
                "Supervisor Evaluation Submitted",
                "Manager Approval Pending"
            )
        )

        manager_options = []

        change_request_recipients = []
        manager_change_requests = []

        if session["user_role"] == "Manager":
            change_request_recipients = [
                {
                    "user_id": review["employee_user_id"],
                    "role": "Employee",
                    "name": review["employee_name_snapshot"],
                    "description": "Reopen the self-assessment and supporting evidence."
                },
                {
                    "user_id": review["supervisor_id"],
                    "role": "Supervisor",
                    "name": review["supervisor_name"],
                    "description": "Reopen the supervisor evaluation and recommendation."
                }
            ]

            peer_recipients = connection.execute(
                """
                SELECT DISTINCT
                    peer_review_assignments.reviewer_user_id AS user_id,
                    users.full_name
                FROM peer_review_assignments
                JOIN peer_reviews
                    ON peer_reviews.peer_assignment_id
                        = peer_review_assignments.id
                JOIN users
                    ON users.id = peer_review_assignments.reviewer_user_id
                WHERE peer_review_assignments.employee_review_id = ?
                AND peer_review_assignments.status = 'Submitted'
                AND peer_reviews.status = 'Submitted'
                ORDER BY users.full_name
                """,
                (employee_review_id,)
            ).fetchall()

            change_request_recipients.extend(
                {
                    "user_id": peer["user_id"],
                    "role": "Peer Reviewer",
                    "name": peer["full_name"],
                    "description": "Reopen this confidential peer feedback only."
                }
                for peer in peer_recipients
            )

            manager_change_requests = connection.execute(
                """
                SELECT
                    manager_change_requests.recipient_role,
                    manager_change_requests.private_note,
                    manager_change_requests.status,
                    manager_change_requests.created_at,
                    manager_change_requests.completed_at,
                    users.full_name AS recipient_name
                FROM manager_change_requests
                JOIN users
                    ON users.id = manager_change_requests.recipient_user_id
                WHERE manager_change_requests.employee_review_id = ?
                ORDER BY manager_change_requests.id DESC
                """,
                (employee_review_id,)
            ).fetchall()

        if session["user_role"] == "HR":
            manager_options = connection.execute(
                """
                SELECT id, full_name, email
                FROM users
                WHERE role = 'Manager'
                ORDER BY full_name
                """
            ).fetchall()

        return render_template(
            "manager_approval.html",
            review=review,
            baseline_items=baseline_items,
            comments_by_item=comments_by_item,
            evidence_files=evidence_files,
            readonly=readonly,
            manager_options=manager_options,
            change_request_recipients=change_request_recipients,
            manager_change_requests=manager_change_requests,
            user_name=session["user_name"],
            user_role=session["user_role"]
        )

    finally:
        connection.close()


@app.route(
    "/reviews/<int:employee_review_id>/manager-approval/assign",
    methods=["POST"]
)
def assign_manager_approval(employee_review_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["user_role"] != "HR":
        return redirect(url_for("dashboard"))

    try:
        manager_id = int(request.form.get("manager_id", ""))
    except (TypeError, ValueError):
        flash("Select a valid manager.", "error")
        return redirect(url_for(
            "manager_approval_workspace",
            employee_review_id=employee_review_id
        ))

    connection = get_db_connection()

    try:
        review = get_manager_approval_context(
            connection,
            employee_review_id
        )

        manager = connection.execute(
            """
            SELECT id, full_name
            FROM users
            WHERE id = ?
            AND role = 'Manager'
            """,
            (manager_id,)
        ).fetchone()

        if review is None or manager is None:
            flash("The review or manager could not be found.", "error")
            return redirect(url_for("dashboard"))

        if (
            review["cycle_status"] != "Active"
            or review["approval_status"] != "Pending"
        ):
            flash("This approval can no longer be reassigned.", "error")
            return redirect(url_for(
                "manager_approval_workspace",
                employee_review_id=employee_review_id
            ))

        previous_manager_id = review["manager_id"]

        if previous_manager_id == manager_id:
            flash("This approval is already assigned to that manager.", "success")
            return redirect(url_for(
                "manager_approval_workspace",
                employee_review_id=employee_review_id
            ))

        connection.execute(
            """
            UPDATE manager_approvals
            SET
                manager_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE employee_review_id = ?
            AND status = 'Pending'
            """,
            (manager_id, employee_review_id)
        )

        connection.execute(
            """
            UPDATE review_actions
            SET
                status = 'Completed',
                completed_at = CURRENT_TIMESTAMP
            WHERE employee_review_id = ?
            AND assigned_to = ?
            AND action_type = 'MANAGER_APPROVAL'
            AND status = 'Pending'
            """,
            (employee_review_id, previous_manager_id)
        )

        connection.execute(
            """
            INSERT INTO review_actions
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
            VALUES (?, ?, ?, 'MANAGER_APPROVAL', ?, ?, 'Pending', 'High')
            ON CONFLICT(
                review_cycle_id,
                employee_review_id,
                assigned_to,
                action_type
            )
            DO UPDATE SET
                title = excluded.title,
                description = excluded.description,
                status = 'Pending',
                priority = 'High',
                completed_at = NULL
            """,
            (
                review["review_cycle_id"],
                employee_review_id,
                manager_id,
                f"Approve {review['employee_name_snapshot']}'s Review",
                "Review the submitted evaluation and record the final decision."
            )
        )

        connection.execute(
            """
            INSERT INTO notifications
            (
                user_id,
                review_cycle_id,
                employee_review_id,
                notification_type,
                title,
                message
            )
            VALUES (?, ?, ?, 'MANAGER_APPROVAL_ASSIGNED', ?, ?)
            """,
            (
                manager_id,
                review["review_cycle_id"],
                employee_review_id,
                "Approval Assigned",
                (
                    f"HR assigned {review['employee_name_snapshot']}'s "
                    "review to you for approval."
                )
            )
        )

        connection.commit()
        flash(
            f"Approval assigned to {manager['full_name']}.",
            "success"
        )

    except sqlite3.Error as error:
        connection.rollback()
        print("Manager assignment error:", error)
        flash("The manager assignment could not be changed.", "error")

    finally:
        connection.close()

    return redirect(url_for(
        "manager_approval_workspace",
        employee_review_id=employee_review_id
    ))


@app.route(
    "/reviews/<int:employee_review_id>/manager-approval/approve",
    methods=["POST"]
)
def approve_manager_review(employee_review_id):

    if "user_id" not in session:
        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401

    if session["user_role"] != "Manager":
        return jsonify({
            "success": False,
            "message": "Only the assigned manager can approve this review."
        }), 403

    try:
        decision_note = parse_manager_decision_note(
            request.get_json(silent=True)
        )
    except ValueError as error:
        return jsonify({
            "success": False,
            "message": str(error)
        }), 400

    connection = get_db_connection()

    try:
        review = get_manager_approval_context(
            connection,
            employee_review_id
        )

        if review is None or review["manager_id"] != session["user_id"]:
            return jsonify({
                "success": False,
                "message": "Management approval not found."
            }), 404

        if review["cycle_status"] != "Active":
            return jsonify({
                "success": False,
                "message": "This review cycle is no longer active."
            }), 409

        if (
            review["approval_status"] != "Pending"
            or review["supervisor_evaluation_status"] != "Submitted"
            or review["employee_review_status"] not in (
                "Supervisor Evaluation Submitted",
                "Manager Approval Pending"
            )
        ):
            return jsonify({
                "success": False,
                "message": "This review is no longer awaiting approval."
            }), 409

        connection.execute(
            """
            UPDATE manager_approvals
            SET
                status = 'Approved',
                decision_note = ?,
                decided_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            AND status = 'Pending'
            """,
            (
                decision_note,
                review["manager_approval_id"]
            )
        )

        connection.execute(
            """
            UPDATE employee_reviews
            SET
                status = 'Approved',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (employee_review_id,)
        )

        connection.execute(
            """
            INSERT INTO final_review_acknowledgements
            (
                employee_review_id,
                employee_user_id,
                status
            )
            VALUES (?, ?, 'Pending')
            ON CONFLICT(employee_review_id)
            DO UPDATE SET
                employee_user_id = excluded.employee_user_id,
                status = 'Pending',
                employee_comment = NULL,
                acknowledged_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                employee_review_id,
                review["employee_user_id"]
            )
        )

        connection.execute(
            """
            INSERT INTO review_actions
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
            VALUES (?, ?, ?, ?, ?, ?, 'Pending', 'High')
            ON CONFLICT(
                review_cycle_id,
                employee_review_id,
                assigned_to,
                action_type
            )
            DO UPDATE SET
                title = excluded.title,
                description = excluded.description,
                status = 'Pending',
                priority = 'High',
                completed_at = NULL
            """,
            (
                review["review_cycle_id"],
                employee_review_id,
                review["employee_user_id"],
                "FINAL_REVIEW_ACKNOWLEDGEMENT",
                "Acknowledge Final Review Outcome",
                (
                    "Read the approved review outcome and confirm that "
                    "it has been received. You may also add a final "
                    "employee comment."
                )
            )
        )

        connection.execute(
            """
            INSERT INTO review_actions
            (review_cycle_id, employee_review_id, assigned_to, action_type,
             title, description, status, priority)
            VALUES (?, ?, ?, 'PAR_MEETING', 'Arrange PAR meeting',
                    'Check shared availability and arrange the Performance Appraisal Review meeting.',
                    'Pending', 'High')
            ON CONFLICT(review_cycle_id, employee_review_id, assigned_to, action_type)
            DO UPDATE SET title=excluded.title, description=excluded.description,
                status='Pending', completed_at=NULL, priority='High'
            """,
            (review["review_cycle_id"], employee_review_id, review["supervisor_id"])
        )

        connection.execute(
            """
            UPDATE review_actions
            SET
                status = 'Completed',
                completed_at = CURRENT_TIMESTAMP
            WHERE employee_review_id = ?
            AND action_type IN (
                'MANAGER_APPROVAL',
                'MANAGER_APPROVAL_COORDINATION'
            )
            AND status != 'Completed'
            """,
            (employee_review_id,)
        )

        recipients = (
            (
                review["employee_user_id"],
                "REVIEW_APPROVED",
                "Performance Review Approved",
                (
                    f"Your {review['cycle_name']} performance review "
                    "has received final management approval."
                )
            ),
            (
                review["supervisor_id"],
                "REVIEW_APPROVED",
                "Team Review Approved",
                (
                    f"{review['employee_name_snapshot']}'s review "
                    "has received final management approval."
                )
            ),
            (
                session["user_id"],
                "MANAGER_APPROVAL_CONFIRMED",
                "Approval Recorded",
                (
                    f"Your approval for "
                    f"{review['employee_name_snapshot']} is recorded."
                )
            )
        )

        for user_id, notification_type, title, message in recipients:
            connection.execute(
                """
                INSERT INTO notifications
                (
                    user_id,
                    review_cycle_id,
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    review["review_cycle_id"],
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )
            )

        hr_users = connection.execute(
            "SELECT id FROM users WHERE role = 'HR'"
        ).fetchall()

        for hr_user in hr_users:
            connection.execute(
                """
                INSERT INTO notifications
                (
                    user_id,
                    review_cycle_id,
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    hr_user["id"],
                    review["review_cycle_id"],
                    employee_review_id,
                    "REVIEW_APPROVED",
                    "Review Approved",
                    (
                        f"{review['employee_name_snapshot']}'s review "
                        "has completed the approval workflow."
                    )
                )
            )

        connection.commit()

        flash(
            "The performance review has been approved and locked.",
            "success"
        )

        return jsonify({
            "success": True,
            "message": "Final management approval recorded.",
            "redirect_url": url_for(
                "manager_approval_workspace",
                employee_review_id=employee_review_id
            )
        })

    except sqlite3.Error as error:
        connection.rollback()
        print("Manager approval error:", error)
        return jsonify({
            "success": False,
            "message": "The management decision could not be recorded."
        }), 500

    finally:
        connection.close()


@app.route(
    "/reviews/<int:employee_review_id>/manager-approval/request-changes",
    methods=["POST"]
)
def request_manager_review_changes(employee_review_id):

    if "user_id" not in session:
        return jsonify({"success": False, "message": "Authentication required."}), 401

    if session["user_role"] != "Manager":
        return jsonify({"success": False, "message": "Only the assigned manager can request changes."}), 403

    payload = request.get_json(silent=True)
    requests_payload = payload.get("change_requests") if isinstance(payload, dict) else None

    if not isinstance(requests_payload, list) or not requests_payload:
        return jsonify({"success": False, "message": "Select at least one recipient and add their private note."}), 400

    connection = get_db_connection()

    try:
        ensure_manager_change_request_schema(connection)
        review = get_manager_approval_context(connection, employee_review_id)

        if review is None or review["manager_id"] != session["user_id"]:
            return jsonify({"success": False, "message": "Management approval not found."}), 404

        if (
            review["cycle_status"] != "Active"
            or review["approval_status"] != "Pending"
            or review["supervisor_evaluation_status"] != "Submitted"
            or review["employee_review_status"] not in (
                "Supervisor Evaluation Submitted",
                "Manager Approval Pending"
            )
        ):
            return jsonify({"success": False, "message": "This review can no longer be returned."}), 409

        allowed_recipients = {
            review["employee_user_id"]: "Employee",
            review["supervisor_id"]: "Supervisor"
        }
        peer_rows = connection.execute(
            """
            SELECT peer_review_assignments.reviewer_user_id
            FROM peer_review_assignments
            JOIN peer_reviews
                ON peer_reviews.peer_assignment_id = peer_review_assignments.id
            WHERE peer_review_assignments.employee_review_id = ?
            AND peer_review_assignments.status = 'Submitted'
            AND peer_reviews.status = 'Submitted'
            """,
            (employee_review_id,)
        ).fetchall()
        allowed_recipients.update(
            {peer["reviewer_user_id"]: "Peer Reviewer" for peer in peer_rows}
        )

        change_requests = []
        seen_recipients = set()
        for requested_change in requests_payload:
            if not isinstance(requested_change, dict):
                raise ValueError("Invalid change request.")
            try:
                recipient_user_id = int(requested_change.get("recipient_user_id"))
            except (TypeError, ValueError):
                raise ValueError("Select a valid change-request recipient.")
            private_note = requested_change.get("private_note", "")
            if not isinstance(private_note, str) or not private_note.strip():
                raise ValueError("Every selected recipient needs a private note.")
            private_note = private_note.strip()
            if len(private_note) > 3000:
                raise ValueError("A private note must be 3,000 characters or fewer.")
            if recipient_user_id not in allowed_recipients:
                raise ValueError("A selected recipient is not part of this review.")
            if recipient_user_id in seen_recipients:
                raise ValueError("Each person can receive only one request at a time.")
            seen_recipients.add(recipient_user_id)
            change_requests.append((
                recipient_user_id,
                allowed_recipients[recipient_user_id],
                private_note
            ))

        connection.execute(
            """
            UPDATE manager_approvals
            SET
                status = 'Changes Requested',
                decision_note = ?,
                decided_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            AND status = 'Pending'
            """,
            (
                f"Private changes requested from {len(change_requests)} contributor(s).",
                review["manager_approval_id"]
            )
        )
        connection.execute(
            """
            UPDATE employee_reviews
            SET status = 'Changes Requested', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (employee_review_id,)
        )
        connection.execute(
            """
            UPDATE review_actions
            SET status = 'Completed', completed_at = CURRENT_TIMESTAMP
            WHERE employee_review_id = ?
            AND action_type IN ('MANAGER_APPROVAL', 'MANAGER_APPROVAL_COORDINATION')
            AND status != 'Completed'
            """,
            (employee_review_id,)
        )

        for recipient_user_id, recipient_role, private_note in change_requests:
            connection.execute(
                """
                INSERT INTO manager_change_requests
                (
                    employee_review_id, recipient_user_id, recipient_role,
                    private_note, requested_by
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    employee_review_id,
                    recipient_user_id,
                    recipient_role,
                    private_note,
                    session["user_id"]
                )
            )

            if recipient_role == "Employee":
                connection.execute(
                    """
                    UPDATE self_assessments
                    SET status = 'Draft', submitted_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE employee_review_id = ?
                    """,
                    (employee_review_id,)
                )
                action_type = "SELF_ASSESSMENT"
                title = "Update Self-Assessment"
                description = "Management requested a private update to your self-assessment."
            elif recipient_role == "Peer Reviewer":
                connection.execute(
                    """
                    UPDATE peer_reviews
                    SET status = 'Draft', submitted_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE peer_assignment_id IN (
                        SELECT id FROM peer_review_assignments
                        WHERE employee_review_id = ? AND reviewer_user_id = ?
                    )
                    """,
                    (employee_review_id, recipient_user_id)
                )
                connection.execute(
                    """
                    UPDATE peer_review_assignments
                    SET status = 'In Progress'
                    WHERE employee_review_id = ? AND reviewer_user_id = ?
                    """,
                    (employee_review_id, recipient_user_id)
                )
                action_type = "PEER_REVIEW"
                title = "Update Confidential Peer Feedback"
                description = "Management requested a private update to your confidential peer feedback."
            else:
                connection.execute(
                    """
                    UPDATE supervisor_evaluations
                    SET status = 'Draft', submitted_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE employee_review_id = ?
                    """,
                    (employee_review_id,)
                )
                action_type = "SUPERVISOR_EVALUATION"
                title = "Update Supervisor Evaluation"
                description = "Management requested a private update to your evaluation."

            connection.execute(
                """
                INSERT INTO review_actions
                (
                    review_cycle_id, employee_review_id, assigned_to,
                    action_type, title, description, status, priority
                )
                VALUES (?, ?, ?, ?, ?, ?, 'Pending', 'High')
                ON CONFLICT(
                    review_cycle_id, employee_review_id, assigned_to, action_type
                )
                DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    status = 'Pending',
                    priority = 'High',
                    completed_at = NULL
                """,
                (
                    review["review_cycle_id"],
                    employee_review_id,
                    recipient_user_id,
                    action_type,
                    title,
                    description
                )
            )
            connection.execute(
                """
                INSERT INTO notifications
                (
                    user_id, review_cycle_id, employee_review_id,
                    notification_type, title, message
                )
                VALUES (?, ?, ?, 'MANAGER_PRIVATE_CHANGE_REQUEST', ?, ?)
                """,
                (
                    recipient_user_id,
                    review["review_cycle_id"],
                    employee_review_id,
                    "Private update requested",
                    "Management has requested a private update to your part of this review."
                )
            )

        connection.execute(
            """
            INSERT INTO notifications
            (
                user_id, review_cycle_id, employee_review_id,
                notification_type, title, message
            )
            VALUES (?, ?, ?, 'MANAGER_CHANGE_REQUEST_CREATED', ?, ?)
            """,
            (
                session["user_id"],
                review["review_cycle_id"],
                employee_review_id,
                "Private change requests sent",
                f"{len(change_requests)} contributor(s) were asked to update their section."
            )
        )

        connection.commit()
        return jsonify({
            "success": True,
            "message": "Private change requests sent.",
            "redirect_url": url_for("dashboard")
        })

    except ValueError as error:
        connection.rollback()
        return jsonify({"success": False, "message": str(error)}), 400
    except sqlite3.Error as error:
        connection.rollback()
        print("Manager change request error:", error)
        return jsonify({"success": False, "message": "The private change requests could not be sent."}), 500
    finally:
        connection.close()


# =========================================================
# PB11 - PAR MEETING + AVAILABILITY
# =========================================================

@app.route("/reviews/<int:employee_review_id>/par-meeting")
def par_meeting_workspace(employee_review_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    connection = get_db_connection()
    try:
        review = get_par_meeting_context(connection, employee_review_id)
        if review is None or not can_access_par_meeting(connection, review):
            flash("PAR meeting not found.", "error")
            return redirect(url_for("dashboard"))
        attendees = []
        if review["par_meeting_id"]:
            attendees = connection.execute(
                """SELECT users.full_name, par_meeting_attendees.attendee_role
                   FROM par_meeting_attendees JOIN users ON users.id = par_meeting_attendees.user_id
                   WHERE par_meeting_attendees.par_meeting_id = ?
                   ORDER BY CASE par_meeting_attendees.attendee_role
                       WHEN 'Employee' THEN 1 WHEN 'Supervisor' THEN 2 ELSE 3 END""",
                (review["par_meeting_id"],)
            ).fetchall()
        manager_attending = any(attendee["attendee_role"] == "Manager" for attendee in attendees)
        outcome = None
        if review["par_meeting_id"]:
            outcome = connection.execute(
                """SELECT par_meeting_outcomes.*, users.full_name AS recorded_by_name
                   FROM par_meeting_outcomes
                   JOIN users ON users.id = par_meeting_outcomes.recorded_by
                   WHERE par_meeting_outcomes.par_meeting_id = ?""",
                (review["par_meeting_id"],)
            ).fetchone()
        return render_template(
            "par_meeting.html", review=review, attendees=attendees, outcome=outcome,
            manager_attending=manager_attending,
            can_schedule=(session["user_role"] == "Supervisor" and review["supervisor_id"] == session["user_id"] and review['cycle_status'] == 'Active'),
            is_follow_up=review["par_status"] in ("Held", "Cancelled"),
            can_record_outcome=(session["user_role"] == "Supervisor"
                                and review["supervisor_id"] == session["user_id"]
                                and review['cycle_status'] == 'Active'
                                and review["par_status"] == "Held"),
            user_name=session["user_name"], user_role=session["user_role"]
        )
    finally:
        connection.close()


@app.route("/reviews/<int:employee_review_id>/par-meeting/availability")
def par_meeting_availability(employee_review_id):
    if "user_id" not in session or session["user_role"] != "Supervisor":
        return jsonify({"success": False, "message": "Supervisor access is required."}), 403
    meeting_date = request.args.get("date", "")
    duration_raw = request.args.get("duration", "60")
    manager_attends = request.args.get("manager_attends") == "true"
    try:
        date_value = datetime.strptime(meeting_date, "%Y-%m-%d").date()
        duration = int(duration_raw)
        if duration not in (30, 45, 60, 90):
            raise ValueError
    except ValueError:
        return jsonify({"success": False, "message": "Choose a valid weekday and meeting duration."}), 400
    if date_value.weekday() >= 5:
        return jsonify({"success": True, "slots": [], "message": "PAR meetings are scheduled on weekdays, 9:00 AM–5:00 PM."})
    connection = get_db_connection()
    try:
        review = get_par_meeting_context(connection, employee_review_id)
        if (review is None or review["supervisor_id"] != session["user_id"]
                or review["cycle_status"] != "Active"
                or review["review_status"] not in ("Approved", "Completed")):
            return jsonify({"success": False, "message": "PAR meeting not found."}), 404
        attendee_ids = [user_id for user_id, _ in par_attendees(review, manager_attends)]
        slots = []
        current = datetime.combine(date_value, datetime.min.time()).replace(hour=9)
        closing = current.replace(hour=17)
        while current + timedelta(minutes=duration) <= closing:
            end = current + timedelta(minutes=duration)
            if current > par_now() and valid_par_slot(connection, attendee_ids, meeting_date, current.strftime("%H:%M"), end.strftime("%H:%M"), review["par_meeting_id"]):
                slots.append({"value": current.strftime("%H:%M"), "label": current.strftime("%I:%M %p").lstrip("0")})
            current += timedelta(minutes=30)
        return jsonify({"success": True, "slots": slots, "message": "" if slots else "No shared availability was found for this date."})
    finally:
        connection.close()


@app.route("/reviews/<int:employee_review_id>/par-meeting/schedule", methods=["POST"])
def schedule_par_meeting(employee_review_id):
    if "user_id" not in session or session["user_role"] != "Supervisor":
        flash("Only the assigned supervisor can arrange this PAR meeting.", "error")
        return redirect(url_for("dashboard"))
    meeting_date = request.form.get("meeting_date", "").strip()
    start_time = request.form.get("start_time", "").strip()
    duration_raw = request.form.get("duration", "60").strip()
    meeting_format = request.form.get("meeting_format", "").strip()
    location = request.form.get("location", "").strip()
    agenda = request.form.get("agenda", "").strip()
    manager_attends = request.form.get("manager_attends") == "on"
    try:
        start = datetime.strptime(f"{meeting_date} {start_time}", "%Y-%m-%d %H:%M")
        duration = int(duration_raw)
        if duration not in (30, 45, 60, 90) or start.weekday() >= 5 or start.hour < 9 or start <= par_now():
            raise ValueError
        end = start + timedelta(minutes=duration)
        if end.hour > 17 or (end.hour == 17 and end.minute > 0):
            raise ValueError
    except ValueError:
        flash("Choose a future weekday time between 9:00 AM and 5:00 PM and a valid duration.", "error")
        return redirect(url_for("par_meeting_workspace", employee_review_id=employee_review_id))
    if meeting_format not in ("In person", "Online", "Hybrid") or not location or len(location) > 300 or len(agenda) > 3000:
        flash("Provide a meeting format, location or link, and a concise agenda.", "error")
        return redirect(url_for("par_meeting_workspace", employee_review_id=employee_review_id))
    connection = get_db_connection()
    try:
        ensure_par_meeting_schema(connection)
        # Reserve the write lock before checking availability, so simultaneous
        # requests cannot both book the same attendee into overlapping meetings.
        connection.execute('BEGIN IMMEDIATE')
        review = get_par_meeting_context(connection, employee_review_id, initialize_schema=False)
        if review is None or review["supervisor_id"] != session["user_id"]:
            flash("PAR meeting not found.", "error")
            return redirect(url_for("dashboard"))
        if review["cycle_status"] != "Active" or review["review_status"] not in ("Approved", "Completed"):
            flash("This review is not ready for a PAR meeting.", "error")
            return redirect(url_for("dashboard"))
        if review["par_status"] == "Held":
            if not connection.execute('SELECT 1 FROM par_meeting_outcomes WHERE par_meeting_id=?',
                                      (review['par_meeting_id'],)).fetchone():
                flash('Record the current meeting outcome before arranging a follow-up.', 'error')
                return redirect(url_for('par_meeting_workspace', employee_review_id=employee_review_id))
            # A held meeting is a permanent record; a later meeting is a new follow-up.
            review = dict(review)
            review["par_meeting_id"] = None
        attendees = par_attendees(review, manager_attends)
        if not valid_par_slot(connection, [user_id for user_id, _ in attendees], meeting_date, start_time, end.strftime("%H:%M"), review["par_meeting_id"]):
            flash("That time is no longer available for every attendee. Check availability again.", "error")
            return redirect(url_for("par_meeting_workspace", employee_review_id=employee_review_id))
        previous_attendee_ids = set()
        if review["par_meeting_id"] and review["par_status"] in ("Scheduled", "Rescheduled"):
            previous_attendee_ids = {
                row["user_id"] for row in connection.execute(
                    "SELECT user_id FROM par_meeting_attendees WHERE par_meeting_id = ?",
                    (review["par_meeting_id"],)
                ).fetchall()
            }
            connection.execute("""UPDATE par_meetings SET meeting_date=?, start_time=?, end_time=?, meeting_format=?, location=?, agenda=?, status='Rescheduled', scheduled_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (meeting_date, start_time, end.strftime("%H:%M"), meeting_format, location, agenda, session["user_id"], review["par_meeting_id"]))
            meeting_id = review["par_meeting_id"]
            connection.execute("DELETE FROM par_meeting_attendees WHERE par_meeting_id = ?", (meeting_id,))
            action_word = "rescheduled"
        else:
            meeting_id = connection.execute("""INSERT INTO par_meetings (employee_review_id, scheduled_by, meeting_date, start_time, end_time, meeting_format, location, agenda) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (employee_review_id, session["user_id"], meeting_date, start_time, end.strftime("%H:%M"), meeting_format, location, agenda)).lastrowid
            action_word = "scheduled"
        connection.executemany("INSERT INTO par_meeting_attendees (par_meeting_id, user_id, attendee_role) VALUES (?, ?, ?)",
            [(meeting_id, user_id, role) for user_id, role in attendees])
        connection.execute("""INSERT INTO review_actions (review_cycle_id, employee_review_id, assigned_to, action_type, title, description, status, priority)
            VALUES (?, ?, ?, 'PAR_MEETING', 'Hold PAR meeting', 'Lead the scheduled Performance Appraisal Review meeting and record its outcome.', 'Pending', 'High')
            ON CONFLICT(review_cycle_id, employee_review_id, assigned_to, action_type) DO UPDATE SET title=excluded.title, description=excluded.description, status='Pending', completed_at=NULL""",
            (review["review_cycle_id"], employee_review_id, review["supervisor_id"]))
        for user_id, role in attendees:
            if user_id != session["user_id"]:
                connection.execute("""INSERT INTO notifications (user_id, review_cycle_id, employee_review_id, notification_type, title, message)
                    VALUES (?, ?, ?, 'PAR_MEETING_SCHEDULED', 'PAR meeting ' || ?, ?)""",
                    (user_id, review["review_cycle_id"], employee_review_id, action_word.title(),
                     f"Your PAR meeting is {action_word} for {meeting_date} at {start.strftime('%I:%M %p')}."))
        for user_id in previous_attendee_ids - {user_id for user_id, _ in attendees}:
            connection.execute("""INSERT INTO notifications (user_id, review_cycle_id, employee_review_id, notification_type, title, message)
                VALUES (?, ?, ?, 'PAR_MEETING_ATTENDANCE_UPDATED', 'PAR meeting attendance updated', ?)""",
                (user_id, review["review_cycle_id"], employee_review_id,
                 "You are no longer required to attend this PAR meeting."))
        connection.commit()
        flash(f"PAR meeting {action_word}. All selected attendees were notified.", "success")
    except sqlite3.Error as error:
        connection.rollback()
        print("PAR schedule error:", error)
        flash("The PAR meeting could not be saved.", "error")
    finally:
        connection.close()
    return redirect(url_for("par_meeting_workspace", employee_review_id=employee_review_id))


@app.route("/reviews/<int:employee_review_id>/par-meeting/held", methods=["POST"])
def mark_par_meeting_held(employee_review_id):
    if "user_id" not in session or session["user_role"] != "Supervisor":
        flash("Only the assigned supervisor can update this meeting.", "error")
        return redirect(url_for("dashboard"))
    connection = get_db_connection()
    try:
        review = get_par_meeting_context(connection, employee_review_id)
        if review is None or review["supervisor_id"] != session["user_id"] or review['cycle_status'] != 'Active' or review["par_status"] not in ("Scheduled", "Rescheduled"):
            flash("This meeting cannot be marked as held.", "error")
            return redirect(url_for("dashboard"))
        if datetime.strptime(f"{review['meeting_date']} {review['start_time']}", '%Y-%m-%d %H:%M') > par_now():
            flash('This meeting has not started yet. Mark it as held after the conversation.', 'error')
            return redirect(url_for('par_meeting_workspace', employee_review_id=employee_review_id))
        connection.execute("UPDATE par_meetings SET status='Held', held_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?", (review["par_meeting_id"],))
        connection.execute("UPDATE review_actions SET status='Completed', completed_at=CURRENT_TIMESTAMP WHERE employee_review_id=? AND assigned_to=? AND action_type='PAR_MEETING'", (employee_review_id, session["user_id"]))
        connection.execute(
            """INSERT INTO review_actions
                (review_cycle_id, employee_review_id, assigned_to, action_type,
                 title, description, status, priority)
                VALUES (?, ?, ?, 'PAR_OUTCOME', 'Record PAR meeting outcome',
                        'Document the agreed discussion points and next development actions.',
                        'Pending', 'High')
                ON CONFLICT(review_cycle_id, employee_review_id, assigned_to, action_type)
                DO UPDATE SET status='Pending', completed_at=NULL, priority='High'""",
            (review["review_cycle_id"], employee_review_id, review["supervisor_id"])
        )
        connection.commit()
        flash("PAR meeting marked as held. You can now record the outcome.", "success")
    finally:
        connection.close()
    return redirect(url_for("par_meeting_workspace", employee_review_id=employee_review_id))


@app.route("/reviews/<int:employee_review_id>/par-meeting/outcome", methods=["POST"])
def record_par_meeting_outcome(employee_review_id):
    if "user_id" not in session or session["user_role"] != "Supervisor":
        flash("Only the assigned supervisor can record this PAR outcome.", "error")
        return redirect(url_for("dashboard"))

    fields = {
        "discussion_summary": request.form.get("discussion_summary", "").strip(),
        "confirmed_strengths": request.form.get("confirmed_strengths", "").strip(),
        "development_priorities": request.form.get("development_priorities", "").strip(),
        "employee_comments": request.form.get("employee_comments", "").strip(),
        "agreed_actions": request.form.get("agreed_actions", "").strip(),
        "outcome": request.form.get("outcome", "").strip(),
    }
    if (not fields["discussion_summary"] or not fields["agreed_actions"]
            or fields["outcome"] not in ("PDP Required", "No PDP Required")
            or any(len(value) > 3000 for value in fields.values())):
        flash("Add the discussion summary, agreed actions and a valid meeting outcome.", "error")
        return redirect(url_for("par_meeting_workspace", employee_review_id=employee_review_id))

    connection = get_db_connection()
    try:
        review = get_par_meeting_context(connection, employee_review_id)
        if (review is None or review["supervisor_id"] != session["user_id"]
                or review['cycle_status'] != 'Active' or review["par_status"] != "Held"):
            flash("The PAR meeting must be marked as held before recording its outcome.", "error")
            return redirect(url_for("dashboard"))

        connection.execute(
            """INSERT INTO par_meeting_outcomes
                (par_meeting_id, recorded_by, discussion_summary, confirmed_strengths,
                 development_priorities, employee_comments, agreed_actions, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(par_meeting_id) DO UPDATE SET
                    recorded_by=excluded.recorded_by,
                    discussion_summary=excluded.discussion_summary,
                    confirmed_strengths=excluded.confirmed_strengths,
                    development_priorities=excluded.development_priorities,
                    employee_comments=excluded.employee_comments,
                    agreed_actions=excluded.agreed_actions,
                    outcome=excluded.outcome,
                    updated_at=CURRENT_TIMESTAMP""",
            (review["par_meeting_id"], session["user_id"], fields["discussion_summary"],
             fields["confirmed_strengths"], fields["development_priorities"],
             fields["employee_comments"], fields["agreed_actions"], fields["outcome"])
        )
        connection.execute(
            """UPDATE review_actions SET status='Completed', completed_at=CURRENT_TIMESTAMP
                WHERE employee_review_id=? AND assigned_to=? AND action_type='PAR_OUTCOME'
                AND status != 'Completed'""",
            (employee_review_id, session["user_id"])
        )
        if fields["outcome"] == "PDP Required":
            connection.execute(
                """INSERT INTO review_actions
                    (review_cycle_id, employee_review_id, assigned_to, action_type,
                     title, description, status, priority)
                    VALUES (?, ?, ?, 'PDP_CREATION', 'Create employee PDP',
                            'Create a development plan from the agreed PAR actions.', 'Pending', 'High')
                    ON CONFLICT(review_cycle_id, employee_review_id, assigned_to, action_type)
                    DO UPDATE SET status='Pending', completed_at=NULL, priority='High'""",
                (review["review_cycle_id"], employee_review_id, review["supervisor_id"])
            )
        else:
            connection.execute(
                """UPDATE review_actions SET status='Completed', completed_at=CURRENT_TIMESTAMP
                    WHERE employee_review_id=? AND assigned_to=? AND action_type='PDP_CREATION'""",
                (employee_review_id, session["user_id"]),
            )

        attendee_ids = connection.execute(
            "SELECT user_id FROM par_meeting_attendees WHERE par_meeting_id = ?",
            (review["par_meeting_id"],)
        ).fetchall()
        for attendee in attendee_ids:
            if attendee["user_id"] != session["user_id"]:
                connection.execute(
                    """INSERT INTO notifications
                        (user_id, review_cycle_id, employee_review_id, notification_type, title, message)
                        VALUES (?, ?, ?, 'PAR_OUTCOME_RECORDED', 'PAR meeting outcome recorded', ?)""",
                    (attendee["user_id"], review["review_cycle_id"], employee_review_id,
                     f"The PAR meeting outcome for {review['employee_name_snapshot']} is now available.")
                )
        connection.commit()
        flash("PAR meeting outcome recorded and shared with the meeting attendees.", "success")
    except sqlite3.Error as error:
        connection.rollback()
        print("PAR outcome error:", error)
        flash("The PAR meeting outcome could not be saved.", "error")
    finally:
        connection.close()

    return redirect(url_for("par_meeting_workspace", employee_review_id=employee_review_id))


def get_pdp_context(connection, employee_review_id, initialize_schema=True):
    if initialize_schema:
        ensure_pdp_schema(connection)
    return connection.execute(
        """
        SELECT employee_reviews.id AS employee_review_id,
               employee_reviews.review_cycle_id,
               employee_reviews.supervisor_id,
               employee_reviews.employee_name_snapshot,
               employee_reviews.employee_code_snapshot,
               employee_reviews.department_snapshot,
               employees.user_id AS employee_user_id,
               review_cycles.cycle_name,
               review_cycles.status AS cycle_status,
               pdp_plans.id AS pdp_plan_id,
               pdp_plans.title,
               pdp_plans.focus_area,
               pdp_plans.overall_goal,
               pdp_plans.success_measure,
               pdp_plans.target_date,
               pdp_plans.status AS pdp_status
        FROM employee_reviews
        JOIN employees ON employees.id = employee_reviews.employee_id
        JOIN review_cycles ON review_cycles.id = employee_reviews.review_cycle_id
        LEFT JOIN pdp_plans
            ON pdp_plans.employee_review_id = employee_reviews.id
        WHERE employee_reviews.id = ?
          AND (
              pdp_plans.id IS NOT NULL
              OR EXISTS (
                  SELECT 1
                  FROM par_meetings
                  JOIN par_meeting_outcomes
                      ON par_meeting_outcomes.par_meeting_id = par_meetings.id
                  WHERE par_meetings.employee_review_id = employee_reviews.id
                    AND par_meeting_outcomes.outcome = 'PDP Required'
              )
          )
        """,
        (employee_review_id,),
    ).fetchone()


@app.route("/reviews/<int:employee_review_id>/pdp", methods=["GET", "POST"])
def pdp_workspace(employee_review_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_db_connection()
    try:
        ensure_pdp_schema(connection)
        if request.method == 'POST':
            connection.execute('BEGIN IMMEDIATE')
        review = get_pdp_context(connection, employee_review_id, initialize_schema=False)
        can_manage = (
            review is not None
            and session["user_role"] == "Supervisor"
            and review["supervisor_id"] == session["user_id"]
        )
        can_update_progress = (
            review is not None
            and review["pdp_plan_id"] is not None
            and session["user_role"] == "Employee"
            and review["employee_user_id"] == session["user_id"]
        )
        can_monitor = review is not None and session["user_role"] == "HR"
        if not can_manage and not can_update_progress and not can_monitor:
            flash("This Personal Development Plan is not available to your account.", "error")
            return redirect(url_for("dashboard"))

        if request.method == 'POST' and not can_manage:
            return jsonify(success=False, message='Only the assigned supervisor can edit this plan.'), 403

        if request.method == "POST" and can_manage:
            plan_fields = {
                "title": request.form.get("title", "").strip(),
                "focus_area": request.form.get("focus_area", "").strip(),
                "overall_goal": request.form.get("overall_goal", "").strip(),
                "success_measure": request.form.get("success_measure", "").strip(),
                "target_date": request.form.get("target_date", "").strip(),
            }
            activities = []
            raw_activities = request.form.getlist("activity")
            raw_support = request.form.getlist("support_needed")
            raw_dates = request.form.getlist("activity_target_date")
            raw_ids = request.form.getlist('activity_id')
            existing_ids = {
                str(row['id']) for row in connection.execute(
                    'SELECT id FROM pdp_activities WHERE pdp_plan_id=?', (review['pdp_plan_id'],)
                ).fetchall()
            }
            posted_ids = [value for value in raw_ids if value]
            if (set(posted_ids) != existing_ids or len(posted_ids) != len(set(posted_ids))):
                flash('The plan activities changed. Reload the plan before editing it.', 'error')
                return redirect(url_for('pdp_workspace', employee_review_id=employee_review_id))
            for index, activity in enumerate(raw_activities):
                activity = activity.strip()
                support = raw_support[index].strip() if index < len(raw_support) else ""
                target_date = raw_dates[index].strip() if index < len(raw_dates) else ""
                activity_id = raw_ids[index] if index < len(raw_ids) else ''
                if activity or support or target_date or activity_id:
                    activities.append((activity_id, activity, support, target_date))

            if (not all(plan_fields.values())
                    or any(len(value) > 3000 for value in plan_fields.values())
                    or len(plan_fields['title']) > 180 or len(plan_fields['focus_area']) > 300
                    or not activities
                    or any(not activity or not target_date or len(activity) > 1000 or len(support) > 1000
                           for _, activity, support, target_date in activities)):
                flash("Complete the PDP summary and add at least one development activity with a target date.", "error")
                return redirect(url_for("pdp_workspace", employee_review_id=employee_review_id))

            try:
                plan_date = datetime.strptime(plan_fields['target_date'], '%Y-%m-%d').date()
                for _, _, _, target_date in activities:
                    if datetime.strptime(target_date, '%Y-%m-%d').date() > plan_date:
                        raise ValueError
            except ValueError:
                flash('Use valid target dates. Each activity must be due on or before the plan target date.', 'error')
                return redirect(url_for('pdp_workspace', employee_review_id=employee_review_id))

            connection.execute(
                """
                INSERT INTO pdp_plans
                    (employee_review_id, created_by, title, focus_area, overall_goal,
                     success_measure, target_date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'Active')
                ON CONFLICT(employee_review_id) DO UPDATE SET
                    title=excluded.title,
                    focus_area=excluded.focus_area,
                    overall_goal=excluded.overall_goal,
                    success_measure=excluded.success_measure,
                    target_date=excluded.target_date,
                    status='Active',
                    updated_at=CURRENT_TIMESTAMP
                """,
                (employee_review_id, session["user_id"], plan_fields["title"],
                 plan_fields["focus_area"], plan_fields["overall_goal"],
                 plan_fields["success_measure"], plan_fields["target_date"]),
            )
            plan = connection.execute(
                "SELECT id FROM pdp_plans WHERE employee_review_id = ?",
                (employee_review_id,),
            ).fetchone()
            # Stable IDs preserve employee notes, progress and reminder history.
            for position, (activity_id, activity, support, target_date) in enumerate(activities, start=1):
                if activity_id:
                    connection.execute(
                        """UPDATE pdp_activities SET activity=?, support_needed=?, target_date=?,
                           sort_order=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND pdp_plan_id=?""",
                        (activity, support, target_date, position, activity_id, plan['id']),
                    )
                else:
                    connection.execute(
                        """INSERT INTO pdp_activities (pdp_plan_id, activity, support_needed, target_date, sort_order)
                           VALUES (?, ?, ?, ?, ?)""", (plan['id'], activity, support, target_date, position),
                    )
            connection.execute(
                """
                UPDATE review_actions
                SET status='Completed', completed_at=CURRENT_TIMESTAMP
                WHERE employee_review_id=? AND assigned_to=? AND action_type='PDP_CREATION'
                """,
                (employee_review_id, session["user_id"]),
            )
            connection.execute(
                """
                INSERT INTO notifications
                    (user_id, review_cycle_id, employee_review_id, notification_type, title, message)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (review["employee_user_id"], review["review_cycle_id"], employee_review_id,
                 'PDP_UPDATED' if review['pdp_plan_id'] else 'PDP_CREATED',
                 'Your development plan was updated' if review['pdp_plan_id'] else 'Your development plan is ready',
                 'Your supervisor has saved your development plan. Open it to review the activities.'),
            )
            connection.execute(
                """
                INSERT INTO review_actions
                    (review_cycle_id, employee_review_id, assigned_to, action_type,
                     title, description, status, priority)
                VALUES (?, ?, ?, 'PDP_PROGRESS', 'Update PDP progress',
                        'Review your development activities and share your latest progress.',
                        'Pending', 'Normal')
                ON CONFLICT(review_cycle_id, employee_review_id, assigned_to, action_type)
                DO UPDATE SET status='Pending', completed_at=NULL, priority='Normal'
                """,
                (review["review_cycle_id"], employee_review_id, review["employee_user_id"]),
            )
            sync_pdp_progress_actions(connection, initialize_schema=False)
            connection.commit()
            flash("Personal Development Plan saved and shared with the employee.", "success")
            return redirect(url_for("pdp_workspace", employee_review_id=employee_review_id))

        activities = []
        if review["pdp_plan_id"]:
            activities = connection.execute(
                """SELECT * FROM pdp_activities WHERE pdp_plan_id = ?
                   ORDER BY sort_order, id""",
                (review["pdp_plan_id"],),
            ).fetchall()
        return render_template(
            "pdp_workspace.html", review=review, activities=activities,
            can_manage=can_manage, can_update_progress=can_update_progress,
            can_monitor=can_monitor,
            user_name=session["user_name"], user_role=session["user_role"],
        )
    except sqlite3.Error:
        connection.rollback()
        app.logger.exception('PDP save failed')
        flash('The plan could not be saved. Please reload and try again.', 'error')
        return redirect(url_for('pdp_workspace', employee_review_id=employee_review_id))
    finally:
        connection.close()


@app.route("/reviews/<int:employee_review_id>/pdp/progress", methods=["POST"])
def update_pdp_progress(employee_review_id):
    if "user_id" not in session or session["user_role"] != "Employee":
        flash("Only the employee can update their PDP progress.", "error")
        return redirect(url_for("dashboard"))

    connection = get_db_connection()
    try:
        ensure_pdp_schema(connection)
        connection.execute('BEGIN IMMEDIATE')
        review = get_pdp_context(connection, employee_review_id, initialize_schema=False)
        if (review is None or review["pdp_plan_id"] is None
                or review["employee_user_id"] != session["user_id"]):
            flash("This Personal Development Plan is not available to your account.", "error")
            return redirect(url_for("dashboard"))

        activity_ids = request.form.getlist("activity_id")
        statuses = request.form.getlist("activity_status")
        notes = request.form.getlist("employee_progress_note")
        allowed_statuses = {"Not Started", "In Progress", "Completed"}
        if (not activity_ids or len(activity_ids) != len(statuses)
                or len(activity_ids) != len(notes)
                or any(status not in allowed_statuses for status in statuses)
                or any(len(note.strip()) > 3000 for note in notes)):
            flash("Check the progress updates and try again.", "error")
            return redirect(url_for("pdp_workspace", employee_review_id=employee_review_id))

        permitted_ids = {
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM pdp_activities WHERE pdp_plan_id = ?",
                (review["pdp_plan_id"],),
            ).fetchall()
        }
        if set(activity_ids) != permitted_ids or len(activity_ids) != len(permitted_ids):
            flash("One or more PDP activities could not be verified.", "error")
            return redirect(url_for("pdp_workspace", employee_review_id=employee_review_id))

        connection.executemany(
            """
            UPDATE pdp_activities
            SET status=?, employee_progress_note=?, employee_updated_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND pdp_plan_id=?
            """,
            [(status, note.strip(), activity_id, review["pdp_plan_id"])
             for activity_id, status, note in zip(activity_ids, statuses, notes)],
        )
        all_complete = all(status == "Completed" for status in statuses)
        connection.execute(
            """
            UPDATE review_actions
            SET status=?, completed_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END
            WHERE employee_review_id=? AND assigned_to=? AND action_type='PDP_PROGRESS'
            """,
            ("Completed" if all_complete else "Pending", int(all_complete),
             employee_review_id, session["user_id"]),
        )
        connection.execute(
            """
            INSERT INTO notifications
                (user_id, review_cycle_id, employee_review_id, notification_type, title, message)
            VALUES (?, ?, ?, 'PDP_PROGRESS_UPDATED', 'PDP progress updated', ?)
            """,
            (review["supervisor_id"], review["review_cycle_id"], employee_review_id,
             f"{review['employee_name_snapshot']} has updated their development plan progress."),
        )
        connection.commit()
        flash("Your PDP progress has been saved and shared with your supervisor.", "success")
    except sqlite3.Error as error:
        connection.rollback()
        print("PDP progress error:", error)
        flash("Your PDP progress could not be saved.", "error")
    finally:
        connection.close()
    return redirect(url_for("pdp_workspace", employee_review_id=employee_review_id))


@app.route("/development-pulse")
def development_pulse():
    if "user_id" not in session or session["user_role"] not in ("Supervisor", "HR", "Employee"):
        flash("Development Pulse is available to employees, supervisors and HR.", "error")
        return redirect(url_for("dashboard"))

    connection = get_db_connection()
    try:
        ensure_pdp_schema(connection)
        scope_condition = ""
        parameters = []
        if session["user_role"] == "Supervisor":
            scope_condition = "AND employee_reviews.supervisor_id = ?"
            parameters.append(session["user_id"])
        elif session['user_role'] == 'Employee':
            scope_condition = 'AND employees.user_id = ?'
            parameters.append(session['user_id'])
        plans = connection.execute(
            f"""
            SELECT pdp_plans.id AS pdp_plan_id,
                   employee_reviews.id AS employee_review_id,
                   employee_reviews.employee_name_snapshot,
                   employee_reviews.employee_code_snapshot,
                   employee_reviews.department_snapshot,
                   users.full_name AS supervisor_name,
                   pdp_plans.title, pdp_plans.focus_area, pdp_plans.target_date,
                   COUNT(pdp_activities.id) AS activity_total,
                   SUM(CASE WHEN pdp_activities.status = 'Completed' THEN 1 ELSE 0 END) AS completed_total,
                   SUM(CASE WHEN pdp_activities.status = 'In Progress' THEN 1 ELSE 0 END) AS in_progress_total,
                   SUM(CASE WHEN pdp_activities.status != 'Completed'
                             AND date(pdp_activities.target_date) < date('now') THEN 1 ELSE 0 END) AS overdue_total,
                   MAX(pdp_activities.employee_updated_at) AS last_employee_update
            FROM pdp_plans
            JOIN employee_reviews ON employee_reviews.id = pdp_plans.employee_review_id
            JOIN employees ON employees.id = employee_reviews.employee_id
            JOIN users ON users.id = employee_reviews.supervisor_id
            JOIN review_cycles ON review_cycles.id = employee_reviews.review_cycle_id
            LEFT JOIN pdp_activities ON pdp_activities.pdp_plan_id = pdp_plans.id
            WHERE pdp_plans.status = 'Active'
              AND review_cycles.status IN ('Active', 'Closed')
              {scope_condition}
            GROUP BY pdp_plans.id
            ORDER BY overdue_total DESC, last_employee_update DESC, pdp_plans.target_date ASC
            """,
            parameters,
        ).fetchall()
        plan_cards = []
        for plan in plans:
            card = dict(plan)
            card["activity_total"] = card["activity_total"] or 0
            card["completed_total"] = card["completed_total"] or 0
            card["in_progress_total"] = card["in_progress_total"] or 0
            card["overdue_total"] = card["overdue_total"] or 0
            card["progress_percent"] = round(
                (card["completed_total"] / card["activity_total"] * 100)
                if card["activity_total"] else 0
            )
            card["needs_attention"] = card["overdue_total"] > 0 or (
                card["activity_total"] > 0 and card["in_progress_total"] == 0
                and card["completed_total"] == 0
            )
            plan_cards.append(card)

        view = request.args.get("view", "all")
        if view == "attention":
            visible_plans = [card for card in plan_cards if card["needs_attention"]]
        elif view == "moving":
            visible_plans = [card for card in plan_cards if card["in_progress_total"] > 0]
        else:
            view = "all"
            visible_plans = plan_cards

        return render_template(
            "development_pulse.html", plans=visible_plans, view=view,
            total_plans=len(plan_cards),
            moving_plans=sum(card["in_progress_total"] > 0 for card in plan_cards),
            attention_plans=sum(card["needs_attention"] for card in plan_cards),
            user_name=session["user_name"], user_role=session["user_role"],
        )
    finally:
        connection.close()


@app.route("/reviews/<int:employee_review_id>/par-meeting/cancel", methods=["POST"])
def cancel_par_meeting(employee_review_id):
    if "user_id" not in session or session["user_role"] != "Supervisor":
        flash("Only the assigned supervisor can cancel this meeting.", "error")
        return redirect(url_for("dashboard"))
    connection = get_db_connection()
    try:
        review = get_par_meeting_context(connection, employee_review_id)
        if (review is None or review["supervisor_id"] != session["user_id"]
                or review['cycle_status'] != 'Active' or review["par_status"] not in ("Scheduled", "Rescheduled")):
            flash("This meeting cannot be cancelled.", "error")
            return redirect(url_for("dashboard"))
        attendee_ids = connection.execute(
            "SELECT user_id FROM par_meeting_attendees WHERE par_meeting_id = ?",
            (review["par_meeting_id"],)
        ).fetchall()
        connection.execute("UPDATE par_meetings SET status='Cancelled', updated_at=CURRENT_TIMESTAMP WHERE id=?", (review["par_meeting_id"],))
        for attendee in attendee_ids:
            if attendee["user_id"] != session["user_id"]:
                connection.execute("""INSERT INTO notifications (user_id, review_cycle_id, employee_review_id, notification_type, title, message)
                    VALUES (?, ?, ?, 'PAR_MEETING_CANCELLED', 'PAR meeting cancelled', ?)""",
                    (attendee["user_id"], review["review_cycle_id"], employee_review_id,
                     "The scheduled PAR meeting was cancelled. The supervisor will arrange a new time."))
        connection.commit()
        flash("PAR meeting cancelled. You can arrange a new time when ready.", "success")
    finally:
        connection.close()
    return redirect(url_for("par_meeting_workspace", employee_review_id=employee_review_id))


@app.route("/availability", methods=["GET", "POST"])
def availability():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session["user_role"] not in ("Employee", "Supervisor", "Manager"):
        flash("Availability is available to meeting participants only.", "error")
        return redirect(url_for("dashboard"))
    connection = get_db_connection()
    try:
        ensure_par_meeting_schema(connection)
        if request.method == "POST":
            start_at = request.form.get("start_at", "").strip()
            end_at = request.form.get("end_at", "").strip()
            reason = request.form.get("reason", "").strip()
            try:
                start = datetime.fromisoformat(start_at)
                end = datetime.fromisoformat(end_at)
                if start.tzinfo is not None or end.tzinfo is not None:
                    raise ValueError
                if end <= start or len(reason) > 300:
                    raise ValueError
            except ValueError:
                flash("Enter a valid unavailable period and optional reason.", "error")
            else:
                connection.execute("INSERT INTO user_unavailability (user_id, start_at, end_at, reason) VALUES (?, ?, ?, ?)", (session["user_id"], start.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M"), reason))
                connection.commit()
                flash("Your unavailable time was saved.", "success")
            return redirect(url_for("availability"))
        periods = connection.execute("SELECT id, start_at, end_at, reason FROM user_unavailability WHERE user_id=? AND end_at >= ? ORDER BY start_at", (session["user_id"], datetime.now().strftime("%Y-%m-%dT%H:%M"))).fetchall()
        return render_template("availability.html", periods=periods, user_name=session["user_name"], user_role=session["user_role"])
    finally:
        connection.close()


@app.route("/availability/<int:period_id>/delete", methods=["POST"])
def delete_availability(period_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session["user_role"] not in ("Employee", "Supervisor", "Manager"):
        return redirect(url_for("dashboard"))
    connection = get_db_connection()
    try:
        ensure_par_meeting_schema(connection)
        connection.execute("DELETE FROM user_unavailability WHERE id=? AND user_id=?", (period_id, session["user_id"]))
        connection.commit()
        flash("Unavailable time removed.", "success")
    finally:
        connection.close()
    return redirect(url_for("availability"))


# =========================================================
# FINAL REVIEW OUTCOME + ACKNOWLEDGEMENT
# =========================================================

def get_final_review_outcome_context(connection, employee_review_id):

    return connection.execute(
        """
        SELECT
            employee_reviews.id AS employee_review_id,
            employee_reviews.employee_id,
            employee_reviews.review_cycle_id,
            employee_reviews.supervisor_id,
            employee_reviews.employee_name_snapshot,
            employee_reviews.employee_code_snapshot,
            employee_reviews.department_snapshot,
            employee_reviews.job_title_snapshot,
            employee_reviews.status AS employee_review_status,
            employees.user_id AS employee_user_id,
            review_cycles.cycle_name,
            review_cycles.start_date,
            review_cycles.end_date,
            review_cycles.status AS cycle_status,
            supervisor_users.full_name AS supervisor_name,
            supervisor_evaluations.id AS supervisor_evaluation_id,
            supervisor_evaluations.overall_rating,
            supervisor_evaluations.performance_summary,
            supervisor_evaluations.key_strengths,
            supervisor_evaluations.development_priorities,
            supervisor_evaluations.support_plan,
            supervisor_evaluations.recommendation,
            manager_approvals.manager_id,
            manager_approvals.decision_note AS manager_decision_note,
            manager_approvals.decided_at AS manager_decided_at,
            manager_users.full_name AS manager_name,
            final_review_acknowledgements.id AS acknowledgement_id,
            final_review_acknowledgements.status
                AS acknowledgement_status,
            final_review_acknowledgements.employee_comment,
            final_review_acknowledgements.acknowledged_at

        FROM employee_reviews

        JOIN employees
            ON employees.id = employee_reviews.employee_id

        JOIN review_cycles
            ON review_cycles.id = employee_reviews.review_cycle_id

        JOIN users AS supervisor_users
            ON supervisor_users.id = employee_reviews.supervisor_id

        JOIN supervisor_evaluations
            ON supervisor_evaluations.employee_review_id
                = employee_reviews.id
            AND supervisor_evaluations.status = 'Submitted'

        JOIN manager_approvals
            ON manager_approvals.employee_review_id
                = employee_reviews.id
            AND manager_approvals.status = 'Approved'

        JOIN users AS manager_users
            ON manager_users.id = manager_approvals.manager_id

        JOIN final_review_acknowledgements
            ON final_review_acknowledgements.employee_review_id
                = employee_reviews.id

        WHERE employee_reviews.id = ?
        """,
        (employee_review_id,)
    ).fetchone()


def can_view_final_review_outcome(review):

    if session["user_role"] == "HR":
        return True

    if session["user_role"] == "Employee":
        return review["employee_user_id"] == session["user_id"]

    if session["user_role"] == "Supervisor":
        return review["supervisor_id"] == session["user_id"]

    if session["user_role"] == "Manager":
        return review["manager_id"] == session["user_id"]

    return False


@app.route(
    "/reviews/<int:employee_review_id>/final-outcome"
)
def final_review_outcome(employee_review_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_db_connection()

    try:
        review = get_final_review_outcome_context(
            connection,
            employee_review_id
        )

        if review is None or not can_view_final_review_outcome(review):
            flash("Final review outcome not found.", "error")
            return redirect(url_for("dashboard"))

        if review["employee_review_status"] not in (
            "Approved",
            "Completed"
        ):
            flash("This review outcome is not yet available.", "error")
            return redirect(url_for("dashboard"))

        baseline_items = connection.execute(
            """
            SELECT
                review_plan_items.id AS review_plan_item_id,
                review_plan_items.item_type,
                review_plan_items.title,
                review_plan_items.description,
                review_plan_items.target,
                review_plan_items.due_date,
                self_assessment_items.rating AS self_rating,
                supervisor_evaluation_items.rating
                    AS supervisor_rating,
                supervisor_evaluation_items.evaluation_text,
                (
                    SELECT ROUND(AVG(peer_review_items.rating), 1)
                    FROM peer_review_items
                    JOIN peer_reviews
                        ON peer_reviews.id
                            = peer_review_items.peer_review_id
                    JOIN peer_review_assignments
                        ON peer_review_assignments.id
                            = peer_reviews.peer_assignment_id
                    WHERE peer_review_items.review_plan_item_id
                        = review_plan_items.id
                    AND peer_review_assignments.employee_review_id
                        = review_plan_items.employee_review_id
                    AND peer_reviews.status = 'Submitted'
                    AND peer_review_assignments.status = 'Submitted'
                ) AS peer_average_rating,
                (
                    SELECT COUNT(*)
                    FROM peer_review_items
                    JOIN peer_reviews
                        ON peer_reviews.id
                            = peer_review_items.peer_review_id
                    JOIN peer_review_assignments
                        ON peer_review_assignments.id
                            = peer_reviews.peer_assignment_id
                    WHERE peer_review_items.review_plan_item_id
                        = review_plan_items.id
                    AND peer_review_assignments.employee_review_id
                        = review_plan_items.employee_review_id
                    AND peer_reviews.status = 'Submitted'
                    AND peer_review_assignments.status = 'Submitted'
                ) AS peer_rating_count

            FROM review_plan_items

            LEFT JOIN self_assessments
                ON self_assessments.employee_review_id
                    = review_plan_items.employee_review_id

            LEFT JOIN self_assessment_items
                ON self_assessment_items.self_assessment_id
                    = self_assessments.id
                AND self_assessment_items.review_plan_item_id
                    = review_plan_items.id

            LEFT JOIN supervisor_evaluation_items
                ON supervisor_evaluation_items.supervisor_evaluation_id
                    = ?
                AND supervisor_evaluation_items.review_plan_item_id
                    = review_plan_items.id

            WHERE review_plan_items.employee_review_id = ?
            ORDER BY review_plan_items.id
            """,
            (
                review["supervisor_evaluation_id"],
                employee_review_id
            )
        ).fetchall()

        readonly = (
            session["user_role"] != "Employee"
            or review["acknowledgement_status"] == "Acknowledged"
            or review["employee_review_status"] == "Completed"
        )

        return render_template(
            "final_review_outcome.html",
            review=review,
            baseline_items=baseline_items,
            readonly=readonly,
            user_name=session["user_name"],
            user_role=session["user_role"]
        )

    finally:
        connection.close()


@app.route(
    "/reviews/<int:employee_review_id>/final-outcome/acknowledge",
    methods=["POST"]
)
def acknowledge_final_review_outcome(employee_review_id):

    if "user_id" not in session:
        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401

    if session["user_role"] != "Employee":
        return jsonify({
            "success": False,
            "message": "Only the reviewed employee can acknowledge this outcome."
        }), 403

    data = request.get_json(silent=True)

    if not isinstance(data, dict) or data.get("confirmed") is not True:
        return jsonify({
            "success": False,
            "message": "Please confirm that you received the final outcome."
        }), 400

    employee_comment = data.get("employee_comment", "")

    if not isinstance(employee_comment, str):
        return jsonify({
            "success": False,
            "message": "Invalid employee comment."
        }), 400

    employee_comment = employee_comment.strip()

    if len(employee_comment) > 3000:
        return jsonify({
            "success": False,
            "message": "The final comment must be 3,000 characters or fewer."
        }), 400

    connection = get_db_connection()

    try:
        review = get_final_review_outcome_context(
            connection,
            employee_review_id
        )

        if (
            review is None
            or review["employee_user_id"] != session["user_id"]
        ):
            return jsonify({
                "success": False,
                "message": "Final review outcome not found."
            }), 404

        if (
            review["employee_review_status"] != "Approved"
            or review["acknowledgement_status"] != "Pending"
        ):
            return jsonify({
                "success": False,
                "message": "This review outcome has already been acknowledged."
            }), 409

        acknowledgement = connection.execute(
            """
            UPDATE final_review_acknowledgements
            SET
                status = 'Acknowledged',
                employee_comment = ?,
                acknowledged_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            AND status = 'Pending'
            """,
            (
                employee_comment,
                review["acknowledgement_id"]
            )
        )

        if not acknowledgement.rowcount:
            return jsonify({
                "success": False,
                "message": "This outcome was already acknowledged."
            }), 409

        transition = connection.execute(
            """
            UPDATE employee_reviews
            SET
                status = 'Completed',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            AND status = 'Approved'
            """,
            (employee_review_id,)
        )

        if not transition.rowcount:
            connection.rollback()
            return jsonify({
                "success": False,
                "message": "The review could not be completed."
            }), 409

        connection.execute(
            """
            UPDATE review_actions
            SET
                status = 'Completed',
                completed_at = CURRENT_TIMESTAMP
            WHERE employee_review_id = ?
            AND assigned_to = ?
            AND action_type = 'FINAL_REVIEW_ACKNOWLEDGEMENT'
            AND status != 'Completed'
            """,
            (
                employee_review_id,
                session["user_id"]
            )
        )

        recipients = (
            (
                session["user_id"],
                "FINAL_OUTCOME_ACKNOWLEDGED",
                "Review Outcome Acknowledged",
                (
                    f"Your {review['cycle_name']} review is now complete "
                    "and available as a final record."
                )
            ),
            (
                review["supervisor_id"],
                "FINAL_OUTCOME_ACKNOWLEDGED",
                "Review Outcome Acknowledged",
                (
                    f"{review['employee_name_snapshot']} acknowledged "
                    "the final review outcome."
                )
            ),
            (
                review["manager_id"],
                "FINAL_OUTCOME_ACKNOWLEDGED",
                "Approved Review Completed",
                (
                    f"{review['employee_name_snapshot']} acknowledged "
                    "the approved outcome and completed the review."
                )
            )
        )

        for user_id, notification_type, title, message in recipients:
            connection.execute(
                """
                INSERT INTO notifications
                (
                    user_id,
                    review_cycle_id,
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    review["review_cycle_id"],
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )
            )

        hr_users = connection.execute(
            "SELECT id FROM users WHERE role = 'HR'"
        ).fetchall()

        for hr_user in hr_users:
            connection.execute(
                """
                INSERT INTO notifications
                (
                    user_id,
                    review_cycle_id,
                    employee_review_id,
                    notification_type,
                    title,
                    message
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    hr_user["id"],
                    review["review_cycle_id"],
                    employee_review_id,
                    "FINAL_OUTCOME_ACKNOWLEDGED",
                    "Review Workflow Completed",
                    (
                        f"{review['employee_name_snapshot']}'s review "
                        "has been acknowledged and completed."
                    )
                )
            )

        connection.commit()

        flash(
            "Your final review outcome has been acknowledged.",
            "success"
        )

        return jsonify({
            "success": True,
            "message": "Final review outcome acknowledged.",
            "redirect_url": url_for(
                "final_review_outcome",
                employee_review_id=employee_review_id
            )
        })

    except sqlite3.Error as error:
        connection.rollback()
        print("Final review acknowledgement error:", error)
        return jsonify({
            "success": False,
            "message": "The acknowledgement could not be recorded."
        }), 500

    finally:
        connection.close()


@app.route("/account/password", methods=["GET", "POST"])
def change_password():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        connection = get_db_connection()

        try:
            user = connection.execute(
                "SELECT password FROM users WHERE id = ?",
                (session["user_id"],)
            ).fetchone()

            if user is None or not check_password_hash(
                user["password"],
                current_password
            ):
                flash("Your current password is incorrect.", "error")

            elif len(new_password) < 12:
                flash(
                    "Your new password must contain at least 12 characters.",
                    "error"
                )

            elif new_password != confirm_password:
                flash("The new passwords do not match.", "error")

            elif check_password_hash(user["password"], new_password):
                flash(
                    "Choose a password that is different from your current one.",
                    "error"
                )

            else:
                connection.execute(
                    """
                    UPDATE users
                    SET password = ?
                    WHERE id = ?
                    """,
                    (
                        generate_password_hash(new_password),
                        session["user_id"]
                    )
                )
                connection.commit()
                session.clear()
                flash(
                    "Password updated. Sign in again with your new password.",
                    "success"
                )
                return redirect(url_for("login"))

        finally:
            connection.close()

    return render_template(
        "change_password.html",
        user_name=session["user_name"],
        user_role=session["user_role"]
    )


@app.route("/review-history")
def review_history():

    if "user_id" not in session:
        return redirect(url_for("login"))

    role = session["user_role"]
    user_id = session["user_id"]

    role_filters = {
        "HR": ("1 = 1", ()),
        "Employee": ("employees.user_id = ?", (user_id,)),
        "Supervisor": (
            "employee_reviews.supervisor_id = ?",
            (user_id,)
        ),
        "Manager": ("manager_approvals.manager_id = ?", (user_id,))
    }

    if role not in role_filters:
        return redirect(url_for("dashboard"))

    access_clause, parameters = role_filters[role]
    connection = get_db_connection()

    try:
        reviews = connection.execute(
            f"""
            SELECT
                employee_reviews.id AS employee_review_id,
                employee_reviews.employee_name_snapshot,
                employee_reviews.employee_code_snapshot,
                employee_reviews.department_snapshot,
                employee_reviews.job_title_snapshot,
                employee_reviews.status AS review_status,
                review_cycles.id AS review_cycle_id,
                review_cycles.cycle_name,
                review_cycles.cycle_year,
                review_cycles.status AS cycle_status,
                supervisor_users.full_name AS supervisor_name,
                supervisor_evaluations.overall_rating,
                supervisor_evaluations.recommendation,
                manager_users.full_name AS manager_name,
                manager_approvals.decided_at,
                final_review_acknowledgements.acknowledged_at
            FROM employee_reviews
            JOIN employees
                ON employees.id = employee_reviews.employee_id
            JOIN review_cycles
                ON review_cycles.id = employee_reviews.review_cycle_id
            JOIN users AS supervisor_users
                ON supervisor_users.id = employee_reviews.supervisor_id
            LEFT JOIN supervisor_evaluations
                ON supervisor_evaluations.employee_review_id
                    = employee_reviews.id
                AND supervisor_evaluations.status = 'Submitted'
            LEFT JOIN manager_approvals
                ON manager_approvals.employee_review_id
                    = employee_reviews.id
            LEFT JOIN users AS manager_users
                ON manager_users.id = manager_approvals.manager_id
            LEFT JOIN final_review_acknowledgements
                ON final_review_acknowledgements.employee_review_id
                    = employee_reviews.id
            WHERE employee_reviews.status = 'Completed'
            AND {access_clause}
            ORDER BY
                COALESCE(
                    final_review_acknowledgements.acknowledged_at,
                    employee_reviews.updated_at
                ) DESC,
                review_cycles.cycle_year DESC,
                employee_reviews.employee_name_snapshot
            """,
            parameters
        ).fetchall()

        return render_template(
            "review_history.html",
            reviews=reviews,
            user_name=session["user_name"],
            user_role=role
        )

    finally:
        connection.close()


@app.route("/review-records")
def review_records():
    """HR's read-only, searchable register of every review case."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session["user_role"] != "HR":
        flash("The Records Vault is available to HR only.", "error")
        return redirect(url_for("dashboard"))

    query = request.args.get("q", "").strip()
    selected_status = request.args.get("status", "").strip()
    selected_cycle = request.args.get("cycle", "").strip()
    connection = get_db_connection()
    try:
        cycles = connection.execute(
            "SELECT id, cycle_name, cycle_year, status FROM review_cycles ORDER BY cycle_year DESC, cycle_number DESC"
        ).fetchall()
        conditions, parameters = ["1 = 1"], []
        if query:
            conditions.append("(employee_reviews.employee_name_snapshot LIKE ? OR employee_reviews.employee_code_snapshot LIKE ? OR employee_reviews.department_snapshot LIKE ? OR review_cycles.cycle_name LIKE ?)")
            wildcard = f"%{query}%"
            parameters.extend([wildcard, wildcard, wildcard, wildcard])
        if selected_status:
            conditions.append("employee_reviews.status = ?")
            parameters.append(selected_status)
        if selected_cycle.isdigit():
            conditions.append("employee_reviews.review_cycle_id = ?")
            parameters.append(int(selected_cycle))

        records = connection.execute(
            f"""
            SELECT employee_reviews.id AS employee_review_id, employee_reviews.review_cycle_id,
                   employee_reviews.employee_name_snapshot, employee_reviews.employee_code_snapshot,
                   employee_reviews.department_snapshot, employee_reviews.job_title_snapshot,
                   employee_reviews.status AS review_status, employee_reviews.updated_at,
                   review_cycles.cycle_name, review_cycles.cycle_year, review_cycles.status AS cycle_status,
                   supervisor_users.full_name AS supervisor_name, manager_users.full_name AS manager_name,
                   manager_approvals.status AS manager_status
            FROM employee_reviews
            JOIN review_cycles ON review_cycles.id = employee_reviews.review_cycle_id
            JOIN users AS supervisor_users ON supervisor_users.id = employee_reviews.supervisor_id
            LEFT JOIN manager_approvals ON manager_approvals.employee_review_id = employee_reviews.id
            LEFT JOIN users AS manager_users ON manager_users.id = manager_approvals.manager_id
            WHERE {' AND '.join(conditions)}
            ORDER BY CASE review_cycles.status WHEN 'Active' THEN 0 ELSE 1 END,
                     employee_reviews.updated_at DESC, employee_reviews.employee_name_snapshot
            LIMIT 250
            """, parameters
        ).fetchall()
        totals = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN review_cycles.status = 'Active' THEN 1 ELSE 0 END) AS active,
                   SUM(CASE WHEN employee_reviews.status = 'Completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN employee_reviews.status NOT IN ('Approved', 'Completed') THEN 1 ELSE 0 END) AS in_progress
            FROM employee_reviews JOIN review_cycles ON review_cycles.id = employee_reviews.review_cycle_id
            """
        ).fetchone()
        return render_template("review_records.html", records=records, cycles=cycles, totals=totals,
                               query=query, selected_status=selected_status, selected_cycle=selected_cycle,
                               user_name=session["user_name"], user_role=session["user_role"])
    finally:
        connection.close()


@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(
        debug=(os.environ.get("PERFORMANCEFLOW_DEBUG", "0") == "1")
    )
