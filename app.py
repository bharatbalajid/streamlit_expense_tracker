# app.py
"""
Expense Tracker (full) with cookie-backed sessions via small JS snippets.
- MongoDB backend: users, expenses, audit_logs
- Redis-backed session tokens (persist across refresh)
- JS writes session_token cookie and removes token from URL
- Tanglish funny + money-saving tips on login page (centered)
- Admin controls: create/reset/delete user, delete expenses, view audit logs
- PDF export with reportlab (optional)
- OpenTelemetry tracing (Jaeger thrift collector hardcoded)
"""

import os
import io
import uuid
import random
import hashlib
import socket
import logging
from datetime import datetime, timezone
from typing import Optional

import streamlit as st
import pandas as pd
import plotly.express as px
from pymongo import MongoClient
from bson.objectid import ObjectId

# Redis should be installed for session persistence
try:
    import redis
except Exception:
    redis = None

# Optional ReportLab
HAS_REPORTLAB = True
try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
except Exception:
    HAS_REPORTLAB = False

# --------------------------
# Hardcoded Jaeger thrift collector endpoint & service name
# --------------------------
JAEGER_COLLECTOR_ENDPOINT = "http://3.208.18.133:14268/api/traces"  # thrift HTTP collector that worked for you
SERVICE_NAME = "expense-tracker"

# --------------------------
# Logging config (reduce noisy OTEL logs)
# --------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("expense-tracker")
logging.getLogger("opentelemetry").setLevel(logging.ERROR)
logging.getLogger("opentelemetry.sdk").setLevel(logging.ERROR)
logging.getLogger("opentelemetry.exporter").setLevel(logging.ERROR)
logging.getLogger("opentelemetry.sdk._shared_internal").setLevel(logging.ERROR)

# --------------------------
# Tracing init using Jaeger thrift exporter (collector endpoint)
# - If initialization fails, we fall back to ConsoleSpanExporter.
# - This avoids repeated connection refused stack traces.
# --------------------------
TRACING_AVAILABLE = False
tracer = None

def _is_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception as e:
        logger.debug("TCP check failed for %s:%s — %s", host, port, e)
        return False

try:
    # import opentelemetry components
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    # Jaeger thrift exporter (deprecated but works with collector endpoint)
    # requires 'opentelemetry-exporter-jaeger-thrift' and 'deprecated'
    from opentelemetry.exporter.jaeger.thrift import JaegerExporter

    resource = Resource.create(attributes={"service.name": SERVICE_NAME})
    tracer_provider = TracerProvider(resource=resource)

    # Do a lightweight TCP check to 14268 (collector port) to avoid obvious connection refused loops.
    # Parse port from configured endpoint if possible
    from urllib.parse import urlparse
    parsed = urlparse(JAEGER_COLLECTOR_ENDPOINT)
    collect_host = parsed.hostname or "localhost"
    collect_port = parsed.port or 14268

    if _is_reachable(collect_host, collect_port, timeout=2.0):
        try:
            jaeger_exporter = JaegerExporter(collector_endpoint=JAEGER_COLLECTOR_ENDPOINT)
            span_processor = BatchSpanProcessor(jaeger_exporter)
            tracer_provider.add_span_processor(span_processor)
            TRACING_AVAILABLE = True
            tracer = trace.get_tracer(__name__)
            logger.info("Tracing initialized: Jaeger thrift collector at %s", JAEGER_COLLECTOR_ENDPOINT)
        except Exception as e:
            # fallback to console exporter
            logger.warning("JaegerExporter init failed (%s). Falling back to ConsoleSpanExporter.", e)
            console_exporter = ConsoleSpanExporter()
            tracer_provider.add_span_processor(BatchSpanProcessor(console_exporter))
            trace.set_tracer_provider(tracer_provider)
            tracer = trace.get_tracer(__name__)
            TRACING_AVAILABLE = False
    else:
        # collector not reachable -> fallback to console exporter
        console_exporter = ConsoleSpanExporter()
        tracer_provider.add_span_processor(BatchSpanProcessor(console_exporter))
        trace.set_tracer_provider(tracer_provider)
        tracer = trace.get_tracer(__name__)
        TRACING_AVAILABLE = False
        logger.info("Jaeger collector not reachable at %s:%s — using ConsoleSpanExporter.", collect_host, collect_port)

    # set provider (done earlier if fallback)
    trace.set_tracer_provider(tracer_provider)

    # Try auto-instrumentations (best-effort)
    try:
        from opentelemetry.instrumentation.pymongo import PymongoInstrumentor
        PymongoInstrumentor().instrument()
    except Exception:
        pass
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
    except Exception:
        pass

