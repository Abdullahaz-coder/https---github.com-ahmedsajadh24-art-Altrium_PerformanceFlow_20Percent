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

    if (!managerDecisionNote) {
        return;
    }

    const hasNote = managerDecisionNote.value.trim().length > 0;

    if (approveManagerReview) {
        approveManagerReview.disabled = !hasNote;
    }

    if (requestManagerChanges) {
        requestManagerChanges.disabled = !hasNote;
    }

    setManagerDecisionStatus(
        hasNote
            ? "Decision note ready"
            : "A decision note is required.",
        hasNote ? "ready" : "neutral"
    );
}


function closeManagerConfirmation() {

    pendingManagerDecision = null;

    managerConfirmBackdrop?.classList.remove("visible");
    managerConfirmDialog?.classList.remove("visible");
    managerConfirmDialog?.setAttribute("aria-hidden", "true");
}


function openManagerConfirmation(decision) {

    if (!managerDecisionNote?.value.trim()) {
        setManagerDecisionStatus(
            "Please enter a decision note first.",
            "error"
        );
        managerDecisionNote?.focus();
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
            "The supervisor evaluation will reopen with your decision " +
            "note and must be submitted again.";
        confirmManagerDecision.textContent = "Request Changes";
        confirmManagerDecision.classList.add("confirm-return");
    }

    managerConfirmBackdrop?.classList.add("visible");
    managerConfirmDialog?.classList.add("visible");
    managerConfirmDialog?.setAttribute("aria-hidden", "false");
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
            body: JSON.stringify({
                decision_note: managerDecisionNote.value.trim()
            })
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

document.getElementById("cancelManagerDecision")?.addEventListener(
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
