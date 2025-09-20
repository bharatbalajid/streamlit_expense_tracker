# app.py
import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime
import plotly.express as px
from bson.objectid import ObjectId
import io

# --------------------------
# Optional PDF generation (ReportLab)
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
defaults = {
    "authenticated": False,
    "username": None,
    "is_admin": False,
    "_login_error": None,
    "__login_user": "",
    "__login_pwd": ""
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# --------------------------
# Premium login CSS
# --------------------------
LOGIN_CSS_PREMIUM = """
<style>
/* Page background slight radial vignette for depth */
[data-testid="stApp"] {
  background: radial-gradient(1200px 400px at 10% 10%, rgba(43,124,255,0.04), transparent 6%),
              radial-gradient(1200px 400px at 90% 90%, rgba(46,196,182,0.02), transparent 6%),
              #0b0d0f;
}

/* Center the content vertically using a tall container */
.login-outer {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 78vh;
  padding: 28px;
}

/* Card */
.login-card {
  width: 780px;
  max-width: calc(100% - 48px);
  background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.015));
  border-radius: 16px;
  padding: 34px;
  box-shadow:
    0 8px 30px rgba(2,6,23,0.6),
    inset 0 1px 0 rgba(255,255,255,0.01);
  display: grid;
  grid-template-columns: 1fr 420px;
  gap: 28px;
  align-items: center;
}

/* Left promo area */
.login-left {
  padding: 12px 8px;
}
.brand-bubble {
  width:72px; height:72px; border-radius:16px;
  display:flex; align-items:center; justify-content:center;
  font-weight:800; font-size:22px; color:white;
  background: linear-gradient(135deg,#2b7cff,#2ec4b6);
  box-shadow: 0 8px 24px rgba(46,196,182,0.08);
  margin-bottom: 14px;
}
.hero-title {
  font-size: 28px;
  font-weight: 800;
  margin-bottom: 8px;
  color: #fff;
}
.hero-sub {
  color: #9aa3ad;
  line-height: 1.5;
}

/* Right form area */
.login-right {
  padding: 6px 8px;
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(0,0,0,0.02), rgba(255,255,255,0.005));
}

/* Inputs - large and rounded */
.stTextInput>div>div>input,
.stTextInput>div>div>textarea {
  height:48px;
  padding: 12px 14px;
  border-radius:10px;
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.02);
  color: #e6eef3;
}

/* Small labels style */
.label-small {
  font-size:13px;
  color:#9aa3ad;
  margin-bottom:6px;
}

/* Primary button with gradient */
.signin-btn .stButton>button {
  background: linear-gradient(90deg,#2b7cff,#2ec4b6);
  color: white;
  border: none;
  padding: 12px 14px;
  border-radius: 10px;
  font-weight: 700;
  width: 100%;
  box-shadow: 0 10px 30px rgba(46,196,182,0.12);
}

/* Secondary subtle button */
.cancel-btn .stButton>button {
  background: transparent;
  border: 1px solid rgba(255,255,255,0.04);
  color:#d8e0e6;
  padding: 10px 12px;
  border-radius: 10px;
}

/* small helper row */
.helper-row {
  display:flex; justify-content:space-between; align-items:center; gap:12px;
  margin-top:10px; margin-bottom:8px;
}

/* responsive */
@media (max-width: 900px) {
  .login-card { grid-template-columns: 1fr; padding: 22px; }
  .brand-bubble { width:56px; height:56px; font-size:18px; }
  .hero-title { font-size:22px; }
}
</style>
"""

# --------------------------
# Authentication callbacks
# --------------------------
def login_callback():
    user = st.session_state.get("__login_user", "").strip()
    pwd = st.session_state.get("__login_pwd", "")
    secret_user = st.secrets.get("admin", {}).get("username")
    secret_pass = st.secrets.get("admin", {}).get("password")

    if secret_user is None or secret_pass is None:
        st.session_state["_login_error"] = "Admin credentials not configured in secrets."
        return

    if user == secret_user and pwd == secret_pass:
        st.session_state["authenticated"] = True
        st.session_state["username"] = user
        st.session_state["is_admin"] = True
        st.session_state["_login_error"] = None
        if hasattr(st, "experimental_rerun"):
            st.experimental_rerun()
    else:
        st.session_state["_login_error"] = "Invalid username or password."

def logout_callback():
    for k in ["authenticated", "username", "is_admin"]:
        if k in st.session_state:
            del st.session_state[k]
    for k, v in defaults.items():
        st.session_state[k] = v
    if hasattr(st, "experimental_rerun"):
        st.experimental_rerun()

# --------------------------
# Login UI (ultra clean)
# --------------------------
def show_login():
    st.markdown(LOGIN_CSS_PREMIUM, unsafe_allow_html=True)

    # center using columns to keep parity with Streamlit layout
    left_col, center_col, right_col = st.columns([1, 2, 1])
    with center_col:
        st.markdown('<div class="login-outer">', unsafe_allow_html=True)
        st.markdown('<div class="login-card">', unsafe_allow_html=True)

        # Left marketing / brand
        st.markdown('<div class="login-left">', unsafe_allow_html=True)
        st.markdown('<div class="brand-bubble">💰</div>', unsafe_allow_html=True)
        st.markdown('<div class="hero-title">Expense Tracker</div>', unsafe_allow_html=True)
        st.markdown('<div class="hero-sub">Securely track group expenses, export reports, and manage entries — admin access only.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)  # close login-left

        # Right form
        st.markdown('<div class="login-right">', unsafe_allow_html=True)

        st.markdown('<div class="label-small">Username</div>', unsafe_allow_html=True)
        st.text_input("", key="__login_user", placeholder="admin", label_visibility="collapsed")

        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
        st.markdown('<div class="label-small">Password</div>', unsafe_allow_html=True)
        st.text_input("", type="password", key="__login_pwd", placeholder="••••••••", label_visibility="collapsed")

        if st.session_state.get("_login_error"):
            st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
            st.error(st.session_state.get("_login_error"))

        st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
        cols = st.columns([1, 1])
        with cols[0]:
            # empty or could place "Forgot?" small text
            st.write("")
        with cols[1]:
            st.markdown('<div class="signin-btn">', unsafe_allow_html=True)
            st.button("Sign in", on_click=login_callback, key="__login_btn")
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)  # close login-right

        st.markdown('</div>', unsafe_allow_html=True)  # close login-card
        st.markdown('</div>', unsafe_allow_html=True)  # close login-outer

# --------------------------
# PDF generator
# --------------------------
def generate_pdf_bytes(df: pd.DataFrame, title: str = "Expense Report") -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph(title, styles["Title"]))
    elems.append(Spacer(1, 12))
    total = df["amount"].sum()
    summary_text = f"Total expenses: ₹ {total:.2f} — Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    elems.append(Paragraph(summary_text, styles["Normal"]))
    elems.append(Spacer(1, 12))
    df_export = df.copy()
    if "timestamp" in df_export.columns:
        df_export["timestamp"] = df_export["timestamp"].astype(str)
    cols = [c for c in ["timestamp", "category", "friend", "amount", "notes"] if c in df_export.columns]
    table_data = [cols]
    for _, r in df_export.iterrows():
        row = [str(r.get(c, "")) for c in cols]
        table_data.append(row)
    tbl = Table(table_data, repeatRows=1)
    tbl_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b2b2b")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("FONTNAME", (0,0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ])
    tbl.setStyle(tbl_style)
    elems.append(tbl)
    doc.build(elems)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