except Exception as e:
    # tracing not available — keep application functional
    logger.debug("Tracing initialization exception: %s", e)
    TRACING_AVAILABLE = False
    tracer = None

# --------------------------
# Streamlit page config
# --------------------------
st.set_page_config(page_title="💰 Expense Tracker", layout="wide")

# Show on sidebar whether tracing is active
if TRACING_AVAILABLE:
    st.sidebar.success(f"Tracing: enabled (Jaeger collector)\nservice={SERVICE_NAME}")
else:
    st.sidebar.info("Tracing: disabled or Console fallback (no Jaeger)")

# --------------------------
# Redis connection
# --------------------------
if redis is None:
    st.error("`redis` package not installed. Install it with `pip install redis` and restart the app.")
    st.stop()

REDIS_URL = None
if st.secrets and st.secrets.get("redis", {}).get("url"):
    REDIS_URL = st.secrets.get("redis", {}).get("url")
else:
    REDIS_URL = os.environ.get("REDIS_URL")

if not REDIS_URL:
    st.error("Redis URL not configured. Add it to .streamlit/secrets.toml under [redis] url or set REDIS_URL env var.")
    st.stop()

try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
except Exception as e:
    st.error(f"Failed to connect to Redis: {e}")
    st.stop()

# --------------------------
# MongoDB
# --------------------------
if st.secrets and st.secrets.get("mongo", {}).get("uri"):
    MONGO_URI = st.secrets.get("mongo", {}).get("uri")
    DB_NAME = st.secrets.get("mongo", {}).get("db", "expense_tracker")
    COLLECTION_NAME = st.secrets.get("mongo", {}).get("collection", "expenses")
else:
    MONGO_URI = os.environ.get("MONGO_URI")
    DB_NAME = os.environ.get("MONGO_DB", "expense_tracker")
    COLLECTION_NAME = os.environ.get("MONGO_COLLECTION", "expenses")

if not MONGO_URI:
    st.error("MongoDB URI not configured in .streamlit/secrets.toml or environment.")
    st.stop()

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]
users_col = db["users"]
audit_col = db["audit_logs"]

# --------------------------
# Helpers & audit logging
# --------------------------
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def now_utc():
    return datetime.now(timezone.utc)

def log_action(action: str, actor: str, target: str = None, details: dict = None):
    try:
        rec = {
            "action": action,
            "actor": actor,
            "target": target,
            "details": details or {},
            "timestamp": now_utc()
        }
        if TRACING_AVAILABLE and tracer:
            with tracer.start_as_current_span("audit_log_insert") as span:
                span.set_attribute("audit.action", action or "")
                span.set_attribute("audit.actor", actor or "")
                audit_col.insert_one(rec)
        else:
            audit_col.insert_one(rec)
    except Exception:
        pass

# Ensure optional superadmin from secrets
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
                "created_at": now_utc()
            })
            log_action("create_superadmin", "system", target=secret_user)

ensure_superadmin()

# --------------------------
# Session defaults
# --------------------------
defaults = {
    "authenticated": False,
    "username": None,
    "is_admin": False,
    "_login_error": None,
    "login_heading": None,
    "login_tip": None,
    # admin UI keys
    "create_user_username": "",
    "create_user_password": "",
    "create_user_role": "user",
    "reset_user_select": "",
    "reset_user_newpass": "",
    "delete_user_select": "",
    "delete_user_confirm": False,
    "delete_user_expenses": False,
    "del_all_confirm": False,
    "confirm_delete_selected_key": False,
}
for k, default in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = default

