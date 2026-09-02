/* =========================================================
   ALTRIUM SUPERVISOR EVALUATION
========================================================= */

const supervisorWorkspace =
    document.getElementById(
        "supervisorEvaluationWorkspace"
    );


const supervisorDraftStatus =
    document.getElementById(
        "supervisorDraftStatus"
    );


function setSupervisorDraftStatus(message, state = "neutral") {

    if (!supervisorDraftStatus) {
        return;
    }

    supervisorDraftStatus.textContent = message;

    supervisorDraftStatus.classList.remove(
        "supervisor-save-success",
        "supervisor-save-error"
    );

    if (state === "success") {
        supervisorDraftStatus.classList.add(
            "supervisor-save-success"
        );
    }

    if (state === "error") {
        supervisorDraftStatus.classList.add(
            "supervisor-save-error"
        );
    }

}


/* =========================================================
   RATING INTERACTIONS
========================================================= */

const supervisorRatingPoints =
    document.querySelectorAll(
        ".supervisor-rating-point"
    );


supervisorRatingPoints.forEach(
    function (button) {

        button.addEventListener(
            "click",
            function () {

                if (button.disabled) {
                    return;
                }

                const itemId = button.dataset.item;

                document.querySelectorAll(
                    `.supervisor-rating-point[data-item="${itemId}"]`
                ).forEach(
                    function (ratingButton) {
                        ratingButton.classList.remove("selected");
                    }
                );

                button.classList.add("selected");

                setSupervisorDraftStatus(
                    "Unsaved changes"
                );
            }
        );
    }
);


const supervisorOverallRatings =
    document.querySelectorAll(
        ".supervisor-overall-rating"
    );


supervisorOverallRatings.forEach(
    function (button) {

        button.addEventListener(
            "click",
            function () {

                if (button.disabled) {
                    return;
                }

                supervisorOverallRatings.forEach(
                    function (ratingButton) {
                        ratingButton.classList.remove("selected");
                    }
                );

                button.classList.add("selected");

                setSupervisorDraftStatus(
                    "Unsaved changes"
                );
            }
        );
    }
);


const supervisorTextInputs =
    document.querySelectorAll(
        `
        .supervisor-item-evaluation,
        #supervisorPerformanceSummary,
        #supervisorKeyStrengths,
        #supervisorDevelopmentPriorities,
        #supervisorSupportPlan,
        #supervisorRecommendation
        `
    );


supervisorTextInputs.forEach(
    function (input) {
        input.addEventListener(
            "input",
            function () {
                setSupervisorDraftStatus("Unsaved changes");
            }
        );

        input.addEventListener(
            "change",
            function () {
                setSupervisorDraftStatus("Unsaved changes");
            }
        );
    }
);


/* =========================================================
   PAYLOAD
========================================================= */

function collectSupervisorEvaluationData() {

    const responses = [];

    document.querySelectorAll(
        ".supervisor-item-evaluation"
    ).forEach(
        function (textarea) {

            const itemId = textarea.dataset.item;

            const selectedRating = document.querySelector(
                `.supervisor-rating-point[data-item="${itemId}"].selected`
            );

            responses.push({
                review_plan_item_id: itemId,
                rating:
                    selectedRating
                        ? selectedRating.dataset.rating
                        : null,
                evaluation_text: textarea.value
            });
        }
    );

    const overallRating = document.querySelector(
        ".supervisor-overall-rating.selected"
    );

    return {
        responses: responses,
        overall_rating:
            overallRating
                ? overallRating.dataset.rating
                : null,
        performance_summary:
            document.getElementById(
                "supervisorPerformanceSummary"
            )?.value || "",
        key_strengths:
            document.getElementById(
                "supervisorKeyStrengths"
            )?.value || "",
        development_priorities:
            document.getElementById(
                "supervisorDevelopmentPriorities"
            )?.value || "",
        support_plan:
            document.getElementById(
                "supervisorSupportPlan"
            )?.value || "",
        recommendation:
            document.getElementById(
                "supervisorRecommendation"
            )?.value || ""
    };
}


async function readSupervisorJsonResponse(response) {

    try {
        return await response.json();
    }
    catch {
        throw new Error(
            "The server returned an invalid response."
        );
    }
}


/* =========================================================
   SAVE DRAFT
========================================================= */

const saveSupervisorEvaluation =
    document.getElementById(
        "saveSupervisorEvaluation"
    );


