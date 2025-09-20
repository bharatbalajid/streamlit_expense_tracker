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
MONGO_URI = st.secrets.get("mongo", {}).get("uri")
if not MONGO_URI:
    st.error("MongoDB URI not configured in .streamlit/secrets.toml")
    st.stop()

client = MongoClient(MONGO_URI)

db = client["expense_tracker"]
collection = db["expenses"]

# --------------------------
# Page config
# --------------------------
st.set_page_config(page_title="💰 Expense Tracker", layout="wide")

# --------------------------
# Session state defaults
# --------------------------
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "username" not in st.session_state:
    st.session_state["username"] = None
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False

# --------------------------
# Small centered login UI (mobile-friendly)
# --------------------------
LOGIN_BOX_CSS = """
<style>
/* center the main content area and make the login card narrow */
[data-testid="stApp"] > div:first-child {
  display: flex;
  justify-content: center;
}
.login-card {
  width: 420px;
  max-width: calc(100% - 48px);
  padding: 18px 22px;
  border-radius: 10px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.45);
  background: rgba(255,255,255,0.02);
}
.login-title {
  font-size: 22px;
  margin-bottom: 6px;
}
.login-sub {
  color: #bfc7cf;
  margin-bottom: 12px;
}
@media (max-width: 480px) {
  .login-card { width: 100%; padding: 12px; }
  .login-title { font-size: 18px; }
}
</style>
"""

def show_login():
    st.markdown(LOGIN_BOX_CSS, unsafe_allow_html=True)

    # small centered card
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>🔐 Admin Login</div>", unsafe_allow_html=True)
    st.markdown("<div class='login-sub'>Sign in to access the expense tracker</div>", unsafe_allow_html=True)

    with st.form("login_form"):
        user = st.text_input("Username", placeholder="admin")
        pwd = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

        if submitted:
            secret_user = st.secrets.get("admin", {}).get("username")
            secret_pass = st.secrets.get("admin", {}).get("password")

            if secret_user is None or secret_pass is None:
                st.error("Admin credentials are not configured in .streamlit/secrets.toml")
                st.markdown("</div>", unsafe_allow_html=True)
                return

            if user == secret_user and pwd == secret_pass:
                st.session_state["authenticated"] = True
                st.session_state["username"] = user
                st.session_state["is_admin"] = True
                st.success("Login successful — opening the app...")
                # Attempt a rerun if available to immediately show main UI
                if hasattr(st, "experimental_rerun"):
                    st.experimental_rerun()
            else:
                st.error("Invalid credentials")

    st.markdown("</div>", unsafe_allow_html=True)

# --------------------------
# Main app UI
# --------------------------
def show_app():
    st.title("💰 Personal Expense Tracker")

    with st.sidebar:
        st.header("🔒 Account")
        st.write(f"User: **{st.session_state.get('username','-')}**")
        if st.session_state.get("is_admin"):
            st.success("Admin")
        if st.button("Logout"):
            # clear only the relevant session keys
            for k in ["authenticated", "username", "is_admin"]:
                if k in st.session_state:
                    del st.session_state[k]
            # best effort: trigger a rerun if supported
            if hasattr(st, "experimental_rerun"):
                st.experimental_rerun()
            else:
                st.info("Logged out — please refresh if the view does not update")
                return

    # Expense form
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

    # Show data and analytics
    data = list(collection.find())
    if data:
        df = pd.DataFrame(data)
        df["_id"] = df["_id"].astype(str)

        st.subheader("📊 All Expenses (Manage)")
        delete_ids = []
        for i, row in df.iterrows():
            c1, c2, c3, c4, c5, c6 = st.columns([2,2,2,2,2,1])
            with c1: st.write(row.get("timestamp"))
            with c2: st.write(row.get("category"))
            with c3: st.write(row.get("friend"))
            with c4: st.write(f"₹ {row.get('amount')}")
            with c5: st.write(row.get("notes") or "-")
            with c6:
                if st.checkbox("❌", key=row["_id"]):
                    delete_ids.append(row["_id"])

        if delete_ids:
            if st.button("🗑️ Delete Selected"):
                for del_id in delete_ids:
                    collection.delete_one({"_id": ObjectId(del_id)})
                st.success(f"Deleted {len(delete_ids)} entries.")

        # Admin-only controls
        if st.session_state.get("is_admin"):
            st.markdown("---")
            st.subheader("⚙️ Admin Controls")
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("🔥 Delete All Expenses (Admin)"):
                    collection.delete_many({})
                    st.warning("⚠️ All expenses deleted by admin.")
            with col_b:
                csv_buffer = io.StringIO()
                df_export = df.copy()
                df_export["timestamp"] = df_export["timestamp"].astype(str)
                df_export.to_csv(csv_buffer, index=False)
                csv_bytes = csv_buffer.getvalue().encode("utf-8")
                st.download_button("⬇️ Download CSV (Admin)", data=csv_bytes,
                                   file_name="expenses_export.csv", mime="text/csv")

        total = df["amount"].sum()
        st.metric("💵 Total Spending", f"₹ {total:.2f}")

        c1, c2 = st.columns(2)
        with c1:
            cat_summary = df.groupby("category")["amount"].sum().reset_index()
            st.subheader("📌 Spending by Category")
            fig1 = px.bar(cat_summary, x="category", y="amount", text="amount", color="category")
            st.plotly_chart(fig1, use_container_width=True)

        with c2:
            friend_summary = df.groupby("friend")["amount"].sum().reset_index()
            st.subheader("👥 Spending by Friend")
            fig2 = px.bar(friend_summary, x="friend", y="amount", text="amount", color="friend")
            st.plotly_chart(fig2, use_container_width=True)

        st.subheader("🥧 Category Breakdown")
        fig3 = px.pie(cat_summary, names="category", values="amount", title="Expenses by Category")
        st.plotly_chart(fig3, use_container_width=True)

        st.subheader("Summary by Friend")
        st.table(friend_summary.set_index("friend"))
    else:
        st.info("No expenses yet. Add your first one above")

# --------------------------
# App entry
# --------------------------
if not st.session_state["authenticated"]:
    show_login()
else:
    show_app()