# Tanglish headings & tips
tip_headings = [
    "😂 Kasa Save Panra Comedy Scene",
    "🤣 Wallet Cry Aana Avoid Panna Tip",
    "💡 Ennada Expense Ah Comedy Pannradhu",
    "🔥 Bill Kandu Shock Aagama Hack",
    "😅 Salary Vanthuruchu… Aana Enga?",
    "🤑 Budget Scene ku Punch Dialogue",
    "📉 Spend Pannadha… Laugh Pannu Da",
]

sample_tips = [
    "😂 ATM la cash illana, adhu unoda saving reminder da!",
    "🍲 Veetla sambar ₹50… hotel la same sambar ₹250. Comedy ah illa?",
    "💳 Credit card swipe easy, pay panna hard — ontime pay pannunga!",
    "⚡ AC full night on panna — morning bill paartha shock guaranteed.",
    "📦 Online cart la 24 hrs vacha think pannunga — impulse buy avoid.",
    "🤣 Monthly budget panna, illa na budget dhan unga comedy pannum.",
    "🚗 Carpool pannunga — petrol save + friends' jokes included.",
    "🍕 Daily pizza stop panna — 1 year la oven vanganum nu sollanum.",
    "💡 Light off pannunga da — electric bill ku break poda.",
    "📊 Expense note panni paarunga — small leaks big loss."
]

def get_random_heading_and_tip():
    return random.choice(tip_headings), random.choice(sample_tips)

# --------------------------
# Redis session helpers
# --------------------------
def generate_token() -> str:
    return uuid.uuid4().hex

def store_token_in_redis(token: str, username: str, ttl_seconds: int = 60 * 60 * 4) -> bool:
    try:
        if TRACING_AVAILABLE and tracer:
            with tracer.start_as_current_span("redis_set_session") as span:
                span.set_attribute("session.token", token)
                span.set_attribute("session.username", username)
                return redis_client.setex(f"session:{token}", ttl_seconds, username)
        else:
            return redis_client.setex(f"session:{token}", ttl_seconds, username)
    except Exception:
        return False

def get_username_from_token(token: str) -> Optional[str]:
    try:
        if TRACING_AVAILABLE and tracer:
            with tracer.start_as_current_span("redis_get_session") as span:
                span.set_attribute("session.token", token)
                return redis_client.get(f"session:{token}")
        else:
            return redis_client.get(f"session:{token}")
    except Exception:
        return None

def delete_token(token: str) -> bool:
    try:
        if TRACING_AVAILABLE and tracer:
            with tracer.start_as_current_span("redis_delete_session") as span:
                span.set_attribute("session.token", token)
                return bool(redis_client.delete(f"session:{token}"))
        else:
            return bool(redis_client.delete(f"session:{token}"))
    except Exception:
        return False

def refresh_token_ttl(token: str, ttl_seconds: int = 60 * 60 * 4) -> bool:
    try:
        if TRACING_AVAILABLE and tracer:
            with tracer.start_as_current_span("redis_refresh_ttl") as span:
                span.set_attribute("session.token", token)
                return redis_client.expire(f"session:{token}", ttl_seconds)
        else:
            return redis_client.expire(f"session:{token}", ttl_seconds)
    except Exception:
        return False

# Query-param helpers
def set_query_token(token: str):
    st.query_params.update({"session_token": token})

def clear_query_params():
    st.query_params.clear()

def read_token_from_query() -> Optional[str]:
    val = st.query_params.get("session_token", None)
    if val is None:
        return None
    if isinstance(val, list):
        return val[0] if val else None
    return val

# Cookie/URL tiny JS helpers
COOKIE_READER_HTML = """
<script>
(function(){
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.has('session_token')) return;
  function readCookie(name) {
    const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return v ? v.pop() : '';
  }
  const token = readCookie('session_token');
  if (token) {
    const newUrl = window.location.pathname + '?session_token=' + encodeURIComponent(token);
    window.location.href = newUrl;
  }
})();
</script>
"""

COOKIE_SETTER_HTML = """
<script>
(function(){
  const urlParams = new URLSearchParams(window.location.search);
  if (!urlParams.has('session_token')) return;
  const token = urlParams.get('session_token');
  if (!token) return;
  const maxAge = 60*60*4;
  document.cookie = 'session_token=' + encodeURIComponent(token) + '; path=/; max-age=' + maxAge + ';';
  const cleanUrl = window.location.protocol + '//' + window.location.host + window.location.pathname;
  window.history.replaceState({}, document.title, cleanUrl + window.location.hash);
})();
</script>
"""

