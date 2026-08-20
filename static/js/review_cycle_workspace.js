const employeeCheckboxes =
    document.querySelectorAll(
        ".cycle-employee-checkbox"
    );


const selectAllCheckbox =
    document.getElementById(
        "selectAllCycleEmployees"
    );


const selectedEmployeeCount =
    document.getElementById(
        "selectedEmployeeCount"
    );


const assignEmployeesButton =
    document.getElementById(
        "assignEmployeesButton"
    );


const employeeSearch =
    document.getElementById(
        "cycleEmployeeSearch"
    );


const eligibleEmployees =
    document.querySelectorAll(
        ".eligible-employee"
    );



function updateSelectionCount() {

    const selected =
        document.querySelectorAll(
            ".cycle-employee-checkbox:checked"
        );


    selectedEmployeeCount.textContent =
        selected.length;


    assignEmployeesButton.disabled =
        selected.length === 0;

}



employeeCheckboxes.forEach(
    function (checkbox) {

        checkbox.addEventListener(
            "change",
            updateSelectionCount
        );

    }
);



if (selectAllCheckbox) {

    selectAllCheckbox.addEventListener(
        "change",
        function () {

            eligibleEmployees.forEach(
                function (employee) {

                    if (
                        employee.style.display
                        !== "none"
                    ) {

                        const checkbox =
                            employee.querySelector(
                                ".cycle-employee-checkbox"
                            );


                        checkbox.checked =
                            selectAllCheckbox.checked;

                    }

                }
            );


            updateSelectionCount();

        }
    );

}



if (employeeSearch) {

    employeeSearch.addEventListener(
        "input",
        function () {

            const searchValue =
                employeeSearch.value
                    .trim()
                    .toLowerCase();


            eligibleEmployees.forEach(
                function (employee) {

                    const searchableText =
                        employee.dataset.search;


                    const matches =
                        searchableText.includes(
                            searchValue
                        );


                    employee.style.display =
                        matches
                        ? "grid"
                        : "none";

                }
            );

        }
    );

}



/* ========================================
   REMOVE CONFIRMATION
======================================== */

const removeAssignmentForms =
    document.querySelectorAll(
        ".remove-cycle-assignment-form"
    );


removeAssignmentForms.forEach(
    function (form) {

        form.addEventListener(
            "submit",
            function (event) {

                const confirmed =
                    confirm(
                        "Remove this employee from the draft review cycle?"
                    );


                if (!confirmed) {

                    event.preventDefault();

                }

            }
        );

    }
);

/* ========================================
   SCHEDULE CYCLE CONFIRMATION
======================================== */

const scheduleCycleForm =
    document.getElementById(
        "scheduleCycleForm"
    );


if (scheduleCycleForm) {

    scheduleCycleForm.addEventListener(
        "submit",
        function (event) {

            const confirmed =
                confirm(
                    "Schedule this review cycle? Employee assignments will be locked once the cycle leaves Draft."
                );


            if (!confirmed) {

                event.preventDefault();

            }

        }
    );

}

/* ========================================
   ACTIVATE CYCLE CONFIRMATION
======================================== */

const activateCycleForm =
    document.getElementById(
        "activateCycleForm"
    );


if (activateCycleForm) {

    activateCycleForm.addEventListener(
        "submit",
        function (event) {

            const confirmed =
                confirm(
                    "Activate this review cycle? Each employee's current Performance Blueprint will be frozen as the review baseline."
                );


            if (!confirmed) {

                event.preventDefault();

            }

        }
    );

}

const returnCycleToDraftForm =
    document.getElementById(
        "returnCycleToDraftForm"
    );


if (returnCycleToDraftForm) {

    returnCycleToDraftForm.addEventListener(
        "submit",
        function (event) {

            const confirmed =
                confirm(
                    "Return this cycle to Draft? The employee cohort will become editable again."
                );


            if (!confirmed) {
                event.preventDefault();
            }

        }
    );

}

/* ========================================
   EDIT CYCLE CONFIGURATION
======================================== */

const cycleEditDrawer =
    document.getElementById(
        "cycleEditDrawer"
    );


const cycleEditBackdrop =
    document.getElementById(
        "cycleEditBackdrop"
    );


const openCycleEdit =
    document.getElementById(
        "openCycleEdit"
    );


const closeCycleEdit =
    document.getElementById(
        "closeCycleEdit"
    );


const cancelCycleEdit =
    document.getElementById(
        "cancelCycleEdit"
    );


const cycleEditForm =
    document.getElementById(
        "cycleEditForm"
    );


const editCycleName =
    document.getElementById(
        "editCycleName"
    );


const editCycleStart =
    document.getElementById(
        "editCycleStart"
    );


const editCycleEnd =
    document.getElementById(
        "editCycleEnd"
    );


const saveCycleEdit =
    document.getElementById(
        "saveCycleEdit"
    );



function openCycleEditDrawer() {

    cycleEditDrawer.classList.add(
        "open"
    );


    cycleEditBackdrop.classList.add(
        "show"
    );


    document.body.style.overflow =
        "hidden";

}



function closeCycleEditDrawer() {

    cycleEditDrawer.classList.remove(
        "open"
    );


    cycleEditBackdrop.classList.remove(
        "show"
    );


    document.body.style.overflow =
        "";

}



if (openCycleEdit) {

    openCycleEdit.addEventListener(
        "click",
        openCycleEditDrawer
    );

}


if (closeCycleEdit) {

    closeCycleEdit.addEventListener(
        "click",
        closeCycleEditDrawer
    );

}


if (cancelCycleEdit) {

    cancelCycleEdit.addEventListener(
        "click",
        closeCycleEditDrawer
    );

}


if (cycleEditBackdrop) {

    cycleEditBackdrop.addEventListener(
        "click",
        closeCycleEditDrawer
    );

}

if (cycleEditForm) {

    cycleEditForm.addEventListener(
        "submit",
        function (event) {

            const name =
                editCycleName.value.trim();


            const start =
                editCycleStart.value;


            const end =
                editCycleEnd.value;


            if (!name) {

                event.preventDefault();

                alert(
                    "Please enter a cycle name."
                );

                return;

            }


            if (!start || !end) {

                event.preventDefault();

                alert(
                    "Please provide both review cycle dates."
                );

                return;

            }


            if (start >= end) {

                event.preventDefault();

                alert(
                    "The end date must be after the start date."
                );

                return;

            }


            saveCycleEdit.disabled =
                true;


            saveCycleEdit.textContent =
                "Saving...";

        }
    );

}