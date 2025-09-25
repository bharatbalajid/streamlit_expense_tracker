# app.py
"""
Expense Tracker (full) with cookie-backed sessions via small JS snippets.
- MongoDB backend: users, expenses, audit_logs
- Redis-backed session tokens (persist across refresh)
- JS writes session_token cookie and removes token from URL
- Tanglish funny + money-saving tips on login page (centered)
- Admin controls: create/reset/delete user, delete expenses, view audit logs
- PDF export with reportlab (optional)
- OpenTelemetry tracing: OTLP only when ENABLE_OTLP=1 (safe fallback to console exporter)
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
# Hardcoded endpoints (kept here but OTLP disabled by default)
# --------------------------
HARDCODED_OTLP_ENDPOINT = "http://3.208.18.133:4318/v1/traces"
HARDCODED_OTEL_SERVICE_NAME = "expense-tracker"

# Use ENABLE_OTLP=1 to explicitly enable OTLP exporter.
ENABLE_OTLP = os.environ.get("ENABLE_OTLP", "0") == "1" or (st.secrets and st.secrets.get("jaeger", {}).get("enable_otlp") == "1")

# --------------------------
# Logging config
# --------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("expense-tracker")

# Quiet opentelemetry internals to avoid flood of connection errors
logging.getLogger("opentelemetry").setLevel(logging.ERROR)
logging.getLogger("opentelemetry.sdk").setLevel(logging.ERROR)
logging.getLogger("opentelemetry.exporter").setLevel(logging.ERROR)
logging.getLogger("opentelemetry.sdk._shared_internal").setLevel(logging.ERROR)

# --------------------------
# Helper: check OTLP endpoint TCP connectability
# --------------------------
def _check_otlp_tcp(endpoint_url: str, timeout: float = 2.0) -> bool:
    try:
        from urllib.parse import urlparse
        p = urlparse(endpoint_url)
        host = p.hostname
        port = p.port or (443 if p.scheme == "https" else 80)
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except Exception as e:
        logger.warning("OTLP TCP check failed: %s", e)
        return False

# --------------------------
# Tracing init (conditional + robust fallback)
# --------------------------
TRACING_AVAILABLE = False
tracer = None
_tracing_status_text = "Tracing disabled (ENABLE_OTLP not set)"

if ENABLE_OTLP:
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # Optional instrumentations
        try:
            from opentelemetry.instrumentation.pymongo import PymongoInstrumentor
        except Exception:
            PymongoInstrumentor = None
        try:
            from opentelemetry.instrumentation.redis import RedisInstrumentor
        except Exception:
            RedisInstrumentor = None

        resource = Resource.create(attributes={"service.name": HARDCODED_OTEL_SERVICE_NAME})
        tracer_provider = TracerProvider(resource=resource)

        # TCP check first — avoid immediate connection refused flooding
        if _check_otlp_tcp(HARDCODED_OTLP_ENDPOINT, timeout=2.0):
            try:
                otlp_exporter = OTLPSpanExporter(endpoint=HARDCODED_OTLP_ENDPOINT, timeout=5)
                span_processor = BatchSpanProcessor(otlp_exporter)
                tracer_provider.add_span_processor(span_processor)
                TRACING_AVAILABLE = True
                _tracing_status_text = f"Tracing: OTLP exporter active -> {HARDCODED_OTLP_ENDPOINT}"
            except Exception as e:
                logger.warning("Failed to initialize OTLP exporter: %s — falling back to ConsoleSpanExporter", e)
                console_exporter = ConsoleSpanExporter()
                tracer_provider.add_span_processor(BatchSpanProcessor(console_exporter))
                TRACING_AVAILABLE = False
                _tracing_status_text = f"Tracing: OTLP init failed; using ConsoleSpanExporter. Reason: {e}"
        else:
            # OTLP not reachable: use console exporter
            console_exporter = ConsoleSpanExporter()
            tracer_provider.add_span_processor(BatchSpanProcessor(console_exporter))
            TRACING_AVAILABLE = False
            _tracing_status_text = "Tracing: OTLP endpoint unreachable; using ConsoleSpanExporter."

        trace.set_tracer_provider(tracer_provider)
        tracer = trace.get_tracer(__name__)

        # Try to instrument pymongo & redis (best-effort)
        try:
            if PymongoInstrumentor:
                PymongoInstrumentor().instrument()
        except Exception as e:
            logger.debug("PymongoInstrumentor failed: %s", e)
        try:
            if RedisInstrumentor:
                RedisInstrumentor().instrument()
        except Exception as e:
            logger.debug("RedisInstrumentor failed: %s", e)

    except Exception as e:
        logger.exception("Tracing initialization failed; proceeding without tracing: %s", e)
        TRACING_AVAILABLE = False
        tracer = None
        _tracing_status_text = f"Tracing initialization error: {e}"

# --------------------------
# Page config
# --------------------------
st.set_page_config(page_title="💰 Expense Tracker", layout="wide")

# show tracing status on the sidebar so user knows what's happening
st.sidebar.info(_tracing_status_text)

# --------------------------
# Require redis (we need persistence across refresh)
# --------------------------
if redis is None:
    st.error("`redis` package not installed. Install it with `pip install redis` and restart the app.")
    st.stop()

# --------------------------
# Redis connection (from secrets or env)
# --------------------------
REDIS_URL = None
if st.secrets and st.secrets.get("redis", {}).get("url"):
    REDIS_URL = st.secrets.get("redis", {}).get("url")
else:
    REDIS_URL = os.environ.get("REDIS_URL")  # optional env fallback

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
# MongoDB connection
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
# Helpers
# --------------------------
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def _now_utc():
    return datetime.now(timezone.utc)

def log_action(action: str, actor: str, target: str = None, details: dict = None):
    try:
        rec = {
            "action": action,
            "actor": actor,
            "target": target,
            "details": details or {},
            "timestamp": _now_utc()
        }
        if TRACING_AVAILABLE and tracer:
            with tracer.start_as_current_span("audit_log_insert") as span:
                span.set_attribute("audit.action", action or "")
                span.set_attribute("audit.actor", actor or "")
                if target:
                    span.set_attribute("audit.target", target)
                audit_col.insert_one(rec)
        else:
            audit_col.insert_one(rec)
    except Exception as e:
        logger.debug("audit log failed: %s", e)

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
                "created_at": _now_utc()
            })
            log_action("create_superadmin", "system", target=secret_user)

ensure_superadmin()

# --------------------------
# Session defaults (admin UI keys included)
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

# --------------------------
# Tanglish headings & tips
# --------------------------
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

# helper to set query param (temporary)
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

# --------------------------
# Cookie <-> URL tiny JS helpers
# --------------------------
COOKIE_READER_HTML = """<script>(function(){const urlParams=new URLSearchParams(window.location.search); if(urlParams.has('session_token')) return; function readCookie(name){const v=document.cookie.match('(^|;)\\s*'+name+'\\s*=\\s*([^;]+)'); return v? v.pop():'';} const token=readCookie('session_token'); if(token){const newUrl=window.location.pathname+'?session_token='+encodeURIComponent(token); window.location.href=newUrl;} })();</script>"""

COOKIE_SETTER_HTML = """<script>(function(){const urlParams=new URLSearchParams(window.location.search); if(!urlParams.has('session_token')) return; const token=urlParams.get('session_token'); if(!token) return; const maxAge=60*60*4; document.cookie='session_token='+encodeURIComponent(token)+'; path=/; max-age='+maxAge+';'; const cleanUrl=window.location.protocol+'//'+window.location.host+window.location.pathname; window.history.replaceState({}, document.title, cleanUrl+window.location.hash); })();</script>"""

# --------------------------
# Authentication functions
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
        if TRACING_AVAILABLE and tracer:
            with tracer.start_as_current_span("restore_session_from_url_token") as span:
                span.set_attribute("token.present", bool(token))
                username = get_username_from_token(token)
                if username:
                    st.session_state["authenticated"] = True
                    st.session_state["username"] = username
                    u = users_col.find_one({"username": username})
                    st.session_state["is_admin"] = (u.get("role") == "admin") if u else False
                    log_action("session_restored", username)
                return
        else:
            username = get_username_from_token(token)
            if username:
                st.session_state["authenticated"] = True
                st.session_state["username"] = username
                u = users_col.find_one({"username": username})
                st.session_state["is_admin"] = (u.get("role") == "admin") if u else False
                log_action("session_restored", username)

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
    st.components.v1.html("""<script>document.cookie="session_token=; path=/; max-age=0;"; setTimeout(function(){ window.location.href=window.location.pathname; }, 200);</script>""", height=80)
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

# --------------------------
# Admin helpers (same as before)
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
        "created_at": _now_utc()
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
    if TRACING_AVAILABLE and tracer:
        span_ctx = tracer.start_as_current_span("delete_user")
    else:
        span_ctx = None
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
            if delete_expenses:
                if exp_result and exp_result.deleted_count > 0:
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
# PDF helpers (same as before)
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
            elems.append(Paragraph(f"Total expenses: ₹ {total:.2f} — Generated: {_now_utc().date()}", styles["Normal"]))
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
        elems.append(Paragraph(f"Total expenses: ₹ {total:.2f} — Generated: {_now_utc().date()}", styles["Normal"]))
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

# --------------------------
# Visible docs
# --------------------------
def get_visible_docs():
    if st.session_state.get("is_admin"):
        return list(collection.find())
    else:
        owner = st.session_state.get("username")
        return list(collection.find({"owner": owner}))

# --------------------------
# Main UI (same as your current UI)
# --------------------------
def show_app():
    # If not authenticated and no token in URL, inject cookie reader JS
    token_in_query = read_token_from_query()
    if not st.session_state.get("authenticated") and not token_in_query:
        st.components.v1.html(COOKIE_READER_HTML, height=10)

    restore_session_from_url_token()

    token_in_query = read_token_from_query()
    if token_in_query:
        st.components.v1.html(COOKIE_SETTER_HTML, height=10)

    st.title("💰 Personal Expense Tracker")

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

    # ... (rest of your authenticated UI remains the same)
    # For brevity in this response I won't repeat the entire UI again,
    # but use the exact UI code you had previously (expense form, admin controls, datatables).
    # In your actual file paste the UI code you already have below this point.
    st.write("Authenticated UI here... (paste your UI code)")

if __name__ == "__main__":
    show_app()