# --------------------------
# Authentication & admin functions
# --------------------------
def create_redis_session_and_set_url(username: str, ttl_seconds: int = 60 * 60 * 4) -> Optional[str]:
    token = generate_token()
    ok = store_token_in_redis(token, username, ttl_seconds)
    if ok:
        set_query_token(token)
        return token
    return None

def restore_session_from_url_token():
    token = read_token_from_query()
    if token and not st.session_state.get("authenticated"):
        username = get_username_from_token(token)
        if username:
            st.session_state["authenticated"] = True
            st.session_state["username"] = username
            u = users_col.find_one({"username": username})
            st.session_state["is_admin"] = (u.get("role") == "admin") if u else False
            log_action("session_restored", username)
            if TRACING_AVAILABLE and tracer:
                with tracer.start_as_current_span("session_restored") as span:
                    span.set_attribute("user", username)

def clear_url_token_and_redis():
    token = read_token_from_query()
    if token:
        try:
            delete_token(token)
        except Exception:
            pass
    clear_query_params()

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
        create_redis_session_and_set_url(user)
        log_action("login", user)
        if TRACING_AVAILABLE and tracer:
            with tracer.start_as_current_span("user_login") as span:
                span.set_attribute("user", user)
    else:
        st.session_state["_login_error"] = "Invalid username or password."

def logout():
    user = st.session_state.get("username")
    log_action("logout", user)
    st.components.v1.html("""
    <script>
      document.cookie = "session_token=; path=/; max-age=0;";
      setTimeout(function(){ window.location.href = window.location.pathname; }, 200);
    </script>
    """, height=80)
    try:
        clear_url_token_and_redis()
    except Exception:
        pass
    st.session_state["authenticated"] = False
    st.session_state["username"] = None
    st.session_state["is_admin"] = False
    st.session_state["_login_error"] = None
    if TRACING_AVAILABLE and tracer:
        with tracer.start_as_current_span("user_logout") as span:
            span.set_attribute("user", user or "")

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
        "created_at": now_utc()
    })
    log_action("create_user", st.session_state.get("username"), target=username, details={"role": role})
    if TRACING_AVAILABLE and tracer:
        with tracer.start_as_current_span("create_user") as span:
            span.set_attribute("created.username", username)
            span.set_attribute("created.role", role)
    st.success(f"User '{username}' created with role '{role}'.")

def reset_user_password(target_username: str, new_password: str):
    if not target_username or not new_password:
        st.error("Provide target user and new password.")
        return
    result = users_col.update_one({"username": target_username}, {"$set": {"password_hash": hash_password(new_password)}})
    if result.matched_count == 0:
        st.warning(f"No user record found for '{target_username}'.")
        return
    log_action("reset_password", st.session_state.get("username"), target=target_username)
    if TRACING_AVAILABLE and tracer:
        with tracer.start_as_current_span("reset_user_password") as span:
            span.set_attribute("target.user", target_username)
    st.success(f"Password for '{target_username}' has been reset.")

def delete_user(target_username: str, delete_expenses: bool = False):
    if not target_username:
        st.error("Select a user to delete.")
        return
    span_ctx = tracer.start_as_current_span("delete_user") if (TRACING_AVAILABLE and tracer) else None
    try:
        if span_ctx:
            span_ctx.__enter__()
            span_ctx.set_attribute("target.username", target_username)
            span_ctx.set_attribute("delete_expenses", bool(delete_expenses))
        result = users_col.delete_one({"username": target_username})
        exp_result = None
        if delete_expenses:
            exp_result = collection.delete_many({"owner": target_username})
        if result.deleted_count == 0:
            st.warning(f"No user record found for '{target_username}'.")
            if delete_expenses and exp_result and exp_result.deleted_count > 0:
                st.info(f"User not found, but {exp_result.deleted_count} expense(s) owned by '{target_username}' were deleted.")
                log_action("delete_user_expenses_only", st.session_state.get("username"), target=target_username, details={"deleted_expenses": exp_result.deleted_count})
            return
        if exp_result and exp_result.deleted_count == 0 and delete_expenses:
            st.info(f"User '{target_username}' deleted, but no expenses were found for that user.")
        elif exp_result and exp_result.deleted_count > 0:
            st.success(f"User '{target_username}' and {exp_result.deleted_count} expense(s) deleted.")
        else:
            st.success(f"User '{target_username}' deleted.")
        log_action("delete_user", st.session_state.get("username"), target=target_username, details={"deleted_expenses": delete_expenses})
    finally:
        if span_ctx:
            span_ctx.__exit__(None, None, None)

