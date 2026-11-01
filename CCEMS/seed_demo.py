import random
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
from app import app, query, execute

PASSWORD = "Demo@1234"
USERS = [("Ayesha Khan", "ayesha@ccems.test", "investigator", ("Inspector", "Cyber Crime Unit", "0300-1111111")),
         ("Bilal Ahmed", "bilal@ccems.test", "investigator", ("Sub-Inspector", "Cyber Crime Unit", "0300-2222222")),
         ("Sara Malik", "sara@ccems.test", "evidence_officer", ("Forensic Analyst", "Digital Forensics Lab", "0300-3333333")),
         ("Viewer Demo", "viewer@ccems.test", "viewer", None)]
TYPES = ["Hacking", "Phishing", "Online Fraud", "Identity Theft", "Cyber Bullying", "Ransomware", "Data Breach"]
TITLES = ["Fake bank SMS link", "Social media account hacked", "Online shopping fraud", "Fake job offer scam",
          "Threatening messages on WhatsApp", "Company files encrypted", "Customer data leaked", "Cloned ID card misuse"]
NAMES = ["Test Complainant A", "Test Complainant B", "Test Complainant C", "Test Complainant D"]

with app.app_context():
    for name, email, role, off in USERS:
        if query("SELECT id FROM users WHERE email=%s", (email,), one=True):
            continue
        uid = execute("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,%s)",
                      (name, email, generate_password_hash(PASSWORD), role))
        if off:
            execute("INSERT INTO officers (user_id,designation,department,contact) VALUES (%s,%s,%s,%s)",
                    (uid, *off))
    officer_ids = [r["id"] for r in query(
        "SELECT o.id FROM officers o JOIN users u ON o.user_id=u.id WHERE u.role='investigator'")]
    if query("SELECT COUNT(*) n FROM cases", one=True)["n"] > 0:
        print("Cases already exist - skipped.")
        raise SystemExit
    for i in range(24):
        created = datetime.now() - timedelta(days=random.randint(0, 200))
        cid = execute(
            "INSERT INTO cases (case_number,title,complainant_name,contact_number,case_type,description,"
            "priority,status,officer_id,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"TMP-{i}", random.choice(TITLES), random.choice(NAMES), "0300-0000000", random.choice(TYPES),
             "Demo case with fake data.", random.choice(["Low", "Medium", "High", "Critical"]),
             random.choice(["Pending", "Investigating", "Resolved"]), random.choice(officer_ids), created))
        execute("UPDATE cases SET case_number=%s WHERE id=%s", (f"CC-{created.year}-{cid:04d}", cid))
    print("Done. Demo users password:", PASSWORD)