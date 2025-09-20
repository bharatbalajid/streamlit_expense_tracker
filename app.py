# app.py
import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime
import plotly.express as px
from bson.objectid import ObjectId
import io

# --------------------------
# PDF generation (optional)
# --------------------------
HAS_REPORTLAB = True
try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
except Exception:
    HAS_REPORTLAB = False

# --------------------------
# Page config
# --------------------------
st.set_page_config(page_title="💰 Expense Tracker", layout="wide", initial_sidebar_state="collapsed")

# --------------------------
# MongoDB Connection
# --------------------------
MONGO_URI = st.secrets.get("mongo", {}).get("uri")
if not MONGO_URI:
    st.error("MongoDB URI not configured in .streamlit/secrets.toml")
    st.stop()

client = MongoClient(MONGO_URI)
db = client["expense_tracker"]
collection = db["expenses"]

# --------------------------
# Session defaults
# --------------------------
for k, v in {
    "authenticated": False,
    "username": None,
    "is_admin": False,
    "_login_error": None,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# --------------------------
# Login CSS (scroll locked)
# --------------------------
LOGIN_CSS = """
<style>
/* Disable scrolling on login */
html, body, [data-testid="stAppViewContainer"], [data-testid="stAppViewBlockContainer"] {
  overflow: hidden !important;
  height: 100% !important;
}

/* Background */
[data-testid="stApp"] {
  background: #0b0d0f;
}

/* Full height flexbox center */
.login-outer {
  height: 100vh;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 0;
  margin: 0;
}

/* Card */
.login-card {
  width: 380px;
  background: rgba(255,255,255,0.04);
  border-radius: 14px;
  padding: 32px;
  box-shadow: 0 12px 40px rgba(0,0,0,0.5);
  text-align: center;
}

/* Title + subtitle */
.login-title { font-size: 24px; font-weight: 800; margin-bottom: 6px; }
.login-sub { font-size: 14px; color:#9aa3ad; margin-bottom: 20px; }

/* Inputs */
.stTextInput>div>div>input, .stPassword>div>div>input {
  background: #15171a !important;
  color: #e6eef3 !important;
  border-radius: 10px;
  padding: 12px 14px;
  height: 44px;
  border: 1px solid rgba(255,255,255,0.08);
}

/* Button */
.stButton > button {
  width: 100% !important;
  padding: 12px;
  border-radius: 10px;
  background: linear-gradient(90deg,#2b7cff,#2ec4b6) !important;
  color: #fff !important;
  font-weight: 600 !important;
  border: none !important;
  margin-top: 12px;
}
</style>
"""

# --------------------------
# Login handler
# --------------------------
def do_login(user: str, pwd: str):
    secret_user = st.secrets.get("admin", {}).get("username")
    secret_pass = st.secrets.get("admin", {}).get("password")
    if secret_user is None or secret_pass is None:
        st.session_state["_login_error"] = "Admin credentials not configured in secrets."
        return False
    if user == secret_user and pwd == secret_pass:
        st.session_state["authenticated"] = True
        st.session_state["username"] = user
        st.session_state["is_admin"] = True
        st.session_state["_login_error"] = None
        return True
    else:
        st.session_state["_login_error"] = "Invalid username or password."
        return False

# --------------------------
# Login screen
# --------------------------
def show_login():
    st.markdown(LOGIN_CSS, unsafe_allow_html=True)

    # Outer centered container
    st.markdown('<div class="login-outer">', unsafe_allow_html=True)
    st.markdown('<div class="login-card">', unsafe_allow_html=True)

    st.markdown('<div class="login-title">💰 Expense Tracker</div>', unsafe_allow_html=True)
    st.markdown('<div class="login-sub">Please sign in to continue</div>', unsafe_allow_html=True)

    user = st.text_input("Username", key="__login_user", placeholder="admin")
    pwd = st.text_input("Password", type="password", key="__login_pwd", placeholder="••••••••")

    if st.session_state.get("_login_error"):
        st.error(st.session_state.get("_login_error"))

    if st.button("Sign in"):
        success = do_login(user, pwd)
        if success:
            st.experimental_rerun()

    st.markdown('</div></div>', unsafe_allow_html=True)

# --------------------------
# PDF generator
# --------------------------
def generate_pdf_bytes(df: pd.DataFrame, title: str = "Expense Report") -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph(title, styles["Title"]))
    elems.append(Spacer(1, 12))
    total = df["amount"].sum()
    elems.append(Paragraph(f"Total expenses: ₹ {total:.2f}", styles["Normal"]))
    elems.append(Spacer(1, 12))
    df_export = df.copy()
    if "timestamp" in df_export.columns:
        df_export["timestamp"] = df_export["timestamp"].astype(str)
    cols = ["timestamp", "category", "friend", "amount", "notes"]
    table_data = [cols] + [[str(r.get(c, "")) for c in cols] for _, r in df_export.iterrows()]
    tbl = Table(table_data, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#2b2b2b")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
    ]))
    elems.append(tbl)
    doc.build(elems)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