# --------------------------
# PDF helpers
# --------------------------
def generate_pdf_bytes(df: pd.DataFrame, title: str = "Expense Report") -> bytes:
    if not HAS_REPORTLAB:
        raise RuntimeError("reportlab not available")
    if TRACING_AVAILABLE and tracer:
        with tracer.start_as_current_span("generate_pdf_bytes") as span:
            span.set_attribute("pdf.title", title)
            buffer = io.BytesIO()
            doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
            styles = getSampleStyleSheet()
            elems = []
            elems.append(Paragraph(title, styles["Title"]))
            elems.append(Spacer(1, 12))
            total = df["amount"].sum() if "amount" in df.columns else 0.0
            elems.append(Paragraph(f"Total expenses: ₹ {total:.2f} — Generated: {now_utc().date()}", styles["Normal"]))
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
    else:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
        styles = getSampleStyleSheet()
        elems = []
        elems.append(Paragraph(title, styles["Title"]))
        elems.append(Spacer(1, 12))
        total = df["amount"].sum() if "amount" in df.columns else 0.0
        elems.append(Paragraph(f"Total expenses: ₹ {total:.2f} — Generated: {now_utc().date()}", styles["Normal"]))
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

def generate_friend_pdf_bytes(friend_name: str) -> bytes:
    if not friend_name:
        raise ValueError("friend_name required")
    docs = list(collection.find({"friend": friend_name}))
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

# Visible docs
def get_visible_docs():
    if st.session_state.get("is_admin"):
        return list(collection.find())
    else:
        owner = st.session_state.get("username")
        return list(collection.find({"owner": owner}))

