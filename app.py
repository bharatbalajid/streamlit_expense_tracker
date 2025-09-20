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
st.set_page_config(page_title="💰 Expense Tracker", layout="wide")

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
    "__login_email": "",
    "__login_pwd": ""
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# --------------------------
# Styling
# --------------------------
LOGIN_STYLE = """
<style>
[data-testid="stApp"] > div:first-child { display:flex; justify-content:center; }
.login-wrapper { width:760px; max-width:calc(100%-32px); margin:36px auto; display:flex; gap:40px; align-items:center; justify-content:center; }
.login-brand { width:220px; min-width:160px; display:flex; align-items:flex-start; justify-content:center; flex-direction:column; gap:12px; }
.brand-logo { width:56px; height:56px; border-radius:8px; background:linear-gradient(135deg,#2b7cff,#2ec4b6); display:flex; align-items:center; justify-content:center; color:white; font-weight:700; font-size:20px; }
.login-card { background: rgba(255,255,255,0.02); border-radius:12px; padding:36px; box-shadow:0 8px 40px rgba(0,0,0,0.35); display:flex; flex-direction:column; min-width:360px; }
.login-title { font-size:28px; font-weight:700; margin-bottom:6px; } .login-sub { color:#8f98a3; margin-bottom:18px; }
.stTextInput>div>div>input, .stTextInput>div>div>textarea { height:46px; padding:10px 12px; border-radius:8px; }
.helper-row { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-top:6px; margin-bottom:14px; color:#9aa3ad; font-size:14px; }
.stButton>button.primary-btn { background: linear-gradient(90deg,#2b7cff,#2ec4b6); color: white; width:100%; padding:12px 14px; border-radius:10px; border:none; font-weight:600; box-shadow:0 6px 18px rgba(46,196,182,0.12); }
.google-btn { width:100%; display:flex; align-items:center; justify-content:center; gap:10px; padding:10px 12px; border-radius:10px; border:1px solid rgba(150,150,150,0.12); background:transparent; cursor:pointer; }
.forgot-link { color:#2b7cff; text-decoration:none; font-size:14px; }
@media (max-width:880px) { .login-wrapper { flex-direction:column; gap:18px; padding:18px; } .login-brand { order:-1; width:100%; justify-content:flex-start; } .login-card { width:100%; padding:20px; } }
</style>
"""

# --------------------------
# Callbacks
# --------------------------
def login_callback_plain():
    """Read inputs from session_state and authenticate."""
    user = st.session_state.get("__login_email", "").strip()
    pwd = st.session_state.get("__login_pwd", "")
    secret_user = st.secrets.get("admin", {}).get("username")
    secret_pass = st.secrets.get("admin", {}).get("password")

    if secret_user is None or secret_pass is None:
        st.session_state["_login_error"] = "Admin credentials not set in .streamlit/secrets.toml"
        return

    if user == secret_user and pwd == secret_pass:
        st.session_state["authenticated"] = True
        st.session_state["username"] = user
        st.session_state["is_admin"] = True
        st.session_state["_login_error"] = None
        # immediate rerun if supported
        if hasattr(st, "experimental_rerun"):
            st.experimental_rerun()
    else:
        st.session_state["_login_error"] = "Invalid credentials"

def logout_callback_plain():
    for k in ["authenticated", "username", "is_admin"]:
        if k in st.session_state:
            del st.session_state[k]
    st.session_state.update({"authenticated": False, "username": None, "is_admin": False})
    st.session_state["_login_error"] = None
    if hasattr(st, "experimental_rerun"):
        st.experimental_rerun()

# --------------------------
# Login UI (NO st.form) -> single click
# --------------------------
def show_login():
    st.markdown(LOGIN_STYLE, unsafe_allow_html=True)
    st.markdown("<div class='login-wrapper'>", unsafe_allow_html=True)

    # Centered login card only
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>💰 Expense Tracker</div>", unsafe_allow_html=True)
    st.markdown("<div class='login-sub'>Please sign in to continue</div>", unsafe_allow_html=True)

    # Inputs
    st.text_input("Email address", key="__login_email", placeholder="you@example.com")
    st.text_input("Password", type="password", key="__login_pwd")

    # Login button
    st.button("Sign in", on_click=login_callback_plain, key="__login_btn")

    # Error message if login failed
    if st.session_state.get("_login_error"):
        st.error(st.session_state.get("_login_error"))

    st.markdown("</div>", unsafe_allow_html=True)  # close login-card
    st.markdown("</div>", unsafe_allow_html=True)  # close wrapper

# --------------------------
# PDF generator (same as before)
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
# Main app UI
# --------------------------
def show_app():
    st.title("💰 Personal Expense Tracker")

    with st.sidebar:
        st.header("🔒 Account")
        st.write(f"User: **{st.session_state.get('username','-')}**")
        if st.session_state.get("is_admin"):
            st.success("Admin")
        st.button("Logout", on_click=logout_callback_plain, key="__logout_btn")

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

    # Show data
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
# Entry point
# --------------------------
if not st.session_state.get("authenticated"):
    show_login()
else:
    show_app()