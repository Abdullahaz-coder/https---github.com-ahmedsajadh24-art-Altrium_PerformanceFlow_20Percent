/* =========================================================
   ALTRIUM PEER REVIEW STUDIO
========================================================= */

const peerReviewWorkspace =
    document.getElementById(
        "peerReviewWorkspace"
    );


const peerRatingPoints =
    document.querySelectorAll(
        ".peer-rating-point"
    );


/* =========================================================
   RATING INTERACTION
========================================================= */

peerRatingPoints.forEach(
    function (button) {

        button.addEventListener(
            "click",
            function () {

                if (button.disabled) {
                    return;
                }


                const itemId =
                    button.dataset.item;


                const sameItemRatings =
                    document.querySelectorAll(
                        `.peer-rating-point[data-item="${itemId}"]`
                    );


                sameItemRatings.forEach(
                    function (ratingButton) {

                        ratingButton.classList.remove(
                            "selected"
                        );

                    }
                );


                button.classList.add(
                    "selected"
                );


                setPeerDraftStatus(
                    "Unsaved changes"
                );

            }
        );

    }
);



/* =========================================================
   DRAFT STATUS
========================================================= */

const peerDraftStatus =
    document.getElementById(
        "peerDraftStatus"
    );


function setPeerDraftStatus(message) {

    if (!peerDraftStatus) {
        return;
    }


    peerDraftStatus.textContent =
        message;


    peerDraftStatus.classList.remove(
        "peer-save-success",
        "peer-save-error"
    );


    if (message === "All changes saved") {

        peerDraftStatus.classList.add(
            "peer-save-success"
        );

    }

}



/* =========================================================
   TEXT CHANGE DETECTION
========================================================= */

const peerTextInputs =
    document.querySelectorAll(
        `
        .peer-item-feedback,
        #peerStrengths,
        #peerDevelopmentFeedback,
        #peerCollaborationFeedback,
        #peerOverallComment
        `
    );


peerTextInputs.forEach(
    function (input) {

        input.addEventListener(
            "input",
            function () {

                setPeerDraftStatus(
                    "Unsaved changes"
                );

            }
        );

    }
);



/* =========================================================
   COLLECT PEER REVIEW DATA
========================================================= */

function collectPeerReviewData() {

    const responses = [];


    document.querySelectorAll(
        ".peer-item-feedback"
    ).forEach(
        function (textarea) {

            const itemId =
                textarea.dataset.item;


            const selectedRating =
                document.querySelector(
                    `.peer-rating-point[data-item="${itemId}"].selected`
                );


            responses.push({
                review_plan_item_id:
                    itemId,

                rating:
                    selectedRating
                        ? selectedRating.dataset.rating
                        : null,

                feedback_text:
                    textarea.value
            });

        }
    );


    return {
        responses: responses,

        strengths:
            document.getElementById(
                "peerStrengths"
            )?.value || "",

        development_feedback:
            document.getElementById(
                "peerDevelopmentFeedback"
            )?.value || "",

        collaboration_feedback:
            document.getElementById(
                "peerCollaborationFeedback"
            )?.value || "",

        overall_comment:
            document.getElementById(
                "peerOverallComment"
            )?.value || ""
    };

}



/* =========================================================
   SAVE PEER REVIEW DRAFT
========================================================= */

const savePeerReviewDraft =
    document.getElementById(
        "savePeerReviewDraft"
    );


