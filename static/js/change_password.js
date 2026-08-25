document.addEventListener("DOMContentLoaded", () => {
    const currentPassword = document.getElementById("currentPassword");
    const newPassword = document.getElementById("newPassword");
    const confirmPassword = document.getElementById("confirmPassword");

    document.querySelectorAll("[data-password-toggle]").forEach((button) => {
        button.addEventListener("click", () => {
            const input = document.getElementById(button.dataset.passwordToggle);
            const isVisible = input.type === "text";

            input.type = isVisible ? "password" : "text";
            button.textContent = isVisible ? "Show" : "Hide";
            button.setAttribute(
                "aria-label",
                `${isVisible ? "Show" : "Hide"} ${input.labels[0].textContent.toLowerCase()}`
            );
        });
    });

    const setRuleState = (name, isMet) => {
        const rule = document.querySelector(`[data-password-rule="${name}"]`);
        rule.classList.toggle("is-met", isMet);
    };

    const refreshRequirements = () => {
        const currentValue = currentPassword.value;
        const newValue = newPassword.value;
        const confirmationValue = confirmPassword.value;

        setRuleState("length", newValue.length >= 12);
        setRuleState(
            "different",
            newValue.length > 0 && currentValue.length > 0 && newValue !== currentValue
        );
        setRuleState(
            "match",
            newValue.length > 0 && confirmationValue.length > 0 && newValue === confirmationValue
        );
    };

    [currentPassword, newPassword, confirmPassword].forEach((input) => {
        input.addEventListener("input", refreshRequirements);
    });
});