# --------------------------
# Main UI (full)
# --------------------------
def show_app():
    # If not authenticated and no token in URL, inject cookie reader JS
    token_in_query = read_token_from_query()
    if not st.session_state.get("authenticated") and not token_in_query:
        st.components.v1.html(COOKIE_READER_HTML, height=10)

    # restore session if query token present
    restore_session_from_url_token()

    # if token present in query, instruct client to set cookie (JS) and clean URL
    token_in_query = read_token_from_query()
    if token_in_query:
        st.components.v1.html(COOKIE_SETTER_HTML, height=10)

    st.title("💰 Personal Expense Tracker")

    # Sidebar: Login / Logout
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

    # If not authenticated: show centered Tanglish tip & heading
    if not st.session_state["authenticated"]:
        st.info("🔒 Please log in from the sidebar to access the Expense Tracker.")
        st.markdown("---")

        if not st.session_state.get("login_heading") or not st.session_state.get("login_tip"):
            h, t = get_random_heading_and_tip()
            st.session_state["login_heading"] = h
            st.session_state["login_tip"] = t

        st.markdown(f"<h3 style='text-align:center'>{st.session_state['login_heading']}</h3>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align:center; font-size:20px; color:#2E8B57; margin-bottom:8px'>{st.session_state['login_tip']}</div>", unsafe_allow_html=True)

        if st.button("😂 Refresh Tip", key="refresh_tip_center"):
            h, t = get_random_heading_and_tip()
            st.session_state["login_heading"] = h
            st.session_state["login_tip"] = t

        return

    # Authenticated UI
    categories = ["Food", "Cinema", "Groceries", "Bill & Investment", "Medical", "Fuel", "Others"]
    grocery_subcategories = ["Vegetables", "Fruits", "Milk & Dairy", "Rice & Grains", "Lentils & Pulses",
                             "Spices & Masalas", "Oil & Ghee", "Snacks & Packaged Items", "Bakery & Beverages"]
    bill_payment_subcategories = ["CC", "Electricity Bill", "RD", "Mutual Fund", "Gold Chit"]
    fuel_subcategories = ["Petrol", "Diesel", "EV Charge"]
    friends = ["Iyyappa", "Srinath", "Gokul", "Balaji", "Magesh", "Others"]

    col1, col2 = st.columns([2,1])
    with col1:
        chosen_cat = st.selectbox("Expense Type", options=categories, key="ui_category_key")
        if chosen_cat == "Groceries":
            sub = st.selectbox("Grocery Subcategory", grocery_subcategories, key="ui_grocery_subcat_key")
            category_final = f"Groceries - {sub}"
        elif chosen_cat == "Bill & Investment":
            sub = st.selectbox("Bill & Investment Subcategory", bill_payment_subcategories, key="ui_bill_subcat_key")
            category_final = f"Bill & Investment - {sub}"
        elif chosen_cat == "Fuel":
            sub = st.selectbox("Fuel Subcategory", fuel_subcategories, key="ui_fuel_subcat_key")
            category_final = f"Fuel - {sub}"
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

    with st.form("expense_form", clear_on_submit=True):
        expense_date = st.date_input("Date", value=datetime.now().date(), key="expense_date_key")
        amount = st.number_input("Amount (₹)", min_value=1.0, step=1.0, key="expense_amount_key")
        notes = st.text_area("Comments / Notes (optional)", key="expense_notes_key")
        if st.form_submit_button("💾 Save Expense", key="submit_expense_key"):
            ts = datetime.combine(expense_date, datetime.min.time()).replace(tzinfo=timezone.utc)
            owner = st.session_state["username"]
            try:
                if TRACING_AVAILABLE and tracer:
                    with tracer.start_as_current_span("save_expense") as span:
                        span.set_attribute("expense.owner", owner)
                        span.set_attribute("expense.category", category_final)
                        span.set_attribute("expense.amount", float(amount))
                        collection.insert_one({
                            "category": category_final,
                            "friend": friend_final,
                            "amount": float(amount),
                            "notes": notes,
                            "timestamp": ts,
                            "owner": owner
                        })
                else:
                    collection.insert_one({
                        "category": category_final,
                        "friend": friend_final,
                        "amount": float(amount),
                        "notes": notes,
                        "timestamp": ts,
                        "owner": owner
                    })

                token = read_token_from_query()
                if token:
                    refresh_token_ttl(token)
                log_action("add_expense", owner, details={"category": category_final, "amount": float(amount)})
                st.success("✅ Expense saved successfully!")
            except Exception as e:
                st.error(f"Failed to save expense: {e}")

    # Admin controls (omitted copying again? keep your same admin UI)
    if st.session_state.get("is_admin"):
        st.markdown("---")
        # Reset admin forms callback
        def reset_admin_forms():
            st.session_state["create_user_username"] = ""
            st.session_state["create_user_password"] = ""
            st.session_state["create_user_role"] = "user"
            st.session_state["reset_user_newpass"] = ""
            st.session_state["delete_user_confirm"] = False
            st.session_state["delete_user_expenses"] = False
            st.session_state["del_all_confirm"] = False
            st.session_state["confirm_delete_selected_key"] = False
            keys_to_clear = [k for k in list(st.session_state.keys()) if str(k).startswith("del_cb_")]
            for k in keys_to_clear:
                st.session_state[k] = False

        admin_col_left, admin_col_right = st.columns([9,1])
        with admin_col_left:
            st.subheader("⚙️ Admin Controls")
        with admin_col_right:
            st.button("🔁 Reset Admin Forms", key="reset_admin_forms_btn", help="Clear admin form inputs (does not modify DB)", on_click=reset_admin_forms)

        with st.expander("Create User"):
            cu_name = st.text_input("New username", key="create_user_username")
            cu_pass = st.text_input("New password", type="password", key="create_user_password")
            cu_role = st.selectbox("Role", ["user", "admin"], key="create_user_role")
            create_col1, create_col2 = st.columns([1,1])
            with create_col1:
                if st.button("Create User", key="create_user_btn"):
                    create_user(cu_name, cu_pass, cu_role)

        with st.expander("Reset Password"):
            users_list_reset = [d["username"] for d in users_col.find({}, {"username": 1}) if d["username"] != st.session_state["username"]]
            if users_list_reset:
                tgt_reset = st.selectbox("Select user to reset", options=users_list_reset, key="reset_user_select")
                new_pass = st.text_input("New password", type="password", key="reset_user_newpass")
                reset_col1, reset_col2 = st.columns([1,1])
                with reset_col1:
                    if st.button("Reset Password", key="reset_user_btn"):
                        if not new_pass:
                            st.error("Provide a new password.")
                        else:
                            reset_user_password(tgt_reset, new_pass)
            else:
                st.info("No other users available for reset.")

        with st.expander("Delete User"):
            users_list_del = [d["username"] for d in users_col.find({}, {"username": 1})
                              if d["username"] != st.session_state["username"]
                              and d["username"] != (st.secrets.get("admin", {}).get("username") if st.secrets else None)]
            if users_list_del:
                tgt_del = st.selectbox("Select user to delete", options=users_list_del, key="delete_user_select")
                del_confirm = st.checkbox("I confirm deletion of this user and optionally their expenses", key="delete_user_confirm")
                del_expenses_opt = st.checkbox("Also delete user's expenses", key="delete_user_expenses")
                del_col1, del_col2 = st.columns([1,1])
                with del_col1:
                    if st.button("🗑️ Delete User", key="delete_user_btn") and del_confirm:
                        delete_user(tgt_del, delete_expenses=del_expenses_opt)
            else:
                st.info("No other users to delete.")

        st.markdown("#### Danger Zone")
        del_all_confirm = st.checkbox("I confirm deleting ALL expenses (admin only)", key="del_all_confirm")
        delall_col1, delall_col2 = st.columns([1,1])
        with delall_col1:
            if st.button("🔥 Delete All Expenses", key="delete_all_btn") and del_all_confirm:
                if TRACING_AVAILABLE and tracer:
                    with tracer.start_as_current_span("admin_delete_all_expenses"):
                        result = collection.delete_many({})
                else:
                    result = collection.delete_many({})
                if result.deleted_count == 0:
                    st.info("No expense records found to delete.")
                else:
                    log_action("delete_all_expenses", st.session_state["username"], details={"deleted_count": result.deleted_count})
                    st.warning(f"⚠️ {result.deleted_count} expense(s) deleted.")

        with st.expander("View Audit Logs"):
            logs = list(audit_col.find().sort("timestamp", -1).limit(200))
            if logs:
                logs_df = pd.DataFrame(logs)
                if "_id" in logs_df.columns:
                    logs_df["_id"] = logs_df["_id"].astype(str)
                # timezone-aware timestamps -> format safely
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
        st.dataframe(df)

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
        except Exception as e:
            st.error(f"Failed to prepare download: {e}")

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

        # Admin delete selected expenses
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
                delsel_col1, delsel_col2 = st.columns([1,1])
                with delsel_col1:
                    if st.button("🗑️ Delete Selected Expenses", key="delete_selected_expenses_button_key") and confirm_sel:
                        not_found = []
                        deleted_ids = []
                        for did in selected_for_delete:
                            try:
                                result = collection.delete_one({"_id": ObjectId(did)})
                            except Exception:
                                result = collection.delete_one({"_id": did})
                            if result.deleted_count == 0:
                                not_found.append(did)
                            else:
                                deleted_ids.append(did)
                        if deleted_ids:
                            log_action("delete_selected_expenses", st.session_state["username"], details={"ids": deleted_ids})
                        if not_found and deleted_ids:
                            st.warning(f"Some IDs were not found and could not be deleted: {', '.join(not_found)}. Deleted: {', '.join(deleted_ids)}")
                        elif not_found and not deleted_ids:
                            st.info(f"No records found for selected IDs: {', '.join(not_found)}")
                        else:
                            st.success("Selected expenses deleted.")
    else:
        st.info("No expenses to show.")

if __name__ == "__main__":
    show_app()