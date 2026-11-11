import os, re, uuid, hashlib
from datetime import datetime, date
from functools import wraps
import mysql.connector
from dotenv import load_dotenv
from flask import (Flask, g, render_template, request, redirect, url_for, flash,
                   abort, send_from_directory, send_file)
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from pdfgen import build_pdf

load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
CSRFProtect(app)

UPLOAD_DIR = os.path.abspath(os.path.join(BASE_DIR, os.environ.get("UPLOAD_FOLDER", "uploads")))
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".pdf"}

ROLES = {"admin": "Admin", "investigator": "Investigation Officer",
         "evidence_officer": "Evidence Officer", "viewer": "Viewer",
         "citizen": "Complainant (Citizen)"}
STATUSES = ["Pending", "Investigating", "Resolved"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]
CRIME_TYPES = ["Hacking", "Phishing", "Online Fraud", "Identity Theft", "Cyber Bullying",
               "Ransomware", "Data Breach", "Other"]

login_manager = LoginManager(app)
login_manager.login_view = "login"


# ------------------------------------------------------------------ DATABASE
def db_config():
    return dict(host=os.getenv("MYSQLHOST", "localhost"),
                port=int(os.getenv("MYSQLPORT", 3306)),
                user=os.getenv("MYSQLUSER", "root"),
                password=os.getenv("MYSQLPASSWORD", ""),
                database=os.getenv("MYSQLDATABASE", "ccems_db"))


def get_db():
    if "db" not in g:  # one connection per request (not one global cursor)
        g.db = mysql.connector.connect(**db_config())
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def query(sql, args=(), one=False):
    cur = get_db().cursor(dictionary=True)
    cur.execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=()):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, args)
    conn.commit()
    last = cur.lastrowid
    cur.close()
    return last


def ensure_schema():
    """Create database + tables if they do not exist (safe to run many times)."""
    cfg = db_config()
    try:
        c = mysql.connector.connect(host=cfg["host"], port=cfg["port"],
                                    user=cfg["user"], password=cfg["password"])
        c.cursor().execute(f"CREATE DATABASE IF NOT EXISTS `{cfg['database']}`")
        c.close()
    except Exception as e:  # on hosted DB the database already exists
        print("DB create skipped:", e)
    conn = mysql.connector.connect(**cfg)
    cur = conn.cursor()
    with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as f:
        for stmt in f.read().split(";"):
            if stmt.strip():
                cur.execute(stmt)
    try:  # add the 'citizen' role to an already existing users table
        cur.execute("ALTER TABLE users MODIFY role ENUM('admin','investigator','evidence_officer',"
                    "'viewer','citizen') NOT NULL DEFAULT 'viewer'")
    except Exception as e:
        print("Role migration skipped:", e)
    conn.commit()
    conn.close()


# ------------------------------------------------------------------ AUTH
class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.name = row["name"]
        self.email = row["email"]
        # Only 3 account types are used: admin, investigator, citizen.
        # Any other stored role (viewer / evidence_officer) gets the complainant portal.
        self.role = "citizen" if row["role"] in ("viewer", "evidence_officer") else row["role"]
        self._active = bool(row["active"])

    @property
    def is_active(self):
        return self._active


@login_manager.user_loader
def load_user(uid):
    row = query("SELECT * FROM users WHERE id=%s", (uid,), one=True)
    return User(row) if row else None


def roles_required(*roles):
    def deco(f):
        @wraps(f)
        @login_required
        def wrapper(*a, **kw):
            if current_user.role not in roles:
                abort(403)
            return f(*a, **kw)
        return wrapper
    return deco


def log(action, case_id=None, details=""):
    uid = current_user.id if current_user.is_authenticated else None
    execute("INSERT INTO activity_logs (user_id, action, case_id, details) VALUES (%s,%s,%s,%s)",
            (uid, action, case_id, details[:250]))


CITIZEN_OK = {"my_complaints", "complaint_new", "case_view", "evidence_upload",
              "logout", "login", "register", "static", "setup"}


@app.before_request
def citizen_guard():
    """Citizens may only use their own complaint pages."""
    if (current_user.is_authenticated and current_user.role == "citizen"
            and request.endpoint and request.endpoint not in CITIZEN_OK):
        abort(403)


@app.template_filter("dt")
def fmt_dt(v, f="%Y-%m-%d %H:%M"):
    return v.strftime(f) if v else "-"


@app.context_processor
def inject():
    return dict(ROLES=ROLES)


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, msg="You are not allowed to do this."), 403


