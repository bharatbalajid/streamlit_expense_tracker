# app.py
import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime
import plotly.express as px
from bson.objectid import ObjectId
import io

# --------------------------
# Optional PDF generation
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
# MongoDB connection
# --------------------------
MONGO_URI = st.secrets.get("mongo", {}).get("uri")
DB_NAME = st.secrets.get("mongo", {}).get("db", "expense_tracker")
COLLECTION_NAME = st.secrets.get("mongo", {}).get("collection", "expenses")

if not MONGO_URI:
    st.error("MongoDB URI not configured in .streamlit/secrets.toml")
    st.stop()

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

# --------------------------
# Session defaults
# --------------------------
for key in ["authenticated", "username", "is_admin", "_login_error", "__login_user", "__login_pwd"]:
    if key not in st.session_state:
        st.session_state[key] = False if key == "authenticated" or key == "is_admin" else None

# --------------------------
# Authentication
# --------------------------
def login_callback():
    user = st.session_state.get("__login_user", "").strip()
    pwd = st.session_state.get("__login_pwd", "")
    secret_user = st.secrets.get("admin", {}).get("username")
    secret_pass = st.secrets.get("admin", {}).get("password")
    if user == secret_user and pwd == secret_pass:
        st.session_state["authenticated"] = True
        st.session_state["username"] = user
        st.session_state["is_admin"] = True
        st.session_state["_login_error"] = None
    else:
        st.session_state["_login_error"] = "Invalid username or password."

def logout_callback():
    st.session_state["authenticated"] = False
    st.session_state["username"] = None
    st.session_state["is_admin"] = False
    st.session_state["_login_error"] = None

# --------------------------
# PDF export helper
# --------------------------
def generate_pdf_bytes(df: pd.DataFrame, title: str = "Expense Report") -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph(title, styles["Title"]))
    elems.append(Spacer(1,12))
    total = df["amount"].sum()
    summary_text = f"Total expenses: ₹ {total:.2f} — Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    elems.append(Paragraph(summary_text, styles["Normal"]))
    elems.append(Spacer(1,12))
    df_export = df.copy()
    if "timestamp" in df_export.columns:
        df_export["timestamp"] = df_export["timestamp"].astype(str)
    cols = [c for c in ["timestamp", "category", "friend", "amount", "notes"] if c in df_export.columns]
    table_data = [cols]
    for _, r in df_export.iterrows():
        table_data.append([str(r.get(c,"")) for c in cols])
    tbl = Table(table_data, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#2b2b2b")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE")
    ]))
    elems.append(tbl)
    doc.build(elems)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

# --------------------------
# Show sidebar login
# --------------------------
def show_login():
    st.sidebar.title("🔑 Login")
    st.sidebar.text_input("Username", key="__login_user")
    st.sidebar.text_input("Password", type="password", key="__login_pwd")
    if st.sidebar.button("Login"):
        login_callback()
    if st.session_state["_login_error"]:
        st.sidebar.error(st.session_state["_login_error"])

# --------------------------
# Main app
# --------------------------
def show_app():
    st.sidebar.header("Account")
    st.sidebar.write(f"User: **{st.session_state['username']}**")
    if st.session_state["is_admin"]:
        st.sidebar.success("Admin")
    st.sidebar.button("Logout", on_click=logout_callback)

    st.title("💰 Personal Expense Tracker")

    # Categories
    categories = ["Food", "Cinema", "Groceries", "Vegetables", "Others"]
    grocery_subcategories = [
        "Vegetables","Fruits","Milk & Dairy","Rice & Grains","Lentils & Pulses",
        "Spices & Masalas","Oil & Ghee","Snacks & Packaged Items","Bakery & Beverages","Medical & Household Essentials"
    ]
    friends = ["Iyyappa", "Gokul", "Balaji", "Magesh", "Others"]

    with st.form("expense_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            category = st.selectbox("Expense Type", categories)
            if category == "Groceries":
                subcat = st.selectbox("Grocery Subcategory", grocery_subcategories)
                category = f"Groceries - {subcat}"
            elif category == "Others":
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

    # Show expenses
    data = list(collection.find())
    if data:
        df = pd.DataFrame(data)
        df["_id"] = df["_id"].astype(str)
        st.subheader("📊 All Expenses (Manage)")
        delete_ids = []
        for i,row in df.iterrows():
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

        if st.session_state["is_admin"]:
            st.markdown("---")
            st.subheader("⚙️ Admin Controls")
            if st.button("🔥 Delete All Expenses (Admin)"):
                collection.delete_many({})
                st.warning("⚠️ All expenses deleted by admin.")
                st.experimental_rerun()
            if HAS_REPORTLAB:
                pdf_bytes = generate_pdf_bytes(df)
                st.download_button("⬇️ Download PDF (Admin)", data=pdf_bytes, file_name="expenses_report.pdf", mime="application/pdf")
            else:
                st.warning("PDF export requires 'reportlab'.")

        st.metric("💵 Total Spending", f"₹ {df['amount'].sum():.2f}")

        cat_summary = df.groupby("category")["amount"].sum().reset_index()
        friend_summary = df.groupby("friend")["amount"].sum().reset_index()

        c1,c2 = st.columns(2)
        with c1:
            st.subheader("📌 Spending by Category")
            st.plotly_chart(px.bar(cat_summary, x="category", y="amount", text="amount", color="category"), use_container_width=True)
        with c2:
            st.subheader("👥 Spending by Friend")
            st.plotly_chart(px.bar(friend_summary, x="friend", y="amount", text="amount", color="friend"), use_container_width=True)

        st.subheader("🥧 Category Breakdown")
        st.plotly_chart(px.pie(cat_summary, names="category", values="amount", title="Expenses by Category"), use_container_width=True)
    else:
        st.info("No expenses yet. Add your first one above.")

# --------------------------
# App entry
# --------------------------
if not st.session_state["authenticated"]:
    show_login()
else:
    show_app()
