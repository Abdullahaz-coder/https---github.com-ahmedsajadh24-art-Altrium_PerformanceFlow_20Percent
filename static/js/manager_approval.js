/* =========================================================
   ALTRIUM MANAGEMENT APPROVAL
========================================================= */

const managerWorkspace = document.getElementById(
    "managerApprovalWorkspace"
);

const managerDecisionNote = document.getElementById(
    "managerDecisionNote"
);

const managerDecisionStatus = document.getElementById(
    "managerDecisionStatus"
);

const approveManagerReview = document.getElementById(
    "approveManagerReview"
);

const requestManagerChanges = document.getElementById(
    "requestManagerChanges"
);

const managerConfirmBackdrop = document.getElementById(
    "managerConfirmBackdrop"
);

const managerConfirmDialog = document.getElementById(
    "managerConfirmDialog"
);

const managerConfirmTitle = document.getElementById(
    "managerConfirmTitle"
);

const managerConfirmMessage = document.getElementById(
    "managerConfirmMessage"
);

const confirmManagerDecision = document.getElementById(
    "confirmManagerDecision"
);

const cancelManagerDecision = document.getElementById(
    "cancelManagerDecision"
);

const changeRequestRecipientCards = document.querySelectorAll(
    "[data-recipient-card]"
);

if (managerConfirmBackdrop && managerConfirmDialog) {
    document.body.append(managerConfirmBackdrop, managerConfirmDialog);
}

let pendingManagerDecision = null;


function setManagerDecisionStatus(message, state = "neutral") {

    if (!managerDecisionStatus) {
        return;
    }

    managerDecisionStatus.textContent = message;
    managerDecisionStatus.classList.remove(
        "manager-status-ready",
        "manager-status-error"
    );

    if (state === "ready") {
        managerDecisionStatus.classList.add(
            "manager-status-ready"
        );
    }

    if (state === "error") {
        managerDecisionStatus.classList.add(
            "manager-status-error"
        );
    }
}


function updateManagerDecisionReadiness() {

    const hasNote = managerDecisionNote?.value.trim().length > 0;
    const privateRequests = getPrivateChangeRequests();
    const hasCompletePrivateRequests =
        privateRequests.length > 0
        && privateRequests.every(
            function (request) {
                return request.private_note.length > 0;
            }
        );

    if (approveManagerReview) {
        approveManagerReview.disabled = !hasNote;
    }

    if (requestManagerChanges) {
        requestManagerChanges.disabled = !hasCompletePrivateRequests;
    }

    setManagerDecisionStatus(
        hasCompletePrivateRequests
            ? `${privateRequests.length} private request(s) ready to send`
            : (
                hasNote
                    ? "Approval note ready"
                    : "Add an approval note, or select recipients and add private notes."
            ),
        (hasNote || hasCompletePrivateRequests) ? "ready" : "neutral"
    );
}


function getPrivateChangeRequests() {

    return Array.from(changeRequestRecipientCards)
        .filter(function (card) {
            return card.querySelector("[data-change-recipient]")?.checked;
        })
        .map(function (card) {
            return {
                recipient_user_id: Number(
                    card.querySelector("[data-change-recipient]").value
                ),
                private_note: card.querySelector("[data-change-note]")
                    .value
                    .trim()
            };
        });
}


function closeManagerConfirmation() {

    pendingManagerDecision = null;

    managerConfirmBackdrop?.classList.remove("visible");
    managerConfirmDialog?.classList.remove("visible");
    managerConfirmDialog?.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    managerDecisionNote?.focus();
}


function openManagerConfirmation(decision) {

    if (decision === "approve" && !managerDecisionNote?.value.trim()) {
        setManagerDecisionStatus("Please enter an approval note first.", "error");
        managerDecisionNote?.focus();
        return;
    }

    if (
        decision === "return"
        && (
            !getPrivateChangeRequests().length
            || getPrivateChangeRequests().some(
                function (request) {
                    return !request.private_note;
                }
            )
        )
    ) {
        setManagerDecisionStatus(
            "Select at least one contributor and add a private note for each one.",
            "error"
        );
        return;
    }

    pendingManagerDecision = decision;

    if (decision === "approve") {
        managerConfirmTitle.textContent = "Approve and lock this review?";
        managerConfirmMessage.textContent =
            "This records the final management approval and completes " +
            "the employee's review workflow.";
        confirmManagerDecision.textContent = "Approve Review";
        confirmManagerDecision.classList.remove("confirm-return");
    } else {
        managerConfirmTitle.textContent = "Return this review for changes?";
        managerConfirmMessage.textContent =
            "Each selected contributor will receive only their own private " +
            "note and can update only their part of the review.";
        confirmManagerDecision.textContent = "Request Changes";
        confirmManagerDecision.classList.add("confirm-return");
    }

    managerConfirmBackdrop?.classList.add("visible");
    managerConfirmDialog?.classList.add("visible");
    managerConfirmDialog?.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    cancelManagerDecision?.focus();
}


async function readManagerResponse(response) {

    try {
        return await response.json();
    } catch (error) {
        return {
            success: false,
            message: "The server returned an unexpected response."
        };
    }
}


async function submitManagerDecision() {

    if (!pendingManagerDecision || !managerWorkspace) {
        return;
    }

    const decision = pendingManagerDecision;
    const reviewId = managerWorkspace.dataset.reviewId;
    const endpoint = decision === "approve"
        ? `/reviews/${reviewId}/manager-approval/approve`
        : `/reviews/${reviewId}/manager-approval/request-changes`;

    confirmManagerDecision.disabled = true;
    confirmManagerDecision.textContent = "Recording...";

    try {
        const response = await fetch(endpoint, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
        body: JSON.stringify(
            decision === "approve"
                ? { decision_note: managerDecisionNote.value.trim() }
                : { change_requests: getPrivateChangeRequests() }
        )
        });

        const data = await readManagerResponse(response);

        if (!response.ok || !data.success) {
            throw new Error(
                data.message || "The decision could not be recorded."
            );
        }

        window.location.href = data.redirect_url || "/dashboard";

    } catch (error) {
        closeManagerConfirmation();
        setManagerDecisionStatus(error.message, "error");
    } finally {
        confirmManagerDecision.disabled = false;
    }
}


managerDecisionNote?.addEventListener(
    "input",
    updateManagerDecisionReadiness
);

changeRequestRecipientCards.forEach(function (card) {
    const checkbox = card.querySelector("[data-change-recipient]");
    const note = card.querySelector("[data-change-note]");

    checkbox?.addEventListener("change", function () {
        note.disabled = !checkbox.checked;
        card.classList.toggle("selected", checkbox.checked);
        if (!checkbox.checked) {
            note.value = "";
        }
        updateManagerDecisionReadiness();
    });

    note?.addEventListener("input", updateManagerDecisionReadiness);
});

approveManagerReview?.addEventListener(
    "click",
    function () {
        openManagerConfirmation("approve");
    }
);

requestManagerChanges?.addEventListener(
    "click",
    function () {
        openManagerConfirmation("return");
    }
);

confirmManagerDecision?.addEventListener(
    "click",
    submitManagerDecision
);

document.getElementById("closeManagerConfirm")?.addEventListener(
    "click",
    closeManagerConfirmation
);

cancelManagerDecision?.addEventListener(
    "click",
    closeManagerConfirmation
);

managerConfirmBackdrop?.addEventListener(
    "click",
    closeManagerConfirmation
);

document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
        closeManagerConfirmation();
    }
});

updateManagerDecisionReadiness();
