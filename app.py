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
# MongoDB Connection (use secrets)
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
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "username" not in st.session_state:
    st.session_state["username"] = None
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False
if "_login_error" not in st.session_state:
    st.session_state["_login_error"] = None

# --------------------------
# STRONG CSS for login page
# - make Streamlit main container full-viewport
# - center its contents
# - hide header/sidebar/footer while on login
# - lock scrolling
# --------------------------
LOGIN_LOCK_CSS = """
<style>
/* Lock page scrolling and make main container full viewport */
html, body, [data-testid="stAppViewContainer"], [data-testid="stAppViewBlockContainer"] {
  height: 100vh !important;
  overflow: hidden !important;
}

/* Make Streamlit's main area use full height and flex center its children */
[data-testid="stAppViewContainer"] > main {
  min-height: 100vh !important;
  height: 100vh !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  padding: 0 !important;
}

/* Hide header, footer and sidebar during login so nothing shifts the layout */
[data-testid="stToolbar"], header, footer, [data-testid="stSidebarNav"], .css-1lsmgbg {
  display: none !important;
}

/* Styling for the login card */
.login-card {
  width: 420px;
  max-width: calc(100% - 40px);
  border-radius: 14px;
  padding: 32px;
  background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01));
  box-shadow: 0 14px 40px rgba(0,0,0,0.6);
  color: #eaf0f6;
}

/* Title and subtitle */
.login-title { font-size: 22px; font-weight: 800; margin-bottom: 6px; color: #fff; }
.login-sub { color: #9aa3ad; margin-bottom: 18px; }

/* Inputs */
.stTextInput>div>div>input, .stTextInput>div>div>textarea, .stPassword>div>div>input {
  background: #14161a !important;
  color: #e6eef3 !important;
  border-radius: 10px;
  padding: 12px 14px;
  height: 46px;
  border: 1px solid rgba(255,255,255,0.02);
}

/* Full-width primary button */
.stButton > button {
  width: 100% !important;
  padding: 12px !important;
  border-radius: 10px !important;
  background: linear-gradient(90deg,#2b7cff,#2ec4b6) !important;
  color: white !important;
  border: none !important;
  font-weight: 700 !important;
  box-shadow: 0 8px 30px rgba(46,196,182,0.12);
}

/* make sure small spacing is consistent */
.login-gap { height: 12px; }

/* Responsive shrink */
@media (max-width: 520px) {
  .login-card { width: calc(100% - 20px); padding: 20px; }
  .login-title { font-size: 20px; }
}
</style>
"""

# --------------------------
# Restore scrolling CSS (to use after login)
# --------------------------
RESTORE_SCROLL_CSS = """
<style>
html, body, [data-testid="stAppViewContainer"], [data-testid="stAppViewBlockContainer"] {
  overflow: auto !important;
  height: auto !important;
}
</style>
"""

# --------------------------
# Authentication helpers
# --------------------------
def do_login(user: str, pwd: str) -> bool:
    secret_user = st.secrets.get("admin", {}).get("username")
    secret_pass = st.secrets.get("admin", {}).get("password")
    if secret_user is None or secret_pass is None:
        st.session_state["_login_error"] = "Admin credentials not configured in .streamlit/secrets.toml"
        return False
    if user == secret_user and pwd == secret_pass:
        st.session_state["authenticated"] = True
        st.session_state["username"] = user
        st.session_state["is_admin"] = True
        st.session_state["_login_error"] = None
        return True
    st.session_state["_login_error"] = "Invalid username or password"
    return False

def do_logout():
    for k in ["authenticated", "username", "is_admin"]:
        if k in st.session_state:
            del st.session_state[k]
    st.session_state["_login_error"] = None

# --------------------------
# Login UI (centered and scroll locked)
# --------------------------
def show_login():
    # inject CSS that locks scroll and centers main container
    st.markdown(LOGIN_LOCK_CSS, unsafe_allow_html=True)

    # Render card in the main area; because main is flex-centered by CSS, this will sit center of viewport
    st.markdown('<div class="login-card" role="dialog" aria-label="Login card">', unsafe_allow_html=True)

    st.markdown('<div class="login-title">💰 Expense Tracker</div>', unsafe_allow_html=True)
    st.markdown('<div class="login-sub">Please sign in to continue</div>', unsafe_allow_html=True)

    # Show inputs (real Streamlit widgets) - they appear inside the card div visually
    username = st.text_input("Username", key="__login_user", placeholder="admin")
    st.markdown('<div class="login-gap"></div>', unsafe_allow_html=True)
    password = st.text_input("Password", type="password", key="__login_pwd", placeholder="••••••••")

    # error message
    if st.session_state.get("_login_error"):
        st.error(st.session_state.get("_login_error"))

    st.markdown('<div class="login-gap"></div>', unsafe_allow_html=True)
    if st.button("Sign in"):
        if do_login(username, password):
            # login success -> trigger rerun so show_app can restore scrolling
            st.experimental_rerun()

    st.markdown("</div>", unsafe_allow_html=True)

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
# Main app UI (restores scrolling)
# --------------------------
def show_app():
    # Restore scrolling and show app normally
    st.markdown(RESTORE_SCROLL_CSS, unsafe_allow_html=True)

    st.title("💰 Personal Expense Tracker")

    with st.sidebar:
        st.header("🔒 Account")
        st.write(f"User: **{st.session_state.get('username','-')}**")
        if st.session_state.get("is_admin"):
            st.success("Admin")
        if st.button("Logout"):
            do_logout()
            st.experimental_rerun()

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
            collection.insert_one({
                "category": category,
                "friend": friend,
                "amount": float(amount),
                "notes": notes,
                "timestamp": datetime.now()
            })
            st.success("✅ Expense saved successfully!")

    # Data display
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
                st.download_button("⬇️ Download PDF (Admin)", data=pdf_bytes, file_name="expenses_report.pdf", mime="application/pdf")
            else:
                st.error("PDF export requires 'reportlab' in requirements.txt.")

        st.metric("💵 Total Spending", f"₹ {df['amount'].sum():.2f}")

        c1, c2 = st.columns(2)
        with c1:
            cat_summary = df.groupby("category")["amount"].sum().reset_index()
            st.subheader("📌 Spending by Category")
            st.plotly_chart(px.bar(cat_summary, x="category", y="amount", text="amount", color="category"), use_container_width=True)
        with c2:
            friend_summary = df.groupby("friend")["amount"].sum().reset_index()
            st.subheader("👥 Spending by Friend")
            st.plotly_chart(px.bar(friend_summary, x="friend", y="amount", text="amount", color="friend"), use_container_width=True)

        st.subheader("🥧 Category Breakdown")
        st.plotly_chart(px.pie(cat_summary, names="category", values="amount", title="Expenses by Category"), use_container_width=True)

    else:
        st.info("No expenses yet. Add your first one above")

# --------------------------
# App entry
# --------------------------
if not st.session_state.get("authenticated"):
    show_login()
else:
    show_app()