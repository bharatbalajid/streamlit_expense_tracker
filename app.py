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
# Generate PDF for a friend (friend field)
# --------------------------
def generate_friend_pdf_bytes(friend_name: str, query_filter: dict) -> bytes:
    # query_filter should already be the visibility filter (e.g. {"owner": username} for normal users)
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
# Helper: get visible data for current viewer
# --------------------------
def get_visible_data():
    """
    Returns list of documents visible to the current user.
    Admin -> all documents
    User  -> documents where owner == username
    """
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

    # --- UI variables (categories/subcategories/friends) ---
    categories = ["Food", "Cinema", "Groceries", "Bill Payment", "Medical", "Others"]
    grocery_subcategories = [
        "Vegetables", "Fruits", "Milk & Dairy", "Rice & Grains", "Lentils & Pulses",
        "Spices & Masalas", "Oil & Ghee", "Snacks & Packaged Items", "Bakery & Beverages"
    ]
    bill_payment_subcategories = [
        "CC", "Electricity Bill", "RD", "Mutual Fund", "Gold Chit"
    ]
    # friends list for selection: show a limited set and allow custom names
    friends = ["Iyyappa", "Gokul", "Balaji", "Magesh", "Others"]

    # --- Category & friend selection OUTSIDE the form so conditional widgets render immediately ---
    col_top_left, col_top_right = st.columns([2, 1])
    with col_top_left:
        st.write("**Expense Type**")
        chosen_cat = st.selectbox("Select Expense Type", options=categories, key="ui_category")
        # show appropriate subcategory control immediately
        if chosen_cat == "Groceries":
            st.write("**Grocery Subcategory**")
            chosen_g_sub = st.selectbox("Choose Grocery Subcategory", grocery_subcategories, key="ui_grocery_subcat")
            try:
                st.session_state["ui_subcategory"] = f"Groceries - {chosen_g_sub}"
            except Exception:
                pass
        elif chosen_cat == "Bill Payment":
            st.write("**Bill Payment Subcategory**")
            chosen_b_sub = st.selectbox("Choose Bill Payment Subcategory", bill_payment_subcategories, key="ui_bill_subcat")
            try:
                st.session_state["ui_subcategory"] = f"Bill Payment - {chosen_b_sub}"
            except Exception:
                pass
        elif chosen_cat == "Others":
            custom_cat = st.text_input("Enter custom category", key="ui_custom_category")
            try:
                st.session_state["ui_subcategory"] = custom_cat.strip() if custom_cat.strip() else "Others"
            except Exception:
                pass
        else:
            # for simple categories (Food, Cinema, Vegetables, Medical etc.)
            try:
                st.session_state["ui_subcategory"] = chosen_cat
            except Exception:
                pass

    with col_top_right:
        st.write("**Who Spent?**")
        chosen_friend = st.selectbox("Select Friend", options=friends, key="ui_friend")
        if chosen_friend == "Others":
            custom_friend = st.text_input("Enter custom friend name", key="ui_custom_friend")
            try:
                st.session_state["ui_friend"] = custom_friend.strip() if custom_friend.strip() else "Others"
            except Exception:
                pass
        else:
            try:
                st.session_state["ui_friend"] = chosen_friend
            except Exception:
                pass

    st.markdown("---")

    # --- Now the form contains the remaining inputs (date, amount, notes) ---
    with st.form("expense_form", clear_on_submit=True):
        expense_date = st.date_input("Date", value=datetime.now().date(), key="expense_date_form")
        amount = st.number_input("Amount (₹)", min_value=1.0, step=1.0, key="expense_amount_form")
        notes = st.text_area("Comments / Notes (optional)", key="expense_notes_form")

        submitted = st.form_submit_button("💾 Save Expense")
        if submitted:
            # read chosen category & friend from session_state (set above)
            category_to_save = st.session_state.get("ui_subcategory") or st.session_state.get("ui_category")
            friend_to_save = st.session_state.get("ui_friend") or st.session_state.get("ui_friend")

            ts = expense_date  # store date only (no time)
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
            # refresh visible data by rerunning (Streamlit will rerun automatically after interaction)

    # ----------------------
    # Fetch visible data (admin -> all, user -> own)
    # ----------------------
    docs = get_visible_data()
    if docs:
        df = pd.DataFrame(docs)
        # normalize display columns
        if "_id" in df.columns:
            df["_id"] = df["_id"].astype(str)
        if "timestamp" in df.columns:
            try:
                df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d")
            except Exception:
                df["timestamp"] = df["timestamp"].astype(str)
    else:
        df = pd.DataFrame(columns=["timestamp", "category", "friend", "amount", "notes", "owner"])

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

        # Delete User option (admin only)
        with st.expander("Delete user"):
            users_list = [u["username"] for u in users_col.find({}, {"username": 1})]
            users_list = [u for u in users_list if u != st.session_state["username"]]
            if users_list:
                user_to_delete = st.selectbox("Select user to delete", users_list, key="delete_user_select")
                delete_user_confirm = st.checkbox("Also delete user's expenses", key="delete_user_expenses_confirm")
                if st.button("🗑️ Delete User", key="delete_user_btn"):
                    users_col.delete_one({"username": user_to_delete})
                    if delete_user_confirm:
                        collection.delete_many({"owner": user_to_delete})
                    st.success(f"User '{user_to_delete}' deleted successfully.")
            else:
                st.info("No other users to delete.")

        if st.button("🔥 Delete All Expenses (Admin)", key="delete_all_admin"):
            collection.delete_many({})
            st.warning("⚠️ All expenses deleted by admin.")

    # ----------------------
    # Display expenses (only visible ones)
    # ----------------------
    st.subheader("📊 All Expenses (Visible to you)")
    if df.empty:
        st.info("No expenses yet. Add your first one above.")
    else:
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
                # Only admin may delete rows
                if st.session_state["is_admin"]:
                    if st.checkbox("❌", key=checkbox_key):
                        delete_ids.append(row["_id"])
                else:
                    st.write("")  # placeholder

        if st.session_state["is_admin"]:
            if delete_ids and st.button("🗑️ Delete Selected", key="delete_selected_admin"):
                for del_id in delete_ids:
                    try:
                        collection.delete_one({"_id": ObjectId(del_id)})
                    except Exception:
                        collection.delete_one({"_id": del_id})
                st.success("Deleted selected expenses.")

        # ----------------------
        # Downloads & friend-based PDFs: use only visible data
        # ----------------------
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

            st.markdown("---")
            st.subheader("👥 Download Friend's Expense Report")

            friends_available = sorted(df_download['friend'].dropna().unique().tolist()) if 'friend' in df_download.columns else []

            selected_friend = st.selectbox("Select friend", options=friends_available, key="select_friend_for_pdf") if friends_available else None

            if HAS_REPORTLAB and selected_friend:
                try:
                    # build the visibility filter
                    if st.session_state.get("is_admin"):
                        qfilter = {}
                    else:
                        qfilter = {"owner": st.session_state.get("username")}
                    friend_pdf = generate_friend_pdf_bytes(selected_friend, qfilter)
                    filename = f"expenses_friend_{selected_friend}.pdf"
                    st.download_button(f"⬇️ Download PDF for friend: {selected_friend}", data=friend_pdf, file_name=filename, mime="application/pdf")
                except Exception as e:
                    st.error(f"Failed to generate friend PDF: {e}")

        except Exception as e:
            st.error(f"Failed to prepare download: {e}")

        # ----------------------
        # Metrics & charts computed only from visible data
        # ----------------------
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

# --------------------------
# App Entry
# --------------------------
if __name__ == "__main__":
    show_app()