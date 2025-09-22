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

# initialize UI keys
if "ui_category" not in st.session_state:
    st.session_state["ui_category"] = None
if "ui_subcategory" not in st.session_state:
    st.session_state["ui_subcategory"] = None
if "ui_friend" not in st.session_state:
    st.session_state["ui_friend"] = None

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
        f"Total expenses: ₹ {total:.2f} — Generated: {datetime.now().strftime('%Y-%m-%d')}",
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
# Generate PDF for a friend
# --------------------------
def generate_friend_pdf_bytes(friend_name: str, query_filter: dict) -> bytes:
    if not friend_name:
        raise ValueError("friend_name required")
    q = query_filter.copy()
    q.update({"friend": friend_name})
    docs = list(collection.find(q))
    if not docs:
        empty_df = pd.DataFrame(columns=["timestamp", "category", "friend", "amount", "notes", "owner"])
        title = f"Expense Report - Friend: {friend_name} (No records)"
        return generate_pdf_bytes(empty_df, title=title)
    df = pd.DataFrame(docs)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d")
    if "_id" in df.columns:
        df = df.drop(columns=["_id"])
    title = f"Expense Report - Friend: {friend_name}"
    return generate_pdf_bytes(df, title=title)

# --------------------------
# Helper: get visible data
# --------------------------
def get_visible_data():
    if st.session_state.get("is_admin"):
        docs = list(collection.find())
    else:
        owner = st.session_state.get("username")
        docs = list(collection.find({"owner": owner}))
    return docs

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

    if not st.session_state["authenticated"]:
        st.markdown(
            """
            <div style="display:flex; align-items:center; justify-content:center; height:70vh; color:#fff; font-size:24px;">
                🔒 Please log in from the sidebar to access the Expense Tracker.
            </div>
            """,
            unsafe_allow_html=True
        )
        return

    # Categories
    categories = ["Food", "Cinema", "Groceries", "Bill Payment", "Medical", "Others"]
    grocery_subcategories = [
        "Vegetables", "Fruits", "Milk & Dairy", "Rice & Grains", "Lentils & Pulses",
        "Spices & Masalas", "Oil & Ghee", "Snacks & Packaged Items", "Bakery & Beverages"
    ]
    bill_payment_subcategories = ["CC", "Electricity Bill", "RD", "Mutual Fund", "Gold Chit"]
    friends = ["Iyyappa", "Gokul", "Balaji", "Magesh", "Others"]

    col_top_left, col_top_right = st.columns([2, 1])
    with col_top_left:
        st.write("**Expense Type**")
        chosen_cat = st.selectbox("Select Expense Type", options=categories, key="ui_category")
        if chosen_cat == "Groceries":
            chosen_g_sub = st.selectbox("Choose Grocery Subcategory", grocery_subcategories, key="ui_grocery_subcat")
            st.session_state["ui_subcategory"] = f"Groceries - {chosen_g_sub}"
        elif chosen_cat == "Bill Payment":
            chosen_b_sub = st.selectbox("Choose Bill Payment Subcategory", bill_payment_subcategories, key="ui_bill_subcat")
            st.session_state["ui_subcategory"] = f"Bill Payment - {chosen_b_sub}"
        elif chosen_cat == "Others":
            custom_cat = st.text_input("Enter custom category", key="ui_custom_category")
            st.session_state["ui_subcategory"] = custom_cat.strip() if custom_cat.strip() else "Others"
        else:
            st.session_state["ui_subcategory"] = chosen_cat

    with col_top_right:
        st.write("**Who Spent?**")
        chosen_friend = st.selectbox("Select Friend", options=friends, key="ui_friend")
        if chosen_friend == "Others":
            custom_friend = st.text_input("Enter custom friend name", key="ui_custom_friend")
            st.session_state["ui_friend"] = custom_friend.strip() if custom_friend.strip() else "Others"
        else:
            st.session_state["ui_friend"] = chosen_friend

    st.markdown("---")

    with st.form("expense_form", clear_on_submit=True):
        expense_date = st.date_input("Date", value=datetime.now().date(), key="expense_date_form")
        amount = st.number_input("Amount (₹)", min_value=1.0, step=1.0, key="expense_amount_form")
        notes = st.text_area("Comments / Notes (optional)", key="expense_notes_form")

        submitted = st.form_submit_button("💾 Save Expense")
        if submitted:
            category_to_save = st.session_state.get("ui_subcategory") or st.session_state.get("ui_category")
            friend_to_save = st.session_state.get("ui_friend")

            # ✅ convert date to datetime (midnight)
            try:
                ts = datetime.combine(expense_date, datetime.min.time())
            except Exception:
                ts = datetime.now()

            owner = st.session_state.get("username")
            collection.insert_one({
                "category": category_to_save,
                "friend": friend_to_save,
                "amount": float(amount),
                "notes": notes,
                "timestamp": ts,
                "owner": owner
            })
            st.success("✅ Expense saved successfully!")

    # Fetch visible data
    docs = get_visible_data()
    if docs:
        df = pd.DataFrame(docs)
        if "_id" in df.columns:
            df["_id"] = df["_id"].astype(str)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d")
    else:
        df = pd.DataFrame(columns=["timestamp", "category", "friend", "amount", "notes", "owner"])

    # Expenses
    st.subheader("📊 All Expenses (Visible to you)")
    if df.empty:
        st.info("No expenses yet. Add your first one above.")
    else:
        st.dataframe(df)

# --------------------------
# App Entry
# --------------------------
if __name__ == "__main__":
    show_app()