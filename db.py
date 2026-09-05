import sqlite3
from datetime import datetime

DB_PATH = "recovery.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us access columns by name, like a dict
    return conn

def init_db():
    """Create tables if they don't already exist. Safe to call every run."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT PRIMARY KEY,
            customer_name TEXT NOT NULL,
            amount REAL NOT NULL,
            due_date TEXT NOT NULL,
            previous_reminders INTEGER NOT NULL,
            days_overdue INTEGER,
            score REAL,
            promise_to_pay INTEGER NOT NULL DEFAULT 0
        )
    """)

    # One row per drafted/sent message -- an invoice can have many over time.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id TEXT NOT NULL,
            draft_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'drafted',
            created_at TEXT NOT NULL,
            FOREIGN KEY (invoice_id) REFERENCES invoices (invoice_id)
        )
    """)

    # Append-only trail: every meaningful action taken by the system or a
    # human gets a row here. Nothing is ever updated or deleted from this
    # table -- that's what makes it a real audit log, not just a status field.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

def upsert_invoice(invoice):
    """Insert an invoice, or update it if the invoice_id already exists."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO invoices (invoice_id, customer_name, amount, due_date,
                               previous_reminders, days_overdue, score)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(invoice_id) DO UPDATE SET
            days_overdue = excluded.days_overdue,
            score = excluded.score
    """, (
        invoice["invoice_id"],
        invoice["customer_name"],
        invoice["amount"],
        invoice["due_date"].strftime("%Y-%m-%d"),
        invoice["previous_reminders"],
        invoice.get("days_overdue"),
        invoice.get("score"),
    ))
    conn.commit()
    conn.close()

def save_message(invoice_id, draft_text):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO messages (invoice_id, draft_text, status, created_at)
        VALUES (?, ?, 'drafted', ?)
    """, (invoice_id, draft_text, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_messages_for_invoice(invoice_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM messages WHERE invoice_id = ? ORDER BY created_at", (invoice_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def log_action(invoice_id, action, details=""):
    """Append one row to the audit trail. Called for every meaningful
    event: drafted, approved, rejected, sent, stopped."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO audit_log (invoice_id, action, details, created_at)
        VALUES (?, ?, ?, ?)
    """, (invoice_id, action, details, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def update_message_status(message_id, status):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE messages SET status = ? WHERE id = ?", (status, message_id))
    conn.commit()
    conn.close()

def get_latest_message_id(invoice_id):
    """Grab the id of the most recently drafted message for an invoice --
    used right after save_message() so we know which row to update."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM messages WHERE invoice_id = ?
        ORDER BY created_at DESC LIMIT 1
    """, (invoice_id,))
    row = cur.fetchone()
    conn.close()
    return row["id"] if row else None

def count_sent_messages(invoice_id):
    """How many messages our system has actually sent (approved+sent) for
    this invoice so far -- this is what the stopping rule caps."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) as c FROM messages
        WHERE invoice_id = ? AND status = 'sent'
    """, (invoice_id,))
    row = cur.fetchone()
    conn.close()
    return row["c"]

def set_promise_to_pay(invoice_id, value=1):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE invoices SET promise_to_pay = ? WHERE invoice_id = ?", (value, invoice_id))
    conn.commit()
    conn.close()

def get_invoice(invoice_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
    row = cur.fetchone()
    conn.close()
    return row

def get_audit_log():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_log ORDER BY created_at")
    rows = cur.fetchall()
    conn.close()
    return rows

def get_recovery_summary():
    """The headline numbers for the pitch: how much of the total overdue
    amount got approved for recovery, vs rejected/stopped, across this
    batch run."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as c, COALESCE(SUM(amount),0) as total FROM invoices")
    row = cur.fetchone()
    total_invoices, total_amount = row["c"], row["total"]

    # "sent" status on the messages table maps 1:1 to approved invoices
    # here since each invoice gets at most one message per run.
    cur.execute("""
        SELECT COUNT(DISTINCT m.invoice_id) as c, COALESCE(SUM(i.amount),0) as total
        FROM messages m JOIN invoices i ON m.invoice_id = i.invoice_id
        WHERE m.status = 'sent'
    """)
    row = cur.fetchone()
    sent_count, sent_amount = row["c"], row["total"]

    cur.execute("""
        SELECT COUNT(DISTINCT m.invoice_id) as c, COALESCE(SUM(i.amount),0) as total
        FROM messages m JOIN invoices i ON m.invoice_id = i.invoice_id
        WHERE m.status = 'rejected'
    """)
    row = cur.fetchone()
    rejected_count, rejected_amount = row["c"], row["total"]

    cur.execute("SELECT COUNT(*) as c, COALESCE(SUM(amount),0) as total FROM invoices WHERE promise_to_pay = 1")
    row = cur.fetchone()
    promised_count, promised_amount = row["c"], row["total"]

    # Escalated and held invoices never get a message row -- their only
    # record is the audit_log decision entry, so we count them from there.
    cur.execute("""
        SELECT COUNT(DISTINCT a.invoice_id) as c, COALESCE(SUM(i.amount),0) as total
        FROM audit_log a JOIN invoices i ON a.invoice_id = i.invoice_id
        WHERE a.action = 'decision:escalate_to_human'
    """)
    row = cur.fetchone()
    escalated_count, escalated_amount = row["c"], row["total"]

    cur.execute("""
        SELECT COUNT(DISTINCT a.invoice_id) as c, COALESCE(SUM(i.amount),0) as total
        FROM audit_log a JOIN invoices i ON a.invoice_id = i.invoice_id
        WHERE a.action = 'decision:hold'
    """)
    row = cur.fetchone()
    held_count, held_amount = row["c"], row["total"]

    conn.close()
    return {
        "total_invoices": total_invoices,
        "total_amount": total_amount,
        "sent_count": sent_count,
        "sent_amount": sent_amount,
        "rejected_count": rejected_count,
        "rejected_amount": rejected_amount,
        "promised_count": promised_count,
        "promised_amount": promised_amount,
        "escalated_count": escalated_count,
        "escalated_amount": escalated_amount,
        "held_count": held_count,
        "held_amount": held_amount,
        "recovery_rate_pct": round((sent_amount / total_amount * 100), 1) if total_amount else 0,
    }