if (saveSupervisorEvaluation && supervisorWorkspace) {

    saveSupervisorEvaluation.addEventListener(
        "click",
        async function () {

            const reviewId = supervisorWorkspace.dataset.reviewId;
            const originalText = saveSupervisorEvaluation.textContent;

            saveSupervisorEvaluation.disabled = true;
            saveSupervisorEvaluation.textContent = "Saving...";

            setSupervisorDraftStatus(
                "Saving your evaluation draft..."
            );

            try {
                const response = await fetch(
                    `/reviews/${reviewId}/supervisor-evaluation/save`,
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json"
                        },
                        body: JSON.stringify(
                            collectSupervisorEvaluationData()
                        )
                    }
                );

                const result = await readSupervisorJsonResponse(response);

                if (!response.ok || !result.success) {
                    throw new Error(
                        result.message
                        ||
                        "The evaluation draft could not be saved."
                    );
                }

                saveSupervisorEvaluation.textContent = "Saved ✓";

                setSupervisorDraftStatus(
                    "All changes saved",
                    "success"
                );

                window.setTimeout(
                    function () {
                        saveSupervisorEvaluation.textContent = originalText;
                    },
                    1800
                );
            }
            catch (error) {
                saveSupervisorEvaluation.textContent = "Try Again";

                setSupervisorDraftStatus(
                    error.message
                    ||
                    "The evaluation draft could not be saved.",
                    "error"
                );
            }
            finally {
                saveSupervisorEvaluation.disabled = false;
            }
        }
    );
}


/* =========================================================
   SUBMISSION READINESS
========================================================= */

function calculateSupervisorReadiness() {

    const evaluationData = collectSupervisorEvaluationData();

    const totalItems = evaluationData.responses.length;

    const completeItems = evaluationData.responses.filter(
        function (response) {
            return (
                response.rating !== null
                &&
                response.evaluation_text.trim().length > 0
            );
        }
    ).length;

    const overallTexts = [
        evaluationData.performance_summary,
        evaluationData.key_strengths,
        evaluationData.development_priorities,
        evaluationData.support_plan
    ];

    const completeOverallTexts = overallTexts.filter(
        function (text) {
            return text.trim().length > 0;
        }
    ).length;

    const decisionReady = (
        evaluationData.overall_rating !== null
        &&
        evaluationData.recommendation.length > 0
    );

    const totalRequirements = totalItems + 6;

    const completedRequirements = (
        completeItems
        +
        completeOverallTexts
        +
        (evaluationData.overall_rating !== null ? 1 : 0)
        +
        (evaluationData.recommendation.length > 0 ? 1 : 0)
    );

    const itemsReady = (
        totalItems > 0
        &&
        completeItems === totalItems
    );

    const overallReady = completeOverallTexts === 4;

    return {
        evaluationData: evaluationData,
        totalItems: totalItems,
        completeItems: completeItems,
        completeOverallTexts: completeOverallTexts,
        itemsReady: itemsReady,
        overallReady: overallReady,
        decisionReady: decisionReady,
        score:
            totalRequirements > 0
                ? Math.round(
                    completedRequirements
                    /
                    totalRequirements
                    * 100
                )
                : 0,
        ready: itemsReady && overallReady && decisionReady
    };
}


function updateSupervisorSubmissionCheck(
    cardId,
    textId,
    ready,
    message
) {

    const card = document.getElementById(cardId);
    const text = document.getElementById(textId);

    if (!card || !text) {
        return;
    }

    card.classList.remove("ready", "blocked");
    card.classList.add(ready ? "ready" : "blocked");

    card.querySelector(
        ".submission-check-icon"
    ).textContent = ready ? "✓" : "!";

    text.textContent = message;
}


/* =========================================================
   SUBMISSION GATE
========================================================= */

const reviewSupervisorEvaluation =
    document.getElementById(
        "reviewSupervisorEvaluation"
    );

const supervisorSubmissionGate =
    document.getElementById(
        "supervisorSubmissionGate"
    );

const supervisorSubmissionGateBackdrop =
    document.getElementById(
        "supervisorSubmissionGateBackdrop"
    );

const closeSupervisorSubmissionGate =
    document.getElementById(
        "closeSupervisorSubmissionGate"
    );

const returnToSupervisorEvaluation =
    document.getElementById(
        "returnToSupervisorEvaluation"
    );

const confirmSupervisorSubmission =
    document.getElementById(
        "confirmSupervisorSubmission"
    );


if (
    supervisorSubmissionGate
    && supervisorSubmissionGateBackdrop
) {
    document.body.append(
        supervisorSubmissionGateBackdrop,
        supervisorSubmissionGate
    );
}


