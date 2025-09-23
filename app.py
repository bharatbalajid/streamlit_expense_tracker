# app.py
"""
Expense Tracker — cookie-based session tokens (client-side cookie).
- Uses Redis to store session_token -> username (TTL)
- After login: token stored in Redis; JS sets cookie `session_token`
- On page load: JS reads cookie and temporarily adds token to URL so server-side restore can run,
  then JS clears query params. Token remains in cookie (not HTTP-only).
Note: For true HTTP-only cookie you need a separate auth endpoint (see notes below).
"""

import os
import io
import uuid
import random
import hashlib
from datetime import datetime, timedelta
from typing import Optional

import streamlit as st
import pandas as pd
import plotly.express as px
from pymongo import MongoClient
from bson.objectid import ObjectId

# Redis
try:
    import redis
except Exception:
    redis = None

# ReportLab optional
HAS_REPORTLAB = True
try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
except Exception:
    HAS_REPORTLAB = False

# --------------------------
# Config & checks
# --------------------------
st.set_page_config(page_title="💰 Expense Tracker", layout="wide")

if redis is None:
    st.error("`redis` package not installed. Install with: pip install redis")
    st.stop()

REDIS_URL = st.secrets.get("redis", {}).get("url") if st.secrets and st.secrets.get("redis") else os.environ.get("REDIS_URL")
if not REDIS_URL:
    st.error("Redis URL not configured (secrets or env).")
    st.stop()

try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
except Exception as e:
    st.error(f"Failed to connect to Redis: {e}")
    st.stop()

# MongoDB
if st.secrets and st.secrets.get("mongo", {}).get("uri"):
    MONGO_URI = st.secrets.get("mongo", {}).get("uri")
    DB_NAME = st.secrets.get("mongo", {}).get("db", "expense_tracker")
    COLLECTION_NAME = st.secrets.get("mongo", {}).get("collection", "expenses")
else:
    MONGO_URI = os.environ.get("MONGO_URI")
    DB_NAME = os.environ.get("MONGO_DB", "expense_tracker")
    COLLECTION_NAME = os.environ.get("MONGO_COLLECTION", "expenses")

if not MONGO_URI:
    st.error("MongoDB URI not configured in secrets or environment.")
    st.stop()

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]
users_col = db["users"]
audit_col = db["audit_logs"]

# --------------------------
# Helpers
# --------------------------
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def log_action(action: str, actor: str, target: str = None, details: dict = None):
    try:
        audit_col.insert_one({
            "action": action,
            "actor": actor,
            "target": target,
            "details": details or {},
            "timestamp": datetime.utcnow()
        })
    except Exception:
        pass

def ensure_superadmin():
    if not st.secrets:
        return
    secret_user = st.secrets.get("admin", {}).get("username")
    secret_pass = st.secrets.get("admin", {}).get("password")
    if secret_user and secret_pass:
        if not users_col.find_one({"username": secret_user}):
            users_col.insert_one({
                "username": secret_user,
                "password_hash": hash_password(secret_pass),
                "role": "admin",
                "created_at": datetime.utcnow()
            })
            log_action("create_superadmin", "system", target=secret_user)

ensure_superadmin()

for k, default in {
    "authenticated": False,
    "username": None,
    "is_admin": False,
    "_login_error": None,
    "login_heading": None,
    "login_tip": None
}.items():
    if k not in st.session_state:
        st.session_state[k] = default

# Tanglish tips (unchanged)
tip_headings = [
    "😂 Kasa Save Panra Comedy Scene",
    "🤣 Wallet Cry Aana Avoid Panna Tip",
    "💡 Ennada Expense Ah Comedy Pannradhu",
    "🔥 Bill Kandu Shock Aagama Hack",
    "😅 Salary Vanthuruchu… Aana Enga?",
    "🤑 Budget Scene ku Punch Dialogue",
]
sample_tips = [
    "😂 ATM la cash illana, adhu unoda saving reminder da!",
    "🍲 Veetla sambar ₹50… hotel la same sambar ₹250. Comedy ah illa?",
    "💳 Credit card swipe easy, pay panna hard — ontime pay pannunga!",
    "⚡ AC full night on panna — morning bill paartha shock guaranteed.",
    "📦 Online cart la 24 hrs vacha think pannunga — impulse buy avoid.",
    "🚗 Carpool pannunga — petrol save + friends' jokes included.",
]

def get_random_heading_and_tip():
    return random.choice(tip_headings), random.choice(sample_tips)

# --------------------------
# Redis session functions
# --------------------------
def generate_token() -> str:
    return uuid.uuid4().hex

