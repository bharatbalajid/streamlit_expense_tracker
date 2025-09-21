# app.py
import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime
from bson.objectid import ObjectId
import io
import hashlib
import plotly.express as px

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
DB_NAME = st.secrets.get("mongo", {}).get("db", "expense_tracker")
COLLECTION_NAME = st.secrets.get("mongo", {}).get("collection", "expenses")
USERS_COLLECTION = "users"

if not MONGO_URI:
    st.error("MongoDB URI not configured in .streamlit/secrets.toml")
    st.stop()

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]
users_col = db[USERS_COLLECTION]

# --------------------------
# Helpers: password hashing
# --------------------------
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

# Ensure super-admin exists from secrets
def ensure_superadmin():
    secret_user = st.secrets.get("admin", {}).get("username")
    secret_pass = st.secrets.get("admin", {}).get("password")
    if secret_user and secret_pass:
        existing = users_col.find_one({"username": secret_user})
        if not existing:
            users_col.insert_one({
                "username": secret_user,
                "password_hash": hash_password(secret_pass),
                "role": "admin",
                "created_at": datetime.utcnow()
            })

ensure_superadmin()

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
# Authentication functions
# --------------------------
def login():
    user = st.session_state.get("login_user", "").strip()
    pwd = st.session_state.get("login_pwd", "")
    if not user or not pwd:
        st.session_state["_login_error"] = "Provide both username and password."
        return

    u = users_col.find_one({"username": user})
    if not u:
        st.session_state["_login_error"] = "Invalid username or password."
        return

    if u.get("password_hash") == hash_password(pwd):
        st.session_state["authenticated"] = True
        st.session_state["username"] = user
        st.session_state["is_admin"] = (u.get("role") == "admin")
        st.session_state["_login_error"] = None
    else:
        st.session_state["_login_error"] = "Invalid username or password."

def logout():
    st.session_state["authenticated"] = False
    st.session_state["username"] = None
    st.session_state["is_admin"] = False
    st.session_state["_login_error"] = None

# Admin: create user
def create_user(username: str, password: str, role: str = "user"):
    username = (username or "").strip()
    if not username or not password:
        st.error("Provide username and password.")
        return
    if users_col.find_one({"username": username}):
        st.error("User already exists.")
        return
    users_col.insert_one({
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "created_at": datetime.utcnow()
    })
    st.success(f"User '{username}' created with role '{role}'.")

