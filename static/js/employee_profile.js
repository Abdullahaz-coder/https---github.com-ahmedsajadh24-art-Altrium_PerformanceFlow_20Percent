const editProfileButton =
    document.getElementById("editProfileButton");


const editDrawer =
    document.getElementById("editEmployeeDrawer");


const editBackdrop =
    document.getElementById("editDrawerBackdrop");


const closeEditButton =
    document.getElementById("closeEditDrawer");


const cancelEditButton =
    document.getElementById("cancelEditButton");


const editEmployeeForm =
    document.getElementById("editEmployeeForm");


const saveProfileButton =
    document.getElementById("saveProfileButton");



function openEditDrawer() {

    editDrawer.classList.add("open");

    editBackdrop.classList.add("show");

    document.body.style.overflow = "hidden";

}



function closeEditDrawer() {

    editDrawer.classList.remove("open");

    editBackdrop.classList.remove("show");

    document.body.style.overflow = "";

}



editProfileButton.addEventListener(
    "click",
    openEditDrawer
);



closeEditButton.addEventListener(
    "click",
    closeEditDrawer
);



cancelEditButton.addEventListener(
    "click",
    closeEditDrawer
);



editBackdrop.addEventListener(
    "click",
    closeEditDrawer
);



document.addEventListener(
    "keydown",
    function (event) {

        if (event.key === "Escape") {

            closeEditDrawer();

        }

    }
);


editEmployeeForm.addEventListener(
    "submit",
    function (event) {

        const fullName =
            document.getElementById("editFullName").value.trim();


        const employeeCode =
            document.getElementById("editEmployeeCode").value.trim();


        const email =
            document.getElementById("editEmail").value.trim();


        const hireDate =
            document.getElementById("editHireDate").value;


        const department =
            document.getElementById("editDepartment").value;


        const jobTitle =
            document.getElementById("editJobTitle").value.trim();


        const status =
            document.getElementById("editStatus").value;



        if (
            !fullName ||
            !employeeCode ||
            !email ||
            !hireDate ||
            !department ||
            !jobTitle ||
            !status
        ) {

            event.preventDefault();

            alert(
                "Please complete all required fields."
            );

            return;
        }



        if (!/^[^@\s]+@altrium\.com$/i.test(email)) {

            event.preventDefault();

            alert(
                "Please enter a valid @altrium.com email address."
            );

            return;
        }



        saveProfileButton.disabled = true;

        saveProfileButton.textContent =
            "Saving...";

    }
);
const editDepartmentSelect = document.getElementById("editDepartment");
const editSupervisorSelect = document.getElementById("editSupervisor");
const editSupervisorOptions = Array.from(editSupervisorSelect?.options || []).slice(1);

function filterEditSupervisors() {
    if (!editSupervisorSelect) return;
    const selected = editSupervisorSelect.value;
    const matching = editSupervisorOptions.filter(option => option.dataset.department === editDepartmentSelect?.value);
    editSupervisorSelect.replaceChildren(new Option(
        matching.length ? "Not assigned yet" : "No active supervisors in this department", ""
    ), ...matching);
    editSupervisorSelect.value = matching.some(option => option.value === selected) ? selected : "";
}
editDepartmentSelect?.addEventListener("change", filterEditSupervisors);
filterEditSupervisors();
