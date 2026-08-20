from database import get_db_connection


connection = get_db_connection()


# =====================================
# GET ACTIVE REVIEW CYCLES
# =====================================

active_cycles = connection.execute(
    """
    SELECT
        id,
        cycle_name

    FROM review_cycles

    WHERE status = 'Active'
    """
).fetchall()



for cycle in active_cycles:


    cycle_id = cycle["id"]

    cycle_name = cycle["cycle_name"]


    print(
        f"Checking active cycle: {cycle_name}"
    )


    # =====================================
    # EMPLOYEE REVIEW CASES
    # =====================================

    reviews = connection.execute(
        """
        SELECT

            employee_reviews.id
                AS employee_review_id,

            employee_reviews.employee_id,

            employee_reviews.supervisor_id,

            employees.user_id
                AS employee_user_id,

            users.full_name
                AS employee_name

        FROM employee_reviews

        JOIN employees
            ON employee_reviews.employee_id
            = employees.id

        JOIN users
            ON employees.user_id
            = users.id

        WHERE employee_reviews.review_cycle_id = ?
        """,

        (
            cycle_id,
        )

    ).fetchall()


    print(
        f"Found {len(reviews)} employee review(s)."
    )



    # =====================================
    # EACH EMPLOYEE REVIEW
    # =====================================

    for review in reviews:


        employee_review_id = review[
            "employee_review_id"
        ]


        employee_user_id = review[
            "employee_user_id"
        ]


        supervisor_id = review[
            "supervisor_id"
        ]


        employee_name = review[
            "employee_name"
        ]



        # =================================
        # EMPLOYEE SELF-ASSESSMENT ACTION
        # =================================

        employee_action = connection.execute(
            """
            SELECT id

            FROM review_actions

            WHERE review_cycle_id = ?

            AND employee_review_id = ?

            AND assigned_to = ?

            AND action_type = 'SELF_ASSESSMENT'
            """,

            (
                cycle_id,
                employee_review_id,
                employee_user_id
            )

        ).fetchone()



        if employee_action is None:


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
                    employee_user_id,
                    "SELF_ASSESSMENT",
                    "Complete Self Assessment",
                    (
                        f"Complete your self-assessment "
                        f"for {cycle_name}."
                    ),
                    "Pending",
                    "High"
                )
            )


            print(
                f"Created self-assessment action "
                f"for {employee_name}."
            )



        # =================================
        # EMPLOYEE NOTIFICATION
        # =================================

        employee_notification = connection.execute(
            """
            SELECT id

            FROM notifications

            WHERE user_id = ?

            AND review_cycle_id = ?

            AND employee_review_id = ?

            AND notification_type = 'REVIEW_STARTED'
            """,

            (
                employee_user_id,
                cycle_id,
                employee_review_id
            )

        ).fetchone()



        if employee_notification is None:


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
                    employee_user_id,
                    cycle_id,
                    employee_review_id,
                    "REVIEW_STARTED",
                    "Your Performance Review Has Started",
                    (
                        f"{cycle_name} is now active. "
                        f"Your self-assessment is ready "
                        f"to complete."
                    )
                )
            )



        # =================================
        # SUPERVISOR ACTION
        # =================================

        if supervisor_id is not None:


            supervisor_action = connection.execute(
                """
                SELECT id

                FROM review_actions

                WHERE review_cycle_id = ?

                AND employee_review_id = ?

                AND assigned_to = ?

                AND action_type = 'SUPERVISOR_MONITORING'
                """,

                (
                    cycle_id,
                    employee_review_id,
                    supervisor_id
                )

            ).fetchone()



            if supervisor_action is None:


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
                        supervisor_id,
                        "SUPERVISOR_MONITORING",
                        (
                            f"Monitor "
                            f"{employee_name}'s Review"
                        ),
                        (
                            f"Monitor review progress for "
                            f"{employee_name} during "
                            f"{cycle_name}."
                        ),
                        "Pending",
                        "Normal"
                    )
                )


                print(
                    f"Created supervisor action "
                    f"for {employee_name}."
                )



            # =============================
            # SUPERVISOR NOTIFICATION
            # =============================

            supervisor_notification = connection.execute(
                """
                SELECT id

                FROM notifications

                WHERE user_id = ?

                AND review_cycle_id = ?

                AND employee_review_id = ?

                AND notification_type = 'REVIEW_ASSIGNED'
                """,

                (
                    supervisor_id,
                    cycle_id,
                    employee_review_id
                )

            ).fetchone()



            if supervisor_notification is None:


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
                        supervisor_id,
                        cycle_id,
                        employee_review_id,
                        "REVIEW_ASSIGNED",
                        "Employee Review Assigned",
                        (
                            f"{employee_name} has entered "
                            f"{cycle_name} under your supervision."
                        )
                    )
                )



    # =====================================
    # HR ACTIONS
    # =====================================

    hr_users = connection.execute(
        """
        SELECT
            id

        FROM users

        WHERE role = 'HR'
        """
    ).fetchall()



    for hr_user in hr_users:


        hr_action = connection.execute(
            """
            SELECT id

            FROM review_actions

            WHERE review_cycle_id = ?

            AND employee_review_id IS NULL

            AND assigned_to = ?

            AND action_type = 'CYCLE_MONITORING'
            """,

            (
                cycle_id,
                hr_user["id"]
            )

        ).fetchone()



        if hr_action is None:


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
                    hr_user["id"],
                    "CYCLE_MONITORING",
                    f"Monitor {cycle_name}",
                    (
                        "Monitor participation, "
                        "outstanding actions and review "
                        "progress across this cycle."
                    ),
                    "Pending",
                    "Normal"
                )
            )


            print(
                f"Created HR monitoring action "
                f"for {cycle_name}."
            )



        # =================================
        # HR NOTIFICATION
        # =================================

        hr_notification = connection.execute(
            """
            SELECT id

            FROM notifications

            WHERE user_id = ?

            AND review_cycle_id = ?

            AND employee_review_id IS NULL

            AND notification_type = 'CYCLE_ACTIVATED'
            """,

            (
                hr_user["id"],
                cycle_id
            )

        ).fetchone()



        if hr_notification is None:


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
                    cycle_id,
                    None,
                    "CYCLE_ACTIVATED",
                    "Review Cycle Activated",
                    (
                        f"{cycle_name} is currently active "
                        f"with {len(reviews)} employee review(s)."
                    )
                )
            )



connection.commit()

connection.close()


print("")
print("====================================")
print("ACTION BACKFILL COMPLETE")
print("====================================")
print(
    "Existing active review actions and "
    "notifications have been checked."
)