
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(100) NOT NULL,
  email VARCHAR(120) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role VARCHAR(20) NOT NULL DEFAULT 'viewer'
    CHECK(role IN ('admin','investigator','evidence_officer','viewer')),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS officers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL UNIQUE,
  designation VARCHAR(100),
  department VARCHAR(100),
  contact VARCHAR(30),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_number VARCHAR(30) NOT NULL UNIQUE,
  title VARCHAR(200) NOT NULL,
  complainant_name VARCHAR(120) NOT NULL,
  contact_number VARCHAR(20),
  case_type VARCHAR(60) NOT NULL,
  description TEXT,
  priority VARCHAR(20) NOT NULL DEFAULT 'Medium'
    CHECK(priority IN ('Low','Medium','High','Critical')),
  status VARCHAR(20) NOT NULL DEFAULT 'Pending'
    CHECK(status IN ('Pending','Investigating','Resolved')),
  officer_id INTEGER,
  created_by INTEGER,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (officer_id) REFERENCES officers(id) ON DELETE SET NULL,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TRIGGER IF NOT EXISTS cases_updated_at
AFTER UPDATE ON cases
FOR EACH ROW
BEGIN
  UPDATE cases SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id INTEGER NOT NULL,
  file_name VARCHAR(255) NOT NULL,
  file_path VARCHAR(255) NOT NULL,
  file_hash CHAR(64) NOT NULL,
  file_size INTEGER,
  description VARCHAR(255),
  uploaded_by INTEGER,
  current_holder_id INTEGER,
  uploaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
  FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (current_holder_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS custody_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  evidence_id INTEGER NOT NULL,
  action VARCHAR(20) NOT NULL
    CHECK(action IN ('Uploaded','Accessed','Downloaded','Verified','Transferred')),
  user_id INTEGER,
  to_user_id INTEGER,
  note VARCHAR(255),
  timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (evidence_id) REFERENCES evidence(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (to_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS activity_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  action VARCHAR(60) NOT NULL,
  case_id INTEGER,
  details VARCHAR(255),
  timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE SET NULL
);
