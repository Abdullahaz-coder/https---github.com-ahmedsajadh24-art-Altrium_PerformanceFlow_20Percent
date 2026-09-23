"""Start a disposable copy of PerformanceFlow for browser tests."""

import importlib.util
import os
import runpy
import shutil
import sys
import tempfile
from pathlib import Path


project_root = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix="performanceflow-e2e-") as directory:
    scratch = Path(directory)
    for filename in ("app.py", "database.py", "init_db.py"):
        shutil.copy2(project_root / filename, scratch / filename)
    for dirname in ("templates", "static"):
        shutil.copytree(project_root / dirname, scratch / dirname)

    os.chdir(scratch)
    sys.path.insert(0, str(scratch))
    os.environ.update({
        "PERFORMANCEFLOW_SECRET_KEY": "e2e-local-secret-never-used-outside-temp-copy",
        "PERFORMANCEFLOW_DEBUG": "0",
        "PERFORMANCEFLOW_SECURE_COOKIES": "0",
        "PERFORMANCEFLOW_HR_PASSWORD": "TestOnly-HR-2026!",
        "PERFORMANCEFLOW_SUPERVISOR_PASSWORD": "TestOnly-Supervisor-2026!",
        "PERFORMANCEFLOW_MANAGER_PASSWORD": "TestOnly-Manager-2026!",
    })

    runpy.run_path(str(scratch / "init_db.py"), run_name="__main__")
    runpy.run_path(
        str(project_root / "tests" / "e2e" / "seed_test_data.py"),
        run_name="__main__",
    )

    spec = importlib.util.spec_from_file_location("app", scratch / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.run(host="127.0.0.1", port=5107, debug=False, use_reloader=False)
