import db

# Hard cap on total reminders ever sent for a single invoice by this
# system. This is a deliberate, simple, auditable policy -- not something
# the LLM decides. Business teams could tune this number, but the *rule
# itself* should never live inside a prompt.
MAX_REMINDERS = 3


def should_stop(invoice_id):
    """Returns (stop: bool, reason: str). Checked before we ever draft or
    send a new message for an invoice."""
    invoice = db.get_invoice(invoice_id)

    if invoice and invoice["promise_to_pay"]:
        return True, "promise_to_pay flag is set -- customer has committed to pay"

    sent_count = db.count_sent_messages(invoice_id)
    if sent_count >= MAX_REMINDERS:
        return True, f"reached MAX_REMINDERS cap ({MAX_REMINDERS} sent)"

    return False, ""


def review_and_send(invoice, draft_text, message_id):
    """The human-in-the-loop gate. Nothing is 'sent' (simulated) without
    an explicit yes from a person looking at the actual drafted text."""

    stop, reason = should_stop(invoice["invoice_id"])
    if stop:
        db.log_action(invoice["invoice_id"], "stopped", reason)
        print(f"  -> STOPPED: {reason}")
        return

    print(f"\n--- Review: {invoice['invoice_id']} ({invoice['customer_name']}) ---")
    print(draft_text)
    print("-" * 50)
    choice = input("Approve and send this message? [y/n/p=promise-to-pay logged instead]: ").strip().lower()

    if choice == "y":
        db.update_message_status(message_id, "approved")
        db.log_action(invoice["invoice_id"], "approved", f"message_id={message_id}")
        # In a real system this is where an actual email/SMS send call
        # would happen. We simulate it -- the important part for this
        # build is the decision trail, not real email delivery.
        db.update_message_status(message_id, "sent")
        db.log_action(invoice["invoice_id"], "sent", f"message_id={message_id}")
        print("  -> SENT (simulated)")

    elif choice == "p":
        db.set_promise_to_pay(invoice["invoice_id"])
        db.update_message_status(message_id, "promised")
        db.log_action(invoice["invoice_id"], "promise_to_pay_logged",
                       "human logged a promise-to-pay instead of sending")
        print("  -> Promise-to-pay logged, no message sent, future reminders will stop")

    else:
        db.update_message_status(message_id, "rejected")
        db.log_action(invoice["invoice_id"], "rejected", f"message_id={message_id}")
        print("  -> REJECTED, not sent")