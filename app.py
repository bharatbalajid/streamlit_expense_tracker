# app.py
import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime, date
import plotly.express as px
from bson.objectid import ObjectId
import io

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
# Helpers: Mongo collection and creds
# --------------------------
def get_collection():
    mongo = st.secrets.get("mongo", {}) if isinstance(st.secrets, dict) else st.secrets.get("mongo", {})
    MONGO_URI = mongo.get("uri")
    DB_NAME = mongo.get("db", "expense_tracker")
    COLLECTION_NAME = mongo.get("collection", "expenses")

    if not MONGO_URI:
        st.error("❌ MongoDB URI not found in .streamlit/secrets.toml (key: [mongo] uri). Add it or export MONGO_URI env var.")
        st.stop()

    client = MongoClient(MONGO_URI)
    return client[DB_NAME][COLLECTION_NAME]

def get_credentials():
    # You can put real usernames/passwords in secrets.toml under [credentials]
    # Example:
    # [credentials]
    # admin = "adminpass"
    # balaji = "balajipass"
    # iyyappa = "iyyappapass"
    creds_from_secrets = st.secrets.get("credentials", {}) if isinstance(st.secrets, dict) else st.secrets.get("credentials", {})
    # Normalize keys to lowercase
    creds = {k.lower(): v for k, v in creds_from_secrets.items()}
    # sensible defaults if none provided
    if not creds:
        creds = {
            "admin": "admin123",
            "balaji": "balaji123",
            "iyyappa": "iyyappa123",
            "gokul": "gokul123"
        }
    return creds

