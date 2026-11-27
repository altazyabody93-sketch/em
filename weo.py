#!/usr/bin/env python3
# ============================================================
# HOST BOT v13.0 — ULTRA STABLE
# - نظام عزل أعطال كامل لكل مشروع (عمليات مستقلة + أقفال تزامن + حدود موارد)
# - نظام مراقبة ذكي وإعادة تشغيل تلقائية مع تدرج زمني ومنع الحلقات اللانهائية
# - نظام تشخيص أعطال ذكي (Python AST / Node.js / السجلات / المكتبات / الملفات)
# - نظام نسخ احتياطي واستعادة آمن مع التحقق من السلامة ومنع Zip Slip
# - لوحة تحكم Telegram و Web Dashboard متكاملة + إخفاء الأسرار من السجلات
# - نظام تخصيص كامل للأزرار والرسائل مع حفظ دائم واستعادة الافتراضي
# ============================================================

import os
import sys
import io
import ast
import html
import json
import time
import shutil
import signal
import socket
import sqlite3
import secrets
import hashlib
import zipfile
import tempfile
import platform
import threading
import subprocess
import urllib.request
import re
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

try:
    import resource
except Exception:
    resource = None


# ============================================================
# ضمان المكتبات الأساسية (متوافق مع virtualenv والنظام)
# ============================================================
def ensure_packages():
    pkgs = {
        "telebot": "pyTelegramBotAPI",
        "flask": "flask",
        "requests": "requests",
        "psutil": "psutil",
    }
    missing = []
    for imp, pip_name in pkgs.items():
        try:
            __import__(imp)
        except ImportError:
            missing.append(pip_name)
    if not missing:
        return
    print(f"[Setup] مكتبات ناقصة: {missing}")
    in_venv = (sys.prefix != getattr(sys, "base_prefix", sys.prefix)) or bool(os.environ.get("VIRTUAL_ENV"))
    for pip_name in missing:
        cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir"]
        if not in_venv:
            cmd.append("--user")
        cmd.append(pip_name)
        try:
            subprocess.run(cmd, timeout=300, check=False)
        except Exception as e:
            print(f"[Setup] فشل {pip_name}: {e}")
    print("[Setup] تم")


ensure_packages()

import telebot
from telebot import types
from flask import Flask, render_template_string, request, Response, jsonify

try:
    import psutil
except ImportError:
    psutil = None


# ============================================================
# الإعدادات العامة وحدود البيئة
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "9062665659:AAE2ubEi1CWrrsRM83Pp0XyKMlqlelpTCvQ")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "7325566792").split(",") if x.strip().isdigit()]
DEVELOPER_USERNAME = os.environ.get("DEVELOPER_USERNAME", "E_E_72")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

PROJECTS_DIR = os.path.join(DATA_DIR, "hosted_projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "host_v12.db")

BACKUPS_24H_DIR = os.path.join(DATA_DIR, "backups_24h")
os.makedirs(BACKUPS_24H_DIR, exist_ok=True)
SESSIONS_BACKUP_DIR = os.path.join(DATA_DIR, "session_backups")
os.makedirs(SESSIONS_BACKUP_DIR, exist_ok=True)
ADMIN_STORAGE_DIR = os.path.join(DATA_DIR, "admin_storage")
os.makedirs(ADMIN_STORAGE_DIR, exist_ok=True)

# حدود الموارد والأمان (قابلة للتعديل عبر متغيرات البيئة)
def _env_int(name, default):
    try:
        return int(float(os.environ.get(name, default)))
    except Exception:
        return default

MAX_RAM_MB = _env_int("PROJECT_MAX_RAM_MB", 512)
MAX_CPU_PCT = _env_int("PROJECT_MAX_CPU_PCT", 95)
CPU_STRIKES = _env_int("PROJECT_CPU_STRIKES", 6)
MAX_FILE_MB = _env_int("PROJECT_MAX_FILE_MB", 200)
STRICT_AS_MB = _env_int("PROJECT_STRICT_AS_MB", 0)
HOST_RAM_DANGER = _env_int("HOST_RAM_DANGER_PCT", 93)
MAX_PROJECT_SIZE_MB = _env_int("PROJECT_MAX_DISK_MB", 250)
DEFAULT_MAX_AUTO_RESTARTS = _env_int("MAX_AUTO_RESTARTS", 3)
CRASH_WINDOW_SEC = _env_int("CRASH_WINDOW_SEC", 600)
START_WATCH_SECONDS = _env_int("START_WATCH_SECONDS", 15)
BACKUP_HOURS = float(os.environ.get("BACKUP_HOURS", "2"))
BACKUP_RETENTION_COUNT = _env_int("BACKUP_RETENTION_COUNT", 5)

VENV_DIRNAME = ".venv"
PYLIBS_DIRNAME = ".pylibs"
TG_UPLOAD_LIMIT = 49 * 1024 * 1024
TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024
MAX_FILE_SIZE = 20 * 1024 * 1024
PER_PAGE = 8

RUNNING = {}
RUNNING_LOCK = threading.RLock()
PROJECT_LOCKS = {}
PROJECT_LOCKS_GUARD = threading.Lock()
USER_STATES_LOCK = threading.RLock()
user_states = {}
START_TIME = datetime.now()
PORT_COUNTER = 7000
PORT_LOCK = threading.Lock()
TUNNEL_URL = None
DB_LOCK = threading.RLock()

_START_PROGRESS = {}
_MANUAL_STOP = set()
_CRASH_HISTORY = {}
_RESTART_TIMERS = {}
_CPU_LAST = {}
_CPU_STRIKES = {}


def get_project_lock(pid):
    with PROJECT_LOCKS_GUARD:
        if pid not in PROJECT_LOCKS:
            PROJECT_LOCKS[pid] = threading.RLock()
        return PROJECT_LOCKS[pid]


PLANS = {
    "free": {"name": "المجانية (Free)", "emoji": "🆓", "max_projects": 3, "price": 0.0, "vip": 0},
    "pro":  {"name": "الاحترافية (Pro)", "emoji": "⚡", "max_projects": 8, "price": 5.0, "vip": 0},
    "vip":  {"name": "الفاخرة (VIP)", "emoji": "👑", "max_projects": 25, "price": 12.0, "vip": 1},
}

NODE_BUILTINS = {
    "fs", "path", "os", "http", "https", "url", "util", "crypto", "stream",
    "events", "child_process", "cluster", "dns", "net", "tls", "dgram",
    "readline", "zlib", "buffer", "assert", "querystring", "timers", "tty",
    "process", "worker_threads", "perf_hooks", "async_hooks", "vm",
}

PTYPE_LABELS = {"bot": "🐍 بوت", "web": "🌐 موقع", "script": "📜 سكربت", "node": "🟨 Node.js"}

ALLOWED_EXTS = [
    ".py", ".txt", ".json", ".env", ".sh", ".html", ".css",
    ".js", ".mjs", ".cjs", ".yml", ".yaml", ".zip", ".xml", ".md", ".toml",
    ".ini", ".cfg", ".csv",
]
ALLOWED_FILENAMES = [
    "Procfile", "Makefile", "Dockerfile", "LICENSE",
    "README", ".gitignore", ".env",
]


# ============================================================
# 🔒 الأمان وإخفاء الأسرار وفحص المسارات
# ============================================================
_SECRET_PATTERNS = [
    (re.compile(r'\b([0-9]{8,12}:[A-Za-z0-9_-]{30,50})\b'), r'[REDACTED_TOKEN]'),
    (re.compile(r'(?i)(api[_-]?key|secret|password|token|auth[_-]?token|private[_-]?key)\s*[:=]\s*["\']?([^\s"\'\n]{6,})["\']?'), r'\1=[REDACTED]'),
    (re.compile(r'\b(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z\-_]{30,})\b'), r'[REDACTED_KEY]'),
]


def redact_secrets(text):
    if not text or not isinstance(text, str):
        return text or ""
    out = text
    for rx, rep in _SECRET_PATTERNS:
        out = rx.sub(rep, out)
    return out


def safe_join_in_dir(base_dir, rel_path):
    """يتأكد أن المسار النهائي لا يخرج أبداً عن المجلد المسموح (منع Path Traversal)."""
    if not base_dir or not rel_path:
        return None
    rel_clean = str(rel_path).replace("\\", "/").lstrip("/")
    if not rel_clean or ".." in rel_clean.split("/"):
        return None
    base_real = os.path.realpath(base_dir)
    target_real = os.path.realpath(os.path.join(base_real, rel_clean))
    if target_real == base_real or target_real.startswith(base_real + os.sep):
        return target_real
    return None


def safe_project_name(raw_name):
    cleaned = re.sub(r"[^A-Za-z0-9_\u0600-\u06FF.-]", "_", (raw_name or "").strip())
    cleaned = cleaned.strip("._")[:30]
    return cleaned


def safe_extract_zip(zip_path, dest_dir, max_total_bytes=100 * 1024 * 1024):
    """فك ضغط آمن يمنع هجمات Zip Slip و Zip Bomb."""
    dest_real = os.path.realpath(dest_dir)
    total_size = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise ValueError(f"مسار غير آمن داخل الأرشيف: {info.filename}")
            # منع الروابط الرمزية (Symlinks) داخل الأرشيف
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"روابط رمزية غير مسموحة في الأرشيف: {info.filename}")
            total_size += max(0, info.file_size)
            if total_size > max_total_bytes:
                raise ValueError("حجم الملفات بعد فك الضغط يتجاوز الحد المسموح")
            target = os.path.realpath(os.path.join(dest_real, name))
            if target != dest_real and not target.startswith(dest_real + os.sep):
                raise ValueError(f"محاولة اختراق مسار: {info.filename}")
        for info in zf.infolist():
            if info.is_dir():
                target = safe_join_in_dir(dest_real, info.filename)
                if target:
                    os.makedirs(target, exist_ok=True)
            else:
                target = safe_join_in_dir(dest_real, info.filename)
                if not target:
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info, "r") as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)


def get_dir_size_bytes(path):
    total = 0
    if not path or not os.path.isdir(path):
        return 0
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except Exception:
                pass
    return total