if (
    peerReviewWorkspace
    &&
    savePeerReviewDraft
) {

    savePeerReviewDraft.addEventListener(
        "click",
        async function () {

            const reviewId =
                peerReviewWorkspace.dataset.reviewId;


            const originalButtonText =
                savePeerReviewDraft.textContent;


            savePeerReviewDraft.disabled = true;

            savePeerReviewDraft.textContent =
                "Saving...";

            setPeerDraftStatus(
                "Saving your confidential draft..."
            );


            try {

                const response = await fetch(
                    `/reviews/${reviewId}/peer-review/save`,
                    {
                        method: "POST",

                        headers: {
                            "Content-Type":
                                "application/json"
                        },

                        body: JSON.stringify(
                            collectPeerReviewData()
                        )
                    }
                );


                const result = await response.json();


                if (
                    !response.ok
                    ||
                    !result.success
                ) {

                    throw new Error(
                        result.message
                        ||
                        "Your draft could not be saved."
                    );

                }


                savePeerReviewDraft.textContent =
                    "Saved ✓";

                setPeerDraftStatus(
                    "All changes saved"
                );


                window.setTimeout(
                    function () {

                        savePeerReviewDraft.textContent =
                            originalButtonText;

                    },
                    1800
                );

            }

            catch (error) {

                savePeerReviewDraft.textContent =
                    "Try Again";

                setPeerDraftStatus(
                    error.message
                    ||
                    "Your draft could not be saved."
                );


                if (peerDraftStatus) {

                    peerDraftStatus.classList.add(
                        "peer-save-error"
                    );

                }

            }

            finally {

                savePeerReviewDraft.disabled = false;

            }

        }
    );

}



/* =========================================================
   FINAL PEER REVIEW SUBMISSION GATE
========================================================= */

const submitPeerReview =
    document.getElementById(
        "submitPeerReview"
    );


const peerSubmissionGate =
    document.getElementById(
        "peerSubmissionGate"
    );


const peerSubmissionGateBackdrop =
    document.getElementById(
        "peerSubmissionGateBackdrop"
    );


const closePeerSubmissionGate =
    document.getElementById(
        "closePeerSubmissionGate"
    );


const returnToPeerReview =
    document.getElementById(
        "returnToPeerReview"
    );


const confirmPeerReviewSubmission =
    document.getElementById(
        "confirmPeerReviewSubmission"
    );


if (peerSubmissionGate && peerSubmissionGateBackdrop) {
    document.body.append(
        peerSubmissionGateBackdrop,
        peerSubmissionGate
    );
}


function calculatePeerSubmissionReadiness() {

    const peerReviewData =
        collectPeerReviewData();


    const totalItems =
        peerReviewData.responses.length;


    const completeItems =
        peerReviewData.responses.filter(
            function (response) {

                return (
                    response.rating !== null
                    &&
                    response.feedback_text.trim().length > 0
                );

            }
        ).length;


    const overallResponses = [
        peerReviewData.strengths,
        peerReviewData.development_feedback,
        peerReviewData.collaboration_feedback,
        peerReviewData.overall_comment
    ];


    const completeOverall =
        overallResponses.filter(
            function (response) {

                return response.trim().length > 0;

            }
        ).length;


    const totalRequirements =
        totalItems + overallResponses.length;


    const completedRequirements =
        completeItems + completeOverall;


    return {
        peerReviewData: peerReviewData,

        totalItems: totalItems,
        completeItems: completeItems,

        completeOverall: completeOverall,
        totalOverall: overallResponses.length,

        itemFeedbackReady:
            totalItems > 0
            &&
            completeItems === totalItems,

        overallFeedbackReady:
            completeOverall === overallResponses.length,

        score:
            totalRequirements > 0
                ? Math.round(
                    (
                        completedRequirements
                        /
                        totalRequirements
                    )
                    * 100
                )
                : 0,

        ready:
            (
                totalItems > 0
                &&
                completeItems === totalItems
                &&
                completeOverall === overallResponses.length
            )
    };

}


function updatePeerSubmissionCheck(
    cardId,
    textId,
    ready,
    message
) {

    const card =
        document.getElementById(
            cardId
        );


    const text =
        document.getElementById(
            textId
        );


    if (!card || !text) {
        return;
    }


    card.classList.remove(
        "ready",
        "blocked"
    );


    card.classList.add(
        ready
            ? "ready"
            : "blocked"
    );


    card.querySelector(
        ".submission-check-icon"
    ).textContent =
        ready
            ? "✓"
            : "!";


    text.textContent =
        message;

}


