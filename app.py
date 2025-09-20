import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime
import plotly.express as px
from bson.objectid import ObjectId

# --------------------------
# MongoDB Connection
# --------------------------
MONGO_URI = st.secrets["mongo"]["uri"]
DB_NAME = st.secrets["mongo"]["db"]
COLLECTION_NAME = st.secrets["mongo"]["collection"]

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

# --------------------------
# App Config
# --------------------------
st.set_page_config(page_title="💰 Expense Tracker", page_icon="📊", layout="wide")
st.title("💰 Expense Tracker")

# --------------------------
# Category & Friend Options
# --------------------------
categories = ["Food", "Cinema", "Groceries", "Vegetables", "Others"]

grocery_subcategories = [
    "Vegetables",
    "Fruits",
    "Milk & Dairy",
    "Rice & Grains",
    "Lentils & Pulses",
    "Spices & Masalas",
    "Oil & Ghee",
    "Snacks & Packaged Items",
    "Bakery & Beverages",
    "Medical & Household Essentials"
]

friends = ["Iyyappa", "Gokul", "Balaji", "Magesh", "Others"]

# --------------------------
# Expense Form
# --------------------------
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

# --------------------------
# Load Data
# --------------------------
data = list(collection.find())
if data:
    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.date

    # --------------------------
    # Summary Stats
    # --------------------------
    st.subheader("📊 Expense Summary")
    total_spent = df["amount"].sum()
    st.metric("Total Spent", f"₹{total_spent:,.2f}")

    # --------------------------
    # Analytics
    # --------------------------
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 💡 By Category")
        cat_summary = df.groupby("category")["amount"].sum().reset_index()
        fig_cat = px.bar(cat_summary, x="category", y="amount", text="amount", title="Expenses by Category")
        st.plotly_chart(fig_cat, use_container_width=True)

    with col2:
        st.markdown("### 👥 By Friend")
        friend_summary = df.groupby("friend")["amount"].sum().reset_index()
        fig_friend = px.pie(friend_summary, names="friend", values="amount", title="Expenses by Friend")
        st.plotly_chart(fig_friend, use_container_width=True)

    # --------------------------
    # Transactions
    # --------------------------
    st.markdown("### 📝 Transactions")

    # Allow delete only for Balaji & Iyyappa
    allowed_deleters = ["Balaji", "Iyyappa"]

    for idx, row in df.sort_values(by="date", ascending=False).iterrows():
        with st.expander(f"📌 {row['date']} - {row['category']} - ₹{row['amount']} (by {row['friend']})"):
            st.write(f"**Category:** {row['category']}")
            st.write(f"**Friend:** {row['friend']}")
            st.write(f"**Amount:** ₹{row['amount']}")
            st.write(f"**Notes:** {row['notes'] if row['notes'] else '-'}")

            if row["friend"] in allowed_deleters:
                if st.button(f"❌ Delete", key=str(row["_id"])):
                    collection.delete_one({"_id": ObjectId(row["_id"])})
                    st.success("✅ Expense deleted!")
                    st.experimental_rerun()
else:
    st.info("No expenses recorded yet. Add one above! 🚀")
