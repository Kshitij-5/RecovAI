import csv
from datetime import datetime
 
import db
import llm
import workflow

# In a real system this would be datetime.now(). We hardcode it here so the
# demo is reproducible no matter when you run it (and when you present it).
TODAY = datetime(2026, 9, 1)

def load_invoices(path):
    """Read the CSV into a list of dicts, with types converted."""
    invoices = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            invoices.append({
                "invoice_id": row["invoice_id"],
                "customer_name": row["customer_name"],
                "amount": float(row["amount"]),
                "due_date": datetime.strptime(row["due_date"], "%Y-%m-%d"),
                "previous_reminders": int(row["previous_reminders"]),
            })
    return invoices

def compute_days_overdue(invoice):
    delta = TODAY - invoice["due_date"]
    return delta.days

def priority_score(invoice, days_overdue):
    # Bigger amount, more overdue, more reminders already sent (i.e. more
    # ignored) -> higher priority to act on. This is intentionally simple
    # and explainable -- that's the point, not a black box.
    return invoice["amount"] * days_overdue * (invoice["previous_reminders"] + 1)

def get_overdue_invoices(invoices):
    overdue = []
    for inv in invoices:
        days = compute_days_overdue(inv)
        if days > 0:
            inv["days_overdue"] = days
            inv["score"] = priority_score(inv, days)
            overdue.append(inv)
    return overdue

def main():
    db.init_db()

    invoices = load_invoices("invoices.csv")
    overdue = get_overdue_invoices(invoices)
    overdue.sort(key=lambda x: x["score"], reverse=True)

    print(f"{'Invoice':<10}{'Customer':<20}{'Amount':<10}{'Days OD':<10}{'Reminders':<10}{'Score':<12}")
    for inv in overdue:
        print(f"{inv['invoice_id']:<10}{inv['customer_name']:<20}"
              f"{inv['amount']:<10.0f}{inv['days_overdue']:<10}"
              f"{inv['previous_reminders']:<10}{inv['score']:<12.0f}")
        db.upsert_invoice(inv)

    print("\n---- Processing recovery messages ----\n")
    for inv in overdue:
        stop, reason = workflow.should_stop(inv["invoice_id"])
        if stop:
            db.log_action(inv["invoice_id"], "stopped", reason)
            print(f"[{inv['invoice_id']}] {inv['customer_name']}: STOPPED -- {reason}")
            continue

        # This is the agentic decision step: the model, not our code,
        # determines the right intervention from a fixed, bounded menu.
        action, reasoning = llm.decide_intervention(inv)
        db.log_action(inv["invoice_id"], f"decision:{action}", reasoning)

        if action == "hold":
            print(f"[{inv['invoice_id']}] {inv['customer_name']}: HELD -- {reasoning}")
            continue

        if action == "escalate_to_human":
            print(f"[{inv['invoice_id']}] {inv['customer_name']}: ESCALATED -- {reasoning}")
            continue

        # action == "draft_reminder"
        message = llm.draft_message(inv)
        db.save_message(inv["invoice_id"], message)
        db.log_action(inv["invoice_id"], "drafted")
        message_id = db.get_latest_message_id(inv["invoice_id"])

        workflow.review_and_send(inv, message, message_id)

    summary = db.get_recovery_summary()
    print("\n---- Recovery Simulation Summary ----")
    print(f"Total overdue invoices:  {summary['total_invoices']}  (Rs. {summary['total_amount']:.0f})")
    print(f"Approved & sent:         {summary['sent_count']}  (Rs. {summary['sent_amount']:.0f})")
    print(f"Rejected:                {summary['rejected_count']}  (Rs. {summary['rejected_amount']:.0f})")
    print(f"Promise-to-pay logged:   {summary['promised_count']}  (Rs. {summary['promised_amount']:.0f})")
    print(f"Escalated to human:      {summary['escalated_count']}  (Rs. {summary['escalated_amount']:.0f})")
    print(f"Held (too early):        {summary['held_count']}  (Rs. {summary['held_amount']:.0f})")
    print(f"Recovery rate (approved amount / total overdue): {summary['recovery_rate_pct']}%")

if __name__ == "__main__":
    main()