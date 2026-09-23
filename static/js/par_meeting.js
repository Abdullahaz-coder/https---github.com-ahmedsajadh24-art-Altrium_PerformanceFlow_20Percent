const parWorkspace = document.getElementById("parMeetingWorkspace");
const parDate = document.getElementById("parMeetingDate");
const parDuration = document.getElementById("parMeetingDuration");
const parManager = document.getElementById("parManagerAttends");
const parStartTime = document.getElementById("parStartTime");
const parAvailabilityMessage = document.getElementById("parAvailabilityMessage");
const checkParAvailability = document.getElementById("checkParAvailability");

if (parDate) parDate.min = new Date().toISOString().slice(0, 10);

function setParMessage(message, isError = false) {
    if (!parAvailabilityMessage) return;
    parAvailabilityMessage.textContent = message;
    parAvailabilityMessage.classList.toggle("manager-status-error", isError);
}

async function checkAvailability() {
    if (!parWorkspace || !parDate?.value || !parStartTime) {
        setParMessage("Choose a meeting date first.", true);
        return;
    }
    checkParAvailability.disabled = true;
    setParMessage("Checking shared availability...");
    try {
        const params = new URLSearchParams({date: parDate.value, duration: parDuration.value, manager_attends: String(parManager.checked)});
        const response = await fetch(`/reviews/${parWorkspace.dataset.reviewId}/par-meeting/availability?${params}`);
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.message || "Availability could not be checked.");
        parStartTime.replaceChildren(new Option(data.slots.length ? "Select an available time" : "No shared time available", ""));
        data.slots.forEach(slot => parStartTime.add(new Option(slot.label, slot.value)));
        parStartTime.disabled = !data.slots.length;
        setParMessage(data.message || `${data.slots.length} shared time slot(s) available.`);
    } catch (error) { setParMessage(error.message, true); }
    finally { checkParAvailability.disabled = false; }
}

checkParAvailability?.addEventListener("click", checkAvailability);
[parDate, parDuration, parManager].forEach(element => element?.addEventListener("change", () => {
    if (parStartTime) { parStartTime.disabled = true; parStartTime.replaceChildren(new Option("Check availability after changing attendees or duration", "")); }
}));
