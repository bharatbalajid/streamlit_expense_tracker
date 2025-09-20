import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime
import plotly.express as px
from bson.objectid import ObjectId
import io

# --------------------------
# MongoDB Connection (via Streamlit Secrets)
# --------------------------
MONGO_URI = st.secrets["mongo"]["uri"]
client = MongoClient(MONGO_URI)

db = client["expense_tracker"]
collection = db["expenses"]

# --------------------------
# Streamlit Page Config
# --------------------------
st.set_page_config(page_title="💰 Expense Tracker", layout="wide")
st.title("💰 Personal Expense Tracker")

# --------------------------
# Admin Authentication
# --------------------------
# Add the following to your Streamlit secrets.toml:
# [admin]
# username = "admin"
# password = "yourpassword"

if "admin_authenticated" not in st.session_state:
    st.session_state["admin_authenticated"] = False

with st.sidebar:
    st.header("🔒 Admin")
    if not st.session_state["admin_authenticated"]:
        admin_user = st.text_input("Username", key="_admin_user")
        admin_pass = st.text_input("Password", type="password", key="_admin_pass")
        if st.button("Login as Admin"):
            secret_user = st.secrets.get("admin", {}).get("username")
            secret_pass = st.secrets.get("admin", {}).get("password")
            if secret_user is None or secret_pass is None:
                st.error("Admin credentials not set in Streamlit secrets. Add [admin] username and password.")
            elif admin_user == secret_user and admin_pass == secret_pass:
                st.session_state["admin_authenticated"] = True
                st.success("Admin login successful")
                st.experimental_rerun()
            else:
                st.error("Invalid admin credentials")
    else:
        st.write("✅ Logged in as admin")
        if st.button("Logout"):
            st.session_state["admin_authenticated"] = False
            st.experimental_rerun()

# --------------------------
# Input Form
# --------------------------
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
        st.experimental_rerun()

# --------------------------
# Show Data
# --------------------------
data = list(collection.find())
if data:
    df = pd.DataFrame(data)
    df["_id"] = df["_id"].astype(str)  # Convert ObjectId to string

    # Expense Management
    st.subheader("📊 All Expenses (Manage)")

    delete_ids = []
    for i, row in df.iterrows():
        col1, col2, col3, col4, col5, col6 = st.columns([2, 2, 2, 2, 2, 1])
        with col1: st.write(row.get("timestamp"))
        with col2: st.write(row.get("category"))
        with col3: st.write(row.get("friend"))
        with col4: st.write(f"₹ {row.get('amount')}")
        with col5: st.write(row.get("notes") if row.get("notes") else "-")
        with col6:
            if st.checkbox("❌", key=row["_id"]):
                delete_ids.append(row["_id"])

    if delete_ids:
        if st.button("🗑️ Delete Selected"):
            for del_id in delete_ids:
                collection.delete_one({"_id": ObjectId(del_id)})
            st.success(f"Deleted {len(delete_ids)} entries.")
            st.experimental_rerun()

    # Admin-only controls
    if st.session_state["admin_authenticated"]:
        st.markdown("---")
        st.subheader("⚙️ Admin Controls")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("🔥 Delete All Expenses (Admin)"):
                collection.delete_many({})
                st.warning("⚠️ All expenses deleted by admin.")
                st.experimental_rerun()
        with col_b:
            # Export CSV
            csv_buffer = io.StringIO()
            df_export = df.copy()
            df_export["timestamp"] = df_export["timestamp"].astype(str)
            df_export.to_csv(csv_buffer, index=False)
            csv_bytes = csv_buffer.getvalue().encode("utf-8")
            st.download_button("⬇️ Download CSV (Admin)", data=csv_bytes, file_name="expenses_export.csv", mime="text/csv")

    # Total Spending
    total = df["amount"].sum()
    st.metric("💵 Total Spending", f"₹ {total:.2f}")

    col1, col2 = st.columns(2)

    # Spending by Category
    with col1:
        cat_summary = df.groupby("category")["amount"].sum().reset_index()
        st.subheader("📌 Spending by Category")
        fig1 = px.bar(cat_summary, x="category", y="amount", text="amount", color="category")
        st.plotly_chart(fig1, use_container_width=True)

    # Spending by Friend
    with col2:
        friend_summary = df.groupby("friend")["amount"].sum().reset_index()
        st.subheader("👥 Spending by Friend")
        fig2 = px.bar(friend_summary, x="friend", y="amount", text="amount", color="friend")
        st.plotly_chart(fig2, use_container_width=True)

    # Pie chart of categories
    st.subheader("🥧 Category Breakdown")
    fig3 = px.pie(cat_summary, names="category", values="amount", title="Expenses by Category")
    st.plotly_chart(fig3, use_container_width=True)

    # Summary table per friend
    st.subheader("Summary by Friend")
    st.table(friend_summary.set_index("friend"))
else:
    st.info("No expenses yet. Add your first one above")