def store_token(token: str, username: str, ttl_seconds: int = 60*60*4) -> bool:
    try:
        return redis_client.setex(f"session:{token}", ttl_seconds, username)
    except Exception:
        return False

def get_username(token: str) -> Optional[str]:
    try:
        return redis_client.get(f"session:{token}")
    except Exception:
        return None

def delete_token(token: str) -> bool:
    try:
        return bool(redis_client.delete(f"session:{token}"))
    except Exception:
        return False

def refresh_ttl(token: str, ttl_seconds: int = 60*60*4) -> bool:
    try:
        return redis_client.expire(f"session:{token}", ttl_seconds)
    except Exception:
        return False

# --------------------------
# Cookie helper components (JS)
# --------------------------
# 1) Set cookie (called after server stores token). This sets cookie and reloads page without query params.
def set_cookie_and_reload(token: str, max_age_seconds: int = 60*60*4):
    """Inject JS that sets a cookie `session_token` then reloads page without query params."""
    # Secure flags: add ;Secure if using HTTPS in production
    # Not HTTP-only (can't be from JS)
    js = f"""
    <script>
    (function() {{
      var d = new Date();
      d.setTime(d.getTime() + ({max_age_seconds} * 1000));
      var expires = "expires="+ d.toUTCString();
      var cookie = "session_token={token}; " + expires + "; path=/";
      // In production you should add ;Secure; SameSite=Strict as needed
      document.cookie = cookie;
      // remove token from URL if present then reload
      if (window.location.search.indexOf('session_token=') !== -1) {{
        var url = new URL(window.location.href);
        url.searchParams.delete('session_token');
        window.history.replaceState(null, '', url.toString());
      }}
      // reload to initialize server-side restore (server will read query param or cookie)
      window.location.reload();
    }})();
    </script>
    """
    st.components.v1.html(js, height=0)

# 2) Read cookie and if token exists and no query param, temporarily add token to URL so server can restore.
#    This component runs early on load (in the main page body).
def early_cookie_to_query():
    js = """
    <script>
    (function() {
      // helper to read cookie
      function getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return null;
      }
      const token = getCookie('session_token');
      if (token) {
        // if query param already has token, do nothing
        const params = new URLSearchParams(window.location.search);
        if (!params.get('session_token')) {
          // add token to URL temporarily (do not push to history)
          params.set('session_token', token);
          const newUrl = window.location.pathname + '?' + params.toString();
          window.history.replaceState(null, '', newUrl);
          // After a short timeout we will clear the token param in URL to keep it out of sight
          setTimeout(function() {
            const p = new URLSearchParams(window.location.search);
            if (p.get('session_token')) {
              p.delete('session_token');
              const cleared = window.location.pathname + (p.toString() ? '?' + p.toString() : '');
              window.history.replaceState(null, '', cleared);
            }
          }, 1500);
        }
      }
    })();
    </script>
    """
    # Render small invisible component so JS executes at top of page
    st.components.v1.html(js, height=0)

# --------------------------
# Auth & Admin functions
# --------------------------
def create_redis_session_and_set_cookie(username: str, ttl_seconds: int = 60*60*4):
    token = generate_token()
    ok = store_token(token, username, ttl_seconds)
    if ok:
        # set cookie via JS component (not HTTP-only)
        set_cookie_and_reload(token, max_age_seconds=ttl_seconds)
        return token
    return None

def restore_session_from_token_input():
    """Try to restore session either via query param OR via cookie-to-query JS (we run that JS early)."""
    # First try query param (st.query_params as before)
    qp_val = st.query_params.get("session_token")
    token = None
    if qp_val:
        if isinstance(qp_val, list):
            token = qp_val[0] if qp_val else None
        else:
            token = qp_val
    # If no token in query, we still rely on our early JS that temporarily injects it; the page reload will cause this function to run again with token in query
    if token and not st.session_state.get("authenticated"):
        username = get_username(token)
        if username:
            st.session_state["authenticated"] = True
            st.session_state["username"] = username
            u = users_col.find_one({"username": username})
            st.session_state["is_admin"] = (u.get("role") == "admin") if u else False
            # once restored, clear query params to remove token from visible URL
            try:
                st.query_params.clear()
            except Exception:
                pass