# --------------------------
# PDF builder (ReportLab platypus) or CSV fallback
# --------------------------
def generate_pdf_bytes(df: pd.DataFrame, title: str = "Expense Report") -> bytes:
    """
    Returns PDF bytes. If ReportLab not available this will raise.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph(title, styles["Title"]))
    elems.append(Spacer(1, 12))
    total = df["amount"].sum()
    summary_text = f"Total expenses: ₹ {total:.2f} — Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    elems.append(Paragraph(summary_text, styles["Normal"]))
    elems.append(Spacer(1, 12))

    df_export = df.copy()
    if "timestamp" in df_export.columns:
        df_export["timestamp"] = df_export["timestamp"].astype(str)

    cols = [c for c in ["timestamp", "date", "category", "friend", "amount", "notes"] if c in df_export.columns]
    table_data = [cols]
    for _, r in df_export.iterrows():
        row = [str(r.get(c, "")) for c in cols]
        table_data.append(row)

    tbl = Table(table_data, repeatRows=1)
    tbl_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b2b2b")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("FONTNAME", (0,0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ])
    tbl.setStyle(tbl_style)
    elems.append(tbl)
    doc.build(elems)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

# --------------------------
# Login UI (sidebar)
# --------------------------
def login_ui():
    creds = get_credentials()
    st.sidebar.title("🔐 Login")

    # Pre-fill from session state if present
    default_user = st.session_state.get("login_username", "")
    default_pass = ""

    username = st.sidebar.text_input("Username", value=default_user, key="login_username")
    password = st.sidebar.text_input("Password", value=default_pass, type="password", key="login_password")
    login_clicked = st.sidebar.button("Login")

    if login_clicked:
        uname = (username or "").strip().lower()
        if uname and uname in creds and creds[uname] == password:
            st.session_state["user"] = uname
            st.sidebar.success(f"✅ Logged in as {uname}")
        else:
            st.sidebar.error("❌ Invalid username or password")

    # Show logged in user & logout
    if st.session_state.get("user"):
        st.sidebar.markdown(f"**User:** {st.session_state['user']}")
        if st.sidebar.button("Logout"):
            for k in ("user", "login_username", "login_password"):
                if k in st.session_state:
                    del st.session_state[k]
            st.sidebar.success("Logged out")

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
    # Use readable names but the usernames are lower-case in session state
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
            # pre-select friend based on logged in user if possible
            current_user = st.session_state.get("user", "")
            # capitalise first letter for display match if possible
            default_friend = None
            if current_user:
                # try to match a friend name ignoring case
                for f in friends:
                    if f.lower() == current_user:
                        default_friend = f
                        break
            friend = st.selectbox("Who Spent?", friends, index=friends.index(default_friend) if default_friend else 0)
            if friend == "Others":
                friend_comment = st.text_input("Enter custom friend name")
                if friend_comment.strip():
                    friend = friend_comment

        amount = st.number_input("Amount (₹)", min_value=1.0, step=1.0)
        notes = st.text_area("Comments / Notes (optional)")

        submitted = st.form_submit_button("💾 Save Expense")
        if submitted:
            doc = {
                "category": category,
                "friend": friend,
                "amount": float(amount),
                "notes": notes,
                "timestamp": datetime.now(),
                "added_by": st.session_state.get("user", "")
            }
            collection.insert_one(doc)
            st.success("✅ Expense saved successfully!")

# --------------------------
# Filter UI (optional)
# --------------------------
def filter_ui():
    today = date.today()
    default_from = date(today.year, today.month, 1)
    col1, col2 = st.columns(2)
    with col1:
        date_from = st.date_input("From", value=st.session_state.get("date_from", default_from), key="date_from")
    with col2:
        date_to = st.date_input("To", value=st.session_state.get("date_to", today), key="date_to")
    st.session_state["date_from"] = date_from
    st.session_state["date_to"] = date_to
    return date_from, date_to

# --------------------------
# Analytics & Transactions
# --------------------------
def show_analytics(df: pd.DataFrame):
    st.subheader("📊 Expense Summary")
    total_spent = df["amount"].sum()
    st.metric("Total Spent", f"₹{total_spent:,.2f}")

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

def show_transactions(df: pd.DataFrame, collection):
    st.markdown("### 📝 Transactions")
    # allowed deleters (lowercase usernames)
    allowed = {"admin", "balaji", "iyyappa"}
    current_user = st.session_state.get("user", "").lower()

    # show each row as expander
    for _, row in df.sort_values(by="date", ascending=False).iterrows():
        id_str = str(row["_id"])
        with st.expander(f"📌 {row['date']} - {row['category']} - ₹{row['amount']} (by {row['friend']})"):
            st.write(f"**Category:** {row['category']}")
            st.write(f"**Friend:** {row['friend']}")
            st.write(f"**Amount:** ₹{row['amount']}")
            st.write(f"**Notes:** {row['notes'] if row['notes'] else '-'}")
            st.write(f"**Added by:** {row.get('added_by', '-')}")
            # Delete allowed for certain logged-in users
            if current_user in allowed:
                if st.button("❌ Delete", key=f"del-{id_str}"):
                    try:
                        collection.delete_one({"_id": ObjectId(id_str)})
                        st.success("✅ Expense deleted")
                        # no experimental_rerun(); Streamlit will rerun after this button click automatically
                    except Exception as e:
                        st.error(f"Failed to delete: {e}")

# --------------------------
# Export options
# --------------------------
def export_downloads(df: pd.DataFrame):
    st.markdown("### ⤓ Export")
    col1, col2 = st.columns(2)

    # CSV download
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    col1.download_button("⬇️ Download CSV", data=csv_bytes, file_name="expenses.csv", mime="text/csv")

    # PDF download (ReportLab)
    if HAS_REPORTLAB:
        try:
            pdf_bytes = generate_pdf_bytes(df, title="Expense Report")
            col2.download_button("⬇️ Download PDF", data=pdf_bytes, file_name="expenses.pdf", mime="application/pdf")
        except Exception as e:
            col2.error(f"PDF export failed: {e} (reportlab ok?)")
    else:
        col2.info("PDF export requires reportlab. Install with `pip install reportlab`")

# --------------------------
# Main function
# --------------------------
def main():
    st.set_page_config(page_title="💰 Expense Tracker", page_icon="📊", layout="wide")
    st.title("💰 Expense Tracker")

    # Login UI (sidebar)
    login_ui()
    if "user" not in st.session_state:
        st.info("Please log in from the sidebar to continue.")
        return

    # DB collection
    collection = get_collection()

    # Expense entry form
    expense_form(collection)

    # Date filter
    date_from, date_to = filter_ui()

    # Load data and filter by date range
    raw = list(collection.find())
    if not raw:
        st.info("No expenses recorded yet. Add one above! 🚀")
        return

    df = pd.DataFrame(raw)
    # Keep id as string for UI keys; original _id present for deletion if needed
    df["_id"] = df["_id"].astype(str)
    # Ensure timestamp field exists and convert
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    else:
        df["timestamp"] = pd.NaT
    df["date"] = df["timestamp"].dt.date.fillna(date.today())
    # Filter by selected dates
    mask = (df["date"] >= date_from) & (df["date"] <= date_to)
    df = df.loc[mask].reset_index(drop=True)

    if df.empty:
        st.info("No expenses in the selected date range.")
        return

    # Show analytics, transactions, exports
    show_analytics(df)
    show_transactions(df, collection)
    export_downloads(df)

if __name__ == "__main__":
    main()
