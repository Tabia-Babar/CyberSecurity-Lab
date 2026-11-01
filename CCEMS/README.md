# CCEMS - Cybercrime Case & Evidence Management System
Flask + SQLite + Bootstrap 5 + Chart.js + ReportLab

Roles: Admin, Investigation Officer, Evidence Officer, Viewer.
Features: dashboard charts, case CRUD + filters, evidence upload (JPG/PNG/PDF) with SHA-256,
integrity verification, chain of custody, user/officer management, 5 PDF reports, activity logs.

## Run locally
1. Create and activate a virtual environment, then run `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env` and set `SECRET_KEY`.
3. Run `python app.py`, then open http://127.0.0.1:5000.
   Tables are created automatically in `database.db`. First visit to `/setup` creates the admin.
   Optional fake data: `python seed_demo.py`.

## Deploy on PythonAnywhere
Upload the project, install `requirements.txt`, and configure the WSGI app to import `app.app`.
Set `SQLITE_DATABASE=database.db` or an absolute writable path such as `/home/yourname/CCEMS/database.db`.
Set a strong `SECRET_KEY`, and use an absolute writable `UPLOAD_FOLDER` if needed. SQLite and uploaded files must be stored in writable directories.