# ============================================================
# قاعدة البيانات (مع WAL + المعاملات الآمنة + التوافق الكامل مع v12)
# ============================================================
@contextmanager
def db_conn():
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 30000")
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db():
    with db_conn() as conn:
        c = conn.cursor()
        try:
            c.execute("PRAGMA journal_mode = WAL")
            c.execute("PRAGMA synchronous = NORMAL")
        except Exception:
            pass
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            created_at TEXT, is_banned INTEGER DEFAULT 0, is_premium INTEGER DEFAULT 0,
            max_projects INTEGER DEFAULT 3, total_uploads INTEGER DEFAULT 0,
            theme_color TEXT DEFAULT 'blue', balance REAL DEFAULT 0.0,
            referred_by INTEGER DEFAULT 0, referral_count INTEGER DEFAULT 0,
            plan_expires TEXT DEFAULT '')''')
        for _col_sql in [
            "ALTER TABLE users ADD COLUMN balance REAL DEFAULT 0.0",
            "ALTER TABLE users ADD COLUMN referred_by INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN referral_count INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN plan_expires TEXT DEFAULT ''",
        ]:
            try:
                c.execute(_col_sql)
            except sqlite3.OperationalError:
                pass

        c.execute('''CREATE TABLE IF NOT EXISTS recharge_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            amount REAL NOT NULL, proof TEXT DEFAULT '',
            status TEXT DEFAULT 'pending', created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY, amount REAL NOT NULL,
            max_uses INTEGER DEFAULT 1, used_count INTEGER DEFAULT 0,
            created_at TEXT)''')
        c.execute("CREATE TABLE IF NOT EXISTS btn_custom (bkey TEXT PRIMARY KEY, label TEXT, style TEXT, icon TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS tpl_custom (tid TEXT PRIMARY KEY, text TEXT)")
        c.execute('''CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL,
            name TEXT NOT NULL, ptype TEXT DEFAULT 'bot', path TEXT, entry TEXT,
            files TEXT DEFAULT '[]', requirements TEXT DEFAULT '',
            bot_token TEXT DEFAULT '', bot_username TEXT DEFAULT '', bot_name TEXT DEFAULT '',
            port INTEGER DEFAULT 0, public_url TEXT DEFAULT '', installed INTEGER DEFAULT 0,
            pid INTEGER DEFAULT 0, status TEXT DEFAULT 'stopped', created_at TEXT,
            last_start TEXT DEFAULT '', last_stop TEXT DEFAULT '',
            approved INTEGER DEFAULT 1,
            auto_restart INTEGER DEFAULT 1, max_restarts INTEGER DEFAULT 3,
            restart_count INTEGER DEFAULT 0, last_error TEXT DEFAULT '',
            stop_reason TEXT DEFAULT '', last_attempt TEXT DEFAULT '',
            install_status TEXT DEFAULT 'none', last_backup TEXT DEFAULT '',
            FOREIGN KEY (owner_id) REFERENCES users(user_id) ON DELETE CASCADE)''')

        # ترقية الجداول القديمة بدون فقدان البيانات
        for _pcol_sql in [
            "ALTER TABLE projects ADD COLUMN approved INTEGER DEFAULT 1",
            "ALTER TABLE projects ADD COLUMN auto_restart INTEGER DEFAULT 1",
            "ALTER TABLE projects ADD COLUMN max_restarts INTEGER DEFAULT 3",
            "ALTER TABLE projects ADD COLUMN restart_count INTEGER DEFAULT 0",
            "ALTER TABLE projects ADD COLUMN last_error TEXT DEFAULT ''",
            "ALTER TABLE projects ADD COLUMN stop_reason TEXT DEFAULT ''",
            "ALTER TABLE projects ADD COLUMN last_attempt TEXT DEFAULT ''",
            "ALTER TABLE projects ADD COLUMN install_status TEXT DEFAULT 'none'",
            "ALTER TABLE projects ADD COLUMN last_backup TEXT DEFAULT ''",
        ]:
            try:
                c.execute(_pcol_sql)
            except sqlite3.OperationalError:
                pass

        c.execute('''CREATE TABLE IF NOT EXISTS trusted_users (
            user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '',
            first_name TEXT DEFAULT '', added_by INTEGER DEFAULT 0, created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS ui_settings (key TEXT PRIMARY KEY, value TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS action_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT,
            details TEXT, timestamp TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS dashboard_tokens (
            token TEXT PRIMARY KEY, user_id INTEGER, created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS backup_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, btype TEXT, filename TEXT,
            size_bytes INTEGER DEFAULT 0, project_count INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0, verified INTEGER DEFAULT 0,
            created_at TEXT)''')


init_db()


# ============================================================
# إدارة المستخدمين
# ============================================================
def get_user(uid):
    with db_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()


def add_user(uid, username="", first_name=""):
    with db_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO users (user_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
                (uid, username, first_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_all_users():
    with db_conn() as conn:
        return [x[0] for x in conn.execute("SELECT user_id FROM users WHERE is_banned=0").fetchall()]


def get_user_count():
    with db_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_all_users_full():
    with db_conn() as conn:
        return conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()


def ban_user(uid):
    with db_conn() as conn:
        conn.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,))


def unban_user(uid):
    with db_conn() as conn:
        conn.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (uid,))


def set_user_theme(uid, color):
    with db_conn() as conn:
        conn.execute("UPDATE users SET theme_color=? WHERE user_id=?", (color, uid))


def is_banned(uid):
    u = get_user(uid)
    return bool(u and u[4] == 1)


def is_premium(uid):
    u = get_user(uid)
    return bool(u and u[5] == 1)


def is_paid_mode():
    return get_ui("bot_mode") == "paid"


def get_max_projects(uid):
    u = get_user(uid)
    if not u:
        return int(float(get_ui("free_trial_projects") or 1)) if is_paid_mode() else 3
    if is_paid_mode():
        return u[6] if u[6] is not None else int(float(get_ui("free_trial_projects") or 1))
    return u[6] or 3


def get_balance(uid):
    u = get_user(uid)
    if not u or len(u) < 10 or u[9] is None:
        return 0.0
    return float(u[9])


def add_balance(uid, amount):
    with db_conn() as conn:
        conn.execute("UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE user_id=?", (float(amount), uid))


def set_max_projects(uid, val):
    with db_conn() as conn:
        conn.execute("UPDATE users SET max_projects = ? WHERE user_id=?", (int(val), uid))


def add_max_projects(uid, delta):
    with db_conn() as conn:
        conn.execute("UPDATE users SET max_projects = COALESCE(max_projects, 0) + ? WHERE user_id=?", (int(delta), uid))


def register_referral(new_uid, ref_uid):
    if not ref_uid or ref_uid == new_uid or not get_user(ref_uid):
        return False
    bonus = float(get_ui("referral_bonus") or 0.5)
    with db_conn() as conn:
        r = conn.execute("SELECT referred_by FROM users WHERE user_id=?", (new_uid,)).fetchone()
        if r and r[0]:
            return False
        conn.execute("UPDATE users SET referred_by=? WHERE user_id=?", (ref_uid, new_uid))
        conn.execute(
            "UPDATE users SET referral_count = COALESCE(referral_count, 0) + 1, balance = COALESCE(balance, 0) + ? WHERE user_id=?",
            (bonus, ref_uid),
        )
    return bonus


def create_recharge_request(uid, amount, proof):
    with db_conn() as conn:
        c = conn.execute(
            "INSERT INTO recharge_requests (user_id, amount, proof, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (uid, float(amount), proof, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        return c.lastrowid


def get_recharge_request(rid):
    with db_conn() as conn:
        return conn.execute("SELECT * FROM recharge_requests WHERE id=?", (rid,)).fetchone()


def get_pending_recharges():
    with db_conn() as conn:
        return conn.execute("SELECT * FROM recharge_requests WHERE status='pending' ORDER BY id DESC LIMIT 20").fetchall()


def resolve_recharge_request(rid, status):
    with db_conn() as conn:
        c = conn.execute("UPDATE recharge_requests SET status=? WHERE id=? AND status='pending'", (status, rid))
        return c.rowcount > 0


def create_gift_code(code, amount, max_uses=1):
    with db_conn() as conn:
        conn.execute(
            "REPLACE INTO gift_codes (code, amount, max_uses, used_count, created_at) VALUES (?, ?, ?, 0, ?)",
            (code.strip().upper(), float(amount), int(max_uses), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )


def redeem_gift_code(uid, code):
    code = code.strip().upper()
    with db_conn() as conn:
        r = conn.execute("SELECT amount, max_uses, used_count FROM gift_codes WHERE code=?", (code,)).fetchone()
        if not r:
            return False, "❌ كود الشحن غير صحيح."
        amt, mx, used = r
        if used >= mx:
            return False, "⚠️ تم استهلاك هذا الكود بالكامل."
        conn.execute("UPDATE gift_codes SET used_count = used_count + 1 WHERE code=?", (code,))
        conn.execute("UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE user_id=?", (float(amt), uid))
    return True, amt


# ============================================================
# إدارة المشاريع (مع حماية الأعمدة واستعلامات آمنة)
# ============================================================
_PROJECT_UPDATABLE_COLS = {
    "name", "ptype", "path", "entry", "files", "requirements",
    "bot_token", "bot_username", "bot_name", "port", "public_url",
    "installed", "pid", "status", "last_start", "last_stop", "approved",
    "auto_restart", "max_restarts", "restart_count", "last_error",
    "stop_reason", "last_attempt", "install_status", "last_backup",
}


def get_projects_by_owner(owner_id):
    with db_conn() as conn:
        return conn.execute("SELECT * FROM projects WHERE owner_id=? ORDER BY id DESC", (owner_id,)).fetchall()


def get_project(pid):
    with db_conn() as conn:
        return conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()


def get_all_projects():
    with db_conn() as conn:
        return conn.execute("SELECT * FROM projects ORDER BY id DESC").fetchall()


def add_project(owner_id, name, ptype, path, entry):
    with db_conn() as conn:
        c = conn.execute(
            """INSERT INTO projects (owner_id, name, ptype, path, entry, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (owner_id, name, ptype, path, entry, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        return c.lastrowid


def update_project(project_id, **kw):
    if not kw:
        return
    safe_items = [(k, v) for k, v in kw.items() if k in _PROJECT_UPDATABLE_COLS]
    if not safe_items:
        return
    with db_conn() as conn:
        for k, v in safe_items:
            conn.execute(f"UPDATE projects SET {k}=? WHERE id=?", (v, project_id))


def delete_project(pid):
    with db_conn() as conn:
        conn.execute("DELETE FROM projects WHERE id=?", (pid,))


def count_user_projects(uid):
    with db_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM projects WHERE owner_id=?", (uid,)).fetchone()[0]


def proj_meta(p):
    """ترجع قاموساً واضحاً لحقول المشروع مع التوافق التام مع فهارس SQLite."""
    if not p:
        return {}
    def _g(idx, default=""):
        return p[idx] if len(p) > idx and p[idx] is not None else default
    return {
        "id": _g(0, 0),
        "owner_id": _g(1, 0),
        "name": _g(2, ""),
        "ptype": _g(3, "bot"),
        "path": _g(4, ""),
        "entry": _g(5, ""),
        "files": _g(6, "[]"),
        "requirements": _g(7, ""),
        "bot_token": _g(8, ""),
        "bot_username": _g(9, ""),
        "bot_name": _g(10, ""),
        "port": _g(11, 0),
        "public_url": _g(12, ""),
        "installed": _g(13, 0),
        "pid": _g(14, 0),
        "status": _g(15, "stopped"),
        "created_at": _g(16, ""),
        "last_start": _g(17, ""),
        "last_stop": _g(18, ""),
        "approved": _g(19, 1),
        "auto_restart": _g(20, 1),
        "max_restarts": _g(21, DEFAULT_MAX_AUTO_RESTARTS),
        "restart_count": _g(22, 0),
        "last_error": _g(23, ""),
        "stop_reason": _g(24, ""),
        "last_attempt": _g(25, ""),
        "install_status": _g(26, "none"),
        "last_backup": _g(27, ""),
    }


def is_trusted_user(uid):
    if is_admin(uid):
        return True
    with db_conn() as conn:
        return bool(conn.execute("SELECT 1 FROM trusted_users WHERE user_id=?", (uid,)).fetchone())


def add_trusted_user(uid, username="", first_name="", added_by=0):
    with db_conn() as conn:
        conn.execute(
            "REPLACE INTO trusted_users (user_id, username, first_name, added_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (int(uid), username or "", first_name or "", int(added_by), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


def remove_trusted_user(uid):
    with db_conn() as conn:
        c = conn.execute("DELETE FROM trusted_users WHERE user_id=?", (int(uid),))
        return c.rowcount > 0


def get_all_trusted_users():
    with db_conn() as conn:
        return conn.execute("SELECT user_id, username, first_name, added_by, created_at FROM trusted_users ORDER BY created_at DESC").fetchall()


def find_user_by_username(uname):
    uname = (uname or "").strip().lstrip("@").lower()
    if not uname:
        return None
    with db_conn() as conn:
        return conn.execute("SELECT user_id, username, first_name FROM users WHERE LOWER(username)=?", (uname,)).fetchone()


def get_project_approved(pid):
    with db_conn() as conn:
        r = conn.execute("SELECT COALESCE(approved, 1) FROM projects WHERE id=?", (pid,)).fetchone()
        return int(r[0]) if r else 0


def set_project_approved(pid, val):
    with db_conn() as conn:
        conn.execute("UPDATE projects SET approved=? WHERE id=?", (int(val), pid))


# ============================================================
# إعدادات الواجهة + الأزرار + القوالب القابلة للتخصيص
# ============================================================
DEFAULT_UI = {
    "welcome_text": "أهلاً بك في Host Bot v13 — ULTRA STABLE",
    "dashboard_title": "HOST BOT v13 — ULTRA STABLE",
    "footer_text": "━━━━━━━━━━━━━━",
    "divider_line": "━━━━━━━━━━━━━━",
    "theme_color": "#2563eb",
    "bot_mode": "free",
    "currency_name": "$",
    "slot_price": "2.0",
    "vip_price": "5.0",
    "referral_bonus": "0.5",
    "free_trial_projects": "1",
    "recharge_info": "لشحن رصيدك، قم بتحويل المبلغ المطلوب ثم أرسل رقم الحوالة أو إثبات الدفع ليتم التفعيل فوراً.",
    "smart_install_intro": "🪄 <b>التثبيت التلقائي الذكي</b>\n\nسأفحص كود مشروعك واكتشف كل المكتبات التي يحتاجها تلقائياً، وأثبّتها في بيئة معزولة خاصة بهذا المشروع فقط.",
    "storage_intro": "📥 <b>تخزين المطوّر</b>\n\nهنا نسخ احتياطية من كل مشاريع المستخدمين، محفوظة عندك حتى لو حذفوها بأنفسهم.",
    "maintenance_text": "🛠 <b>الاستضافة متوقفة مؤقتاً للصيانة</b>\n\nراجعنا بعد قليل 🙏",
    # نصوص قابلة للتخصيص الكامل في v13
    "msg_start_success": "🎉 تم تشغيل المشروع بنجاح وهو يعمل الآن باستقرار.",
    "msg_stop_success": "⏹️ تم إيقاف المشروع وتنظيف عملياته بأمان.",
    "msg_restart_success": "🔁 تم إعادة تشغيل المشروع بنجاح.",
    "msg_error_generic": "❌ تعذّر تنفيذ العملية، راجع السجلات أو أداة التشخيص.",
    "msg_backup_done": "💾 تم إنشاء النسخة الاحتياطية والتحقق من سلامتها بنجاح.",
    "msg_restore_done": "♻️ تمت استعادة النسخة الاحتياطية والتحقق من سلامة الملفات.",
    "msg_help_extra": "للمساعدة الإضافية أو الاستفسار تواصل مع المطور مباشرة.",
    "msg_support_prompt": "📤 اكتب رسالتك الآن وسيتم إرسالها للمطور مباشرة.",
    "msg_project_status_header": "📊 تفاصيل وحالة المشروع المباشرة",
    "msg_alert_crash": "⚠️ تنبيه: توقف المشروع بشكل غير متوقع.",
    "msg_alert_recover": "✅ تم استعادة المشروع وإعادة تشغيله تلقائياً.",
}

UI_TEMPLATES = {
    "welcome_text": "رسالة الترحيب في /start",
    "dashboard_title": "عنوان لوحة التحكم (الويب)",
    "msg_project_status_header": "عنوان رسالة حالة المشروع",
    "msg_start_success": "رسالة نجاح التشغيل",
    "msg_stop_success": "رسالة نجاح الإيقاف",
    "msg_restart_success": "رسالة نجاح إعادة التشغيل",
    "msg_error_generic": "رسالة الأخطاء العامة",
    "msg_backup_done": "رسالة نجاح النسخ الاحتياطي",
    "msg_restore_done": "رسالة نجاح الاستعادة",
    "msg_help_extra": "نص المساعدة الإضافي",
    "msg_support_prompt": "رسالة التواصل مع الدعم",
    "msg_alert_crash": "رسالة تنبيه التوقف المفاجئ",
    "msg_alert_recover": "رسالة تنبيه استعادة المشروع",
    "smart_install_intro": "مقدمة شاشة التثبيت الذكي",
    "storage_intro": "مقدمة شاشة تخزين المطوّر",
    "divider_line": "📏 خط الفاصل الزخرفي",
    "maintenance_text": "📝 نص شاشة الصيانة",
}

DEFAULT_BUTTONS = {
    "btn_dashboard":    ("🌐 لوحة التحكم", "primary", 0),
    "btn_my_projects":  ("📦 مشاريعي", "primary", 1),
    "btn_new_project":  ("➕ مشروع جديد", "success", 1),
    "btn_account":      ("👤 حسابي", "primary", 2),
    "btn_help":         ("❓ مساعدة", "primary", 2),
    "btn_wallet":       ("💳 محفظتي والشحن", "success", 3),
    "btn_support":      ("💬 تواصل مع الدعم", "primary", 3),
    "btn_server":       ("📊 حالة السيرفر", "primary", 4),
    "btn_admin":        ("👑 لوحة المطور", "danger", 5),
}


def get_ui(key):
    with db_conn() as conn:
        r = conn.execute("SELECT value FROM ui_settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else DEFAULT_UI.get(key, "")


def set_ui(key, value):
    with db_conn() as conn:
        conn.execute("REPLACE INTO ui_settings (key, value) VALUES (?, ?)", (key, value))


def reset_ui_key(key):
    with db_conn() as conn:
        conn.execute("DELETE FROM ui_settings WHERE key=?", (key,))


def reset_all_ui_defaults():
    with db_conn() as conn:
        for k in list(UI_TEMPLATES.keys()) + ["divider_line", "maintenance_text"]:
            conn.execute("DELETE FROM ui_settings WHERE key=?", (k,))
        for k in DEFAULT_BUTTONS.keys():
            conn.execute("DELETE FROM ui_settings WHERE key=?", (f"cfg_{k}",))
        conn.execute("DELETE FROM btn_custom")
        conn.execute("DELETE FROM tpl_custom")
    _load_btn_cache()
    _load_tpl_cache()


def get_all_ui():
    result = dict(DEFAULT_UI)
    with db_conn() as conn:
        for k, v in conn.execute("SELECT key, value FROM ui_settings").fetchall():
            result[k] = v
    return result


def UT(key):
    return get_ui(key)


def get_button(key):
    try:
        with db_conn() as conn:
            r = conn.execute("SELECT value FROM ui_settings WHERE key=?", (f"cfg_{key}",)).fetchone()
            if r and r[0]:
                parts = r[0].split("|")
                if len(parts) == 3:
                    return (parts[0], parts[1], int(parts[2]))
    except Exception:
        pass
    return DEFAULT_BUTTONS.get(key, (key, "primary", 0))


def set_button(key, name, style, row):
    set_ui(f"cfg_{key}", f"{name}|{style}|{row}")


def get_all_buttons():
    return {k: get_button(k) for k in DEFAULT_BUTTONS.keys()}


def log_action(user_id, action, details=""):
    try:
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO action_logs (user_id, action, details, timestamp) VALUES (?, ?, ?, ?)",
                (user_id, action, redact_secrets(str(details))[:400], datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
    except Exception:
        pass


# ============================================================
# أدوات مساعدة والشبكة والمقاييس
# ============================================================
def type_label(ptype):
    return PTYPE_LABELS.get(ptype, "🌐 موقع")


def get_file_size_str(fp):
    try:
        size = os.path.getsize(fp)
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
    except Exception:
        return "?"


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_public_url():
    global TUNNEL_URL
    if TUNNEL_URL:
        return TUNNEL_URL
    platforms = [
        ("RENDER_EXTERNAL_URL", "{}"), ("RAILWAY_PUBLIC_DOMAIN", "https://{}"),
        ("KOYEB_PUBLIC_DOMAIN", "https://{}"), ("FLY_APP_NAME", "https://{}.fly.dev"),
        ("HEROKU_APP_NAME", "https://{}.herokuapp.com"), ("VERCEL_URL", "https://{}"),
        ("NETLIFY_URL", "https://{}"), ("RENDER_EXTERNAL_HOSTNAME", "https://{}"),
        ("CYCLIC_URL", "https://{}"), ("GLITCH_PROJECT_DOMAIN", "https://{}"),
        ("REPLIT_DEV_DOMAIN", "https://{}"), ("NORTHFLANK_URL", "https://{}"),
        ("ADAPTABLE_URL", "https://{}"), ("FL0_DOMAIN", "https://{}"),
        ("PUBLIC_URL", "{}"), ("APP_URL", "{}"), ("SERVER_DOMAIN", "https://{}"),
        ("SPACE_HOST", "https://{}"),
        ("CODESPACE_NAME", "https://{}.github.dev"),
        ("CLOUDFLARE_TUNNEL_URL", "{}"),
    ]
    for env_key, fmt in platforms:
        v = os.environ.get(env_key)
        if v:
            TUNNEL_URL = fmt.format(v.rstrip("/"))
            return TUNNEL_URL
    try:
        r_val = get_ui("public_url")
        if r_val:
            TUNNEL_URL = r_val
            return r_val
    except Exception:
        pass
    port = int(os.environ.get("PORT", 8080))
    return f"http://{get_local_ip()}:{port}"


def calc_uptime(start_str):
    if not start_str:
        return "—"
    try:
        s = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
        t = max(0, int((datetime.now() - s).total_seconds()))
        h, m, sec = t // 3600, (t % 3600) // 60, t % 60
        return f"{h:02d}:{m:02d}:{sec:02d}"
    except Exception:
        return "—"


def get_free_port():
    global PORT_COUNTER
    with PORT_LOCK:
        for _ in range(5000):
            if PORT_COUNTER >= 65000:
                PORT_COUNTER = 7000
            cand = PORT_COUNTER
            PORT_COUNTER += 1
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(("0.0.0.0", cand))
                return cand
            except Exception:
                pass
            finally:
                try:
                    s.close()
                except Exception:
                    pass
    return 7500


def list_dir_files(dirpath):
    result = []
    if not dirpath or not os.path.exists(dirpath):
        return result
    for root, dirs, files in os.walk(dirpath):
        dirs[:] = [d for d in dirs if d not in (VENV_DIRNAME, PYLIBS_DIRNAME, "__pycache__", ".git", "node_modules")]
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, dirpath)
            result.append(rel)
    return result


def get_server_metrics():
    if not psutil:
        return {"cpu": 0.0, "ram_pct": 0.0, "ram_used": 0.0, "ram_total": 0.0, "disk_pct": 0.0, "disk_used": 0.0, "disk_total": 0.0}
    try:
        vm = psutil.virtual_memory()
        dk = psutil.disk_usage("/")
        return {
            "cpu": round(psutil.cpu_percent(interval=0.1), 1),
            "ram_pct": round(vm.percent, 1),
            "ram_used": round(vm.used / (1024 ** 3), 2),
            "ram_total": round(vm.total / (1024 ** 3), 2),
            "disk_pct": round(dk.percent, 1),
            "disk_used": round(dk.used / (1024 ** 3), 2),
            "disk_total": round(dk.total / (1024 ** 3), 2),
        }
    except Exception:
        return {"cpu": 0.0, "ram_pct": 0.0, "ram_used": 0.0, "ram_total": 0.0, "disk_pct": 0.0, "disk_used": 0.0, "disk_total": 0.0}


def get_proc_metrics(pid_num):
    if not psutil or not pid_num:
        return 0.0, 0.0
    try:
        ps = psutil.Process(int(pid_num))
        if not ps.is_running() or ps.status() == psutil.STATUS_ZOMBIE:
            return 0.0, 0.0
        cpu = ps.cpu_percent(interval=0.05)
        mem = ps.memory_info().rss / (1024 * 1024)
        for ch in ps.children(recursive=True):
            try:
                cpu += ch.cpu_percent(interval=0.02)
                mem += ch.memory_info().rss / (1024 * 1024)
            except Exception:
                pass
        return round(cpu, 1), round(mem, 1)
    except Exception:
        return 0.0, 0.0


def load_project_env_dict(pdir):
    env_vars = {}
    ep = os.path.join(pdir, ".env")
    if os.path.isfile(ep):
        try:
            with open(ep, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#") or "=" not in s:
                        continue
                    k, v = s.split("=", 1)
                    k = k.strip()
                    if k:
                        env_vars[k] = v.strip().strip('"').strip("'")
        except Exception:
            pass
    return env_vars


def detect_entry_and_type(pdir, preferred_ptype=None):
    all_files = list_dir_files(pdir)
    clean_files = [f for f in all_files if not f.startswith(("__MACOSX", ".git", "node_modules", ".venv"))]
    py_files = [f for f in clean_files if f.endswith(".py")]
    js_files = [f for f in clean_files if f.endswith((".js", ".mjs", ".cjs"))]

    pkg_path = os.path.join(pdir, "package.json")
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path, "r", encoding="utf-8", errors="ignore") as f:
                pkg = json.load(f)
            main_f = pkg.get("main", "")
            if main_f and safe_join_in_dir(pdir, main_f) and os.path.isfile(os.path.join(pdir, main_f)):
                return "node", main_f
            sc = (pkg.get("scripts") or {}).get("start", "")
            m = re.search(r"node\s+([^\s&|;]+)", sc)
            if m and safe_join_in_dir(pdir, m.group(1)) and os.path.isfile(os.path.join(pdir, m.group(1))):
                return "node", m.group(1)
        except Exception:
            pass

    if preferred_ptype == "node" or (not py_files and js_files):
        for cand in ("index.js", "bot.js", "app.js", "server.js", "main.js", "index.mjs"):
            if cand in js_files:
                return "node", cand
        if js_files:
            return "node", js_files[0]

    for cand in ("main.py", "bot.py", "app.py", "run.py", "server.py", "index.py"):
        if cand in py_files:
            return (preferred_ptype if preferred_ptype in ("bot", "web", "script") else "bot"), cand
    if py_files:
        return (preferred_ptype if preferred_ptype in ("bot", "web", "script") else "bot"), py_files[0]

    if js_files:
        return "node", js_files[0]
    return None, None


def ensure_node_package_json(pdir, entry_file):
    pkg_path = os.path.join(pdir, "package.json")
    if os.path.isfile(pkg_path):
        return False
    deps = {}
    rx = re.compile(r"""(?:require\s*\(\s*['"]([^'"./][^'"]*)['"]\s*\)|import\s+.*?\s+from\s+['"]([^'"./][^'"]*)['"])""")
    for root, dirs, files in os.walk(pdir):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git")]
        for fn in files:
            if fn.endswith((".js", ".mjs", ".cjs")):
                try:
                    with open(os.path.join(root, fn), "r", encoding="utf-8", errors="ignore") as f:
                        txt = f.read()
                    for m in rx.finditer(txt):
                        pkg = (m.group(1) or m.group(2) or "").strip()
                        if pkg.startswith("node:"):
                            continue
                        pkg = "/".join(pkg.split("/")[:2]) if pkg.startswith("@") else pkg.split("/")[0]
                        if pkg and pkg not in NODE_BUILTINS:
                            deps[pkg] = "latest"
                except Exception:
                    pass
    data = {
        "name": re.sub(r"[^a-z0-9-]", "-", os.path.basename(pdir).lower()) or "node-project",
        "version": "1.0.0",
        "main": entry_file,
        "scripts": {"start": f"node {entry_file}"},
        "dependencies": deps,
    }
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return True


def extract_bot_info_project(pdir):
    rx = re.compile(r'\b([0-9]{8,12}:[A-Za-z0-9_-]{30,50})\b')
    found = None
    for root, dirs, files in os.walk(pdir):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".venv", ".pylibs", ".git", "__pycache__")]
        for fn in files:
            if not (fn.endswith((".py", ".js", ".mjs", ".cjs", ".json", ".txt")) or fn == ".env"):
                continue
            fp = os.path.join(root, fn)
            try:
                if os.path.getsize(fp) > 2 * 1024 * 1024:
                    continue
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    m = rx.search(f.read())
                if m:
                    found = m.group(1)
                    break
            except Exception:
                continue
        if found:
            break
    info = {"token": None, "username": None, "name": None}
    if not found:
        return info
    info["token"] = found
    try:
        import requests as req
        r = req.get(f"https://api.telegram.org/bot{found}/getMe", timeout=6)
        if r.status_code == 200 and r.json().get("ok"):
            res = r.json()["result"]
            info["username"] = res.get("username")
            info["name"] = res.get("first_name")
    except Exception:
        pass
    return info


def extract_bot_info(fp):
    info = {"token": None, "username": None, "name": None}
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        patterns = [
            r'BOT_TOKEN\s*=\s*["\']([0-9]{8,12}:[A-Za-z0-9_-]{30,50})["\']',
            r'BOT_TOKEN\s*:\s*["\']([0-9]{8,12}:[A-Za-z0-9_-]{30,50})["\']',
            r'["\']BOT_TOKEN["\']\s*:\s*["\']([0-9]{8,12}:[A-Za-z0-9_-]{30,50})["\']',
            r'bot_token\s*=\s*["\']([0-9]{8,12}:[A-Za-z0-9_-]{30,50})["\']',
        ]
        for p in patterns:
            m = re.search(p, content)
            if m:
                info["token"] = m.group(1)
                break
        if not info["token"]:
            return info
        import requests as req
        r = req.get(f"https://api.telegram.org/bot{info['token']}/getMe", timeout=8)
        if r.status_code == 200 and r.json().get("ok"):
            res = r.json()["result"]
            info["username"] = res.get("username")
            info["name"] = res.get("first_name")
        else:
            info["token"] = None
    except Exception:
        pass
    return info


def get_project_files(pid):
    p = get_project(pid)
    if not p:
        return []
    try:
        return json.loads(p[6] or "[]")
    except Exception:
        return []


def add_project_file(pid, filename):
    files = get_project_files(pid)
    if filename not in files:
        files.append(filename)
    update_project(pid, files=json.dumps(files, ensure_ascii=False))


def create_dashboard_token(uid):
    with db_conn() as conn:
        r = conn.execute("SELECT token FROM dashboard_tokens WHERE user_id=?", (uid,)).fetchone()
        if r and r[0]:
            return r[0]
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO dashboard_tokens (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, uid, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        return token


def get_dashboard_user(token):
    if not token:
        return None
    try:
        with db_conn() as conn:
            r = conn.execute("SELECT user_id FROM dashboard_tokens WHERE token=?", (token,)).fetchone()
            return r[0] if r else None
    except Exception:
        return None


def get_dashboard_link(uid):
    token = create_dashboard_token(uid)
    base = get_public_url()
    return f"{base}/?token={token}"


# ============================================================
# 🧠 نظام الفحص والتشخيص الذكي للأعطال
# ============================================================
def analyze_python_file(fp):
    issues = {"syntax": [], "imports": [], "warnings": [], "info": [], "stats": {}}
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            src = f.read()
        lines = src.splitlines()
        issues["stats"]["total_lines"] = len(lines)
        issues["stats"]["size"] = os.path.getsize(fp)
        try:
            tree = ast.parse(src)
            issues["info"].append("الصيغة سليمة")
        except SyntaxError as e:
            issues["syntax"].append({
                "line": e.lineno or 0,
                "col": e.offset or 0,
                "msg": e.msg,
                "text": (e.text or "").strip(),
            })
            return issues
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imports.add(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
        issues["stats"]["imports"] = sorted(imports)
        known_libs = {
            "telebot": "pyTelegramBotAPI", "requests": "requests", "flask": "flask",
            "httpx": "httpx", "bs4": "beautifulsoup4", "aiohttp": "aiohttp", "PIL": "Pillow",
            "pandas": "pandas", "numpy": "numpy", "sqlalchemy": "SQLAlchemy", "pydantic": "pydantic",
            "fastapi": "fastapi", "uvicorn": "uvicorn", "dotenv": "python-dotenv", "yaml": "pyyaml",
            "bcrypt": "bcrypt", "gunicorn": "gunicorn", "aiogram": "aiogram", "telegram": "python-telegram-bot",
        }
        required = {known_libs[imp] for imp in imports if imp in known_libs}
        issues["stats"]["required_libs"] = sorted(required)
        for i, line in enumerate(lines, 1):
            if len(line) > 160:
                issues["warnings"].append(f"السطر {i} طويل ({len(line)} حرف)")
        if re.search(r'(BOT_TOKEN|TOKEN)\s*=\s*["\']([0-9]{8,12}:[A-Za-z0-9_-]{30,50})["\']', src):
            issues["info"].append("يوجد توكن بوت معرف في الملف")
        issues["stats"]["functions"] = sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        issues["stats"]["classes"] = sum(1 for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    except Exception as e:
        issues["syntax"].append({"line": 0, "col": 0, "msg": str(e), "text": ""})
    return issues


def format_check_result(fp, issues):
    name = os.path.basename(fp)
    lines = [f"<blockquote><b>🔍 فحص {html.escape(name)}</b></blockquote>", ""]
    stats = issues.get("stats", {})
    lines.append("<b><u>📊 الإحصائيات:</u></b>")
    lines.append(f"  📏 السطور: <code>{stats.get('total_lines', 0)}</code>")
    lines.append(f"  📦 الحجم: <code>{get_file_size_str(fp)}</code>")
    lines.append(f"  🔧 الدوال: <code>{stats.get('functions', 0)}</code>")
    lines.append(f"  📚 الكلاسات: <code>{stats.get('classes', 0)}</code>\n")
    syntax = issues.get("syntax", [])
    if syntax:
        lines.append("<b><u>❌ أخطاء الصيغة (Syntax):</u></b>")
        for s in syntax:
            lines.append(f"  📍 السطر {s['line']}: <code>{html.escape(str(s['msg']))}</code>")
            if s["text"]:
                lines.append(f"  <code>{html.escape(s['text'][:80])}</code>")
        lines.append("")
    else:
        lines.append("<b><u>✅ الصيغة سليمة</u></b>\n")
    required = stats.get("required_libs", [])
    if required:
        lines.append("<b><u>📦 المكتبات المطلوبة:</u></b>")
        for lib in required[:15]:
            lines.append(f"  • <code>{html.escape(lib)}</code>")
        lines.append("")
    warnings = issues.get("warnings", [])
    if warnings:
        lines.append("<b><u>⚠️ تنبيهات:</u></b>")
        for w in warnings[:6]:
            lines.append(f"  • {html.escape(w)}")
    return "\n".join(lines)


def check_python_file(fp):
    issues = analyze_python_file(fp)
    return (not issues["syntax"]), format_check_result(fp, issues)


def check_json_file(fp):
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        if isinstance(data, dict):
            count, type_name = len(data), "كائن"
        elif isinstance(data, list):
            count, type_name = len(data), "قائمة"
        else:
            count, type_name = 0, type(data).__name__
        return True, f"<b>✅ JSON سليم</b>\n\n<code>{html.escape(os.path.basename(fp))}</code>\n📦 الحجم: <code>{get_file_size_str(fp)}</code>\n🔤 النوع: <code>{type_name}</code>\n🔑 العناصر: <code>{count}</code>"
    except json.JSONDecodeError as e:
        return False, f"<b>❌ خطأ JSON</b>\n📍 السطر: <b>{e.lineno}</b>\n🔤 <code>{html.escape(e.msg)}</code>"
    except Exception as e:
        return False, f"❌ خطأ: {html.escape(str(e))}"


def check_shell_file(fp):
    try:
        r = subprocess.run(["bash", "-n", fp], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return True, f"<b>✅ Shell سليم</b>\n📄 <code>{html.escape(os.path.basename(fp))}</code>"
        return False, f"<b>❌ خطأ Shell</b>\n<pre>{html.escape((r.stderr or '')[-400:])}</pre>"
    except FileNotFoundError:
        return True, "ℹ️ Shell: أداة bash غير متوفرة للفحص"
    except Exception as e:
        return False, f"❌ خطأ: {html.escape(str(e))}"


def check_requirements_file(fp):
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        lines = [l.strip() for l in content.splitlines() if l.strip() and not l.startswith("#")]
        if not lines:
            return False, "<b>⚠️ الملف فارغ</b>"
        result = f"<b>✅ requirements.txt سليم</b>\n\n📦 <b>العدد:</b> <code>{len(lines)}</code>\n\n"
        for line in lines[:30]:
            pkg_name = re.split(r'[=<>!]', line)[0].strip()
            result += f"  • <code>{html.escape(pkg_name)}</code>\n"
        return True, result
    except Exception as e:
        return False, f"❌ خطأ: {html.escape(str(e))}"


def check_js_file(fp):
    try:
        node_bin = shutil.which("node")
        if node_bin:
            r = subprocess.run([node_bin, "--check", fp], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return False, f"<b>❌ خطأ JavaScript Syntax</b>\n<pre>{html.escape((r.stderr or r.stdout)[-500:])}</pre>"
            return True, f"<b>✅ JavaScript سليم (فحص Node.js)</b>\n📄 <code>{html.escape(os.path.basename(fp))}</code> ({get_file_size_str(fp)})"
        return True, f"ℹ️ <b>ملف JavaScript</b> (أداة Node.js غير مثبتة للفحص المسبق)\n📄 <code>{html.escape(os.path.basename(fp))}</code> ({get_file_size_str(fp)})"
    except Exception as e:
        return True, f"ℹ️ JS: {html.escape(str(e))}"


def check_any_file(fp):
    if not os.path.exists(fp):
        return False, "<b>❌ الملف غير موجود</b>"
    name = os.path.basename(fp).lower()
    if name == "requirements.txt":
        return check_requirements_file(fp)
    elif name.endswith(".py"):
        return check_python_file(fp)
    elif name.endswith(".json"):
        return check_json_file(fp)
    elif name.endswith(".sh"):
        return check_shell_file(fp)
    elif name.endswith((".js", ".mjs", ".cjs")):
        return check_js_file(fp)
    elif name.endswith(".txt"):
        return check_requirements_file(fp)
    else:
        return True, f"<b>✅ مدعوم</b>\n📄 <code>{html.escape(name)}</code> ({get_file_size_str(fp)})"


# ============================================================
# 🛡️ العزل الكامل للبيئة والمكتبات (venv + pylibs)
# ============================================================
IMPORT_TO_PIP = {
    "telebot": "pyTelegramBotAPI", "telegram": "python-telegram-bot", "aiogram": "aiogram",
    "requests": "requests", "httpx": "httpx", "aiohttp": "aiohttp", "urllib3": "urllib3",
    "flask": "flask", "fastapi": "fastapi", "uvicorn": "uvicorn", "starlette": "starlette",
    "django": "django", "gunicorn": "gunicorn", "bs4": "beautifulsoup4", "lxml": "lxml",
    "selenium": "selenium", "PIL": "Pillow", "cv2": "opencv-python-headless",
    "numpy": "numpy", "pandas": "pandas", "scipy": "scipy", "sklearn": "scikit-learn",
    "matplotlib": "matplotlib", "sqlalchemy": "SQLAlchemy", "pymongo": "pymongo",
    "redis": "redis", "pymysql": "PyMySQL", "psycopg2": "psycopg2-binary",
    "asyncpg": "asyncpg", "motor": "motor", "pydantic": "pydantic", "dotenv": "python-dotenv",
    "yaml": "pyyaml", "bcrypt": "bcrypt", "jwt": "pyjwt", "cryptography": "cryptography",
    "Crypto": "pycryptodome", "qrcode": "qrcode", "barcode": "python-barcode",
    "websockets": "websockets", "socketio": "python-socketio", "openpyxl": "openpyxl",
    "xlrd": "xlrd", "docx": "python-docx", "fpdf": "fpdf2", "reportlab": "reportlab",
    "pyrogram": "pyrogram", "tgcrypto": "TgCrypto", "discord": "discord.py",
    "schedule": "schedule", "apscheduler": "APScheduler", "tqdm": "tqdm",
    "colorama": "colorama", "rich": "rich", "click": "click", "faker": "Faker",
    "validators": "validators", "phonenumbers": "phonenumbers", "geopy": "geopy",
    "pycountry": "pycountry", "babel": "Babel", "dateutil": "python-dateutil",
    "pytz": "pytz", "arabic_reshaper": "arabic-reshaper", "bidi": "python-bidi",
    "googletrans": "googletrans==4.0.0-rc1", "translate": "translate", "gtts": "gTTS",
    "speech_recognition": "SpeechRecognition", "moviepy": "moviepy", "pydub": "pydub",
    "yt_dlp": "yt-dlp", "pytube": "pytube", "web3": "web3", "ccxt": "ccxt",
    "paramiko": "paramiko", "ftplib": None, "dns": "dnspython", "ujson": "ujson",
    "orjson": "orjson", "jinja2": "Jinja2", "markdown": "Markdown", "emoji": "emoji",
    "nest_asyncio": "nest_asyncio", "psutil": "psutil", "pyngrok": "pyngrok",
    "firebase_admin": "firebase-admin", "google": "google-api-python-client",
    "stripe": "stripe", "boto3": "boto3", "paho": "paho-mqtt",
}

_STDLIB_FALLBACK = {
    "os", "sys", "re", "io", "json", "time", "math", "random", "string", "typing",
    "collections", "itertools", "functools", "datetime", "threading", "asyncio",
    "subprocess", "socket", "sqlite3", "logging", "traceback", "hashlib", "hmac",
    "base64", "uuid", "copy", "enum", "abc", "pathlib", "shutil", "tempfile", "zipfile",
    "csv", "glob", "signal", "contextlib", "dataclasses", "inspect", "argparse",
    "urllib", "http", "email", "unittest", "queue", "pickle", "struct", "array",
    "decimal", "fractions", "statistics", "secrets", "textwrap", "platform",
    "warnings", "weakref", "gc", "ast", "importlib", "multiprocessing", "concurrent",
    "ssl", "select", "ctypes", "configparser", "getpass", "shlex", "zlib", "gzip",
    "bz2", "lzma", "tarfile", "operator", "bisect", "heapq", "calendar", "locale",
    "codecs", "binascii", "difflib", "pprint", "reprlib", "numbers", "cmath",
    "webbrowser", "mimetypes", "xml", "html", "ipaddress", "sched",
}


def _stdlib_names():
    return set(getattr(sys, "stdlib_module_names", ())) | _STDLIB_FALLBACK


def resolve_required_pkgs(entry_path):
    issues = analyze_python_file(entry_path)
    imports = issues.get("stats", {}).get("imports") or []
    stdlib = _stdlib_names()
    pdir = os.path.dirname(entry_path)
    local_mods = {os.path.splitext(f)[0] for f in os.listdir(pdir)} if os.path.isdir(pdir) else set()
    pkgs = []
    for imp in imports:
        if imp in stdlib or imp in local_mods or imp.startswith("_"):
            continue
        mapped = IMPORT_TO_PIP.get(imp, imp)
        if mapped:
            pkgs.append(mapped)
    return sorted(set(pkgs))


def venv_dir(pdir):
    return os.path.join(pdir, VENV_DIRNAME)


def venv_python(pdir):
    vd = venv_dir(pdir)
    py = os.path.join(vd, "Scripts", "python.exe") if os.name == "nt" else os.path.join(vd, "bin", "python3")
    return py if os.path.exists(py) else None


def venv_pip(pdir):
    vd = venv_dir(pdir)
    pip = os.path.join(vd, "Scripts", "pip.exe") if os.name == "nt" else os.path.join(vd, "bin", "pip")
    return pip if os.path.exists(pip) else None


def ensure_venv(pdir, log=None):
    if venv_python(pdir):
        return True, "موجودة"
    try:
        if log:
            log("🧪 إنشاء بيئة بايثون معزولة لهذا المشروع...")
        r = subprocess.run([sys.executable, "-m", "venv", venv_dir(pdir)], capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not venv_python(pdir):
            msg = (r.stderr or r.stdout or "")[-300:]
            if log:
                log(f"⚠️ تعذّر إنشاء بيئة معزولة ({msg}) — سيُستخدم المجلد المحلي للمشروع.")
            return False, msg
        if log:
            log("✅ البيئة المعزولة جاهزة.")
        return True, "تم الإنشاء"
    except Exception as e:
        if log:
            log(f"⚠️ خطأ في إنشاء البيئة المعزولة: {e}")
        return False, str(e)


def _run_pip(cmd, timeout):
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            preexec_fn=os.setsid if os.name != "nt" else None
        )
        try:
            out, _ = proc.communicate(timeout=timeout)
            return proc.returncode, out or ""
        except subprocess.TimeoutExpired:
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.kill()
            except Exception:
                pass
            return -9, "انتهت المهلة"
    except Exception as e:
        return -1, str(e)


def _py_has_pip(py):
    rc, _ = _run_pip([py, "-m", "pip", "--version"], 30)
    return rc == 0


def pip_install_in(pdir, pkg, timeout=300):
    pkg = (pkg or "").strip()
    if not pkg:
        return False, "اسم المكتبة فارغ"
    pkg_name = re.split(r'[=<>!;\[]', pkg)[0].strip()
    if not pkg_name or not re.match(r'^[A-Za-z0-9_.\-]+$', pkg_name):
        return False, f"اسم مكتبة غير صالح: {html.escape(pkg)}"

    ensure_venv(pdir)
    common = ["-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check"]
    attempts = []
    vpy = venv_python(pdir)
    if vpy and _py_has_pip(vpy):
        attempts.append(("معزولة", [vpy] + common + [pkg]))
    libs = os.path.join(pdir, PYLIBS_DIRNAME)
    os.makedirs(libs, exist_ok=True)
    attempts.append(("محلية", [sys.executable] + common + ["--target", libs, "--upgrade", pkg]))

    last_err = ""
    for tag, cmd in attempts:
        rc, out = _run_pip(cmd, timeout)
        if rc == 0:
            ver = re.search(rf'Successfully installed .*?{re.escape(pkg_name)}[- ](\S+)', out)
            v = f" v{ver.group(1)}" if ver else ""
            return True, f"<code>{html.escape(pkg_name)}</code>{v} ({tag})"
        errs = [l for l in out.splitlines() if "ERROR" in l.upper()]
        last_err = errs[-1] if errs else out[-200:]
    return False, f"<code>{html.escape(pkg_name)}</code>\n<code>{html.escape(redact_secrets(last_err[:200]))}</code>"


def install_single_pkg(pid, pkg):
    p = get_project(pid)
    if not p:
        return False, "المشروع غير موجود"
    update_project(pid, install_status="installing")
    ok, msg = pip_install_in(p[4], pkg)
    update_project(pid, install_status="ok" if ok else "failed", installed=1 if ok else p[13])
    return ok, msg


def install_from_requirements(pid, reqs):
    lines = [l.strip() for l in (reqs or "").split("\n") if l.strip() and not l.startswith("#")]
    if not lines:
        return False, "لا توجد مكتبات", []
    update_project(pid, install_status="installing")
    results = []
    success = fail = 0
    for i, pkg in enumerate(lines, 1):
        ok, msg = install_single_pkg(pid, pkg)
        if ok:
            success += 1
        else:
            fail += 1
        results.append((i, len(lines), pkg, ok, msg))
    update_project(pid, install_status="ok" if fail == 0 else "partial", installed=1)
    return True, f"نجح {success} / فشل {fail}", results


def smart_install_project(pid, progress=None):
    p = get_project(pid)
    if not p:
        return False, "المشروع غير موجود", []
    entry_path = os.path.join(p[4], p[5])
    needed = set()
    if p[5].endswith(".py") and os.path.exists(entry_path):
        try:
            needed |= set(resolve_required_pkgs(entry_path))
        except Exception:
            pass
    for line in (p[7] or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            needed.add(line)
    if not needed:
        update_project(pid, install_status="ok")
        return False, "لا توجد مكتبات لتثبيتها", []

    update_project(pid, install_status="installing")
    ensure_venv(p[4], log=progress)
    results, ok_n, fail_n = [], 0, 0
    needed = sorted(needed)
    for i, pkg in enumerate(needed, 1):
        if progress:
            progress(f"⚙️ [{i}/{len(needed)}] <code>{html.escape(pkg)}</code>")
        ok, msg = pip_install_in(p[4], pkg)
        ok_n += int(ok)
        fail_n += int(not ok)
        results.append((i, len(needed), pkg, ok, msg))

    update_project(pid, requirements="\n".join(needed), installed=1, install_status="ok" if fail_n == 0 else "partial")
    return True, f"نجح {ok_n} / فشل {fail_n}", results


# ============================================================
# 🧠 محرك التشخيص الذكي للمشاريع (v13)
# ============================================================
_MISSING_RX = re.compile(r"No module named ['\"]([\w\.\-]+)['\"]")
_NODE_MISSING_RX = re.compile(r"Cannot find module ['\"]([^'\"]+)['\"]")
_ERR_HINTS = [
    ("SyntaxError", "خطأ صيغة (Syntax) في كود Python — راجع رقم السطر المذكور في السجل."),
    ("IndentationError", "خطأ مسافات/محاذاة (Indentation) في كود Python — تأكد من توحيد المسافات."),
    ("Unauthorized", "توكن البوت غير صحيح أو تم إلغاؤه. أنشئ توكن جديد من @BotFather."),
    ("401", "توكن البوت غير صحيح أو مرفوض (401)."),
    ("terminated by other getUpdates", "نفس التوكن يعمل في نسخة أخرى حالياً (409 Conflict). أوقف النسخة الثانية أو غيّر التوكن."),
    ("Conflict: terminated by other getUpdates", "تعارض تشغيل: نفس التوكن شغال في مكان آخر."),
    ("409", "تعارض تشغيل (409): نفس البوت يعمل في مكان آخر."),
    ("Address already in use", "المنفذ مستخدم بالفعل. استخدم متغير البيئة PORT بدل تثبيت البورت يدوياً."),
    ("EADDRINUSE", "المنفذ مستخدم بالفعل في Node.js. استخدم process.env.PORT."),
    ("PermissionError", "لا توجد صلاحية للوصول إلى هذا الملف أو المجلد."),
    ("FileNotFoundError", "ملف مطلوب غير موجود. تأكد من رفع جميع ملفات المشروع."),
    ("ENOENT", "ملف أو مسار مفقود في Node.js. تأكد من اكتمال ملفات المشروع."),
    ("KeyError", "متغير بيئة أو مفتاح غير موجود. أضفه من محرر .env."),
    ("MemoryError", "المشروع استهلك كامل الذاكرة المتاحة له."),
]


def _log_tail(p, n=45):
    try:
        lp = os.path.join(p[4], "output.log")
        if not os.path.isfile(lp):
            return ""
        with open(lp, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
        txt = txt.split("===== START")[-1]
        return redact_secrets("\n".join(txt.splitlines()[-n:]))
    except Exception:
        return ""


def get_logs(pid, n=100):
    p = get_project(pid)
    if not p:
        return "المشروع غير موجود"
    lp = os.path.join(p[4], "output.log")
    if not os.path.exists(lp):
        return "لا توجد سجلات بعد"
    try:
        with open(lp, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            return redact_secrets("".join(lines[-n:]))
    except Exception as e:
        return f"خطأ في قراءة السجل: {e}"


def get_error_logs(pid, n=60):
    raw = get_logs(pid, 300)
    if raw in ("المشروع غير موجود", "لا توجد سجلات بعد"):
        return raw
    err_lines = []
    capture = False
    for line in raw.splitlines():
        low = line.lower()
        if any(k in low for k in ("traceback", "error", "exception", "fatal", "syntaxerror", "cannot find module", "failed", "killed")):
            capture = True
        if capture:
            err_lines.append(line)
            if len(err_lines) >= n:
                break
        if line.startswith("===== START"):
            capture = False
    return "\n".join(err_lines[-n:]) if err_lines else "لا توجد أخطاء مسجلة في السجل الأخير ✅"


def diagnose_project(pid):
    """تشخيص شامل للمشروع بدون الادعاء بإصلاح المشكلة تلقائياً إن لم تُصلح فعلاً."""
    p = get_project(pid)
    if not p:
        return {"ok": False, "summary": "المشروع غير موجود.", "findings": [], "hints": []}

    pm = proj_meta(p)
    pdir = pm["path"]
    entry = pm["entry"]
    findings = []
    hints = []
    has_blocker = False

    if not pdir or not os.path.isdir(pdir):
        return {
            "ok": False,
            "summary": "مجلد المشروع غير موجود على القرص.",
            "findings": ["❌ مجلد المشروع مفقود — قد يكون حُذف أو لم يُستعد بعد."],
            "hints": ["استعد المشروع من النسخة الاحتياطية أو أعد رفعه."],
        }

    entry_path = safe_join_in_dir(pdir, entry)
    if not entry_path or not os.path.isfile(entry_path):
        has_blocker = True
        findings.append(f"❌ الملف الرئيسي <code>{html.escape(entry)}</code> غير موجود في المجلد.")
        hints.append("ارفع الملف الرئيسي أو تحقق من اسم الملف.")
    else:
        findings.append(f"✅ الملف الرئيسي موجود: <code>{html.escape(entry)}</code>")
        if entry.endswith(".py"):
            issues = analyze_python_file(entry_path)
            if issues["syntax"]:
                has_blocker = True
                for s in issues["syntax"]:
                    findings.append(f"❌ خطأ Python Syntax في السطر {s['line']}: <code>{html.escape(str(s['msg']))}</code>")
                hints.append("صحّح خطأ الصيغة في الكود ثم أعد رفع الملف.")
            else:
                findings.append("✅ صيغة Python (Syntax) سليمة.")
            # فحص المكتبات غير المثبتة
            try:
                req_pkgs = resolve_required_pkgs(entry_path)
                vpy = venv_python(pdir)
                pylibs = os.path.join(pdir, PYLIBS_DIRNAME)
                if req_pkgs and not vpy and not os.path.isdir(pylibs) and pm["installed"] == 0:
                    findings.append(f"⚠️ لم يتم تثبيت المكتبات بعد ({len(req_pkgs)} مكتبة مكتشفة).")
                    hints.append("اضغط «🪄 تثبيت تلقائي ذكي» قبل التشغيل.")
            except Exception:
                pass
        elif entry.endswith((".js", ".mjs", ".cjs")):
            node_bin = shutil.which("node")
            if node_bin:
                ok_js, msg_js = check_js_file(entry_path)
                if not ok_js:
                    has_blocker = True
                    findings.append("❌ خطأ JavaScript Syntax اكتشفه Node.js.")
                else:
                    findings.append("✅ صيغة JavaScript سليمة.")
            else:
                has_blocker = True
                findings.append("❌ Node.js غير مثبت على بيئة الاستضافة.")
            if not os.path.isfile(os.path.join(pdir, "package.json")):
                findings.append("⚠️ ملف <code>package.json</code> غير موجود (سيُنشأ تلقائياً عند التشغيل).")
            if not os.path.isdir(os.path.join(pdir, "node_modules")):
                findings.append("⚠️ مجلد <code>node_modules</code> غير موجود — قد يحتاج وقتاً للتثبيت عند التشغيل.")

    # فحص حالة التثبيت والسجل الأخير
    if pm["install_status"] == "failed":
        has_blocker = True
        findings.append("❌ آخر محاولة تثبيت للمكتبات فشلت.")
        hints.append("راجع اسم المكتبة أو ملف requirements.txt وأعد المحاولة.")

    tail = _log_tail(p, 50)
    if tail:
        miss_py = _MISSING_RX.findall(tail)
        if miss_py:
            has_blocker = True
            findings.append(f"❌ مكتبة Python ناقصة في آخر تشغيل: <code>{html.escape(miss_py[-1])}</code>")
            hints.append(f"ثبّت المكتبة <code>{html.escape(miss_py[-1])}</code> أو استخدم التثبيت الذكي.")
        miss_js = _NODE_MISSING_RX.findall(tail)
        if miss_js:
            has_blocker = True
            findings.append(f"❌ حزمة Node.js ناقصة في آخر تشغيل: <code>{html.escape(miss_js[-1])}</code>")
            hints.append("أضف الحزمة إلى package.json ثم أعد التشغيل.")
        for key, hint in _ERR_HINTS:
            if key in tail and hint not in hints:
                findings.append(f"⚠️ تم رصد <code>{html.escape(key)}</code> في السجلات.")
                hints.append(hint)

    if pm["stop_reason"]:
        findings.append(f"📌 سبب آخر توقف: {html.escape(pm['stop_reason'])}")

    summary = "✅ لم تُرصد أعطال هيكلية تمنع التشغيل." if not has_blocker else "⚠️ تم رصد مشاكل تحتاج مراجعة قبل أو أثناء التشغيل."
    return {"ok": not has_blocker, "summary": summary, "findings": findings, "hints": hints}


# ============================================================
# 🛡️ نظام إدارة العمليات المستقل والعزل الكامل (v13)
# ============================================================
class _AdoptedProcess:
    """يمثّل عملية مشروع بقيت تعمل بعد إعادة تشغيل البوت الرئيسي."""
    def __init__(self, pid):
        self.pid = pid
        self._p = psutil.Process(pid) if psutil else None

    def poll(self):
        if not self._p:
            try:
                os.kill(self.pid, 0)
                return None
            except OSError:
                return 1
        try:
            if self._p.is_running() and self._p.status() != psutil.STATUS_ZOMBIE:
                return None
        except Exception:
            pass
        return 1

    def wait(self, timeout=None):
        if self._p:
            try:
                self._p.wait(timeout)
            except Exception:
                pass
        return 1

    def terminate(self):
        if self._p:
            try:
                self._p.terminate()
            except Exception:
                pass

    def kill(self):
        if self._p:
            try:
                self._p.kill()
            except Exception:
                pass


def _kill_process_tree(pid_num):
    """إيقاف آمن للعملية وجميع عملياتها الفرعية دون ترك عمليات معلقة."""
    if not pid_num:
        return False
    killed = False
    try:
        pid_num = int(pid_num)
    except Exception:
        return False

    if os.name != "nt":
        try:
            pgid = os.getpgid(pid_num)
            os.killpg(pgid, signal.SIGTERM)
            killed = True
        except Exception:
            try:
                os.kill(pid_num, signal.SIGTERM)
                killed = True
            except Exception:
                pass

    if psutil:
        try:
            parent = psutil.Process(pid_num)
            children = parent.children(recursive=True)
            for ch in children:
                try:
                    ch.terminate()
                except Exception:
                    pass
            try:
                parent.terminate()
            except Exception:
                pass
            gone, alive = psutil.wait_procs(children + [parent], timeout=4)
            for p_alive in alive:
                try:
                    p_alive.kill()
                except Exception:
                    pass
            killed = True
        except Exception:
            pass

    if os.name != "nt":
        time.sleep(0.2)
        try:
            os.kill(pid_num, 0)
            try:
                os.killpg(os.getpgid(pid_num), signal.SIGKILL)
            except Exception:
                os.kill(pid_num, signal.SIGKILL)
        except OSError:
            pass
    return killed


def _find_orphan_pid(p):
    """يتحقق إذا كانت هناك عملية قديمة لنفس المشروع لا تزال تعمل في النظام."""
    if not psutil or not p[14] or not p[4]:
        return None
    try:
        ps = psutil.Process(int(p[14]))
        if not ps.is_running() or ps.status() == psutil.STATUS_ZOMBIE:
            return None
        cwd = os.path.realpath(ps.cwd())
        root = os.path.realpath(p[4])
        if cwd == root or cwd.startswith(root + os.sep):
            return ps.pid
    except Exception:
        pass
    return None


def is_running(pid):
    key = f"p{pid}"
    with RUNNING_LOCK:
        proc = RUNNING.get(key)
        if proc is not None:
            rc = proc.poll()
            if rc is None:
                return True
            RUNNING.pop(key, None)
            if pid not in _MANUAL_STOP:
                p = get_project(pid)
                if p and p[15] == "running":
                    tail = _log_tail(p, 15)
                    err_line = next((l for l in reversed(tail.splitlines()) if l.strip()), f"خرجت العملية برمز {rc}")
                    update_project(
                        pid, pid=0, status="crashed",
                        last_stop=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        stop_reason=f"توقف غير متوقع (code={rc})",
                        last_error=err_line[:300],
                    )
            return False

    # التحقق من عملية متبناة إذا لم تكن في RUNNING
    p = get_project(pid)
    if p and p[15] == "running" and p[14]:
        orphan = _find_orphan_pid(p)
        if orphan:
            with RUNNING_LOCK:
                RUNNING[key] = _AdoptedProcess(orphan)
            return True
        update_project(pid, pid=0, status="stopped")
    return False


def get_live_uptime(pid):
    p = get_project(pid)
    if not p or not is_running(pid):
        return "متوقف"
    return calc_uptime(proj_meta(p)["last_start"])


def _sp(pid, **kw):
    d = _START_PROGRESS.setdefault(pid, {"notes": [], "phase": "check", "t0": time.time()})
    note = kw.pop("note", None)
    if note:
        d["notes"].append(note)
    d.update(kw)


def _rotate_log_if_huge(log_path, max_bytes=2 * 1024 * 1024):
    try:
        if os.path.isfile(log_path) and os.path.getsize(log_path) > max_bytes:
            with open(log_path, "rb") as f:
                f.seek(-max_bytes // 2, os.SEEK_END)
                tail = f.read()
            with open(log_path, "wb") as f:
                f.write(b"--- [Log Rotated] ---\n" + tail)
    except Exception:
        pass


def _start_project_once(pid, watch=START_WATCH_SECONDS):
    p = get_project(pid)
    if not p:
        return False, "المشروع غير موجود", None
    pm = proj_meta(p)
    _sp(pid, phase="check")

    if is_running(pid):
        return False, "المشروع شغال بالفعل", None

    # تنظيف أي عملية قديمة عالقة قبل التشغيل
    orphan = _find_orphan_pid(p)
    if orphan:
        _kill_process_tree(orphan)

    entry_path = safe_join_in_dir(pm["path"], pm["entry"])
    if not entry_path or not os.path.exists(entry_path):
        update_project(pid, status="error", stop_reason="الملف الرئيسي غير موجود", last_error=f"Missing: {pm['entry']}")
        return False, f"الملف الرئيسي <code>{html.escape(pm['entry'])}</code> غير موجود", None

    if pm["entry"].endswith(".py"):
        ok, msg = check_python_file(entry_path)
        if not ok:
            update_project(pid, status="error", stop_reason="خطأ صيغة (SyntaxError)", last_error="فشل فحص الصيغة")
            return False, f"<b>فشل الفحص المسبق:</b>\n\n{msg[:800]}", None
    elif pm["entry"].endswith((".js", ".mjs", ".cjs")):
        ok_js, msg_js = check_js_file(entry_path)
        if not ok_js:
            update_project(pid, status="error", stop_reason="خطأ JavaScript Syntax", last_error="فشل فحص JS")
            return False, f"<b>فشل فحص JavaScript:</b>\n\n{msg_js[:800]}", None

    lf = None
    try:
        env = os.environ.copy()
        # عزل متغيرات البوت الرئيسي عن المشروع المستضاف
        for sensitive_var in ("BOT_TOKEN", "ADMIN_IDS", "DATA_DIR", "RENDER_EXTERNAL_URL"):
            env.pop(sensitive_var, None)

        _pl = os.path.join(pm["path"], PYLIBS_DIRNAME)
        env["PYTHONPATH"] = _pl + os.pathsep + env.get("PYTHONPATH", "")
        env["NODE_PATH"] = os.path.join(pm["path"], "node_modules")
        _vbin = os.path.join(pm["path"], VENV_DIRNAME, "bin")
        if os.path.isdir(_vbin):
            env["VIRTUAL_ENV"] = os.path.join(pm["path"], VENV_DIRNAME)
            env["PATH"] = _vbin + os.pathsep + env.get("PATH", "")
        env.update(load_project_env_dict(pm["path"]))

        port = 0
        public_url = ""
        if pm["ptype"] == "web":
            port = get_free_port()
            env["PORT"] = str(port)
            env["FLASK_RUN_PORT"] = str(port)
            env["HOST"] = "0.0.0.0"
            public_url = f"{get_public_url()}/proxy/{pid}"

        log_path = os.path.join(pm["path"], "output.log")
        _rotate_log_if_huge(log_path)
        lf = open(log_path, "a", encoding="utf-8")
        lf.write(f"\n\n===== START {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        lf.flush()

        def _log_line(line):
            try:
                lf.write(redact_secrets(line) + "\n")
                lf.flush()
            except Exception:
                pass

        if pm["entry"].endswith(".py"):
            _sp(pid, phase="install")
            ensure_venv(pm["path"], log=_log_line)
            py_exe = venv_python(pm["path"]) or sys.executable
            deps_marker = os.path.join(pm["path"], ".deps_installed")
            needed_now = set()
            try:
                needed_now = set(resolve_required_pkgs(entry_path))
            except Exception:
                pass
            for line in (pm["requirements"] or "").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    needed_now.add(line)
            sig = "|".join(sorted(needed_now))
            old_sig = ""
            if os.path.exists(deps_marker):
                try:
                    with open(deps_marker, "r", encoding="utf-8") as f:
                        old_sig = f.read()
                except Exception:
                    pass
            if needed_now and sig != old_sig:
                update_project(pid, install_status="installing")
                _log_line(f"🪄 تثبيت تلقائي ذكي ({len(needed_now)} مكتبة)...")
                all_ok = True
                for pkg in sorted(needed_now):
                    ok_i, _ = pip_install_in(pm["path"], pkg)
                    all_ok = all_ok and ok_i
                    _log_line(f"  {'✅' if ok_i else '❌'} {pkg}")
                    _sp(pid, note=f"{'✅' if ok_i else '❌'} {html.escape(pkg)}")
                update_project(pid, install_status="ok" if all_ok else "partial", installed=1)
                try:
                    with open(deps_marker, "w", encoding="utf-8") as f:
                        f.write(sig)
                except Exception:
                    pass
            cmd = [py_exe, "-u", pm["entry"]]
        elif pm["entry"].endswith((".js", ".mjs", ".cjs")):
            node_bin = shutil.which("node")
            if not node_bin:
                update_project(pid, status="error", stop_reason="Node.js غير مثبت", install_status="failed")
                return False, "❌ Node.js غير مثبت على السيرفر.", None
            _sp(pid, phase="install", note="🟨 تجهيز بيئة Node.js...")
            if ensure_node_package_json(pm["path"], pm["entry"]):
                _log_line("📝 تم إنشاء package.json تلقائياً")
            if not os.path.exists(os.path.join(pm["path"], "node_modules")):
                update_project(pid, install_status="installing")
                use_yarn = os.path.isfile(os.path.join(pm["path"], "yarn.lock")) and shutil.which("yarn")
                inst_cmd = ["yarn", "install"] if use_yarn else ["npm", "install", "--no-audit", "--no-fund"]
                _log_line(f"📦 {' '.join(inst_cmd)} ...")
                try:
                    r_npm = subprocess.run(inst_cmd, cwd=pm["path"], stdout=lf, stderr=subprocess.STDOUT, timeout=600)
                    ok_npm = (r_npm.returncode == 0)
                    update_project(pid, install_status="ok" if ok_npm else "failed", installed=1 if ok_npm else 0)
                    _sp(pid, note="✅ اكتمل تثبيت حزم Node.js" if ok_npm else "⚠️ تحذير أثناء تثبيت حزم Node.js")
                except Exception as e:
                    update_project(pid, install_status="failed")
                    _log_line(f"⚠️ npm install: {e}")
            cmd = [node_bin, pm["entry"]]
        else:
            cmd = ["bash", pm["entry"]]

        def _detach_and_limit():
            if os.name != "nt":
                os.setsid()
                try:
                    signal.signal(signal.SIGHUP, signal.SIG_IGN)
                except Exception:
                    pass
                if resource:
                    try:
                        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                        fs = MAX_FILE_MB * 1024 * 1024
                        resource.setrlimit(resource.RLIMIT_FSIZE, (fs, fs))
                        if STRICT_AS_MB > 0:
                            lim = STRICT_AS_MB * 1024 * 1024
                            resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
                    except Exception:
                        pass

        kw = {"preexec_fn": _detach_and_limit} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

        _sp(pid, phase="launch")
        proc = subprocess.Popen(
            cmd, cwd=pm["path"], stdout=lf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env, **kw
        )
        # إغلاق مقبض الملف في العملية الأم فوراً لمنع تسرب الواصفات (FD leak)
        try:
            lf.close()
            lf = None
        except Exception:
            pass

        with RUNNING_LOCK:
            RUNNING[f"p{pid}"] = proc

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        update_project(
            pid, pid=proc.pid, status="running", port=port,
            public_url=public_url, last_start=now_str,
            last_attempt=now_str, stop_reason="", last_error="",
        )

        _sp(pid, phase="watch", watch_t0=time.time(), watch_total=int(watch))
        for _ in range(int(watch)):
            time.sleep(1)
            if proc.poll() is not None:
                break

        rc = proc.poll()
        if rc is not None:
            with RUNNING_LOCK:
                RUNNING.pop(f"p{pid}", None)
            tail = _log_tail(p, 20)
            err_summary = next((l for l in reversed(tail.splitlines()) if l.strip()), f"exit code {rc}")
            update_project(
                pid, status="crashed" if rc != 0 else "stopped", pid=0,
                last_stop=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                stop_reason=f"توقف بعد التشغيل مباشرة (code={rc})",
                last_error=err_summary[:300],
            )
            return False, (
                f"<b>❌ المشروع توقف بعد التشغيل مباشرة (code={rc})</b>\n\n"
                f"📄 الملف: <code>{html.escape(pm['entry'])}</code>\n\n"
                f"<b><u>🔤 آخر السجلات:</u></b>\n"
                f"<pre>{html.escape(tail[-900:] or '(لا توجد تفاصيل)')}</pre>"
            ), None

        return True, "تم التشغيل بنجاح", {
            "project": pm["name"],
            "entry": pm["entry"],
            "ptype": pm["ptype"],
            "pid": proc.pid,
            "port": port,
            "public_url": public_url,
            "bot_username": pm["bot_username"],
            "bot_name": pm["bot_name"],
        }
    except Exception as e:
        err_clean = redact_secrets(str(e))[:300]
        update_project(pid, status="error", pid=0, stop_reason="استثناء أثناء التشغيل", last_error=err_clean)
        return False, f"خطأ أثناء التشغيل: <code>{html.escape(err_clean)}</code>", None
    finally:
        if lf is not None:
            try:
                lf.close()
            except Exception:
                pass


def _pretty_fail(msg, tail):
    hint = next((h for k, h in _ERR_HINTS if k in (tail or "")), "")
    out = msg
    if hint:
        out += f"\n\n💡 <b>التشخيص المقترح:</b> {hint}"
    return out


def start_project(pid, watch=START_WATCH_SECONDS):
    """تشغيل معزول بقفل خاص للمشروع + تثبيت تلقائي للمكتبات الناقصة (حتى 4 محاولات)."""
    appr = get_project_approved(pid)
    if appr != 1:
        msg_block = "⏳ هذا المشروع بانتظار موافقة الإدارة قبل التشغيل." if appr == 0 else "❌ تم رفض تشغيل هذا المشروع من قبل الإدارة."
        return False, msg_block, None

    lock = get_project_lock(pid)
    if not lock.acquire(blocking=False):
        return False, "⏳ هناك عملية تشغيل أو إيقاف جارية بالفعل لهذا المشروع.", None

    try:
        update_project(pid, last_attempt=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        _START_PROGRESS[pid] = {"notes": [], "phase": "check", "t0": time.time()}
        installed, tried = [], set()
        ok, msg, info = False, "", None
        for _ in range(4):
            ok, msg, info = _start_project_once(pid, watch)
            if ok:
                if installed:
                    msg += "\n\n📦 ثُبّتت تلقائياً: " + ", ".join(installed)
                    if info is not None:
                        info["auto_installed"] = installed
                return ok, msg, info
            p = get_project(pid)
            if not p:
                return False, msg, None
            tail = _log_tail(p)
            miss = _MISSING_RX.findall(tail)
            if not miss:
                return False, _pretty_fail(msg, tail), None
            mod = miss[-1].split(".")[0]
            local = os.path.exists(os.path.join(p[4], mod + ".py")) or os.path.isdir(os.path.join(p[4], mod))
            if local:
                return False, msg + f"\n\n💡 <b>الحل:</b> الملف المحلي <code>{html.escape(mod)}</code> غير مرفوع أو اسمه مختلف.", None
            pkg = IMPORT_TO_PIP.get(mod, mod)
            if pkg in tried:
                return False, msg + f"\n\n💡 تعذّر تثبيت <code>{html.escape(pkg)}</code> تلقائياً.", None
            tried.add(pkg)
            _sp(pid, phase="install", note=f"🔧 مكتبة ناقصة: {html.escape(pkg)}")
            ok2, m2 = pip_install_in(p[4], pkg)
            _sp(pid, note=f"{'✅' if ok2 else '❌'} {html.escape(pkg)}")
            if not ok2:
                update_project(pid, install_status="failed")
                return False, msg + f"\n\n❌ فشل تثبيت <code>{html.escape(pkg)}</code>:\n{m2}", None
            installed.append(pkg)
            update_project(pid, install_status="ok", installed=1)
        return False, msg, None
    finally:
        _START_PROGRESS.setdefault(pid, {})["done"] = True
        lock.release()


def stop_project(pid, reason="إيقاف يدوي"):
    p = get_project(pid)
    if not p:
        return False, "المشروع غير موجود"

    lock = get_project_lock(pid)
    with lock:
        _MANUAL_STOP.add(pid)
        try:
            key = f"p{pid}"
            stopped = False
            with RUNNING_LOCK:
                proc = RUNNING.pop(key, None)

            if proc is not None:
                _kill_process_tree(proc.pid)
                stopped = True

            if p[14]:
                if _kill_process_tree(p[14]):
                    stopped = True

            orphan = _find_orphan_pid(p)
            if orphan:
                _kill_process_tree(orphan)
                stopped = True

            update_project(
                pid, pid=0, status="stopped",
                last_stop=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                stop_reason=reason,
            )
            return True, "تم الإيقاف وتنظيف العمليات" if stopped else "المشروع متوقف بالفعل"
        finally:
            _MANUAL_STOP.discard(pid)


def render_start_card(pid, name=""):
    d = _START_PROGRESS.get(pid, {})
    phase = d.get("phase", "check")
    steps = [
        ("check", "🔍 فحص الملفات"),
        ("install", "📦 تجهيز البيئة والمكتبات"),
        ("launch", "▶️ إطلاق المشروع"),
        ("watch", "👁 مراقبة الاستقرار"),
    ]
    order = [k for k, _ in steps]
    idx = order.index(phase) if phase in order else 0
    base = {0: 10, 1: 35, 2: 65}.get(idx, 75)
    if phase == "watch":
        total = max(int(d.get("watch_total", START_WATCH_SECONDS)), 1)
        done = min(int(time.time() - d.get("watch_t0", time.time())), total)
        pct = 75 + int(25 * done / total)
    else:
        pct = base
    width = 12
    filled = int(round(width * pct / 100.0))
    bar = "▰" * filled + "▱" * (width - filled)
    lines = []
    for i, (_, label) in enumerate(steps):
        mark = "✅" if i < idx else ("⏳" if i == idx else "▫️")
        lines.append(f"{mark} {label}")
    name_line = ("📦 <b>" + html.escape(str(name)) + "</b>\n") if name else ""
    out = (
        "<blockquote><b>🚀 جاري تشغيل المشروع</b></blockquote>\n"
        f"<blockquote>{name_line}{bar}  <b>{pct}%</b></blockquote>\n"
        "<blockquote>" + "\n".join(lines) + "</blockquote>"
    )
    notes = d.get("notes", [])[-6:]
    if notes:
        out += "\n<blockquote expandable>" + "\n".join(notes) + "</blockquote>"
    return out


# ============================================================
# ♻️ نظام المراقبة الموحد + إعادة التشغيل المتدرج + حماية الموارد (v13)
# ============================================================
def _notify_users(uids, text):
    for u in set(uids):
        try:
            bot.send_message(u, text, parse_mode="HTML")
        except Exception:
            pass


def _limit_ram_for(pid, owner_id):
    try:
        custom = int(float(get_ui(f"limit_ram_{pid}") or 0))
        if custom > 0:
            return custom
    except Exception:
        pass
    return MAX_RAM_MB * (2 if is_premium(owner_id) else 1)


def _tree_usage(pid_os):
    root = psutil.Process(pid_os)
    procs = [root] + root.children(recursive=True)
    rss, secs = 0, 0.0
    for pr in procs:
        try:
            rss += pr.memory_info().rss
            t = pr.cpu_times()
            secs += t.user + t.system
        except Exception:
            pass
    return rss / (1024 * 1024), secs


def _schedule_auto_restart(pid, p):
    pm = proj_meta(p)
    if pm["auto_restart"] != 1 or pm["approved"] != 1 or pm["ptype"] == "script":
        return

    now = time.time()
    hist = [t for t in _CRASH_HISTORY.get(pid, []) if now - t < CRASH_WINDOW_SEC]
    max_r = max(1, int(pm["max_restarts"] or DEFAULT_MAX_AUTO_RESTARTS))
    if len(hist) >= max_r:
        update_project(pid, status="crashed", stop_reason=f"تجاوز حد إعادة التشغيل ({max_r}/{max_r})")
        _CRASH_HISTORY.pop(pid, None)
        _notify_users(
            [pm["owner_id"]] + ADMIN_IDS,
            f"<blockquote><b>🚫 توقف نهائي للمشروع</b></blockquote>\n"
            f"<blockquote>📦 <b>{html.escape(pm['name'])}</b> (ID: <code>{pid}</code>)\n"
            f"{get_ui('msg_alert_crash')}\n"
            f"⚠️ فشل بعد {max_r} محاولات إعادة تشغيل تلقائية.\n"
            "استخدم «🩺 تشخيص» أو «📄 السجلات» ثم أعد المحاولة يدوياً.</blockquote>",
        )
        return

    if pid in _RESTART_TIMERS and _RESTART_TIMERS[pid].is_alive():
        return

    attempt_no = len(hist) + 1
    backoff_sec = min(90, 5 * (3 ** (attempt_no - 1)))  # 5ث -> 15ث -> 45ث -> 90ث
    hist.append(now)
    _CRASH_HISTORY[pid] = hist

    _notify_users(
        [pm["owner_id"]],
        f"<blockquote><b>{html.escape(pm['name'])}</b></blockquote>\n"
        f"⚠️ {get_ui('msg_alert_crash')}\n"
        f"⏳ ستتم محاولة إعادة التشغيل رقم <code>{attempt_no}/{max_r}</code> بعد <code>{backoff_sec}</code> ثانية...",
    )

    def _delayed_restart():
        time.sleep(backoff_sec)
        cur = get_project(pid)
        if not cur:
            return
        cur_m = proj_meta(cur)
        if cur_m["auto_restart"] != 1 or cur_m["status"] == "stopped" or is_running(pid):
            return
        ok, _, _ = start_project(pid, watch=8)
        update_project(pid, restart_count=(cur_m["restart_count"] + 1))
        if ok:
            log_action(cur_m["owner_id"], "auto_restart_ok", f"pid={pid} attempt={attempt_no}")
            _notify_users(
                [cur_m["owner_id"]],
                f"<blockquote><b>✅ تمت الاستعادة</b></blockquote>\n"
                f"📦 <b>{html.escape(cur_m['name'])}</b>\n"
                f"{get_ui('msg_alert_recover')} (محاولة {attempt_no}/{max_r})",
            )
        else:
            log_action(cur_m["owner_id"], "auto_restart_fail", f"pid={pid} attempt={attempt_no}")

    t = threading.Thread(target=_delayed_restart, daemon=True, name=f"restart_{pid}")
    _RESTART_TIMERS[pid] = t
    t.start()


def unified_watchdog():
    """مراقب موحد يتابع حالة العمليات + حماية الموارد + إعادة التشغيل المتدرجة."""
    last_host_guard = 0.0
    while True:
        time.sleep(15)
        try:
            all_projs = get_all_projects()
            usage = {}
            for p in all_projs:
                pid = p[0]
                pm = proj_meta(p)
                was_running = (pm["status"] == "running")
                alive = is_running(pid)

                if was_running and not alive:
                    # توقف مفاجئ تم رصده
                    fresh = get_project(pid)
                    if fresh and proj_meta(fresh)["status"] == "crashed":
                        _schedule_auto_restart(pid, fresh)
                    continue

                if alive and psutil and pm["pid"]:
                    if is_admin(pm["owner_id"]):
                        continue
                    try:
                        rss, secs = _tree_usage(int(pm["pid"]))
                    except Exception:
                        continue
                    usage[pid] = (rss, p)
                    now = time.time()
                    prev = _CPU_LAST.get(pid)
                    _CPU_LAST[pid] = (now, secs)
                    limit = _limit_ram_for(pid, pm["owner_id"])
                    if rss > limit:
                        reason = f"تجاوز الذاكرة ({rss:.0f}MB / {limit}MB)"
                        stop_project(pid, reason=reason)
                        log_action(0, "resource_limit", f"pid={pid} {reason}")
                        _notify_users(
                            ADMIN_IDS + [pm["owner_id"]],
                            f"<blockquote><b>🛡 إيقاف تلقائي لحماية السيرفر</b></blockquote>\n"
                            f"📦 <b>{html.escape(pm['name'])}</b> (ID: <code>{pid}</code>)\n⚠️ السبب: {reason}",
                        )
                        continue
                    if prev and now > prev[0]:
                        cpu = (secs - prev[1]) / (now - prev[0]) * 100.0
                        if cpu > MAX_CPU_PCT:
                            _CPU_STRIKES[pid] = _CPU_STRIKES.get(pid, 0) + 1
                        else:
                            _CPU_STRIKES[pid] = 0
                        if _CPU_STRIKES.get(pid, 0) >= CPU_STRIKES:
                            _CPU_STRIKES[pid] = 0
                            reason = f"استهلاك معالج مستمر ({cpu:.0f}%)"
                            stop_project(pid, reason=reason)
                            log_action(0, "resource_limit", f"pid={pid} {reason}")
                            _notify_users(
                                ADMIN_IDS + [pm["owner_id"]],
                                f"<blockquote><b>🛡 إيقاف تلقائي لحماية السيرفر</b></blockquote>\n"
                                f"📦 <b>{html.escape(pm['name'])}</b> (ID: <code>{pid}</code>)\n⚠️ السبب: {reason}",
                            )

            if psutil and usage and time.time() - last_host_guard > 60:
                vm = psutil.virtual_memory()
                if vm.percent >= HOST_RAM_DANGER:
                    last_host_guard = time.time()
                    heavy_pid = max(usage, key=lambda x: usage[x][0])
                    heavy_p = usage[heavy_pid][1]
                    reason = f"ضغط عام على ذاكرة السيرفر ({vm.percent}%)"
                    stop_project(heavy_pid, reason=reason)
                    _notify_users(
                        ADMIN_IDS + [heavy_p[1]],
                        f"<blockquote><b>🛡 إيقاف تلقائي لحماية السيرفر</b></blockquote>\n"
                        f"📦 <b>{html.escape(heavy_p[2])}</b>\n⚠️ السبب: {reason}",
                    )
        except Exception as e:
            print(f"[Watchdog] {e}")


def _state_file():
    return os.path.join(DATA_DIR, "projects_state.json")


def snapshot_loop():
    while True:
        time.sleep(300)
        try:
            ids = [p[0] for p in get_all_projects() if is_running(p[0])]
            with open(_state_file(), "w", encoding="utf-8") as f:
                json.dump({"running": ids, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
        except Exception as e:
            print(f"[Snapshot] {e}")


def boot_restore_projects():
    time.sleep(12)
    try:
        ids = {p[0] for p in get_all_projects() if p[15] == "running"}
        try:
            with open(_state_file(), "r", encoding="utf-8") as f:
                ids |= set(json.load(f).get("running", []))
        except Exception:
            pass
        restored = []
        for pid in sorted(ids):
            p = get_project(pid)
            if not p or get_project_approved(pid) != 1:
                continue
            if is_running(pid):
                restored.append(p[2])
                continue
            ok, _, _ = start_project(pid, watch=8)
            if ok:
                restored.append(p[2])
            time.sleep(2)
        if restored:
            _notify_users(
                ADMIN_IDS,
                "<blockquote><b>♻️ استعادة المشاريع بعد الإقلاع</b></blockquote>\n"
                "<blockquote>" + "\n".join("✅ " + html.escape(n) for n in restored[:25]) + "</blockquote>",
            )
    except Exception as e:
        print(f"[BootRestore] {e}")


# ============================================================
# 💾 نظام النسخ الاحتياطي والاستعادة الموثوق (v13)
# ============================================================
_BK_LOCK = threading.Lock()
_BK_EXCL_DIRS = {VENV_DIRNAME, PYLIBS_DIRNAME, "__pycache__", ".git", "node_modules"}
_BK_EXCL_FILES = {".deps_installed"}


def zip_project_dir(pdir, dest_zip, exclude_dirnames=tuple(_BK_EXCL_DIRS)):
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(pdir):
            dirs[:] = [d for d in dirs if d not in exclude_dirnames]
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, pdir))
    return dest_zip


def verify_zip_integrity(zip_path):
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            return (bad is None), f"ملف تالف داخل الأرشيف: {bad}" if bad else "سليم"
    except Exception as e:
        return False, str(e)


def verify_db_integrity(db_path):
    try:
        c = sqlite3.connect(db_path, timeout=15.0)
        res = c.execute("PRAGMA integrity_check").fetchone()
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        c.close()
        if not res or res[0].lower() != "ok":
            return False, f"فشل فحص القاعدة: {res}"
        if not {"users", "projects"} <= tables:
            return False, "جداول القاعدة الأساسية مفقودة"
        return True, "سليمة"
    except Exception as e:
        return False, str(e)


def record_backup_history(btype, filename, size_bytes, proj_count, file_count, verified):
    try:
        with db_conn() as conn:
            conn.execute(
                """INSERT INTO backup_history (btype, filename, size_bytes, project_count, file_count, verified, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (btype, filename, int(size_bytes), int(proj_count), int(file_count), int(bool(verified)), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
    except Exception:
        pass


def get_backup_history(limit=10):
    with db_conn() as conn:
        return conn.execute("SELECT * FROM backup_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def backup_project_to_storage(pid):
    p = get_project(pid)
    if not p or not os.path.isdir(p[4]):
        return None
    try:
        safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', p[2])
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(ADMIN_STORAGE_DIR, f"{p[1]}_{safe_name}_{pid}_{stamp}.zip")
        zip_project_dir(p[4], dest)
        ok_z, _ = verify_zip_integrity(dest)
        if ok_z:
            update_project(pid, last_backup=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            return dest
        try:
            os.remove(dest)
        except Exception:
            pass
        return None
    except Exception as e:
        print(f"[Storage] فشل حفظ نسخة المشروع {pid}: {e}")
        return None


def list_admin_backups(limit=200):
    try:
        files = sorted(os.listdir(ADMIN_STORAGE_DIR), reverse=True)
    except Exception:
        files = []
    return files[:limit]


def _bk_files():
    for root, dirs, files in os.walk(PROJECTS_DIR):
        dirs[:] = [d for d in dirs if d not in _BK_EXCL_DIRS]
        for f in files:
            if f in _BK_EXCL_FILES or f.endswith(".log"):
                continue
            yield os.path.join(root, f)


def _data_sig():
    h = hashlib.md5()
    for fp in sorted(_bk_files()):
        try:
            st = os.stat(fp)
            h.update(f"{fp}:{st.st_size}:{int(st.st_mtime)}".encode())
        except Exception:
            pass
    h.update(str(get_user_count()).encode())
    h.update(str(len(get_all_projects())).encode())
    return h.hexdigest()


def _build_backup_zip():
    out = os.path.join(tempfile.gettempdir(), f"hostbot_backup_{secrets.token_hex(4)}.zip")
    tmpdb = os.path.join(tempfile.gettempdir(), f"hostbot_db_copy_{secrets.token_hex(4)}.db")
    with DB_LOCK:
        src = sqlite3.connect(DB_PATH, timeout=30.0)
        dst = sqlite3.connect(tmpdb)
        src.backup(dst)
        dst.close()
        src.close()
    ok_db, err_db = verify_db_integrity(tmpdb)
    if not ok_db:
        try:
            os.remove(tmpdb)
        except Exception:
            pass
        raise ValueError(f"قاعدة البيانات غير سليمة: {err_db}")

    file_count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmpdb, "db/host_v12.db")
        file_count += 1
        for fp in _bk_files():
            z.write(fp, "projects/" + os.path.relpath(fp, PROJECTS_DIR).replace(os.sep, "/"))
            file_count += 1
    try:
        os.remove(tmpdb)
    except Exception:
        pass
    ok_zip, err_zip = verify_zip_integrity(out)
    if not ok_zip:
        try:
            os.remove(out)
        except Exception:
            pass
        raise ValueError(f"فشل التحقق من سلامة النسخة: {err_zip}")
    return out, file_count


def local_24h_backup_once():
    with _BK_LOCK:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(BACKUPS_24H_DIR, f"auto_24h_{ts}.zip")
        tmp_dest = dest + ".tmp"
        tmpdb = os.path.join(tempfile.gettempdir(), f"local_db_{secrets.token_hex(4)}.db")
        try:
            with DB_LOCK:
                src = sqlite3.connect(DB_PATH, timeout=30.0)
                dst = sqlite3.connect(tmpdb)
                src.backup(dst)
                dst.close()
                src.close()
            file_count = 0
            with zipfile.ZipFile(tmp_dest, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(tmpdb, "db/host_v12.db")
                file_count += 1
                for fp in _bk_files():
                    z.write(fp, "projects/" + os.path.relpath(fp, PROJECTS_DIR).replace(os.sep, "/"))
                    file_count += 1
            ok_z, err_z = verify_zip_integrity(tmp_dest)
            if not ok_z:
                raise ValueError(err_z)
            os.replace(tmp_dest, dest)
            keep = max(1, int(float(get_ui("backup_keep_count") or BACKUP_RETENTION_COUNT)))
            baks = sorted([os.path.join(BACKUPS_24H_DIR, x) for x in os.listdir(BACKUPS_24H_DIR) if x.endswith(".zip")])
            for old in baks[:-keep]:
                try:
                    os.remove(old)
                except Exception:
                    pass
            sz = os.path.getsize(dest)
            record_backup_history("local", os.path.basename(dest), sz, len(get_all_projects()), file_count, True)
            log_action(0, "local_backup", os.path.basename(dest))
            return True, os.path.basename(dest), sz, file_count
        finally:
            for tf in (tmp_dest, tmpdb):
                if os.path.exists(tf):
                    try:
                        os.remove(tf)
                    except Exception:
                        pass


def local_24h_backup_loop():
    time.sleep(600)
    while True:
        try:
            files = [os.path.join(BACKUPS_24H_DIR, x) for x in os.listdir(BACKUPS_24H_DIR) if x.endswith(".zip")]
            newest = max((os.path.getmtime(f) for f in files), default=0)
            if time.time() - newest > 24 * 3600:
                local_24h_backup_once()
        except Exception as e:
            print(f"[Backup24h] {e}")
        time.sleep(1800)


def cloud_backup(force=False):
    if not ADMIN_IDS:
        return False, "لا يوجد أدمن معرف"
    with _BK_LOCK:
        sig = _data_sig()
        if not force and sig == get_ui("last_backup_sig"):
            return True, "لا تغييرات منذ آخر نسخة — تم تجنب الإرسال المكرر"
        path, file_count = _build_backup_zip()
        try:
            size = os.path.getsize(path)
            if size > TG_UPLOAD_LIMIT:
                return False, f"حجم النسخة {size // 1048576}MB أكبر من حد تيليجرام (49MB)."
            aid = ADMIN_IDS[0]
            proj_cnt = len(get_all_projects())
            cap = (
                f"☁️ <b>نسخة احتياطية سحابية (متحققة)</b>\n"
                f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                f"📦 المشاريع: <code>{proj_cnt}</code> · 📄 الملفات: <code>{file_count}</code>\n"
                f"👥 المستخدمين: <code>{get_user_count()}</code> · 💾 الحجم: <code>{size // 1024}KB</code>"
                + ("\n⚠️ أكبر من 20MB: الاستعادة التلقائية عند الإقلاع لن تعمل، أرسلها يدوياً." if size > TG_DOWNLOAD_LIMIT else "")
            )
            with open(path, "rb") as f:
                msg = bot.send_document(
                    aid, f, caption=cap, parse_mode="HTML",
                    visible_file_name="hostbot_backup.zip", disable_notification=True
                )
            try:
                bot.pin_chat_message(aid, msg.message_id, disable_notification=True)
            except Exception:
                pass
            old = get_ui("last_backup_msg")
            if old and str(old) != str(msg.message_id):
                try:
                    bot.delete_message(aid, int(old))
                except Exception:
                    pass
            set_ui("last_backup_msg", str(msg.message_id))
            set_ui("last_backup_sig", sig)
            now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for pr in get_all_projects():
                update_project(pr[0], last_backup=now_s)
            record_backup_history("cloud", "hostbot_backup.zip", size, proj_cnt, file_count, True)
            return True, f"تم النسخ والتحقق ({proj_cnt} مشروع · {file_count} ملف · {size // 1024}KB)"
        finally:
            try:
                os.remove(path)
            except Exception:
                pass


def cloud_backup_loop():
    time.sleep(120)
    while True:
        try:
            if len(get_all_projects()) or get_user_count() > 1:
                ok, m = cloud_backup(force=False)
                if not ok:
                    print(f"[Backup] {m}")
        except Exception as e:
            print(f"[Backup] {e}")
        time.sleep(max(BACKUP_HOURS, 0.25) * 3600)


def _apply_backup_zip(data):
    """استعادة آمنة مع فحص سلامة ZIP و SQLite ومنع Zip Slip والنسخ الاحتياطي قبل الاستبدال."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        return False, "الملف ليس أرشيف ZIP صالحاً"

    bad = zf.testzip()
    if bad is not None:
        return False, f"الأرشيف تالف عند الملف: {bad}"

    names = zf.namelist()
    if "db/host_v12.db" not in names:
        return False, "النسخة لا تحتوي على قاعدة البيانات (db/host_v12.db)"

    for info in zf.infolist():
        n = info.filename.replace("\\", "/")
        if n.startswith("/") or ".." in n.split("/"):
            return False, f"النسخة تحتوي مساراً غير آمن: {info.filename}"
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            return False, f"النسخة تحتوي روابط رمزية غير آمنة: {info.filename}"

    # التحقق من سلامة قاعدة البيانات قبل لمس القاعدة الحالية
    tmp_db = DB_PATH + ".verify_restore"
    with open(tmp_db, "wb") as f:
        f.write(zf.read("db/host_v12.db"))
    ok_db, err_db = verify_db_integrity(tmp_db)
    if not ok_db:
        try:
            os.remove(tmp_db)
        except Exception:
            pass
        return False, f"قاعدة البيانات داخل النسخة تالفة: {err_db}"

    # إيقاف المشاريع العاملة بأمان
    with RUNNING_LOCK:
        running_keys = list(RUNNING.keys())
    for key in running_keys:
        try:
            stop_project(int(key[1:]), reason="إيقاف مؤقت للاستعادة")
        except Exception:
            pass

    # استخراج ملفات المشاريع بأمان
    count = 0
    for info in zf.infolist():
        n = info.filename.replace("\\", "/")
        if n.startswith("projects/") and not info.is_dir():
            rel = n[len("projects/"):]
            dest = safe_join_in_dir(PROJECTS_DIR, rel)
            if not dest:
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(zf.read(info.filename))
            count += 1

    # نسخة أمان قبل استبدال قاعدة البيانات
    rollback_db = DB_PATH + ".pre_restore_bak"
    with DB_LOCK:
        if os.path.exists(DB_PATH):
            try:
                shutil.copy2(DB_PATH, rollback_db)
            except Exception:
                pass
        os.replace(tmp_db, DB_PATH)

    init_db()
    for p in get_all_projects():
        newp = os.path.join(PROJECTS_DIR, os.path.basename((p[4] or "").rstrip("/\\")))
        if os.path.isdir(newp):
            update_project(p[0], path=newp, pid=0, status="stopped")
    _load_btn_cache()
    _load_tpl_cache()
    return True, f"{count} ملف · {len(get_all_projects())} مشروع"


def cloud_restore_from_pinned():
    aid = ADMIN_IDS[0]
    chat = bot.get_chat(aid)
    pm = getattr(chat, "pinned_message", None)
    doc = getattr(pm, "document", None) if pm else None
    if not doc or not (doc.file_name or "").startswith("hostbot_backup"):
        return False, "لا توجد نسخة مثبّتة"
    if (doc.file_size or 0) > TG_DOWNLOAD_LIMIT:
        return False, "النسخة أكبر من 20MB. أرسلها يدوياً من زر الاستعادة"
    fi = bot.get_file(doc.file_id)
    return _apply_backup_zip(bot.download_file(fi.file_path))


def boot_recover():
    try:
        if len(get_all_projects()) == 0 and get_user_count() <= 1:
            ok, m = cloud_restore_from_pinned()
            if ok:
                print(f"[Recover] {m}")
                try:
                    bot.send_message(
                        ADMIN_IDS[0],
                        f"♻️ <b>تمت استعادة النسخة المثبّتة تلقائياً بعد الإقلاع</b>\n{m}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
    except Exception as e:
        print(f"[Recover] تعذّر: {e}")


# ============================================================
# 🤖 تهيئة البوت + دوال الواجهة الآمنة (منع أخطاء Telegram API و callback)
# ============================================================
bot = telebot.TeleBot(BOT_TOKEN)

BTN_STYLES = ("primary", "success", "danger", "none")
BTN_STYLE_NAMES = {"primary": "🔵 أزرق", "success": "🟢 أخضر", "danger": "🔴 أحمر", "none": "⚪ بدون"}

_btn_cache = {}
_tpl_cache = {}
_tpl_rx_cache = {}
_pattern_cache = []
_TL = threading.local()

ORIG_LINE = "━" * 25
SHORT_LINE = "━" * 14
_HDR_RX = re.compile(r"━{8,}[ \t]*\n[ \t]*(<b>[^\n]*?</b>)[ \t]*\n[ \t]*━{8,}[ \t]*")
_LINE_RX = re.compile(r"━{8,}")
_PROTECT_RX = re.compile(r"(<pre.*?</pre>|<code>.*?</code>|<blockquote.*?</blockquote>)", re.S)


def safe_answer_cb(call, text=None, show_alert=False):
    try:
        if text is not None:
            bot.answer_callback_query(call.id, str(text)[:190], show_alert=show_alert)
        else:
            bot.answer_callback_query(call.id)
    except Exception:
        pass


def safe_edit_msg(text, chat_id, message_id, reply_markup=None, parse_mode="HTML"):
    try:
        return bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        low = str(e).lower()
        if "message is not modified" in low:
            return None
        try:
            return bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            return None


def _rebuild_patterns():
    _pattern_cache.clear()
    for k, cfg in _btn_cache.items():
        if "*" in k:
            try:
                rx = re.compile("^" + re.escape(k).replace(r"\*", ".*") + "$")
                _pattern_cache.append((rx, cfg))
            except Exception:
                pass


def _lookup_btn_cfg(key):
    exact = _btn_cache.get(key)
    if exact:
        return exact, True
    for rx, cfg in _pattern_cache:
        if rx.match(key):
            return cfg, False
    return None, False


def _load_btn_cache():
    try:
        with db_conn() as conn:
            rows = conn.execute("SELECT bkey, label, style, icon FROM btn_custom").fetchall()
        _btn_cache.clear()
        for k, label, style, icon in rows:
            _btn_cache[k] = {"label": label, "style": style, "icon": icon}
        _rebuild_patterns()
    except Exception:
        pass


def _load_tpl_cache():
    try:
        with db_conn() as conn:
            rows = conn.execute("SELECT tid, text FROM tpl_custom").fetchall()
        _tpl_cache.clear()
        _tpl_cache.update(dict(rows))
        _tpl_rx_cache.clear()
    except Exception:
        pass


_load_btn_cache()
_load_tpl_cache()


def set_btn_custom(key, **changes):
    allowed = {"label", "style", "icon"}
    safe = {k: v for k, v in changes.items() if k in allowed}
    if not safe:
        return
    with db_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO btn_custom (bkey) VALUES (?)", (key,))
        for field, val in safe.items():
            conn.execute(f"UPDATE btn_custom SET {field}=? WHERE bkey=?", (val, key))
    _load_btn_cache()


def reset_btn_custom(key):
    with db_conn() as conn:
        conn.execute("DELETE FROM btn_custom WHERE bkey=?", (key,))
    _load_btn_cache()


def _strip_lead(text):
    s = re.sub(r'^[^\w\u0600-\u06FF]+', '', str(text or '')).strip()
    return s or text


def _orig_btn(text, cb=None, url=None, style=None):
    b = types.InlineKeyboardButton(text=text, callback_data=cb, url=url)
    if style:
        try:
            b.style = style
        except Exception:
            pass
    return b


def btn(text, cb=None, url=None, style=None):
    key = cb if cb else (f"url:{url}" if url else f"txt:{text}")
    cust, _ = _lookup_btn_cfg(key)
    if cust:
        if cust.get("label"):
            text = cust["label"]
        if cust.get("icon"):
            text = _strip_lead(text)
        if cust.get("style"):
            style = None if cust["style"] == "none" else cust["style"]
    b = _orig_btn(text, cb=cb, url=url, style=style)
    if cust and cust.get("icon"):
        try:
            b.icon_custom_emoji_id = cust["icon"]
        except Exception:
            pass
    return b


def btn_green(t, cb=None, url=None): return btn(t, cb=cb, url=url, style="success")
def btn_red(t, cb=None, url=None): return btn(t, cb=cb, url=url, style="danger")
def btn_blue(t, cb=None, url=None): return btn(t, cb=cb, url=url, style="primary")
def btn_gray(t, cb=None, url=None): return btn(t, cb=cb, url=url)


def make_custom_btn(key, cb=None, url=None):
    name, style, _ = get_button(key)
    return btn(name, cb=cb, url=url, style=style if style else None)


try:
    _orig_ikb_to_dict = types.InlineKeyboardButton.to_dict

    def _patched_ikb_to_dict(self):
        d = _orig_ikb_to_dict(self)
        st = getattr(self, "style", None)
        if st and "style" not in d:
            d["style"] = st
        ic = getattr(self, "icon_custom_emoji_id", None)
        if ic and "icon_custom_emoji_id" not in d:
            d["icon_custom_emoji_id"] = ic
        return d

    types.InlineKeyboardButton.to_dict = _patched_ikb_to_dict
except Exception:
    pass


def make_header(title):
    return f"<blockquote><b>{title}</b></blockquote>"


def make_section(title):
    return f"\n<b>{title}</b>\n"


def make_footer():
    return get_ui("divider_line") or SHORT_LINE


def _bar(pct, width=12):
    try:
        pct = max(0.0, min(100.0, float(pct)))
    except Exception:
        pct = 0.0
    filled = int(round(width * pct / 100.0))
    return "▰" * filled + "▱" * (width - filled)


def render_progress(title, icon_done, icon_fail, items, footer=""):
    done = sum(1 for _, ok in items if ok)
    total = len(items)
    pct = int(100 * done / max(total, 1))
    lines = [f"{icon_done if ok else icon_fail} {name}" for name, ok in items]
    shown = lines[-30:]
    more = f"… +{len(lines) - 30} أقدم\n" if len(lines) > 30 else ""
    tag = "blockquote expandable" if len(shown) > 10 else "blockquote"
    out = (
        f"<blockquote><b>{title}</b></blockquote>\n"
        f"<blockquote>{_bar(pct)}  <b>{pct}%</b>  ·  <code>{done}/{total}</code></blockquote>"
    )
    if shown:
        out += f"\n<{tag}>{more}" + "\n".join(shown) + "</" + tag.split()[0] + ">"
    if footer:
        out += f"\n{footer}"
    return out


def render_error(title, detail, hint=""):
    clean_detail = redact_secrets(str(detail))[:900]
    return (
        f"{make_header('💥 ' + title)}\n\n"
        f"<pre>{html.escape(clean_detail)}</pre>"
        + (f"\n💡 <i>{hint}</i>" if hint else "")
    )


def make_success_message(info):
    ptype = info.get("ptype", "bot")
    icon = type_label(ptype)
    txt = make_header("🎉 تم التشغيل بنجاح") + "\n\n"
    txt += f"<i>{get_ui('msg_start_success')}</i>\n\n"
    txt += f"📦 <b>المشروع:</b> <code>{html.escape(str(info['project']))}</code>\n"
    txt += f"🏷️ <b>النوع:</b> {icon}\n"
    txt += f"📄 <b>الملف:</b> <code>{html.escape(str(info['entry']))}</code>\n"
    txt += f"🆔 <b>PID:</b> <code>{info['pid']}</code>\n"
    txt += f"⏱️ <b>بدأ في:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>\n"
    if ptype == "web" and info.get("public_url"):
        txt += f"\n🔗 <b>الرابط:</b>\n<code>{info['public_url']}</code>\n"
    bot_user = info.get("bot_username", "")
    if bot_user:
        txt += f"\n🤖 <b>يوزر البوت:</b> @{bot_user}\n"
        txt += f"👤 <b>اسم البوت:</b> {html.escape(str(info.get('bot_name') or '—'))}\n"
    txt += "\n<b>✅ البوت شغال حالياً</b>"
    return txt


# ============================================================
# 🎨 نظام مسح القوالب والأزرار وتطبيق التخصيصات
# ============================================================
_PREMIUM_SELF_FUNCS = (
    'cb_pbtn', 'cb_ptpl', '_do_pbtn_input', '_do_pgrp_input', '_do_ptpl_input',
    'premium_main_menu', 'cb_premium_root', '_nav_row',
    'cb_backup_ui', 'cb_restore_ui_prompt', '_do_restore_ui',
    'cb_backup_db', 'cb_restore_db_prompt', '_do_restore_db',
    'cb_admin_buttons', 'make_buttons_editor', 'edit_button_menu',
    'cb_edit_btn', 'cb_chg_name', 'do_btn_name', 'cb_chg_style', 'cb_chg_row',
    'cb_admin_ui', 'cb_ui_edit', 'do_ui_edit', 'cb_ui_reset_one', 'cb_ui_reset_all',
    'cb_restart_project', 'cb_restart_all', 'cb_restart_host_confirm', 'cb_restart_host_go',
    'cb_files_v2', 'cb_file_view', 'cb_file_download', 'cb_file_delete_confirm', 'cb_file_delete_go',
    'cb_env_editor', '_do_env_save', 'cb_toggle_pause', '_paused_message', '_paused_callback',
    'cb_dx_actions', 'cb_dx_intercept', 'cb_dx_toggle', 'cmd_dx_edit', '_do_dx_input',
    '_dx_on_message', '_dx_editor_markup', '_dx_attach_page_btn',
)


def _ui_id(s, n=10):
    return hashlib.md5(str(s).encode("utf-8")).hexdigest()[:n]


def _ui_pat_of(node, env, depth=0):
    if node is None or depth > 6:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = ""
        for part in node.values:
            if isinstance(part, ast.Constant):
                out += str(part.value)
            else:
                r = _ui_pat_of(getattr(part, "value", None), env, depth + 1)
                out += r if r is not None else "*"
        return out
    if isinstance(node, ast.Name):
        return _ui_pat_of(env.get(node.id), env, depth + 1) if node.id in env else None
    if isinstance(node, ast.IfExp):
        return _ui_pat_of(node.body, env, depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        l, r = _ui_pat_of(node.left, env, depth + 1), _ui_pat_of(node.right, env, depth + 1)
        if l is None and r is None:
            return None
        return (l if l is not None else "*") + (r if r is not None else "*")
    return None


def _tparts(node, env, depth=0):
    if node is None or depth > 8:
        return [("p", None)]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [("s", node.value)]
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(("s", str(part.value)))
            else:
                out.append(("p", getattr(part, "value", None)))
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _tparts(node.left, env, depth + 1) + _tparts(node.right, env, depth + 1)
    if isinstance(node, ast.Name):
        return list(env[node.id]) if node.id in env else [("p", node)]
    if isinstance(node, ast.IfExp):
        return _tparts(node.body, env, depth + 1)
    return [("p", node)]


def _ph_name(node, used):
    base = "v"
    if isinstance(node, ast.Name):
        base = node.id
    elif isinstance(node, ast.Attribute):
        base = node.attr
    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            base = node.func.id
        elif isinstance(node.func, ast.Attribute):
            base = node.func.attr
    base = re.sub(r"\W", "_", str(base)) or "v"
    name, i = base, 2
    while name in used:
        name, i = f"{base}_{i}", i + 1
    used.add(name)
    return name


def _display_text(s):
    s = re.sub(r"<[^>]+>", " ", str(s or ""))
    s = re.sub(r"[━═─—–=~*`_•·]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_clean_title = _display_text
_FRIENDLY_HINTS = {
    "set_theme": "🎨 اختيار الثيم", "ui_edit": "📝 تعديل نص", "edit_btn": "🎛 تعديل زر",
    "pfview": "📄 عرض ملف", "pfdl": "📥 تحميل ملف", "pfdel": "🗑 حذف ملف",
    "pfiles": "📁 متصفح الملفات", "penv": "🔐 محرر .env", "proj": "📦 تفاصيل المشروع",
    "start": "▶️ تشغيل المشروع", "stop": "⏹️ إيقاف المشروع", "restart": "🔁 إعادة التشغيل",
    "logs": "📄 السجلات", "errlogs": "🚨 سجل الأخطاء", "diag": "🩺 تشخيص الأعطال",
    "pset": "⚙️ إعدادات المشروع", "smart": "🪄 تثبيت تلقائي ذكي", "install": "⚙️ تثبيت المكتبات",
    "addfile": "➕ إضافة ملف", "addpkg": "⚙️ تثبيت مكتبة", "check": "🔍 فحص الملف",
    "del": "🗑 حذف المشروع", "openweb": "🌐 فتح الموقع", "upload_req": "📤 رفع المتطلبات",
}


def _derive_clean_label(raw_label, key, func_name=""):
    clean = _display_text(str(raw_label or "").replace("*", " "))
    if len(re.findall(r"[\u0600-\u06FFA-Za-z]", clean)) >= 3:
        return clean
    raw_key = str(key or "").split(":", 1)[-1]
    for prefix, friendly in _FRIENDLY_HINTS.items():
        if raw_key.startswith(prefix):
            return friendly
    hint = re.sub(r"^(cb_|admin_|btn_|adm_)", "", raw_key).replace("*", "").replace("_", " ").strip()
    if len(re.findall(r"[\u0600-\u06FFA-Za-z]", hint)) >= 3:
        return f"🔘 {hint[:28]}"
    fn_hint = re.sub(r"^(cb_|hndl_|show_|do_)", "", str(func_name or "")).replace("_", " ").strip()
    return f"🔘 {fn_hint[:28]}" if fn_hint else "🔘 زر واجهة"


_SCAN = {"mtime": 0, "data": None}


def _scan_source(src):
    tree = ast.parse(src)

    class V(ast.NodeVisitor):
        def __init__(self):
            self.func = ["_module"]
            self.tenv = [{}]
            self.buttons = []
            self.rows = []
            self.tpls = []

        def visit_FunctionDef(self, node):
            self.func.append(node.name)
            self.tenv.append({})
            self.generic_visit(node)
            self.tenv.pop()
            self.func.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Assign(self, node):
            for tg in node.targets:
                if isinstance(tg, ast.Name):
                    self.tenv[-1][tg.id] = _tparts(node.value, self.tenv[-1])
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            if isinstance(node.target, ast.Name):
                cur = self.tenv[-1].get(node.target.id, [("p", node.target)])
                self.tenv[-1][node.target.id] = cur + _tparts(node.value, self.tenv[-1])
            self.generic_visit(node)

        def _btn_info(self, call):
            f = call.func
            fname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            if fname not in ("btn_green", "btn_red", "btn_blue", "btn_gray", "btn", "make_custom_btn"):
                return None
            kws = {k.arg: k.value for k in call.keywords if k.arg}
            if fname == "make_custom_btn":
                if not call.args or not isinstance(call.args[0], ast.Constant):
                    return None
                dkey = call.args[0].value
                label = DEFAULT_BUTTONS.get(dkey, (dkey,))[0]
                cb_node = call.args[1] if len(call.args) > 1 else kws.get("cb")
                url_node = kws.get("url")
            else:
                text_node = call.args[0] if call.args else kws.get("text") or kws.get("t")
                label = _ui_pat_of(text_node, self.tenv[-1]) or "(متغير)"
                cb_node = call.args[1] if len(call.args) > 1 else kws.get("cb")
                url_node = kws.get("url")
            key = None
            if cb_node is not None:
                p = _ui_pat_of(cb_node, self.tenv[-1])
                if p and not p.startswith("*") and re.search(r"[A-Za-z_]{3}", p):
                    key = p
            elif url_node is not None:
                p = _ui_pat_of(url_node, self.tenv[-1])
                if p and "*" not in p:
                    key = "url:" + p
            elif "*" not in label:
                key = "txt:" + label
            return key, _derive_clean_label(label, key, self.func[-1])

        def visit_List(self, node):
            infos = [self._btn_info(e) if isinstance(e, ast.Call) else None for e in node.elts]
            if node.elts and all(i is not None for i in infos):
                keys = [i[0] for i in infos if i[0]]
                if keys:
                    self.rows.append((self.func[-1], keys))
            self.generic_visit(node)

        def _record_tpl(self, arg):
            merged, used = [], set()
            for kind, val in _tparts(arg, self.tenv[-1]):
                if kind == "s":
                    if merged and merged[-1][0] == "s":
                        merged[-1] = ("s", merged[-1][1] + val)
                    else:
                        merged.append(("s", val))
                else:
                    merged.append(("p", _ph_name(val, used)))
            static = "".join(v for k, v in merged if k == "s")
            if len(static.strip()) < 6 or len(re.findall(r"[\u0600-\u06FFA-Za-z]", static)) < 3:
                return
            if sum(1 for k, _ in merged if k == "p") > 16:
                return
            self.tpls.append((self.func[-1], merged))

        def visit_Call(self, node):
            f = node.func
            fname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            info = self._btn_info(node) if isinstance(node, ast.Call) else None
            if info and info[0]:
                self.buttons.append((self.func[-1], info[0], info[1]))
            if fname in ("edit_message_text", "safe_edit_msg") and node.args:
                self._record_tpl(node.args[0])
            elif fname in ("send_message", "reply_to") and len(node.args) > 1:
                self._record_tpl(node.args[1])
            self.generic_visit(node)

    v = V()
    v.visit(tree)
    funcs, buttons, bkeys, tpls, groups = {}, {}, {}, {}, {}

    def fobj(name):
        fid = _ui_id(name, 8)
        funcs.setdefault(fid, {
            "name": name, "title": "",
            "admin": name.startswith(("cb_admin", "admin_", "do_ui", "cb_edit_btn", "cb_chg")) or "admin" in name,
            "btns": [], "rows": [], "tpls": []
        })
        return fid, funcs[fid]

    _self_prefixes = ("pbtn_", "pgrp_", "ptpl_", "admin_premium", "admin_backup_ui", "admin_restore_ui", "admin_backup_db", "admin_restore_db", "admin_buttons")
    for fn, key, label in v.buttons:
        if key.startswith(_self_prefixes) or fn in _PREMIUM_SELF_FUNCS or fn.startswith(("_dx", "cb_dx", "cmd_dx")):
            continue
        if "*" in key:
            glabel = _derive_clean_label(label, key, fn)
            g = groups.setdefault(key, {"key": key, "label": glabel, "count": 0})
            g["count"] += 1
        bid = _ui_id(key, 8)
        fid, fo = fobj(fn)
        if bid not in buttons:
            buttons[bid] = {"key": key, "label": _derive_clean_label(label, key, fn), "fid": fid}
            bkeys[key] = bid
        if bid not in fo["btns"]:
            fo["btns"].append(bid)

    for fn, keys in v.rows:
        fo = fobj(fn)[1]
        row = [bkeys[k] for k in keys if k in bkeys]
        if row:
            fo["rows"].append(row)

    for fn, parts in v.tpls:
        canon = "|".join(("S:" + x) if k == "s" else ("P:" + x) for k, x in parts)
        tid = _ui_id(canon, 10)
        if tid in tpls:
            continue
        fid, fo = fobj(fn)
        names = [x for k, x in parts if k == "p"]
        prev = _clean_title("".join(x for k, x in parts if k == "s"))[:40]
        tpls[tid] = {"parts": parts, "fid": fid, "names": names, "preview": prev}
        fo["tpls"].append(tid)

    for fid, fo in funcs.items():
        if not fo["title"]:
            fo["title"] = re.sub(r"^(cb_|hndl_|show_|do_)", "", fo["name"]).replace("_", " ").strip()[:32] or fo["name"]

    return {"funcs": funcs, "buttons": buttons, "bkeys": bkeys, "tpls": tpls, "groups": groups}


def ui_scan():
    path = os.path.abspath(__file__)
    mt = os.path.getmtime(path)
    if _SCAN["data"] is not None and _SCAN["mtime"] == mt:
        return _SCAN["data"]
    with open(path, encoding="utf-8") as fh:
        data = _scan_source(fh.read())
    _SCAN.update(mtime=mt, data=data)
    return data


def _tpl_text(parts):
    return "".join(v.replace("{", "{{").replace("}", "}}") if k == "s" else "{" + v + "}" for k, v in parts)


def _render_tpl(custom, vals):
    def _r(m):
        s = m.group(0)
        if s == "{{": return "{"
        if s == "}}": return "}"
        return str(vals.get(m.group(1), s))
    return re.sub(r"\{\{|\}\}|\{(\w+)\}", _r, custom)


def _tpl_regex(parts):
    rx, names = "", []
    for kind, v in parts:
        if kind == "s":
            rx += re.escape(v)
        else:
            rx += "(.*?)"; names.append(v)
    return re.compile("^" + rx + "$", re.S), names


def apply_templates(text):
    if not _tpl_cache or not isinstance(text, str):
        return text
    try:
        for tid, new_text in list(_tpl_cache.items()):
            rx = _tpl_rx_cache.get(tid)
            if rx is None:
                info = ui_scan()["tpls"].get(tid)
                if not info:
                    continue
                rx = _tpl_rx_cache[tid] = _tpl_regex(info["parts"])
            m = rx[0].match(text)
            if m:
                return _render_tpl(new_text, dict(zip(rx[1], m.groups())))
    except Exception:
        pass
    return text


def _divider():
    try:
        custom = get_ui("divider_line")
        if custom and custom != ORIG_LINE:
            return custom
    except Exception:
        pass
    return SHORT_LINE


def apply_divider(text):
    if not isinstance(text, str):
        return text
    if "━" not in text:
        return text
    if getattr(_TL, "html", False):
        text = _HDR_RX.sub(lambda m: "<blockquote>" + m.group(1) + "</blockquote>", text)
        parts = _PROTECT_RX.split(text)
        for i in range(0, len(parts), 2):
            parts[i] = _LINE_RX.sub(_divider(), parts[i])
        return "".join(parts)
    return _LINE_RX.sub(_divider(), text)


def apply_cosmetics(text):
    return apply_divider(apply_templates(text))


def _fix_markup(k):
    rm = k.get("reply_markup")
    if rm is None or not hasattr(rm, "keyboard"):
        return
    try:
        seen, rows = set(), []
        for row in rm.keyboard:
            nr = []
            for b in row:
                cb = getattr(b, "callback_data", None)
                if cb and cb != "noop":
                    if cb in seen:
                        continue
                    seen.add(cb)
                nr.append(b)
            if nr:
                rows.append(nr)
        rm.keyboard = rows
    except Exception:
        pass


_orig_send_message = bot.send_message
_orig_edit_message_text = bot.edit_message_text
_orig_reply_to = bot.reply_to
_DX_ON = set()
_DX_MSG_PAGE = {}


def _match_msg_tpl(raw_text):
    if not isinstance(raw_text, str) or not raw_text:
        return None, None
    try:
        sc = ui_scan()
        for tid, info in sc["tpls"].items():
            fn_name = sc["funcs"].get(info["fid"], {}).get("name", "")
            if fn_name in _PREMIUM_SELF_FUNCS or fn_name.startswith(("_dx", "cb_dx", "cmd_dx")):
                continue
            rx = _tpl_rx_cache.get(tid)
            if rx is None:
                rx = _tpl_rx_cache[tid] = _tpl_regex(info["parts"])
            if rx[0].match(raw_text):
                return tid, info["fid"]
    except Exception:
        pass
    return None, None


def _dx_attach_page_btn(chat_id, raw_text, k):
    if chat_id not in _DX_ON or not isinstance(raw_text, str):
        return None, None
    tid, fid = _match_msg_tpl(raw_text)
    if not tid and not fid:
        return None, None
    rm = k.get("reply_markup")
    if isinstance(rm, types.InlineKeyboardMarkup):
        try:
            has_dx = any(
                (getattr(b, "callback_data", "") or "").startswith(("dx", "ptpl_", "pbtn_", "pgrp_", "admin_premium"))
                for row in rm.keyboard for b in row
            )
            if not has_dx:
                new_rm = types.InlineKeyboardMarkup(row_width=rm.row_width)
                new_rm.keyboard = [list(r) for r in rm.keyboard]
                cb_val = f"dxptpl_{tid}" if tid else f"dxpscr_{fid}"
                new_rm.add(_orig_btn("📝 تعديل نص هذه الصفحة", cb=cb_val, style="primary"))
                k["reply_markup"] = new_rm
        except Exception:
            pass
    return tid, fid


def _send_message(chat_id, text, *a, **k):
    _TL.html = (k.get("parse_mode") or getattr(bot, "parse_mode", None)) == "HTML"
    _fix_markup(k)
    tid, fid = _dx_attach_page_btn(chat_id, text, k)
    res = _orig_send_message(chat_id, apply_cosmetics(text), *a, **k)
    if (tid or fid) and res and getattr(res, "message_id", None):
        _DX_MSG_PAGE[(chat_id, res.message_id)] = (tid, fid)
    return res


def _edit_message_text(text, *a, **k):
    _TL.html = (k.get("parse_mode") or getattr(bot, "parse_mode", None)) == "HTML"
    _fix_markup(k)
    chat_id = k.get("chat_id") or (a[0] if len(a) > 0 else None)
    tid, fid = _dx_attach_page_btn(chat_id, text, k)
    res = _orig_edit_message_text(apply_cosmetics(text), *a, **k)
    msg_id = k.get("message_id") or (a[1] if len(a) > 1 else None)
    if (tid or fid) and chat_id and msg_id:
        _DX_MSG_PAGE[(chat_id, msg_id)] = (tid, fid)
    return res


def _reply_to(message, text, *a, **k):
    _TL.html = (k.get("parse_mode") or getattr(bot, "parse_mode", None)) == "HTML"
    _fix_markup(k)
    return _orig_reply_to(message, apply_cosmetics(text), *a, **k)


bot.send_message = _send_message
bot.edit_message_text = _edit_message_text
bot.reply_to = _reply_to


# ============================================================
# ⏸ وضع الصيانة الشامل
# ============================================================
def host_paused():
    return get_ui("host_paused") == "1"


def toggle_host_paused():
    set_ui("host_paused", "0" if host_paused() else "1")
    return host_paused()


def is_admin(uid):
    return uid in ADMIN_IDS


def can_access_project(project, uid):
    if not project:
        return False
    return project[1] == uid or is_admin(uid)


def check_user_limit(uid):
    count = count_user_projects(uid)
    limit = get_max_projects(uid)
    return count < limit, count, limit


_orig_message_handler = bot.message_handler
_orig_callback_query_handler = bot.callback_query_handler


def message_handler(*a, **k):
    orig_func = k.get("func")

    def gated(m):
        if host_paused() and not is_admin(m.from_user.id):
            return False
        return orig_func(m) if orig_func else True

    k["func"] = gated
    return _orig_message_handler(*a, **k)


def callback_query_handler(*a, **k):
    orig_func = k.get("func")

    def gated(c):
        if host_paused() and not is_admin(c.from_user.id):
            return False
        return orig_func(c) if orig_func else True

    k["func"] = gated
    return _orig_callback_query_handler(*a, **k)


bot.message_handler = message_handler
bot.callback_query_handler = callback_query_handler


@bot.message_handler(func=lambda m: host_paused() and not is_admin(m.from_user.id),
                     content_types=['text', 'document', 'photo', 'video', 'audio'])
def _paused_message(m):
    bot.send_message(m.chat.id, get_ui("maintenance_text"), parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: host_paused() and not is_admin(c.from_user.id))
def _paused_callback(c):
    safe_answer_cb(c, "⏸ الاستضافة متوقفة مؤقتاً للصيانة", show_alert=True)


# ============================================================
# 🏠 القائمة الرئيسية + /start + مشاريعي + لوحة المشروع v13
# ============================================================
def main_menu(uid):
    m = types.InlineKeyboardMarkup(row_width=2)
    dashboard_url = get_dashboard_link(uid)
    _dcust = _btn_cache.get("btn_dashboard", {})
    _dname, _dstyle, _ = get_button("btn_dashboard")
    if _dcust.get("label"):
        _dname = _dcust["label"]
    if _dcust.get("icon"):
        _dname = _strip_lead(_dname)
    if _dcust.get("style"):
        _dstyle = None if _dcust["style"] == "none" else _dcust["style"]

    if dashboard_url.startswith("https://"):
        try:
            b = types.InlineKeyboardButton(_dname, web_app=types.WebAppInfo(url=dashboard_url))
            if _dstyle:
                try:
                    b.style = _dstyle
                except Exception:
                    pass
            if _dcust.get("icon"):
                try:
                    b.icon_custom_emoji_id = _dcust["icon"]
                except Exception:
                    pass
            m.add(b)
        except Exception:
            m.add(_orig_btn(_dname, url=dashboard_url, style=_dstyle))
    else:
        b = _orig_btn(_dname, url=dashboard_url, style=_dstyle)
        if _dcust.get("icon"):
            try:
                b.icon_custom_emoji_id = _dcust["icon"]
            except Exception:
                pass
        m.add(b)

    callbacks = {
        "btn_my_projects": "my_projs",
        "btn_new_project": "new_proj",
        "btn_account": "my_account",
        "btn_help": "help",
        "btn_wallet": "wallet_menu",
        "btn_support": "contact_support",
        "btn_server": "server_monitor",
    }
    rows = {}
    for key, cb in callbacks.items():
        _, _, row = get_button(key)
        rows.setdefault(row, []).append(make_custom_btn(key, cb=cb))

    for row in sorted(rows.keys()):
        btns = rows[row]
        if len(btns) >= 2:
            m.row(btns[0], btns[1])
        else:
            m.add(btns[0])

    if is_admin(uid):
        m.add(make_custom_btn("btn_admin", cb="admin_panel"))
    return m


@bot.message_handler(commands=['start'])
def cmd_start(msg):
    uid = msg.from_user.id
    user_states.pop(uid, None)
    if is_banned(uid):
        bot.send_message(msg.chat.id, "🚫 أنت محظور من استخدام البوت", parse_mode="HTML")
        return

    if not get_user(uid):
        add_user(uid, msg.from_user.username or "", msg.from_user.first_name or "")
        if is_paid_mode():
            set_max_projects(uid, int(float(get_ui("free_trial_projects") or 1)))
        log_action(uid, "register")
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) > 1 and parts[1].startswith("ref_"):
            try:
                ref_uid = int(parts[1][4:])
                bonus = register_referral(uid, ref_uid)
                if bonus:
                    cur = get_ui("currency_name") or "$"
                    try:
                        bot.send_message(ref_uid, f"🎉 انضم مستخدم جديد عبر رابط إحالتك! تمت إضافة <code>{bonus} {cur}</code> إلى رصيدك.", parse_mode="HTML")
                    except Exception:
                        pass
            except Exception:
                pass

    name = html.escape(msg.from_user.first_name or "صديقي")
    ui = get_all_ui()
    txt = (
        f"{make_header('🚀 HOST BOT v13 — ULTRA STABLE')}\n\n"
        f"👋 أهلاً <b>{name}</b>\n\n"
        f"<i>{ui['welcome_text']}</i>\n\n"
        "<b><u>📌 مميزات المنصة:</u></b>\n"
        "  🛡️ عزل عمليات مستقل لكل مشروع\n"
        "  ♻️ مراقبة وإعادة تشغيل تلقائية ذكية\n"
        "  🩺 تشخيص أعطال فوري + سجلات حية\n"
        "  💾 نسخ احتياطي واستعادة آمنة\n"
        "  🌐 لوحة تحكم Web + رابط لكل مشروع ويب\n\n"
        "<b><u>🎯 اختر من القائمة:</u></b>"
    )
    bot.send_message(msg.chat.id, txt, parse_mode="HTML", reply_markup=main_menu(uid))


@bot.callback_query_handler(func=lambda c: c.data == "main_menu")
def cb_main(call):
    safe_answer_cb(call)
    safe_edit_msg(make_header("🏠 القائمة الرئيسية"), call.message.chat.id, call.message.message_id,
                  reply_markup=main_menu(call.from_user.id), parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "my_projs")
def cb_my_projs(call):
    safe_answer_cb(call)
    uid = call.from_user.id
    projs = get_projects_by_owner(uid)
    if not projs:
        txt = f"{make_header('📦 مشاريعي')}\n\n📭 <b>لا توجد مشاريع بعد</b>\n\n🎯 <b>ابدأ بإنشاء مشروعك الأول</b>"
        m = types.InlineKeyboardMarkup()
        m.add(btn_green("➕ مشروع جديد", "new_proj"))
        m.add(btn_gray("🔙 رجوع", "main_menu"))
        safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
        return

    txt = f"{make_header('📦 مشاريعي')}\n\n👤 <b>المستخدم:</b> <code>{uid}</code>\n📊 <b>عدد المشاريع:</b> <code>{len(projs)}</code>\n"
    m = types.InlineKeyboardMarkup(row_width=1)
    for p in projs:
        pm = proj_meta(p)
        running = is_running(pm["id"])
        up = calc_uptime(pm["last_start"]) if running else "متوقف"
        icon = "🐍" if pm["ptype"] == "bot" else ("🌐" if pm["ptype"] == "web" else ("🟨" if pm["ptype"] == "node" else "📜"))
        if running:
            m.add(btn_green(f"{icon} {pm['name']} | ⏱️ {up}", f"proj_{pm['id']}"))
        elif pm["status"] in ("crashed", "error"):
            m.add(btn_red(f"{icon} {pm['name']} | 💥 متوقف بخطأ", f"proj_{pm['id']}"))
        else:
            m.add(btn_red(f"{icon} {pm['name']} | 🔴 متوقف", f"proj_{pm['id']}"))
    m.add(btn_green("➕ مشروع جديد", "new_proj"))
    m.add(btn_gray("🔙 رجوع", "main_menu"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("proj_"))
def cb_proj(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[5:])
    except Exception:
        return
    p = get_project(pid)
    if not p:
        safe_answer_cb(call, "❌ المشروع غير موجود", show_alert=True)
        return
    if not can_access_project(p, uid):
        safe_answer_cb(call, "🚫 غير مصرح لك بالوصول لهذا المشروع", show_alert=True)
        log_action(uid, "unauthorized_access", f"project={pid}")
        return
    safe_answer_cb(call)

    pm = proj_meta(p)
    running = is_running(pid)
    up = calc_uptime(pm["last_start"]) if running else "—"
    files = get_project_files(pid)
    appr = pm["approved"]
    appr_txt = "✅ معتمد" if appr == 1 else ("⏳ بانتظار الموافقة" if appr == 0 else "❌ مرفوض")

    if running:
        status_txt = "🟢 شغال"
    elif pm["status"] == "crashed":
        status_txt = "💥 متوقف بسبب عطل"
    elif pm["status"] == "error":
        status_txt = "⚠️ فشل عند التشغيل"
    else:
        status_txt = "🔴 متوقف"

    inst_map = {"ok": "✅ مثبتة", "installing": "⏳ جاري التثبيت", "partial": "⚠️ تثبيت جزئي", "failed": "❌ فشل التثبيت", "none": "▫️ لم تُثبّت"}
    inst_txt = inst_map.get(pm["install_status"], "✅ مثبتة" if pm["installed"] else "▫️ لم تُثبّت")
    auto_txt = "🟢 مفعّلة" if pm["auto_restart"] == 1 else "🔴 معطّلة"

    txt = (
        f"{make_header(html.escape(pm['name']))}\n"
        f"<i>{get_ui('msg_project_status_header')}</i>\n\n"
        f"🆔 <b>المعرف:</b> <code>#{pid}</code> · <b>النوع:</b> {type_label(pm['ptype'])}\n"
        f"📄 <b>الملف:</b> <code>{html.escape(pm['entry'])}</code> · <b>الملفات:</b> <code>{len(files)}</code>\n"
        f"📊 <b>الحالة:</b> {status_txt} · <b>الموافقة:</b> {appr_txt}\n"
        f"⏱️ <b>مدة التشغيل:</b> <code>{up}</code> · <b>PID:</b> <code>{pm['pid'] or '—'}</code>\n"
        f"🟢 <b>آخر تشغيل:</b> <code>{pm['last_start'] or '—'}</code>\n"
        f"🔴 <b>آخر توقف:</b> <code>{pm['last_stop'] or '—'}</code>\n"
        f"🔁 <b>إعادة التشغيل:</b> <code>{pm['restart_count']}</code> · <b>المراقبة:</b> {auto_txt}\n"
        f"📦 <b>المكتبات:</b> {inst_txt} · <b>النسخ:</b> <code>{pm['last_backup'] or '—'}</code>\n"
    )
    if running:
        cpu, mem = get_proc_metrics(pm["pid"])
        txt += f"📈 <b>الموارد:</b> <code>CPU {cpu}% | RAM {mem} MB</code>\n"
    if pm["stop_reason"] and not running:
        txt += f"📌 <b>سبب التوقف:</b> <code>{html.escape(pm['stop_reason'][:120])}</code>\n"
    if pm["last_error"] and not running:
        txt += f"⚠️ <b>آخر خطأ:</b> <code>{html.escape(pm['last_error'][:140])}</code>\n"
    if pm["ptype"] == "web" and pm["public_url"]:
        txt += f"\n🔗 <b>الرابط:</b> <code>{pm['public_url']}</code>\n"

    m = types.InlineKeyboardMarkup(row_width=2)
    if running:
        m.row(btn_red("⏹️ إيقاف", f"stop_{pid}"), btn_blue("🔁 إعادة تشغيل", f"restart_{pid}"))
    else:
        start_lbl = "🔄 إعادة محاولة التشغيل" if pm["status"] in ("crashed", "error") else "▶️ تشغيل"
        m.row(btn_green(start_lbl, f"start_{pid}"), btn_blue("🩺 تشخيص الأعطال", f"diag_{pid}"))

    m.row(btn_blue("📄 السجلات", f"logs_{pid}"), btn_red("🚨 سجل الأخطاء", f"errlogs_{pid}"))
    if running:
        m.row(btn_blue("🩺 تشخيص", f"diag_{pid}"), btn_blue("⚙️ الإعدادات", f"pset_{pid}"))
    else:
        m.row(btn_blue("🔍 فحص الملف", f"check_{pid}"), btn_blue("⚙️ الإعدادات", f"pset_{pid}"))

    m.row(btn_blue("📁 الملفات", f"pfiles_{pid}:0"), btn_blue("🔐 محرر .env", f"penv_{pid}"))
    m.row(btn_blue("💾 نسخ احتياطي", f"projbk_{pid}"), btn_blue("⚙️ مكتبة", f"addpkg_{pid}"))
    if pm["entry"].endswith(".py"):
        m.add(btn_green("🪄 تثبيت تلقائي ذكي", f"smart_{pid}"))
    if pm["ptype"] == "web" and pm["public_url"]:
        m.add(btn_blue("🌐 فتح الموقع", f"openweb_{pid}"))
    if pm["bot_username"]:
        m.add(btn_green("🚀 دخول للبوت", url=f"https://t.me/{pm['bot_username']}"))
    m.row(btn_blue("💾 حفظ الجلسة", f"sess_save_{pid}"), btn_red("🔄 تصفير الجلسة", f"sess_reset_{pid}"))
    m.row(btn_red("🗑️ حذف المشروع", f"del_{pid}"), btn_gray("🔙 رجوع", "my_projs"))

    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("pset_"))
def cb_proj_settings(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[5:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    safe_answer_cb(call)
    pm = proj_meta(p)
    auto_on = pm["auto_restart"] == 1
    txt = (
        f"{make_header('⚙️ إعدادات المشروع — ' + html.escape(pm['name']))}\n\n"
        f"♻️ <b>إعادة التشغيل التلقائية:</b> {'🟢 مفعّلة' if auto_on else '🔴 معطّلة'}\n"
        f"🔢 <b>الحد الأقصى للمحاولات:</b> <code>{pm['max_restarts']}</code>\n"
        f"🔁 <b>مرات إعادة التشغيل المسجلة:</b> <code>{pm['restart_count']}</code>\n"
        f"🕒 <b>آخر محاولة تشغيل:</b> <code>{pm['last_attempt'] or '—'}</code>\n"
        f"💾 <b>آخر نسخة احتياطية:</b> <code>{pm['last_backup'] or '—'}</code>"
    )
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn("♻️ " + ("تعطيل إعادة التشغيل التلقائية" if auto_on else "تفعيل إعادة التشغيل التلقائية"),
              f"pauto_{pid}", style="danger" if auto_on else "success"))
    m.row(btn_blue("➖ تقليل المحاولات", f"pmaxr_{pid}_-1"), btn_blue("➕ زيادة المحاولات", f"pmaxr_{pid}_1"))
    m.add(btn_gray("🔙 رجوع للمشروع", f"proj_{pid}"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("pauto_"))
def cb_toggle_autorestart(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[6:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    new_val = 0 if proj_meta(p)["auto_restart"] == 1 else 1
    update_project(pid, auto_restart=new_val)
    safe_answer_cb(call, "✅ تم " + ("تفعيل" if new_val else "تعطيل") + " إعادة التشغيل التلقائية")
    call.data = f"pset_{pid}"
    cb_proj_settings(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pmaxr_"))
def cb_max_restarts(call):
    uid = call.from_user.id
    parts = call.data.split("_")
    try:
        pid, delta = int(parts[1]), int(parts[2])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    cur = int(proj_meta(p)["max_restarts"] or DEFAULT_MAX_AUTO_RESTARTS)
    nxt = max(1, min(10, cur + delta))
    update_project(pid, max_restarts=nxt)
    safe_answer_cb(call, f"✅ الحد الآن: {nxt}")
    call.data = f"pset_{pid}"
    cb_proj_settings(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("projbk_"))
def cb_proj_backup(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[7:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    safe_answer_cb(call, "💾 جاري إنشاء نسخة احتياطية...")
    dest = backup_project_to_storage(pid)
    if not dest or not os.path.isfile(dest):
        bot.send_message(call.message.chat.id, "❌ تعذّر إنشاء نسخة احتياطية للمشروع.")
        return
    sz = os.path.getsize(dest)
    if sz <= TG_UPLOAD_LIMIT:
        with open(dest, "rb") as f:
            bot.send_document(
                call.message.chat.id, f,
                caption=f"💾 <b>نسخة احتياطية للمشروع</b> <code>{html.escape(p[2])}</code>\n📊 الحجم: <code>{get_file_size_str(dest)}</code>",
                parse_mode="HTML",
                visible_file_name=f"{p[2]}_backup.zip"
            )
    else:
        bot.send_message(call.message.chat.id, f"✅ تم حفظ النسخة محلياً ({get_file_size_str(dest)}) — تتجاوز حد 49MB للإرسال.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("diag_"))
def cb_diagnose(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[5:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    safe_answer_cb(call, "🩺 جاري التشخيص...")
    res = diagnose_project(pid)
    lines = [make_header(f"🩺 تشخيص الأعطال — {html.escape(p[2])}"), "", f"<b>{res['summary']}</b>", ""]
    if res["findings"]:
        lines.append("<b><u>🔎 نتائج الفحص:</u></b>")
        for f in res["findings"][:12]:
            lines.append(f"  {f}")
        lines.append("")
    if res["hints"]:
        lines.append("<b><u>💡 التوصيات:</u></b>")
        for h in res["hints"][:8]:
            lines.append(f"  • {h}")
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_blue("📄 عرض السجل", f"logs_{pid}"), btn_blue("🔍 فحص المشروع", f"check_{pid}"))
    m.row(btn_green("🔄 إعادة محاولة التشغيل", f"start_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))
    safe_edit_msg("\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("errlogs_"))
def cb_errlogs(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[8:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    safe_answer_cb(call)
    errs = get_error_logs(pid, 50)
    if len(errs) > 2800:
        errs = "...\n" + errs[-2800:]
    txt = f"{make_header('🚨 سجل الأخطاء — ' + html.escape(p[2]))}\n\n<pre>{html.escape(errs)}</pre>"
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_blue("🩺 تشخيص الأعطال", f"diag_{pid}"), btn_blue("📄 السجل الكامل", f"logs_{pid}"))
    m.add(btn_gray("🔙 رجوع", f"proj_{pid}"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("start_"))
def cb_start(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[6:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    appr = get_project_approved(pid)
    if appr != 1:
        msg_block = "⏳ مشروعك بانتظار موافقة الإدارة قبل التشغيل." if appr == 0 else "❌ تم رفض تشغيل هذا المشروع من قبل الإدارة."
        return safe_answer_cb(call, msg_block, show_alert=True)
    if is_running(pid):
        return safe_answer_cb(call, "✅ المشروع شغال بالفعل", show_alert=True)

    safe_answer_cb(call, "🚀 بدء التشغيل...")
    msg = bot.send_message(call.message.chat.id, "⏳ جاري التجهيز...", parse_mode="HTML")

    def do_start():
        res = {}
        t = threading.Thread(target=lambda: res.__setitem__("r", start_project(pid)), daemon=True)
        t.start()
        last = ""
        while t.is_alive():
            card = render_start_card(pid, p[2])
            if card != last:
                try:
                    bot.edit_message_text(card, msg.chat.id, msg.message_id, parse_mode="HTML")
                    last = card
                except Exception:
                    pass
            time.sleep(2)
        ok, m_txt, info = res.get("r", (False, "خطأ غير متوقع", None))
        if ok and info:
            text = make_success_message(info)
            markup = types.InlineKeyboardMarkup(row_width=2)
            if info.get("bot_username"):
                markup.add(btn_blue("🤖 فتح البوت", url=f"https://t.me/{info['bot_username']}"))
            if info.get("public_url"):
                markup.add(btn_blue("🌐 فتح الموقع", url=info["public_url"]))
            markup.row(btn_blue("📄 السجلات", f"logs_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))
            safe_edit_msg(text, msg.chat.id, msg.message_id, reply_markup=markup, parse_mode="HTML")
        else:
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.row(btn_green("🔄 إعادة المحاولة", f"start_{pid}"), btn_blue("🩺 تشخيص", f"diag_{pid}"))
            mk.row(btn_blue("📄 السجلات", f"logs_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))
            safe_edit_msg(f"{make_header('❌ فشل التشغيل')}\n\n{m_txt}", msg.chat.id, msg.message_id, reply_markup=mk, parse_mode="HTML")

    threading.Thread(target=do_start, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data.startswith("stop_"))
def cb_stop(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[5:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    _, msg = stop_project(pid, reason="إيقاف يدوي من المستخدم")
    safe_answer_cb(call, f"{get_ui('msg_stop_success')} ({msg})", show_alert=True)
    call.data = f"proj_{pid}"
    cb_proj(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("restart_"))
def cb_restart_project(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[8:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    appr = get_project_approved(pid)
    if appr != 1:
        return safe_answer_cb(call, "⏳ المشروع بانتظار موافقة الإدارة أو مرفوض.", show_alert=True)
    safe_answer_cb(call, "🔁 جاري إعادة التشغيل...")

    def do():
        stop_project(pid, reason="إعادة تشغيل يدوية")
        time.sleep(1)
        ok, m_txt, info = start_project(pid)
        txt = make_success_message(info) if ok and info else render_error("فشلت إعادة التشغيل", m_txt)
        mu = types.InlineKeyboardMarkup(row_width=2)
        if not ok:
            mu.row(btn_green("🔄 إعادة المحاولة", f"start_{pid}"), btn_blue("🩺 تشخيص", f"diag_{pid}"))
        mu.add(btn_gray("🔙 رجوع", f"proj_{pid}"))
        safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=mu, parse_mode="HTML")

    threading.Thread(target=do, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data.startswith(("logs_", "logs_live_", "logs_dl_")))
def cb_logs(call):
    uid = call.from_user.id
    is_dl = call.data.startswith("logs_dl_")
    is_live = call.data.startswith("logs_live_")
    try:
        pid = int(call.data.split("_")[-1])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)

    if is_dl:
        lp = os.path.join(p[4], "output.log")
        if not os.path.isfile(lp):
            return safe_answer_cb(call, "لا يوجد ملف سجل بعد", show_alert=True)
        safe_answer_cb(call)
        with open(lp, "r", encoding="utf-8", errors="ignore") as f:
            clean_txt = redact_secrets(f.read())
        bio = io.BytesIO(clean_txt.encode("utf-8"))
        bio.name = f"{p[2]}_logs.txt"
        bot.send_document(call.message.chat.id, bio, caption=f"📜 سجل {html.escape(p[2])}")
        return

    def _build_log_msg():
        raw = get_logs(pid, 80)
        if len(raw) > 2800:
            raw = "...\n" + raw[-2800:]
        hint = next((h for k, h in _ERR_HINTS if k in raw), "")
        diag = f"\n🚨 <b>تشخيص سريع:</b> {hint}\n" if hint else ""
        t = f"{make_header('📄 السجلات — ' + html.escape(p[2]))}{diag}\n<pre>{html.escape(raw)}</pre>"
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.row(btn_blue("🔄 تحديث", f"logs_{pid}"), btn_green("📡 مباشر (15ث)", f"logs_live_{pid}"))
        mk.row(btn_blue("📥 تحميل السجل", f"logs_dl_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))
        return t, mk

    if is_live:
        safe_answer_cb(call, "📡 جاري التحديث المباشر لـ 15 ثانية...")
        def _live_loop():
            for _ in range(3):
                t, mk = _build_log_msg()
                safe_edit_msg(t, call.message.chat.id, call.message.message_id, reply_markup=mk, parse_mode="HTML")
                time.sleep(5)
        threading.Thread(target=_live_loop, daemon=True).start()
        return

    safe_answer_cb(call)
    txt, m = _build_log_msg()
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("openweb_"))
def cb_openweb(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[8:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid) or not p[12]:
        return safe_answer_cb(call, "❌ لا يوجد رابط", show_alert=True)
    safe_answer_cb(call)
    m = types.InlineKeyboardMarkup()
    m.add(btn_blue("🌐 فتح الموقع", url=p[12]))
    m.add(btn_gray("🔙 رجوع", f"proj_{pid}"))
    bot.send_message(call.message.chat.id, f"🔗 <b>رابط موقعك:</b>\n\n<code>{p[12]}</code>", reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("check_"))
def cb_check(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[6:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    fp = safe_join_in_dir(p[4], p[5])
    if not fp:
        return safe_answer_cb(call, "❌ مسار الملف غير صالح", show_alert=True)
    ok, msg = check_any_file(fp)
    safe_answer_cb(call, "✅ الفحص نجح" if ok else "⚠️ توجد ملاحظات في الملف", show_alert=True)
    bot.send_message(call.message.chat.id, msg, parse_mode="HTML")


# ============================================================
# 📁 متصفح الملفات الآمن (يحمي من Path Traversal وحد 64 بايت) + محرر .env
# ============================================================
_FILE_REF_MAP = {}


def _reg_file_ref(pid, rel_path):
    h = hashlib.md5(f"{pid}:{rel_path}".encode("utf-8")).hexdigest()[:10]
    _FILE_REF_MAP[h] = (pid, rel_path)
    return h


def _get_file_ref(h, expected_pid=None):
    item = _FILE_REF_MAP.get(h)
    if not item:
        return None, None
    pid, rel = item
    if expected_pid is not None and pid != expected_pid:
        return None, None
    return pid, rel


def _nav_row(prefix, page, pages, back):
    nav = []
    if page > 0:
        nav.append(btn_blue("◀️ السابق", f"{prefix}:{page - 1}"))
    if page < pages - 1:
        nav.append(btn_blue("التالي ▶️", f"{prefix}:{page + 1}"))
    rows = [nav] if nav else []
    rows.append([btn_gray("🔙 رجوع", back)])
    return rows


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfiles_"))
def cb_files_v2(call):
    uid = call.from_user.id
    rest = call.data[len("pfiles_"):]
    pid_s, _, page_s = rest.partition(":")
    try:
        pid, page = int(pid_s), int(page_s or 0)
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    safe_answer_cb(call)

    files = list_dir_files(p[4])
    pages = max(1, (len(files) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    m = types.InlineKeyboardMarkup(row_width=1)
    for f in files[page * PER_PAGE:(page + 1) * PER_PAGE]:
        fp = safe_join_in_dir(p[4], f)
        size = get_file_size_str(fp) if fp else "?"
        fh = _reg_file_ref(pid, f)
        m.add(btn_blue(f"📄 {f[:28]} ({size})", f"pfv_{fh}"))
    m.add(btn_green("➕ إضافة ملف", f"addfile_{pid}"))
    lbl_env = "🔐 تعديل .env" if os.path.exists(os.path.join(p[4], ".env")) else "🔐 إنشاء .env"
    m.add(btn_blue(lbl_env, f"penv_{pid}"))
    for r in _nav_row(f"pfiles_{pid}", page, pages, f"proj_{pid}"):
        m.row(*r)
    txt = f"{make_header('📁 ملفات ' + html.escape(p[2]))}\n\n📊 العدد: <code>{len(files)}</code> — صفحة {page + 1}/{pages}"
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfv_"))
def cb_file_view(call):
    uid = call.from_user.id
    fh = call.data[4:]
    pid, fname = _get_file_ref(fh)
    if not pid:
        return safe_answer_cb(call, "⚠️ انتهت الجلسة، افتح الملفات مجدداً", show_alert=True)
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    fp = safe_join_in_dir(p[4], fname)
    if not fp or not os.path.isfile(fp):
        return safe_answer_cb(call, "❌ الملف غير موجود", show_alert=True)
    safe_answer_cb(call)
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_blue("📥 تحميل", f"pfd_{fh}"), btn_red("🗑 حذف", f"pfx_{fh}"))
    m.add(btn_gray("🔙 رجوع", f"pfiles_{pid}:0"))
    txt = f"{make_header(html.escape(fname))}\n\n📊 الحجم: <code>{get_file_size_str(fp)}</code>"
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfd_"))
def cb_file_download(call):
    uid = call.from_user.id
    fh = call.data[4:]
    pid, fname = _get_file_ref(fh)
    if not pid:
        return safe_answer_cb(call, "⚠️ انتهت الجلسة", show_alert=True)
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    fp = safe_join_in_dir(p[4], fname)
    if not fp or not os.path.isfile(fp):
        return safe_answer_cb(call, "❌ الملف غير موجود", show_alert=True)
    safe_answer_cb(call)
    with open(fp, "rb") as f:
        bot.send_document(call.message.chat.id, f, visible_file_name=os.path.basename(fname))


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfx_"))
def cb_file_delete_confirm(call):
    uid = call.from_user.id
    fh = call.data[4:]
    pid, fname = _get_file_ref(fh)
    if not pid:
        return safe_answer_cb(call, "⚠️ انتهت الجلسة", show_alert=True)
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    if fname == p[5]:
        return safe_answer_cb(call, "⚠️ هذا الملف الرئيسي، لا يمكن حذفه منفرداً.", show_alert=True)
    safe_answer_cb(call)
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_red("✅ نعم احذف", f"pfok_{fh}"), btn_gray("❌ إلغاء", f"pfv_{fh}"))
    safe_edit_msg(f"⚠️ حذف <code>{html.escape(fname)}</code>؟", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfok_"))
def cb_file_delete_go(call):
    uid = call.from_user.id
    fh = call.data[5:]
    pid, fname = _get_file_ref(fh)
    if not pid:
        return safe_answer_cb(call, "⚠️ انتهت الجلسة", show_alert=True)
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    fp = safe_join_in_dir(p[4], fname)
    if fp and os.path.isfile(fp) and fname != p[5]:
        try:
            os.remove(fp)
        except Exception:
            pass
    safe_answer_cb(call, "✅ تم الحذف")
    call.data = f"pfiles_{pid}:0"
    cb_files_v2(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("penv_"))
def cb_env_editor(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[5:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    safe_answer_cb(call)
    fp = os.path.join(p[4], ".env")
    content = ""
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            pass
    user_states[uid] = f"waiting_env_{pid}"
    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ إلغاء", f"pfiles_{pid}:0"))
    shown = content if content.strip() else "KEY=VALUE"
    txt = (
        f"{make_header('🔐 تعديل .env')}\n\n"
        f"📄 المحتوى الحالي (يُحفظ بأمان ولا يظهر في السجلات):\n<pre>{html.escape(shown[:1500])}</pre>\n\n"
        "👆 أرسل المحتوى الكامل الجديد لملف .env:"
    )
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_env_"))
def _do_env_save(msg):
    uid = msg.from_user.id
    pid = int(user_states.pop(uid)[len("waiting_env_"):])
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return
    content = msg.text or ""
    if len(content) > 10000:
        bot.reply_to(msg, "❌ المحتوى طويل جداً.")
        return
    try:
        with open(os.path.join(p[4], ".env"), "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        bot.reply_to(msg, render_error("فشل الحفظ", e), parse_mode="HTML")
        return
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_green("🔁 إعادة التشغيل", f"restart_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))
    bot.send_message(msg.chat.id, "✅ تم حفظ .env بنجاح. أعد تشغيل المشروع لتطبيق المتغيرات الجديدة.", reply_markup=m)


# ============================================================
# ➕ إضافة ملف / تثبيت مكتبة / التثبيت الذكي / حسابي / مساعدة / دعم / مشروع جديد
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("addfile_"))
def cb_addfile(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[8:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    safe_answer_cb(call)
    user_states[uid] = f"waiting_addfile_{pid}"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", f"proj_{pid}"))
    safe_edit_msg(
        f"{make_header('📤 إضافة ملف')}\n\n"
        "الأنواع المدعومة:\n<code>.py .txt .json .env .sh .html .css .js .yml .yaml .zip .xml .md</code>\n\n"
        "📤 أرسل الملف الآن",
        call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML"
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("addpkg_"))
def cb_addpkg(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[7:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    safe_answer_cb(call)
    user_states[uid] = f"waiting_pkg_{pid}"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", f"proj_{pid}"))
    safe_edit_msg(
        f"{make_header('⚙️ تثبيت مكتبة')}\n\n📝 أرسل اسم المكتبة\nمثال: <code>requests</code>",
        call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML"
    )


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_pkg_"))
def do_addpkg(msg):
    uid = msg.from_user.id
    try:
        pid = int(user_states.pop(uid).replace("waiting_pkg_", ""))
    except Exception:
        return
    pkg = (msg.text or "").strip()
    if not pkg:
        bot.reply_to(msg, "❌ اسم غير صالح")
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.reply_to(msg, "🚫 غير مصرح")
        return
    msg_wait = bot.reply_to(msg, f"⏳ جاري تثبيت: <code>{html.escape(pkg)}</code>...", parse_mode="HTML")

    def do():
        ok, m_txt = install_single_pkg(pid, pkg)
        txt = f"<b>✅ تم التثبيت</b>\n\n{m_txt}" if ok else f"<b>❌ فشل التثبيت</b>\n\n{m_txt}"
        mk = types.InlineKeyboardMarkup().add(btn_gray("🔙 رجوع للمشروع", f"proj_{pid}"))
        safe_edit_msg(txt, msg_wait.chat.id, msg_wait.message_id, reply_markup=mk, parse_mode="HTML")
        log_action(uid, "install_pkg", f"pid={pid} pkg={pkg} ok={ok}")

    threading.Thread(target=do, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data.startswith("smart_"))
def cb_smart(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[6:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    entry_path = safe_join_in_dir(p[4], p[5])
    detected = []
    if entry_path and p[5].endswith(".py") and os.path.exists(entry_path):
        try:
            detected = resolve_required_pkgs(entry_path)
        except Exception:
            detected = []
    for line in (p[7] or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line not in detected:
            detected.append(line)
    if not detected:
        return safe_answer_cb(call, "✅ لا توجد مكتبات خارجية مطلوبة لهذا المشروع", show_alert=True)

    safe_answer_cb(call, f"🪄 بدء التثبيت الذكي ({len(detected)} مكتبة)")
    init_items = [(f"<code>{html.escape(pkg)}</code> <i>(في الانتظار)</i>", False) for pkg in detected]
    safe_edit_msg(
        render_progress(f"🪄 التثبيت الذكي — {html.escape(p[2])}", "✅", "⏳", init_items,
                        footer="🧪 <i>جاري تجهيز البيئة المعزولة (.venv)...</i>"),
        call.message.chat.id, call.message.message_id, parse_mode="HTML"
    )

    def do_smart():
        update_project(pid, install_status="installing")
        ensure_venv(p[4])
        items = []
        ok_n = fail_n = 0
        for i, pkg in enumerate(detected, 1):
            cur_items = list(items) + [(f"<code>{html.escape(pkg)}</code> <b>(جاري التثبيت...)</b>", False)]
            safe_edit_msg(
                render_progress(f"🪄 التثبيت الذكي ({i}/{len(detected)})", "✅", "⚙️", cur_items,
                                footer="🛡️ <i>تثبيت معزول خاص بهذا المشروع فقط</i>"),
                call.message.chat.id, call.message.message_id, parse_mode="HTML"
            )
            ok, res_msg = pip_install_in(p[4], pkg)
            ok_n += int(ok)
            fail_n += int(not ok)
            items.append((res_msg, ok))
        update_project(pid, requirements="\n".join(detected), installed=1,
                       install_status="ok" if fail_n == 0 else ("partial" if ok_n > 0 else "failed"))
        footer = f"✅ نجح: <code>{ok_n}</code> · ❌ فشل: <code>{fail_n}</code> · 🛡️ البيئة: <code>معزولة</code>"
        final_txt = render_progress(f"🪄 نتيجة التثبيت الذكي — {html.escape(p[2])}", "✅", "❌", items, footer=footer)
        m = types.InlineKeyboardMarkup(row_width=2)
        m.add(btn_green("▶️ تشغيل المشروع", f"start_{pid}"))
        m.row(btn_blue("📄 السجلات", f"logs_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))
        safe_edit_msg(final_txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
        log_action(uid, "smart_install", f"pid={pid} ok={ok_n} fail={fail_n}")

    threading.Thread(target=do_smart, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data.startswith("install_"))
def cb_install(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[8:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    reqs = p[7] or ""
    lines = [l.strip() for l in reqs.split("\n") if l.strip() and not l.startswith("#")]
    if not lines:
        return safe_answer_cb(call, "⚠️ لا توجد مكتبات", show_alert=True)
    safe_answer_cb(call, f"⏳ بدء التثبيت ({len(lines)})")

    def do_install():
        update_project(pid, install_status="installing")
        prog_items, results = [], []
        success_count = fail_count = 0
        for i, pkg in enumerate(lines, 1):
            cur_view = list(prog_items) + [(f"<code>{html.escape(pkg)}</code> <b>(جاري التثبيت...)</b>", False)]
            safe_edit_msg(
                render_progress(f"⚙️ تثبيت المتطلبات ({i}/{len(lines)})", "✅", "⏳", cur_view),
                call.message.chat.id, call.message.message_id, parse_mode="HTML"
            )
            ok, m_txt = pip_install_in(p[4], pkg)
            if ok:
                success_count += 1
                results.append(f"✅ {m_txt}")
                prog_items.append((m_txt, True))
            else:
                fail_count += 1
                results.append(f"❌ {m_txt}")
                prog_items.append((m_txt, False))
        update_project(pid, installed=1, install_status="ok" if fail_count == 0 else ("partial" if success_count > 0 else "failed"))
        final = (
            f"{make_header('📊 نتيجة التثبيت')}\n\n"
            f"✅ <b>نجح:</b> <code>{success_count}</code> · ❌ <b>فشل:</b> <code>{fail_count}</code>\n\n"
            "<b><u>📋 التفاصيل:</u></b>\n" + "\n".join(results[:30])
        )
        m = types.InlineKeyboardMarkup(row_width=1)
        m.add(btn_green("▶️ تشغيل المشروع", f"start_{pid}"))
        m.add(btn_blue("📄 السجلات", f"logs_{pid}"))
        m.add(btn_gray("🔙 رجوع", f"proj_{pid}"))
        safe_edit_msg(final, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
        log_action(uid, "install_all", f"pid={pid} ok={success_count} fail={fail_count}")

    threading.Thread(target=do_install, daemon=True).start()


COLOR_OPTIONS = [
    ("green", "🟢 أخضر", "success"),
    ("red", "🔴 أحمر", "danger"),
    ("blue", "🔵 أزرق", "primary"),
    ("gray", "⚫ رمادي", None),
]


def get_user_theme(uid):
    u = get_user(uid)
    if not u or len(u) < 9:
        return "blue"
    return u[8] or "blue"


def make_color_keyboard(current="blue"):
    m = types.InlineKeyboardMarkup(row_width=2)
    btns = []
    for color_key, label, style in COLOR_OPTIONS:
        mark = "✅ " if color_key == current else ""
        btns.append(btn(f"{mark}{label}", f"set_theme_{color_key}", style=style if style else None))
    m.row(*btns[:2])
    m.row(*btns[2:4])
    return m


@bot.callback_query_handler(func=lambda c: c.data == "my_account")
def cb_account(call):
    safe_answer_cb(call)
    uid = call.from_user.id
    u = get_user(uid)
    projs = get_projects_by_owner(uid)
    running = sum(1 for p in projs if is_running(p[0]))
    limit = get_max_projects(uid)
    premium_badge = "⭐ Premium" if is_premium(uid) else "👤 عادي"
    theme = get_user_theme(uid)
    txt = (
        f"{make_header('👤 حسابي')}\n\n"
        f"🆔 <b>ID:</b> <code>{uid}</code>\n"
        f"👤 <b>الاسم:</b> {html.escape(call.from_user.first_name or '—')}\n"
        f"🏷️ <b>النوع:</b> {premium_badge}\n"
        f"🎨 <b>الثيم:</b> {theme}\n\n"
        "<b><u>📊 الإحصائيات:</u></b>\n"
        f"  📦 المشاريع: <code>{len(projs)}</code> / <code>{limit}</code>\n"
        f"  🟢 شغالة: <code>{running}</code>\n"
        f"  📤 الرفعات: <code>{u[7] if u and len(u) > 7 else 0}</code>\n"
    )
    m = types.InlineKeyboardMarkup()
    m.add(btn_blue("🎨 تخصيص الألوان", "my_theme"))
    m.add(btn_gray("🔙 رجوع", "main_menu"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "my_theme")
def cb_my_theme(call):
    safe_answer_cb(call)
    uid = call.from_user.id
    current = get_user_theme(uid)
    txt = f"<b>🎨 تخصيص الألوان</b>\n\n🎨 الثيم الحالي: <code>{current}</code>\n\nاختر اللون المفضل:"
    m = make_color_keyboard(current)
    m.add(btn_gray("🔙 رجوع", "my_account"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("set_theme_"))
def cb_set_theme(call):
    uid = call.from_user.id
    color = call.data.replace("set_theme_", "")
    if color not in [c[0] for c in COLOR_OPTIONS]:
        return safe_answer_cb(call, "لون غير صالح", show_alert=True)
    set_user_theme(uid, color)
    log_action(uid, "set_theme", color)
    safe_answer_cb(call, "✅ تم تغيير الثيم", show_alert=True)
    cb_my_theme(call)


@bot.callback_query_handler(func=lambda c: c.data == "help")
def cb_help(call):
    safe_answer_cb(call)
    txt = (
        f"{make_header('📚 المساعدة')}\n\n"
        "<b><u>📝 خطوات إنشاء مشروع:</u></b>\n"
        "<b>1.</b> اضغط <b>➕ مشروع جديد</b>\n"
        "<b>2.</b> اختر النوع (Python / Web / Script / Node.js / GitHub)\n"
        "<b>3.</b> أرسل اسم المشروع ثم الملف الرئيسي أو ZIP\n"
        "<b>4.</b> أرسل <code>requirements.txt</code> أو استخدم التثبيت الذكي\n"
        "<b>5.</b> اضغط <b>▶️ تشغيل</b>\n\n"
        "<b><u>🩺 التشخيص وإعادة التشغيل:</u></b>\n"
        "إذا توقف مشروعك استخدم <b>🩺 تشخيص الأعطال</b> لمعرفة السبب بدقة.\n\n"
        f"<b><u>📞 التواصل:</u></b>\n👤 المطور: @{DEVELOPER_USERNAME}\n"
        f"<i>{get_ui('msg_help_extra')}</i>"
    )
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_blue("👤 المطور", url=f"https://t.me/{DEVELOPER_USERNAME}"))
    m.add(btn_gray("🔙 رجوع", "main_menu"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "contact_support")
def cb_contact_support(call):
    safe_answer_cb(call)
    uid = call.from_user.id
    user_states[uid] = "waiting_support_msg"
    txt = f"{make_header('💬 التواصل مع الدعم')}\n\n{get_ui('msg_support_prompt')}"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "main_menu"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_support_msg")
def do_support_msg(msg):
    uid = msg.from_user.id
    user = get_user(uid)
    name = html.escape(user[2] if user and user[2] else "مستخدم")
    user_states.pop(uid, None)
    support_txt = (
        f"{make_header('💬 رسالة دعم جديدة')}\n\n"
        f"👤 <b>من:</b> {name}\n"
        f"🆔 <b>ID:</b> <code>{uid}</code>\n"
        f"🔗 <b>يوزر:</b> @{msg.from_user.username or 'None'}\n\n"
        f"📝 <b>الرسالة:</b>\n{html.escape(msg.text or '')}"
    )
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, support_txt, parse_mode="HTML")
        except Exception:
            pass
    bot.reply_to(msg, "✅ <b>تم إرسال رسالتك للمطور</b>\n\nسيتم الرد عليك قريباً", parse_mode="HTML")
    log_action(uid, "support_message", (msg.text or "")[:100])


@bot.callback_query_handler(func=lambda c: c.data == "new_proj")
def cb_new(call):
    uid = call.from_user.id
    ok, count, limit = check_user_limit(uid)
    if not ok:
        if is_paid_mode():
            safe_answer_cb(call)
            cur = get_ui("currency_name") or "$"
            bal = get_balance(uid)
            sp = get_ui("slot_price") or "2.0"
            txt_lim = (
                f"{make_header('💎 الترقية أو شراء خانة مشروع')}\n\n"
                f"📦 <b>مشاريعك الحالية:</b> <code>{count}/{limit}</code>\n"
                f"💳 <b>رصيدك:</b> <code>{bal:.2f} {cur}</code>\n"
                f"🏷 <b>سعر الخانة الإضافية:</b> <code>{sp} {cur}</code>"
            )
            mlim = types.InlineKeyboardMarkup(row_width=1)
            mlim.add(btn_green(f"🛒 شراء خانة مشروع ({sp} {cur})", "shop_buy_slot"))
            mlim.row(btn_blue("💳 شحن الرصيد", "wallet_recharge"), btn_blue("🤝 دعوة أصدقاء", "referral_menu"))
            mlim.add(btn_gray("🔙 رجوع", "main_menu"))
            safe_edit_msg(txt_lim, call.message.chat.id, call.message.message_id, reply_markup=mlim, parse_mode="HTML")
            return
        return safe_answer_cb(call, f"⚠️ وصلت الحد الأقصى ({count}/{limit})", show_alert=True)

    safe_answer_cb(call)
    txt = f"{make_header('➕ مشروع جديد')}\n\n📊 <b>مشاريعك:</b> <code>{count}</code> / <code>{limit}</code>\n\n🎯 <b>اختر نوع الخدمة:</b>"
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_green("🐍 بوت Python", "type_bot"), btn_blue("🌐 موقع Web", "type_web"))
    m.row(btn_blue("📜 سكربت Python", "type_script"), btn_blue("🟨 بوت Node.js", "type_node"))
    m.add(btn_green("🐙 استيراد من GitHub", "new_github"))
    m.add(btn_gray("❌ إلغاء", "main_menu"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("type_"))
def cb_type(call):
    safe_answer_cb(call)
    ptype = call.data[5:]
    uid = call.from_user.id
    user_states[uid] = f"waiting_name_{ptype}"
    txt = (
        f"{make_header('📝 اسم المشروع')}\n\n"
        f"🏷️ <b>النوع:</b> {type_label(ptype)}\n\n"
        "<b><u>📝 الخطوة 1/3</u></b>\n"
        "📤 أرسل اسم المشروع (بدون مسافات وبالانجليزي أفضل)"
    )
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "main_menu"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_name_"))
def msg_name(msg):
    uid = msg.from_user.id
    ptype = user_states[uid].replace("waiting_name_", "")
    name = safe_project_name(msg.text)
    if not name:
        bot.reply_to(msg, "❌ اسم غير صالح — استخدم حروفاً أو أرقاماً.")
        return
    projs = get_projects_by_owner(uid)
    if any(p[2] == name for p in projs):
        bot.reply_to(msg, "⚠️ الاسم موجود بالفعل - اختر اسماً آخر.")
        return
    user_states[uid] = f"waiting_file_{ptype}_{name}"
    txt = (
        f"{make_header('📤 رفع الملف الرئيسي')}\n\n"
        f"🏷️ <b>النوع:</b> {type_label(ptype)}\n"
        f"✅ <b>الاسم:</b> <code>{html.escape(name)}</code>\n\n"
        "<b><u>📝 الخطوة 2/3</u></b>\n"
        f"📤 أرسل الملف الرئيسي ({'<code>.js</code>' if ptype == 'node' else '<code>.py</code>'}) أو أرشيف <code>.zip</code>"
    )
    bot.reply_to(msg, txt, parse_mode="HTML")


def is_allowed_file(filename):
    base = os.path.basename(filename or "")
    ext = os.path.splitext(base)[1].lower()
    return ext in ALLOWED_EXTS or base in ALLOWED_FILENAMES


def notify_admins_project_approval(pid, owner_uid, tg_user, file_path, extra_filename=None):
    p = get_project(pid)
    if not p:
        return
    uname = f"@{tg_user.username}" if getattr(tg_user, "username", None) else "لا يوجد"
    fname = html.escape(getattr(tg_user, "first_name", None) or "مستخدم")
    title = "📎 ملف إضافي لمشروع" if extra_filename else "📥 طلب تشغيل ملف جديد"
    shown_file = extra_filename or p[5]
    cap = (
        f"{make_header(title)}\n\n"
        f"👤 <b>المرسل:</b> {fname} ({uname})\n"
        f"🆔 <b>الآيدي:</b> <code>{owner_uid}</code>\n"
        f"📦 <b>المشروع:</b> <code>{html.escape(p[2])}</code> (ID: <code>{pid}</code>)\n"
        f"🏷️ <b>النوع:</b> {type_label(p[3])}\n"
        f"📄 <b>الملف:</b> <code>{html.escape(shown_file)}</code>\n"
        f"📊 <b>الحجم:</b> <code>{get_file_size_str(file_path)}</code>"
    )
    am = types.InlineKeyboardMarkup(row_width=2)
    am.row(btn_green("✅ قبول التشغيل", f"adm_proj_ok_{pid}"), btn_red("🛑 إيقاف / رفض", f"adm_proj_no_{pid}"))
    send_path = file_path
    tmp_zip = None
    try:
        all_files = list_dir_files(p[4])
        if not extra_filename and len(all_files) > 1 and os.path.isdir(p[4]):
            fd, tmp_zip = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            zip_project_dir(p[4], tmp_zip)
            if os.path.getsize(tmp_zip) <= TG_UPLOAD_LIMIT:
                send_path = tmp_zip
    except Exception:
        pass
    for aid in ADMIN_IDS:
        try:
            if os.path.isfile(send_path) and os.path.getsize(send_path) <= TG_UPLOAD_LIMIT:
                with open(send_path, "rb") as f:
                    vname = f"{owner_uid}_{os.path.basename(send_path)}" if tmp_zip else os.path.basename(send_path)
                    bot.send_document(aid, f, caption=cap, parse_mode="HTML", reply_markup=am, visible_file_name=vname)
            else:
                bot.send_message(aid, cap, parse_mode="HTML", reply_markup=am)
        except Exception as e:
            print(f"[ApprovalNotify] {e}")
    if tmp_zip:
        try:
            os.remove(tmp_zip)
        except Exception:
            pass


@bot.message_handler(content_types=['document'])
def handle_doc(msg):
    uid = msg.from_user.id
    state = user_states.get(uid, "")
    if is_banned(uid):
        return

    if state.startswith("waiting_file_"):
        parts = state.replace("waiting_file_", "").split("_", 1)
        ptype = parts[0]
        name = safe_project_name(parts[1] if len(parts) > 1 else "proj") or "proj"
        filename = os.path.basename(msg.document.file_name or "unknown")
        ext = os.path.splitext(filename)[1].lower()

        if not is_allowed_file(filename):
            return bot.send_message(msg.chat.id, f"❌ نوع غير مدعوم: <code>{html.escape(ext)}</code>", parse_mode="HTML")
        if msg.document.file_size and msg.document.file_size > MAX_FILE_SIZE:
            return bot.send_message(msg.chat.id, "❌ الملف كبير جداً (الحد 20 MB)", parse_mode="HTML")

        pdir = safe_join_in_dir(PROJECTS_DIR, f"{uid}_{name}")
        if not pdir:
            return bot.send_message(msg.chat.id, "❌ اسم مشروع غير آمن")

        try:
            fi = bot.get_file(msg.document.file_id)
            data = bot.download_file(fi.file_path)
            os.makedirs(pdir, exist_ok=True)

            if ext == ".zip":
                zip_path = os.path.join(pdir, filename)
                with open(zip_path, "wb") as f:
                    f.write(data)
                try:
                    safe_extract_zip(zip_path, pdir, max_total_bytes=MAX_PROJECT_SIZE_MB * 1024 * 1024)
                    os.remove(zip_path)
                    top_items = [x for x in os.listdir(pdir) if x != "__MACOSX"]
                    if len(top_items) == 1 and os.path.isdir(os.path.join(pdir, top_items[0])):
                        inner_dir = os.path.join(pdir, top_items[0])
                        for sub in os.listdir(inner_dir):
                            shutil.move(os.path.join(inner_dir, sub), os.path.join(pdir, sub))
                        shutil.rmtree(inner_dir, ignore_errors=True)
                    all_files = list_dir_files(pdir)
                    det_ptype, entry = detect_entry_and_type(pdir, ptype)
                    if not entry:
                        shutil.rmtree(pdir, ignore_errors=True)
                        return bot.send_message(msg.chat.id, "❌ لم يتم العثور على ملف تشغيل صالح (.py أو .js) داخل الـ ZIP")
                    ptype = det_ptype or ptype
                    bot.send_message(msg.chat.id, f"📦 تم فك الـ ZIP بأمان ({len(all_files)} ملف)\n🎯 الملف الرئيسي: <code>{html.escape(entry)}</code>", parse_mode="HTML")
                except Exception as e:
                    shutil.rmtree(pdir, ignore_errors=True)
                    return bot.send_message(msg.chat.id, f"❌ خطأ ZIP: <code>{html.escape(str(e)[:200])}</code>", parse_mode="HTML")
            else:
                entry = filename
                fp = safe_join_in_dir(pdir, entry)
                if not fp:
                    shutil.rmtree(pdir, ignore_errors=True)
                    return bot.send_message(msg.chat.id, "❌ اسم ملف غير آمن")
                with open(fp, "wb") as f:
                    f.write(data)

            entry_path = safe_join_in_dir(pdir, entry)
            ok, chk = check_any_file(entry_path)
            if not ok:
                shutil.rmtree(pdir, ignore_errors=True)
                return bot.send_message(msg.chat.id, f"⚠️ <b>فشل الفحص المسبق</b>\n\n{chk}\n\n📤 أرسل ملفاً معدلاً", parse_mode="HTML")

            info = extract_bot_info_project(pdir)
            pid = add_project(uid, name, ptype, pdir, entry)
            add_project_file(pid, entry)
            _trusted_now = is_trusted_user(uid)
            set_project_approved(pid, 1 if _trusted_now else 0)
            backup_project_to_storage(pid)

            if info.get("token"):
                update_project(pid, bot_token=info["token"], bot_username=info["username"] or "", bot_name=info["name"] or "")

            with db_conn() as conn:
                conn.execute("UPDATE users SET total_uploads = total_uploads + 1 WHERE user_id=?", (uid,))
            log_action(uid, "upload_file", f"pid={pid} file={entry}")
            if not _trusted_now:
                notify_admins_project_approval(pid, uid, msg.from_user, entry_path)

            txt = (
                f"{make_header('✅ تم رفع الملف')}\n\n"
                f"📦 <b>المشروع:</b> <code>{html.escape(name)}</code>\n"
                f"📄 <b>الملف:</b> <code>{html.escape(entry)}</code>\n"
                f"📊 <b>الحجم:</b> <code>{get_file_size_str(entry_path)}</code>\n"
                f"🔍 <b>الفحص:</b> ✅ سليم\n\n"
            )
            if info.get("username"):
                txt += f"🤖 <b>البوت المكتشف:</b> @{info['username']}\n\n"
            if not _trusted_now:
                txt += "⏳ <b>الموافقة:</b> تم إرسال الملف للإدارة للمراجعة قبل التشغيل.\n\n"
            txt += "هل عندك ملف <code>requirements.txt</code>؟"
            user_states[uid] = f"waiting_req_{pid}"
            m = types.InlineKeyboardMarkup()
            m.row(btn_green("✅ نعم، سأرفعه", f"upload_req_{pid}"), btn_blue("⏭️ تخطي", f"proj_{pid}"))
            bot.send_message(msg.chat.id, txt, parse_mode="HTML", reply_markup=m)
        except Exception as e:
            bot.send_message(msg.chat.id, f"❌ خطأ: <code>{html.escape(redact_secrets(str(e))[:250])}</code>", parse_mode="HTML")

    elif state.startswith("waiting_req_"):
        try:
            pid = int(state.replace("waiting_req_", ""))
        except Exception:
            return
        p = get_project(pid)
        if not p or not can_access_project(p, uid):
            return bot.reply_to(msg, "🚫 غير مصرح")
        try:
            fi = bot.get_file(msg.document.file_id)
            data = bot.download_file(fi.file_path)
            rp = os.path.join(p[4], "requirements.txt")
            with open(rp, "wb") as f:
                f.write(data)
            with open(rp, "r", encoding="utf-8", errors="ignore") as f:
                reqs = f.read().strip()
            if not reqs:
                return bot.send_message(msg.chat.id, "⚠️ الملف فارغ")
            update_project(pid, requirements=reqs)
            add_project_file(pid, "requirements.txt")
            user_states.pop(uid, None)
            m = types.InlineKeyboardMarkup()
            m.row(btn_green("⚙️ نعم، ثبّت", f"install_{pid}"), btn_blue("⏭️ تخطي", f"proj_{pid}"))
            bot.send_message(msg.chat.id, f"{make_header('📋 requirements.txt')}\n\n<pre>{html.escape(reqs[:600])}</pre>\n\nهل تريد تثبيت المكتبات الآن؟", parse_mode="HTML", reply_markup=m)
        except Exception as e:
            bot.reply_to(msg, f"❌ خطأ: <code>{html.escape(str(e)[:200])}</code>", parse_mode="HTML")

    elif state.startswith("waiting_addfile_"):
        try:
            pid = int(state.replace("waiting_addfile_", ""))
        except Exception:
            return
        p = get_project(pid)
        if not p or not can_access_project(p, uid):
            return bot.reply_to(msg, "🚫 غير مصرح")
        filename = os.path.basename(msg.document.file_name or "unknown")
        if not is_allowed_file(filename):
            return bot.send_message(msg.chat.id, f"❌ نوع غير مدعوم: <code>{html.escape(filename)}</code>", parse_mode="HTML")
        if get_dir_size_bytes(p[4]) + (msg.document.file_size or 0) > MAX_PROJECT_SIZE_MB * 1024 * 1024:
            return bot.send_message(msg.chat.id, f"❌ يتجاوز الحد الأقصى لحجم المشروع ({MAX_PROJECT_SIZE_MB} MB).")
        fp = safe_join_in_dir(p[4], filename)
        if not fp:
            return bot.send_message(msg.chat.id, "❌ اسم ملف غير آمن")
        try:
            fi = bot.get_file(msg.document.file_id)
            data = bot.download_file(fi.file_path)
            with open(fp, "wb") as f:
                f.write(data)
            ok, chk = check_any_file(fp)
            add_project_file(pid, filename)
            user_states.pop(uid, None)
            _trusted_add = is_trusted_user(uid)
            if not _trusted_add:
                if is_running(pid):
                    stop_project(pid, reason="تحديث ملف بانتظار المراجعة")
                set_project_approved(pid, 0)
                notify_admins_project_approval(pid, uid, msg.from_user, fp, extra_filename=filename)
            txt = f"<b>✅ تم إضافة الملف</b>\n\n📄 <code>{html.escape(filename)}</code> ({get_file_size_str(fp)})\n"
            txt += "🔍 الفحص: ✅ سليم" if ok else f"⚠️ <b>تحذير:</b>\n{chk}"
            if not _trusted_add:
                txt += "\n\n⏳ تم إرسال الملف المضاف للإدارة للمراجعة."
            m = types.InlineKeyboardMarkup().add(btn_gray("🔙 رجوع", f"proj_{pid}"))
            bot.send_message(msg.chat.id, txt, parse_mode="HTML", reply_markup=m)
            log_action(uid, "add_file", f"pid={pid} file={filename}")
        except Exception as e:
            bot.reply_to(msg, f"❌ خطأ: <code>{html.escape(str(e)[:200])}</code>", parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("upload_req_"))
def cb_upload_req(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[11:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    safe_answer_cb(call)
    user_states[uid] = f"waiting_req_{pid}"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", f"proj_{pid}"))
    safe_edit_msg("<b>📤 رفع requirements.txt</b>\n\n📋 أرسل ملف requirements.txt الآن.", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("del_"))
def cb_del(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[4:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    safe_answer_cb(call)
    txt = f"{make_header('⚠️ تأكيد الحذف')}\n\n📦 <b>المشروع:</b> <code>{html.escape(p[2])}</code>\n⚠️ هذا الإجراء لا يمكن التراجع عنه"
    m = types.InlineKeyboardMarkup()
    m.row(btn_red("✅ نعم، احذف", f"cdel_{pid}"), btn_green("❌ إلغاء", f"proj_{pid}"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("cdel_"))
def cb_cdel(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[5:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    stop_project(pid, reason="حذف المشروع")
    if p[4] and os.path.realpath(p[4]).startswith(os.path.realpath(PROJECTS_DIR) + os.sep):
        shutil.rmtree(p[4], ignore_errors=True)
    delete_project(pid)
    log_action(uid, "delete_project", f"pid={pid} name={p[2]}")
    safe_answer_cb(call, "✅ تم الحذف")
    call.data = "my_projs"
    cb_my_projs(call)


# ============================================================
# 🐙 GitHub Import + 💾 إدارة الجلسات + 📊 مراقبة السيرفر
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "new_github")
def cb_new_github(call):
    uid = call.from_user.id
    ok, count, limit = check_user_limit(uid)
    if not ok:
        return safe_answer_cb(call, f"⚠️ وصلت للحد الأقصى ({count}/{limit})", show_alert=True)
    safe_answer_cb(call)
    user_states[uid] = "waiting_github_repo"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "main_menu"))
    safe_edit_msg(
        f"{make_header('🐙 استيراد مشروع من GitHub')}\n\n"
        "أرسل رابط المستودع العام الآن (مثال: <code>https://github.com/user/repo</code> أو <code>user/repo</code>).",
        call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML"
    )


@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id) == "waiting_github_repo")
def do_github_clone(msg):
    uid = msg.from_user.id
    user_states.pop(uid, None)
    repo = (msg.text or "").strip()
    if not repo.startswith("http"):
        repo = f"https://github.com/{repo.lstrip('/')}"
    if not re.match(r'^https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(\.git)?/?$', repo):
        return bot.reply_to(msg, "❌ يرجى إرسال رابط GitHub صحيح.")
    name = safe_project_name(repo.rstrip("/").split("/")[-1].replace(".git", "")) or "github_proj"
    pdir = os.path.join(PROJECTS_DIR, f"{uid}_{name}_{secrets.token_hex(2)}")
    wait = bot.reply_to(msg, "🐙 جاري سحب المستودع عبر <code>git clone</code>...", parse_mode="HTML")

    def _clone_bg():
        try:
            res = subprocess.run(["git", "clone", "--depth", "1", repo, pdir], capture_output=True, text=True, timeout=90)
            if res.returncode != 0:
                shutil.rmtree(pdir, ignore_errors=True)
                return safe_edit_msg(f"❌ فشل الاستنساخ:\n<pre>{html.escape(redact_secrets(res.stderr)[:400])}</pre>", msg.chat.id, wait.message_id, parse_mode="HTML")
            ptype, entry = detect_entry_and_type(pdir)
            if not entry:
                shutil.rmtree(pdir, ignore_errors=True)
                return safe_edit_msg("❌ لم يتم العثور على ملف تشغيل (.py أو .js) في المستودع.", msg.chat.id, wait.message_id)
            pid = add_project(uid, name, ptype, pdir, entry)
            add_project_file(pid, entry)
            info = extract_bot_info_project(pdir)
            if info.get("token"):
                update_project(pid, bot_token=info["token"], bot_username=info.get("username") or "", bot_name=info.get("name") or "")
            trusted = is_trusted_user(uid)
            set_project_approved(pid, 1 if trusted else 0)
            backup_project_to_storage(pid)
            if not trusted:
                notify_admins_project_approval(pid, uid, msg.from_user, os.path.join(pdir, entry))
            m = types.InlineKeyboardMarkup(row_width=1).add(btn_green("▶️ فتح لوحة المشروع", f"proj_{pid}"))
            safe_edit_msg(
                f"✅ <b>تم استيراد المشروع من GitHub!</b>\n\n"
                f"📦 الاسم: <code>{html.escape(name)}</code>\n🏷️ النوع: {type_label(ptype)}\n📄 الملف: <code>{html.escape(entry)}</code>\n"
                + ("⏳ بانتظار موافقة المطور للتشغيل." if not trusted else "🚀 جاهز للتشغيل الفوري!"),
                msg.chat.id, wait.message_id, reply_markup=m, parse_mode="HTML"
            )
        except Exception as e:
            shutil.rmtree(pdir, ignore_errors=True)
            safe_edit_msg(f"❌ خطأ: <code>{html.escape(str(e)[:250])}</code>", msg.chat.id, wait.message_id, parse_mode="HTML")

    threading.Thread(target=_clone_bg, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data.startswith(("sess_save_", "sess_reset_")))
def cb_session_manage(call):
    uid = call.from_user.id
    is_reset = call.data.startswith("sess_reset_")
    try:
        pid = int(call.data.split("_")[-1])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    if not is_reset:
        zpath = os.path.join(SESSIONS_BACKUP_DIR, f"sess_{pid}.zip")
        cnt = 0
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(p[4]):
                dirs[:] = [d for d in dirs if d not in (".venv", "node_modules", ".git")]
                for fn in files:
                    if any(k in fn.lower() or k in root.lower() for k in ("session", "auth", "creds.json", ".wwebjs")):
                        if fn not in ("package.json", "package-lock.json"):
                            full = os.path.join(root, fn)
                            zf.write(full, os.path.relpath(full, p[4]))
                            cnt += 1
        if cnt == 0:
            return safe_answer_cb(call, "⚠️ لم يتم العثور على ملفات جلسة", show_alert=True)
        safe_answer_cb(call, f"💾 تم حفظ {cnt} ملف جلسة!")
        with open(zpath, "rb") as f:
            bot.send_document(call.message.chat.id, f, caption=f"💾 نسخة جلسة {html.escape(p[2])}", visible_file_name=f"{p[2]}_session.zip")
    else:
        stop_project(pid, reason="تصفير الجلسة")
        removed = 0
        for root, dirs, files in os.walk(p[4]):
            for d in list(dirs):
                if any(k in d.lower() for k in ("auth_info", "session", ".wwebjs")):
                    shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                    removed += 1
            for fn in files:
                if fn.endswith((".session", ".session-journal")) or fn == "creds.json":
                    try:
                        os.remove(os.path.join(root, fn))
                        removed += 1
                    except Exception:
                        pass
        safe_answer_cb(call, f"🔄 تم إعادة تعيين الجلسة ({removed} عنصر)", show_alert=True)


@bot.callback_query_handler(func=lambda c: c.data == "server_monitor")
def cb_server_monitor(call):
    safe_answer_cb(call)
    uid = call.from_user.id
    sm = get_server_metrics()
    projs = get_all_projects() if is_admin(uid) else get_projects_by_owner(uid)
    run = [p for p in projs if is_running(p[0])]
    up = calc_uptime(START_TIME.strftime("%Y-%m-%d %H:%M:%S"))
    box = (
        f"🧠 <b>المعالج</b>  <code>{sm['cpu']}%</code>\n{_bar(sm['cpu'])}\n\n"
        f"💾 <b>الذاكرة</b>  <code>{sm['ram_used']}/{sm['ram_total']} GB</code> · <code>{sm['ram_pct']}%</code>\n{_bar(sm['ram_pct'])}\n\n"
        f"💿 <b>القرص</b>  <code>{sm['disk_used']}/{sm['disk_total']} GB</code> · <code>{sm['disk_pct']}%</code>\n{_bar(sm['disk_pct'])}"
    )
    txt = (
        f"{make_header('📊 مراقبة السيرفر الحية')}\n"
        f"<blockquote>{box}</blockquote>\n"
        f"<blockquote>🟢 النشطة: <code>{len(run)}/{len(projs)}</code>\n⏱ التشغيل: <code>{up}</code></blockquote>"
    )
    if run:
        lines = []
        for p in run[:8]:
            cpu, mem = get_proc_metrics(p[14])
            lines.append(f"• <b>{html.escape(p[2])}</b> — RAM <code>{mem} MB</code> · CPU <code>{cpu}%</code>")
        txt += "\n<blockquote expandable>" + "\n".join(lines) + "</blockquote>"
    txt += f"\n<i>🛡 حد كل مشروع: {MAX_RAM_MB}MB رام</i>"
    m = types.InlineKeyboardMarkup()
    m.row(btn_blue("🔄 تحديث", "server_monitor"), btn_gray("🔙 رجوع", "main_menu"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


# ============================================================
# 💳 المحفظة + الشحن + الإحالات + الباقات
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "wallet_menu")
def cb_wallet(call):
    safe_answer_cb(call)
    uid = call.from_user.id
    if not get_user(uid):
        add_user(uid, call.from_user.username or "", call.from_user.first_name or "")
    u = get_user(uid)
    cur = get_ui("currency_name") or "$"
    bal = get_balance(uid)
    count = count_user_projects(uid)
    limit = get_max_projects(uid)
    refs = u[11] if u and len(u) > 11 and u[11] else 0
    mode_txt = "💎 مدفوع (شحن / باقات)" if is_paid_mode() else "🆓 مجاني مفتوح"
    badge = "⭐ VIP" if is_premium(uid) else "👤 عادي"
    txt = (
        f"{make_header('💳 محفظتي والشحن')}\n\n"
        f"⚙️ <b>وضع البوت:</b> {mode_txt}\n"
        f"🏷 <b>حسابك:</b> {badge}\n"
        f"💰 <b>الرصيد الحالي:</b> <code>{bal:.2f} {cur}</code>\n"
        f"📦 <b>خانات المشاريع:</b> <code>{count}/{limit}</code>\n"
        f"🤝 <b>عدد إحالاتك:</b> <code>{refs}</code>\n"
    )
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_green("💳 شحن الرصيد", "wallet_recharge"), btn_blue("🎟 كود شحن", "wallet_redeem"))
    m.row(btn_blue("🛒 متجر الباقات", "shop_menu"), btn_green("🤝 نظام الإحالة", "referral_menu"))
    m.add(btn_gray("🔙 رجوع", "main_menu"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "wallet_recharge")
def cb_wallet_recharge(call):
    safe_answer_cb(call)
    uid = call.from_user.id
    user_states[uid] = "waiting_recharge_amount"
    cur = get_ui("currency_name") or "$"
    txt = f"{make_header('💳 شحن الرصيد')}\n\n{get_ui('recharge_info')}\n\n📤 أرسل الآن المبلغ (بـ {cur})، مثال: <code>5</code>"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "wallet_menu"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_recharge_amount")
def do_recharge_amount(msg):
    uid = msg.from_user.id
    try:
        amt = float((msg.text or "").strip().replace(",", "."))
        if amt <= 0 or amt > 100000:
            raise ValueError()
    except Exception:
        return bot.reply_to(msg, "❌ أرسل رقماً صحيحاً أكبر من 0.")
    user_states[uid] = f"waiting_recharge_proof_{amt}"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "wallet_menu"))
    bot.reply_to(msg, "📸 أرسل الآن إثبات الدفع (رقم الحوالة/المعاملة أو لقطة الشاشة):", reply_markup=m)


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_recharge_proof_"),
                     content_types=['text', 'photo', 'document'])
def do_recharge_proof(msg):
    uid = msg.from_user.id
    amt = float(user_states.pop(uid)[len("waiting_recharge_proof_"):])
    proof = (msg.text or msg.caption or "مرفق إثبات دفع").strip()[:400]
    rid = create_recharge_request(uid, amt, proof)
    cur = get_ui("currency_name") or "$"
    adm_txt = (
        f"{make_header('💳 طلب شحن جديد')}\n\n"
        f"🆔 <b>الطلب:</b> <code>#{rid}</code>\n"
        f"👤 <b>المستخدم:</b> <code>{uid}</code> (@{msg.from_user.username or 'None'})\n"
        f"💰 <b>المبلغ:</b> <code>{amt:.2f} {cur}</code>\n"
        f"📝 <b>الإثبات:</b> {html.escape(proof)}"
    )
    am = types.InlineKeyboardMarkup(row_width=2)
    am.row(btn_green("✅ قبول الشحن", f"adm_rech_ok_{rid}"), btn_red("❌ رفض", f"adm_rech_no_{rid}"))
    for aid in ADMIN_IDS:
        try:
            if msg.photo:
                bot.send_photo(aid, msg.photo[-1].file_id, caption=adm_txt, parse_mode="HTML", reply_markup=am)
            else:
                bot.send_message(aid, adm_txt, parse_mode="HTML", reply_markup=am)
        except Exception:
            pass
    bot.reply_to(msg, f"✅ تم إرسال طلب الشحن <code>#{rid}</code> بمبلغ <code>{amt:.2f} {cur}</code> للمراجعة.", parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "wallet_redeem")
def cb_wallet_redeem(call):
    safe_answer_cb(call)
    user_states[call.from_user.id] = "waiting_gift_code"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "wallet_menu"))
    safe_edit_msg(f"{make_header('🎟 استخدام كود شحن')}\n\nأرسل الكود الآن:", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_gift_code")
def do_redeem_code(msg):
    uid = msg.from_user.id
    user_states.pop(uid, None)
    ok, res = redeem_gift_code(uid, msg.text or "")
    cur = get_ui("currency_name") or "$"
    if ok:
        bot.reply_to(msg, f"🎉 تم شحن <code>{res:.2f} {cur}</code> إلى محفظتك!\n💳 رصيدك: <code>{get_balance(uid):.2f} {cur}</code>", parse_mode="HTML")
    else:
        bot.reply_to(msg, res)


@bot.callback_query_handler(func=lambda c: c.data == "shop_menu")
def cb_shop(call):
    safe_answer_cb(call)
    uid = call.from_user.id
    cur = get_ui("currency_name") or "$"
    bal = get_balance(uid)
    sp = float(get_ui("slot_price") or 2.0)
    vp = float(get_ui("vip_price") or 5.0)
    txt = (
        f"{make_header('🛒 متجر الباقات')}\n\n"
        f"💳 <b>رصيدك:</b> <code>{bal:.2f} {cur}</code>\n\n"
        f"1️⃣ <b>خانة مشروع إضافية (+1):</b> <code>{sp:.2f} {cur}</code>\n"
        f"2️⃣ <b>ترقية VIP (+5 مشاريع + شارة ⭐):</b> <code>{vp:.2f} {cur}</code>"
    )
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_green(f"📦 شراء خانة مشروع ({sp:.2f} {cur})", "shop_buy_slot"))
    m.add(btn_blue(f"⭐ ترقية VIP ({vp:.2f} {cur})", "shop_buy_vip"))
    m.add(btn_gray("🔙 رجوع", "wallet_menu"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data in ("shop_buy_slot", "shop_buy_vip"))
def cb_shop_buy(call):
    uid = call.from_user.id
    cur = get_ui("currency_name") or "$"
    bal = get_balance(uid)
    if call.data == "shop_buy_slot":
        price = float(get_ui("slot_price") or 2.0)
        if bal < price:
            return safe_answer_cb(call, f"❌ رصيدك غير كافٍ ({bal:.2f}/{price:.2f} {cur})", show_alert=True)
        add_balance(uid, -price)
        add_max_projects(uid, 1)
        log_action(uid, "buy_slot", f"price={price}")
        safe_answer_cb(call, "✅ تم شراء خانة مشروع إضافية!", show_alert=True)
    else:
        price = float(get_ui("vip_price") or 5.0)
        if bal < price:
            return safe_answer_cb(call, f"❌ رصيدك غير كافٍ ({bal:.2f}/{price:.2f} {cur})", show_alert=True)
        add_balance(uid, -price)
        add_max_projects(uid, 5)
        with db_conn() as conn:
            conn.execute("UPDATE users SET is_premium=1 WHERE user_id=?", (uid,))
        log_action(uid, "buy_vip", f"price={price}")
        safe_answer_cb(call, "⭐ مبروك! تمت ترقيتك إلى VIP (+5 مشاريع)", show_alert=True)
    cb_wallet(call)


@bot.callback_query_handler(func=lambda c: c.data == "referral_menu")
def cb_referral(call):
    safe_answer_cb(call)
    uid = call.from_user.id
    u = get_user(uid)
    refs = u[11] if u and len(u) > 11 and u[11] else 0
    bonus = get_ui("referral_bonus") or "0.5"
    cur = get_ui("currency_name") or "$"
    try:
        bot_user = bot.get_me().username
    except Exception:
        bot_user = "HostBot"
    ref_link = f"https://t.me/{bot_user}?start=ref_{uid}"
    txt = (
        f"{make_header('🤝 نظام الإحالة')}\n\n"
        f"🎁 <b>المكافأة لكل صديق:</b> <code>{bonus} {cur}</code>\n"
        f"👥 <b>إجمالي إحالاتك:</b> <code>{refs}</code>\n\n"
        f"🔗 <b>رابط إحالتك الخاص:</b>\n<code>{ref_link}</code>"
    )
    m = types.InlineKeyboardMarkup()
    m.add(btn_blue("📤 مشاركة الرابط", url=f"https://t.me/share/url?url={ref_link}"))
    m.add(btn_gray("🔙 رجوع", "wallet_menu"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


# ============================================================
# 👑 لوحة المطور + النسخ الاحتياطي + التخزين + التخصيص
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "admin_panel")
def cb_admin(call):
    uid = call.from_user.id
    if not is_admin(uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    safe_answer_cb(call)
    user_states.pop(uid, None)

    projs = get_all_projects()
    running = sum(1 for p in projs if is_running(p[0]))
    banned_count = sum(1 for u in get_all_users_full() if u[4] == 1)

    txt = (
        f"{make_header('👑 لوحة المطور — HOST BOT v13')}\n\n"
        f"  👥 المستخدمين: <code>{get_user_count()}</code> · 🚫 المحظورين: <code>{banned_count}</code>\n"
        f"  📦 المشاريع: <code>{len(projs)}</code> · 🟢 شغالة: <code>{running}</code>\n"
        f"  ⏱️ Uptime: <code>{calc_uptime(START_TIME.strftime('%Y-%m-%d %H:%M:%S'))}</code>\n"
        f"  🌐 Tunnel: <code>{TUNNEL_URL or 'غير متصل'}</code>\n"
    )
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_red("📢 إعلان", "broadcast"), btn_blue("📊 إحصائيات", "admin_stats"))
    m.row(btn_blue("👥 المستخدمين", "admin_users"), btn_blue("📦 المشاريع", "admin_projs"))
    m.row(btn_blue("🔗 Tunnel", "admin_tunnel"), btn_green("🌐 ويب المراقبة", "admin_demo_web"))
    m.row(btn_blue("📥 تخزين المطوّر", "admin_storage_0"), btn_blue("💳 المدفوعات والباقات", "admin_billing"))
    m.row(btn_blue("📋 سجل الأحداث", "admin_logs"), btn_green("🔓 فك حظر", "admin_unban"))
    m.row(btn_green("✅ مستخدمين بدون موافقة", "admin_trusted_users"), btn_blue("📤 سحب ملفات", "pull_0"))
    m.row(btn_green("🎨 تخصيص الأزرار والرسائل", "admin_premium"), btn_green("🎯 التخصيص المباشر", "dx_toggle"))
    m.row(btn_blue("✏️ اسم البوت", "admin_bot_name"), btn_blue("🖼 صورة البوت", "admin_bot_photo"))
    m.row(btn_blue("💾 مركز النسخ الاحتياطي", "admin_backup_center"), btn_blue("💽 نسخة DB كاملة", "admin_backup_db"))
    m.row(btn_blue("🔁 إعادة تشغيل الكل", "admin_restart_all"), btn_red("♻️ إعادة تشغيل الاستضافة", "admin_restart_host"))
    m.row(btn(f"{'▶️ تشغيل الاستضافة' if host_paused() else '⏸ إيقاف الاستضافة'}", "admin_toggle_pause",
              style="success" if host_paused() else "danger"), btn_green("📖 دليل الأدمن", "adm_guide"))
    m.add(btn_gray("🔙 رجوع", "main_menu"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_backup_center")
def cb_admin_backup_center(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    hist = get_backup_history(5)
    keep = get_ui("backup_keep_count") or str(BACKUP_RETENTION_COUNT)
    local_files = sorted([x for x in os.listdir(BACKUPS_24H_DIR) if x.endswith(".zip")], reverse=True)
    lines = [
        make_header("💾 مركز النسخ الاحتياطي والاستعادة"),
        "",
        f"🗂️ <b>عدد النسخ المحلية المحتفظ بها:</b> <code>{len(local_files)} / {keep}</code>",
        f"📦 <b>إجمالي المشاريع:</b> <code>{len(get_all_projects())}</code>",
        "",
    ]
    if hist:
        lines.append("<b><u>🕒 آخر النسخ المسجلة:</u></b>")
        for _, btype, fname, sz, pcnt, fcnt, ver, ts in hist:
            v_icon = "✅" if ver else "⚠️"
            lines.append(f"  {v_icon} [{btype}] <code>{ts}</code> | <code>{sz // 1024}KB</code> ({pcnt} مشروع / {fcnt} ملف)")
    else:
        lines.append("📭 لا توجد نسخ مسجلة في السجل بعد.")

    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_green("☁️ نسخ سحابي الآن", "cb_now"), btn_blue("💾 نسخ محلي الآن", "cb_local_bk_now"))
    m.row(btn_blue("♻️ استعادة سحابية / ZIP", "cb_restore"), btn_blue("📥 استعادة DB فقط", "admin_restore_db"))
    m.row(btn_blue("➖ تقليل الاحتفاظ", "bk_keep_-1"), btn_blue("➕ زيادة الاحتفاظ", "bk_keep_1"))
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    safe_edit_msg("\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("bk_keep_"))
def cb_bk_keep(call):
    if not is_admin(call.from_user.id):
        return
    delta = int(call.data.split("_")[-1])
    cur = int(float(get_ui("backup_keep_count") or BACKUP_RETENTION_COUNT))
    nxt = max(1, min(30, cur + delta))
    set_ui("backup_keep_count", str(nxt))
    safe_answer_cb(call, f"✅ الاحتفاظ بآخر {nxt} نسخ محلية")
    cb_admin_backup_center(call)


@bot.callback_query_handler(func=lambda c: c.data == "cb_local_bk_now")
def cb_local_bk_now(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call, "⏳ جاري إنشاء نسخة محلية والتحقق منها...")

    def _work():
        try:
            ok, fn, sz, fcnt = local_24h_backup_once()
            bot.send_message(call.message.chat.id,
                             f"✅ <b>{get_ui('msg_backup_done')}</b>\n📄 <code>{fn}</code>\n💾 الحجم: <code>{sz // 1024}KB</code> · 📄 الملفات: <code>{fcnt}</code>",
                             parse_mode="HTML")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ فشل النسخ المحلي: <code>{html.escape(str(e)[:200])}</code>", parse_mode="HTML")

    threading.Thread(target=_work, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_storage_"))
def cb_admin_storage(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    try:
        page = int(call.data.split("_")[-1])
    except Exception:
        page = 0
    files = list_admin_backups(200)
    pages = max(1, (len(files) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    m = types.InlineKeyboardMarkup(row_width=1)
    for idx, fn in enumerate(files[page * PER_PAGE:(page + 1) * PER_PAGE]):
        fp = os.path.join(ADMIN_STORAGE_DIR, fn)
        sz = get_file_size_str(fp)
        real_idx = page * PER_PAGE + idx
        m.add(btn_blue(f"📦 {fn[:30]} ({sz})", f"adm_st_dl_{real_idx}"))
    for r in _nav_row("admin_storage", page, pages, "admin_panel"):
        m.row(*r)
    txt = f"{get_ui('storage_intro')}\n\n📊 إجمالي الأرشيفات: <code>{len(files)}</code> — صفحة {page + 1}/{pages}"
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_st_dl_"))
def cb_admin_storage_dl(call):
    if not is_admin(call.from_user.id):
        return
    try:
        idx = int(call.data[len("adm_st_dl_"):])
    except Exception:
        return
    files = list_admin_backups(200)
    if idx < 0 or idx >= len(files):
        return safe_answer_cb(call, "❌ الملف غير موجود", show_alert=True)
    fp = os.path.join(ADMIN_STORAGE_DIR, files[idx])
    if not os.path.isfile(fp):
        return safe_answer_cb(call, "❌ الملف غير موجود", show_alert=True)
    if os.path.getsize(fp) > TG_UPLOAD_LIMIT:
        return safe_answer_cb(call, "⚠️ الملف أكبر من حد 49MB", show_alert=True)
    safe_answer_cb(call)
    with open(fp, "rb") as f:
        bot.send_document(call.message.chat.id, f, caption=f"📥 <code>{html.escape(files[idx])}</code>", parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_stats")
def cb_admin_stats(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    projs = get_all_projects()
    running = sum(1 for p in projs if is_running(p[0]))
    web_count = sum(1 for p in projs if p[3] == "web")
    bot_count = sum(1 for p in projs if p[3] == "bot")
    other_count = len(projs) - web_count - bot_count
    txt = (
        f"{make_header('📊 إحصائيات الاستضافة')}\n\n"
        f"👥 <b>المستخدمين:</b> <code>{get_user_count()}</code>\n"
        f"📦 <b>المشاريع:</b> <code>{len(projs)}</code>\n"
        f"🐍 <b>بوتات:</b> <code>{bot_count}</code> · 🌐 <b>مواقع:</b> <code>{web_count}</code> · ✨ <b>أخرى:</b> <code>{other_count}</code>\n"
        f"🟢 <b>شغالة:</b> <code>{running}</code>\n"
    )
    m = types.InlineKeyboardMarkup().add(btn_gray("🔙 رجوع", "admin_panel"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_users")
def cb_admin_users(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    users = get_all_users_full()
    txt = f"{make_header(f'👥 المستخدمين ({len(users)})')}\n\n"
    for u in users[:15]:
        projs = get_projects_by_owner(u[0])
        status = "🚫 محظور" if u[4] == 1 else "✅ نشط"
        txt += f"🆔 <code>{u[0]}</code> - {html.escape(u[2] or 'N/A')} [{status}] (📦 {len(projs)})\n"
    m = types.InlineKeyboardMarkup().add(btn_gray("🔙 رجوع", "admin_panel"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_projs")
def cb_admin_projs(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    projs = get_all_projects()
    txt = f"{make_header(f'📦 المشاريع ({len(projs)})')}\n\n"
    for p in projs[:15]:
        s = "🟢" if is_running(p[0]) else "🔴"
        owner = get_user(p[1])
        owner_name = html.escape(owner[2] if owner and owner[2] else str(p[1]))
        txt += f"{s} <b>{html.escape(p[2])}</b> (#{p[0]}) — 👤 {owner_name}\n"
    m = types.InlineKeyboardMarkup().add(btn_gray("🔙 رجوع", "admin_panel"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_logs")
def cb_admin_logs(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    with db_conn() as conn:
        logs = conn.execute("SELECT * FROM action_logs ORDER BY id DESC LIMIT 20").fetchall()
    txt = f"{make_header('📋 سجل الأحداث')}\n\n"
    if not logs:
        txt += "لا توجد أحداث"
    else:
        for _, u_id, action, details, ts in logs:
            txt += f"🕐 <code>{ts}</code> | <code>{u_id}</code> — <b>{html.escape(action)}</b>"
            if details:
                txt += f" ({html.escape(details[:45])})"
            txt += "\n"
    m = types.InlineKeyboardMarkup().add(btn_gray("🔙 رجوع", "admin_panel"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_tunnel")
def cb_admin_tunnel(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call, "🔄 جاري تحديث Tunnel...")

    def refresh():
        url = start_tunnel()
        if url:
            set_ui("public_url", url)
            bot.send_message(call.message.chat.id, f"<b>✅ الرابط الجديد:</b>\n<code>{url}</code>", parse_mode="HTML")
        else:
            bot.send_message(call.message.chat.id, "❌ فشل تحديث Tunnel")

    threading.Thread(target=refresh, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == "admin_unban")
def cb_admin_unban(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    user_states[call.from_user.id] = "waiting_unban_id"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "admin_panel"))
    safe_edit_msg("<b>🔓 فك حظر</b>\n\n📤 أرسل ID المستخدم:", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_unban_id")
def do_unban(msg):
    if not is_admin(msg.from_user.id):
        return
    try:
        target = int((msg.text or "").strip())
    except Exception:
        return bot.reply_to(msg, "❌ ID غير صالح")
    unban_user(target)
    user_states.pop(msg.from_user.id, None)
    bot.reply_to(msg, f"✅ تم فك حظر <code>{target}</code>", parse_mode="HTML")
    log_action(msg.from_user.id, "unban", f"target={target}")


@bot.callback_query_handler(func=lambda c: c.data == "broadcast")
def cb_broadcast(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    user_states[call.from_user.id] = "broadcast_msg"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "admin_panel"))
    safe_edit_msg("<b>📢 إعلان عام</b>\n\n📤 أرسل الرسالة الآن:", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "broadcast_msg")
def do_broadcast(msg):
    if not is_admin(msg.from_user.id):
        return
    text = msg.text or ""
    user_states.pop(msg.from_user.id, None)
    users = get_all_users()
    sent = failed = 0
    bot.send_message(msg.chat.id, f"⏳ جاري الإرسال لـ {len(users)} مستخدم...")
    for uid in users:
        try:
            bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
            time.sleep(0.05)
        except Exception:
            failed += 1
    bot.send_message(msg.chat.id, f"✅ تم الإرسال\nنجح: {sent}\nفشل: {failed}")
    log_action(msg.from_user.id, "broadcast", f"sent={sent} failed={failed}")


# ============================================================
# 🎨 لوحة التخصيص الكاملة + استعادة الافتراضي
# ============================================================
def _entities_to_html(text, entities):
    if not text:
        return ""
    if not entities:
        return text
    data = text.encode("utf-16-le")
    events = []
    for e in entities:
        if e.type == "custom_emoji" and getattr(e, "custom_emoji_id", None):
            o, c = f'<tg-emoji emoji-id="{e.custom_emoji_id}">', "</tg-emoji>"
        elif e.type == "bold": o, c = "<b>", "</b>"
        elif e.type == "italic": o, c = "<i>", "</i>"
        elif e.type == "underline": o, c = "<u>", "</u>"
        elif e.type == "code": o, c = "<code>", "</code>"
        else: continue
        events.append((e.offset, 1, -e.length, o))
        events.append((e.offset + e.length, 0, e.length, c))
    events.sort(key=lambda x: (x[0], x[1], x[2]))
    out, pos = [], 0
    for off, _, _, tag in events:
        if off > pos:
            out.append(data[pos * 2:off * 2].decode("utf-16-le", "ignore"))
            pos = off
        out.append(tag)
    out.append(data[pos * 2:].decode("utf-16-le", "ignore"))
    return "".join(out)


def _entities_label_icon(message):
    text = message.text or ""
    ents = sorted([e for e in (message.entities or []) if e.type == "custom_emoji" and getattr(e, "custom_emoji_id", None)],
                  key=lambda e: e.offset)
    icon = str(ents[0].custom_emoji_id) if ents else None
    data, out, pos = text.encode("utf-16-le"), bytearray(), 0
    for e in ents:
        out += data[pos * 2:e.offset * 2]
        pos = e.offset + e.length
    out += data[pos * 2:]
    label = out.decode("utf-16-le", "ignore").strip()
    return icon, (label or None)


def premium_main_menu():
    nb, nt = len(_btn_cache), len(_tpl_cache)
    txt = (
        f"{make_header('🎨 تخصيص البوت بالكامل')}\n\n"
        f"🔘 أزرار مخصصة: <code>{nb}</code> · 📝 قوالب نصوص مخصصة: <code>{nt}</code>\n\n"
        "كل التخصيصات محفوظة في قاعدة البيانات وتبقى بعد إعادة تشغيل البوت:"
    )
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_green("📝 تخصيص النصوص والرسائل", "admin_ui"))
    m.add(btn_green("🔘 تخصيص الأزرار الرئيسية (الاسم / اللون / الصف)", "admin_buttons"))
    m.add(btn_blue("🎛 متصفح تخصيص جميع الأزرار (إيموجي مميز)", "pbtn_root"))
    m.add(btn_blue("📄 متصفح قوالب الرسائل المتقدمة", "ptpl_root"))
    m.row(btn_blue("💾 تصدير التخصيص", "admin_backup_ui"), btn_blue("📥 استيراد التخصيص", "admin_restore_ui"))
    m.add(btn_red("♻️ استعادة جميع النصوص والأزرار الافتراضية", "ui_reset_all_confirm"))
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    return txt, m


@bot.callback_query_handler(func=lambda c: c.data == "admin_premium")
def cb_premium_root(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    txt, m = premium_main_menu()
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "ui_reset_all_confirm")
def cb_ui_reset_all_confirm(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_red("✅ نعم، استعد الافتراضي", "ui_reset_all_go"), btn_gray("❌ إلغاء", "admin_premium"))
    safe_edit_msg("⚠️ هل تريد استعادة جميع النصوص والأزرار إلى وضعها الافتراضي؟", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "ui_reset_all_go")
def cb_ui_reset_all(call):
    if not is_admin(call.from_user.id):
        return
    reset_all_ui_defaults()
    safe_answer_cb(call, "✅ تمت استعادة النصوص والأزرار الافتراضية", show_alert=True)
    cb_premium_root(call)


@bot.callback_query_handler(func=lambda c: c.data == "admin_ui")
def cb_admin_ui(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    m = types.InlineKeyboardMarkup(row_width=1)
    for key, label in UI_TEMPLATES.items():
        m.add(btn_blue(f"📝 {label}", f"ui_edit_{key}"))
    m.add(btn_gray("🔙 رجوع", "admin_premium"))
    safe_edit_msg(f"{make_header('📝 تخصيص النصوص والرسائل')}\n\nاختر النص الذي تريد تعديله أو استعادة افتراضيه:", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("ui_edit_"))
def cb_ui_edit(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    key = call.data.replace("ui_edit_", "")
    user_states[call.from_user.id] = f"waiting_ui_{key}"
    current = get_ui(key)
    txt = f"<b>✏️ تعديل:</b> <code>{html.escape(key)}</code>\n\n📄 القيمة الحالية:\n<pre>{html.escape(current[:1200])}</pre>\n\n📤 أرسل القيمة الجديدة:"
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_red("♻️ استعادة الافتراضي", f"ui_rst_{key}"), btn_gray("❌ إلغاء", "admin_ui"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("ui_rst_"))
def cb_ui_reset_one(call):
    if not is_admin(call.from_user.id):
        return
    key = call.data[len("ui_rst_"):]
    user_states.pop(call.from_user.id, None)
    reset_ui_key(key)
    safe_answer_cb(call, "✅ تمت استعادة النص الافتراضي", show_alert=True)
    cb_admin_ui(call)


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_ui_"))
def do_ui_edit(msg):
    if not is_admin(msg.from_user.id):
        return
    key = user_states[msg.from_user.id].replace("waiting_ui_", "")
    value = _entities_to_html(msg.text or "", msg.entities).strip()
    if not value:
        return bot.reply_to(msg, "❌ القيمة فارغة")
    if len(value) > 3000:
        return bot.reply_to(msg, "❌ النص طويل جداً.")
    set_ui(key, value)
    user_states.pop(msg.from_user.id, None)
    m = types.InlineKeyboardMarkup().add(btn_gray("🔙 رجوع للنصوص", "admin_ui"))
    bot.reply_to(msg, f"✅ تم تحديث <code>{html.escape(key)}</code> وحفظه في قاعدة البيانات.", parse_mode="HTML", reply_markup=m)


def make_buttons_editor():
    all_btns = get_all_buttons()
    m = types.InlineKeyboardMarkup(row_width=1)
    for key, (name, style, row) in all_btns.items():
        m.add(btn(f"{name} | {style or 'none'} | صف {row}", f"edit_btn_{key}", style=style if style else None))
    m.add(btn_gray("🔙 رجوع", "admin_premium"))
    return m


def edit_button_menu(key):
    _, _, row = get_button(key)
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(btn_blue("✏️ تغيير الاسم والإيموجي", f"chg_name_{key}"))
    m.row(btn_green("🟢 أخضر", f"chg_style_{key}_success"), btn_red("🔴 أحمر", f"chg_style_{key}_danger"))
    m.row(btn_blue("🔵 أزرق", f"chg_style_{key}_primary"), btn_gray("⚫ بدون", f"chg_style_{key}_none"))
    m.row(btn_blue(f"⬆️ صف {row - 1 if row > 0 else 0}", f"chg_row_{key}_{row - 1 if row > 0 else 0}"),
          btn_blue(f"⬇️ صف {row + 1}", f"chg_row_{key}_{row + 1}"))
    m.add(btn_red("♻️ استعادة الافتراضي", f"rst_btn_{key}"))
    m.add(btn_gray("🔙 رجوع", "admin_buttons"))
    return m


@bot.callback_query_handler(func=lambda c: c.data == "admin_buttons")
def cb_admin_buttons(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    safe_edit_msg(f"{make_header('🔘 تعديل الأزرار الرئيسية')}\n\nاختر زراً لتعديله:", call.message.chat.id, call.message.message_id, reply_markup=make_buttons_editor(), parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("edit_btn_"))
def cb_edit_btn(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    key = call.data.replace("edit_btn_", "")
    name, style, row = get_button(key)
    txt = f"{make_header('🎨 تعديل: ' + html.escape(name))}\n\n📝 الاسم: <code>{html.escape(name)}</code>\n🎨 اللون: <code>{style or 'بدون'}</code>\n📊 الصف: <code>{row}</code>"
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=edit_button_menu(key), parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("rst_btn_"))
def cb_rst_btn(call):
    if not is_admin(call.from_user.id):
        return
    key = call.data[len("rst_btn_"):]
    with db_conn() as conn:
        conn.execute("DELETE FROM ui_settings WHERE key=?", (f"cfg_{key}",))
    safe_answer_cb(call, "✅ تمت استعادة الزر الافتراضي", show_alert=True)
    call.data = f"edit_btn_{key}"
    cb_edit_btn(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("chg_name_"))
def cb_chg_name(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    key = call.data.replace("chg_name_", "")
    user_states[call.from_user.id] = f"waiting_btn_name_{key}"
    name, _, _ = get_button(key)
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", f"edit_btn_{key}"))
    safe_edit_msg(f"<b>✏️ تغيير اسم الزر</b>\n\n📄 الحالي: <code>{html.escape(name)}</code>\n📤 أرسل الاسم الجديد:", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_btn_name_"))
def do_btn_name(msg):
    if not is_admin(msg.from_user.id):
        return
    key = user_states[msg.from_user.id].replace("waiting_btn_name_", "")
    new_name = (msg.text or "").strip()[:30]
    if not new_name:
        return bot.reply_to(msg, "❌ اسم غير صالح")
    _, style, row = get_button(key)
    set_button(key, new_name, style, row)
    user_states.pop(msg.from_user.id, None)
    bot.reply_to(msg, f"✅ تم تغيير الاسم إلى: <code>{html.escape(new_name)}</code>", parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("chg_style_"))
def cb_chg_style(call):
    if not is_admin(call.from_user.id):
        return
    parts = call.data.replace("chg_style_", "").rsplit("_", 1)
    if len(parts) != 2:
        return
    key, style = parts
    if style == "none":
        style = ""
    name, _, row = get_button(key)
    set_button(key, name, style, row)
    safe_answer_cb(call, "✅ تم تغيير اللون", show_alert=True)
    call.data = f"edit_btn_{key}"
    cb_edit_btn(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("chg_row_"))
def cb_chg_row(call):
    if not is_admin(call.from_user.id):
        return
    parts = call.data.replace("chg_row_", "").rsplit("_", 1)
    if len(parts) != 2:
        return
    key, new_row = parts
    try:
        new_row = int(new_row)
    except Exception:
        return
    name, style, _ = get_button(key)
    set_button(key, name, style, new_row)
    safe_answer_cb(call, "✅ تم التغيير", show_alert=True)
    call.data = f"edit_btn_{key}"
    cb_edit_btn(call)


# ============================================================
# 🎛 متصفح الأزرار والنصوص المتقدم + التصدير والاستيراد + Direct Edit
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith(("pbtn_", "pgrp_")))
def cb_pbtn(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    uid = call.from_user.id
    parts = call.data.split(":")
    act = parts[0]
    sc = ui_scan()
    if act == "pbtn_root":
        screens = [(fid, fo) for fid, fo in sc["funcs"].items() if fo["btns"] and fo["name"] not in _PREMIUM_SELF_FUNCS]
        screens.sort(key=lambda x: (x[1]["admin"], x[1]["title"]))
        page = int(parts[1]) if len(parts) > 1 else 0
        pages = max(1, (len(screens) + PER_PAGE - 1) // PER_PAGE)
        page = max(0, min(page, pages - 1))
        m = types.InlineKeyboardMarkup(row_width=1)
        for fid, fo in screens[page * PER_PAGE:(page + 1) * PER_PAGE]:
            m.add(btn_blue(f"🗂 {fo['title']} ({len(fo['btns'])})"[:48], f"pbtn_scr_{fid}_0"))
        for r in _nav_row("pbtn_root", page, pages, "admin_premium"):
            m.row(*r)
        safe_edit_msg(f"{make_header('🎛 متصفح الأزرار')}\n\nصفحة {page + 1}/{pages}", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    elif act.startswith("pbtn_scr_"):
        seg = parts[0].split("_")
        fid = seg[2]
        page = int(parts[1]) if len(parts) > 1 else (int(seg[3]) if len(seg) > 3 else 0)
        fo = sc["funcs"].get(fid)
        if not fo:
            return cb_premium_root(call)
        btns_list = [bid for bid in fo["btns"] if bid in sc["buttons"]]
        pages = max(1, (len(btns_list) + PER_PAGE - 1) // PER_PAGE)
        page = max(0, min(page, pages - 1))
        m = types.InlineKeyboardMarkup(row_width=1)
        for bid in btns_list[page * PER_PAGE:(page + 1) * PER_PAGE]:
            b = sc["buttons"][bid]
            mark = "✨" if b["key"] in _btn_cache else "▫️"
            m.add(btn_blue(f"{mark} {b['label']}"[:38], f"pbtn_e_{bid}"))
        for r in _nav_row(f"pbtn_scr_{fid}", page, pages, "pbtn_root:0"):
            m.row(*r)
        safe_edit_msg(f"{make_header('🎛 ' + fo['title'])}\n\nصفحة {page + 1}/{pages}", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    elif act.startswith("pbtn_e_"):
        bid = call.data[len("pbtn_e_"):]
        b = sc["buttons"].get(bid)
        if not b:
            return cb_premium_root(call)
        cust = _btn_cache.get(b["key"], {})
        m = types.InlineKeyboardMarkup(row_width=1)
        m.add(btn(cust.get("label") or b["label"], cb="noop"))
        m.row(btn_blue("🎨 الإيموجي", f"pbtn_i_{bid}"), btn_blue("✏️ الاسم", f"pbtn_n_{bid}"), btn_blue("🌈 اللون", f"pbtn_c_{bid}"))
        m.add(btn_red("♻️ استعادة الافتراضي", f"pbtn_r_{bid}"))
        m.add(btn_gray("🔙 رجوع", f"pbtn_scr_{b['fid']}_0"))
        safe_edit_msg(f"{make_header('🎛 تخصيص زر')}\n\n🏷 الاسم: <code>{html.escape(cust.get('label') or b['label'])}</code>", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    elif act.startswith("pbtn_c_"):
        bid = call.data[len("pbtn_c_"):]
        m = types.InlineKeyboardMarkup(row_width=1)
        for s in BTN_STYLES:
            m.add(btn(BTN_STYLE_NAMES[s], f"pbtn_sc_{bid}_{s}", style=(s if s != "none" else None)))
        m.add(btn_gray("🔙 رجوع", f"pbtn_e_{bid}"))
        safe_edit_msg(make_header("🌈 اختر اللون"), call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    elif act.startswith("pbtn_sc_"):
        bid, style = call.data[len("pbtn_sc_"):].rsplit("_", 1)
        b = sc["buttons"].get(bid)
        if b:
            set_btn_custom(b["key"], style=style)
        call.data = f"pbtn_e_{bid}"
        return cb_pbtn(call)
    elif act.startswith("pbtn_r_"):
        bid = call.data[len("pbtn_r_"):]
        b = sc["buttons"].get(bid)
        if b:
            reset_btn_custom(b["key"])
        call.data = f"pbtn_e_{bid}"
        return cb_pbtn(call)
    elif act.startswith(("pbtn_i_", "pbtn_n_")):
        prefix = "pbtn_i_" if act.startswith("pbtn_i_") else "pbtn_n_"
        bid = call.data[len(prefix):]
        user_states[uid] = f"waiting_{prefix}{bid}"
        hint = "أرسل إيموجي مميز ليصبح أيقونة الزر." if prefix == "pbtn_i_" else "أرسل الاسم الجديد (حتى 30 حرفاً) أو - للعودة للأصلي."
        m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", f"pbtn_e_{bid}"))
        safe_edit_msg(f"{make_header('✏️ تخصيص')}\n\n{hint}", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id, "").startswith("waiting_pbtn_"))
def _do_pbtn_input(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    state = user_states.pop(uid)
    sc = ui_scan()
    bid = state.split("_")[-1]
    b = sc["buttons"].get(bid)
    if not b:
        return
    if state.startswith("waiting_pbtn_i_"):
        icon, _ = _entities_label_icon(msg)
        if not icon:
            user_states[uid] = state
            return bot.reply_to(msg, "❌ لم أجد إيموجي مميزاً.")
        set_btn_custom(b["key"], icon=icon)
    else:
        label = (msg.text or "").strip()
        if label == "-":
            set_btn_custom(b["key"], label=None)
        elif 0 < len(label) <= 30:
            set_btn_custom(b["key"], label=label)
        else:
            user_states[uid] = state
            return bot.reply_to(msg, "❌ الاسم يجب أن يكون 1-30 حرفاً.")
    bot.send_message(msg.chat.id, "✅ تم الحفظ.", reply_markup=types.InlineKeyboardMarkup().add(btn_green("🎛 فتح الزر", f"pbtn_e_{bid}")))


_DX_PTPL_BACK = {}
_PTPL_PREVIEW = {}


@bot.callback_query_handler(func=lambda c: c.data.startswith("ptpl_"))
def cb_ptpl(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    uid = call.from_user.id
    data = call.data
    sc = ui_scan()
    if data.startswith("ptpl_root"):
        _DX_PTPL_BACK.pop(uid, None)
        page = int(data.split(":")[1]) if ":" in data else 0
        screens = [(fid, fo) for fid, fo in sc["funcs"].items() if fo["tpls"] and fo["name"] not in _PREMIUM_SELF_FUNCS]
        screens.sort(key=lambda x: (x[1]["admin"], x[1]["title"]))
        pages = max(1, (len(screens) + PER_PAGE - 1) // PER_PAGE)
        page = max(0, min(page, pages - 1))
        m = types.InlineKeyboardMarkup(row_width=1)
        for fid, fo in screens[page * PER_PAGE:(page + 1) * PER_PAGE]:
            m.add(btn_blue(f"🗂 {fo['title']} ({len(fo['tpls'])})"[:48], f"ptpl_scr_{fid}_0"))
        for r in _nav_row("ptpl_root", page, pages, "admin_premium"):
            m.row(*r)
        safe_edit_msg(f"{make_header('📝 اختر شاشة')}\n\nصفحة {page + 1}/{pages}", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    elif data.startswith("ptpl_scr_"):
        fid, page_s = data[len("ptpl_scr_"):].rsplit("_", 1)
        page = int(page_s)
        fo = sc["funcs"].get(fid)
        if not fo:
            return cb_premium_root(call)
        pages = max(1, (len(fo["tpls"]) + PER_PAGE - 1) // PER_PAGE)
        page = max(0, min(page, pages - 1))
        m = types.InlineKeyboardMarkup(row_width=1)
        for tid in fo["tpls"][page * PER_PAGE:(page + 1) * PER_PAGE]:
            mark = "✨" if tid in _tpl_cache else "▫️"
            m.add(btn_blue(f"{mark} {sc['tpls'][tid]['preview']}"[:42], f"ptpl_v_{tid}"))
        for r in _nav_row(f"ptpl_scr_{fid}", page, pages, _DX_PTPL_BACK.get(uid) or "ptpl_root:0"):
            m.row(*r)
        safe_edit_msg(f"{make_header('📝 ' + fo['title'])}\n\nصفحة {page + 1}/{pages}", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    elif data.startswith("ptpl_v_"):
        tid = data[len("ptpl_v_"):]
        info = sc["tpls"].get(tid)
        if not info:
            return cb_premium_root(call)
        shown = _tpl_cache.get(tid) or _tpl_text(info["parts"])
        legend = "  ".join("{" + n + "}" for n in info["names"]) or "لا يوجد"
        txt = f"{make_header('📝 نص قابل للنسخ')}\n\n🔑 المعرّفات: <code>{legend}</code>\n\n<pre>{html.escape(shown)}</pre>"
        m = types.InlineKeyboardMarkup(row_width=2)
        m.add(btn_green("✏️ تعديل", f"ptpl_e_{tid}"))
        if tid in _tpl_cache:
            m.add(btn_red("♻️ إعادة الأصل", f"ptpl_x_{tid}"))
        m.add(btn_gray("🔙 رجوع", _DX_PTPL_BACK.get(uid) or f"ptpl_scr_{info['fid']}_0"))
        safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    elif data.startswith("ptpl_e_"):
        tid = data[len("ptpl_e_"):]
        user_states[uid] = f"waiting_ptpl_{tid}"
        m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", f"ptpl_v_{tid}"))
        safe_edit_msg(f"{make_header('✏️ أرسل النص الجديد')}\n\nحافظ على المعرّفات {{...}}.", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    elif data.startswith("ptpl_x_"):
        tid = data[len("ptpl_x_"):]
        with db_conn() as conn:
            conn.execute("DELETE FROM tpl_custom WHERE tid=?", (tid,))
        _load_tpl_cache()
        call.data = f"ptpl_v_{tid}"
        return cb_ptpl(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ptpl_save_"))
def cb_ptpl_save(call):
    if not is_admin(call.from_user.id):
        return
    tid = call.data[len("ptpl_save_"):]
    new = _PTPL_PREVIEW.pop(tid, None)
    if not new:
        return safe_answer_cb(call, "⚠️ انتهت المعاينة، أرسل النص مجدداً", show_alert=True)
    with db_conn() as conn:
        conn.execute("REPLACE INTO tpl_custom (tid, text) VALUES (?, ?)", (tid, new))
    _load_tpl_cache()
    safe_answer_cb(call, "✅ تم الحفظ!")
    safe_edit_msg("✅ تم حفظ القالب وتفعيله فوراً.", call.message.chat.id, call.message.message_id,
                  reply_markup=types.InlineKeyboardMarkup().add(btn_green("📝 عرض القالب", f"ptpl_v_{tid}")))


@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id, "").startswith("waiting_ptpl_"))
def _do_ptpl_input(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    tid = user_states.pop(uid)[len("waiting_ptpl_"):]
    info = ui_scan()["tpls"].get(tid)
    if not info:
        return
    new = _entities_to_html(msg.text or "", msg.entities)
    names = set(info["names"])
    unknown = sorted({m.group(1) for m in re.finditer(r"\{\{|\}\}|\{(\w+)\}", new) if m.group(1) and m.group(1) not in names})
    if not new or len(new) > 3500 or unknown:
        user_states[uid] = f"waiting_ptpl_{tid}"
        return bot.reply_to(msg, "❌ نص غير صالح أو يحتوي معرّفات غير معروفة.")
    _PTPL_PREVIEW[tid] = new
    rendered = _render_tpl(new, {n: f"[{n}]" for n in info["names"]})
    pm = types.InlineKeyboardMarkup(row_width=2)
    pm.row(btn_green("✅ تأكيد وحفظ", f"ptpl_save_{tid}"), btn_blue("✏️ تعديل مرة أخرى", f"ptpl_e_{tid}"))
    pm.add(btn_red("❌ إلغاء", f"ptpl_v_{tid}"))
    bot.send_message(msg.chat.id, f"{make_header('👁️ معاينة حية قبل الحفظ')}\n\n{rendered}", reply_markup=pm, parse_mode="HTML")


def export_customization():
    return json.dumps({
        "version": 1,
        "buttons": _btn_cache,
        "templates": _tpl_cache,
        "ui": get_all_ui(),
        "buttons_main": {k: list(v) for k, v in get_all_buttons().items()},
    }, ensure_ascii=False, indent=2).encode("utf-8")


def import_customization(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("ملف التخصيص غير صالح")
    nb = nt = nu = nm = 0
    for k, c in (data.get("buttons") or {}).items():
        if isinstance(k, str) and isinstance(c, dict):
            set_btn_custom(k, icon=c.get("icon"), label=c.get("label"), style=c.get("style"))
            nb += 1
    for tid, text in (data.get("templates") or {}).items():
        if isinstance(tid, str) and isinstance(text, str) and 0 < len(text) <= 3500:
            with db_conn() as conn:
                conn.execute("REPLACE INTO tpl_custom (tid, text) VALUES (?, ?)", (tid, text))
            nt += 1
    _load_tpl_cache()
    for k, v in (data.get("ui") or {}).items():
        if isinstance(k, str) and isinstance(v, str) and len(v) <= 3000:
            set_ui(k, v)
            nu += 1
    for k, v in (data.get("buttons_main") or {}).items():
        if k in DEFAULT_BUTTONS and isinstance(v, list) and len(v) == 3:
            set_button(k, str(v[0])[:60], v[1], int(v[2]))
            nm += 1
    return nb, nt, nu, nm


@bot.callback_query_handler(func=lambda c: c.data == "admin_backup_ui")
def cb_backup_ui(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    f = io.BytesIO(export_customization())
    f.name = f"customization_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    bot.send_document(call.message.chat.id, f, caption="💾 نسخة احتياطية لكل التخصيص")


@bot.callback_query_handler(func=lambda c: c.data == "admin_restore_ui")
def cb_restore_ui_prompt(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    user_states[call.from_user.id] = "waiting_restore_ui"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "admin_premium"))
    safe_edit_msg(f"{make_header('📥 استعادة التخصيص')}\n\nأرسل ملف customization.json", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_restore_ui", content_types=['document'])
def _do_restore_ui(msg):
    if not is_admin(msg.from_user.id):
        return
    user_states.pop(msg.from_user.id, None)
    try:
        fi = bot.get_file(msg.document.file_id)
        nb, nt, nu, nm = import_customization(json.loads(bot.download_file(fi.file_path).decode("utf-8")))
        bot.send_message(msg.chat.id, f"✅ تمت الاستعادة: 🎛 {nb} زر · 📝 {nt} نص · ⚙️ {nu} إعداد · 🔘 {nm} زر رئيسي")
    except Exception as e:
        bot.reply_to(msg, render_error("ملف غير صالح", e), parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_backup_db")
def cb_backup_db(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    try:
        tmp_path = DB_PATH + ".bak"
        with DB_LOCK:
            src_conn = sqlite3.connect(DB_PATH)
            dst_conn = sqlite3.connect(tmp_path)
            src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()
        with open(tmp_path, "rb") as f:
            bot.send_document(call.message.chat.id, f, caption=f"💽 نسخة قاعدة البيانات — {datetime.now().strftime('%Y-%m-%d %H:%M')}", visible_file_name="host_backup.db")
        os.remove(tmp_path)
    except Exception as e:
        bot.send_message(call.message.chat.id, render_error("فشل نسخ القاعدة", e), parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_restore_db")
def cb_restore_db_prompt(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    user_states[call.from_user.id] = "waiting_restore_db"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "admin_backup_center"))
    safe_edit_msg(f"{make_header('⚠️ استعادة قاعدة البيانات')}\n\nأرسل ملف .db السليم لاستبدال القاعدة الحالية:", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_restore_db", content_types=['document'])
def _do_restore_db(msg):
    if not is_admin(msg.from_user.id):
        return
    user_states.pop(msg.from_user.id, None)
    tmp = DB_PATH + ".restore_tmp"
    try:
        fi = bot.get_file(msg.document.file_id)
        with open(tmp, "wb") as f:
            f.write(bot.download_file(fi.file_path))
        ok_db, err_db = verify_db_integrity(tmp)
        if not ok_db:
            raise ValueError(err_db)
        with DB_LOCK:
            dst = sqlite3.connect(DB_PATH)
            src = sqlite3.connect(tmp)
            src.backup(dst)
            src.close()
            dst.close()
        init_db()
        bot.send_message(msg.chat.id, f"✅ {get_ui('msg_restore_done')}")
    except Exception as e:
        bot.reply_to(msg, render_error("فشلت الاستعادة", e), parse_mode="HTML")
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


# ============================================================
# 💳 Billing / Trusted / Approval / Bot Name & Photo / Pull / Cloud / Direct Edit
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "admin_billing")
def cb_admin_billing(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    paid = is_paid_mode()
    cur = get_ui("currency_name") or "$"
    pending = get_pending_recharges()
    txt = (
        f"{make_header('💰 إدارة المدفوعات والباقات')}\n\n"
        f"⚙️ <b>الوضع الحالي:</b> {'💎 مدفوع' if paid else '🆓 مجاني'}\n"
        f"📦 <b>سعر الخانة:</b> <code>{get_ui('slot_price')} {cur}</code> · ⭐ <b>VIP:</b> <code>{get_ui('vip_price')} {cur}</code>\n"
        f"⏳ <b>طلبات شحن معلقة:</b> <code>{len(pending)}</code>"
    )
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(btn("🔄 تبديل إلى: " + ("🆓 وضع مجاني" if paid else "💎 وضع مدفوع"), "adm_toggle_mode", style="success" if paid else "danger"))
    m.row(btn_blue(f"⏳ طلبات الشحن ({len(pending)})", "adm_recharges"), btn_green("🎟 إنشاء كود شحن", "adm_new_code"))
    m.row(btn_blue("💳 تعديل رصيد مستخدم", "adm_add_bal"), btn_blue("⚙️ تعديل الأسعار", "adm_prices"))
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "adm_toggle_mode")
def cb_adm_toggle_mode(call):
    if not is_admin(call.from_user.id):
        return
    new_mode = "free" if is_paid_mode() else "paid"
    set_ui("bot_mode", new_mode)
    safe_answer_cb(call, f"✅ تم التحويل إلى: {'💎 مدفوع' if new_mode == 'paid' else '🆓 مجاني'}", show_alert=True)
    cb_admin_billing(call)


@bot.callback_query_handler(func=lambda c: c.data == "adm_recharges")
def cb_adm_recharges(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    reqs = get_pending_recharges()
    cur = get_ui("currency_name") or "$"
    m = types.InlineKeyboardMarkup(row_width=2)
    if not reqs:
        txt = f"{make_header('⏳ طلبات الشحن')}\n\n📭 لا توجد طلبات معلقة."
    else:
        lines = [make_header(f"⏳ طلبات الشحن ({len(reqs)})"), ""]
        for rid, uid, amt, proof, _, _ in reqs[:8]:
            lines.append(f"• <b>#{rid}</b> | <code>{uid}</code> | <code>{amt:.2f} {cur}</code>\n  {html.escape(proof[:50])}")
            m.row(btn_green(f"✅ قبول #{rid}", f"adm_rech_ok_{rid}"), btn_red(f"❌ رفض #{rid}", f"adm_rech_no_{rid}"))
        txt = "\n".join(lines)
    m.add(btn_gray("🔙 رجوع", "admin_billing"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith(("adm_rech_ok_", "adm_rech_no_")))
def cb_adm_rech_action(call):
    if not is_admin(call.from_user.id):
        return
    approve = call.data.startswith("adm_rech_ok_")
    rid = int(call.data.rsplit("_", 1)[1])
    req = get_recharge_request(rid)
    if not req or not resolve_recharge_request(rid, "approved" if approve else "rejected"):
        return safe_answer_cb(call, "⚠️ الطلب معالَج مسبقاً أو غير موجود", show_alert=True)
    _, uid, amt, _, _, _ = req
    cur = get_ui("currency_name") or "$"
    if approve:
        add_balance(uid, amt)
        safe_answer_cb(call, f"✅ تم قبول #{rid}")
        try:
            bot.send_message(uid, f"✅ تم قبول طلب الشحن <code>#{rid}</code>! أُضيف <code>{amt:.2f} {cur}</code> لرصيدك.", parse_mode="HTML")
        except Exception:
            pass
    else:
        safe_answer_cb(call, f"❌ تم رفض #{rid}")
        try:
            bot.send_message(uid, f"❌ تم رفض طلب الشحن <code>#{rid}</code>.", parse_mode="HTML")
        except Exception:
            pass
    cb_adm_recharges(call)


@bot.callback_query_handler(func=lambda c: c.data == "adm_new_code")
def cb_adm_new_code(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    user_states[call.from_user.id] = "waiting_adm_code"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "admin_billing"))
    safe_edit_msg(f"{make_header('🎟 إنشاء كود شحن')}\n\nأرسل المبلغ وعدد الاستخدامات (مثال: <code>5 10</code>):", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_adm_code")
def do_adm_new_code(msg):
    if not is_admin(msg.from_user.id):
        return
    user_states.pop(msg.from_user.id, None)
    parts = (msg.text or "").split()
    try:
        amt = float(parts[0])
        uses = int(parts[1]) if len(parts) > 1 else 1
    except Exception:
        return bot.reply_to(msg, "❌ صيغة غير صحيحة. مثال: <code>5 1</code>", parse_mode="HTML")
    code_str = "HOST-" + secrets.token_hex(4).upper()
    create_gift_code(code_str, amt, uses)
    cur = get_ui("currency_name") or "$"
    bot.reply_to(msg, f"✅ تم إنشاء الكود:\n<code>{code_str}</code>\n💰 القيمة: <code>{amt:.2f} {cur}</code> · 🔢 الاستخدامات: <code>{uses}</code>", parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "adm_add_bal")
def cb_adm_add_bal(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    user_states[call.from_user.id] = "waiting_adm_bal"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "admin_billing"))
    safe_edit_msg(f"{make_header('💳 تعديل رصيد مستخدم')}\n\nأرسل <code>USER_ID المبلغ</code>:", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_adm_bal")
def do_adm_add_bal(msg):
    if not is_admin(msg.from_user.id):
        return
    user_states.pop(msg.from_user.id, None)
    parts = (msg.text or "").split()
    try:
        t_uid, amt = int(parts[0]), float(parts[1])
    except Exception:
        return bot.reply_to(msg, "❌ صيغة غير صحيحة.")
    if not get_user(t_uid):
        return bot.reply_to(msg, "❌ المستخدم غير موجود.")
    add_balance(t_uid, amt)
    cur = get_ui("currency_name") or "$"
    bot.reply_to(msg, f"✅ تم تعديل رصيد <code>{t_uid}</code> بمقدار <code>{amt:+.2f} {cur}</code>.", parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "adm_prices")
def cb_adm_prices(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    m = types.InlineKeyboardMarkup(row_width=1)
    for k, lbl in [
        ("slot_price", "📦 سعر الخانة الإضافية"),
        ("vip_price", "⭐ سعر ترقية VIP"),
        ("referral_bonus", "🤝 مكافأة الإحالة"),
        ("free_trial_projects", "🎁 المشاريع المجانية"),
        ("currency_name", "💱 العملة"),
        ("recharge_info", "📝 تعليمات الشحن"),
    ]:
        m.add(btn_blue(f"{lbl}: {get_ui(k)[:20]}", f"ui_edit_{k}"))
    m.add(btn_gray("🔙 رجوع", "admin_billing"))
    safe_edit_msg(make_header("⚙️ أسعار الباقات والإحالة"), call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_demo_web")
def cb_admin_demo_web(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    base = get_public_url().rstrip("/")
    ping_on = get_ui("demo_web_ping") != "0"
    txt = (
        f"{make_header('🌐 ويب المراقبة (OK)')}\n\n"
        f"📡 <b>الحالة:</b> <code>OK (200)</code> · 🔄 <b>النبض التلقائي:</b> {'🟢 شغال' if ping_on else '🔴 متوقف'}\n\n"
        f"• الرئيسي: <code>{base}/</code>\n• فحص سريع: <code>{base}/ping</code>\n• الحالة JSON: <code>{base}/status</code>"
    )
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn("🔄 " + ("إيقاف النبض التلقائي" if ping_on else "تفعيل النبض التلقائي"), "admin_toggle_demo_ping", style="danger" if ping_on else "success"))
    m.add(btn_blue("🌐 فتح رابط /ping", url=f"{base}/ping"))
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_toggle_demo_ping")
def cb_toggle_demo_ping(call):
    if not is_admin(call.from_user.id):
        return
    cur_on = get_ui("demo_web_ping") != "0"
    set_ui("demo_web_ping", "0" if cur_on else "1")
    safe_answer_cb(call, "✅ تم التحديث")
    cb_admin_demo_web(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith(("adm_proj_ok_", "adm_proj_no_")))
def cb_adm_proj_approval(call):
    uid = call.from_user.id
    if not is_admin(uid):
        return safe_answer_cb(call, "🚫 غير مصرح", show_alert=True)
    approve = call.data.startswith("adm_proj_ok_")
    try:
        pid = int(call.data.rsplit("_", 1)[1])
    except Exception:
        return
    p = get_project(pid)
    if not p:
        return safe_answer_cb(call, "❌ المشروع غير موجود", show_alert=True)
    chat_id, mid = call.message.chat.id, call.message.message_id

    if not approve:
        set_project_approved(pid, -1)
        stop_project(pid, reason="رفض من الإدارة")
        log_action(uid, "reject_project", f"pid={pid} owner={p[1]}")
        safe_answer_cb(call, "🛑 تم إيقاف / رفض التشغيل")
        status_line = f"🛑 <b>الحالة:</b> تم الإيقاف / الرفض بواسطة الإدارة\n📦 <b>المشروع:</b> <code>{html.escape(p[2])}</code>"
        m = types.InlineKeyboardMarkup().add(btn_green("✅ قبول التشغيل", f"adm_proj_ok_{pid}"))
        try:
            if call.message.document:
                bot.edit_message_caption(status_line, chat_id, mid, parse_mode="HTML", reply_markup=m)
            else:
                safe_edit_msg(status_line, chat_id, mid, reply_markup=m, parse_mode="HTML")
        except Exception:
            bot.send_message(chat_id, status_line, parse_mode="HTML", reply_markup=m)
        try:
            bot.send_message(p[1], f"🛑 تم إيقاف / رفض تشغيل مشروعك <b>{html.escape(p[2])}</b> من قبل الإدارة.", parse_mode="HTML")
        except Exception:
            pass
        return

    set_project_approved(pid, 1)
    log_action(uid, "approve_project", f"pid={pid} owner={p[1]}")
    safe_answer_cb(call, "✅ تم القبول وجاري التشغيل...")

    def _run_approved():
        ok, res_msg, info = start_project(pid)
        m = types.InlineKeyboardMarkup().add(btn_red("🛑 إيقاف التشغيل", f"adm_proj_no_{pid}"))
        if ok:
            status_line = f"✅ <b>الحالة:</b> تم القبول والتشغيل بنجاح\n📦 <b>المشروع:</b> <code>{html.escape(p[2])}</code> (PID: <code>{info.get('pid') if info else '-'}</code>)"
            try:
                bot.send_message(p[1], f"✅ تمت الموافقة على مشروعك <b>{html.escape(p[2])}</b> وتم تشغيله بنجاح!", parse_mode="HTML")
            except Exception:
                pass
        else:
            status_line = f"✅ <b>الحالة:</b> تمت الموافقة، لكن تعذّر التشغيل التلقائي:\n{res_msg[:400]}"
            try:
                bot.send_message(p[1], f"✅ تمت الموافقة على مشروعك <b>{html.escape(p[2])}</b>! يمكنك تشغيله أو فحص السجلات من مشاريعك.", parse_mode="HTML")
            except Exception:
                pass
        try:
            if call.message.document:
                bot.edit_message_caption(status_line, chat_id, mid, parse_mode="HTML", reply_markup=m)
            else:
                safe_edit_msg(status_line, chat_id, mid, reply_markup=m, parse_mode="HTML")
        except Exception:
            bot.send_message(chat_id, status_line, parse_mode="HTML", reply_markup=m)

    threading.Thread(target=_run_approved, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == "admin_trusted_users")
def cb_admin_trusted(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    user_states.pop(call.from_user.id, None)
    trusted = get_all_trusted_users()
    lines = [make_header("✅ مستخدمين بدون موافقة"), "", "ملفاتهم تعمل مباشرة بدون انتظار موافقة الأدمن:", ""]
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_green("➕ إضافة عضو (آيدي أو يوزر)", "adm_add_trusted"))
    if not trusted:
        lines.append("📭 لا يوجد أعضاء مضافون حالياً.")
    else:
        for t_uid, t_uname, t_fname, _, _ in trusted[:25]:
            u_db = get_user(t_uid)
            disp_name = t_fname or (u_db[2] if u_db and u_db[2] else "")
            disp_user = t_uname or (u_db[1] if u_db and u_db[1] else "")
            tag = f" (@{disp_user})" if disp_user else ""
            lines.append(f"• <code>{t_uid}</code> {html.escape(disp_name)}{tag}")
            m.add(btn_red(f"🗑 إزالة {t_uid} {disp_name[:15]}".strip(), f"adm_del_trusted_{t_uid}"))
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    safe_edit_msg("\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "adm_add_trusted")
def cb_adm_add_trusted(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    user_states[call.from_user.id] = "waiting_trusted_user"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "admin_trusted_users"))
    safe_edit_msg(f"{make_header('➕ إضافة عضو بدون موافقة')}\n\n📤 أرسل آيدي المستخدم أو اليوزر (@username):", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id) == "waiting_trusted_user")
def do_add_trusted(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    target_id = None
    uname = fname = ""
    if getattr(msg, "forward_from", None):
        target_id = msg.forward_from.id
        uname = msg.forward_from.username or ""
        fname = msg.forward_from.first_name or ""
    else:
        raw = (msg.text or "").strip()
        if raw.isdigit():
            target_id = int(raw)
            u_db = get_user(target_id)
            if u_db:
                uname, fname = u_db[1] or "", u_db[2] or ""
        else:
            found = find_user_by_username(raw)
            if found:
                target_id, uname, fname = found[0], found[1] or "", found[2] or ""
            else:
                return bot.reply_to(msg, "❌ لم يتم العثور على هذا اليوزر، أرسل الآيدي الرقمي مباشرة.")
    user_states.pop(uid, None)
    add_trusted_user(target_id, uname, fname, added_by=uid)
    log_action(uid, "add_trusted_user", f"target={target_id}")
    m = types.InlineKeyboardMarkup().add(btn_gray("🔙 رجوع للقائمة", "admin_trusted_users"))
    bot.reply_to(msg, f"✅ تم تفعيل الاستضافة بدون موافقة للعضو <code>{target_id}</code>.", parse_mode="HTML", reply_markup=m)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_del_trusted_"))
def cb_adm_del_trusted(call):
    if not is_admin(call.from_user.id):
        return
    remove_trusted_user(int(call.data[len("adm_del_trusted_"):]))
    safe_answer_cb(call, "✅ تم إزالة العضو")
    cb_admin_trusted(call)


def _tg_bot_api(method, json_data=None, data=None, files=None, timeout=25):
    try:
        import requests as req
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
        r = req.post(url, json=json_data, data=data, files=files, timeout=timeout)
        res = r.json()
        if r.status_code == 200 and res.get("ok"):
            return True, res.get("result")
        return False, res.get("description") or f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:200]


@bot.callback_query_handler(func=lambda c: c.data == "admin_bot_name")
def cb_admin_bot_name(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    user_states[call.from_user.id] = "waiting_bot_name"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "admin_panel"))
    safe_edit_msg(f"{make_header('✏️ تغيير اسم البوت')}\n\n📤 أرسل الاسم الجديد (1-64 حرفاً):", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id) == "waiting_bot_name")
def do_bot_name(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    new_name = (msg.text or "").strip()
    if not new_name or len(new_name) > 64:
        return bot.reply_to(msg, "❌ الاسم يجب أن يكون بين 1 و 64 حرفاً.")
    ok, res = _tg_bot_api("setMyName", json_data={"name": new_name})
    if ok:
        user_states.pop(uid, None)
        bot.reply_to(msg, f"✅ تم تغيير اسم البوت إلى: <code>{html.escape(new_name)}</code>", parse_mode="HTML")
    else:
        bot.reply_to(msg, f"❌ فشل التغيير: <code>{html.escape(str(res))}</code>", parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_bot_photo")
def cb_admin_bot_photo(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    user_states[call.from_user.id] = "waiting_bot_photo"
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_red("🗑 حذف صورة البوت الحالية", "admin_bot_photo_remove"))
    m.add(btn_gray("❌ إلغاء", "admin_panel"))
    safe_edit_msg(f"{make_header('🖼 تغيير صورة البوت')}\n\n📤 أرسل الصورة الجديدة للبوت:", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_bot_photo_remove")
def cb_admin_bot_photo_remove(call):
    if not is_admin(call.from_user.id):
        return
    user_states.pop(call.from_user.id, None)
    ok, res = _tg_bot_api("removeMyProfilePhoto", json_data={})
    safe_answer_cb(call, "✅ تم حذف الصورة" if ok else f"❌ {str(res)[:100]}", show_alert=True)
    if ok:
        cb_admin(call)


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_bot_photo", content_types=['photo', 'document'])
def do_bot_photo(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    try:
        if msg.photo:
            fi = bot.get_file(msg.photo[-1].file_id)
            file_bytes = bot.download_file(fi.file_path)
        elif msg.document:
            fi = bot.get_file(msg.document.file_id)
            file_bytes = bot.download_file(fi.file_path)
        else:
            return
        payload = {"photo": json.dumps({"type": "static", "photo": "attach://avatar"})}
        files = {"avatar": ("avatar.jpg", file_bytes, "image/jpeg")}
        ok, res = _tg_bot_api("setMyProfilePhoto", data=payload, files=files, timeout=30)
        if ok:
            user_states.pop(uid, None)
            bot.reply_to(msg, "✅ تم تغيير صورة البوت بنجاح!")
        else:
            bot.reply_to(msg, f"❌ فشل التغيير: <code>{html.escape(str(res))}</code>", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(msg, f"❌ خطأ: <code>{html.escape(str(e)[:200])}</code>", parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_restart_all")
def cb_restart_all(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call, "🔁 جاري إعادة تشغيل كل المشاريع الشغّالة...")
    msg = bot.send_message(call.message.chat.id, "⏳ جاري التنفيذ...")

    def do():
        projs = [p for p in get_all_projects() if is_running(p[0])]
        items = []
        for p in projs:
            stop_project(p[0], reason="إعادة تشغيل جماعية")
            time.sleep(0.5)
            ok, _, _ = start_project(p[0], watch=8)
            items.append((html.escape(p[2]), ok))
        txt = render_progress("🔁 نتيجة إعادة التشغيل", "✅", "❌", items) if items else "ℹ️ لا توجد مشاريع شغّالة حالياً."
        safe_edit_msg(txt, msg.chat.id, msg.message_id, parse_mode="HTML",
                      reply_markup=types.InlineKeyboardMarkup().add(btn_gray("🔙 رجوع", "admin_panel")))

    threading.Thread(target=do, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == "admin_restart_host")
def cb_restart_host_confirm(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_red("✅ نعم، أعد التشغيل", "admin_restart_host_go"), btn_gray("❌ إلغاء", "admin_panel"))
    safe_edit_msg(f"{make_header('♻️ إعادة تشغيل بوت الاستضافة')}\n\nالمشاريع الشغّالة معزولة وستُستعاد تلقائياً.", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_restart_host_go")
def cb_restart_host_go(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    bot.send_message(call.message.chat.id, "♻️ جاري إعادة تشغيل بوت الاستضافة...")
    threading.Thread(target=lambda: (time.sleep(1), os.execv(sys.executable, [sys.executable] + sys.argv)), daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == "admin_toggle_pause")
def cb_toggle_pause(call):
    if not is_admin(call.from_user.id):
        return
    toggle_host_paused()
    cb_admin(call)


@bot.callback_query_handler(func=lambda c: c.data == "adm_guide")
def cb_adm_guide(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    txt = (
        f"{make_header('📖 دليل الأدمن — HOST BOT v13')}\n\n"
        "• <b>🛡️ عزل الأعطال:</b> كل مشروع يعمل في عملية مستقلة بقفل تزامن وبيئة معزولة.\n"
        "• <b>♻️ المراقبة وإعادة التشغيل:</b> تدرج زمني (5ث -> 15ث -> 45ث) ومنع الحلقات اللانهائية.\n"
        "• <b>🩺 التشخيص:</b> يفحص Syntax و Node.js والمكتبات والسجلات بدقة.\n"
        "• <b>💾 النسخ الاحتياطي:</b> يتحقق من سلامة ZIP و SQLite قبل أي استبدال أو حذف.\n"
        "• <b>🎨 التخصيص:</b> كل النصوص والأزرار محفوظة في القاعدة مع زر استعادة الافتراضي."
    )
    m = types.InlineKeyboardMarkup().add(btn_gray("🔙 رجوع", "admin_panel"))
    safe_edit_msg(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "cb_now")
def cb_cloud_now(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call, "⏳ جاري إنشاء النسخة والتحقق منها...")

    def work():
        try:
            ok, m = cloud_backup(force=True)
        except Exception as e:
            ok, m = False, str(e)[:200]
        bot.send_message(call.message.chat.id, f"✅ {m}" if ok else f"❌ <code>{html.escape(m)}</code>", parse_mode="HTML")

    threading.Thread(target=work, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == "cb_restore")
def cb_cloud_restore(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    user_states[call.from_user.id] = "waiting_restore_cloud"
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_green("📌 استعادة من النسخة المثبّتة", "cb_restore_pin"))
    m.add(btn_red("❌ إلغاء", "admin_backup_center"))
    bot.send_message(call.message.chat.id,
                     f"{make_header('♻️ استعادة نسخة احتياطية')}\n\nأرسل ملف <code>hostbot_backup.zip</code> الآن، أو اضغط لاستعادة آخر نسخة مثبّتة.",
                     reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "cb_restore_pin")
def cb_cloud_restore_pin(call):
    if not is_admin(call.from_user.id):
        return
    user_states.pop(call.from_user.id, None)
    safe_answer_cb(call, "⏳ جاري الفحص والاستعادة...")
    try:
        ok, m = cloud_restore_from_pinned()
    except Exception as e:
        ok, m = False, str(e)[:200]
    bot.send_message(call.message.chat.id, f"✅ {get_ui('msg_restore_done')}\n{m}" if ok else f"❌ {html.escape(m)}", parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_restore_cloud", content_types=["document"])
def _do_restore_cloud(msg):
    if not is_admin(msg.from_user.id):
        return
    user_states.pop(msg.from_user.id, None)
    try:
        if (msg.document.file_size or 0) > TG_DOWNLOAD_LIMIT:
            return bot.reply_to(msg, "❌ الملف أكبر من 20MB (حد تيليجرام للبوتات).")
        fi = bot.get_file(msg.document.file_id)
        ok, m = _apply_backup_zip(bot.download_file(fi.file_path))
    except Exception as e:
        ok, m = False, str(e)[:200]
    bot.reply_to(msg, f"✅ {get_ui('msg_restore_done')}\n{m}" if ok else f"❌ {m}")


def _send_user_projects(chat_id, uid):
    sent = 0
    for p in get_projects_by_owner(uid):
        if not p[4] or not os.path.isdir(p[4]):
            continue
        fd, tmp = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        try:
            zip_project_dir(p[4], tmp)
            if os.path.getsize(tmp) > TG_UPLOAD_LIMIT:
                continue
            with open(tmp, "rb") as f:
                bot.send_document(chat_id, f, visible_file_name=f"{uid}_{re.sub(r'[^A-Za-z0-9_.-]', '_', p[2])}.zip",
                                  caption=f"📦 {html.escape(p[2])} · المالك <code>{uid}</code>", parse_mode="HTML")
            sent += 1
        except Exception:
            pass
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass
    return sent


@bot.callback_query_handler(func=lambda c: c.data.startswith("pull_"))
def cb_pull(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call)
    page = int(call.data[5:] or 0)
    users = [u for u in get_all_users_full() if count_user_projects(u[0]) > 0]
    pages = max(1, (len(users) + 7) // 8)
    page = max(0, min(page, pages - 1))
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_green(f"📦 سحب ملفات الكل ({len(users)})", "pullall"))
    for u in users[page * 8:(page + 1) * 8]:
        name = (u[2] or u[1] or str(u[0]))[:20]
        m.add(btn_blue(f"👤 {name} · {count_user_projects(u[0])} مشروع", f"pullu_{u[0]}"))
    for r in _nav_row("pull", page, pages, "admin_panel"):
        m.row(*r)
    safe_edit_msg(f"{make_header('📤 سحب ملفات المستخدمين')}\n\nصفحة {page + 1}/{pages}", call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("pullu_") or c.data == "pullall")
def cb_pull_go(call):
    if not is_admin(call.from_user.id):
        return
    safe_answer_cb(call, "⏳ جاري التجهيز...")
    chat_id = call.message.chat.id

    def work():
        if call.data == "pullall":
            total = sum(_send_user_projects(chat_id, u[0]) for u in get_all_users_full())
        else:
            total = _send_user_projects(chat_id, int(call.data[6:]))
        bot.send_message(chat_id, f"✅ تم إرسال <code>{total}</code> ملف.", parse_mode="HTML")

    threading.Thread(target=work, daemon=True).start()


# ============================================================
# 🎯 التخصيص المباشر (/edit)
# ============================================================
_DX_KEYS = {}
_DX_LABELS = {}
_DX_BYPASS = set()
_DX_ORIGIN_PAGE = {}
_DX_SAFE = ("pbtn_", "pgrp_", "ptpl_", "dx", "admin_premium", "noop")


def _dx_id(key, label=""):
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
    _DX_KEYS[h] = key
    if label:
        _DX_LABELS[h] = label
    return h


def _dx_editor_markup(h):
    key = _DX_KEYS[h]
    cust = _btn_cache.get(key, {})
    label = cust.get("label") or _DX_LABELS.get(h, key)
    style = None if cust.get("style") in (None, "none") else cust.get("style")
    prev = _orig_btn(label, cb="noop", style=style)
    if cust.get("icon"):
        try:
            prev.icon_custom_emoji_id = cust["icon"]
        except Exception:
            pass
    txt = f"{make_header('🎯 تخصيص هذا الزر')}\n\n🏷 الاسم: <b>{html.escape(label)}</b>\n🌈 اللون: {BTN_STYLE_NAMES.get(cust.get('style'), 'افتراضي')}"
    m = types.InlineKeyboardMarkup(row_width=3)
    m.add(prev)
    m.row(btn_blue("✏️ الاسم", f"dxn_{h}"), btn_blue("🎨 الإيموجي", f"dxi_{h}"), btn_blue("🌈 اللون", f"dxc_{h}"))
    m.add(btn_red("♻️ إعادة الافتراضي", f"dxr_{h}"))
    m.add(btn_green("▶️ فتح الزر (تنقّل)", f"dxgo_{h}"))
    m.row(btn_gray("🔙 إغلاق", f"dxback_{h}"), btn_red("🔴 إنهاء التخصيص", "dx_off"))
    return txt, m


@bot.callback_query_handler(func=lambda c: c.from_user.id in _DX_ON and is_admin(c.from_user.id) and not (c.data or "").startswith(_DX_SAFE) and (c.from_user.id, c.data) not in _DX_BYPASS)
def cb_dx_intercept(call):
    key = call.data
    label = key
    try:
        for row in call.message.reply_markup.keyboard:
            for b in row:
                if b.callback_data == key:
                    label = b.text
    except Exception:
        pass
    h = _dx_id(key, label)
    safe_answer_cb(call, "🎯 وضع التخصيص")
    txt, m = _dx_editor_markup(h)
    bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


def _dx_on_message(uid, chat_id):
    _DX_ON.add(uid)
    bot.send_message(chat_id, f"{make_header('🎯 التخصيص المباشر مفعّل')}\n\nاضغط أي زر ليظهر محرره فوراً، أو أرسل /edit للإنهاء.",
                     parse_mode="HTML", reply_markup=main_menu(uid))


@bot.callback_query_handler(func=lambda c: c.data in ("dx_toggle", "dx_off"))
def cb_dx_toggle(call):
    uid = call.from_user.id
    if not is_admin(uid):
        return
    safe_answer_cb(call)
    if call.data == "dx_off" or uid in _DX_ON:
        _DX_ON.discard(uid)
        bot.send_message(call.message.chat.id, "✅ تم إنهاء التخصيص المباشر.", reply_markup=main_menu(uid))
    else:
        _dx_on_message(uid, call.message.chat.id)


@bot.message_handler(commands=["edit"])
def cmd_dx_edit(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    if uid in _DX_ON:
        _DX_ON.discard(uid)
        bot.send_message(msg.chat.id, "✅ تم إنهاء التخصيص المباشر.", reply_markup=main_menu(uid))
    else:
        _dx_on_message(uid, msg.chat.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("dx") and c.data not in ("dx_toggle", "dx_off"))
def cb_dx_actions(call):
    uid = call.from_user.id
    if not is_admin(uid):
        return
    d = call.data
    chat_id, mid = call.message.chat.id, call.message.message_id
    act, _, rest = d.partition("_")
    if act == "dxs":
        h, _, style = rest.rpartition("_")
        key = _DX_KEYS.get(h)
        if key and style in BTN_STYLES:
            set_btn_custom(key, style=style)
        act, rest = "dxe", h
    h = rest
    key = _DX_KEYS.get(h)
    if not key:
        return safe_answer_cb(call, "انتهت الجلسة", show_alert=True)
    safe_answer_cb(call)
    if act == "dxe":
        txt, m = _dx_editor_markup(h)
        safe_edit_msg(txt, chat_id, mid, reply_markup=m, parse_mode="HTML")
    elif act == "dxr":
        reset_btn_custom(key)
        txt, m = _dx_editor_markup(h)
        safe_edit_msg(txt, chat_id, mid, reply_markup=m, parse_mode="HTML")
    elif act == "dxc":
        m = types.InlineKeyboardMarkup(row_width=1)
        for st in BTN_STYLES:
            m.add(btn(BTN_STYLE_NAMES[st], f"dxs_{h}_{st}", style=(st if st != "none" else None)))
        m.add(btn_gray("🔙 رجوع", f"dxe_{h}"))
        safe_edit_msg(make_header("🌈 اختر اللون"), chat_id, mid, reply_markup=m, parse_mode="HTML")
    elif act in ("dxn", "dxi"):
        user_states[uid] = f"waiting_dx_{act[2]}_{h}"
        hint = "أرسل الاسم الجديد (حتى 30 حرفاً) أو - للعودة للأصلي." if act == "dxn" else "أرسل إيموجي مميز ليصبح أيقونة الزر."
        m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", f"dxe_{h}"))
        safe_edit_msg(f"{make_header('✏️ تخصيص')}\n\n{hint}", chat_id, mid, reply_markup=m, parse_mode="HTML")
    elif act == "dxback":
        try:
            bot.delete_message(chat_id, mid)
        except Exception:
            pass
    elif act == "dxgo":
        _DX_BYPASS.add((uid, key))
        call.data = key
        try:
            bot.process_new_callback_query([call])
        except Exception:
            pass


@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id, "").startswith("waiting_dx_"))
def _do_dx_input(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    state = user_states.pop(uid)
    _, _, kind, h = state.split("_", 3)
    key = _DX_KEYS.get(h)
    if not key:
        return bot.reply_to(msg, "❌ انتهت الجلسة.")
    icon, label = _entities_label_icon(msg)
    if kind == "n":
        txt = (msg.text or "").strip()
        set_btn_custom(key, label="" if txt == "-" else txt[:30])
    else:
        if not icon:
            return bot.reply_to(msg, "❌ لم أجد إيموجي مميزاً.")
        set_btn_custom(key, icon=icon)
    txt, m = _dx_editor_markup(h)
    bot.send_message(msg.chat.id, "✅ تم الحفظ\n\n" + txt, reply_markup=m, parse_mode="HTML")


try:
    _gate_h = next(h for h in bot.callback_query_handlers if h["function"] is cb_dx_intercept)
    bot.callback_query_handlers.remove(_gate_h)
    bot.callback_query_handlers.insert(0, _gate_h)
    _inp_h = next(h for h in bot.message_handlers if h["function"] is _do_dx_input)
    bot.message_handlers.remove(_inp_h)
    bot.message_handlers.insert(0, _inp_h)
except Exception:
    pass


# ============================================================
# 🌐 Flask + Web Dashboard v13 (آمن ومحمي ومحدث بالكامل)
# ============================================================
web = Flask(__name__)


@web.route('/health', methods=['GET', 'HEAD'])
@web.route('/ping', methods=['GET', 'HEAD'])
@web.route('/demo', methods=['GET', 'HEAD'])
def health():
    if request.method == 'HEAD':
        return "", 200
    return "OK", 200


@web.route('/api/server_stats')
def api_server_stats():
    token = request.args.get("token")
    if not get_dashboard_user(token):
        return jsonify({"error": "unauthorized"}), 403
    sm = get_server_metrics()
    projs = get_all_projects()
    sm["running"] = sum(1 for pr in projs if is_running(pr[0]))
    sm["total"] = len(projs)
    return jsonify(sm), 200


@web.route('/status', methods=['GET'])
def web_status():
    return {
        "status": "OK",
        "version": "v13 ULTRA STABLE",
        "uptime": calc_uptime(START_TIME.strftime('%Y-%m-%d %H:%M:%S')),
        "started_at": START_TIME.strftime('%Y-%m-%d %H:%M:%S'),
        "self_ping": get_ui("demo_web_ping") != "0",
    }, 200


@web.route('/proxy/status')
def proxy_status():
    return {"status": "ok", "message": "Reverse Proxy شغال"}


@web.route('/proxy/<int:pid>', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
@web.route('/proxy/<int:pid>/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
def proxy(pid, path):
    p = get_project(pid)
    if not p:
        return "<h1>المشروع غير موجود</h1>", 404
    if p[3] != "web":
        return "<h1>هذا ليس مشروع Web</h1>", 400
    if not is_running(pid):
        return "<body style='background:#111;color:#f55;text-align:center;padding:100px;font-family:sans-serif'><h1>المشروع متوقف حالياً</h1></body>", 503
    port = p[11]
    if not port:
        return "<h1>لا يوجد بورت</h1>", 500
    target = f"http://127.0.0.1:{port}/{path}"
    headers = {k: v for k, v in request.headers if k.lower() != 'host'}
    headers['X-Forwarded-For'] = request.remote_addr or ''
    headers['X-Forwarded-Proto'] = request.scheme
    headers['X-Forwarded-Host'] = request.host
    try:
        import requests as req
        resp = req.request(
            method=request.method, url=target, headers=headers,
            data=request.get_data(), cookies=request.cookies, params=request.args,
            allow_redirects=False, timeout=60, stream=False
        )
        excluded = {'content-encoding', 'content-length', 'transfer-encoding', 'connection'}
        out_headers = [(k, v) for k, v in resp.raw.headers.items() if k.lower() not in excluded]
        return Response(resp.content, resp.status_code, out_headers)
    except Exception as e:
        return f"<h1>خطأ في البروكسي</h1><pre>{html.escape(str(e)[:300])}</pre>", 502


WEB_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<title>{{ title }}</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { margin:0; padding:0; box-sizing:border-box; font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; }
body { background:#060a14; color:#e2e8f0; min-height:100vh; padding:22px; position:relative; overflow-x:hidden; }
.container { max-width:1200px; margin:0 auto; }
.topbar { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:14px; background:rgba(15,23,42,0.85); border:1px solid rgba(96,165,250,0.22); border-radius:18px; padding:16px 22px; margin-bottom:22px; }
.brand h1 { font-size:1.5em; background:linear-gradient(135deg,#60a5fa,#a78bfa,#34d399); -webkit-background-clip:text; -webkit-text-fill-color:transparent; font-weight:800; }
.pill-row { display:flex; gap:10px; flex-wrap:wrap; }
.pill { background:rgba(30,41,59,0.9); border:1px solid #334155; padding:6px 14px; border-radius:999px; font-size:0.85em; color:#cbd5e1; }
.pill strong { color:#38bdf8; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:16px; margin-bottom:22px; }
.card { background:rgba(15,23,42,0.9); border:1px solid rgba(148,163,184,0.18); border-radius:16px; padding:18px; display:flex; align-items:center; justify-content:space-between; }
.card .num { font-size:1.9em; font-weight:800; color:#60a5fa; }
.card .lbl { color:#94a3b8; font-size:0.86em; margin-top:4px; }
.toolbar { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:18px; }
.search-box { flex:1; min-width:220px; }
.search-box input { width:100%; background:rgba(15,23,42,0.85); border:1px solid #334155; color:#f1f5f9; padding:10px 15px; border-radius:12px; outline:none; }
.filters { display:flex; gap:8px; }
.fbtn { background:#1e293b; color:#cbd5e1; border:1px solid #334155; padding:8px 14px; border-radius:10px; cursor:pointer; font-size:0.85em; font-weight:600; }
.fbtn.active { background:#2563eb; border-color:#3b82f6; color:#fff; }
.cards-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(355px,1fr)); gap:16px; }
.prj { background:rgba(15,23,42,0.88); border:1px solid rgba(148,163,184,0.2); border-radius:16px; padding:18px; display:flex; flex-direction:column; gap:12px; }
.prj-head { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.badge { padding:4px 11px; border-radius:999px; font-size:0.78em; font-weight:700; }
.badge.run { background:rgba(22,163,74,0.2); color:#4ade80; border:1px solid rgba(74,222,128,0.35); }
.badge.stop { background:rgba(220,38,38,0.2); color:#f87171; border:1px solid rgba(248,113,113,0.35); }
.meta { display:grid; grid-template-columns:1fr 1fr; gap:8px; font-size:0.83em; color:#cbd5e1; background:rgba(30,41,59,0.5); padding:12px; border-radius:10px; }
.meta div span { color:#94a3b8; display:block; font-size:0.88em; }
.err-box { background:rgba(220,38,38,0.12); border:1px solid rgba(248,113,113,0.3); color:#fca5a5; padding:8px 10px; border-radius:8px; font-size:0.8em; word-break:break-word; }
.actions { display:flex; gap:7px; flex-wrap:wrap; margin-top:auto; }
.btn { padding:7px 12px; border:none; border-radius:8px; text-decoration:none; color:#fff; font-weight:600; font-size:0.82em; cursor:pointer; }
.btn-s { background:#16a34a; } .btn-x { background:#dc2626; } .btn-l { background:#2563eb; } .btn-p { background:#7c3aed; } .btn-w { background:#d97706; }
</style>
</head>
<body>
<div class="container">
  <div class="topbar">
    <div class="brand"><h1>{{ title }}</h1><div style="font-size:0.8em;color:#94a3b8">لوحة تحكم المشاريع المعزولة v13</div></div>
    <div class="pill-row">
      <span class="pill">💳 الرصيد: <strong>{{ balance }} {{ currency }}</strong></span>
      <span class="pill">⚙️ الوضع: <strong>{{ 'مدفوع 💎' if paid_mode else 'مجاني 🆓' }}</strong></span>
      <span class="pill">🤝 الإحالات: <strong>{{ referrals }}</strong></span>
    </div>
  </div>
  <div class="grid">
    <div class="card"><div><div class="num">{{ total }}</div><div class="lbl">إجمالي المشاريع</div></div></div>
    <div class="card"><div><div class="num" style="color:#4ade80">{{ running }}</div><div class="lbl">شغالة الآن</div></div></div>
    <div class="card"><div><div class="num" style="color:#f87171">{{ stopped }}</div><div class="lbl">متوقفة</div></div></div>
  </div>
  <div class="toolbar">
    <div class="search-box"><input id="q" type="text" placeholder="🔍 ابحث عن مشروع..." oninput="filterCards()"></div>
    <div class="filters">
      <button class="fbtn active" onclick="setFilter('all', this)">الكل</button>
      <button class="fbtn" onclick="setFilter('run', this)">🟢 شغالة</button>
      <button class="fbtn" onclick="setFilter('stop', this)">🔴 متوقفة</button>
    </div>
  </div>
  <div class="cards-grid" id="cards">
  {% if items %}
    {% for item in items %}
    <div class="prj" data-name="{{ item.name|lower }}" data-state="{{ 'run' if item.is_run else 'stop' }}">
      <div class="prj-head">
        <h3>#{{ item.id }} · {{ item.name }}</h3>
        <span class="badge {{ 'run' if item.is_run else 'stop' }}">{{ '🟢 شغال' if item.is_run else '🔴 متوقف' }}</span>
      </div>
      <div class="meta">
        <div><span>النوع</span><strong>{{ item.ptype_lbl }}</strong></div>
        <div><span>وقت التشغيل</span><strong>{{ item.uptime }}</strong></div>
        <div><span>آخر تشغيل</span><strong>{{ item.last_start or '—' }}</strong></div>
        <div><span>آخر توقف</span><strong>{{ item.last_stop or '—' }}</strong></div>
        <div><span>إعادة التشغيل</span><strong>{{ item.restart_count }} ({{ 'مفعّل' if item.auto_restart else 'معطّل' }})</strong></div>
        <div><span>حالة المكتبات</span><strong>{{ item.install_status }}</strong></div>
        <div><span>آخر نسخ احتياطي</span><strong>{{ item.last_backup or '—' }}</strong></div>
        <div><span>الملف الرئيسي</span><code>{{ item.entry }}</code></div>
      </div>
      {% if item.last_error %}
      <div class="err-box">⚠️ {{ item.last_error }}</div>
      {% endif %}
      <div class="actions">
        {% if item.is_run %}
          <a href="/stop/{{ item.id }}?token={{ token }}" class="btn btn-x">⏹ إيقاف</a>
          <a href="/restart/{{ item.id }}?token={{ token }}" class="btn btn-w">🔁 إعادة</a>
        {% else %}
          <a href="/start/{{ item.id }}?token={{ token }}" class="btn btn-s">▶ تشغيل</a>
        {% endif %}
        <a href="/logs/{{ item.id }}?token={{ token }}" class="btn btn-l">📄 السجلات</a>
        <a href="/diagnose/{{ item.id }}?token={{ token }}" class="btn btn-p">🩺 تشخيص</a>
        <a href="/toggle_auto/{{ item.id }}?token={{ token }}" class="btn btn-l">♻️ {{ 'تعطيل التلقائي' if item.auto_restart else 'تفعيل التلقائي' }}</a>
        {% if item.ptype == 'web' and item.public_url %}
          <a href="{{ item.public_url }}" target="_blank" class="btn btn-s">🌐 فتح</a>
        {% endif %}
      </div>
    </div>
    {% endfor %}
  {% else %}
    <div style="grid-column:1/-1;text-align:center;color:#64748b;padding:50px">لا توجد مشاريع بعد</div>
  {% endif %}
  </div>
</div>
<script>
let curFilter = 'all';
function setFilter(f, el){
  curFilter = f;
  document.querySelectorAll('.fbtn').forEach(b=>b.classList.remove('active'));
  el.classList.add('active');
  filterCards();
}
function filterCards(){
  const q = (document.getElementById('q').value || '').toLowerCase();
  document.querySelectorAll('#cards .prj').forEach(c=>{
    const okName = c.dataset.name.includes(q);
    const okState = curFilter === 'all' || c.dataset.state === curFilter;
    c.style.display = (okName && okState) ? 'flex' : 'none';
  });
}
</script>
</body>
</html>
"""


@web.route('/', methods=['GET', 'HEAD'])
def home():
    token = request.args.get("token")
    if not token:
        if request.method == 'HEAD':
            return "", 200
        up = calc_uptime(START_TIME.strftime('%Y-%m-%d %H:%M:%S'))
        return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="UTF-8"><title>OK - HOST BOT v13</title></head>
<body style="background:#060a14;color:#e2e8f0;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0">
<div style="background:#0f172a;border:1px solid #22c55e55;border-radius:16px;padding:32px 42px;text-align:center">
<div style="font-size:2.4em;font-weight:800;color:#4ade80">OK</div>
<div style="margin-top:8px;color:#94a3b8">HOST BOT v13 ULTRA STABLE (Uptime: {up})</div>
</div></body></html>""", 200
    uid = get_dashboard_user(token)
    if not uid:
        return "<h1>🔒 الرابط غير صالح — افتح الرابط من البوت مجدداً</h1>", 403

    projs = get_projects_by_owner(uid)
    items = []
    running = 0
    for p in projs:
        pm = proj_meta(p)
        run_now = is_running(pm["id"])
        if run_now:
            running += 1
        items.append({
            "id": pm["id"], "name": pm["name"], "ptype": pm["ptype"],
            "ptype_lbl": type_label(pm["ptype"]), "entry": pm["entry"],
            "is_run": run_now, "uptime": calc_uptime(pm["last_start"]) if run_now else "—",
            "last_start": pm["last_start"], "last_stop": pm["last_stop"],
            "restart_count": pm["restart_count"], "auto_restart": pm["auto_restart"] == 1,
            "install_status": pm["install_status"], "last_backup": pm["last_backup"],
            "last_error": pm["last_error"], "public_url": pm["public_url"],
        })
    u = get_user(uid)
    refs = u[11] if u and len(u) > 11 and u[11] else 0
    return render_template_string(
        WEB_HTML, title=get_ui("dashboard_title"), items=items, total=len(projs),
        running=running, stopped=len(projs) - running, token=token,
        balance=f"{get_balance(uid):.2f}", currency=get_ui("currency_name") or "$",
        paid_mode=is_paid_mode(), referrals=refs
    )


@web.route('/start/<int:pid>')
def w_start(pid):
    token = request.args.get("token")
    uid = get_dashboard_user(token)
    if not uid:
        return "🔒 غير مصرح", 403
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return "🚫 غير مصرح", 403
    if get_project_approved(pid) != 1:
        return "⏳ المشروع بانتظار موافقة الإدارة أو مرفوض", 403
    _, msg, _ = start_project(pid, watch=8)
    return f"<body style='background:#0f172a;color:#e2e8f0;text-align:center;padding:80px;font-family:sans-serif'><div>{msg}</div><br><a href='/?token={html.escape(token)}' style='color:#60a5fa'>رجوع</a></body>"


@web.route('/stop/<int:pid>')
def w_stop(pid):
    token = request.args.get("token")
    uid = get_dashboard_user(token)
    if not uid:
        return "🔒 غير مصرح", 403
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return "🚫 غير مصرح", 403
    _, msg = stop_project(pid, reason="إيقاف من لوحة الويب")
    return f"<body style='background:#0f172a;color:#e2e8f0;text-align:center;padding:80px;font-family:sans-serif'><h2>{html.escape(msg)}</h2><br><a href='/?token={html.escape(token)}' style='color:#60a5fa'>رجوع</a></body>"


@web.route('/restart/<int:pid>')
def w_restart(pid):
    token = request.args.get("token")
    uid = get_dashboard_user(token)
    if not uid:
        return "🔒 غير مصرح", 403
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return "🚫 غير مصرح", 403
    stop_project(pid, reason="إعادة تشغيل من لوحة الويب")
    time.sleep(0.5)
    _, msg, _ = start_project(pid, watch=8)
    return f"<body style='background:#0f172a;color:#e2e8f0;text-align:center;padding:80px;font-family:sans-serif'><div>{msg}</div><br><a href='/?token={html.escape(token)}' style='color:#60a5fa'>رجوع</a></body>"


@web.route('/toggle_auto/<int:pid>')
def w_toggle_auto(pid):
    token = request.args.get("token")
    uid = get_dashboard_user(token)
    if not uid:
        return "🔒 غير مصرح", 403
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return "🚫 غير مصرح", 403
    new_v = 0 if proj_meta(p)["auto_restart"] == 1 else 1
    update_project(pid, auto_restart=new_v)
    return f"<body style='background:#0f172a;color:#e2e8f0;text-align:center;padding:80px;font-family:sans-serif'><h2>تم التحديث بنجاح</h2><br><a href='/?token={html.escape(token)}' style='color:#60a5fa'>رجوع</a></body>"


@web.route('/diagnose/<int:pid>')
def w_diagnose(pid):
    token = request.args.get("token")
    uid = get_dashboard_user(token)
    if not uid:
        return "🔒 غير مصرح", 403
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return "🚫 غير مصرح", 403
    res = diagnose_project(pid)
    items_html = "".join(f"<li>{f}</li>" for f in res["findings"])
    hints_html = "".join(f"<li>{h}</li>" for h in res["hints"])
    return f"""<body style='background:#0f172a;color:#e2e8f0;font-family:sans-serif;padding:30px;direction:rtl'>
    <h2>🩺 تشخيص المشروع: {html.escape(p[2])}</h2><p>{res['summary']}</p>
    <h3>نتائج الفحص:</h3><ul>{items_html}</ul><h3>التوصيات:</h3><ul>{hints_html}</ul>
    <br><a href='/?token={html.escape(token)}' style='color:#60a5fa'>رجوع</a></body>"""


@web.route('/logs/<int:pid>')
def w_logs(pid):
    token = request.args.get("token")
    uid = get_dashboard_user(token)
    if not uid:
        return "🔒 غير مصرح", 403
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return "🚫 غير مصرح", 403
    logs = html.escape(get_logs(pid, 200))
    return f"<body style='background:#0f172a;color:#86efac;font-family:monospace;padding:20px'><pre style='white-space:pre-wrap'>{logs}</pre><a href='/?token={html.escape(token)}' style='color:#60a5fa'>رجوع</a></body>"


# ============================================================
# 🌐 Tunnel + Heartbeat + التشغيل الرئيسي
# ============================================================
def start_ssh_tunnel():
    port = int(os.environ.get("PORT", 8080))
    try:
        proc = subprocess.Popen(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=30",
             "-R", f"80:localhost:{port}", "nokey@localhost.run"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        start = time.time()
        for line in proc.stdout:
            if time.time() - start > 45:
                break
            m = re.search(r'https://[a-zA-Z0-9-]+\.lhr\.life', line)
            if m:
                return m.group(0)
    except Exception:
        pass
    return None


def start_cloudflare_tunnel():
    cf_bin = "/tmp/cloudflared"
    tunnel_log = "/tmp/cloudflare_tunnel.log"
    try:
        if not os.path.exists(cf_bin):
            arch = platform.machine()
            url = ("https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
                   if arch in ("aarch64", "arm64")
                   else "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64")
            urllib.request.urlretrieve(url, cf_bin)
            os.chmod(cf_bin, 0o755)
        port = int(os.environ.get("PORT", 8080))
        with open(tunnel_log, "w") as log_fp:
            subprocess.Popen([cf_bin, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
                             stdout=log_fp, stderr=subprocess.STDOUT)
        for _ in range(30):
            time.sleep(2)
            try:
                with open(tunnel_log, "r") as f:
                    m = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', f.read())
                if m:
                    return m.group(0)
            except Exception:
                pass
    except Exception as e:
        print(f"[Tunnel/CF] {e}")
    return None


def start_tunnel():
    global TUNNEL_URL
    url = start_cloudflare_tunnel() or start_ssh_tunnel()
    if url:
        TUNNEL_URL = url
        set_ui("public_url", url)
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(admin_id, f"<b>🌐 الرابط العام جاهز</b>\n\n<code>{url}</code>", parse_mode="HTML")
            except Exception:
                pass
        return url
    return None


def keep_alive():
    while True:
        time.sleep(30)
        try:
            running_count = sum(1 for p in get_all_projects() if is_running(p[0]))
            print(f"[Heartbeat] {datetime.now().strftime('%H:%M:%S')} | running={running_count} | tunnel={TUNNEL_URL or 'none'}")
            ext_url = os.environ.get("RENDER_EXTERNAL_URL") or TUNNEL_URL
            if ext_url and get_ui("demo_web_ping") != "0" and int(time.time()) % 600 < 35:
                try:
                    urllib.request.urlopen(ext_url.rstrip("/") + "/ping", timeout=8).read()
                except Exception:
                    pass
        except Exception:
            pass


def run_bot():
    print("[Bot] HOST BOT v13 ULTRA STABLE started")
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=25)
        except Exception as e:
            print(f"[Bot] Polling error: {redact_secrets(str(e))}")
            time.sleep(5)


def _is_port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def run_web():
    port = int(os.environ.get("PORT", 8080))
    if not _is_port_free(port):
        port = get_free_port()
    for _ in range(10):
        try:
            print(f"[Web] http://0.0.0.0:{port}")
            web.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
            break
        except OSError:
            port = get_free_port()


if __name__ == "__main__":
    if "--web-only" in sys.argv or os.environ.get("WEB_ONLY") == "1":
        print("=" * 55)
        print("HOST BOT v13.0 - WEB MONITORING ONLY (OK)")
        print("=" * 55)
        threading.Thread(target=keep_alive, daemon=True).start()
        run_web()
        sys.exit(0)

    print("=" * 55)
    print("HOST BOT v13.0 — ULTRA STABLE STARTING")
    print("=" * 55)

    on_cloud = any([
        os.environ.get("RENDER_EXTERNAL_URL"),
        os.environ.get("KOYEB_PUBLIC_DOMAIN"),
        os.environ.get("RAILWAY_PUBLIC_DOMAIN"),
        os.environ.get("FLY_APP_NAME"),
    ])

    boot_recover()
    for _bg in (cloud_backup_loop, local_24h_backup_loop, keep_alive, unified_watchdog, snapshot_loop, boot_restore_projects, run_bot):
        threading.Thread(target=_bg, daemon=True).start()

    if not on_cloud:
        threading.Thread(target=lambda: (time.sleep(5), start_tunnel()), daemon=True).start()
    else:
        print(f"[*] Cloud: {get_public_url()}")

    run_web()