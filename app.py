import sqlite3
import os
import secrets
import time
import uuid
from datetime import datetime

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


    connection.close()


    notification_data = []


    for notification in notifications:

        notification_data.append({

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
            SELECT * FROM users
            WHERE email = ?
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

            login_attempts.pop(client_key, None)

            session["user_id"] = user["id"]

            session["user_name"] = user["full_name"]

            session["user_role"] = user["role"]

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


    # =====================================
    # DEFAULT DASHBOARD DATA
    # =====================================

    employee_count = 0

    current_active_cycle = None

    current_review = None

    supervisor_team_count = 0

    supervisor_active_reviews = 0

    manager_pending_approvals = 0


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

        AND review_cycles.status = 'Active'

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


        return render_template(
            "self_assessment.html",

            review=review,

            self_assessment=self_assessment,

            baseline_items=baseline_items,

            evidence_files=evidence_files,

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
            id,
            full_name

        FROM users

        WHERE role = 'Supervisor'

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
            id,
            full_name

        FROM users

        WHERE role = 'Supervisor'

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
                SELECT id

                FROM users

                WHERE id = ?
                AND role = 'Supervisor'
                """,

                (supervisor_id,)

            ).fetchone()


            if supervisor is None:

                flash(
                    "The selected supervisor is invalid.",
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


    if len(password) < 8:

        flash(
            "Temporary password must contain at least 8 characters.",
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
                SELECT id

                FROM users

                WHERE id = ?
                AND role = 'Supervisor'
                """,
                (supervisor_id,)
            ).fetchone()


            if supervisor is None:

                flash(
                    "The selected supervisor is invalid.",
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

    closure_readiness = {
        "completed": completed_review_count,
        "total": assigned_count,
        "can_close": (
            cycle["status"] == "Active"
            and assigned_count > 0
            and completed_review_count == assigned_count
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
                f"{skipped_count} employee(s) could not be assigned because they were unavailable or already assigned for this review year.",
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


        return render_template(
            "peer_review.html",

            assignment=assignment,

            peer_review=peer_review,

            baseline_items=baseline_items,

            reviewer_progress=reviewer_progress,

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


        if (
            review["employee_review_status"]
            != "Peer Review In Progress"
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

        if peer_phase_complete:

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

        manager_feedback = connection.execute(
            """
            SELECT
                manager_approvals.status,
                manager_approvals.decision_note,
                manager_approvals.decided_at,
                users.full_name AS manager_name
            FROM manager_approvals
            JOIN users
                ON users.id = manager_approvals.manager_id
            WHERE manager_approvals.employee_review_id = ?
            """,
            (employee_review_id,)
        ).fetchone()

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

        if (
            review["employee_review_status"]
            != "Supervisor Evaluation In Progress"
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

        if manager is not None:
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
        return jsonify({
            "success": False,
            "message": "Authentication required."
        }), 401

    if session["user_role"] != "Manager":
        return jsonify({
            "success": False,
            "message": "Only the assigned manager can return this review."
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

        if (
            review["cycle_status"] != "Active"
            or review["approval_status"] != "Pending"
            or review["supervisor_evaluation_status"] != "Submitted"
            or review["employee_review_status"] not in (
                "Supervisor Evaluation Submitted",
                "Manager Approval Pending"
            )
        ):
            return jsonify({
                "success": False,
                "message": "This review can no longer be returned."
            }), 409

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
                decision_note,
                review["manager_approval_id"]
            )
        )

        connection.execute(
            """
            UPDATE supervisor_evaluations
            SET
                status = 'Draft',
                submitted_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            AND status = 'Submitted'
            """,
            (review["supervisor_evaluation_id"],)
        )

        connection.execute(
            """
            UPDATE employee_reviews
            SET
                status = 'Supervisor Evaluation In Progress',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
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
            AND action_type IN (
                'MANAGER_APPROVAL',
                'MANAGER_APPROVAL_COORDINATION'
            )
            AND status != 'Completed'
            """,
            (employee_review_id,)
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
                review["supervisor_id"],
                "SUPERVISOR_EVALUATION",
                (
                    f"Revise {review['employee_name_snapshot']}'s "
                    "Evaluation"
                ),
                (
                    "Management requested changes. Review the decision "
                    "note, update the evaluation and submit it again."
                )
            )
        )

        recipients = (
            (
                review["supervisor_id"],
                "MANAGER_CHANGES_REQUESTED",
                "Evaluation Changes Requested",
                (
                    f"Management returned "
                    f"{review['employee_name_snapshot']}'s evaluation. "
                    "Open it to review the decision note and revise it."
                )
            ),
            (
                review["employee_user_id"],
                "MANAGER_CHANGES_REQUESTED",
                "Review Returned for Revision",
                (
                    f"Your {review['cycle_name']} review was returned "
                    "to your supervisor for revision."
                )
            ),
            (
                session["user_id"],
                "MANAGER_RETURN_CONFIRMED",
                "Review Returned",
                (
                    f"{review['employee_name_snapshot']}'s review was "
                    "returned to the supervisor."
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

        connection.commit()

        flash(
            "The review was returned to the supervisor for revision.",
            "success"
        )

        return jsonify({
            "success": True,
            "message": "Changes requested successfully.",
            "redirect_url": url_for("dashboard")
        })

    except sqlite3.Error as error:
        connection.rollback()
        print("Manager return error:", error)
        return jsonify({
            "success": False,
            "message": "The review could not be returned."
        }), 500

    finally:
        connection.close()


# =========================================================
# PB11 - FINAL REVIEW OUTCOME + ACKNOWLEDGEMENT
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


@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(
        debug=(os.environ.get("PERFORMANCEFLOW_DEBUG", "0") == "1")
    )
