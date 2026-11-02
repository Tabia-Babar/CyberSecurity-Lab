# CCEMS - Cybercrime Case & Evidence Management System
Flask + MySQL + Bootstrap 5 + Chart.js + ReportLab

Roles: Admin, Investigation Officer, Evidence Officer, Viewer.
Features: dashboard charts, case CRUD + filters, evidence upload (JPG/PNG/PDF) with SHA-256,
integrity verification, chain of custody, user/officer management, 5 PDF reports, activity logs.

## Run locally
1. Start XAMPP -> Apache + MySQL.
2. python -m venv venv ; venv\Scripts\activate ; pip install -r requirements.txt
3. copy .env.example .env   (edit SECRET_KEY / MYSQLPASSWORD if needed)
4. python app.py  -> open http://127.0.0.1:5000
   Tables are created automatically in database ccems_db.
5. First visit -> /setup creates the admin.   Optional fake data: python seed_demo.py

## Deploy on Railway
Push to GitHub -> Railway "Deploy from GitHub" -> add MySQL service ->
set variables on the web service (SECRET_KEY, MYSQLHOST, MYSQLPORT, MYSQLUSER, MYSQLPASSWORD, MYSQLDATABASE)
-> add Volume mounted at /data and set UPLOAD_FOLDER=/data/uploads -> Generate Domain.
