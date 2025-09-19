import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime
import plotly.express as px
from bson.objectid import ObjectId

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
# Input Form
# --------------------------
categories = ["Food", "Cinema", "Groceries", "Vegetables", "Others"]
friends = ["Gokul", "Balaji", "Magesh", "Others"]

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
            "amount": amount,
            "notes": notes,
            "timestamp": datetime.now()
        }
        collection.insert_one(expense)
        st.success("✅ Expense saved successfully!")

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
        with col1: st.write(row["timestamp"])
        with col2: st.write(row["category"])
        with col3: st.write(row["friend"])
        with col4: st.write(f"₹ {row['amount']}")
        with col5: st.write(row["notes"] if row["notes"] else "-")
        with col6:
            if st.checkbox("❌", key=row["_id"]):
                delete_ids.append(row["_id"])

    if delete_ids:
        if st.button("🗑️ Delete Selected"):
            for del_id in delete_ids:
                collection.delete_one({"_id": ObjectId(del_id)})
            st.success(f"Deleted {len(delete_ids)} entries.")
            st.rerun()

    # Delete all button
    if st.button("🔥 Delete All Expenses"):
        collection.delete_many({})
        st.warning("⚠️ All expenses deleted.")
        st.rerun()

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