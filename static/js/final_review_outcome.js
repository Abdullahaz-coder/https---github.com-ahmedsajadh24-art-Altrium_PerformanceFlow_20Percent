/* =========================================================
   ALTRIUM FINAL REVIEW OUTCOME
========================================================= */

const finalOutcomeWorkspace = document.getElementById(
    "finalOutcomeWorkspace"
);

const finalOutcomeConfirmed = document.getElementById(
    "finalOutcomeConfirmed"
);

const finalEmployeeComment = document.getElementById(
    "finalEmployeeComment"
);

const acknowledgeFinalOutcome = document.getElementById(
    "acknowledgeFinalOutcome"
);

const finalAcknowledgementStatus = document.getElementById(
    "finalAcknowledgementStatus"
);


function setFinalAcknowledgementStatus(message, state = "neutral") {

    if (!finalAcknowledgementStatus) {
        return;
    }

    finalAcknowledgementStatus.textContent = message;
    finalAcknowledgementStatus.classList.remove(
        "manager-status-ready",
        "manager-status-error"
    );

    if (state === "ready") {
        finalAcknowledgementStatus.classList.add(
            "manager-status-ready"
        );
    }

    if (state === "error") {
        finalAcknowledgementStatus.classList.add(
            "manager-status-error"
        );
    }
}


function updateFinalAcknowledgementReadiness() {

    if (!finalOutcomeConfirmed || !acknowledgeFinalOutcome) {
        return;
    }

    acknowledgeFinalOutcome.disabled = !finalOutcomeConfirmed.checked;

    setFinalAcknowledgementStatus(
        finalOutcomeConfirmed.checked
            ? "Ready to complete the review"
            : "Confirmation is required.",
        finalOutcomeConfirmed.checked ? "ready" : "neutral"
    );
}


async function readFinalOutcomeResponse(response) {

    try {
        return await response.json();
    } catch (error) {
        return {
            success: false,
            message: "The server returned an unexpected response."
        };
    }
}


async function submitFinalAcknowledgement() {

    if (
        !finalOutcomeWorkspace
        || !finalOutcomeConfirmed?.checked
        || !acknowledgeFinalOutcome
    ) {
        return;
    }

    const reviewId = finalOutcomeWorkspace.dataset.reviewId;

    acknowledgeFinalOutcome.disabled = true;
    acknowledgeFinalOutcome.textContent = "Completing...";

    try {
        const response = await fetch(
            `/reviews/${reviewId}/final-outcome/acknowledge`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    confirmed: true,
                    employee_comment:
                        finalEmployeeComment?.value.trim() || ""
                })
            }
        );

        const data = await readFinalOutcomeResponse(response);

        if (!response.ok || !data.success) {
            throw new Error(
                data.message || "The acknowledgement could not be recorded."
            );
        }

        window.location.href = data.redirect_url || "/dashboard";

    } catch (error) {
        acknowledgeFinalOutcome.textContent = "Acknowledge & Complete";
        setFinalAcknowledgementStatus(error.message, "error");
        updateFinalAcknowledgementReadiness();
    }
}


finalOutcomeConfirmed?.addEventListener(
    "change",
    updateFinalAcknowledgementReadiness
);

acknowledgeFinalOutcome?.addEventListener(
    "click",
    submitFinalAcknowledgement
);

updateFinalAcknowledgementReadiness();
