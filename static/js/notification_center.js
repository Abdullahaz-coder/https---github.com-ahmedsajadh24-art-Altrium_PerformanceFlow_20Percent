/* =========================================================
   ALTRIUM SIGNAL CENTER
========================================================= */


const notificationButton =
    document.getElementById(
        "notificationButton"
    );


const notificationBadge =
    document.getElementById(
        "notificationBadge"
    );


const signalCenter =
    document.getElementById(
        "signalCenter"
    );


const signalCenterBackdrop =
    document.getElementById(
        "signalCenterBackdrop"
    );


const closeSignalCenter =
    document.getElementById(
        "closeSignalCenter"
    );


const signalFeed =
    document.getElementById(
        "signalFeed"
    );


const markAllSignals =
    document.getElementById(
        "markAllSignals"
    );



/* ========================================
   ESCAPE USER DATA
======================================== */

function escapeSignalText(value) {

    const element =
        document.createElement("div");


    element.textContent =
        value || "";


    return element.innerHTML;

}



/* ========================================
   TIME FORMAT
======================================== */

function formatSignalTime(timestamp) {

    if (!timestamp) {

        return "";

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


    const now =
        new Date();


    const difference =
        now - date;


    const minutes =
        Math.floor(
            difference / 60000
        );


    if (minutes < 1) {

        return "Just now";

    }


    if (minutes < 60) {

        return `${minutes} min ago`;

    }


    const hours =
        Math.floor(
            minutes / 60
        );


    if (hours < 24) {

        return `${hours} hr ago`;

    }


    return date.toLocaleDateString(
        undefined,
        {
            day: "numeric",
            month: "short",
            year: "numeric"
        }
    );

}



/* ========================================
   BADGE
======================================== */

function updateNotificationBadge(count) {

    if (!notificationBadge) {

        return;

    }


    if (count <= 0) {

        notificationBadge.style.display =
            "none";

        return;

    }


    notificationBadge.style.display =
        "flex";


    notificationBadge.textContent =
        count > 9
            ? "9+"
            : count;

}



/* ========================================
   SIGNAL TYPE ICON
======================================== */

function signalIcon(type) {

    if (type === "REVIEW_STARTED") {
        return "▶";
    }


    if (type === "REVIEW_ASSIGNED") {
        return "↗";
    }


    if (type === "CYCLE_ACTIVATED") {
        return "◎";
    }


    return "◇";

}



/* ========================================
   RENDER SIGNALS
======================================== */

function renderSignals(notifications) {

    if (!notifications.length) {

        signalFeed.innerHTML = `
            <div class="signal-empty">

                <div class="signal-empty-icon">
                    ✓
                </div>

                <strong>
                    You're up to date
                </strong>

                <p>
                    No review signals have been generated yet.
                </p>

            </div>
        `;

        return;

    }


    signalFeed.innerHTML =
        notifications.map(
            function (notification) {

                const unreadClass =
                    notification.is_read
                        ? ""
                        : "unread";


                return `
                    <article
                        class="signal-item ${unreadClass}"
                        data-id="${notification.id}"
                    >

                        <div class="signal-icon">

                            ${signalIcon(notification.type)}

                        </div>


                        <div class="signal-content">


                            <div class="signal-meta">

                                <span class="signal-type">

                                    ${escapeSignalText(
                                        notification.type
                                            .replaceAll("_", " ")
                                    )}

                                </span>


                                ${
                                    notification.cycle_name

                                    ? `
                                        <span class="signal-cycle">

                                            ${escapeSignalText(
                                                notification.cycle_name
                                            )}

                                        </span>
                                    `

                                    : ""
                                }

                            </div>


                            <h3>

                                ${escapeSignalText(
                                    notification.title
                                )}

                            </h3>


                            <p>

                                ${escapeSignalText(
                                    notification.message
                                )}

                            </p>


                            <span class="signal-time">

                                ${formatSignalTime(
                                    notification.created_at
                                )}

                            </span>


                        </div>


                    </article>
                `;

            }

        ).join("");


    connectSignalClicks();

}



/* ========================================
   LOAD FEED
======================================== */

async function loadSignalFeed() {

    try {

        const response =
            await fetch(
                "/notifications/feed"
            );


        const data =
            await response.json();


        if (
            !response.ok
            ||
            !data.success
        ) {

            throw new Error(
                "Unable to load notifications."
            );

        }


        updateNotificationBadge(
            data.unread_count
        );


        renderSignals(
            data.notifications
        );

    }


    catch (error) {

        signalFeed.innerHTML = `
            <div class="signal-error">
                Signal Center could not be loaded.
            </div>
        `;


        console.error(
            "Signal Center:",
            error
        );

    }

}



/* ========================================
   MARK ONE READ
======================================== */

function connectSignalClicks() {

    const items =
        document.querySelectorAll(
            ".signal-item"
        );


    items.forEach(
        function (item) {

            item.addEventListener(
                "click",
                async function () {

                    if (
                        !item.classList.contains(
                            "unread"
                        )
                    ) {

                        return;

                    }


                    const notificationId =
                        item.dataset.id;


                    const response =
                        await fetch(
                            `/notifications/${notificationId}/read`,
                            {
                                method: "POST"
                            }
                        );


                    if (response.ok) {

                        await loadSignalFeed();

                    }

                }
            );

        }
    );

}



/* ========================================
   OPEN / CLOSE
======================================== */

function openSignalCenter() {

    signalCenter.classList.add(
        "open"
    );


    signalCenterBackdrop.classList.add(
        "show"
    );


    document.body.style.overflow =
        "hidden";


    loadSignalFeed();

}



function closeSignalCenterPanel() {

    signalCenter.classList.remove(
        "open"
    );


    signalCenterBackdrop.classList.remove(
        "show"
    );


    document.body.style.overflow =
        "";

}



if (notificationButton) {

    notificationButton.addEventListener(
        "click",
        openSignalCenter
    );

}


if (closeSignalCenter) {

    closeSignalCenter.addEventListener(
        "click",
        closeSignalCenterPanel
    );

}


if (signalCenterBackdrop) {

    signalCenterBackdrop.addEventListener(
        "click",
        closeSignalCenterPanel
    );

}



/* ========================================
   MARK ALL READ
======================================== */

if (markAllSignals) {

    markAllSignals.addEventListener(
        "click",
        async function () {

            const response =
                await fetch(
                    "/notifications/read-all",
                    {
                        method: "POST"
                    }
                );


            if (response.ok) {

                await loadSignalFeed();

            }

        }
    );

}



/* ========================================
   ESCAPE KEY
======================================== */

document.addEventListener(
    "keydown",
    function (event) {

        if (
            event.key === "Escape"
            &&
            signalCenter.classList.contains(
                "open"
            )
        ) {

            closeSignalCenterPanel();

        }

    }
);



/* ========================================
   LIGHTWEIGHT LIVE REFRESH
======================================== */

setInterval(
    function () {

        if (
            document.visibilityState
            === "visible"
        ) {

            loadSignalFeed();

        }

    },
    60000
);