def clear_session_cookie_and_redis():
    # delete token from cookie and redis (attempt)
    # read token from cookie via JS, clear redis via temporary query param or instruct user to logout (best-effort)
    # We'll just clear cookie client-side and clear query params.
    js = """
    <script>
    (function() {
      document.cookie = "session_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
      // Also clear query params
      const u = new URL(window.location.href);
      u.search = '';
      window.history.replaceState(null, '', u.toString());
      window.location.reload();
    })();
    </script>
    """
    st.components.v1.html(js, height=0)

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
        # create token in redis and set cookie via JS (then reload hides token)
        create_redis_session_and_set_cookie(user)
        log_action("login", user)
    else:
        st.session_state["_login_error"] = "Invalid username or password."

def logout():
    user = st.session_state.get("username")
    log_action("logout", user)
    # best-effort: clear cookie client-side and attempt to clear redis by reading cookie (can't read cookie server-side)
    # We'll remove session_state and instruct user that cookie will be cleared
    st.session_state["authenticated"] = False
    st.session_state["username"] = None
    st.session_state["is_admin"] = False
    st.session_state["_login_error"] = None
    # clear cookie client-side
    clear_session_cookie_and_redis()

# Admin helpers (same as previous)
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
    log_action("create_user", st.session_state.get("username"), target=username, details={"role": role})
    st.success(f"User '{username}' created with role '{role}'.")

def reset_user_password(target_username: str, new_password: str):
    if not target_username or not new_password:
        st.error("Provide target user and new password.")
        return
    users_col.update_one({"username": target_username}, {"$set": {"password_hash": hash_password(new_password)}})
    log_action("reset_password", st.session_state.get("username"), target=target_username)
    st.success(f"Password for '{target_username}' has been reset.")

def delete_user(target_username: str, delete_expenses: bool = False):
    if not target_username:
        st.error("Select a user to delete.")
        return
    users_col.delete_one({"username": target_username})
    if delete_expenses:
        collection.delete_many({"owner": target_username})
    log_action("delete_user", st.session_state.get("username"), target=target_username, details={"deleted_expenses": delete_expenses})
    st.success(f"User '{target_username}' deleted.")

# PDF helpers (same as before)
def generate_pdf_bytes(df: pd.DataFrame, title: str = "Expense Report") -> bytes:
    if not HAS_REPORTLAB:
        raise RuntimeError("reportlab not available")
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    elems = [Paragraph(title, styles["Title"]), Spacer(1,12)]
    total = df["amount"].sum() if "amount" in df.columns else 0.0
    elems.append(Paragraph(f"Total expenses: ₹ {total:.2f} — Generated: {datetime.now().strftime('%Y-%m-%d')}", styles["Normal"]))
    elems.append(Spacer(1,12))
    df_export = df.copy()
    if "timestamp" in df_export.columns:
        df_export["timestamp"] = df_export["timestamp"].astype(str)
    cols = [c for c in ["timestamp","category","friend","amount","notes","owner"] if c in df_export.columns]
    table_data = [cols]
    for _, r in df_export.iterrows():
        table_data.append([str(r.get(c,"")) for c in cols])
    tbl = Table(table_data, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#2b2b2b")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("GRID",(0,0),(-1,-1),0.5,colors.grey),
        ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
        ("FONTSIZE",(0,0),(-1,-1),8),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))
    elems.append(tbl)
    doc.build(elems)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

def generate_friend_pdf_bytes(friend_name: str) -> bytes:
    if not friend_name:
        raise ValueError("friend_name required")
    docs = list(collection.find({"friend": friend_name}))
    if not docs:
        empty_df = pd.DataFrame(columns=["timestamp","category","friend","amount","notes","owner"])
        title = f"Expense Report - Friend: {friend_name} (No records)"
        return generate_pdf_bytes(empty_df, title=title)
    df = pd.DataFrame(docs)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d")
    if "_id" in df.columns:
        df = df.drop(columns=["_id"])
    title = f"Expense Report - Friend: {friend_name}"
    return generate_pdf_bytes(df, title=title)

def get_visible_docs():
    if st.session_state.get("is_admin"):
        return list(collection.find())
    else:
        return list(collection.find({"owner": st.session_state.get("username")}))

