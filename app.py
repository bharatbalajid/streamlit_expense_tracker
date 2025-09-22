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
AUDIT_COLLECTION = "audit_logs"

if not MONGO_URI:
    st.error("MongoDB URI not configured in .streamlit/secrets.toml")
    st.stop()

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]
users_col = db[USERS_COLLECTION]
audit_col = db[AUDIT_COLLECTION]

# --------------------------
# Helpers
# --------------------------
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def log_action(action: str, actor: str, target: str = None, details: dict = None):
    """Store admin/user actions in audit log"""
    entry = {
        "action": action,
        "actor": actor,
        "target": target,
        "details": details or {},
        "timestamp": datetime.utcnow(),
    }
    audit_col.insert_one(entry)

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
            log_action("create_superadmin", "system", target=secret_user)

ensure_superadmin()

# --------------------------
# Session defaults
# --------------------------
for k, default in {
    "authenticated": False,
    "username": None,
    "is_admin": False,
    "_login_error": None,
}.items():
    if k not in st.session_state:
        st.session_state[k] = default

# --------------------------
# Authentication
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
        log_action("login", user)
    else:
        st.session_state["_login_error"] = "Invalid username or password."

def logout():
    log_action("logout", st.session_state.get("username"))
    st.session_state["authenticated"] = False
    st.session_state["username"] = None
    st.session_state["is_admin"] = False
    st.session_state["_login_error"] = None

# --------------------------
# Admin Helpers
# --------------------------
def create_user(username: str, password: str, role: str = "user"):
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
    log_action("create_user", st.session_state["username"], target=username, details={"role": role})
    st.success(f"User '{username}' created with role '{role}'.")

# --------------------------
# PDF Export helper
# --------------------------
def generate_pdf_bytes(df: pd.DataFrame, title: str = "Expense Report") -> bytes:
    if not HAS_REPORTLAB:
        raise RuntimeError("reportlab not available")
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph(title, styles["Title"]))
    elems.append(Spacer(1, 12))
    total = df["amount"].sum() if "amount" in df.columns else 0.0
    elems.append(Paragraph(f"Total expenses: ₹ {total:.2f} — Generated: {datetime.now().strftime('%Y-%m-%d')}", styles["Normal"]))
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
    return buffer.getvalue()

# --------------------------
# Main App
# --------------------------
def show_app():
    st.title("💰 Personal Expense Tracker")

    # Sidebar
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
        st.info("🔒 Please log in from the sidebar to access the Expense Tracker.")
        return

    # Expense categories
    categories = ["Food", "Cinema", "Groceries", "Bill Payment", "Medical", "Others"]
    grocery_subcategories = ["Vegetables", "Fruits", "Milk & Dairy", "Rice & Grains", "Lentils & Pulses",
                             "Spices & Masalas", "Oil & Ghee", "Snacks & Packaged Items", "Bakery & Beverages"]
    bill_payment_subcategories = ["CC", "Electricity Bill", "RD", "Mutual Fund", "Gold Chit"]
    friends = ["Iyyappa", "Gokul", "Balaji", "Magesh", "Others"]

    # Category & Friend
    col1, col2 = st.columns([2, 1])
    with col1:
        chosen_cat = st.selectbox("Expense Type", options=categories, key="ui_category")
        if chosen_cat == "Groceries":
            sub = st.selectbox("Grocery Subcategory", grocery_subcategories)
            category_final = f"Groceries - {sub}"
        elif chosen_cat == "Bill Payment":
            sub = st.selectbox("Bill Payment Subcategory", bill_payment_subcategories)
            category_final = f"Bill Payment - {sub}"
        elif chosen_cat == "Others":
            custom = st.text_input("Custom category")
            category_final = custom.strip() if custom else "Others"
        else:
            category_final = chosen_cat
    with col2:
        chosen_friend = st.selectbox("Who Spent?", options=friends, key="ui_friend")
        if chosen_friend == "Others":
            custom_friend = st.text_input("Custom friend")
            friend_final = custom_friend.strip() if custom_friend else "Others"
        else:
            friend_final = chosen_friend

    # Expense Form
    with st.form("expense_form", clear_on_submit=True):
        expense_date = st.date_input("Date", value=datetime.now().date())
        amount = st.number_input("Amount (₹)", min_value=1.0, step=1.0)
        notes = st.text_area("Comments / Notes")
        if st.form_submit_button("💾 Save Expense"):
            ts = datetime.combine(expense_date, datetime.min.time())
            owner = st.session_state["username"]
            collection.insert_one({
                "category": category_final,
                "friend": friend_final,
                "amount": float(amount),
                "notes": notes,
                "timestamp": ts,
                "owner": owner
            })
            log_action("add_expense", owner, details={"category": category_final, "amount": amount})
            st.success("✅ Expense saved successfully!")

    # Admin Controls
    if st.session_state["is_admin"]:
        st.subheader("⚙️ Admin Controls")
        with st.expander("Create User"):
            u = st.text_input("Username")
            p = st.text_input("Password", type="password")
            r = st.selectbox("Role", ["user", "admin"])
            if st.button("Create User"):
                create_user(u, p, r)
        with st.expander("Reset Password"):
            users_list = [u["username"] for u in users_col.find({}, {"username": 1}) if u["username"] != st.session_state["username"]]
            target = st.selectbox("User", users_list) if users_list else None
            new_pass = st.text_input("New password", type="password")
            if st.button("Reset"):
                if target and new_pass:
                    users_col.update_one({"username": target}, {"$set": {"password_hash": hash_password(new_pass)}})
                    log_action("reset_password", st.session_state["username"], target=target)
                    st.success(f"Password for {target} reset.")
        with st.expander("Delete User"):
            users_list = [u["username"] for u in users_col.find({}, {"username": 1}) if u["username"] != st.session_state["username"]]
            target = st.selectbox("User", users_list) if users_list else None
            confirm = st.checkbox("Confirm delete user")
            if st.button("🗑️ Delete User") and target and confirm:
                users_col.delete_one({"username": target})
                collection.delete_many({"owner": target})
                log_action("delete_user", st.session_state["username"], target=target)
                st.success(f"User {target} deleted.")

        confirm_all = st.checkbox("Confirm delete ALL expenses")
        if st.button("🔥 Delete All Expenses") and confirm_all:
            collection.delete_many({})
            log_action("delete_all_expenses", st.session_state["username"])
            st.warning("⚠️ All expenses deleted.")

    # Show Expenses
    docs = list(collection.find({} if st.session_state["is_admin"] else {"owner": st.session_state["username"]}))
    if docs:
        df = pd.DataFrame(docs)
        df["_id"] = df["_id"].astype(str)
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d")
        st.dataframe(df)

        # Delete selected (admin only)
        if st.session_state["is_admin"]:
            delete_ids = []
            for _, r in df.iterrows():
                if st.checkbox(f"Delete {r['_id']}", key=r["_id"]):
                    delete_ids.append(r["_id"])
            confirm_sel = st.checkbox("Confirm delete selected")
            if st.button("🗑️ Delete Selected") and delete_ids and confirm_sel:
                for did in delete_ids:
                    collection.delete_one({"_id": ObjectId(did)})
                log_action("delete_selected_expenses", st.session_state["username"], details={"ids": delete_ids})
                st.success("Selected expenses deleted.")
    else:
        st.info("No expenses yet.")

# --------------------------
if __name__ == "__main__":
    show_app()