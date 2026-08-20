const employeeDrawer =
    document.getElementById("employeeDrawer");


const drawerBackdrop =
    document.getElementById("drawerBackdrop");


const openDrawerButtons =
    document.querySelectorAll(".open-employee-drawer");


const closeDrawerButton =
    document.getElementById("closeEmployeeDrawer");


const cancelEmployeeButton =
    document.getElementById("cancelEmployeeButton");



function openEmployeeDrawer() {

    employeeDrawer.classList.add("open");

    drawerBackdrop.classList.add("show");

    document.body.style.overflow = "hidden";

}



function closeEmployeeDrawer() {

    employeeDrawer.classList.remove("open");

    drawerBackdrop.classList.remove("show");

    document.body.style.overflow = "";

}



openDrawerButtons.forEach(function (button) {

    button.addEventListener(
        "click",
        openEmployeeDrawer
    );

});



closeDrawerButton.addEventListener(
    "click",
    closeEmployeeDrawer
);



cancelEmployeeButton.addEventListener(
    "click",
    closeEmployeeDrawer
);



drawerBackdrop.addEventListener(
    "click",
    closeEmployeeDrawer
);

document.addEventListener(
    "keydown",
    function (event) {

        if (event.key === "Escape") {

            closeEmployeeDrawer();

        }

    }
);

const employeeForm =
    document.getElementById("employeeForm");


const createEmployeeButton =
    document.getElementById("createEmployeeButton");


employeeForm.addEventListener(
    "submit",
    function (event) {

        const fullName =
            document.getElementById("fullName").value.trim();

        const employeeCode =
            document.getElementById("employeeCode").value.trim();

        const email =
            document.getElementById("employeeEmail").value.trim();

        const hireDate =
            document.getElementById("hireDate").value;

        const department =
            document.getElementById("department").value;

        const jobTitle =
            document.getElementById("jobTitle").value.trim();

        const password =
            document.getElementById("temporaryPassword").value;


        if (
            !fullName ||
            !employeeCode ||
            !email ||
            !hireDate ||
            !department ||
            !jobTitle ||
            !password
        ) {

            event.preventDefault();

            alert(
                "Please complete all required fields."
            );

            return;
        }


        if (password.length < 8) {

            event.preventDefault();

            alert(
                "Temporary password must contain at least 8 characters."
            );

            return;
        }


        createEmployeeButton.disabled = true;

        createEmployeeButton.textContent =
            "Creating Profile...";

    }
);