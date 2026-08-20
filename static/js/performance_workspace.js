const composer =
    document.getElementById("performanceComposer");


const backdrop =
    document.getElementById("performanceBackdrop");


const openButtons =
    document.querySelectorAll(".lane-add-button");


const closeButton =
    document.getElementById("closePerformanceComposer");


const cancelButton =
    document.getElementById("cancelPerformanceItem");


const performanceForm =
    document.getElementById("performanceItemForm");



const itemTypeInput =
    document.getElementById("performanceItemType");


const composerEyebrow =
    document.getElementById("composerEyebrow");


const composerTitle =
    document.getElementById("composerTitle");


const composerDescription =
    document.getElementById("composerDescription");


const titleLabel =
    document.getElementById("performanceTitleLabel");


const titleInput =
    document.getElementById("performanceTitle");


const measurementSection =
    document.getElementById("measurementSection");


const targetLabel =
    document.getElementById("targetLabel");


const targetInput =
    document.getElementById("performanceTarget");


const timelineSection =
    document.getElementById("timelineSection");


const dueDateInput =
    document.getElementById("performanceDueDate");

function openComposer() {

    composer.classList.add("open");

    backdrop.classList.add("show");

    document.body.style.overflow = "hidden";

}


function closeComposer() {

    composer.classList.remove("open");

    backdrop.classList.remove("show");

    document.body.style.overflow = "";

}

function configureComposer(type) {

    performanceForm.reset();

    itemTypeInput.value = type;


    measurementSection.style.display = "none";

    timelineSection.style.display = "none";


    if (type === "Responsibility") {

        composerEyebrow.textContent =
            "NEW RESPONSIBILITY";

        composerTitle.textContent =
            "Create Responsibility";

        composerDescription.textContent =
            "Define what this employee is accountable for.";

        titleLabel.textContent =
            "Responsibility";

        titleInput.placeholder =
            "e.g. Maintain backend services";

    }


    else if (type === "Expectation") {

        composerEyebrow.textContent =
            "NEW EXPECTATION";

        composerTitle.textContent =
            "Create Expectation";

        composerDescription.textContent =
            "Define the professional standard or behaviour expected.";

        titleLabel.textContent =
            "Expectation";

        titleInput.placeholder =
            "e.g. Communicate blockers early";

    }


    else if (type === "KPI") {

        composerEyebrow.textContent =
            "NEW KPI";

        composerTitle.textContent =
            "Create Key Performance Indicator";

        composerDescription.textContent =
            "Create a measurable indicator of successful performance.";

        titleLabel.textContent =
            "KPI";

        titleInput.placeholder =
            "e.g. Ticket resolution rate";

        targetLabel.textContent =
            "Performance Target";

        targetInput.placeholder =
            "e.g. 90% within SLA";

        measurementSection.style.display =
            "block";

    }


    else if (type === "Goal") {

        composerEyebrow.textContent =
            "NEW PERFORMANCE GOAL";

        composerTitle.textContent =
            "Create Goal";

        composerDescription.textContent =
            "Define an achievement or development objective.";

        titleLabel.textContent =
            "Goal";

        titleInput.placeholder =
            "e.g. Complete AWS certification";

        targetLabel.textContent =
            "Target / Outcome";

        targetInput.placeholder =
            "e.g. Certification completed";

        measurementSection.style.display =
            "block";

        timelineSection.style.display =
            "block";

    }


    openComposer();

}

openButtons.forEach(function (button) {

    button.addEventListener(
        "click",
        function () {

            const itemType =
                button.dataset.type;

            configureComposer(
                itemType
            );

        }
    );

});

closeButton.addEventListener(
    "click",
    closeComposer
);


cancelButton.addEventListener(
    "click",
    closeComposer
);


backdrop.addEventListener(
    "click",
    closeComposer
);


document.addEventListener(
    "keydown",
    function (event) {

        if (event.key === "Escape") {

            closeComposer();

        }

    }
);

const createItemButton =
    document.getElementById("createPerformanceItem");


performanceForm.addEventListener(
    "submit",
    function (event) {

        const type =
            itemTypeInput.value;


        const title =
            titleInput.value.trim();


        if (!title) {

            event.preventDefault();

            alert(
                "Please enter a title."
            );

            return;

        }


        if (
            type === "KPI"
            &&
            !targetInput.value.trim()
        ) {

            event.preventDefault();

            alert(
                "Please enter a KPI target."
            );

            return;

        }


        createItemButton.disabled = true;


        createItemButton.textContent =
            "Creating...";

    }
);

/* ========================================
   PERFORMANCE INSPECTOR
======================================== */

