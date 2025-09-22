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
    entry = {
        "action": action,
        "actor": actor,
        "target": target,
        "details": details or {},
        "timestamp": datetime.utcnow(),
    }
    try:
        audit_col.insert_one(entry)
    except Exception:
        # do not break the app if logging fails
        pass

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
# Admin helpers
# --------------------------
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
# Helper: visible data
# --------------------------
def get_visible_docs():
    if st.session_state.get("is_admin"):
        return list(collection.find())
    else:
        owner = st.session_state.get("username")
        return list(collection.find({"owner": owner}))

# --------------------------
# Main App
# --------------------------
def show_app():
    st.title("💰 Personal Expense Tracker")

    # Sidebar: login/logout
    with st.sidebar:
        st.header("🔒 Account")
        if not st.session_state["authenticated"]:
            st.text_input("Username", key="login_user")
            st.text_input("Password", type="password", key="login_pwd")
            st.button("Login", on_click=login, key="login_button")
            if st.session_state["_login_error"]:
                st.error(st.session_state["_login_error"])
        else:
            st.write(f"User: **{st.session_state['username']}**")
            if st.session_state["is_admin"]:
                st.success("Admin")
            st.button("Logout", on_click=logout, key="logout_button")

    if not st.session_state["authenticated"]:
        st.info("🔒 Please log in from the sidebar to access the Expense Tracker.")
        return

    # categories
    categories = ["Food", "Cinema", "Groceries", "Bill & Investment", "Medical", "Petrol", "Others"]
    grocery_subcategories = ["Vegetables", "Fruits", "Milk & Dairy", "Rice & Grains", "Lentils & Pulses",
                             "Spices & Masalas", "Oil & Ghee", "Snacks & Packaged Items", "Bakery & Beverages"]
    bill_payment_subcategories = ["CC", "Electricity Bill", "RD", "Mutual Fund", "Gold Chit"]
    friends = ["Iyyappa", "Gokul", "Balaji", "Magesh", "Others"]

    # Category & Friend selection (unique keys)
    col1, col2 = st.columns([2, 1])
    with col1:
        chosen_cat = st.selectbox("Expense Type", options=categories, key="ui_category_key")
        if chosen_cat == "Groceries":
            sub = st.selectbox("Grocery Subcategory", grocery_subcategories, key="ui_grocery_subcat_key")
            category_final = f"Groceries - {sub}"
        elif chosen_cat == "Bill & Investment":
            sub = st.selectbox("Bill & Investment Subcategory", bill_payment_subcategories, key="ui_bill_subcat_key")
            category_final = f"Bill & Investment - {sub}"
        elif chosen_cat == "Others":
            custom = st.text_input("Custom category", key="ui_custom_category_key")
            category_final = custom.strip() if custom else "Others"
        else:
            category_final = chosen_cat
    with col2:
        chosen_friend = st.selectbox("Who Spent?", options=friends, key="ui_friend_key")
        if chosen_friend == "Others":
            custom_friend = st.text_input("Custom friend", key="ui_custom_friend_key")
            friend_final = custom_friend.strip() if custom_friend else "Others"
        else:
            friend_final = chosen_friend

    st.markdown("---")

    # Expense form (unique keys for inputs)
    with st.form("expense_form_key", clear_on_submit=True):
        expense_date = st.date_input("Date", value=datetime.now().date(), key="expense_date_key")
        amount = st.number_input("Amount (₹)", min_value=1.0, step=1.0, key="expense_amount_key")
        notes = st.text_area("Comments / Notes", key="expense_notes_key")
        if st.form_submit_button("💾 Save Expense", key="save_expense_key"):
            ts = datetime.combine(expense_date, datetime.min.time())
            owner = st.session_state["username"]
            try:
                collection.insert_one({
                    "category": category_final,
                    "friend": friend_final,
                    "amount": float(amount),
                    "notes": notes,
                    "timestamp": ts,
                    "owner": owner
                })
                log_action("add_expense", owner, details={"category": category_final, "amount": float(amount)})
                st.success("✅ Expense saved successfully!")
            except Exception as e:
                st.error(f"Failed to save expense: {e}")

    # Admin Controls
    if st.session_state.get("is_admin"):
        st.markdown("---")
        st.subheader("⚙️ Admin Controls")

        # Create User
        with st.expander("Create User", expanded=False):
            cu_name = st.text_input("New username", key="create_username_key")
            cu_pass = st.text_input("New password", type="password", key="create_password_key")
            cu_role = st.selectbox("Role", ["user", "admin"], key="create_role_key")
            if st.button("Create User", key="create_user_button"):
                create_user(cu_name, cu_pass, cu_role)

        # Reset Password (unique keys)
        with st.expander("Reset Password", expanded=False):
            users_list_reset = [d["username"] for d in users_col.find({}, {"username": 1}) if d["username"] != st.session_state["username"]]
            if users_list_reset:
                tgt_reset = st.selectbox("Select user to reset", options=users_list_reset, key="reset_user_select_key")
                new_pass = st.text_input("New password", type="password", key="reset_user_password_key")
                if st.button("Reset Password", key="reset_user_button_key"):
                    if not new_pass:
                        st.error("Provide a new password.")
                    else:
                        users_col.update_one({"username": tgt_reset}, {"$set": {"password_hash": hash_password(new_pass)}})
                        log_action("reset_password", st.session_state["username"], target=tgt_reset)
                        st.success(f"Password for '{tgt_reset}' has been reset.")
            else:
                st.info("No other users available for reset.")

        # Delete User (unique keys + confirm)
        with st.expander("Delete User", expanded=False):
            users_list_del = [d["username"] for d in users_col.find({}, {"username": 1}) if d["username"] != st.session_state["username"] and d["username"] != st.secrets.get("admin", {}).get("username")]
            if users_list_del:
                tgt_del = st.selectbox("Select user to delete", options=users_list_del, key="delete_user_select_key")
                del_confirm = st.checkbox("I confirm deletion of this user and optionally their expenses", key="delete_user_confirm_key")
                del_expenses_opt = st.checkbox("Also delete user's expenses", key="delete_user_expenses_opt_key")
                if st.button("🗑️ Delete User", key="delete_user_button_key") and del_confirm:
                    users_col.delete_one({"username": tgt_del})
                    if del_expenses_opt:
                        collection.delete_many({"owner": tgt_del})
                    log_action("delete_user", st.session_state["username"], target=tgt_del, details={"deleted_expenses": bool(del_expenses_opt)})
                    st.success(f"User '{tgt_del}' deleted.")
            else:
                st.info("No other users to delete.")

        # Delete All Expenses (confirm)
        st.markdown("#### Danger Zone")
        del_all_confirm = st.checkbox("I confirm deleting ALL expenses (admin only)", key="delete_all_confirm_key")
        if st.button("🔥 Delete All Expenses", key="delete_all_button_key") and del_all_confirm:
            collection.delete_many({})
            log_action("delete_all_expenses", st.session_state["username"])
            st.warning("⚠️ All expenses deleted.")

        # View Audit Logs (admin)
        with st.expander("View Audit Logs", expanded=False):
            logs = list(audit_col.find().sort("timestamp", -1).limit(200))
            if logs:
                logs_df = pd.DataFrame(logs)
                if "_id" in logs_df.columns:
                    logs_df["_id"] = logs_df["_id"].astype(str)
                logs_df["timestamp"] = pd.to_datetime(logs_df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
                st.dataframe(logs_df)
            else:
                st.info("No audit logs yet.")

    # ----------------------
    # Show visible expenses
    # ----------------------
    docs = get_visible_docs()
    if docs:
        df = pd.DataFrame(docs)
        if "_id" in df.columns:
            df["_id"] = df["_id"].astype(str)
        if "timestamp" in df.columns:
            try:
                df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d")
            except Exception:
                df["timestamp"] = df["timestamp"].astype(str)
        st.subheader("📊 All Expenses (Visible to you)")
        # show table
        st.dataframe(df)

        # Admin deletion of selected expenses (unique keys per row)
        if st.session_state.get("is_admin"):
            st.markdown("---")
            st.write("Delete individual expenses (admin)")
            selected_for_delete = []
            for idx, row in df.iterrows():
                cb_key = f"del_cb_{row['_id']}"
                if st.checkbox(f"Delete {row['timestamp']} | {row.get('category','')} | ₹{row.get('amount','')}", key=cb_key):
                    selected_for_delete.append(row["_id"])
            if selected_for_delete:
                confirm_sel = st.checkbox("Confirm deletion of selected expenses", key="confirm_delete_selected_key")
                if st.button("🗑️ Delete Selected Expenses", key="delete_selected_expenses_button_key") and confirm_sel:
                    for did in selected_for_delete:
                        try:
                            collection.delete_one({"_id": ObjectId(did)})
                        except Exception:
                            collection.delete_one({"_id": did})
                    log_action("delete_selected_expenses", st.session_state["username"], details={"ids": selected_for_delete})
                    st.success("Selected expenses deleted.")
    else:
        st.info("No expenses to show.")

# Entry
if __name__ == "__main__":
    show_app()