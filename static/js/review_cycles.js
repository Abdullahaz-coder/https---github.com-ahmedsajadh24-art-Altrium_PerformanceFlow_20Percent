const cycleSetupDrawer =
    document.getElementById("cycleSetupDrawer");


const cycleSetupBackdrop =
    document.getElementById("cycleSetupBackdrop");


const configureCycleButtons =
    document.querySelectorAll(".cycle-configure-button");


const closeCycleSetupButton =
    document.getElementById("closeCycleSetup");


const cancelCycleSetupButton =
    document.getElementById("cancelCycleSetup");


const cycleSetupForm =
    document.getElementById("cycleSetupForm");


const cycleNumberInput =
    document.getElementById("cycleNumberInput");


const cycleWindowDisplay =
    document.getElementById("cycleWindowDisplay");


const cycleSetupTitle =
    document.getElementById("cycleSetupTitle");


const cycleName =
    document.getElementById("cycleName");


const cycleStartDate =
    document.getElementById("cycleStartDate");


const cycleEndDate =
    document.getElementById("cycleEndDate");


const createCycleButton =
    document.getElementById("createCycleButton");

function openCycleSetup(cycleNumber) {

    cycleSetupForm.reset();


    cycleNumberInput.value =
        cycleNumber;


    cycleWindowDisplay.textContent =
        String(cycleNumber).padStart(
            2,
            "0"
        );


    cycleSetupTitle.textContent =
        "Configure Cycle "
        +
        String(cycleNumber).padStart(
            2,
            "0"
        );


    cycleName.placeholder =
        "e.g. Cycle "
        +
        String(cycleNumber).padStart(
            2,
            "0"
        )
        +
        " Review Window";


    cycleSetupDrawer.classList.add(
        "open"
    );


    cycleSetupBackdrop.classList.add(
        "show"
    );


    document.body.style.overflow =
        "hidden";


    setTimeout(
        function () {

            cycleName.focus();

        },
        200
    );

}



function closeCycleSetup() {

    cycleSetupDrawer.classList.remove(
        "open"
    );


    cycleSetupBackdrop.classList.remove(
        "show"
    );


    document.body.style.overflow =
        "";

}

configureCycleButtons.forEach(
    function (button) {

        button.addEventListener(
            "click",
            function () {

                const cycleNumber =
                    button.dataset.cycleNumber;


                openCycleSetup(
                    cycleNumber
                );

            }
        );

    }
);

closeCycleSetupButton.addEventListener(
    "click",
    closeCycleSetup
);


cancelCycleSetupButton.addEventListener(
    "click",
    closeCycleSetup
);


cycleSetupBackdrop.addEventListener(
    "click",
    closeCycleSetup
);


document.addEventListener(
    "keydown",
    function (event) {

        if (
            event.key === "Escape"
            &&
            cycleSetupDrawer.classList.contains(
                "open"
            )
        ) {

            closeCycleSetup();

        }

    }
);

cycleSetupForm.addEventListener(
    "submit",
    function (event) {

        const name =
            cycleName.value.trim();


        const start =
            cycleStartDate.value;


        const end =
            cycleEndDate.value;


        if (!name) {

            event.preventDefault();


            alert(
                "Please enter a cycle name."
            );


            cycleName.focus();

            return;

        }


        if (!start || !end) {

            event.preventDefault();


            alert(
                "Please select both the start and end dates."
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


        createCycleButton.disabled =
            true;


        createCycleButton.textContent =
            "Creating Draft...";

    }
);