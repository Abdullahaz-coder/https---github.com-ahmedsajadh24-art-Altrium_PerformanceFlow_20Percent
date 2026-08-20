/* ========================================
   SELF-ASSESSMENT STUDIO
======================================== */

const ratingPoints =
    document.querySelectorAll(
        ".rating-point"
    );


ratingPoints.forEach(
    function (button) {

        button.addEventListener(
            "click",
            function () {

                const itemId =
                    button.dataset.item;


                const sameItemButtons =
                    document.querySelectorAll(
                        `.rating-point[data-item="${itemId}"]`
                    );


                sameItemButtons.forEach(
                    function (ratingButton) {

                        ratingButton.classList.remove(
                            "selected"
                        );

                    }
                );


                button.classList.add(
                    "selected"
                );

            }
        );

    }
);

/* ========================================
   EVIDENCE VAULT
======================================== */

const evidenceFileInput =
    document.getElementById(
        "evidenceFileInput"
    );


const selectedEvidenceFile =
    document.getElementById(
        "selectedEvidenceFile"
    );


const uploadEvidenceButton =
    document.getElementById(
        "uploadEvidenceButton"
    );


if (evidenceFileInput) {

    evidenceFileInput.addEventListener(
        "change",
        function () {

            const file =
                evidenceFileInput.files[0];


            if (!file) {

                selectedEvidenceFile.textContent =
                    "No file selected";


                uploadEvidenceButton.disabled =
                    true;


                return;

            }


            const sizeInMB =
                file.size
                /
                (1024 * 1024);


            if (sizeInMB > 10) {

                alert(
                    "Evidence files must be 10 MB or smaller."
                );


                evidenceFileInput.value =
                    "";


                selectedEvidenceFile.textContent =
                    "No file selected";


                uploadEvidenceButton.disabled =
                    true;


                return;

            }


            selectedEvidenceFile.textContent =
                `${file.name} • ${sizeInMB.toFixed(2)} MB`;


            uploadEvidenceButton.disabled =
                false;

        }
    );

}



/* ========================================
   REMOVE CONFIRMATION
======================================== */

const removeEvidenceForms =
    document.querySelectorAll(
        ".remove-evidence-form"
    );


removeEvidenceForms.forEach(
    function (form) {

        form.addEventListener(
            "submit",
            function (event) {

                const confirmed =
                    confirm(
                        "Remove this evidence from your draft self-assessment?"
                    );


                if (!confirmed) {

                    event.preventDefault();

                }

            }
        );

    }
);

/* =========================================================
   SAVE SELF-ASSESSMENT DRAFT
========================================================= */

const assessmentWorkspace =
    document.getElementById(
        "assessmentWorkspace"
    );


const saveAssessmentDraft =
    document.getElementById(
        "saveAssessmentDraft"
    );



/* ========================================
   COLLECT ASSESSMENT DATA
======================================== */

function collectAssessmentData() {

    const responses = [];


    const reflectionFields =
        document.querySelectorAll(
            ".assessment-item-reflection"
        );


    reflectionFields.forEach(
        function (textarea) {

            const itemId =
                textarea.dataset.item;


            const selectedRating =
                document.querySelector(
                    `.rating-point.selected[data-item="${itemId}"]`
                );


            responses.push({

                review_plan_item_id:
                    Number(itemId),

                rating:
                    selectedRating
                        ? Number(
                            selectedRating.dataset.rating
                        )
                        : null,

                response_text:
                    textarea.value.trim()

            });

        }
    );


    return {

        overall_summary:
            document
                .getElementById(
                    "overallSummary"
                )
                .value
                .trim(),

        key_achievements:
            document
                .getElementById(
                    "keyAchievements"
                )
                .value
                .trim(),

        challenges:
            document
                .getElementById(
                    "challenges"
                )
                .value
                .trim(),

        support_needed:
            document
                .getElementById(
                    "supportNeeded"
                )
                .value
                .trim(),

        responses:
            responses

    };

}



/* ========================================
   SAVE BUTTON
======================================== */

