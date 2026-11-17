import sqlite3
import time
import os

DB_PATH = "monitor.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    is_new = not os.path.exists(DB_PATH)
    conn = get_db()
    c = conn.cursor()
    
    # Schema
    c.executescript('''
        CREATE TABLE IF NOT EXISTS hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_id INTEGER,
            timestamp INTEGER,
            loss_pct REAL,
            rtt_avg REAL,
            jitter REAL,
            FOREIGN KEY(host_id) REFERENCES hosts(id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER,
            host_id INTEGER,
            severity TEXT,
            message TEXT,
            acknowledged INTEGER DEFAULT 0,
            FOREIGN KEY(host_id) REFERENCES hosts(id)
        );
    ''')

    # Run safe migrations for the new columns
    try:
        c.execute("ALTER TABLE hosts ADD COLUMN category TEXT DEFAULT 'General'")
    except sqlite3.OperationalError:
        pass # Column already exists
    try:
        c.execute("ALTER TABLE metrics ADD COLUMN rtt_min REAL DEFAULT 0.0")
        c.execute("ALTER TABLE metrics ADD COLUMN rtt_max REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass
    
    # Default settings
    default_settings = {
        'ping_count': '10',
        'check_interval': '10',
        'threshold_degraded': '2',
        'threshold_down': '10',
        'simulation_mode': '0'
    }
    for k, v in default_settings.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        
    # Seed default hosts
    if is_new:
        default_hosts = [
            ("Google DNS", "8.8.8.8", "DNS"),
            ("Cloudflare DNS", "1.1.1.1", "DNS"),
            ("Google Web", "google.com", "External Web")
        ]
        for name, addr, cat in default_hosts:
            c.execute("INSERT INTO hosts (name, address, category) VALUES (?, ?, ?)", (name, addr, cat))
            
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return float(row['value']) if row else default

def save_metric(host_id, loss, min_rtt, avg_rtt, max_rtt, jitter):
    conn = get_db()
    conn.execute('''INSERT INTO metrics (host_id, timestamp, loss_pct, rtt_min, rtt_avg, rtt_max, jitter) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                 (host_id, int(time.time()), loss, min_rtt, avg_rtt, max_rtt, jitter))
    conn.commit()
    conn.close()