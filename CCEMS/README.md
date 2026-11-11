# CCEMS - Cyber Crime Evidence Management System
Flask + MySQL + Bootstrap 5 + Chart.js + ReportLab

## Roles
- Admin: full access (dashboard, all cases, assign officers, evidence, users, reports, activity logs).
- Investigation Officer: sees only the cases assigned by the admin; can investigate, update status
  (Pending / Investigating / Resolved), handle evidence for those cases.
- Complainant (Citizen): signs up at /register, files a complaint, sees only his own complaints and their status.

## Run locally
1. Start XAMPP -> Apache + MySQL.
2. python -m venv venv  |  venv\Scripts\activate  |  pip install -r requirements.txt
3. copy .env.example .env  (set SECRET_KEY and MYSQLPASSWORD if needed)
4. python app.py  -> http://127.0.0.1:5000
   Tables are created automatically in database ccems_db.
5. First visit -> /setup creates the admin account.
   Optional fake demo data: python seed_demo.py  (password for demo users: Demo@1234)

## Complaint flow
Citizen registers -> files complaint (status Pending) -> admin sees alert and assigns an officer
(Cases -> open case -> Edit) -> officer investigates and updates status -> citizen sees status.

## Deploy (PythonAnywhere / Railway)
Push to GitHub, pull on the host, set environment variables from .env.example
(SECRET_KEY, MYSQLHOST, MYSQLPORT, MYSQLUSER, MYSQLPASSWORD, MYSQLDATABASE), then Reload.
Start command for Railway: gunicorn app:app