function openPeerSubmissionGate() {

    if (
        !peerSubmissionGate
        ||
        !peerSubmissionGateBackdrop
        ||
        !confirmPeerReviewSubmission
    ) {
        return;
    }


    const readiness =
        calculatePeerSubmissionReadiness();


    document.getElementById(
        "peerSubmissionScore"
    ).textContent =
        `${readiness.score}%`;


    updatePeerSubmissionCheck(
        "peerItemSubmissionCheck",
        "peerItemCheckText",
        readiness.itemFeedbackReady,
        `${readiness.completeItems} of ${readiness.totalItems} complete`
    );


    updatePeerSubmissionCheck(
        "peerOverallSubmissionCheck",
        "peerOverallCheckText",
        readiness.overallFeedbackReady,
        `${readiness.completeOverall} of ${readiness.totalOverall} complete`
    );


    const title =
        document.getElementById(
            "peerSubmissionReadinessTitle"
        );


    const message =
        document.getElementById(
            "peerSubmissionReadinessMessage"
        );


    if (readiness.ready) {

        title.textContent =
            "Peer review ready for submission";

        message.textContent =
            "All required ratings and feedback are complete.";

    }

    else {

        title.textContent =
            "Peer review needs attention";

        message.textContent =
            "Resolve the highlighted areas before submitting.";

    }


    confirmPeerReviewSubmission.disabled =
        !readiness.ready;


    peerSubmissionGate.classList.add(
        "open"
    );


    peerSubmissionGateBackdrop.classList.add(
        "show"
    );


    document.body.style.overflow =
        "hidden";

    closePeerSubmissionGate?.focus();

}


function closePeerSubmissionGatePanel() {

    if (
        !peerSubmissionGate
        ||
        !peerSubmissionGateBackdrop
    ) {
        return;
    }


    peerSubmissionGate.classList.remove(
        "open"
    );


    peerSubmissionGateBackdrop.classList.remove(
        "show"
    );


    document.body.style.overflow =
        "";

    submitPeerReview?.focus();

}


if (submitPeerReview) {

    submitPeerReview.addEventListener(
        "click",
        openPeerSubmissionGate
    );

}


if (closePeerSubmissionGate) {

    closePeerSubmissionGate.addEventListener(
        "click",
        closePeerSubmissionGatePanel
    );

}


if (returnToPeerReview) {

    returnToPeerReview.addEventListener(
        "click",
        closePeerSubmissionGatePanel
    );

}


if (peerSubmissionGateBackdrop) {

    peerSubmissionGateBackdrop.addEventListener(
        "click",
        closePeerSubmissionGatePanel
    );

}


document.addEventListener("keydown", function (event) {
    if (
        event.key === "Escape"
        && peerSubmissionGate?.classList.contains("open")
    ) {
        closePeerSubmissionGatePanel();
    }
});


if (
    confirmPeerReviewSubmission
    &&
    peerReviewWorkspace
) {

    confirmPeerReviewSubmission.addEventListener(
        "click",
        async function () {

            const readiness =
                calculatePeerSubmissionReadiness();


            if (!readiness.ready) {

                openPeerSubmissionGate();

                return;

            }


            const reviewId =
                peerReviewWorkspace.dataset.reviewId;


            confirmPeerReviewSubmission.disabled =
                true;

            confirmPeerReviewSubmission.textContent =
                "Submitting...";


            try {

                const response = await fetch(
                    `/reviews/${reviewId}/peer-review/submit`,
                    {
                        method: "POST",

                        headers: {
                            "Content-Type":
                                "application/json"
                        },

                        body: JSON.stringify(
                            readiness.peerReviewData
                        )
                    }
                );


                let result;


                try {

                    result = await response.json();

                }

                catch {

                    throw new Error(
                        "The server returned an invalid response."
                    );

                }


                if (
                    !response.ok
                    ||
                    !result.success
                ) {

                    throw new Error(
                        result.message
                        ||
                        "The peer review could not be submitted."
                    );

                }


                confirmPeerReviewSubmission.textContent =
                    "Submitted ✓";


                window.setTimeout(
                    function () {

                        window.location.href =
                            result.redirect_url;

                    },
                    500
                );

            }

            catch (error) {

                confirmPeerReviewSubmission.disabled =
                    false;

                confirmPeerReviewSubmission.textContent =
                    "Submit & Lock";


                setPeerDraftStatus(
                    error.message
                    ||
                    "The peer review could not be submitted."
                );


                if (peerDraftStatus) {

                    peerDraftStatus.classList.add(
                        "peer-save-error"
                    );

                }


                alert(
                    error.message
                );

            }

        }
    );

}
