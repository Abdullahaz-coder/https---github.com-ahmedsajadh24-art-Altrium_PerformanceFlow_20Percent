# Altrium PerformanceFlow

Altrium PerformanceFlow is a Flask-based employee performance-review system. It supports HR review-cycle administration, employee self-assessments, performance blueprints, confidential peer reviews, supervisor evaluations, final management approvals, employee outcome acknowledgement, evidence uploads, workflow actions, and notifications.

## Technology

- Python and Flask
- SQLite
- Jinja HTML templates
- Vanilla JavaScript
- Custom CSS

## Local setup

1. Create a virtual environment:

   ```powershell
   python -m venv .venv
   ```

2. Activate it:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the dependency:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Create or update the local database:

   ```powershell
   python init_db.py
   ```

   On a new database, the command prints randomly generated bootstrap
   passwords for the HR, Supervisor, and Manager accounts. Save them in a
   password manager. You can instead define the password variables shown in
   `.env.example` before running the command.

5. Start the application:

   ```powershell
   python app.py
   ```

6. Open `http://127.0.0.1:5000`.

The database and uploaded evidence are intentionally excluded from Git. Every developer should create a separate local database with `init_db.py`.

## Deployment security

Before deployment, set a long random `PERFORMANCEFLOW_SECRET_KEY`, keep
`PERFORMANCEFLOW_DEBUG=0`, and set `PERFORMANCEFLOW_SECURE_COOKIES=1` when the
site is served over HTTPS. The application includes CSRF protection, protected
session cookies, sign-in throttling, and an in-app password-change screen.
Never copy the local `database.db` or the `instance/evidence` folder into a
public repository.

## Team workflow

Before starting new work, pull the latest changes from GitHub. Create a separate branch for each feature or fix, commit focused changes, push the branch, and open a pull request for teammate review before merging into `main`.

Do not commit local databases, uploaded evidence, passwords, environment files, or virtual-environment folders.
