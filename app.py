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
# Session state defaults
# --------------------------
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "username" not in st.session_state:
    st.session_state["username"] = None
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False

# --------------------------
# Login page styling
# --------------------------
LOGIN_CSS_PREMIUM = """
<style>
/* Page background slight radial vignette for depth */
[data-testid="stApp"] {
  background: radial-gradient(1200px 400px at 10% 10%, rgba(43,124,255,0.04), transparent 6%),
              radial-gradient(1200px 400px at 90% 90%, rgba(46,196,182,0.02), transparent 6%),
              #0b0d0f;
}

/* Disable scrolling when login page is active */
html, body, [data-testid="stAppViewContainer"], [data-testid="stAppViewBlockContainer"] {
  overflow: hidden !important;
  height: 100% !important;
}

/* Center container */
.login-outer {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  padding: 28px;
}

/* Login card */
.login-card {
  background: #111418;
  border-radius: 16px;
  padding: 38px 44px;
  width: 360px;
  box-shadow: 0 8px 30px rgba(0,0,0,0.55);
  text-align: center;
  animation: fadeIn 0.7s ease;
}

/* Title */
.login-title {
  font-size: 26px;
  font-weight: 700;
  color: #fff;
  margin-bottom: 6px;
}
.login-sub {
  color: #a9b2bd;
  font-size: 14px;
  margin-bottom: 22px;
}

/* Inputs */
.stTextInput > div > div > input, .stPassword > div > div > input {
  background: #1b1f25 !important;
  color: #e5e8eb !important;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 14px;
}

/* Buttons */
.stButton > button {
  background: linear-gradient(90deg, #2d89ff, #00d4ff);
  color: #fff !important;
  border: none;
  border-radius: 8px;
  font-weight: 600;
  padding: 10px 0;
  width: 100%;
  margin-top: 8px;
  transition: transform 0.15s ease;
}
.stButton > button:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0,212,255,0.25);
}

/* Animations */
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(18px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
"""

def show_login():
    st.markdown(LOGIN_CSS_PREMIUM, unsafe_allow_html=True)
    st.markdown("<div class='login-outer'><div class='login-card'>", unsafe_allow_html=True)

    st.markdown("<div class='login-title'>💰 Expense Tracker</div>", unsafe_allow_html=True)
    st.markdown("<div class='login-sub'>Please sign in to continue</div>", unsafe_allow_html=True)

    user = st.text_input("Username", placeholder="admin")
    pwd = st.text_input("Password", type="password", placeholder="••••••••")

    if st.button("Sign in"):
        secret_user = st.secrets.get("admin", {}).get("username")
        secret_pass = st.secrets.get("admin", {}).get("password")

        if secret_user is None or secret_pass is None:
            st.error("Admin credentials not configured in .streamlit/secrets.toml")
        elif user == secret_user and pwd == secret_pass:
            st.session_state["authenticated"] = True
            st.session_state["username"] = user
            st.session_state["is_admin"] = True
            st.success("✅ Login successful!")
            st.experimental_rerun()
        else:
            st.error("❌ Invalid credentials")

    st.markdown("</div></div>", unsafe_allow_html=True)

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
# Main App UI
# --------------------------
def show_app():
    # Restore scrolling for main app
    st.markdown("<style>html, body, [data-testid='stAppViewContainer'], [data-testid='stAppViewBlockContainer'] {overflow: auto !important;}</style>", unsafe_allow_html=True)

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
            expense = {
                "category": category,
                "friend": friend,
                "amount": float(amount),
                "notes": notes,
                "timestamp": datetime.now()
            }
            collection.insert_one(expense)
            st.success("✅ Expense saved successfully!")

    data = list(collection.find())
    if data:
        df = pd.DataFrame(data)
        df["_id"] = df["_id"].astype(str)

        st.subheader("📊 All Expenses (Manage)")
        delete_ids = []
        for i, row in df.iterrows():
            c1, c2, c3, c4, c5, c6 = st.columns([2,2,2,2,2,1])
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
            st.success(f"Deleted {len(delete_ids)} entries.")
            st.experimental_rerun()

        if st.session_state.get("is_admin"):
            st.markdown("---")
            st.subheader("⚙️ Admin Controls")
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("🔥 Delete All Expenses (Admin)"):
                    collection.delete_many({})
                    st.warning("⚠️ All expenses deleted by admin.")
            with col_b:
                if HAS_REPORTLAB:
                    df_export = df.copy()
                    pdf_bytes = generate_pdf_bytes(df_export, title="Expense Report")
                    st.download_button("⬇️ Download PDF (Admin)", data=pdf_bytes,
                                       file_name="expenses_report.pdf", mime="application/pdf")
                else:
                    st.error("PDF export requires 'reportlab' in requirements.txt.")

        total = df["amount"].sum()
        st.metric("💵 Total Spending", f"₹ {total:.2f}")

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

        st.subheader("Summary by Friend")
        st.table(friend_summary.set_index("friend"))
    else:
        st.info("No expenses yet. Add your first one above.")

# --------------------------
# App entry
# --------------------------
if not st.session_state["authenticated"]:
    show_login()
else:
    show_app()