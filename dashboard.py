import streamlit as st
import pandas as pd
import db

st.set_page_config(page_title="Receivable Recovery Agent", layout="wide")
st.title("RecovAI")
st.caption("AI-assisted B2B invoice recovery - Razorpay AI Buildathon 2026")

summary = db.get_recovery_summary()

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total Overdue", f"Rs. {summary['total_amount']:,.0f}", f"{summary['total_invoices']} invoices")
col2.metric("Approved & Sent", f"Rs. {summary['sent_amount']:,.0f}", f"{summary['sent_count']} invoices")
col3.metric("Recovery Rate", f"{summary['recovery_rate_pct']}%")
col4.metric("Escalated / Held", f"{summary['escalated_count']} / {summary['held_count']}")
col5.metric("Promised / Rejected", f"{summary['promised_count']} / {summary['rejected_count']}")

st.divider()

st.subheader("Invoices")
conn = db.get_connection()
invoices_df = pd.read_sql_query("SELECT * FROM invoices ORDER BY score DESC", conn)
st.dataframe(invoices_df, use_container_width=True)

st.subheader("Message History")
messages_df = pd.read_sql_query("SELECT * FROM messages ORDER BY created_at DESC", conn)
st.dataframe(messages_df, use_container_width=True)

st.subheader("Audit Trail")
audit_df = pd.read_sql_query("SELECT * FROM audit_log ORDER BY created_at DESC", conn)
st.dataframe(audit_df, use_container_width=True)

conn.close()

st.caption("This dashboard is read-only. Run `python3 main.py` in the terminal to process a new batch and approve/reject messages.")