# --------------------------
# Main app UI
# --------------------------
def show_app():
    # Restore scrolling
    st.markdown("""
    <style>
    html, body, [data-testid="stAppViewContainer"], [data-testid="stAppViewBlockContainer"] {
      overflow: auto !important;
      height: auto !important;
    }
    </style>
    """, unsafe_allow_html=True)

    st.title("💰 Personal Expense Tracker")

    with st.sidebar:
        st.header("🔒 Account")
        st.write(f"User: **{st.session_state.get('username','-')}**")
        if st.session_state.get("is_admin"):
            st.success("Admin")
        if st.button("Logout"):
            for k in ["authenticated", "username", "is_admin"]:
                if k in st.session_state:
                    del st.session_state[k]
            st.experimental_rerun()

    categories = ["Food", "Cinema", "Groceries", "Vegetables", "Others"]
    friends = ["Iyyappa", "Gokul", "Balaji", "Magesh", "Others"]

    with st.form("expense_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            category = st.selectbox("Expense Type", categories)
            if category == "Others":
                category_comment = st.text_input("Enter custom category")
                if category_comment.strip():
                    category = category_comment
        with col2:
            friend = st.selectbox("Who Spent?", friends)
            if friend == "Others":
                friend_comment = st.text_input("Enter custom friend name")
                if friend_comment.strip():
                    friend = friend_comment
        amount = st.number_input("Amount (₹)", min_value=1.0, step=1.0)
        notes = st.text_area("Comments / Notes (optional)")
        submitted = st.form_submit_button("💾 Save Expense")
        if submitted:
            collection.insert_one({
                "category": category,
                "friend": friend,
                "amount": float(amount),
                "notes": notes,
                "timestamp": datetime.now()
            })
            st.success("✅ Expense saved successfully!")

    data = list(collection.find())
    if data:
        df = pd.DataFrame(data)
        df["_id"] = df["_id"].astype(str)

        st.subheader("📊 All Expenses (Manage)")
        delete_ids = []
        for i, row in df.iterrows():
            c1,c2,c3,c4,c5,c6 = st.columns([2,2,2,2,2,1])
            with c1: st.write(row.get("timestamp"))
            with c2: st.write(row.get("category"))
            with c3: st.write(row.get("friend"))
            with c4: st.write(f"₹ {row.get('amount')}")
            with c5: st.write(row.get("notes") or "-")
            with c6:
                if st.checkbox("❌", key=row["_id"]):
                    delete_ids.append(row["_id"])
        if delete_ids and st.button("🗑️ Delete Selected"):
            for del_id in delete_ids:
                collection.delete_one({"_id": ObjectId(del_id)})
            st.experimental_rerun()

        if st.session_state.get("is_admin"):
            st.markdown("---")
            st.subheader("⚙️ Admin Controls")
            if st.button("🔥 Delete All Expenses (Admin)"):
                collection.delete_many({})
                st.warning("⚠️ All expenses deleted by admin.")
            if HAS_REPORTLAB:
                pdf_bytes = generate_pdf_bytes(df, title="Expense Report")
                st.download_button("⬇️ Download PDF (Admin)", data=pdf_bytes,
                                   file_name="expenses_report.pdf", mime="application/pdf")

        st.metric("💵 Total Spending", f"₹ {df['amount'].sum():.2f}")
        st.plotly_chart(px.bar(df.groupby("category")["amount"].sum().reset_index(),
                               x="category", y="amount", text="amount", color="category"), use_container_width=True)
        st.plotly_chart(px.bar(df.groupby("friend")["amount"].sum().reset_index(),
                               x="friend", y="amount", text="amount", color="friend"), use_container_width=True)
        st.plotly_chart(px.pie(df.groupby("category")["amount"].sum().reset_index(),
                               names="category", values="amount", title="Expenses by Category"), use_container_width=True)
    else:
        st.info("No expenses yet. Add your first one above")

# --------------------------
# App entry
# --------------------------
if not st.session_state.get("authenticated"):
    show_login()
else:
    show_app()