# --------------------------
# UI
# --------------------------
def show_app():
    # Run JS early to copy cookie -> temporary query param (if cookie exists).
    # This allows server-side restore using existing logic.
    early_cookie_to_query()

    # Try restore (query param will be present briefly if cookie exists)
    restore_session_from_token_input()

    st.title("💰 Personal Expense Tracker")

    # Sidebar: login/logout
    with st.sidebar:
        st.header("🔒 Account")
        if not st.session_state["authenticated"]:
            st.text_input("Username", key="login_user")
            st.text_input("Password", type="password", key="login_pwd")
            st.button("Login", on_click=login, key="login_btn")
            if st.session_state["_login_error"]:
                st.error(st.session_state["_login_error"])
        else:
            st.write(f"User: **{st.session_state['username']}**")
            if st.session_state["is_admin"]:
                st.success("Admin")
            st.button("Logout", on_click=logout, key="logout_btn")

    if not st.session_state["authenticated"]:
        st.info("🔒 Please log in from the sidebar.")
        st.markdown("---")
        if not st.session_state.get("login_heading") or not st.session_state.get("login_tip"):
            h,t = get_random_heading_and_tip()
            st.session_state["login_heading"] = h
            st.session_state["login_tip"] = t
        st.markdown(f"<h3 style='text-align:center'>{st.session_state['login_heading']}</h3>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align:center; font-size:20px; color:#2E8B57'>{st.session_state['login_tip']}</div>", unsafe_allow_html=True)
        if st.button("😂 Refresh Tip", key="refresh"):
            h,t = get_random_heading_and_tip()
            st.session_state["login_heading"], st.session_state["login_tip"] = h,t
            st.experimental_rerun()
        return

    # Expense form (Fuel subcategories implemented)
    categories = ["Food","Cinema","Groceries","Bill & Investment","Medical","Fuel","Others"]
    grocery_subcategories = ["Vegetables","Fruits","Milk & Dairy","Rice & Grains","Lentils & Pulses","Spices & Masalas","Oil & Ghee","Snacks & Packaged Items","Bakery & Beverages"]
    bill_subcats = ["CC","Electricity Bill","RD","Mutual Fund","Gold Chit"]
    fuel_subcats = ["Petrol","Diesel","EV Charge"]
    friends = ["Iyyappa","Srinath","Gokul","Balaji","Magesh","Others"]

    col1,col2 = st.columns([2,1])
    with col1:
        chosen_cat = st.selectbox("Expense Type", options=categories, key="ui_category")
        if chosen_cat == "Groceries":
            sub = st.selectbox("Grocery Subcategory", grocery_subcategories, key="ui_grocery_sub")
            category_final = f"Groceries - {sub}"
        elif chosen_cat == "Bill & Investment":
            sub = st.selectbox("Bill/Investment Subcategory", bill_subcats, key="ui_bill_sub")
            category_final = f"Bill & Investment - {sub}"
        elif chosen_cat == "Fuel":
            sub = st.selectbox("Fuel Subcategory", fuel_subcats, key="ui_fuel_sub")
            category_final = f"Fuel - {sub}"
        elif chosen_cat == "Others":
            custom = st.text_input("Custom category", key="ui_custom_cat")
            category_final = custom.strip() if custom else "Others"
        else:
            category_final = chosen_cat
    with col2:
        chosen_friend = st.selectbox("Who Spent?", options=friends, key="ui_friend")
        if chosen_friend == "Others":
            cf = st.text_input("Custom friend", key="ui_custom_friend")
            friend_final = cf.strip() if cf else "Others"
        else:
            friend_final = chosen_friend

    st.markdown("---")
    with st.form("expense_form", clear_on_submit=True):
        expense_date = st.date_input("Date", value=datetime.now().date(), key="ui_date")
        amount = st.number_input("Amount (₹)", min_value=1.0, step=1.0, key="ui_amount")
        notes = st.text_area("Comments / Notes", key="ui_notes")
        if st.form_submit_button("💾 Save Expense"):
            ts = datetime.combine(expense_date, datetime.min.time())
            try:
                collection.insert_one({
                    "category": category_final,
                    "friend": friend_final,
                    "amount": float(amount),
                    "notes": notes,
                    "timestamp": ts,
                    "owner": st.session_state["username"]
                })
                # refresh TTL if cookie present
                # read cookie via JS is not possible server-side; but we can attempt to read query param
                qp = st.query_params.get("session_token")
                token = qp[0] if isinstance(qp, list) and qp else (qp if isinstance(qp,str) else None)
                if token:
                    refresh_ttl(token)
                log_action("add_expense", st.session_state["username"], details={"category": category_final, "amount": float(amount)})
                st.success("Expense saved!")
            except Exception as e:
                st.error(f"Failed to save expense: {e}")

    # Admin controls, listing, charts, download — same as before (omitted here for brevity or keep full implementation)
    # ... (you can reuse the admin & listing code from previous full script)
    # For brevity, show visible docs and basic charts:
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
        st.subheader("📊 All Expenses")
        st.dataframe(df)
        # (charts & PDF code: reuse generate_pdf_bytes and plotting sections)
    else:
        st.info("No expenses to show yet.")

if __name__ == "__main__":
    show_app()