@app.errorhandler(404)
def notfound(e):
    return render_template("error.html", code=404, msg="Page not found."), 404


@app.errorhandler(413)
def toolarge(e):
    return render_template("error.html", code=413, msg="File too large (max 10 MB)."), 413


@app.route("/setup", methods=["GET", "POST"])
def setup():
    """Only works while there are NO users: creates the first admin."""
    if query("SELECT COUNT(*) n FROM users", one=True)["n"] > 0:
        return redirect(url_for("login"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        if not name or not valid_email(email) or len(pw) < 8:
            flash("Valid name, email and password (min 8 chars) required.", "danger")
        else:
            execute("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,'admin')",
                    (name, email, generate_password_hash(pw)))
            flash("Admin created. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("setup.html")


def valid_email(e):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e or ""))


@app.route("/register", methods=["GET", "POST"])
def register():
    """Public sign-up for complainants (citizens)."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        errors = []
        if not name or len(name) > 100:
            errors.append("Name is required.")
        if not valid_email(email):
            errors.append("Valid email is required.")
        if len(pw) < 8:
            errors.append("Password must be at least 8 characters.")
        if pw != pw2:
            errors.append("Passwords do not match.")
        if query("SELECT id FROM users WHERE email=%s", (email,), one=True):
            errors.append("This email is already registered.")
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            execute("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,'citizen')",
                    (name, email, generate_password_hash(pw)))
            execute("INSERT INTO activity_logs (user_id, action, details) VALUES (NULL,'CITIZEN_REGISTERED',%s)",
                    (email[:200],))
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if query("SELECT COUNT(*) n FROM users", one=True)["n"] == 0:
        return redirect(url_for("setup"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        row = query("SELECT * FROM users WHERE email=%s", (email,), one=True)
        if row and row["active"] and check_password_hash(row["password_hash"], request.form.get("password", "")):
            login_user(User(row))
            log("LOGIN", details=email)
            return redirect(url_for("complaint_new" if row["role"] == "citizen" else "dashboard"))
        execute("INSERT INTO activity_logs (user_id, action, details) VALUES (NULL,'LOGIN_FAILED',%s)", (email[:200],))
        flash("Invalid email or password, or account disabled.", "danger")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    log("LOGOUT", details=current_user.email)
    logout_user()
    return redirect(url_for("login"))


# ------------------------------------------------------------------ DASHBOARD
@app.route("/")
@login_required
def dashboard():
    if current_user.role == "citizen":
        return redirect(url_for("complaint_new"))
    cond, a = scope_clause()  # non-admin users only see their own / assigned cases

    def cnt(extra=""):
        return query("SELECT COUNT(*) n FROM cases c WHERE 1=1" + extra + cond, a, one=True)["n"]

    stats = {
        "total": cnt(),
        "pending": cnt(" AND c.status='Pending'"),
        "investigating": cnt(" AND c.status='Investigating'"),
        "resolved": cnt(" AND c.status='Resolved'"),
        "evidence": query("SELECT COUNT(*) n FROM evidence e JOIN cases c ON e.case_id=c.id "
                          "WHERE 1=1" + cond, a, one=True)["n"],
        "officers": query("SELECT COUNT(*) n FROM officers o JOIN users u ON o.user_id=u.id "
                          "WHERE u.active=1 AND u.role='investigator'", one=True)["n"],
    }
    types = query("SELECT c.case_type label, COUNT(*) n FROM cases c WHERE 1=1" + cond +
                  " GROUP BY c.case_type ORDER BY n DESC", a)
    status = query("SELECT c.status label, COUNT(*) n FROM cases c WHERE 1=1" + cond +
                   " GROUP BY c.status", a)
    monthly = query("SELECT YEAR(c.created_at) y, MONTH(c.created_at) m, COUNT(*) n FROM cases c "
                    "WHERE c.created_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)" + cond +
                    " GROUP BY y, m ORDER BY y, m", a)
    chart = {
        "types": {"labels": [r["label"] for r in types], "values": [r["n"] for r in types]},
        "status": {"labels": [r["label"] for r in status], "values": [r["n"] for r in status]},
        "monthly": {"labels": [f"{r['y']}-{r['m']:02d}" for r in monthly], "values": [r["n"] for r in monthly]},
    }
    recent = query(CASE_SQL + " WHERE 1=1" + cond + " ORDER BY c.created_at DESC LIMIT 5", a)
    unassigned = 0
    if current_user.role == "admin":
        unassigned = query("SELECT COUNT(*) n FROM cases WHERE officer_id IS NULL AND status='Pending'",
                           one=True)["n"]
    return render_template("dashboard.html", stats=stats, chart=chart, recent=recent, unassigned=unassigned)


# ------------------------------------------------------------------ CASES
CASE_SQL = ("SELECT c.*, o.user_id AS officer_user_id, u.name AS officer_name "
            "FROM cases c LEFT JOIN officers o ON c.officer_id=o.id "
            "LEFT JOIN users u ON o.user_id=u.id")


def get_case(cid):
    c = query(CASE_SQL + " WHERE c.id=%s", (cid,), one=True)
    if not c:
        abort(404)
    return c


def assigned_to_me(c):
    return current_user.role == "investigator" and c["officer_user_id"] == current_user.id


def scope_clause():
    """SQL fragment limiting cases to what the logged-in user may see (alias c)."""
    r, uid = current_user.role, current_user.id
    if r == "admin":
        return "", ()
    if r == "investigator":
        return (" AND (c.officer_id IN (SELECT id FROM officers WHERE user_id=%s) OR c.created_by=%s)",
                (uid, uid))
    if r == "evidence_officer":
        return (" AND (c.created_by=%s OR c.id IN (SELECT case_id FROM evidence "
                "WHERE uploaded_by=%s OR current_holder_id=%s))", (uid, uid, uid))
    return " AND c.created_by=%s", (uid,)  # citizen, viewer: only their own cases


def can_view_case(c):
    if current_user.role == "admin":
        return True
    frag, args = scope_clause()
    return query("SELECT c.id FROM cases c WHERE c.id=%s" + frag, (c["id"],) + tuple(args), one=True) is not None


def can_edit_case(c):
    return current_user.role == "admin" or assigned_to_me(c)


def can_handle_evidence(c):
    if current_user.role == "admin" or assigned_to_me(c):
        return True
    return current_user.role == "evidence_officer" and can_view_case(c)


def is_owner(c):
    return current_user.role == "citizen" and c["created_by"] == current_user.id


def can_upload(c):
    return can_handle_evidence(c) or (is_owner(c) and c["status"] != "Resolved")


def officer_choices():
    return query("SELECT o.id, u.name, o.designation FROM officers o JOIN users u ON o.user_id=u.id "
                 "WHERE u.active=1 AND u.role='investigator' ORDER BY u.name")


def read_case_form():
    f = request.form
    d = {k: f.get(k, "").strip() for k in
         ("title", "complainant_name", "contact_number", "case_type", "description", "priority")}
    errors = []
    if not d["title"] or len(d["title"]) > 200:
        errors.append("Title is required (max 200 characters).")
    if not d["complainant_name"] or len(d["complainant_name"]) > 120:
        errors.append("Complainant name is required.")
    if d["contact_number"] and not re.fullmatch(r"[0-9+\-\s]{7,20}", d["contact_number"]):
        errors.append("Contact number is invalid.")
    if d["case_type"] not in CRIME_TYPES:
        errors.append("Invalid crime type.")
    if d["priority"] not in PRIORITIES:
        errors.append("Invalid priority.")
    return d, errors


def read_officer_id():
    raw = request.form.get("officer_id", "")
    if not raw:
        return None
    try:
        oid = int(raw)
    except ValueError:
        abort(400)
    if not query("SELECT id FROM officers WHERE id=%s", (oid,), one=True):
        abort(400)
    return oid


def citizen_cases():
    return query(CASE_SQL + " WHERE c.created_by=%s ORDER BY c.created_at DESC", (current_user.id,))


@app.route("/my-complaints")
@roles_required("citizen")
def my_complaints():
    return render_template("my_complaints.html", cases=citizen_cases(), form={}, crime_types=CRIME_TYPES)


@app.route("/complaint/new", methods=["GET", "POST"])
@roles_required("citizen")
def complaint_new():
    if request.method == "GET":
        return redirect(url_for("my_complaints"))
    form = {k: request.form.get(k, "").strip() for k in ("title", "contact_number", "case_type", "description")}
    errors = []
    if not form["title"] or len(form["title"]) > 200:
        errors.append("Subject is required (max 200 characters).")
    if not form["contact_number"] or not re.fullmatch(r"[0-9+\-\s]{7,20}", form["contact_number"]):
        errors.append("A valid contact number is required.")
    if form["case_type"] not in CRIME_TYPES:
        errors.append("Please choose a crime type.")
    if len(form["description"]) < 20:
        errors.append("Please describe the incident (at least 20 characters).")
    if errors:
        for e in errors:
            flash(e, "danger")
        return render_template("my_complaints.html", cases=citizen_cases(), form=form, crime_types=CRIME_TYPES)
    cid = execute(
        "INSERT INTO cases (case_number,title,complainant_name,contact_number,case_type,"
        "description,priority,status,officer_id,created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,'Medium','Pending',NULL,%s)",
        ("TMP-" + uuid.uuid4().hex[:20], form["title"], current_user.name,
         form["contact_number"], form["case_type"], form["description"], current_user.id))
    number = f"CC-{date.today().year}-{cid:04d}"
    execute("UPDATE cases SET case_number=%s WHERE id=%s", (number, cid))
    log("COMPLAINT_FILED", cid, number)
    flash(f"Complaint registered successfully. Your case number is {number}. "
          "It has been sent to the admin for review and officer assignment.", "success")
    return redirect(url_for("my_complaints"))


@app.route("/cases")
@login_required
def cases():
    q = request.args.get("q", "").strip()
    ctype = request.args.get("type", "")
    status = request.args.get("status", "")
    d_from = request.args.get("from", "")
    d_to = request.args.get("to", "")
    frag, fargs = scope_clause()
    sql, args = CASE_SQL + " WHERE 1=1" + frag, list(fargs)
    if q:
        sql += " AND (c.case_number LIKE %s OR c.complainant_name LIKE %s OR c.title LIKE %s)"
        args += [f"%{q}%"] * 3
    if ctype:
        sql += " AND c.case_type=%s"; args.append(ctype)
    if status:
        sql += " AND c.status=%s"; args.append(status)
    if d_from:
        sql += " AND DATE(c.created_at) >= %s"; args.append(d_from)
    if d_to:
        sql += " AND DATE(c.created_at) <= %s"; args.append(d_to)
    rows = query(sql + " ORDER BY c.created_at DESC", args)
    return render_template("cases.html", cases=rows, crime_types=CRIME_TYPES, statuses=STATUSES,
                           f=dict(q=q, type=ctype, status=status, d_from=d_from, d_to=d_to))


@app.route("/cases/new", methods=["GET", "POST"])
@roles_required("admin")
def case_new():
    form = {}
    if request.method == "POST":
        form, errors = read_case_form()
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            if current_user.role == "investigator":
                me = query("SELECT id FROM officers WHERE user_id=%s", (current_user.id,), one=True)
                officer_id = me["id"] if me else None
            else:
                officer_id = read_officer_id()
            cid = execute(
                "INSERT INTO cases (case_number,title,complainant_name,contact_number,case_type,"
                "description,priority,status,officer_id,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,'Pending',%s,%s)",
                ("TMP-" + uuid.uuid4().hex[:20], form["title"], form["complainant_name"],
                 form["contact_number"], form["case_type"], form["description"], form["priority"],
                 officer_id, current_user.id))
            number = f"CC-{date.today().year}-{cid:04d}"
            execute("UPDATE cases SET case_number=%s WHERE id=%s", (number, cid))
            log("CASE_CREATED", cid, number)
            flash(f"Case {number} registered.", "success")
            return redirect(url_for("case_view", cid=cid))
    return render_template("case_form.html", case=None, form=form, officers=officer_choices(),
                           crime_types=CRIME_TYPES, priorities=PRIORITIES, statuses=STATUSES)


@app.route("/cases/<int:cid>")
@login_required
def case_view(cid):
    c = get_case(cid)
    if not can_view_case(c):
        abort(403)
    ev = query("SELECT e.*, u.name uploader_name, h.name holder_name FROM evidence e "
               "LEFT JOIN users u ON e.uploaded_by=u.id LEFT JOIN users h ON e.current_holder_id=h.id "
               "WHERE e.case_id=%s ORDER BY e.uploaded_at DESC", (cid,))
    timeline = query("SELECT l.*, u.name FROM activity_logs l LEFT JOIN users u ON l.user_id=u.id "
                     "WHERE l.case_id=%s ORDER BY l.timestamp DESC, l.id DESC LIMIT 50", (cid,))
    if current_user.role == "citizen":
        timeline = []
    return render_template("case_view.html", case=c, evidence=ev, timeline=timeline,
                           can_edit=can_edit_case(c), can_ev=can_handle_evidence(c),
                           can_upload=can_upload(c))


@app.route("/cases/<int:cid>/edit", methods=["GET", "POST"])
@roles_required("admin", "investigator")
def case_edit(cid):
    c = get_case(cid)
    if not can_edit_case(c):
        abort(403)
    form = c
    if request.method == "POST":
        form, errors = read_case_form()
        status = request.form.get("status", "")
        if status not in STATUSES:
            errors.append("Invalid status.")
        if errors:
            for e in errors:
                flash(e, "danger")
            form = {**c, **form}
        else:
            officer_id = read_officer_id() if current_user.role == "admin" else c["officer_id"]
            execute("UPDATE cases SET title=%s, complainant_name=%s, contact_number=%s, case_type=%s, "
                    "description=%s, priority=%s, status=%s, officer_id=%s WHERE id=%s",
                    (form["title"], form["complainant_name"], form["contact_number"], form["case_type"],
                     form["description"], form["priority"], status, officer_id, cid))
            log("CASE_UPDATED", cid, f"{c['case_number']}: status {c['status']} -> {status}")
            flash("Case updated.", "success")
            return redirect(url_for("case_view", cid=cid))
    return render_template("case_form.html", case=c, form=form, officers=officer_choices(),
                           crime_types=CRIME_TYPES, priorities=PRIORITIES, statuses=STATUSES)


@app.route("/cases/<int:cid>/delete", methods=["POST"])
@roles_required("admin")
def case_delete(cid):
    c = get_case(cid)
    for e in query("SELECT file_path FROM evidence WHERE case_id=%s", (cid,)):
        p = os.path.join(UPLOAD_DIR, e["file_path"])
        if os.path.exists(p):
            os.remove(p)
    execute("DELETE FROM cases WHERE id=%s", (cid,))
    log("CASE_DELETED", None, c["case_number"])
    flash(f"Case {c['case_number']} deleted.", "warning")
    return redirect(url_for("cases"))


# ------------------------------------------------------------------ EVIDENCE
def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def custody(eid, action, to_user=None, note=""):
    execute("INSERT INTO custody_log (evidence_id,action,user_id,to_user_id,note) VALUES (%s,%s,%s,%s,%s)",
            (eid, action, current_user.id, to_user, note[:250]))


def get_evidence(eid):
    e = query("SELECT e.*, u.name uploader_name, h.name holder_name FROM evidence e "
              "LEFT JOIN users u ON e.uploaded_by=u.id LEFT JOIN users h ON e.current_holder_id=h.id "
              "WHERE e.id=%s", (eid,), one=True)
    if not e:
        abort(404)
    c = get_case(e["case_id"])
    if not can_handle_evidence(c):
        abort(403)
    return e, c


@app.route("/cases/<int:cid>/evidence", methods=["POST"])
@login_required
def evidence_upload(cid):
    c = get_case(cid)
    if not can_upload(c):
        abort(403)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Please choose a file.", "danger")
        return redirect(url_for("case_view", cid=cid))
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        flash("Only JPG, PNG and PDF files are allowed.", "danger")
        return redirect(url_for("case_view", cid=cid))
    stored = uuid.uuid4().hex + ext
    path = os.path.join(UPLOAD_DIR, stored)
    f.save(path)
    by_citizen = current_user.role == "citizen"
    holder = None if by_citizen else current_user.id
    eid = execute("INSERT INTO evidence (case_id,file_name,file_path,file_hash,file_size,description,"
                  "uploaded_by,current_holder_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                  (cid, os.path.basename(f.filename)[:255], stored, sha256_of(path), os.path.getsize(path),
                   request.form.get("description", "").strip()[:255], current_user.id, holder))
    custody(eid, "Uploaded", note="Submitted by complainant, SHA-256 recorded" if by_citizen
            else "Evidence uploaded, SHA-256 recorded")
    log("EVIDENCE_UPLOADED", cid, f"{c['case_number']}: {f.filename}")
    flash("Evidence uploaded and SHA-256 hash recorded.", "success")
    return redirect(url_for("case_view", cid=cid))


@app.route("/evidence/<int:eid>")
@login_required
def evidence_view(eid):
    e, c = get_evidence(eid)
    custody(eid, "Accessed", note="Evidence page opened")
    logs = query("SELECT l.*, u.name actor, t.name target FROM custody_log l "
                 "LEFT JOIN users u ON l.user_id=u.id LEFT JOIN users t ON l.to_user_id=t.id "
                 "WHERE l.evidence_id=%s ORDER BY l.timestamp, l.id", (eid,))
    people = query("SELECT id, name, role FROM users WHERE active=1 AND role NOT IN ('viewer','citizen') ORDER BY name")
    is_image = e["file_path"].lower().endswith((".jpg", ".jpeg", ".png"))
    return render_template("evidence_view.html", e=e, case=c, logs=logs, people=people, is_image=is_image)


@app.route("/evidence/<int:eid>/file")
@login_required
def evidence_file(eid):
    e, c = get_evidence(eid)  # inline preview (access already logged on page view)
    return send_from_directory(UPLOAD_DIR, e["file_path"], download_name=e["file_name"])


@app.route("/evidence/<int:eid>/download")
@login_required
def evidence_download(eid):
    e, c = get_evidence(eid)
    custody(eid, "Downloaded")
    log("EVIDENCE_DOWNLOADED", c["id"], e["file_name"])
    return send_from_directory(UPLOAD_DIR, e["file_path"], as_attachment=True, download_name=e["file_name"])


@app.route("/evidence/<int:eid>/verify", methods=["POST"])
@login_required
def evidence_verify(eid):
    e, c = get_evidence(eid)
    path = os.path.join(UPLOAD_DIR, e["file_path"])
    if not os.path.exists(path):
        custody(eid, "Verified", note="FILE MISSING from storage")
        flash("File is missing from storage!", "danger")
    else:
        ok = sha256_of(path) == e["file_hash"]
        custody(eid, "Verified", note="Integrity OK - hash matches" if ok else "TAMPERED - hash mismatch")
        log("EVIDENCE_VERIFIED", c["id"], f"{e['file_name']}: {'OK' if ok else 'MISMATCH'}")
        if ok:
            flash("Integrity verified: file is unchanged (hash matches).", "success")
        else:
            flash("WARNING: hash mismatch! The file may have been modified.", "danger")
    return redirect(url_for("evidence_view", eid=eid))


@app.route("/evidence/<int:eid>/transfer", methods=["POST"])
@login_required
def evidence_transfer(eid):
    e, c = get_evidence(eid)
    try:
        to = int(request.form.get("to_user", ""))
    except ValueError:
        abort(400)
    target = query("SELECT id, name FROM users WHERE id=%s AND active=1 AND role NOT IN ('viewer','citizen')", (to,), one=True)
    if not target:
        abort(400)
    custody(eid, "Transferred", to_user=to, note=request.form.get("note", "").strip())
    execute("UPDATE evidence SET current_holder_id=%s WHERE id=%s", (to, eid))
    log("CUSTODY_TRANSFER", c["id"], f"{e['file_name']} -> {target['name']}")
    flash(f"Custody transferred to {target['name']}.", "success")
    return redirect(url_for("evidence_view", eid=eid))


# ------------------------------------------------------------------ USERS (admin)
@app.route("/users")
@roles_required("admin")
def users():
    rows = query("SELECT u.*, o.designation, o.department, o.contact FROM users u "
                 "LEFT JOIN officers o ON o.user_id=u.id ORDER BY u.id")
    return render_template("users.html", users=rows)


@app.route("/users/new", methods=["GET", "POST"])
@roles_required("admin")
def user_new():
    if request.method == "POST":
        f = {k: request.form.get(k, "").strip() for k in
             ("name", "email", "role", "designation", "department", "contact")}
        f["email"] = f["email"].lower()
        pw = request.form.get("password", "")
        errors = []
        if not f["name"]:
            errors.append("Name is required.")
        if not valid_email(f["email"]):
            errors.append("Valid email is required.")
        if f["role"] not in ROLES:
            errors.append("Invalid role.")
        if len(pw) < 8:
            errors.append("Password must be at least 8 characters.")
        if query("SELECT id FROM users WHERE email=%s", (f["email"],), one=True):
            errors.append("Email already registered.")
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            uid = execute("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,%s)",
                          (f["name"], f["email"], generate_password_hash(pw), f["role"]))
            if f["role"] in ("investigator", "evidence_officer"):
                execute("INSERT INTO officers (user_id,designation,department,contact) VALUES (%s,%s,%s,%s)",
                        (uid, f["designation"], f["department"], f["contact"]))
            log("USER_CREATED", None, f"{f['email']} ({f['role']})")
            flash("User created.", "success")
            return redirect(url_for("users"))
    return render_template("user_form.html", roles=ROLES)


@app.route("/users/<int:uid>/toggle", methods=["POST"])
@roles_required("admin")
def user_toggle(uid):
    u = query("SELECT * FROM users WHERE id=%s", (uid,), one=True) or abort(404)
    if u["id"] == current_user.id:
        flash("You cannot disable your own account.", "danger")
    else:
        execute("UPDATE users SET active=%s WHERE id=%s", (0 if u["active"] else 1, uid))
        log("USER_TOGGLED", None, f"{u['email']} active={0 if u['active'] else 1}")
    return redirect(url_for("users"))


@app.route("/users/<int:uid>/role", methods=["POST"])
@roles_required("admin")
def user_role(uid):
    u = query("SELECT * FROM users WHERE id=%s", (uid,), one=True) or abort(404)
    role = request.form.get("role", "")
    if u["id"] == current_user.id:
        flash("You cannot change your own role.", "danger")
    elif role not in ("admin", "investigator", "citizen"):
        flash("Invalid role.", "danger")
    else:
        execute("UPDATE users SET role=%s WHERE id=%s", (role, uid))
        if role == "investigator" and not query("SELECT id FROM officers WHERE user_id=%s", (uid,), one=True):
            execute("INSERT INTO officers (user_id,designation,department,contact) VALUES (%s,'Investigation Officer','','')", (uid,))
        log("USER_ROLE_CHANGED", None, f"{u['email']}: {u['role']} -> {role}")
        flash(f"{u['name']} is now: {ROLES[role]}.", "success")
    return redirect(url_for("users"))


@app.route("/users/<int:uid>/password", methods=["POST"])
@roles_required("admin")
def user_password(uid):
    u = query("SELECT * FROM users WHERE id=%s", (uid,), one=True) or abort(404)
    pw = request.form.get("password", "")
    if len(pw) < 8:
        flash("Password must be at least 8 characters.", "danger")
    else:
        execute("UPDATE users SET password_hash=%s WHERE id=%s", (generate_password_hash(pw), uid))
        log("PASSWORD_RESET", None, u["email"])
        flash("Password updated.", "success")
    return redirect(url_for("users"))


@app.route("/logs")
@roles_required("admin")
def logs():
    rows = query("SELECT l.*, u.name, c.case_number FROM activity_logs l "
                 "LEFT JOIN users u ON l.user_id=u.id LEFT JOIN cases c ON l.case_id=c.id "
                 "ORDER BY l.timestamp DESC, l.id DESC LIMIT 500")
    return render_template("logs.html", logs=rows)


# ------------------------------------------------------------------ REPORTS (PDF)
def pdf_response(buf, name):
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=name)


@app.route("/reports")
@roles_required("admin")
def reports():
    return render_template("reports.html", this_month=date.today().strftime("%Y-%m"))


@app.route("/cases/<int:cid>/report.pdf")
@roles_required("admin")
def report_case(cid):
    c = get_case(cid)
    blocks = [("h", "Case Details"),
              ("kv", [["Case Number", c["case_number"]], ["Title", c["title"]], ["Type", c["case_type"]],
                      ["Priority", c["priority"]], ["Status", c["status"]],
                      ["Complainant", c["complainant_name"]], ["Contact", c["contact_number"]],
                      ["Assigned Officer", c["officer_name"] or "Unassigned"],
                      ["Registered", fmt_dt(c["created_at"])], ["Last Updated", fmt_dt(c["updated_at"])]]),
              ("h", "Description"), ("p", c["description"])]
    blocks.append(("h", "Evidence and Chain of Custody"))
    ev = query("SELECT * FROM evidence WHERE case_id=%s", (cid,))
    if not ev:
        blocks.append(("p", "No evidence uploaded."))
    for e in ev:
        blocks.append(("kv", [["File", e["file_name"]], ["SHA-256", e["file_hash"]],
                              ["Size (bytes)", e["file_size"]], ["Uploaded", fmt_dt(e["uploaded_at"])]]))
        rows = query("SELECT l.*, u.name actor, t.name target FROM custody_log l "
                     "LEFT JOIN users u ON l.user_id=u.id LEFT JOIN users t ON l.to_user_id=t.id "
                     "WHERE l.evidence_id=%s ORDER BY l.timestamp, l.id", (e["id"],))
        blocks.append(("table", ["Time", "Action", "By", "To", "Note"],
                       [[fmt_dt(r["timestamp"]), r["action"], r["actor"], r["target"], r["note"]] for r in rows]))
    log("REPORT_CASE", cid, c["case_number"])
    return pdf_response(build_pdf("Cybercrime Case Report", c["case_number"], blocks),
                        f"{c['case_number']}_report.pdf")


@app.route("/reports/summary.pdf")
@roles_required("admin")
def report_summary():
    by = lambda col: [[r["k"], r["n"]] for r in query(f"SELECT {col} k, COUNT(*) n FROM cases GROUP BY {col}")]
    total = query("SELECT COUNT(*) n FROM cases", one=True)["n"]
    blocks = [("h", "Total Cases"), ("p", str(total)),
              ("h", "By Status"), ("table", ["Status", "Cases"], by("status")),
              ("h", "By Crime Type"), ("table", ["Crime Type", "Cases"], by("case_type")),
              ("h", "By Priority"), ("table", ["Priority", "Cases"], by("priority"))]
    log("REPORT_SUMMARY")
    return pdf_response(build_pdf("Total Cases Summary", "CCEMS", blocks), "cases_summary.pdf")


@app.route("/reports/monthly.pdf")
@roles_required("admin")
def report_monthly():
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        abort(400)
    y, m = int(month[:4]), int(month[5:])
    rows = query(CASE_SQL + " WHERE YEAR(c.created_at)=%s AND MONTH(c.created_at)=%s ORDER BY c.created_at",
                 (y, m))
    by_type, by_status = {}, {}
    for r in rows:
        by_type[r["case_type"]] = by_type.get(r["case_type"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    blocks = [("h", "Totals"), ("p", f"Cases registered in {month}: {len(rows)}"),
              ("h", "By Crime Type"), ("table", ["Type", "Cases"], [[k, v] for k, v in by_type.items()]),
              ("h", "By Status"), ("table", ["Status", "Cases"], [[k, v] for k, v in by_status.items()]),
              ("h", "Case List"),
              ("table", ["Case No", "Title", "Type", "Priority", "Status", "Officer"],
               [[r["case_number"], r["title"], r["case_type"], r["priority"], r["status"], r["officer_name"]]
                for r in rows])]
    log("REPORT_MONTHLY", None, month)
    return pdf_response(build_pdf("Monthly Crime Report", month, blocks), f"monthly_{month}.pdf")


@app.route("/reports/officers.pdf")
@roles_required("admin")
def report_officers():
    rows = query("SELECT u.name, o.designation, COUNT(c.id) total, "
                 "SUM(c.status='Pending') pending, SUM(c.status='Investigating') investigating, "
                 "SUM(c.status='Resolved') resolved FROM officers o JOIN users u ON o.user_id=u.id "
                 "LEFT JOIN cases c ON c.officer_id=o.id WHERE u.role='investigator' "
                 "GROUP BY o.id, u.name, o.designation ORDER BY total DESC")
    data = []
    for r in rows:
        total = int(r["total"] or 0)
        res = int(r["resolved"] or 0)
        rate = f"{(res / total * 100):.0f}%" if total else "-"
        data.append([r["name"], r["designation"], total, int(r["pending"] or 0),
                     int(r["investigating"] or 0), res, rate])
    log("REPORT_OFFICERS")
    return pdf_response(build_pdf("Officer Performance Report", "CCEMS", [
        ("table", ["Officer", "Designation", "Assigned", "Pending", "Investigating", "Resolved", "Resolution Rate"], data)]),
        "officer_performance.pdf")


@app.route("/reports/evidence.pdf")
@roles_required("admin")
def report_evidence():
    rows = query("SELECT e.*, c.case_number, u.name uploader, h.name holder FROM evidence e "
                 "JOIN cases c ON e.case_id=c.id LEFT JOIN users u ON e.uploaded_by=u.id "
                 "LEFT JOIN users h ON e.current_holder_id=h.id ORDER BY e.uploaded_at DESC")
    data = [[r["case_number"], r["file_name"], r["file_hash"][:16] + "...", r["uploader"],
             r["holder"], fmt_dt(r["uploaded_at"])] for r in rows]
    log("REPORT_EVIDENCE")
    return pdf_response(build_pdf("Evidence Report", f"{len(rows)} files", [
        ("table", ["Case", "File", "SHA-256 (start)", "Uploaded by", "Current holder", "Uploaded"], data)]),
        "evidence_report.pdf")


try:
    ensure_schema()
except Exception as exc:  # app still starts; error shows on first DB use
    print("Schema init failed:", exc)

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
