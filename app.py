import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime
import plotly.express as px
from bson.objectid import ObjectId
import io

# --------------------------
# PDF Export (ReportLab)
# --------------------------
HAS_REPORTLAB = True
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
except ImportError:
    HAS_REPORTLAB = False


# --------------------------
# MongoDB Connection
# --------------------------
def get_collection():
    MONGO_URI = st.secrets.get("mongo", {}).get("uri")
    DB_NAME = st.secrets.get("mongo", {}).get("db", "expense_tracker")
    COLLECTION_NAME = st.secrets.get("mongo", {}).get("collection", "expenses")

    if not MONGO_URI:
        st.error("❌ MongoDB URI not found in .streamlit/secrets.toml")
        st.stop()

    client = MongoClient(MONGO_URI)
    return client[DB_NAME][COLLECTION_NAME]


# --------------------------
# Login
# --------------------------
def login():
    st.sidebar.title("🔐 Login")
    users = {
        "admin": "admin123",   # full access
        "balaji": "balaji123", # can delete
        "iyyapa": "iyyapa123", # can delete
        "gokul": "gokul123",   # view only
    }

    username = st.sidebar.text_input("Username")
    password = st.sidebar.text_input("Password", type="password")

    if st.sidebar.button("Login"):
        if username in users and users[username] == password:
            st.session_state["user"] = username
            st.sidebar.success(f"✅ Logged in as {username}")
            st.experimental_rerun()
        else:
            st.sidebar.error("❌ Invalid credentials")

    if "user" in st.session_state:
        st.sidebar.info(f"👤 {st.session_state['user']}")
        if st.sidebar.button("Logout"):
            st.session_state.pop("user")
            st.experimental_rerun()


# --------------------------
# Expense Form
# --------------------------
def expense_form(collection):
    categories = ["Food", "Cinema", "Groceries", "Vegetables", "Others"]
    grocery_subcategories = [
        "Vegetables", "Fruits", "Milk & Dairy", "Rice & Grains",
        "Lentils & Pulses", "Spices & Masalas", "Oil & Ghee",
        "Snacks & Packaged Items", "Bakery & Beverages",
        "Medical & Household Essentials"
    ]
    friends = ["Iyyappa", "Gokul", "Balaji", "Magesh", "Others"]

    st.subheader("➕ Add Expense")
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
# Show Analytics
# --------------------------
def show_analytics(df: pd.DataFrame):
    st.subheader("📊 Expense Summary")
    st.metric("Total Spent", f"₹{df['amount'].sum():,.2f}")

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
# Show Transactions
# --------------------------
def show_transactions(df: pd.DataFrame, collection):
    st.markdown("### 📝 Transactions")
    allowed_deleters = ["admin", "balaji", "iyyapa"]

    for _, row in df.sort_values(by="date", ascending=False).iterrows():
        with st.expander(f"📌 {row['date']} - {row['category']} - ₹{row['amount']} (by {row['friend']})"):
            st.write(f"**Category:** {row['category']}")
            st.write(f"**Friend:** {row['friend']}")
            st.write(f"**Amount:** ₹{row['amount']}")
            st.write(f"**Notes:** {row['notes'] if row['notes'] else '-'}")

            if st.session_state.get("user") in allowed_deleters:
                if st.button(f"❌ Delete", key=str(row['_id'])):
                    collection.delete_one({"_id": ObjectId(row["_id"])})
                    st.success("✅ Expense deleted!")
                    st.experimental_rerun()


# --------------------------
# Export to PDF
# --------------------------
def export_pdf(df: pd.DataFrame):
    if not HAS_REPORTLAB:
        st.error("ReportLab not installed. Install with `pip install reportlab`")
        return

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    c.setFont("Helvetica-Bold", 16)
    c.drawString(200, height - 50, "Expense Report")

    c.setFont("Helvetica", 12)
    y = height - 100
    for _, row in df.iterrows():
        text = f"{row['date']} | {row['category']} | {row['friend']} | ₹{row['amount']} | {row['notes']}"
        c.drawString(50, y, text)
        y -= 20
        if y < 50:
            c.showPage()
            y = height - 50

    c.save()
    buffer.seek(0)

    st.download_button(
        label="📄 Download PDF Report",
        data=buffer,
        file_name="expense_report.pdf",
        mime="application/pdf"
    )


# --------------------------
# Main
# --------------------------
def main():
    st.set_page_config(page_title="💰 Expense Tracker", page_icon="📊", layout="wide")
    st.title("💰 Expense Tracker")

    # --- Login ---
    login()
    if "user" not in st.session_state:
        st.warning("Please log in to continue.")
        return

    collection = get_collection()

    # Expense form
    expense_form(collection)

    # Load data
    data = list(collection.find())
    if not data:
        st.info("No expenses recorded yet. Add one above! 🚀")
        return

    df = pd.DataFrame(data)
    df["_id"] = df["_id"].astype(str)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.date

    # Show analytics
    show_analytics(df)

    # Show transactions
    show_transactions(df, collection)

    # Export to PDF
    export_pdf(df)


if __name__ == "__main__":
    main()