const inspectableItems =
    document.querySelectorAll(".inspectable-item");


const inspector =
    document.getElementById("performanceInspector");


const inspectorBackdrop =
    document.getElementById("inspectorBackdrop");


const closeInspectorButton =
    document.getElementById("closeInspector");


const inspectorEyebrow =
    document.getElementById("inspectorEyebrow");


const inspectorTitle =
    document.getElementById("inspectorTitle");


const inspectorType =
    document.getElementById("inspectorType");


const inspectorDescription =
    document.getElementById("inspectorDescription");


const inspectorDescriptionBlock =
    document.getElementById("inspectorDescriptionBlock");


const inspectorTarget =
    document.getElementById("inspectorTarget");


const inspectorTargetBlock =
    document.getElementById("inspectorTargetBlock");


const inspectorDueDate =
    document.getElementById("inspectorDueDate");


const inspectorDueDateBlock =
    document.getElementById("inspectorDueDateBlock");


const showInspectorEdit =
    document.getElementById("showInspectorEdit");


const inspectorEditForm =
    document.getElementById("inspectorEditForm");


const cancelInspectorEdit =
    document.getElementById("cancelInspectorEdit");


const inspectorEditTitle =
    document.getElementById("inspectorEditTitle");


const inspectorEditDescription =
    document.getElementById("inspectorEditDescription");


const inspectorEditTarget =
    document.getElementById("inspectorEditTarget");


const inspectorEditTargetGroup =
    document.getElementById("inspectorEditTargetGroup");


const inspectorEditDueDate =
    document.getElementById("inspectorEditDueDate");


const inspectorEditDateGroup =
    document.getElementById("inspectorEditDateGroup");


const archivePerformanceForm =
    document.getElementById("archivePerformanceForm");


const saveInspectorItem =
    document.getElementById("saveInspectorItem");

const performanceHistoryTimeline =
    document.getElementById(
        "performanceHistoryTimeline"
    );

function openInspector(item) {

    const type =
        item.dataset.type;


    const title =
        item.dataset.title;


    const description =
        item.dataset.description;


    const target =
        item.dataset.target;


    const dueDate =
        item.dataset.dueDate;


    const historyUrl =
        item.dataset.historyUrl;

    loadPerformanceHistory(
        historyUrl
     );   

    inspectorEyebrow.textContent =
        type.toUpperCase() + " INSPECTOR";


    inspectorTitle.textContent =
        title;


    inspectorType.textContent =
        type;


    inspectorDescription.textContent =
        description || "No description provided.";


    inspectorEditTitle.value =
        title;


    inspectorEditDescription.value =
        description;


    inspectorEditTarget.value =
        target;


    inspectorEditDueDate.value =
        dueDate;


    inspectorEditForm.action =
        item.dataset.editUrl;


    archivePerformanceForm.action =
        item.dataset.archiveUrl;


    /* =====================================
       TYPE-SPECIFIC DETAILS
    ===================================== */

    inspectorTargetBlock.style.display =
        "none";


    inspectorDueDateBlock.style.display =
        "none";


    inspectorEditTargetGroup.style.display =
        "none";


    inspectorEditDateGroup.style.display =
        "none";


    if (type === "KPI") {

        inspectorTargetBlock.style.display =
            "block";


        inspectorTarget.textContent =
            target || "No target";


        inspectorEditTargetGroup.style.display =
            "flex";

    }


    if (type === "Goal") {

        inspectorTargetBlock.style.display =
            "block";


        inspectorTarget.textContent =
            target || "No target";


        inspectorDueDateBlock.style.display =
            "block";


        inspectorDueDate.textContent =
            dueDate || "No due date";


        inspectorEditTargetGroup.style.display =
            "flex";


        inspectorEditDateGroup.style.display =
            "flex";

    }


    inspectorEditForm.classList.remove(
        "show"
    );


    showInspectorEdit.style.display =
        "block";


    inspector.classList.add("open");


    inspectorBackdrop.classList.add(
        "show"
    );


    document.body.style.overflow =
        "hidden";

}



function closePerformanceInspector() {

    inspector.classList.remove(
        "open"
    );


    inspectorBackdrop.classList.remove(
        "show"
    );


    inspectorEditForm.classList.remove(
        "show"
    );


    document.body.style.overflow =
        "";

}

/* ========================================
   DATE FORMATTER
======================================== */