function openSupervisorSubmissionGate() {

    if (
        !supervisorSubmissionGate
        ||
        !supervisorSubmissionGateBackdrop
        ||
        !confirmSupervisorSubmission
    ) {
        return;
    }

    const readiness = calculateSupervisorReadiness();

    document.getElementById(
        "supervisorSubmissionScore"
    ).textContent = `${readiness.score}%`;

    updateSupervisorSubmissionCheck(
        "supervisorItemSubmissionCheck",
        "supervisorItemCheckText",
        readiness.itemsReady,
        `${readiness.completeItems} of ${readiness.totalItems} complete`
    );

    updateSupervisorSubmissionCheck(
        "supervisorOverallSubmissionCheck",
        "supervisorOverallCheckText",
        readiness.overallReady,
        `${readiness.completeOverallTexts} of 4 complete`
    );

    updateSupervisorSubmissionCheck(
        "supervisorDecisionSubmissionCheck",
        "supervisorDecisionCheckText",
        readiness.decisionReady,
        readiness.decisionReady
            ? "Rating and recommendation selected"
            : "Rating and recommendation required"
    );

    const title = document.getElementById(
        "supervisorSubmissionReadinessTitle"
    );

    const message = document.getElementById(
        "supervisorSubmissionReadinessMessage"
    );

    if (readiness.ready) {
        title.textContent = "Evaluation ready for submission";
        message.textContent =
            "All required judgments and decision fields are complete.";
    }
    else {
        title.textContent = "Evaluation needs attention";
        message.textContent =
            "Resolve the highlighted areas before submitting.";
    }

    confirmSupervisorSubmission.disabled = !readiness.ready;

    supervisorSubmissionGate.classList.add("open");
    supervisorSubmissionGateBackdrop.classList.add("show");
    document.body.style.overflow = "hidden";
    closeSupervisorSubmissionGate?.focus();
}


function closeSupervisorSubmissionGatePanel() {

    if (
        !supervisorSubmissionGate
        ||
        !supervisorSubmissionGateBackdrop
    ) {
        return;
    }

    supervisorSubmissionGate.classList.remove("open");
    supervisorSubmissionGateBackdrop.classList.remove("show");
    document.body.style.overflow = "";
    reviewSupervisorEvaluation?.focus();
}


if (reviewSupervisorEvaluation) {
    reviewSupervisorEvaluation.addEventListener(
        "click",
        openSupervisorSubmissionGate
    );
}

if (closeSupervisorSubmissionGate) {
    closeSupervisorSubmissionGate.addEventListener(
        "click",
        closeSupervisorSubmissionGatePanel
    );
}

if (returnToSupervisorEvaluation) {
    returnToSupervisorEvaluation.addEventListener(
        "click",
        closeSupervisorSubmissionGatePanel
    );
}

if (supervisorSubmissionGateBackdrop) {
    supervisorSubmissionGateBackdrop.addEventListener(
        "click",
        closeSupervisorSubmissionGatePanel
    );
}


document.addEventListener("keydown", function (event) {
    if (
        event.key === "Escape"
        && supervisorSubmissionGate?.classList.contains("open")
    ) {
        closeSupervisorSubmissionGatePanel();
    }
});


/* =========================================================
   SUBMIT + LOCK
========================================================= */

if (confirmSupervisorSubmission && supervisorWorkspace) {

    confirmSupervisorSubmission.addEventListener(
        "click",
        async function () {

            const readiness = calculateSupervisorReadiness();

            if (!readiness.ready) {
                openSupervisorSubmissionGate();
                return;
            }

            const reviewId = supervisorWorkspace.dataset.reviewId;

            confirmSupervisorSubmission.disabled = true;
            confirmSupervisorSubmission.textContent = "Submitting...";

            try {
                const response = await fetch(
                    `/reviews/${reviewId}/supervisor-evaluation/submit`,
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json"
                        },
                        body: JSON.stringify(
                            readiness.evaluationData
                        )
                    }
                );

                const result = await readSupervisorJsonResponse(response);

                if (!response.ok || !result.success) {
                    throw new Error(
                        result.message
                        ||
                        "The evaluation could not be submitted."
                    );
                }

                confirmSupervisorSubmission.textContent = "Submitted ✓";

                window.setTimeout(
                    function () {
                        window.location.href = result.redirect_url;
                    },
                    500
                );
            }
            catch (error) {
                confirmSupervisorSubmission.disabled = false;
                confirmSupervisorSubmission.textContent = "Submit & Lock";

                setSupervisorDraftStatus(
                    error.message
                    ||
                    "The evaluation could not be submitted.",
                    "error"
                );

                alert(error.message);
            }
        }
    );
}