if (
    saveAssessmentDraft
    &&
    assessmentWorkspace
) {

    saveAssessmentDraft.addEventListener(
        "click",
        async function () {

            const reviewId =
                assessmentWorkspace
                    .dataset
                    .reviewId;


            const assessmentData =
                collectAssessmentData();


            const originalText =
                saveAssessmentDraft
                    .textContent;


            saveAssessmentDraft.disabled =
                true;


            saveAssessmentDraft.textContent =
                "Saving...";


            try {

                const response =
                    await fetch(
                        `/reviews/${reviewId}/self-assessment/save`,
                        {
                            method: "POST",

                            headers: {
                                "Content-Type":
                                    "application/json"
                            },

                            body:
                                JSON.stringify(
                                    assessmentData
                                )
                        }
                    );


                const data =
                    await response.json();


                if (
                    !response.ok
                    ||
                    !data.success
                ) {

                    throw new Error(
                        data.message
                        ||
                        "Unable to save draft."
                    );

                }


                saveAssessmentDraft.textContent =
                    "Saved ✓";


                showDraftSaveStatus(
                    "All changes saved",
                    "success"
                );


                setTimeout(
                    function () {

                        saveAssessmentDraft
                            .textContent =
                            originalText;


                        saveAssessmentDraft
                            .disabled =
                            false;

                    },

                    1800
                );

            }


            catch (error) {

                saveAssessmentDraft.textContent =
                    "Save failed";


                saveAssessmentDraft.disabled =
                    false;


                showDraftSaveStatus(
                    error.message,
                    "error"
                );

            }

        }
    );

}


/* =========================================================
   FINAL SUBMISSION GATE
========================================================= */

const submitAssessment =
    document.getElementById(
        "submitAssessment"
    );


const submissionGate =
    document.getElementById(
        "submissionGate"
    );


const submissionGateBackdrop =
    document.getElementById(
        "submissionGateBackdrop"
    );


const closeSubmissionGate =
    document.getElementById(
        "closeSubmissionGate"
    );


const returnToAssessment =
    document.getElementById(
        "returnToAssessment"
    );


const confirmAssessmentSubmission =
    document.getElementById(
        "confirmAssessmentSubmission"
    );



/* ========================================
   READINESS CHECK
======================================== */

function calculateSubmissionReadiness() {

    const assessmentData =
        collectAssessmentData();


    const totalItems =
        assessmentData.responses.length;


    const completeItems =
        assessmentData.responses.filter(
            function (response) {

                return (
                    response.rating !== null
                    &&
                    response.response_text.length > 0
                );

            }
        ).length;


    const baselineReady =
        totalItems > 0
        &&
        completeItems === totalItems;


    const summaryReady =
        assessmentData
            .overall_summary
            .length > 0;


    const evidenceCount =
        Number(
            assessmentWorkspace
                .dataset
                .evidenceCount
        );


    const evidenceReady =
        evidenceCount >= 1;


    const passedChecks = [

        baselineReady,
        summaryReady,
        evidenceReady

    ].filter(Boolean).length;


    const score =
        Math.round(
            (
                passedChecks / 3
            )
            * 100
        );


    return {

        assessmentData:
            assessmentData,

        totalItems:
            totalItems,

        completeItems:
            completeItems,

        baselineReady:
            baselineReady,

        summaryReady:
            summaryReady,

        evidenceCount:
            evidenceCount,

        evidenceReady:
            evidenceReady,

        score:
            score,

        ready:
            (
                baselineReady
                &&
                summaryReady
                &&
                evidenceReady
            )

    };

}



/* ========================================
   UPDATE CHECK CARD
======================================== */