# --------------------------
# Main app (after login)
# --------------------------
def show_app():
    st.title("💰 Personal Expense Tracker")

    with st.sidebar:
        st.header("🔒 Account")
        st.write(f"User: **{st.session_state.get('username','-')}**")
        if st.session_state.get("is_admin"):
            st.success("Admin")
        st.button("Logout", on_click=logout_callback, key="__logout_btn")

    # Expense form
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
            expense = {"category": category, "friend": friend, "amount": float(amount), "notes": notes, "timestamp": datetime.now()}
            collection.insert_one(expense)
            st.success("✅ Expense saved successfully!")

    # Show data & analytics
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
        if delete_ids:
            if st.button("🗑️ Delete Selected"):
                for del_id in delete_ids:
                    collection.delete_one({"_id": ObjectId(del_id)})
                st.success(f"Deleted {len(delete_ids)} entries.")
        if st.session_state.get("is_admin"):
            st.markdown("---")
            st.subheader("⚙️ Admin Controls")
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("🔥 Delete All Expenses (Admin)"):
                    collection.delete_many({})
                    st.warning("⚠️ All expenses deleted by admin.")
            with col_b:
                if not HAS_REPORTLAB:
                    st.error("PDF export requires 'reportlab' in requirements.txt.")
                else:
                    df_export = df.copy()
                    pdf_bytes = generate_pdf_bytes(df_export, title="Expense Report")
                    st.download_button("⬇️ Download PDF (Admin)", data=pdf_bytes, file_name="expenses_report.pdf", mime="application/pdf")
        total = df["amount"].sum()
        st.metric("💵 Total Spending", f"₹ {total:.2f}")
        c1, c2 = st.columns(2)
        with c1:
            cat_summary = df.groupby("category")["amount"].sum().reset_index()
            st.subheader("📌 Spending by Category")
            fig1 = px.bar(cat_summary, x="category", y="amount", text="amount", color="category")
            st.plotly_chart(fig1, use_container_width=True)
        with c2:
            friend_summary = df.groupby("friend")["amount"].sum().reset_index()
            st.subheader("👥 Spending by Friend")
            fig2 = px.bar(friend_summary, x="friend", y="amount", text="amount", color="friend")
            st.plotly_chart(fig2, use_container_width=True)
        st.subheader("🥧 Category Breakdown")
        fig3 = px.pie(cat_summary, names="category", values="amount", title="Expenses by Category")
        st.plotly_chart(fig3, use_container_width=True)
        st.subheader("Summary by Friend")
        st.table(friend_summary.set_index("friend"))
    else:
        st.info("No expenses yet. Add your first one above")

# --------------------------
# Entry
# --------------------------
if not st.session_state.get("authenticated"):
    show_login()
else:
    show_app()