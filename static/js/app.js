const passwordInput = document.getElementById("password");

const togglePassword = document.getElementById("togglePassword");


togglePassword?.addEventListener("click", function () {

    if (passwordInput.type === "password") {

        passwordInput.type = "text";

        togglePassword.textContent = "Hide";
        togglePassword.setAttribute("aria-label", "Hide password");
        togglePassword.setAttribute("aria-pressed", "true");

    } else {

        passwordInput.type = "password";

        togglePassword.textContent = "Show";
        togglePassword.setAttribute("aria-label", "Show password");
        togglePassword.setAttribute("aria-pressed", "false");

    }

});

const loginForm = document.getElementById("loginForm");

const emailInput = document.getElementById("email");

const emailError = document.getElementById("emailError");

const passwordError = document.getElementById("passwordError");

function validateEmail() {

    const email = emailInput.value.trim();

    if (email === "") {

        emailError.textContent = "Email address is required.";

        emailInput.classList.add("input-error");

        emailInput.classList.remove("input-success");

        return false;
    }

    if (!/^[^@\s]+@altrium\.com$/i.test(email)) {

        emailError.textContent =
            "Please use your @altrium.com email address.";

        emailInput.classList.add("input-error");

        emailInput.classList.remove("input-success");

        return false;
    }

    emailError.textContent = "";

    emailInput.classList.remove("input-error");

    emailInput.classList.add("input-success");

    return true;
}

function validatePassword() {

    const password = passwordInput.value;

    if (password === "") {

        passwordError.textContent = "Password is required.";

        passwordInput.classList.add("input-error");

        passwordInput.classList.remove("input-success");

        return false;
    }

    if (password.length < 6) {

        passwordError.textContent =
            "Password must contain at least 6 characters.";

        passwordInput.classList.add("input-error");

        passwordInput.classList.remove("input-success");

        return false;
    }

    passwordError.textContent = "";

    passwordInput.classList.remove("input-error");

    passwordInput.classList.add("input-success");

    return true;
}

loginForm.addEventListener("submit", function (event) {

    const emailValid = validateEmail();

    const passwordValid = validatePassword();

    if (!emailValid || !passwordValid) {

        event.preventDefault();

        return;
    }

    loginButton.disabled = true;

    loginButton.classList.add("is-loading");

    loginButton.querySelector("span").textContent = "Signing in...";

    loginButton.querySelector("strong").textContent = "···";

});

emailInput.addEventListener("input", function () {

    validateEmail();

});


passwordInput.addEventListener("input", function () {

    validatePassword();

});

const loginButton = document.getElementById("loginButton");