function updateSubmissionCheck(
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



/* ========================================
   OPEN GATE
======================================== */

function openSubmissionGate() {

    const readiness =
        calculateSubmissionReadiness();


    document
        .getElementById(
            "submissionScore"
        )
        .textContent =
        `${readiness.score}%`;


    updateSubmissionCheck(
        "baselineSubmissionCheck",
        "baselineCheckText",
        readiness.baselineReady,

        `${readiness.completeItems} of ${readiness.totalItems} complete`
    );


    updateSubmissionCheck(
        "summarySubmissionCheck",
        "summaryCheckText",
        readiness.summaryReady,

        readiness.summaryReady
            ? "Summary complete"
            : "Summary required"
    );


    updateSubmissionCheck(
        "evidenceSubmissionCheck",
        "evidenceCheckText",
        readiness.evidenceReady,

        readiness.evidenceReady
            ? `${readiness.evidenceCount} file(s) attached`
            : "At least 1 file required"
    );


    const title =
        document.getElementById(
            "submissionReadinessTitle"
        );


    const message =
        document.getElementById(
            "submissionReadinessMessage"
        );


    if (readiness.ready) {

        title.textContent =
            "Assessment ready for submission";


        message.textContent =
            "All required assessment components are complete.";

    }

    else {

        title.textContent =
            "Assessment needs attention";


        message.textContent =
            "Resolve the highlighted items before submitting.";

    }


    confirmAssessmentSubmission.disabled =
        !readiness.ready;


    submissionGate.classList.add(
        "open"
    );


    submissionGateBackdrop.classList.add(
        "show"
    );


    document.body.style.overflow =
        "hidden";

}



/* ========================================
   CLOSE GATE
======================================== */

function closeSubmissionGatePanel() {

    submissionGate.classList.remove(
        "open"
    );


    submissionGateBackdrop.classList.remove(
        "show"
    );


    document.body.style.overflow =
        "";

}



if (submitAssessment) {

    submitAssessment.addEventListener(
        "click",
        openSubmissionGate
    );

}


if (closeSubmissionGate) {

    closeSubmissionGate.addEventListener(
        "click",
        closeSubmissionGatePanel
    );

}


if (returnToAssessment) {

    returnToAssessment.addEventListener(
        "click",
        closeSubmissionGatePanel
    );

}


if (submissionGateBackdrop) {

    submissionGateBackdrop.addEventListener(
        "click",
        closeSubmissionGatePanel
    );

}


/* ========================================
   SUBMIT + LOCK
======================================== */

if (
    confirmAssessmentSubmission &&
    assessmentWorkspace
) {

    confirmAssessmentSubmission.addEventListener(
        "click",
        async function () {

            console.log(
                "Submit & Lock clicked"
            );


            const readiness =
                calculateSubmissionReadiness();


            console.log(
                "Submission readiness:",
                readiness
            );


            if (!readiness.ready) {

                alert(
                    "The assessment is not ready for submission."
                );

                return;

            }


            const reviewId =
                assessmentWorkspace.dataset.reviewId;


            console.log(
                "Submitting review ID:",
                reviewId
            );


            confirmAssessmentSubmission.disabled =
                true;


            confirmAssessmentSubmission.textContent =
                "Submitting...";


            try {

                const response =
                    await fetch(
                        `/reviews/${reviewId}/self-assessment/submit`,
                        {
                            method: "POST",

                            headers: {
                                "Content-Type":
                                    "application/json"
                            },

                            body: JSON.stringify(
                                readiness.assessmentData
                            )
                        }
                    );


                console.log(
                    "Response status:",
                    response.status
                );


                const responseText =
                    await response.text();


                console.log(
                    "Server response:",
                    responseText
                );


                let data;


                try {

                    data =
                        JSON.parse(
                            responseText
                        );

                }

                catch {

                    throw new Error(
                        "The server returned an invalid response. Check the Flask terminal."
                    );

                }


                if (
                    !response.ok ||
                    !data.success
                ) {

                    throw new Error(
                        data.message ||
                        "Unable to submit assessment."
                    );

                }


                confirmAssessmentSubmission.textContent =
                    "Submitted ✓";


                setTimeout(
                    function () {

                        window.location.href =
                            data.redirect_url;

                    },
                    500
                );

            }

            catch (error) {

                console.error(
                    "Submission error:",
                    error
                );


                confirmAssessmentSubmission.disabled =
                    false;


                confirmAssessmentSubmission.textContent =
                    "Submit & Lock";


                alert(
                    error.message
                );

            }

        }
    );

}


/* ========================================
   LIVE SAVE STATUS
======================================== */

const draftSaveStatus =
    document.getElementById(
        "draftSaveStatus"
    );


function showDraftSaveStatus(
    message,
    state
) {

    if (!draftSaveStatus) {
        return;
    }


    draftSaveStatus.textContent =
        message;


    draftSaveStatus.classList.remove(
        "save-success",
        "save-error"
    );


    if (state === "success") {

        draftSaveStatus.classList.add(
            "save-success"
        );

    }


    if (state === "error") {

        draftSaveStatus.classList.add(
            "save-error"
        );

    }

}

/* ========================================
   UNSAVED CHANGE DETECTION
======================================== */

const assessmentInputs =
    document.querySelectorAll(
        `
        .assessment-item-reflection,
        #overallSummary,
        #keyAchievements,
        #challenges,
        #supportNeeded
        `
    );


assessmentInputs.forEach(
    function (input) {

        input.addEventListener(
            "input",
            function () {

                showDraftSaveStatus(
                    "Unsaved changes",
                    null
                );

            }
        );

    }
);


ratingPoints.forEach(
    function (button) {

        button.addEventListener(
            "click",
            function () {

                showDraftSaveStatus(
                    "Unsaved changes",
                    null
                );

            }
        );

    }
);