function formatHistoryDate(timestamp) {

    if (!timestamp) {

        return "Unknown time";

    }


    const utcTimestamp =
        timestamp.replace(
            " ",
            "T"
        ) + "Z";


    const date =
        new Date(utcTimestamp);


    if (Number.isNaN(date.getTime())) {

        return timestamp;

    }


    return date.toLocaleString(
        undefined,
        {
            day: "numeric",
            month: "short",
            year: "numeric",
            hour: "numeric",
            minute: "2-digit"
        }
    );

}

function escapeHistoryText(value) {

    const element =
        document.createElement("div");


    element.textContent =
        value || "";


    return element.innerHTML;

}

function renderPerformanceHistory(history) {

    if (!history.length) {

        performanceHistoryTimeline.innerHTML = `
            <div class="history-empty">
                No activity history has been recorded yet.
            </div>
        `;

        return;

    }


    performanceHistoryTimeline.innerHTML =
        history.map(
            function (record) {

                const actionClass =
                    record.action.toLowerCase();


                let marker = "•";


                if (record.action === "Created") {

                    marker = "+";

                }


                else if (record.action === "Updated") {

                    marker = "↻";

                }


                else if (record.action === "Archived") {

                    marker = "–";

                }


                let metadata = "";


                if (record.target) {

                    metadata += `
                        <span>
                            Target:
                            ${escapeHistoryText(record.target)}
                        </span>
                    `;

                }


                if (record.due_date) {

                    metadata += `
                        <span>
                            Due:
                            ${escapeHistoryText(record.due_date)}
                        </span>
                    `;

                }


                return `
                    <div class="history-event ${actionClass}">


                        <div class="history-marker">
                            ${marker}
                        </div>


                        <div class="history-content">


                            <div class="history-event-top">

                                <span class="history-action">
                                    ${escapeHistoryText(record.action)}
                                </span>


                                <span class="history-time">
                                    ${formatHistoryDate(record.performed_at)}
                                </span>

                            </div>


                            <div class="history-person">

                                by
                                ${escapeHistoryText(record.performed_by)}

                            </div>


                            <div class="history-snapshot">


                                <strong>
                                    ${escapeHistoryText(record.title)}
                                </strong>


                                ${
                                    record.description

                                    ? `
                                        <p>
                                            ${escapeHistoryText(record.description)}
                                        </p>
                                    `

                                    : ""
                                }


                                ${
                                    metadata

                                    ? `
                                        <div class="history-meta">
                                            ${metadata}
                                        </div>
                                    `

                                    : ""
                                }


                            </div>


                        </div>


                    </div>
                `;

            }

        ).join("");

}

async function loadPerformanceHistory(url) {

    performanceHistoryTimeline.innerHTML = `
        <div class="history-loading">

            <span class="history-loading-dot"></span>

            Loading activity...

        </div>
    `;


    try {

        const response =
            await fetch(url);


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
                "Unable to load history."
            );

        }


        renderPerformanceHistory(
            data.history
        );

    }


    catch (error) {

        performanceHistoryTimeline.innerHTML = `
            <div class="history-error">
                Unable to load activity history.
            </div>
        `;


        console.error(
            "History loading error:",
            error
        );

    }

}


inspectableItems.forEach(
    function (item) {

        item.addEventListener(
            "click",
            function () {

                openInspector(item);

            }
        );

    }
);



closeInspectorButton.addEventListener(
    "click",
    closePerformanceInspector
);



inspectorBackdrop.addEventListener(
    "click",
    closePerformanceInspector
);



showInspectorEdit.addEventListener(
    "click",
    function () {

        inspectorEditForm.classList.add(
            "show"
        );


        showInspectorEdit.style.display =
            "none";

    }
);



cancelInspectorEdit.addEventListener(
    "click",
    function () {

        inspectorEditForm.classList.remove(
            "show"
        );


        showInspectorEdit.style.display =
            "block";

    }
);

inspectorEditForm.addEventListener(
    "submit",
    function (event) {

        const type =
            inspectorType.textContent;


        const title =
            inspectorEditTitle.value.trim();


        if (!title) {

            event.preventDefault();


            alert(
                "Please enter a title."
            );


            return;

        }


        if (
            type === "KPI"
            &&
            !inspectorEditTarget.value.trim()
        ) {

            event.preventDefault();


            alert(
                "A KPI must include a performance target."
            );


            return;

        }


        saveInspectorItem.disabled =
            true;


        saveInspectorItem.textContent =
            "Saving...";

    }
);

archivePerformanceForm.addEventListener(
    "submit",
    function (event) {

        const confirmed =
            confirm(
                "Archive this item? It will be removed from the active performance plan but retained in the audit history."
            );


        if (!confirmed) {

            event.preventDefault();

        }

    }
);