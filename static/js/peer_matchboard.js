/* =========================================================
   ALTRIUM PEER MATCHBOARD
========================================================= */

const peerMatchboard =
    document.getElementById(
        "peerMatchboard"
    );


const cycleId =
    peerMatchboard
        ? peerMatchboard.dataset.cycleId
        : null;


const peerSelector =
    document.getElementById(
        "peerSelector"
    );


const peerSelectorBackdrop =
    document.getElementById(
        "peerSelectorBackdrop"
    );


const closePeerSelector =
    document.getElementById(
        "closePeerSelector"
    );


const peerSelectorTitle =
    document.getElementById(
        "peerSelectorTitle"
    );


const peerSelectorDescription =
    document.getElementById(
        "peerSelectorDescription"
    );


const peerCandidateSearch =
    document.getElementById(
        "peerCandidateSearch"
    );


let activeReviewId = null;

let activeEmployeeUserId = null;

let activeDepartment = "";



/* =========================================================
   OPEN SELECTOR
========================================================= */

document
    .querySelectorAll(
        ".open-peer-selector"
    )
    .forEach(
        function (button) {

            button.addEventListener(
                "click",
                function () {

                    activeReviewId =
                        button.dataset.reviewId;


                    activeEmployeeUserId =
                        button.dataset.employeeUserId;


                    activeDepartment =
                        (
                            button.dataset.department
                            || ""
                        ).toLowerCase();


                    const subjectName =
                        button.dataset.subjectName;


                    peerSelectorTitle.textContent =
                        `Select peer for ${subjectName}`;


                    peerSelectorDescription.textContent =
                        "Choose an active colleague with useful working context.";


                    prepareCandidatePool();


                    peerSelector.classList.add(
                        "open"
                    );


                    peerSelectorBackdrop.classList.add(
                        "show"
                    );


                    document.body.style.overflow =
                        "hidden";

                }
            );

        }
    );



/* =========================================================
   PREPARE CANDIDATES
========================================================= */

function prepareCandidatePool() {

    const candidateCards =
        document.querySelectorAll(
            ".peer-candidate-card"
        );


    candidateCards.forEach(
        function (card) {

            const userId =
                card.dataset.userId;


            const department =
                card.dataset.department;


            const assignButton =
                card.querySelector(
                    ".assign-peer-button"
                );


            const matchNote =
                card.querySelector(
                    ".candidate-match-note"
                );


            /* SELF REVIEW BLOCK */

            if (
                userId
                ===
                activeEmployeeUserId
            ) {

                card.style.display =
                    "none";

                return;

            }


            card.style.display =
                "grid";


            /* CONTEXT SIGNAL */

            if (
                activeDepartment
                &&
                department
                    === activeDepartment
            ) {

                matchNote.textContent =
                    "Same department";

                matchNote.classList.add(
                    "strong-match"
                );

            }

            else {

                matchNote.textContent =
                    "Cross-team perspective";

                matchNote.classList.remove(
                    "strong-match"
                );

            }


            assignButton.disabled =
                false;

        }
    );


    peerCandidateSearch.value =
        "";

}



/* =========================================================
   CLOSE SELECTOR
========================================================= */

function closePeerSelectorPanel() {

    peerSelector.classList.remove(
        "open"
    );


    peerSelectorBackdrop.classList.remove(
        "show"
    );


    document.body.style.overflow =
        "";


    activeReviewId =
        null;

}



if (closePeerSelector) {

    closePeerSelector.addEventListener(
        "click",
        closePeerSelectorPanel
    );

}


if (peerSelectorBackdrop) {

    peerSelectorBackdrop.addEventListener(
        "click",
        closePeerSelectorPanel
    );

}



/* =========================================================
   SEARCH
========================================================= */

if (peerCandidateSearch) {

    peerCandidateSearch.addEventListener(
        "input",
        function () {

            const query =
                peerCandidateSearch
                    .value
                    .toLowerCase()
                    .trim();


            document
                .querySelectorAll(
                    ".peer-candidate-card"
                )
                .forEach(
                    function (card) {

                        if (
                            card.dataset.userId
                            ===
                            activeEmployeeUserId
                        ) {

                            card.style.display =
                                "none";

                            return;

                        }


                        const searchable =
                            `
                            ${card.dataset.name}
                            ${card.dataset.department}
                            ${card.dataset.role}
                            `;


                        card.style.display =
                            searchable.includes(
                                query
                            )
                                ? "grid"
                                : "none";

                    }
                );

        }
    );

}



/* =========================================================
   ASSIGN REVIEWER
========================================================= */

document
    .querySelectorAll(
        ".assign-peer-button"
    )
    .forEach(
        function (button) {

            button.addEventListener(
                "click",
                async function () {

                    if (!activeReviewId) {
                        return;
                    }


                    const reviewerUserId =
                        button.dataset.userId;


                    const reviewerName =
                        button.dataset.name;


                    button.disabled =
                        true;


                    button.textContent =
                        "Assigning...";


                    try {

                        const response =
                            await fetch(
                                `/review-cycles/${cycleId}/reviews/${activeReviewId}/peers/assign`,
                                {
                                    method: "POST",

                                    headers: {
                                        "Content-Type":
                                            "application/json"
                                    },

                                    body:
                                        JSON.stringify({
                                            reviewer_user_id:
                                                reviewerUserId
                                        })
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
                                "Reviewer could not be assigned."
                            );

                        }


                        button.textContent =
                            "Assigned ✓";


                        alert(
                            `${reviewerName} assigned successfully.`
                        );


                        window.location.reload();

                    }


                    catch (error) {

                        button.disabled =
                            false;


                        button.textContent =
                            "Assign →";


                        alert(
                            error.message
                        );

                    }

                }
            );

        }
    );



/* =========================================================
   REMOVE REVIEWER
========================================================= */

document
    .querySelectorAll(
        ".remove-peer-button"
    )
    .forEach(
        function (button) {

            button.addEventListener(
                "click",
                async function () {

                    const reviewerName =
                        button.dataset.reviewerName;


                    const confirmed =
                        confirm(
                            `Remove ${reviewerName} from this peer review?`
                        );


                    if (!confirmed) {
                        return;
                    }


                    const reviewId =
                        button.dataset.reviewId;


                    const assignmentId =
                        button.dataset.assignmentId;


                    button.disabled =
                        true;


                    try {

                        const response =
                            await fetch(
                                `/review-cycles/${cycleId}/reviews/${reviewId}/peers/${assignmentId}/remove`,
                                {
                                    method: "POST"
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
                                "Reviewer could not be removed."
                            );

                        }


                        window.location.reload();

                    }


                    catch (error) {

                        button.disabled =
                            false;


                        alert(
                            error.message
                        );

                    }

                }
            );

        }
    );





/* =========================================================
   ESCAPE KEY
========================================================= */

document.addEventListener(
    "keydown",
    function (event) {

        if (
            event.key === "Escape"
            &&
            peerSelector
                .classList
                .contains("open")
        ) {

            closePeerSelectorPanel();

        }

    }
);