# --------------------------
# PDF Export helper
# --------------------------
def generate_pdf_bytes(df: pd.DataFrame, title: str = "Expense Report") -> bytes:
    if not HAS_REPORTLAB:
        raise RuntimeError("reportlab not available")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20
    )
    styles = getSampleStyleSheet()
    elems = []

    elems.append(Paragraph(title, styles["Title"]))
    elems.append(Spacer(1, 12))
    total = df["amount"].sum() if "amount" in df.columns else 0.0
    elems.append(Paragraph(
        f"Total expenses: ₹ {total:.2f} — Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        styles["Normal"]
    ))
    elems.append(Spacer(1, 12))

    df_export = df.copy()
    if "timestamp" in df_export.columns:
        df_export["timestamp"] = df_export["timestamp"].astype(str)

    cols = [c for c in ["timestamp", "category", "friend", "amount", "notes", "owner"] if c in df_export.columns]
    table_data = [cols]
    for _, r in df_export.iterrows():
        table_data.append([str(r.get(c, "")) for c in cols])

    tbl = Table(table_data, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#2b2b2b")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))
    elems.append(tbl)
    doc.build(elems)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

# --------------------------
# Generate PDF for a friend (friend field)
# --------------------------
def generate_friend_pdf_bytes(friend_name: str) -> bytes:
    if not friend_name:
        raise ValueError("friend_name required")
    docs = list(collection.find({"friend": friend_name}))
    if not docs:
        empty_df = pd.DataFrame(columns=["timestamp", "category", "friend", "amount", "notes", "owner"])
        title = f"Expense Report - Friend: {friend_name} (No records)"
        return generate_pdf_bytes(empty_df, title=title)
    df = pd.DataFrame(docs)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    if "_id" in df.columns:
        df = df.drop(columns=["_id"])
    title = f"Expense Report - Friend: {friend_name}"
    return generate_pdf_bytes(df, title=title)

# --------------------------
# Main App
# --------------------------
def show_app():
    st.title("💰 Personal Expense Tracker")

    # Sidebar: Login / Logout
    with st.sidebar:
        st.header("🔒 Account")
        if not st.session_state["authenticated"]:
            st.text_input("Username", key="login_user")
            st.text_input("Password", type="password", key="login_pwd")
            st.button("Login", on_click=login)
            if st.session_state["_login_error"]:
                st.error(st.session_state["_login_error"])
        else:
            st.write(f"User: **{st.session_state['username']}**")
            if st.session_state["is_admin"]:
                st.success("Admin")
            st.button("Logout", on_click=logout)

    # Show message if not logged in
    if not st.session_state["authenticated"]:
        st.markdown(
            """
            <div style="display:flex; align-items:center; justify-content:center; height:70vh; color:#fff; font-size:24px;">
                🔒 Please log in from the sidebar to access the Expense Tracker.
            </div>
            """,
            unsafe_allow_html=True
        )
        return  # stop further rendering until user logs in

    # Expense Form
    categories = ["Food", "Cinema", "Groceries", "Vegetables", "Others"]
    grocery_subcategories = [
        "Vegetables", "Fruits", "Milk & Dairy", "Rice & Grains", "Lentils & Pulses",
        "Spices & Masalas", "Oil & Ghee", "Snacks & Packaged Items", "Bakery & Beverages",
        "Medical & Household Essentials"
    ]
    friends = ["Iyyappa", "Gokul", "Balaji", "Magesh", "Others"]

    with st.form("expense_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            category = st.selectbox("Expense Type", categories, key="expense_category")
            if category == "Groceries":
                subcat = st.selectbox("Grocery Subcategory", grocery_subcategories, key="expense_grocery_subcat")
                category = f"Groceries - {subcat}"
            elif category == "Others":
                category_comment = st.text_input("Enter custom category", key="expense_custom_category")
                if category_comment.strip():
                    category = category_comment
        with col2:
            friend = st.selectbox("Who Spent?", friends, key="expense_friend")
            if friend == "Others":
                friend_comment = st.text_input("Enter custom friend name", key="expense_custom_friend")
                if friend_comment.strip():
                    friend = friend_comment
        amount = st.number_input("Amount (₹)", min_value=1.0, step=1.0, key="expense_amount")
        notes = st.text_area("Comments / Notes (optional)", key="expense_notes")
        submitted = st.form_submit_button("💾 Save Expense")
        if submitted:
            collection.insert_one({
                "category": category,
                "friend": friend,
                "amount": float(amount),
                "notes": notes,
                "timestamp": datetime.now(),
                "owner": st.session_state["username"]
            })
            st.success("✅ Expense saved successfully!")

    # Admin Controls
    if st.session_state["is_admin"]:
        st.markdown("---")
        st.subheader("⚙️ Admin Controls")

        with st.expander("Create new user"):
            with st.form("create_user_form"):
                new_username = st.text_input("Username", key="create_user_username")
                new_password = st.text_input("Password", type="password", key="create_user_password")
                new_role = st.selectbox("Role", ["user", "admin"], key="create_user_role")
                create_submitted = st.form_submit_button("Create User")
                if create_submitted:
                    create_user(new_username, new_password, new_role)

        if st.button("🔥 Delete All Expenses (Admin)", key="delete_all_admin"):
            collection.delete_many({})
            st.warning("⚠️ All expenses deleted by admin.")

    # Show Expenses (Admin sees all; users see only their own)
    if st.session_state["is_admin"]:
        data = list(collection.find())
    else:
        data = list(collection.find({"owner": st.session_state["username"]}))

    if data:
        df = pd.DataFrame(data)
        df["_id"] = df["_id"].astype(str)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")

        st.subheader("📊 All Expenses (Manage)")
        delete_ids = []

        for i, row in df.iterrows():
            checkbox_key = f"del_{row['_id']}"
            c1,c2,c3,c4,c5,c6 = st.columns([2,2,2,2,2,1])
            with c1: st.write(row.get("timestamp"))
            with c2: st.write(row.get("category"))
            with c3: st.write(row.get("friend"))
            with c4: st.write(f"₹ {row.get('amount')}")
            with c5: st.write(row.get("notes") or "-")
            with c6:
                # Only show delete checkbox to admins
                if st.session_state["is_admin"]:
                    if st.checkbox("❌", key=checkbox_key):
                        delete_ids.append(row["_id"])
                else:
                    st.write("")  # placeholder to keep layout

        # Delete selected (admin only)
        if st.session_state["is_admin"]:
            if delete_ids and st.button("🗑️ Delete Selected", key="delete_selected_admin"):
                for del_id in delete_ids:
                    try:
                        collection.delete_one({"_id": ObjectId(del_id)})
                    except Exception:
                        collection.delete_one({"_id": del_id})
                st.success("Deleted selected expenses.")
        else:
            st.info("You cannot delete expenses. Contact admin for deletions.")

        # Download: everyone can download the list they can view
        try:
            df_download = df.copy()
            if "_id" in df_download.columns:
                df_download = df_download.drop(columns=["_id"])

            if HAS_REPORTLAB:
                pdf_title = f"Expense Report - {st.session_state['username']}" if not st.session_state["is_admin"] else "Expense Report - Admin View"
                pdf_bytes = generate_pdf_bytes(df_download, title=pdf_title)
                st.download_button("⬇️ Download PDF (Visible Expenses)", data=pdf_bytes, file_name="expenses_report.pdf", mime="application/pdf")
            else:
                st.info("PDF export requires 'reportlab' package.")

            # --------------------------
            # Friend-based PDF export (active)
            # --------------------------
            st.markdown("---")
            st.subheader("👥 Download Friend's Expense Report")

            friends_available = sorted(df_download['friend'].dropna().unique().tolist()) if 'friend' in df_download.columns else []

            if st.session_state['is_admin']:
                selected_friend = st.selectbox("Select friend", options=friends_available, key="select_friend_for_pdf") if friends_available else None
            else:
                selected_friend = st.selectbox("Select friend", options=friends_available, key="select_friend_for_pdf_user") if friends_available else None

            if HAS_REPORTLAB and selected_friend:
                try:
                    friend_pdf = generate_friend_pdf_bytes(selected_friend)
                    filename = f"expenses_friend_{selected_friend}.pdf"
                    st.download_button(f"⬇️ Download PDF for friend: {selected_friend}", data=friend_pdf, file_name=filename, mime="application/pdf")
                except Exception as e:
                    st.error(f"Failed to generate friend PDF: {e}")

        except Exception as e:
            st.error(f"Failed to prepare download: {e}")

        st.metric("💵 Total Spending", f"₹ {df['amount'].sum():.2f}" if "amount" in df.columns else "₹ 0.00")

        cat_summary = df.groupby("category")["amount"].sum().reset_index() if "category" in df.columns and "amount" in df.columns else pd.DataFrame(columns=["category", "amount"])
        friend_summary = df.groupby("friend")["amount"].sum().reset_index() if "friend" in df.columns and "amount" in df.columns else pd.DataFrame(columns=["friend", "amount"])

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("📌 Spending by Category")
            if not cat_summary.empty:
                st.plotly_chart(px.bar(cat_summary, x="category", y="amount", text="amount", color="category"), use_container_width=True)
            else:
                st.info("No category data to plot.")
        with c2:
            st.subheader("👥 Spending by Friend")
            if not friend_summary.empty:
                st.plotly_chart(px.bar(friend_summary, x="friend", y="amount", text="amount", color="friend"), use_container_width=True)
            else:
                st.info("No friend data to plot.")

        st.subheader("🥧 Category Breakdown")
        if not cat_summary.empty:
            st.plotly_chart(px.pie(cat_summary, names="category", values="amount", title="Expenses by Category"), use_container_width=True)
        else:
            st.info("No category data for pie chart.")

        st.subheader("Summary by Friend")
        if not friend_summary.empty:
            st.table(friend_summary.set_index("friend"))
        else:
            st.info("No friend summary yet.")

    else:
        st.info("No expenses yet. Add your first one above.")

# --------------------------
# App Entry
# --------------------------
if __name__ == "__main__":
    show_app()