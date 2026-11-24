# ============================================================
# HOST BOT v12.0 — نسخة كاملة
# - إيموجي معبّر لكل زر
# - شرح منسق بـ HTML (تسطير + تكبير + تمييل)
# - زر المطور داخل قسم "مساعدة"
# - دعم 20+ منصة استضافة
# - Web Dashboard مع SVG
# ============================================================

import os, sys, json, time, shutil, signal, sqlite3, subprocess
import threading, socket, re, random, zipfile, platform, urllib.request
import secrets, ast, html
from types import SimpleNamespace
from datetime import datetime, timedelta

import telebot
from telebot import types
from flask import Flask, render_template_string, request, Response


# ============================================================
# ضمان المكتبات
# ============================================================
def ensure_packages():
    pkgs = {"telebot": "pyTelegramBotAPI", "flask": "flask", "requests": "requests"}
    missing = []
    for imp, pip_name in pkgs.items():
        try:
            __import__(imp)
        except ImportError:
            missing.append(pip_name)
    if missing:
        print(f"[Setup] مكتبات ناقصة: {missing}")
        for pip_name in missing:
            try:
                subprocess.run([sys.executable, "-m", "pip", "install",
                    "--no-cache-dir", "--user", pip_name], timeout=300, check=False)
            except Exception as e:
                print(f"[Setup] فشل {pip_name}: {e}")
        print("[Setup] تم")

ensure_packages()


# ============================================================
# الإعدادات
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "9062665659:AAE2ubEi1CWrrsRM83Pp0XyKMlqlelpTCvQ")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "7325566792").split(",")]
DEVELOPER_USERNAME = "E_E_72"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_DIR = os.path.join(BASE_DIR, "hosted_projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)
DB_PATH = os.path.join(BASE_DIR, "host_v12.db")

RUNNING = {}
user_states = {}
START_TIME = datetime.now()
PORT_COUNTER = 7000
TUNNEL_URL = None
DB_LOCK = threading.RLock()


# ============================================================
# قاعدة البيانات
# ============================================================
def init_db():
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute("PRAGMA foreign_keys = ON")
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
            FOREIGN KEY (owner_id) REFERENCES users(user_id) ON DELETE CASCADE)''')
        c.execute('''CREATE TABLE IF NOT EXISTS ui_settings (key TEXT PRIMARY KEY, value TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS action_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT,
            details TEXT, timestamp TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS dashboard_tokens (
            token TEXT PRIMARY KEY, user_id INTEGER, created_at TEXT)''')
        conn.commit()
        conn.close()

init_db()


def _conn():
    return sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)


# ============================================================
# المستخدمين
# ============================================================
def get_user(uid):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        r = c.fetchone(); conn.close(); return r

def add_user(uid, username="", first_name=""):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        try:
            c.execute("INSERT INTO users (user_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
                (uid, username, first_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit(); return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

def get_all_users():
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE is_banned=0")
        r = [x[0] for x in c.fetchall()]; conn.close(); return r

def get_user_count():
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        r = c.fetchone()[0]; conn.close(); return r

def get_all_users_full():
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT * FROM users ORDER BY created_at DESC")
        r = c.fetchall(); conn.close(); return r

def ban_user(uid):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,))
        conn.commit(); conn.close()

def unban_user(uid):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (uid,))
        conn.commit(); conn.close()

def set_user_theme(uid, color):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("UPDATE users SET theme_color=? WHERE user_id=?", (color, uid))
        conn.commit(); conn.close()

def is_banned(uid):
    u = get_user(uid); return u and u[4] == 1

def is_premium(uid):
    u = get_user(uid); return u and u[5] == 1

def get_max_projects(uid):
    u = get_user(uid)
    if not u:
        return int(float(get_ui("free_trial_projects") or 1)) if is_paid_mode() else 3
    if is_paid_mode():
        return u[6] if u[6] is not None else int(float(get_ui("free_trial_projects") or 1))
    return u[6] or 3


def is_paid_mode():
    return get_ui("bot_mode") == "paid"


def get_balance(uid):
    u = get_user(uid)
    if not u or len(u) < 10 or u[9] is None:
        return 0.0
    return float(u[9])


def add_balance(uid, amount):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE user_id=?", (float(amount), uid))
        conn.commit(); conn.close()


def set_max_projects(uid, val):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("UPDATE users SET max_projects = ? WHERE user_id=?", (int(val), uid))
        conn.commit(); conn.close()


def add_max_projects(uid, delta):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("UPDATE users SET max_projects = COALESCE(max_projects, 0) + ? WHERE user_id=?", (int(delta), uid))
        conn.commit(); conn.close()


def register_referral(new_uid, ref_uid):
    if not ref_uid or ref_uid == new_uid:
        return False
    if not get_user(ref_uid):
        return False
    bonus = float(get_ui("referral_bonus") or 0.5)
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT referred_by FROM users WHERE user_id=?", (new_uid,))
        r = c.fetchone()
        if r and r[0]:
            conn.close()
            return False
        c.execute("UPDATE users SET referred_by=? WHERE user_id=?", (ref_uid, new_uid))
        c.execute("UPDATE users SET referral_count = COALESCE(referral_count, 0) + 1, balance = COALESCE(balance, 0) + ? WHERE user_id=?", (bonus, ref_uid))
        conn.commit(); conn.close()
    return bonus


def create_recharge_request(uid, amount, proof):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("INSERT INTO recharge_requests (user_id, amount, proof, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                  (uid, float(amount), proof, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        rid = c.lastrowid; conn.commit(); conn.close(); return rid


def get_recharge_request(rid):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT * FROM recharge_requests WHERE id=?", (rid,))
        r = c.fetchone(); conn.close(); return r


def get_pending_recharges():
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT * FROM recharge_requests WHERE status='pending' ORDER BY id DESC LIMIT 20")
        r = c.fetchall(); conn.close(); return r


def resolve_recharge_request(rid, status):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("UPDATE recharge_requests SET status=? WHERE id=? AND status='pending'", (status, rid))
        changed = c.rowcount; conn.commit(); conn.close(); return changed > 0


def create_gift_code(code, amount, max_uses=1):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("REPLACE INTO gift_codes (code, amount, max_uses, used_count, created_at) VALUES (?, ?, ?, 0, ?)",
                  (code.strip().upper(), float(amount), int(max_uses), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit(); conn.close()


def redeem_gift_code(uid, code):
    code = code.strip().upper()
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT amount, max_uses, used_count FROM gift_codes WHERE code=?", (code,))
        r = c.fetchone()
        if not r:
            conn.close(); return False, "❌ كود الشحن غير صحيح."
        amt, mx, used = r
        if used >= mx:
            conn.close(); return False, "⚠️ تم استهلاك هذا الكود بالكامل."
        c.execute("UPDATE gift_codes SET used_count = used_count + 1 WHERE code=?", (code,))
        c.execute("UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE user_id=?", (float(amt), uid))
        conn.commit(); conn.close()
    return True, amt


# ============================================================
# المشاريع
# ============================================================
def get_projects_by_owner(owner_id):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT * FROM projects WHERE owner_id=? ORDER BY id DESC", (owner_id,))
        r = c.fetchall(); conn.close(); return r

def get_project(pid):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT * FROM projects WHERE id=?", (pid,))
        r = c.fetchone(); conn.close(); return r

def get_all_projects():
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT * FROM projects ORDER BY id DESC")
        r = c.fetchall(); conn.close(); return r

def add_project(owner_id, name, ptype, path, entry):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("""INSERT INTO projects (owner_id, name, ptype, path, entry, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (owner_id, name, ptype, path, entry, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        pid = c.lastrowid; conn.commit(); conn.close(); return pid

def update_project(project_id, **kw):
    if not kw: return
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        for k, v in kw.items():
            c.execute(f"UPDATE projects SET {k}=? WHERE id=?", (v, project_id))
        conn.commit(); conn.close()

def delete_project(pid):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("DELETE FROM projects WHERE id=?", (pid,))
        conn.commit(); conn.close()

def count_user_projects(uid):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM projects WHERE owner_id=?", (uid,))
        r = c.fetchone()[0]; conn.close(); return r


# ============================================================
# إعدادات الواجهة + الأزرار (بإيموجي معبّر)
# ============================================================
DEFAULT_UI = {
    "welcome_text": "اهلا بك في Host Bot v12",
    "dashboard_title": "HOST BOT v12",
    "footer_text": "━━━━━━━━━━━━━━━━━━━━",
    "theme_color": "#2563eb",
    "bot_mode": "free",
    "currency_name": "$",
    "slot_price": "2.0",
    "vip_price": "5.0",
    "referral_bonus": "0.5",
    "free_trial_projects": "1",
    "recharge_info": "لشحن رصيدك، قم بتحويل المبلغ المطلوب ثم أرسل رقم الحوالة أو إثبات الدفع ليتم التفعيل فوراً.",
}

# key: (الاسم+الإيموجي, اللون, الصف)
DEFAULT_BUTTONS = {
    "btn_my_projects":  ("📦 مشاريعي", "primary", 1),
    "btn_new_project":  ("➕ مشروع جديد", "success", 1),
    "btn_account":      ("👤 حسابي", "primary", 2),
    "btn_help":         ("❓ مساعدة", "primary", 2),
    "btn_wallet":       ("💳 محفظتي والشحن", "success", 3),
    "btn_support":      ("💬 تواصل مع الدعم", "primary", 3),
    "btn_admin":        ("👑 لوحة المطور", "danger", 4),
    "btn_dashboard":    ("🌐 لوحة التحكم", "primary", 0),
}


def get_button(key):
    try:
        with DB_LOCK:
            conn = _conn(); c = conn.cursor()
            c.execute("SELECT value FROM ui_settings WHERE key=?", (f"cfg_{key}",))
            r = c.fetchone(); conn.close()
            if r and r[0]:
                parts = r[0].split("|")
                if len(parts) == 3:
                    return (parts[0], parts[1], int(parts[2]))
    except: pass
    return DEFAULT_BUTTONS.get(key, (key, "primary", 0))


def set_button(key, name, style, row):
    set_ui(f"cfg_{key}", f"{name}|{style}|{row}")


def get_all_buttons():
    return {k: get_button(k) for k in DEFAULT_BUTTONS.keys()}


def get_ui(key):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT value FROM ui_settings WHERE key=?", (key,))
        r = c.fetchone(); conn.close()
        return r[0] if r else DEFAULT_UI.get(key, "")


def set_ui(key, value):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("REPLACE INTO ui_settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit(); conn.close()


def get_all_ui():
    result = dict(DEFAULT_UI)
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT key, value FROM ui_settings")
        for k, v in c.fetchall():
            result[k] = v
        conn.close()
    return result


# ============================================================
# سجل الأحداث
# ============================================================
def log_action(user_id, action, details=""):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("INSERT INTO action_logs (user_id, action, details, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, action, details, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit(); conn.close()


# ============================================================
# Utilities
# ============================================================
def get_file_size_str(fp):
    try:
        size = os.path.getsize(fp)
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024: return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
    except: return "?"

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close(); return ip
    except: return "127.0.0.1"

def get_public_url():
    global TUNNEL_URL
    if TUNNEL_URL: return TUNNEL_URL
    platforms = [
        ("RENDER_EXTERNAL_URL", "{}"), ("RAILWAY_PUBLIC_DOMAIN", "https://{}"),
        ("KOYEB_PUBLIC_DOMAIN", "https://{}"), ("FLY_APP_NAME", "https://{}.fly.dev"),
        ("HEROKU_APP_NAME", "https://{}.herokuapp.com"), ("VERCEL_URL", "https://{}"),
        ("NETLIFY_URL", "https://{}"), ("RENDER_EXTERNAL_HOSTNAME", "https://{}"),
        ("CYCLIC_URL", "https://{}"), ("GLITCH_PROJECT_DOMAIN", "https://{}"),
        ("REPLIT_DEV_DOMAIN", "https://{}"), ("NORTHFLANK_URL", "https://{}"),
        ("ADAPTABLE_URL", "https://{}"), ("FL0_DOMAIN", "https://{}"),
        ("PUBLIC_URL", "{}"), ("APP_URL", "{}"), ("SERVER_DOMAIN", "https://{}"),
    ]
    for env_key, fmt in platforms:
        v = os.environ.get(env_key)
        if v:
            TUNNEL_URL = fmt.format(v.rstrip("/"))
            return TUNNEL_URL
    try:
        with DB_LOCK:
            conn = _conn(); c = conn.cursor()
            c.execute("SELECT value FROM ui_settings WHERE key='public_url'")
            r = c.fetchone(); conn.close()
            if r and r[0]: TUNNEL_URL = r[0]; return r[0]
    except: pass
    port = int(os.environ.get("PORT", 8080))
    return f"http://{get_local_ip()}:{port}"

def calc_uptime(start_str):
    if not start_str: return "—"
    try:
        s = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
        t = int((datetime.now() - s).total_seconds())
        if t < 0: t = 0
        h, m, sec = t // 3600, (t % 3600) // 60, t % 60
        return f"{h:02d}:{m:02d}:{sec:02d}"
    except: return "—"

def get_free_port():
    global PORT_COUNTER
    while PORT_COUNTER < 65000:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", PORT_COUNTER)); s.close()
            p = PORT_COUNTER; PORT_COUNTER += 1; return p
        except:
            PORT_COUNTER += 1
        finally:
            try: s.close()
            except: pass
    return 7500

def list_dir_files(dirpath):
    result = []
    if not os.path.exists(dirpath): return result
    for root, dirs, files in os.walk(dirpath):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, dirpath)
            result.append(rel)
    return result


# ============================================================
# الأزرار
# ============================================================
def btn(text, cb=None, url=None, style=None):
    b = types.InlineKeyboardButton(text=text, callback_data=cb, url=url)
    if style:
        try: b.style = style
        except: pass
    return b

def btn_green(t, cb=None, url=None): return btn(t, cb=cb, url=url, style="success")
def btn_red(t, cb=None, url=None): return btn(t, cb=cb, url=url, style="danger")
def btn_blue(t, cb=None, url=None): return btn(t, cb=cb, url=url, style="primary")
def btn_gray(t, cb=None, url=None): return btn(t, cb=cb, url=url)

def make_custom_btn(key, cb=None, url=None):
    name, style, _ = get_button(key)
    return btn(name, cb=cb, url=url, style=style if style else None)


# ============================================================
# الزخرفة
# ============================================================
def make_header(title):
    return "━━━━━━━━━━━━━━━━━━━━━━━━━\n" + f"  <b>{title}</b>\n" + "━━━━━━━━━━━━━━━━━━━━━━━━━"

def make_section(title): return f"\n<b>{title}</b>\n"
def make_footer(): return "━━━━━━━━━━━━━━━━━━━━━━━━━"


# ============================================================
# الفحص القوي (AST)
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
            issues["syntax"].append({"line": e.lineno, "col": e.offset, "msg": e.msg, "text": (e.text or "").strip()})
            return issues
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names: imports.add(n.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module: imports.add(node.module.split('.')[0])
        issues["stats"]["imports"] = sorted(imports)
        known_libs = {"telebot":"pyTelegramBotAPI","requests":"requests","flask":"flask",
            "httpx":"httpx","bs4":"beautifulsoup4","aiohttp":"aiohttp","PIL":"Pillow",
            "pandas":"pandas","numpy":"numpy","sqlalchemy":"SQLAlchemy","pydantic":"pydantic",
            "fastapi":"fastapi","uvicorn":"uvicorn","dotenv":"python-dotenv","yaml":"pyyaml",
            "bcrypt":"bcrypt","gunicorn":"gunicorn"}
        required = set()
        for imp in imports:
            if imp in known_libs: required.add(known_libs[imp])
        issues["stats"]["required_libs"] = sorted(required)
        for i, line in enumerate(lines, 1):
            if len(line) > 120: issues["warnings"].append(f"السطر {i} طويل ({len(line)} حرف)")
        token_found = False
        for line in lines:
            if re.search(r'BOT_TOKEN\s*=\s*["\']([0-9]{8,12}:[A-Za-z0-9_-]{30,50})["\']', line):
                token_found = True; break
        if token_found: issues["info"].append("يوجد BOT_TOKEN")
        else: issues["warnings"].append("لا يوجد BOT_TOKEN ظاهر")
        issues["stats"]["functions"] = sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        issues["stats"]["classes"] = sum(1 for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    except Exception as e:
        issues["syntax"].append({"line": 0, "col": 0, "msg": str(e), "text": ""})
    return issues


def format_check_result(fp, issues):
    name = os.path.basename(fp)
    lines = [make_header(f"🔍 فحص {name}"), ""]
    stats = issues.get("stats", {})
    lines.append("<b><u>📊 الاحصائيات:</u></b>")
    lines.append(f"  📏 السطور: <code>{stats.get('total_lines', 0)}</code>")
    lines.append(f"  📦 الحجم: <code>{get_file_size_str(fp)}</code>")
    lines.append(f"  🔧 الدوال: <code>{stats.get('functions', 0)}</code>")
    lines.append(f"  📚 الكلاسات: <code>{stats.get('classes', 0)}</code>")
    lines.append("")
    syntax = issues.get("syntax", [])
    if syntax:
        lines.append("<b><u>❌ اخطاء الصيغة:</u></b>")
        for s in syntax:
            lines.append(f"  📍 السطر {s['line']}: <code>{s['msg']}</code>")
            if s['text']: lines.append(f"  <code>{s['text'][:60]}</code>")
        lines.append("")
    else:
        lines.append("<b><u>✅ الصيغة سليمة</u></b>"); lines.append("")
    imports = stats.get("imports", [])
    if imports:
        lines.append("<b><u>📚 الاستيرادات:</u></b>")
        for imp in imports[:15]: lines.append(f"  • <code>{imp}</code>")
        if len(imports) > 15: lines.append(f"  ... +{len(imports) - 15}")
        lines.append("")
    required = stats.get("required_libs", [])
    if required:
        lines.append("<b><u>📦 المكتبات المطلوبة:</u></b>")
        for lib in required: lines.append(f"  • <code>{lib}</code>")
        lines.append("")
    warnings = issues.get("warnings", [])
    if warnings:
        lines.append("<b><u>⚠️ تنبيهات:</u></b>")
        for w in warnings[:10]: lines.append(f"  • {w}")
        lines.append("")
    info = issues.get("info", [])
    if info:
        lines.append("<b><u>ℹ️ معلومات:</u></b>")
        for i in info: lines.append(f"  • {i}")
    return "\n".join(lines)


def check_python_file(fp):
    issues = analyze_python_file(fp)
    result = format_check_result(fp, issues)
    ok = not issues["syntax"]
    return ok, result


def check_json_file(fp):
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        if isinstance(data, dict): count, type_name = len(data), "كائن"
        elif isinstance(data, list): count, type_name = len(data), "قائمة"
        else: count, type_name = 0, type(data).__name__
        return True, f"<b>✅ JSON سليم</b>\n\n<code>{os.path.basename(fp)}</code>\n📦 الحجم: <code>{get_file_size_str(fp)}</code>\n🔤 النوع: <code>{type_name}</code>\n🔑 عدد العناصر: <code>{count}</code>"
    except json.JSONDecodeError as e:
        return False, f"<b>❌ خطأ JSON</b>\n📍 السطر: <b>{e.lineno}</b>\n🔤 <code>{e.msg}</code>"
    except Exception as e:
        return False, f"❌ خطأ: {e}"


def check_shell_file(fp):
    try:
        r = subprocess.run(["bash", "-n", fp], capture_output=True, text=True, timeout=10)
        if r.returncode == 0: return True, f"<b>✅ Shell سليم</b>\n📄 <code>{os.path.basename(fp)}</code>"
        return False, f"<b>❌ خطأ Shell</b>\n<pre>{r.stderr[-400:]}</pre>"
    except FileNotFoundError: return True, "ℹ️ Shell: تم التخطي"
    except Exception as e: return False, f"❌ خطأ: {e}"


def check_requirements_file(fp):
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        lines = [l.strip() for l in content.splitlines() if l.strip() and not l.startswith("#")]
        if not lines: return False, "<b>⚠️ الملف فاضي</b>"
        result = f"<b>✅ requirements.txt سليم</b>\n\n📦 <b>العدد:</b> <code>{len(lines)}</code>\n\n"
        for line in lines[:30]:
            pkg_name = re.split(r'[=<>!]', line)[0].strip()
            result += f"  • <code>{pkg_name}</code>\n"
        if len(lines) > 30: result += f"  ... +{len(lines) - 30}\n"
        return True, result
    except Exception as e: return False, f"❌ خطأ: {e}"


def check_any_file(fp):
    if not os.path.exists(fp): return False, "<b>❌ الملف غير موجود</b>"
    name = os.path.basename(fp).lower()
    if name == "requirements.txt": return check_requirements_file(fp)
    elif name.endswith(".py"): return check_python_file(fp)
    elif name.endswith(".json"): return check_json_file(fp)
    elif name.endswith(".sh"): return check_shell_file(fp)
    elif name.endswith(".txt"): return check_requirements_file(fp)
    else:
        size = get_file_size_str(fp)
        return True, f"<b>✅ مدعوم</b>\n📄 <code>{name}</code> ({size})"


# ============================================================
# استخراج معلومات البوت
# ============================================================
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
        if not info["token"]: return info
        try:
            import requests as req
            r = req.get(f"https://api.telegram.org/bot{info['token']}/getMe", timeout=10)
            if r.status_code == 200:
                d = r.json()
                if d.get("ok"):
                    info["username"] = d["result"].get("username")
                    info["name"] = d["result"].get("first_name")
                else: info["token"] = None
            else: info["token"] = None
        except: info["token"] = None
    except: pass
    return info


# ============================================================
# إدارة ملفات المشروع
# ============================================================
def get_project_files(pid):
    p = get_project(pid)
    if not p: return []
    try: return json.loads(p[6] or "[]")
    except: return []

def add_project_file(pid, filename):
    files = get_project_files(pid)
    if filename not in files: files.append(filename)
    update_project(pid, files=json.dumps(files))


# ============================================================
# جلسات آمنة
# ============================================================
def create_dashboard_token(uid):
    token = secrets.token_urlsafe(32)
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("DELETE FROM dashboard_tokens WHERE user_id=?", (uid,))
        c.execute("INSERT INTO dashboard_tokens (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, uid, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit(); conn.close()
    return token

def get_dashboard_user(token):
    if not token: return None
    try:
        with DB_LOCK:
            conn = _conn(); c = conn.cursor()
            c.execute("SELECT user_id FROM dashboard_tokens WHERE token=?", (token,))
            r = c.fetchone(); conn.close()
            return r[0] if r else None
    except: return None

def get_dashboard_link(uid):
    token = create_dashboard_token(uid)
    base = get_public_url()
    return f"{base}/?token={token}"
    
# ============================================================
# تشغيل / إيقاف المشاريع
# ============================================================
def start_project(pid):
    p = get_project(pid)
    if not p:
        return False, "المشروع غير موجود", None

    key = f"p{pid}"
    if key in RUNNING and RUNNING[key].poll() is None:
        return False, "المشروع شغال بالفعل", None

    entry_path = os.path.join(p[4], p[5])
    if not os.path.exists(entry_path):
        return False, f"الملف <code>{p[5]}</code> غير موجود", None

    if p[5].endswith(".py"):
        ok, msg = check_python_file(entry_path)
        if not ok:
            return False, f"<b>فشل الفحص:</b>\n\n{msg[:800]}", None

    try:
        env = os.environ.copy()
        port = 0
        public_url = ""

        if p[3] == "web":
            port = get_free_port()
            env["PORT"] = str(port)
            env["FLASK_RUN_PORT"] = str(port)
            env["HOST"] = "0.0.0.0"
            base = get_public_url()
            public_url = f"{base}/proxy/{pid}"

        log_path = os.path.join(p[4], "output.log")
        lf = open(log_path, "a", encoding="utf-8")
        lf.write(f"\n\n===== START {datetime.now()} =====\n")
        lf.flush()

        def _log_line(line):
            try:
                lf.write(line + "\n"); lf.flush()
            except Exception:
                pass

        if p[5].endswith(".py"):
            ensure_venv(p[4], log=_log_line)
            py_exe = venv_python(p[4]) or sys.executable
            deps_marker = os.path.join(p[4], ".deps_installed")
            needed_now = set()
            try:
                needed_now = set(resolve_required_pkgs(entry_path))
            except Exception:
                pass
            for line in (p[7] or "").splitlines():
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
                _log_line(f"🪄 تثبيت تلقائي ذكي ({len(needed_now)} مكتبة)...")
                for pkg in sorted(needed_now):
                    ok, msg = pip_install_in(p[4], pkg)
                    _log_line(f"  {'✅' if ok else '❌'} {pkg}")
                try:
                    with open(deps_marker, "w", encoding="utf-8") as f:
                        f.write(sig)
                except Exception:
                    pass
            cmd = [py_exe, "-u", p[5]]
        elif p[5].endswith(".js"):
            node_bin = shutil.which("node")
            if not node_bin:
                return False, ("❌ Node.js غير مثبت على السيرفر.\n"
                                "ثبّته أولاً: <code>pkg install nodejs</code> (Termux) "
                                "أو <code>apt install nodejs npm</code>"), None
            pkg_json = os.path.join(p[4], "package.json")
            if os.path.exists(pkg_json) and not os.path.exists(os.path.join(p[4], "node_modules")):
                _log_line("📦 npm install ...")
                try:
                    subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=p[4],
                                   stdout=lf, stderr=subprocess.STDOUT, timeout=600)
                except Exception as e:
                    _log_line(f"⚠️ npm install: {e}")
            cmd = [node_bin, p[5]]
        else:
            cmd = ["bash", p[5]]
        def _detach_child():
            os.setsid()
            try:
                signal.signal(signal.SIGHUP, signal.SIG_IGN)
            except Exception:
                pass
        kw = {"preexec_fn": _detach_child} if os.name != "nt" \
            else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

        proc = subprocess.Popen(
            cmd, cwd=p[4], stdout=lf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env, **kw
        )
        RUNNING[key] = proc

        update_project(
            pid, pid=proc.pid, status="running", port=port,
            public_url=public_url,
            last_start=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        time.sleep(5)

        if proc.poll() is not None:
            error_msg = "(لا توجد تفاصيل)"
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    error_msg = "".join(f.readlines()[-15:])
            except:
                pass
            update_project(pid, status="stopped", pid=0)
            return False, (
                f"<b>❌ المشروع مات فورا!</b>\n\n"
                f"📄 الملف: <code>{p[5]}</code>\n\n"
                f"<b><u>🔤 آخر سطور:</u></b>\n"
                f"<pre>{error_msg[-1000:]}</pre>"
            ), None

        info = {
            "project": p[2],
            "entry": p[5],
            "ptype": p[3],
            "pid": proc.pid,
            "port": port,
            "public_url": public_url,
            "bot_username": p[9] or "",
            "bot_name": p[10] or "", 
        }

        return True, "تم التشغيل", info

    except Exception as e:
        return False, f"خطأ: <code>{str(e)[:300]}</code>", None


def stop_project(pid):
    p = get_project(pid)
    if not p:
        return False, "المشروع غير موجود"

    key = f"p{pid}"
    stopped = False

    if key in RUNNING:
        proc = RUNNING[key]
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
            proc.wait(timeout=5)
            stopped = True
        except:
            try:
                proc.kill()
                stopped = True
            except:
                pass
        RUNNING.pop(key, None)

    if p[14]:
        try:
            os.kill(p[14], signal.SIGTERM)
            stopped = True
        except:
            pass

    update_project(
        pid, pid=0, status="stopped",
        last_stop=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    return True, "تم الإيقاف" if stopped else "كان متوقف"


def is_running(pid):
    key = f"p{pid}"
    if key in RUNNING:
        p = RUNNING[key]
        if p.poll() is None:
            return True
        RUNNING.pop(key, None)
        update_project(pid, pid=0, status="stopped")
    return False


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
            return "".join(lines[-n:])
    except Exception as e:
        return f"خطأ: {e}"


def get_live_uptime(pid):
    p = get_project(pid)
    if not p:
        return "—"
    if not is_running(pid):
        return "متوقف"
    return calc_uptime(p[16])


# ============================================================
# تثبيت المكتبات
# ============================================================
def install_single_pkg(pid, pkg):
    """تثبيت مكتبة لمشروع محدد — داخل بيئته المعزولة (لا تؤثر على أي مشروع آخر ولا على بوت الاستضافة)."""
    p = get_project(pid)
    if not p:
        return False, "المشروع غير موجود"
    return pip_install_in(p[4], pkg)


def install_from_requirements(pid, reqs):
    lines = [
        l.strip() for l in (reqs or "").split("\n")
        if l.strip() and not l.startswith("#")
    ]
    if not lines:
        return False, "لا توجد مكتبات", []

    results = []
    success = 0
    fail = 0

    for i, pkg in enumerate(lines, 1):
        ok, msg = install_single_pkg(pid, pkg)
        if ok:
            success += 1
        else:
            fail += 1
        results.append((i, len(lines), pkg, ok, msg))

    return True, f"نجح {success} / فشل {fail}", results


# ============================================================
# 🛡️ العزل الكامل بين المشاريع (venv مستقل لكل مشروع)
# يحل مشكلة: تثبيت/تشغيل مشروع يؤثر على مشروع آخر أو على بوت الاستضافة نفسه
# ============================================================
VENV_DIRNAME = ".venv"


def venv_dir(pdir):
    return os.path.join(pdir, VENV_DIRNAME)


def venv_python(pdir):
    """مسار بايثون الخاص بمشروع، أو None إن لم يُنشأ بعد."""
    vd = venv_dir(pdir)
    py = os.path.join(vd, "Scripts", "python.exe") if os.name == "nt" else os.path.join(vd, "bin", "python3")
    return py if os.path.exists(py) else None


def venv_pip(pdir):
    vd = venv_dir(pdir)
    pip = os.path.join(vd, "Scripts", "pip.exe") if os.name == "nt" else os.path.join(vd, "bin", "pip")
    return pip if os.path.exists(pip) else None


def ensure_venv(pdir, log=None):
    """ينشئ بيئة بايثون معزولة لهذا المشروع إن لم تكن موجودة (لا تحتاج إنترنت)."""
    if venv_python(pdir):
        return True, "موجودة"
    try:
        if log:
            log("🧪 إنشاء بيئة بايثون معزولة لهذا المشروع...")
        r = subprocess.run([sys.executable, "-m", "venv", venv_dir(pdir)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not venv_python(pdir):
            msg = (r.stderr or r.stdout or "")[-300:]
            if log:
                log(f"⚠️ تعذّر إنشاء بيئة معزولة ({msg}) — سيُستخدم بايثون المشترك.")
            return False, msg
        if log:
            log("✅ البيئة المعزولة جاهزة.")
        return True, "تم الإنشاء"
    except Exception as e:
        if log:
            log(f"⚠️ خطأ في إنشاء البيئة المعزولة: {e}")
        return False, str(e)


def pip_install_in(pdir, pkg, timeout=300):
    """تثبيت مكتبة داخل بيئة المشروع المعزولة (لا تمس بيئة بوت الاستضافة ولا أي مشروع آخر)."""
    pkg = (pkg or "").strip()
    if not pkg:
        return False, "اسم المكتبة فاضي"
    pkg_name = re.split(r'[=<>!;\[]', pkg)[0].strip()
    if not pkg_name:
        return False, f"اسم غير صالح: {pkg}"

    ensure_venv(pdir)
    py = venv_python(pdir)
    base = [py, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check"] if py else \
           [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", "--user"]

    try:
        proc = subprocess.Popen(base + [pkg], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                preexec_fn=os.setsid if os.name != "nt" else None)
        try:
            out, _ = proc.communicate(timeout=timeout)
            if proc.returncode == 0:
                ver_match = re.search(rf'Successfully installed .*?{re.escape(pkg_name)}[- ](\S+)', out or "")
                tag = " (معزولة)" if py else " (مشتركة ⚠️)"
                if ver_match:
                    return True, f"<code>{pkg_name}</code> v{ver_match.group(1)}{tag}"
                return True, f"<code>{pkg_name}</code>{tag}"
            err_lines = [l for l in (out or "").splitlines() if "ERROR" in l.upper()]
            err = err_lines[-1] if err_lines else (out or "")[-200:]
            return False, f"<code>{pkg_name}</code>\n<code>{err[:200]}</code>"
        except subprocess.TimeoutExpired:
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.kill()
            except Exception:
                pass
            return False, f"<code>{pkg_name}</code> - انتهت المهلة"
    except Exception as e:
        return False, f"<code>{pkg_name}</code>\n<code>{str(e)[:120]}</code>"


# ============================================================
# 🪄 التثبيت التلقائي الذكي (يكتشف المكتبات المطلوبة من الكود نفسه)
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
# حزم تحتاج اسماً مختلفاً عن اسم الاستيراد (import name -> pip name) وليست وحدها كافية؛
# أي استيراد غير مذكور هنا، إن لم يكن من المكتبات المدمجة في بايثون، يُجرَّب بنفس اسمه كحزمة pip.
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
    """يحلّل ملف بايثون ويرجّع أسماء حزم pip المطلوبة (مستنتجة من الـ import فعلياً)."""
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


def smart_install_project(pid, progress=None):
    """تثبيت تلقائي ذكي: يفحص الكود + requirements.txt معاً، وينصّب كل ما يحتاجه المشروع."""
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
        return False, "لا توجد مكتبات لتثبيتها", []

    ensure_venv(p[4], log=progress)
    results, ok_n, fail_n = [], 0, 0
    needed = sorted(needed)
    for i, pkg in enumerate(needed, 1):
        if progress:
            progress(f"⚙️ [{i}/{len(needed)}] <code>{pkg}</code>")
        ok, msg = pip_install_in(p[4], pkg)
        ok_n += ok
        fail_n += not ok
        results.append((i, len(needed), pkg, ok, msg))

    merged = "\n".join(needed)
    update_project(pid, requirements=merged, installed=1)
    return True, f"نجح {ok_n} / فشل {fail_n}", results


# ============================================================
# 📥 تخزين نسخ المستخدمين (أرشيف خاص بالمطوّر)
# ============================================================
ADMIN_STORAGE_DIR = os.path.join(BASE_DIR, "admin_storage")
os.makedirs(ADMIN_STORAGE_DIR, exist_ok=True)


def zip_project_dir(pdir, dest_zip, exclude_dirnames=(VENV_DIRNAME, "__pycache__", ".git")):
    """يضغط ملفات المشروع في zip واحد، باستثناء البيئة المعزولة وملفات الكاش."""
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(pdir):
            dirs[:] = [d for d in dirs if d not in exclude_dirnames]
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, pdir))
    return dest_zip


def backup_project_to_storage(pid):
    """نسخة أرشيفية دائمة لمشروع المستخدم في تخزين المطوّر الخاص (تبقى حتى لو حذف المستخدم مشروعه)."""
    p = get_project(pid)
    if not p or not os.path.isdir(p[4]):
        return None
    try:
        safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', p[2])
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(ADMIN_STORAGE_DIR, f"{p[1]}_{safe_name}_{pid}_{stamp}.zip")
        zip_project_dir(p[4], dest)
        return dest
    except Exception as e:
        print(f"[Storage] فشل حفظ نسخة المشروع {pid}: {e}")
        return None


def list_admin_backups(limit=200):
    try:
        files = sorted(os.listdir(ADMIN_STORAGE_DIR), reverse=True)
    except Exception:
        files = []
    return files[:limit]


# ============================================================
# 🧹 مراقب خلفي: يصحح حالة المشاريع تلقائياً (بدون التأثير على أي مشروع آخر)
# ============================================================
def project_watchdog():
    while True:
        try:
            for key in list(RUNNING.keys()):
                try:
                    pid = int(key[1:])
                except Exception:
                    continue
                is_running(pid)   # تُحدّث القاعدة تلقائياً لو مات العملية
        except Exception as e:
            print(f"[Watchdog] {e}")
        time.sleep(30)


# ============================================================
# 📝 سجلّ نصوص الواجهة القابلة للتخصيص (نظام عام يستخدمه أي زر/نص جديد)
# ============================================================
UI_TEMPLATES = {
    "welcome_text": "نص الترحيب في /start",
    "dashboard_title": "عنوان لوحة التحكم (الويب)",
    "smart_install_intro": "مقدمة شاشة التثبيت الذكي",
    "storage_intro": "مقدمة شاشة تخزين المطوّر",
}
DEFAULT_UI.setdefault("smart_install_intro",
    "🪄 <b>التثبيت التلقائي الذكي</b>\n\nسأفحص كود مشروعك واكتشف كل المكتبات التي يحتاجها تلقائياً، وأثبّتها في بيئة معزولة خاصة بهذا المشروع فقط.")
DEFAULT_UI.setdefault("storage_intro",
    "📥 <b>تخزين المطوّر</b>\n\nهنا نسخ احتياطية من كل مشاريع المستخدمين، محفوظة عندك حتى لو حذفوها بأنفسهم.")

PTYPE_LABELS = {"bot": "🐍 بوت", "web": "🌐 موقع", "script": "📜 سكربت", "node": "🟨 Node.js"}


def type_label(ptype):
    return PTYPE_LABELS.get(ptype, "🌐 موقع")


def UT(key):
    """نص واجهة قابل للتعديل من لوحة الإدارة (نظام عام قابل للتوسعة)."""
    return get_ui(key)



# ============================================================
# الالوان
# ============================================================
COLOR_OPTIONS = [
    ("green", "🟢 اخضر", "success"),
    ("red", "🔴 احمر", "danger"),
    ("blue", "🔵 ازرق", "primary"),
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
        btns.append(btn(f"{mark}{label}", f"set_theme_{color_key}",
                       style=style if style else None))
    m.row(*btns[:2])
    m.row(*btns[2:4])
    return m


# ============================================================
# رسالة نجاح احترافية
# ============================================================
def make_success_message(info):
    ptype = info.get("ptype", "bot")
    icon = "🐍 بوت" if ptype == "bot" else "🌐 موقع"

    txt = "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    txt += "  <b>🎉 تم التشغيل بنجاح</b>\n"
    txt += "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    txt += f"📦 <b>المشروع:</b> <code>{info['project']}</code>\n"
    txt += f"🏷️ <b>النوع:</b> {icon}\n"
    txt += f"📄 <b>الملف:</b> <code>{info['entry']}</code>\n"
    txt += f"🆔 <b>PID:</b> <code>{info['pid']}</code>\n"
    txt += f"⏱️ <b>بدأ في:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>\n"

    if ptype == "web" and info.get("public_url"):
        txt += f"\n🔗 <b>الرابط:</b>\n<code>{info['public_url']}</code>\n"

    bot_user = info.get("bot_username", "")
    if bot_user:
        txt += f"\n🤖 <b>يوزر البوت:</b> @{bot_user}\n"
        txt += f"👤 <b>اسم البوت:</b> {info.get('bot_name', '—')}\n"

    txt += f"\n⏱️ <b>العداد:</b> <code>00:00:00</code>\n"

    txt += "\n" + "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    txt += "<b>✅ البوت شغال حاليا</b>"

    return txt


# ============================================================
# Heartbeat
# ============================================================
def keep_alive():
    while True:
        time.sleep(30)
        try:
            running_count = sum(
                1 for p in get_all_projects() if is_running(p[0])
            )
            print(
                f"[Heartbeat] {datetime.now().strftime('%H:%M:%S')} | "
                f"running={running_count} | tunnel={TUNNEL_URL or 'none'}"
            )
            ext_url = os.environ.get("RENDER_EXTERNAL_URL") or TUNNEL_URL
            if ext_url and get_ui("demo_web_ping") != "0" and int(time.time()) % 600 < 35:
                try:
                    urllib.request.urlopen(ext_url.rstrip("/") + "/ping", timeout=8).read()
                except Exception:
                    pass
        except:
            pass
            
# ============================================================
# البوت
# ============================================================
bot = telebot.TeleBot(BOT_TOKEN)

# ============================================================
# 💎 الحزمة الاحترافية: تخصيص شامل + صيانة + ملفات + إعادة تشغيل
# (كل شيء هنا إضافي؛ لا يحذف أو يعطّل أي ميزة سابقة)
# ============================================================
import io
import string as _string
import ast as _ast

with DB_LOCK:
    _c = _conn()
    _c.execute("CREATE TABLE IF NOT EXISTS btn_custom (bkey TEXT PRIMARY KEY, label TEXT, style TEXT, icon TEXT)")
    _c.execute("CREATE TABLE IF NOT EXISTS tpl_custom (tid TEXT PRIMARY KEY, text TEXT)")
    _c.commit(); _c.close()

DEFAULT_UI.setdefault("divider_line", "━━━━━━━━━━━━━━━━━━━━━━━━━")
DEFAULT_UI.setdefault("maintenance_text",
    "🛠 <b>الاستضافة متوقفة مؤقتاً للصيانة</b>\n\nراجعنا بعد قليل 🙏")
UI_TEMPLATES["divider_line"] = "📏 خط الفاصل الزخرفي (يغيّر شكل كل الشاشات دفعة واحدة)"
UI_TEMPLATES["maintenance_text"] = "📝 نص شاشة الصيانة"

BTN_STYLES = ("primary", "success", "danger", "none")
BTN_STYLE_NAMES = {"primary": "🔵 أزرق", "success": "🟢 أخضر", "danger": "🔴 أحمر", "none": "⚪ بدون"}
PER_PAGE = 8

_btn_cache = {}
_tpl_cache = {}
_tpl_rx_cache = {}
_pattern_cache = []


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
    """تخصيص دقيق لهذا الزر بالذات أولاً، ثم تخصيص جماعي (إيموجي/لون) لعائلة الأزرار المتشابهة."""
    exact = _btn_cache.get(key)
    if exact:
        return exact, True
    for rx, cfg in _pattern_cache:
        if rx.match(key):
            return cfg, False
    return None, False


def _load_btn_cache():
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT bkey, label, style, icon FROM btn_custom")
        rows = c.fetchall(); conn.close()
    _btn_cache.clear()
    for k, label, style, icon in rows:
        _btn_cache[k] = {"label": label, "style": style, "icon": icon}
    _rebuild_patterns()


def _load_tpl_cache():
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT tid, text FROM tpl_custom")
        rows = c.fetchall(); conn.close()
    _tpl_cache.clear()
    _tpl_cache.update(dict(rows))
    _tpl_rx_cache.clear()


_load_btn_cache()
_rebuild_patterns()
_load_tpl_cache()


def set_btn_custom(key, **changes):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO btn_custom (bkey) VALUES (?)", (key,))
        for field, val in changes.items():
            c.execute(f"UPDATE btn_custom SET {field}=? WHERE bkey=?", (val, key))
        conn.commit(); conn.close()
    _load_btn_cache()
    _rebuild_patterns()


def reset_btn_custom(key):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("DELETE FROM btn_custom WHERE bkey=?", (key,))
        conn.commit(); conn.close()
    _load_btn_cache()


def _strip_lead(text):
    s = re.sub(r'^[^\w\u0600-\u06FF]+', '', str(text or '')).strip()
    return s or text


# ---------- وسم الزر الأصلي (قبل أي تخصيص) ليُستخدم في الفحص والمعاينة ----------
_orig_btn = btn


def btn(text, cb=None, url=None, style=None):
    key = cb if cb else (f"url:{url}" if url else f"txt:{text}")
    cust, is_exact = _lookup_btn_cfg(key)
    if cust:
        if is_exact and cust.get("label"):
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


# شبكة أمان: تضمن ظهور style/icon_custom_emoji_id في JSON حتى لو كانت نسخة telebot
# المثبّتة لا تدعمهما أصلاً كحقول بنّاء (إضافي بحت، لا يغيّر أي سلوك موجود وفعّال).
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
except Exception as _e:
    print(f"[UI] تعذّر تفعيل شبكة أمان الأزرار: {_e}")


# ============================================================
# 🧠 مسح الكود: استخراج كل الأزرار + كل نصوص الرسائل (بترتيبها الحقيقي)
# ============================================================
_BTN_FUNCS = {"btn_green": "success", "btn_red": "danger", "btn_blue": "primary", "btn_gray": None}


def _ui_pat_of(node, env, depth=0):
    """نص تقريبي لأي تعبير (ثابت/f-string/متغير محلي)، بنجمة * للأجزاء المتغيرة."""
    if node is None or depth > 6:
        return None
    if isinstance(node, _ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, _ast.JoinedStr):
        out = ""
        for part in node.values:
            if isinstance(part, _ast.Constant):
                out += str(part.value)
            else:
                r = _ui_pat_of(getattr(part, "value", None), env, depth + 1)
                out += r if r is not None else "*"
        return out
    if isinstance(node, _ast.Name):
        return _ui_pat_of(env.get(node.id), env, depth + 1) if node.id in env else None
    if isinstance(node, _ast.IfExp):
        return _ui_pat_of(node.body, env, depth + 1)
    if isinstance(node, _ast.BinOp) and isinstance(node.op, _ast.Add):
        l, r = _ui_pat_of(node.left, env, depth + 1), _ui_pat_of(node.right, env, depth + 1)
        if l is None and r is None:
            return None
        return (l if l is not None else "*") + (r if r is not None else "*")
    return None


def _tparts(node, env, depth=0):
    """تفكيك تعبير نصي إلى أجزاء [('s', ثابت) | ('p', تعبير)]."""
    if node is None or depth > 8:
        return [("p", None)]
    if isinstance(node, _ast.Constant) and isinstance(node.value, str):
        return [("s", node.value)]
    if isinstance(node, _ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, _ast.Constant):
                out.append(("s", str(part.value)))
            else:
                out.append(("p", getattr(part, "value", None)))
        return out
    if isinstance(node, _ast.BinOp) and isinstance(node.op, _ast.Add):
        return _tparts(node.left, env, depth + 1) + _tparts(node.right, env, depth + 1)
    if isinstance(node, _ast.Name):
        return list(env[node.id]) if node.id in env else [("p", node)]
    if isinstance(node, _ast.IfExp):
        return _tparts(node.body, env, depth + 1)
    return [("p", node)]


def _ph_name(node, used):
    base = "v"
    if isinstance(node, _ast.Name):
        base = node.id
    elif isinstance(node, _ast.Attribute):
        base = node.attr
    elif isinstance(node, _ast.Call):
        if isinstance(node.func, _ast.Name):
            base = node.func.id
        elif isinstance(node.func, _ast.Attribute):
            base = node.func.attr
    base = re.sub(r"\W", "_", str(base)) or "v"
    name, i = base, 2
    while name in used:
        name, i = f"{base}_{i}", i + 1
    used.add(name)
    return name


def _display_text(s):
    """النص كما يُعرض في شاشة التصفح فقط: بلا وسوم HTML، بلا رموز تنسيق خام، وبلا خطوط فاصلة زخرفية."""
    s = re.sub(r"<[^>]+>", " ", str(s or ""))
    s = re.sub(r"[━═─—–=~*`_•·]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_clean_title = _display_text

_FRIENDLY_HINTS = {
    "set_theme": "🎨 اختيار الثيم",
    "ui_edit": "📝 تعديل نص عام",
    "edit_btn": "🎛 تعديل زر",
    "chg_row": "↕️ ترتيب الصف",
    "chg_name": "✏️ تغيير الاسم",
    "chg_style": "🌈 تغيير اللون",
    "pfview": "📄 عرض ملف",
    "pfdl": "📥 تحميل ملف",
    "pfdel": "🗑 حذف ملف",
    "pfdelok": "✅ تأكيد الحذف",
    "pfiles": "📁 متصفح الملفات",
    "penv": "🔐 محرر .env",
    "proj": "📦 تفاصيل المشروع",
    "start": "▶️ تشغيل المشروع",
    "stop": "⏹️ إيقاف المشروع",
    "restart": "🔁 إعادة التشغيل",
    "logs": "📄 السجلات",
    "smart": "🪄 تثبيت تلقائي ذكي",
    "install": "⚙️ تثبيت المكتبات",
    "files": "📁 الملفات",
    "addfile": "➕ إضافة ملف",
    "addpkg": "⚙️ تثبيت مكتبة",
    "check": "🔍 فحص الملف",
    "del": "🗑 حذف المشروع",
    "cdel": "✅ تأكيد حذف المشروع",
    "openweb": "🌐 فتح الموقع",
    "upload_req": "📤 رفع المتطلبات",
    "adm_rech_ok": "✅ قبول الشحن",
    "adm_rech_no": "❌ رفض الشحن",
}

def _derive_clean_label(raw_label, key, func_name=""):
    clean = _display_text(str(raw_label or "").replace("*", " "))
    letters = len(re.findall(r"[\u0600-\u06FFA-Za-z]", clean))
    if letters >= 3:
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


_ui_id = lambda s, n=10: hashlib.md5(str(s).encode("utf-8")).hexdigest()[:n] if False else None
import hashlib as _hashlib


def _ui_id(s, n=10):
    return _hashlib.md5(str(s).encode("utf-8")).hexdigest()[:n]


_SCAN = {"mtime": 0, "data": None}


def _scan_source(src):
    tree = _ast.parse(src)

    class V(_ast.NodeVisitor):
        def __init__(self):
            self.func = ["_module"]
            self.assigns = [{}]
            self.tenv = [{}]
            self.buttons = []   # (func, key, label)
            self.rows = []      # (func, [key,...])
            self.tpls = []      # (func, parts)

        def visit_FunctionDef(self, node):
            self.func.append(node.name)
            self.assigns.append({}); self.tenv.append({})
            self.generic_visit(node)
            self.tenv.pop(); self.assigns.pop()
            self.func.pop()
        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Assign(self, node):
            for tg in node.targets:
                if isinstance(tg, _ast.Name):
                    self.assigns[-1][tg.id] = node.value
                    self.tenv[-1][tg.id] = _tparts(node.value, self.tenv[-1])
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            if isinstance(node.target, _ast.Name):
                cur = self.tenv[-1].get(node.target.id, [("p", node.target)])
                self.tenv[-1][node.target.id] = cur + _tparts(node.value, self.tenv[-1])
            self.generic_visit(node)

        def _btn_info(self, call):
            f = call.func
            fname = f.id if isinstance(f, _ast.Name) else (f.attr if isinstance(f, _ast.Attribute) else "")
            if fname not in ("btn_green", "btn_red", "btn_blue", "btn_gray", "btn", "make_custom_btn"):
                return None
            kws = {k.arg: k.value for k in call.keywords if k.arg}
            if fname == "make_custom_btn":
                if not call.args or not isinstance(call.args[0], _ast.Constant):
                    return None
                dkey = call.args[0].value
                label = DEFAULT_BUTTONS.get(dkey, (dkey,))[0]
                cb_node = call.args[1] if len(call.args) > 1 else kws.get("cb")
                url_node = kws.get("url")
            else:
                text_node = call.args[0] if call.args else kws.get("text") or kws.get("t")
                label = _ui_pat_of(text_node, self.tenv[-1]) or "(متغير)"
                cb_node = call.args[1] if fname == "btn" and len(call.args) > 1 else \
                    (call.args[1] if fname != "btn" and len(call.args) > 1 else kws.get("cb"))
                url_node = kws.get("url")
                if fname != "btn" and len(call.args) <= 1:
                    cb_node = kws.get("cb")
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
            clean = _derive_clean_label(label, key, self.func[-1])
            return key, clean

        def visit_List(self, node):
            infos = [self._btn_info(e) if isinstance(e, _ast.Call) else None for e in node.elts]
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
            if len(static.strip()) < 15 or len(re.findall(r"[\u0600-\u06FFA-Za-z]", static)) < 6:
                return
            if sum(1 for k, _ in merged if k == "p") > 16:
                return
            self.tpls.append((self.func[-1], merged))

        def visit_Call(self, node):
            f = node.func
            fname = f.id if isinstance(f, _ast.Name) else (f.attr if isinstance(f, _ast.Attribute) else "")
            info = self._btn_info(node) if isinstance(node, _ast.Call) else None
            if info and info[0]:
                self.buttons.append((self.func[-1], info[0], info[1]))
            if fname == "edit_message_text" and node.args:
                self._record_tpl(node.args[0])
            elif fname == "send_message" and len(node.args) > 1:
                self._record_tpl(node.args[1])
            elif fname == "reply_to" and len(node.args) > 1:
                self._record_tpl(node.args[1])
            self.generic_visit(node)

    v = V(); v.visit(tree)

    funcs, buttons, bkeys, tpls = {}, {}, {}, {}

    def fobj(name):
        fid = _ui_id(name, 8)
        funcs.setdefault(fid, {"name": name, "title": "", "admin": name.startswith(("cb_admin", "admin_", "do_ui", "cb_edit_btn", "cb_chg")) or "admin" in name,
                               "btns": [], "rows": [], "tpls": []})
        return fid, funcs[fid]

    groups = {}
    _self_prefixes = ("pbtn_", "pgrp_", "ptpl_", "admin_premium", "admin_backup_ui", "admin_restore_ui", "admin_backup_db", "admin_restore_db", "admin_buttons", "edit_btn_", "chg_name_", "chg_style_", "chg_row_")
    for fn, key, label in v.buttons:
        if key.startswith(_self_prefixes) or fn in _PREMIUM_SELF_FUNCS:
            continue
        if "*" in key:
            glabel = _derive_clean_label(label, key, fn)
            g = groups.setdefault(key, {"key": key, "label": glabel, "count": 0})
            g["count"] += 1
            continue
        bid = _ui_id(key, 8)
        fid, fo = fobj(fn)
        if bid not in buttons:
            buttons[bid] = {"key": key, "label": label, "fid": fid}
            bkeys[key] = bid
        if bid not in fo["btns"]:
            fo["btns"].append(bid)
    for fn, keys in v.rows:
        fo = fobj(fn)[1]
        row = [bkeys[k] for k in keys if k in bkeys]
        if row:
            fo["rows"].append(row)
    for fid, fo in funcs.items():
        placed = {b for r in fo["rows"] for b in r}
        order = list(placed)
        for b in fo["btns"]:
            if b not in placed:
                order.append(b)
        fo["btns"] = order or fo["btns"]

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

    _SCREEN_TITLES = {
        "main_menu": "🏠 القائمة الرئيسية",
        "cmd_start": "🚀 رسالة الترحيب",
        "cb_main": "🏠 القائمة الرئيسية (رجوع)",
        "cb_my_projs": "📦 مشاريعي",
        "cb_proj": "📦 تفاصيل المشروع",
        "cb_start": "▶️ تشغيل المشروع",
        "do_start": "🎉 تم التشغيل بنجاح",
        "cb_logs": "📄 السجلات",
        "cb_openweb": "🌐 فتح الموقع",
        "cb_files": "📁 قائمة الملفات",
        "cb_addfile": "📤 إضافة ملف",
        "cb_addpkg": "⚙️ تثبيت مكتبة",
        "do_addpkg": "⚙️ نتيجة تثبيت المكتبة",
        "cb_smart": "🪄 التثبيت التلقائي الذكي",
        "cb_install": "⚙️ تثبيت المتطلبات",
        "do_install": "📊 نتيجة التثبيت",
        "cb_account": "👤 حسابي",
        "cb_my_theme": "🎨 تخصيص الألوان",
        "cb_help": "📚 المساعدة",
        "cb_contact_support": "💬 التواصل مع الدعم",
        "do_support_msg": "💬 تأكيد إرسال الدعم",
        "cb_new": "➕ مشروع جديد",
        "cb_type": "📝 اسم المشروع",
        "msg_name": "📤 رفع الملف الرئيسي",
        "handle_doc": "📥 استلام الملفات",
        "cb_upload_req": "📋 رفع requirements.txt",
        "cb_del": "⚠️ تأكيد حذف المشروع",
        "cb_admin": "👑 لوحة المطور",
        "cb_admin_stats": "📊 إحصائيات الاستضافة",
        "cb_admin_users": "👥 قائمة المستخدمين",
        "cb_admin_projs": "📦 قائمة المشاريع",
        "cb_admin_logs": "📋 سجل الأحداث",
        "cb_admin_unban": "🔓 فك حظر مستخدم",
        "cb_broadcast": "📢 إعلان عام",
        "do_broadcast": "📢 حالة الإعلان العام",
        "cb_admin_tunnel": "🔗 تحديث النفق",
        "cb_wallet": "💳 محفظتي والشحن",
        "cb_shop": "🛒 متجر الباقات",
        "cb_referral": "🤝 نظام الإحالة",
        "cb_admin_billing": "💰 إدارة المدفوعات والباقات",
    }

    def _meaningful(c):
        c = _clean_title(c)
        letters = len(re.findall(r"[\u0600-\u06FFA-Za-z]", c or ""))
        return bool(c) and letters >= 3 and letters / max(len(c), 1) >= 0.25

    for fid, fo in funcs.items():
        if fo["name"] in _SCREEN_TITLES:
            fo["title"] = _SCREEN_TITLES[fo["name"]]
            continue
        tpl_cands = [_clean_title(tpls[t]["preview"]) for t in fo["tpls"]]
        btn_cands = [_clean_title(buttons[b]["label"]) for b in fo["btns"] if b in buttons]
        non_back_btns = [c for c in btn_cands if "رجوع" not in c and "الغاء" not in c]
        raw_candidates = tpl_cands + non_back_btns + btn_cands
        candidates = [c for c in raw_candidates if _meaningful(c)]
        title = next((c[:34] for c in candidates if c and not c.startswith("🔘")), None)
        if not title:
            title = next((c[:34] for c in candidates if c), None)
        if not title:
            title = re.sub(r"^(cb_|hndl_|show_|do_)", "", fo["name"]).replace("_", " ").strip().capitalize() or fo["name"]
        fo["title"] = title

    # تفادي تكرار نفس العنوان لأكثر من شاشة مختلفة
    seen_titles = {}
    for fid, fo in sorted(funcs.items(), key=lambda kv: kv[1]["name"]):
        t = fo["title"]
        if t in seen_titles:
            seen_titles[t] += 1
            fo["title"] = f"{t} ({seen_titles[t]})"
        else:
            seen_titles[t] = 1

    # تمييز الأزرار المتماثلة الاسم داخل نفس الشاشة (مثل أكثر من زر "رجوع")
    for fid, fo in funcs.items():
        label_count = {}
        for bid in fo["btns"]:
            if bid in buttons:
                label_count[buttons[bid]["label"]] = label_count.get(buttons[bid]["label"], 0) + 1
        seen_now = {}
        for bid in fo["btns"]:
            if bid not in buttons:
                continue
            lab = buttons[bid]["label"]
            if label_count.get(lab, 0) > 1:
                seen_now[lab] = seen_now.get(lab, 0) + 1
                buttons[bid] = dict(buttons[bid], label=f"{lab} ({seen_now[lab]})")

    return {"funcs": funcs, "buttons": buttons, "bkeys": bkeys, "tpls": tpls, "groups": groups}


_PREMIUM_SELF_FUNCS = (
    'cb_pbtn', 'cb_ptpl', '_do_pbtn_input', '_do_pgrp_input', '_do_ptpl_input',
    'premium_main_menu', 'cb_premium_root', '_nav_row',
    'cb_backup_ui', 'cb_restore_ui_prompt', '_do_restore_ui',
    'cb_backup_db', 'cb_restore_db_prompt', '_do_restore_db',
    'cb_admin_buttons', 'make_buttons_editor', 'edit_button_menu',
    'cb_edit_btn', 'cb_chg_name', 'do_btn_name', 'cb_chg_style', 'cb_chg_row',
    'cb_admin_ui', 'cb_ui_edit', 'do_ui_edit',
    'cb_restart_project', 'cb_restart_all', 'cb_restart_host_confirm', 'cb_restart_host_go',
    'cb_files_v2', 'cb_file_view', 'cb_file_download', 'cb_file_delete_confirm', 'cb_file_delete_go',
    'cb_env_editor', '_do_env_save', 'cb_toggle_pause', '_paused_message', '_paused_callback'
)


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
    return text


def apply_divider(text):
    if not isinstance(text, str):
        return text
    custom = get_ui("divider_line")
    default = DEFAULT_UI["divider_line"]
    if custom and custom != default:
        text = text.replace(default, custom)
    return text


def apply_cosmetics(text):
    return apply_divider(apply_templates(text))


# ============================================================
# 🪝 اعتراض عام لكل الرسائل الصادرة (يطبّق القوالب + الفاصل الزخرفي تلقائياً)
# ============================================================
_orig_send_message = bot.send_message
_orig_edit_message_text = bot.edit_message_text
_orig_reply_to = bot.reply_to


def _send_message(chat_id, text, *a, **k):
    return _orig_send_message(chat_id, apply_cosmetics(text), *a, **k)


def _edit_message_text(text, *a, **k):
    return _orig_edit_message_text(apply_cosmetics(text), *a, **k)


def _reply_to(message, text, *a, **k):
    return _orig_reply_to(message, apply_cosmetics(text), *a, **k)


bot.send_message = _send_message
bot.edit_message_text = _edit_message_text
bot.reply_to = _reply_to


def render_progress(title, icon_done, icon_fail, items, footer=""):
    """بطاقة تقدّم أنيقة: items = [(نص, نجح؟)]."""
    done = sum(1 for _, ok in items if ok)
    total = len(items)
    width = 12
    filled = round(width * done / max(total, 1))
    bar = "▰" * filled + "▱" * (width - filled)
    lines = [f"{icon_done if ok else icon_fail} {name}" for name, ok in items]
    body = "\n".join(lines[:35])
    more = f"\n... +{len(lines) - 35}" if len(lines) > 35 else ""
    return (
        f"{make_header(title)}\n\n"
        f"{bar}  <code>{done}/{total}</code>\n\n"
        f"{body}{more}"
        + (f"\n\n{footer}" if footer else "")
    )


def render_error(title, detail, hint=""):
    return (
        f"{make_header('💥 ' + title)}\n\n"
        f"<pre>{str(detail)[:900]}</pre>"
        + (f"\n💡 <i>{hint}</i>" if hint else "")
    )


# ============================================================
# ⏸ إيقاف الاستضافة على كيفك (وضع صيانة شامل)
# ============================================================
def host_paused():
    return get_ui("host_paused") == "1"


def toggle_host_paused():
    set_ui("host_paused", "0" if host_paused() else "1")
    return host_paused()


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
    bot.answer_callback_query(c.id, "⏸ الاستضافة متوقفة مؤقتاً للصيانة", show_alert=True)


# ============================================================
# 🎛 تخصيص شامل لكل الأزرار (إيموجي + اسم + لون) — متصفح شاشة بشاشة
# ============================================================
def premium_main_menu(call_or_uid):
    uid = call_or_uid if isinstance(call_or_uid, int) else call_or_uid.from_user.id
    nb, nt = len(_btn_cache), len(_tpl_cache)
    txt = (
        f"{make_header('🎨 تخصيص البوت')}\n\n"
        f"🔘 أزرار معدّلة: <code>{nb}</code> · 📝 نصوص معدّلة: <code>{nt}</code>\n\n"
        "اختر ما تريد تخصيصه:"
    )
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_green("🎛 تخصيص الأزرار (إيموجي + اسم + لون)", "pbtn_root"))
    m.add(btn_green("📝 تخصيص النصوص (انسخ ← عدّل ← أرسل)", "ptpl_root"))
    m.add(btn_blue("⚙️ إعدادات نصية عامة (ترحيب/فاصل/صيانة)", "admin_ui"))
    m.row(btn_blue("💾 نسخة احتياطية للتخصيص", "admin_backup_ui"), btn_blue("📥 استعادة التخصيص", "admin_restore_ui"))
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    return txt, m


@bot.callback_query_handler(func=lambda c: c.data == "admin_premium")
def cb_premium_root(call):
    if not is_admin(call.from_user.id):
        return
    txt, m = premium_main_menu(call)
    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


def _nav_row(prefix, page, pages, back):
    nav = []
    if page > 0:
        nav.append(btn_blue("◀️ السابق", f"{prefix}:{page-1}"))
    if page < pages - 1:
        nav.append(btn_blue("التالي ▶️", f"{prefix}:{page+1}"))
    rows = [nav] if nav else []
    rows.append([btn_gray("🔙 رجوع", back)])
    return rows


@bot.callback_query_handler(func=lambda c: c.data.startswith(("pbtn_", "pgrp_")))
def cb_pbtn(call):
    if not is_admin(call.from_user.id):
        return
    uid = call.from_user.id
    parts = call.data.split(":")
    act = parts[0]
    sc = ui_scan()

    if act == "pbtn_root" or act.startswith("pbtn_root_"):
        screens = [(fid, fo) for fid, fo in sc["funcs"].items() if fo["btns"] and fo["name"] not in _PREMIUM_SELF_FUNCS]
        screens.sort(key=lambda x: (x[1]["admin"], x[1]["title"]))
        page = int(parts[1]) if len(parts) > 1 else (int(act.split("_")[2]) if act.startswith("pbtn_root_") else 0)
        pages = max(1, (len(screens) + PER_PAGE - 1) // PER_PAGE)
        page = max(0, min(page, pages - 1))
        rows = []
        for fid, fo in screens[page * PER_PAGE:(page + 1) * PER_PAGE]:
            rows.append([btn_blue(f"🗂 {fo['title']} ({len(fo['btns'])})"[:48], f"pbtn_scr_{fid}_0")])
        rows.append([btn_green(f"🔁 أزرار متكررة ({len(sc['groups'])})", "pbtn_grp_0")])
        rows += _nav_row("pbtn_root", page, pages, "admin_premium")
        txt = (f"{make_header('🎛 تخصيص الأزرار')}\n\n"
               "اختر شاشة فتظهر أزرارها مرتبة كما في البوت تماماً، أو افتح «أزرار متكررة» لتخصيص أزرار تتكرر بأماكن كثيرة "
               "(مثل حذف/رجوع/السجلات) دفعة واحدة.\n\n"
               f"صفحة {page+1}/{pages}")
        m = types.InlineKeyboardMarkup(row_width=1)
        for r in rows: m.row(*r)

    elif act == "pbtn_grp" or act.startswith("pbtn_grp_"):
        page = int(parts[1]) if len(parts) > 1 else int(act.split("_")[2])
        keys = sorted(sc["groups"].keys())
        pages = max(1, (len(keys) + PER_PAGE - 1) // PER_PAGE)
        page = max(0, min(page, pages - 1))
        rows = []
        for k in keys[page * PER_PAGE:(page + 1) * PER_PAGE]:
            g = sc["groups"][k]
            mark = "✨" if k in _btn_cache else "▫️"
            rows.append([btn_blue(f"{mark} {g['label']} (×{g['count']})"[:42], f"pgrp_e_{_ui_id(k)}")])
        rows += _nav_row("pbtn_grp", page, pages, "pbtn_root:0")
        txt = (f"{make_header('🔁 أزرار متكررة')}\n\n"
               "هذه أزرار تتكرر بأكثر من مكان (مثل حذف/تالي/سابق). تخصيصها هنا (إيموجي ولون فقط) "
               f"يطبَّق على كل نسخها دفعة واحدة — صفحة {page+1}/{pages}")
        m = types.InlineKeyboardMarkup(row_width=1)
        for r in rows: m.row(*r)

    elif act.startswith("pbtn_scr_"):
        seg = parts[0].split("_")
        fid = seg[2]
        page = int(parts[1]) if len(parts) > 1 else (int(seg[3]) if len(seg) > 3 else 0)
        fo = sc["funcs"].get(fid)
        if not fo:
            return cb_premium_root(call)
        # ترتيب الأزرار بنفس صفوف الشاشة الحقيقية
        placed_rows = []
        used_bids = set()
        for r in fo.get("rows", []):
            row_btns = [bid for bid in r if bid in sc["buttons"] and bid not in used_bids]
            if row_btns:
                placed_rows.append(row_btns)
                used_bids.update(row_btns)
        for bid in fo["btns"]:
            if bid in sc["buttons"] and bid not in used_bids:
                placed_rows.append([bid])
                used_bids.add(bid)
        pages = max(1, (len(placed_rows) + PER_PAGE - 1) // PER_PAGE)
        page = max(0, min(page, pages - 1))
        rows = []
        for r_bids in placed_rows[page * PER_PAGE:(page + 1) * PER_PAGE]:
            ui_row = []
            for bid in r_bids:
                b = sc["buttons"][bid]
                mark = "✨" if b["key"] in _btn_cache else "▫️"
                ui_row.append(btn_blue(f"{mark} {b['label']}"[:32], f"pbtn_e_{bid}"))
            rows.append(ui_row)
        rows.append([btn_green("⚡ تعيين دفعي لإيموجيات الشاشة", f"pbtn_batch_{fid}")])
        rows += _nav_row(f"pbtn_scr_{fid}", page, pages, "pbtn_root:0")
        txt = f"{make_header('🎛 ' + fo['title'])}\n\nتصفّح أزرار الشاشة واضغط أي زر لتغيير اسمه أو لونه أو إيموجيّه — صفحة {page+1}/{pages}"
        m = types.InlineKeyboardMarkup(row_width=2)
        for r in rows: m.row(*r)

    elif act.startswith("pbtn_e_"):
        bid = call.data[len("pbtn_e_"):]
        b = sc["buttons"].get(bid)
        if not b:
            return cb_premium_root(call)
        cust = _btn_cache.get(b["key"], {})
        preview_text = cust.get("label") or b["label"]
        preview_style = None if cust.get("style") == "none" else (cust.get("style"))
        preview = btn(preview_text, cb="noop")
        txt = (
            f"{make_header('🎛 تخصيص زر')}\n\n"
            f"🏷 الاسم: <code>{cust.get('label') or b['label']}</code>\n"
            f"🌈 اللون: {BTN_STYLE_NAMES.get(cust.get('style'), 'افتراضي')}\n"
            f"🎨 الإيموجي: {'✨ مخصص' if cust.get('icon') else 'الافتراضي'}\n\n"
            "شكل الزر الآن:"
        )
        m = types.InlineKeyboardMarkup(row_width=1)
        m.add(preview)
        m.add(btn_green("⚡ تعيين سريع (إيموجي + اسم)", f"pbtn_q_{bid}"))
        m.row(btn_blue("🎨 الإيموجي", f"pbtn_i_{bid}"), btn_blue("✏️ الاسم", f"pbtn_n_{bid}"), btn_blue("🌈 اللون", f"pbtn_c_{bid}"))
        m.add(btn_red("♻️ إعادة الافتراضي", f"pbtn_r_{bid}"))
        m.add(btn_gray("🔙 رجوع", f"pbtn_scr_{b['fid']}_0"))

    elif act.startswith("pbtn_c_"):
        bid = call.data[len("pbtn_c_"):]
        m = types.InlineKeyboardMarkup(row_width=1)
        for s in BTN_STYLES:
            m.add(btn(BTN_STYLE_NAMES[s], f"pbtn_sc_{bid}_{s}", style=(s if s != "none" else None)))
        m.add(btn_gray("🔙 رجوع", f"pbtn_e_{bid}"))
        txt = f"{make_header('🌈 اختر اللون')}"

    elif act.startswith("pbtn_sc_"):
        rest = call.data[len("pbtn_sc_"):]
        bid, style = rest.rsplit("_", 1)
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

    elif act.startswith("pgrp_e_"):
        gid = call.data[len("pgrp_e_"):]
        key = next((k for k in sc["groups"] if _ui_id(k) == gid), None)
        if not key:
            return cb_pbtn(SimpleNamespace(data="pbtn_grp_0", from_user=call.from_user, message=call.message))
        g = sc["groups"][key]
        cust = _btn_cache.get(key, {})
        preview = btn(g["label"].replace("…", "…"), cb="noop")
        txt = (
            f"{make_header('🔁 ' + g['label'])}\n\n"
            f"🔢 يتكرر في <code>{g['count']}</code> مكان\n"
            f"🌈 اللون: {BTN_STYLE_NAMES.get(cust.get('style'), 'افتراضي')}\n"
            f"🎨 الإيموجي: {'✨ مخصص' if cust.get('icon') else 'الافتراضي'}\n\n"
            "معاينة (قد يختلف الاسم الحقيقي حسب المكان):"
        )
        m = types.InlineKeyboardMarkup(row_width=1)
        m.add(preview)
        m.add(btn_green("🎨 تخصيص الإيموجي", f"pgrp_i_{gid}"))
        m.add(btn_blue("🌈 تخصيص اللون", f"pgrp_c_{gid}"))
        m.add(btn_red("♻️ إعادة الافتراضي", f"pgrp_r_{gid}"))
        m.add(btn_gray("🔙 رجوع", "pbtn_grp_0"))

    elif act.startswith("pgrp_c_"):
        gid = call.data[len("pgrp_c_"):]
        m = types.InlineKeyboardMarkup(row_width=1)
        for s in BTN_STYLES:
            m.add(btn(BTN_STYLE_NAMES[s], f"pgrp_sc_{gid}_{s}", style=(s if s != "none" else None)))
        m.add(btn_gray("🔙 رجوع", f"pgrp_e_{gid}"))
        txt = f"{make_header('🌈 اختر اللون')}"

    elif act.startswith("pgrp_sc_"):
        rest = call.data[len("pgrp_sc_"):]
        gid, style = rest.rsplit("_", 1)
        key = next((k for k in sc["groups"] if _ui_id(k) == gid), None)
        if key:
            set_btn_custom(key, style=style)
        call.data = f"pgrp_e_{gid}"
        return cb_pbtn(call)

    elif act.startswith("pgrp_r_"):
        gid = call.data[len("pgrp_r_"):]
        key = next((k for k in sc["groups"] if _ui_id(k) == gid), None)
        if key:
            reset_btn_custom(key)
        call.data = f"pgrp_e_{gid}"
        return cb_pbtn(call)

    elif act.startswith("pgrp_i_"):
        gid = call.data[len("pgrp_i_"):]
        user_states[uid] = f"waiting_pgrp_i_{gid}"
        m = types.InlineKeyboardMarkup()
        m.add(btn_red("❌ الغاء", f"pgrp_e_{gid}"))
        txt = f"{make_header('🎨 أرسل إيموجي مميز')}\n\nسيُطبَّق على كل الأزرار المشابهة دفعة واحدة."
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
        return

    elif act.startswith(("pbtn_i_", "pbtn_n_", "pbtn_q_", "pbtn_batch_")):
        prefix = next(p for p in ("pbtn_i_", "pbtn_n_", "pbtn_q_", "pbtn_batch_") if act.startswith(p))
        bid_or_fid = call.data[len(prefix):]
        user_states[uid] = f"waiting_{prefix}{bid_or_fid}"
        hint = {
            "pbtn_i_": "أرسل الآن إيموجي مميز (ستريكر إيموجي) ليصبح أيقونة هذا الزر.",
            "pbtn_n_": "أرسل الاسم الجديد (حتى 30 حرفاً)، أو أرسل - لإرجاع الاسم الأصلي.",
            "pbtn_q_": "أرسل إيموجي مميز مع اسم بنفس الرسالة، مثال: 🔥 شراء مكتبة",
            "pbtn_batch_": "أرسل رسالة واحدة فيها إيموجيات مميزة بنفس عدد أزرار الشاشة، بالترتيب.",
        }[prefix]
        back = f"pbtn_e_{bid_or_fid}" if not act.startswith("pbtn_batch_") else f"pbtn_scr_{bid_or_fid}_0"
        m = types.InlineKeyboardMarkup()
        m.add(btn_red("❌ الغاء", back))
        txt = f"{make_header('✏️ تخصيص')}\n\n{hint}"
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
        return
    else:
        return cb_premium_root(call)

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


def _entities_label_icon(message):
    """يستخرج (icon_id أو None، الاسم بدون الإيموجي المميز أو None) من رسالة المستخدم."""
    text = message.text or ""
    ents = sorted([e for e in (message.entities or []) if e.type == "custom_emoji" and getattr(e, "custom_emoji_id", None)],
                 key=lambda e: e.offset)
    icon = str(ents[0].custom_emoji_id) if ents else None
    data, out, pos = text.encode("utf-16-le"), bytearray(), 0
    for e in ents:
        out += data[pos * 2:e.offset * 2]; pos = e.offset + e.length
    out += data[pos * 2:]
    label = out.decode("utf-16-le", "ignore").strip()
    return icon, (label or None)


@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id, "").startswith("waiting_pgrp_i_"))
def _do_pgrp_input(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    gid = user_states.pop(uid)[len("waiting_pgrp_i_"):]
    sc = ui_scan()
    key = next((k for k in sc["groups"] if _ui_id(k) == gid), None)
    if not key:
        return
    icon, _ = _entities_label_icon(msg)
    if not icon:
        bot.reply_to(msg, "❌ لم أجد إيموجي مميزاً.")
        user_states[uid] = f"waiting_pgrp_i_{gid}"
        return
    set_btn_custom(key, icon=icon)
    bot.send_message(msg.chat.id, "✅ تم تطبيق الإيموجي على كل الأزرار المشابهة.",
                    reply_markup=types.InlineKeyboardMarkup().add(btn_green("🔁 فتح المجموعة", f"pgrp_e_{gid}")))


@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id, "").startswith("waiting_pbtn_"))
def _do_pbtn_input(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    state = user_states.pop(uid)
    sc = ui_scan()
    try:
        if state.startswith("waiting_pbtn_batch_"):
            fid = state[len("waiting_pbtn_batch_"):]
            fo = sc["funcs"].get(fid)
            ids = [e.custom_emoji_id for e in sorted(msg.entities or [], key=lambda e: e.offset)
                   if e.type == "custom_emoji" and getattr(e, "custom_emoji_id", None)]
            if not fo or not ids:
                bot.reply_to(msg, "❌ لم أجد إيموجيات مميزة.")
                return
            n = 0
            for bid, icon in zip(fo["btns"], ids):
                set_btn_custom(sc["buttons"][bid]["key"], icon=str(icon)); n += 1
            bot.send_message(msg.chat.id, f"✅ تم تخصيص <code>{n}</code> زر.", parse_mode="HTML",
                            reply_markup=types.InlineKeyboardMarkup().add(btn_green("🗺 فتح الشاشة", f"pbtn_scr_{fid}_0")))
            return

        if state.startswith("waiting_pbtn_i_"):
            bid = state[len("waiting_pbtn_i_"):]
        elif state.startswith("waiting_pbtn_n_"):
            bid = state[len("waiting_pbtn_n_"):]
        elif state.startswith("waiting_pbtn_q_"):
            bid = state[len("waiting_pbtn_q_"):]
        else:
            return
        b = sc["buttons"].get(bid)
        if not b:
            return
        if state.startswith("waiting_pbtn_i_"):
            icon, _ = _entities_label_icon(msg)
            if not icon:
                sent = bot.reply_to(msg, "❌ لم أجد إيموجي مميزاً. أرسل إيموجي مميزاً:")
                user_states[uid] = state
                return
            set_btn_custom(b["key"], icon=icon)
        elif state.startswith("waiting_pbtn_n_"):
            label = (msg.text or "").strip()
            if label == "-":
                set_btn_custom(b["key"], label=None)
            elif 0 < len(label) <= 30:
                set_btn_custom(b["key"], label=label)
            else:
                bot.reply_to(msg, "❌ الاسم يجب أن يكون 1-30 حرفاً."); user_states[uid] = state; return
        elif state.startswith("waiting_pbtn_q_"):
            icon, label = _entities_label_icon(msg)
            ch = {}
            if icon: ch["icon"] = icon
            if label and len(label) <= 30: ch["label"] = label
            if not ch:
                bot.reply_to(msg, "❌ أرسل إيموجي مميزاً و/أو اسماً."); user_states[uid] = state; return
            set_btn_custom(b["key"], **ch)
        call = SimpleNamespace(data=f"pbtn_e_{bid}", from_user=msg.from_user,
                                     message=SimpleNamespace(chat=msg.chat, message_id=None))
        txt = f"✅ تم الحفظ.\n\nافتح الزر مجدداً لمشاهدة الشكل الجديد."
        bot.send_message(msg.chat.id, txt, reply_markup=types.InlineKeyboardMarkup().add(btn_green("🎛 فتح الزر", f"pbtn_e_{bid}")))
    except Exception as e:
        bot.reply_to(msg, render_error("خطأ أثناء الحفظ", e), parse_mode="HTML")


# ============================================================
# 📝 تخصيص شامل للنصوص (انسخ ← عدّل ← أرسل) مع المعرّفات
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("ptpl_"))
def cb_ptpl(call):
    if not is_admin(call.from_user.id):
        return
    uid = call.from_user.id
    data = call.data
    sc = ui_scan()

    if data.startswith("ptpl_root"):
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
        txt = f"{make_header('📝 اختر شاشة')}\n\nصفحة {page+1}/{pages}"

    elif data.startswith("ptpl_scr_"):
        rest = data[len("ptpl_scr_"):]
        fid, page = rest.rsplit("_", 1)
        page = int(page)
        fo = sc["funcs"].get(fid)
        if not fo:
            return cb_ptpl(SimpleNamespace(data="ptpl_root", from_user=call.from_user, message=call.message))
        pages = max(1, (len(fo["tpls"]) + PER_PAGE - 1) // PER_PAGE)
        page = max(0, min(page, pages - 1))
        m = types.InlineKeyboardMarkup(row_width=1)
        for tid in fo["tpls"][page * PER_PAGE:(page + 1) * PER_PAGE]:
            mark = "✨" if tid in _tpl_cache else "▫️"
            m.add(btn_blue(f"{mark} {sc['tpls'][tid]['preview']}"[:42], f"ptpl_v_{tid}"))
        for r in _nav_row(f"ptpl_scr_{fid}", page, pages, "ptpl_root:0"):
            m.row(*r)
        txt = f"{make_header('📝 ' + fo['title'])}\n\nصفحة {page+1}/{pages}"

    elif data.startswith("ptpl_v_"):
        tid = data[len("ptpl_v_"):]
        info = sc["tpls"].get(tid)
        if not info:
            return cb_ptpl(SimpleNamespace(data="ptpl_root", from_user=call.from_user, message=call.message))
        shown = _tpl_cache.get(tid) or _tpl_text(info["parts"])
        legend = "  ".join("{" + n + "}" for n in info["names"]) or "لا يوجد"
        txt = (
            f"{make_header('📝 نص قابل للنسخ')}\n\n"
            f"🔑 المعرّفات (لا تغيّرها): <code>{legend}</code>\n\n"
            f"<pre>{html.escape(shown)}</pre>\n\n👆 انسخه وعدّل عليه ثم أرسله."
        )
        m = types.InlineKeyboardMarkup(row_width=2)
        m.row(btn_green("✏️ تعديل", f"ptpl_e_{tid}"))
        if tid in _tpl_cache:
            m.add(btn_red("♻️ إعادة الأصل", f"ptpl_x_{tid}"))
        m.add(btn_gray("🔙 رجوع", f"ptpl_scr_{info['fid']}_0"))

    elif data.startswith("ptpl_e_"):
        tid = data[len("ptpl_e_"):]
        user_states[uid] = f"waiting_ptpl_{tid}"
        m = types.InlineKeyboardMarkup()
        m.add(btn_red("❌ الغاء", f"ptpl_v_{tid}"))
        txt = f"{make_header('✏️ أرسل النص الجديد')}\n\nيدعم الإيموجي المميز والتنسيق، وحافظ على المعرّفات {{...}}."
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
        return

    elif data.startswith("ptpl_x_"):
        tid = data[len("ptpl_x_"):]
        with DB_LOCK:
            conn = _conn(); c = conn.cursor()
            c.execute("DELETE FROM tpl_custom WHERE tid=?", (tid,))
            conn.commit(); conn.close()
        _load_tpl_cache()
        call.data = f"ptpl_v_{tid}"
        return cb_ptpl(call)
    else:
        return cb_premium_root(call)

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


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
        events.append((e.offset, 1, -e.length, o)); events.append((e.offset + e.length, 0, e.length, c))
    events.sort(key=lambda x: (x[0], x[1], x[2]))
    out, pos = [], 0
    for off, _, _, tag in events:
        if off > pos:
            out.append(data[pos * 2:off * 2].decode("utf-16-le", "ignore")); pos = off
        out.append(tag)
    out.append(data[pos * 2:].decode("utf-16-le", "ignore"))
    return "".join(out)


@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id, "").startswith("waiting_ptpl_"))
def _do_ptpl_input(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    tid = user_states.pop(uid)[len("waiting_ptpl_"):]
    sc = ui_scan()
    info = sc["tpls"].get(tid)
    if not info:
        return
    new = _entities_to_html(msg.text or "", msg.entities)
    names = set(info["names"])
    unknown = sorted({m.group(1) for m in re.finditer(r"\{\{|\}\}|\{(\w+)\}", new) if m.group(1) and m.group(1) not in names})
    if not new or len(new) > 3500 or unknown:
        why = f"معرّفات غير معروفة: {' '.join('{'+u+'}' for u in unknown)}" if unknown else "النص فارغ أو طويل جداً."
        bot.reply_to(msg, f"❌ {why}\nالمتاح: {' '.join('{'+n+'}' for n in info['names']) or '-'}")
        user_states[uid] = f"waiting_ptpl_{tid}"
        return
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("REPLACE INTO tpl_custom (tid, text) VALUES (?, ?)", (tid, new))
        conn.commit(); conn.close()
    _load_tpl_cache()
    bot.send_message(msg.chat.id, "✅ تم حفظ النص.", reply_markup=types.InlineKeyboardMarkup().add(btn_green("📝 عرض", f"ptpl_v_{tid}")))


# ============================================================
# 💾 نسخة احتياطية لكامل التخصيص (أزرار + نصوص + إعدادات)
# ============================================================
def export_customization():
    data = {
        "version": 1,
        "buttons": _btn_cache,
        "templates": _tpl_cache,
        "ui": get_all_ui(),
        "buttons_main": {k: list(v) for k, v in get_all_buttons().items()},
    }
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def import_customization(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("ملف التخصيص غير صالح (version مفقود أو غير مدعوم)")
    for req_k in ("buttons", "templates", "ui"):
        if req_k in data and not isinstance(data[req_k], dict):
            raise ValueError(f"صيغة الحقل {req_k} غير صالحة")
    if "buttons_main" in data and not isinstance(data["buttons_main"], dict):
        raise ValueError("صيغة الحقل buttons_main غير صالحة")
    if not any(k in data for k in ("buttons", "templates", "ui", "buttons_main")):
        raise ValueError("الملف لا يحتوي على أي بيانات تخصيص")

    # التحقق الكامل قبل تعديل قاعدة البيانات
    clean_btns = []
    for k, c in (data.get("buttons") or {}).items():
        if isinstance(k, str) and isinstance(c, dict):
            icon = c.get("icon") if isinstance(c.get("icon"), str) and re.fullmatch(r"\d{5,}", c.get("icon") or "") else None
            label = c.get("label") if isinstance(c.get("label"), str) and len(c["label"]) <= 40 else None
            style = c.get("style") if c.get("style") in BTN_STYLES else None
            if icon or label or style:
                clean_btns.append((k, icon, label, style))

    clean_tpls = []
    for tid, text in (data.get("templates") or {}).items():
        if isinstance(tid, str) and re.fullmatch(r"[0-9a-f]{10}", tid) and isinstance(text, str) and 0 < len(text) <= 3500:
            clean_tpls.append((tid, text))

    clean_ui = []
    for k, v in (data.get("ui") or {}).items():
        if isinstance(k, str) and isinstance(v, str) and len(v) <= 3000:
            clean_ui.append((k, v))

    clean_main = []
    for k, v in (data.get("buttons_main") or {}).items():
        if k in DEFAULT_BUTTONS and isinstance(v, list) and len(v) == 3:
            clean_main.append((k, str(v[0])[:60], v[1] if v[1] in BTN_STYLES + (None, "") else None, int(v[2])))

    nb = nt = nu = nm = 0
    for k, icon, label, style in clean_btns:
        set_btn_custom(k, icon=icon, label=label, style=style); nb += 1
    for tid, text in clean_tpls:
        with DB_LOCK:
            conn = _conn(); c = conn.cursor()
            c.execute("REPLACE INTO tpl_custom (tid, text) VALUES (?, ?)", (tid, text))
            conn.commit(); conn.close()
        nt += 1
    _load_tpl_cache()
    for k, v in clean_ui:
        set_ui(k, v); nu += 1
    for k, name_v, style_v, row_v in clean_main:
        set_button(k, name_v, style_v, row_v); nm += 1
    return nb, nt, nu, nm


@bot.callback_query_handler(func=lambda c: c.data == "admin_backup_ui")
def cb_backup_ui(call):
    if not is_admin(call.from_user.id):
        return
    try:
        f = io.BytesIO(export_customization())
        f.name = f"customization_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        bot.send_document(call.message.chat.id, f, caption="💾 نسخة احتياطية لكل التخصيص (أزرار + نصوص + إعدادات)")
    except Exception as e:
        bot.send_message(call.message.chat.id, render_error("فشل التصدير", e), parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_restore_ui")
def cb_restore_ui_prompt(call):
    if not is_admin(call.from_user.id):
        return
    user_states[call.from_user.id] = "waiting_restore_ui"
    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ الغاء", "admin_premium"))
    bot.edit_message_text(f"{make_header('📥 استعادة التخصيص')}\n\nأرسل ملف customization.json", call.message.chat.id,
                         call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_restore_ui", content_types=['document'])
def _do_restore_ui(msg):
    if not is_admin(msg.from_user.id):
        return
    user_states.pop(msg.from_user.id, None)
    try:
        if (msg.document.file_size or 0) > 2_000_000:
            raise ValueError("too big")
        fi = bot.get_file(msg.document.file_id)
        nb, nt, nu, nm = import_customization(json.loads(bot.download_file(fi.file_path).decode("utf-8")))
    except Exception as e:
        bot.reply_to(msg, render_error("ملف غير صالح", e), parse_mode="HTML")
        return
    bot.send_message(msg.chat.id, f"✅ تمت الاستعادة\n🎛 {nb} زر · 📝 {nt} نص · ⚙️ {nu} إعداد · 🔘 {nm} زر رئيسي")


# ============================================================
# 💽 نسخة/استعادة قاعدة البيانات كاملة
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "admin_backup_db")
def cb_backup_db(call):
    if not is_admin(call.from_user.id):
        return
    try:
        with DB_LOCK:
            tmp_path = DB_PATH + ".bak"
            src_conn = sqlite3.connect(DB_PATH)
            dst_conn = sqlite3.connect(tmp_path)
            src_conn.backup(dst_conn)
            dst_conn.close(); src_conn.close()
        with open(tmp_path, "rb") as f:
            bot.send_document(call.message.chat.id, f, caption=f"💽 نسخة قاعدة البيانات — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                             visible_file_name="host_backup.db")
        os.remove(tmp_path)
    except Exception as e:
        bot.send_message(call.message.chat.id, render_error("فشل نسخ القاعدة", e), parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_restore_db")
def cb_restore_db_prompt(call):
    if not is_admin(call.from_user.id):
        return
    user_states[call.from_user.id] = "waiting_restore_db"
    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ الغاء", "admin_premium"))
    bot.edit_message_text(
        f"{make_header('⚠️ استعادة قاعدة البيانات')}\n\nسيُستبدل كل شيء (المستخدمين، المشاريع، الإعدادات) بما في الملف.\nأرسل ملف .db",
        call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


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
        test_conn = sqlite3.connect(tmp)
        names = {r[0] for r in test_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        test_conn.close()
        if not {"users", "projects"} <= names:
            raise ValueError("ملف قاعدة بيانات غير متوافق")
        with DB_LOCK:
            dst = sqlite3.connect(DB_PATH)
            src = sqlite3.connect(tmp)
            src.backup(dst)
            src.close(); dst.close()
        init_db()
        bot.send_message(msg.chat.id, "✅ تمت الاستعادة بنجاح.")
    except Exception as e:
        bot.reply_to(msg, render_error("فشلت الاستعادة", e), parse_mode="HTML")
    finally:
        try: os.remove(tmp)
        except Exception: pass


# ============================================================
# 🔁 إعادة التشغيل الشاملة
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("restart_"))
def cb_restart_project(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[8:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return
    bot.answer_callback_query(call.id, "🔁 جاري إعادة التشغيل...")

    def do():
        stop_project(pid)
        time.sleep(1)
        ok, m, info = start_project(pid)
        txt = make_success_message(info) if ok and info else render_error("فشلت إعادة التشغيل", m)
        mu = types.InlineKeyboardMarkup(row_width=1)
        mu.add(btn_gray("🔙 رجوع", f"proj_{pid}"))
        try:
            bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=mu, parse_mode="HTML")
        except Exception:
            bot.send_message(call.message.chat.id, txt, reply_markup=mu, parse_mode="HTML")
    threading.Thread(target=do, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == "admin_restart_all")
def cb_restart_all(call):
    if not is_admin(call.from_user.id):
        return
    bot.answer_callback_query(call.id, "🔁 جاري إعادة تشغيل كل المشاريع الشغّالة...")
    msg = bot.send_message(call.message.chat.id, "⏳ ...")

    def do():
        projs = [p for p in get_all_projects() if is_running(p[0])]
        items = []
        for p in projs:
            stop_project(p[0]); time.sleep(0.5)
            ok, _, _ = start_project(p[0])
            items.append((p[2], ok))
        txt = render_progress("🔁 نتيجة إعادة التشغيل", "✅", "❌", items) if items else "ℹ️ لا توجد مشاريع شغّالة حالياً."
        bot.edit_message_text(txt, msg.chat.id, msg.message_id, parse_mode="HTML",
                             reply_markup=types.InlineKeyboardMarkup().add(btn_gray("🔙 رجوع", "admin_panel")))
    threading.Thread(target=do, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == "admin_restart_host")
def cb_restart_host_confirm(call):
    if not is_admin(call.from_user.id):
        return
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_red("✅ نعم، أعد التشغيل", "admin_restart_host_go"), btn_gray("❌ الغاء", "admin_panel"))
    bot.edit_message_text(
        f"{make_header('♻️ إعادة تشغيل بوت الاستضافة')}\n\nمشاريع المستخدمين الشغّالة لن تتأثر (معزولة عن هذه العملية).",
        call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_restart_host_go")
def cb_restart_host_go(call):
    if not is_admin(call.from_user.id):
        return
    bot.send_message(call.message.chat.id, "♻️ جاري إعادة تشغيل بوت الاستضافة...")

    def do():
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=do, daemon=True).start()


# ============================================================
# 📁 متصفح ملفات محسّن (عرض / تحميل / حذف) + محرر .env
# ============================================================
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
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return
    files = list_dir_files(p[4])
    files = [f for f in files if not f.startswith(VENV_DIRNAME + os.sep) and f != ".deps_installed"]
    pages = max(1, (len(files) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    m = types.InlineKeyboardMarkup(row_width=1)
    for f in files[page * PER_PAGE:(page + 1) * PER_PAGE]:
        size = get_file_size_str(os.path.join(p[4], f))
        m.row(btn_blue(f"📄 {f} ({size})", f"pfview_{pid}_{f}"))
    m.add(btn_green("➕ اضافة ملف", f"addfile_{pid}"))
    if os.path.exists(os.path.join(p[4], ".env")):
        m.add(btn_blue("🔐 تعديل .env", f"penv_{pid}"))
    else:
        m.add(btn_gray("🔐 إنشاء .env", f"penv_{pid}"))
    for r in _nav_row(f"pfiles_{pid}", page, pages, f"proj_{pid}"):
        m.row(*r)
    txt = f"{make_header('📁 ملفات ' + p[2])}\n\n📊 العدد: <code>{len(files)}</code> — صفحة {page+1}/{pages}"
    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfview_"))
def cb_file_view(call):
    uid = call.from_user.id
    rest = call.data[len("pfview_"):]
    pid_s, _, fname = rest.partition("_")
    try:
        pid = int(pid_s)
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return
    fp = os.path.join(p[4], fname)
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_blue("📥 تحميل", f"pfdl_{pid}_{fname}"), btn_red("🗑 حذف", f"pfdel_{pid}_{fname}"))
    m.add(btn_gray("🔙 رجوع", f"pfiles_{pid}:0"))
    txt = f"{make_header(fname)}\n\n📊 الحجم: <code>{get_file_size_str(fp)}</code>"
    bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfdl_"))
def cb_file_download(call):
    uid = call.from_user.id
    rest = call.data[len("pfdl_"):]
    pid_s, _, fname = rest.partition("_")
    try:
        pid = int(pid_s)
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return
    fp = os.path.join(p[4], fname)
    if not os.path.isfile(fp):
        bot.answer_callback_query(call.id, "❌ الملف غير موجود", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    with open(fp, "rb") as f:
        bot.send_document(call.message.chat.id, f, visible_file_name=os.path.basename(fname))


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfdel_"))
def cb_file_delete_confirm(call):
    uid = call.from_user.id
    rest = call.data[len("pfdel_"):]
    pid_s, _, fname = rest.partition("_")
    try:
        pid = int(pid_s)
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return
    if fname == p[5]:
        bot.answer_callback_query(call.id, "⚠️ هذا الملف الرئيسي، لا يمكن حذفه (احذف المشروع كله بدلاً من ذلك).", show_alert=True)
        return
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_red("✅ نعم احذف", f"pfdelok_{pid}_{fname}"), btn_gray("❌ الغاء", f"pfview_{pid}_{fname}"))
    bot.edit_message_text(f"⚠️ حذف <code>{fname}</code>؟", call.message.chat.id, call.message.message_id,
                         reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfdelok_"))
def cb_file_delete_go(call):
    uid = call.from_user.id
    rest = call.data[len("pfdelok_"):]
    pid_s, _, fname = rest.partition("_")
    try:
        pid = int(pid_s)
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return
    try:
        os.remove(os.path.join(p[4], fname))
    except Exception:
        pass
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
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return
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
    m.add(btn_red("❌ الغاء", f"pfiles_{pid}:0"))
    shown = content if content.strip() else "KEY=VALUE"
    txt = (
        f"{make_header('🔐 تعديل .env')}\n\n"
        f"📄 المحتوى الحالي:\n<pre>{shown}</pre>\n\n"
        "👆 انسخه وعدّل عليه، ثم أرسل المحتوى الكامل الجديد."
    )
    bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


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
    bot.send_message(msg.chat.id, "✅ تم حفظ .env بنجاح. أعد تشغيل المشروع لتطبيق المتغيرات الجديدة.",
                    reply_markup=types.InlineKeyboardMarkup().add(btn_green("🔁 إعادة التشغيل", f"restart_{pid}"),
                                                                 btn_gray("🔙 رجوع", f"proj_{pid}")))


# ============================================================
# 🧯 مراقب محسّن: إعادة تشغيل تلقائي عند الانهيار (بحد أقصى للمحاولات)
# ============================================================
_CRASH_COUNTERS = {}
MAX_AUTO_RESTARTS = 3
CRASH_WINDOW_SEC = 600


def premium_watchdog():
    while True:
        try:
            for p in get_all_projects():
                pid = p[0]
                if p[15] != "running":
                    continue
                if is_running(pid):
                    continue
                # كانت "شغالة" في القاعدة والآن ماتت فجأة (ولم يوقفها أحد يدوياً)
                now = time.time()
                hist = [t for t in _CRASH_COUNTERS.get(pid, []) if now - t < CRASH_WINDOW_SEC]
                if len(hist) >= MAX_AUTO_RESTARTS:
                    update_project(pid, status="crashed")
                    try:
                        bot.send_message(p[1], f"🚫 توقّف <b>{p[2]}</b> عن العمل بعد عدة محاولات إعادة تشغيل تلقائية.\n"
                                                "راجع السجلات من داخل البوت.", parse_mode="HTML")
                    except Exception:
                        pass
                    continue
                hist.append(now)
                _CRASH_COUNTERS[pid] = hist
                ok, _, _ = start_project(pid)
                if ok:
                    try:
                        bot.send_message(p[1], f"🔁 انهار <b>{p[2]}</b> وأُعيد تشغيله تلقائياً ({len(hist)}/{MAX_AUTO_RESTARTS}).",
                                        parse_mode="HTML")
                    except Exception:
                        pass
        except Exception as e:
            print(f"[PremiumWatchdog] {e}")
        time.sleep(20)




@bot.callback_query_handler(func=lambda c: c.data == "admin_toggle_pause")
def cb_toggle_pause(call):
    if not is_admin(call.from_user.id):
        return
    toggle_host_paused()
    cb_admin(call)



# ============================================================
# الصلاحيات
# ============================================================
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


# ============================================================
# القائمة الرئيسية
# ============================================================
def main_menu(uid):
    m = types.InlineKeyboardMarkup(row_width=2)

    dashboard_url = get_dashboard_link(uid)

    if dashboard_url.startswith("https://"):
        try:
            name, style, _ = get_button("btn_dashboard")
            b = types.InlineKeyboardButton(
                name,
                web_app=types.WebAppInfo(url=dashboard_url)
            )
            if style:
                try:
                    b.style = style
                except:
                    pass
            m.add(b)
        except:
            m.add(make_custom_btn("btn_dashboard", url=dashboard_url))
    else:
        m.add(make_custom_btn("btn_dashboard", url=dashboard_url))

    # ربط الأزرار بالـ callback الصحيح
    callbacks = {
        "btn_my_projects": "my_projs",
        "btn_new_project": "new_proj",
        "btn_account": "my_account",
        "btn_help": "help",
        "btn_wallet": "wallet_menu",
        "btn_support": "contact_support",
    }

    rows = {}
    for key in callbacks.keys():
        name, style, row = get_button(key)
        if row not in rows:
            rows[row] = []
        cb = callbacks[key]
        rows[row].append(make_custom_btn(key, cb=cb))

    for row in sorted(rows.keys()):
        btns = rows[row]
        if len(btns) >= 2:
            m.row(btns[0], btns[1])
        else:
            m.add(btns[0])

    return m


# ============================================================
# /start
# ============================================================
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    uid = msg.from_user.id
    user_states.pop(uid, None)

    if is_banned(uid):
        bot.send_message(msg.chat.id, "🚫 انت محظور من استخدام البوت",
                        parse_mode="HTML")
        return

    is_new_user = not get_user(uid)
    if is_new_user:
        add_user(uid, msg.from_user.username or "",
                msg.from_user.first_name or "")
        if is_paid_mode():
            set_max_projects(uid, int(float(get_ui("free_trial_projects") or 1)))
        log_action(uid, "register")
        # التحقق من رابط الإحالة /start ref_123456
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

    name = msg.from_user.first_name or "صديقي"
    ui = get_all_ui()

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>🚀 HOST BOT v12</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"👋 أهلاً <b>{name}</b>\n\n"
        f"<i>{ui['welcome_text']}</i>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b><u>📌 البوت يخليك:</u></b>\n\n"
        "  📤 <b>ترفع</b> أي نوع ملف\n"
        "  🔍 <b>فحص</b> قوي تلقائي\n"
        "  ⚙️ <b>تثبيت</b> مكتبات\n"
        "  🌐 <b>رابط</b> شغال لكل مشروع ويب\n"
        "  📊 <b>لوحة تحكم</b> Web\n"
        "  🔒 <b>مشاريعك</b> خاصة\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b><u>🎯 اختر من القائمة:</u></b>"
    )

    bot.send_message(msg.chat.id, txt, parse_mode="HTML",
                    reply_markup=main_menu(uid))


@bot.callback_query_handler(func=lambda c: c.data == "main_menu")
def cb_main(call):
    try:
        bot.edit_message_text(
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n  <b>🏠 القائمة الرئيسية</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━",
            call.message.chat.id, call.message.message_id,
            reply_markup=main_menu(call.from_user.id),
            parse_mode="HTML"
        )
    except:
        bot.send_message(call.message.chat.id, "🏠 القائمة الرئيسية",
                        reply_markup=main_menu(call.from_user.id))


# ============================================================
# مشاريعي
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "my_projs")
def cb_my_projs(call):
    uid = call.from_user.id
    projs = get_projects_by_owner(uid)

    if not projs:
        txt = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  <b>📦 مشاريعي</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📭 <b>لا توجد مشاريع بعد</b>\n\n"
            "🎯 <b>ابدأ بإنشاء مشروعك الاول</b>"
        )

        m = types.InlineKeyboardMarkup()
        m.add(btn_green("➕ مشروع جديد", "new_proj"))
        m.add(btn_gray("🔙 رجوع", "main_menu"))

        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
        return

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>📦 مشاريعي</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>المستخدم:</b> <code>{uid}</code>\n"
        f"📊 <b>عدد المشاريع:</b> <code>{len(projs)}</code>\n\n"
    )

    m = types.InlineKeyboardMarkup(row_width=1)

    for p in projs:
        pid = p[0]
        running = is_running(pid)
        up = calc_uptime(p[16]) if running else "متوقف"
        icon = "🐍" if p[3] == "bot" else "🌐"

        if running:
            label = f"{icon} {p[2]} | ⏱️ {up}"
            m.add(btn_green(label, f"proj_{pid}"))
        else:
            label = f"{icon} {p[2]} | 🔴 متوقف"
            m.add(btn_red(label, f"proj_{pid}"))

    m.add(btn_green("➕ مشروع جديد", "new_proj"))
    m.add(btn_gray("🔙 رجوع", "main_menu"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


# ============================================================
# تفاصيل مشروع
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("proj_"))
def cb_proj(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[5:])
    except:
        return

    p = get_project(pid)
    if not p:
        bot.answer_callback_query(call.id, "❌ المشروع غير موجود", show_alert=True)
        return

    if not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        log_action(uid, "unauthorized_access", f"project={pid}")
        return

    running = is_running(pid)
    up = calc_uptime(p[16]) if running else "—"
    icon = type_label(p[3])
    files = get_project_files(pid)

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  <b>{p[2]}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏷️ <b>النوع:</b> {icon}\n"
        f"📄 <b>الملف:</b> <code>{p[5]}</code>\n"
        f"📁 <b>الملفات:</b> <code>{len(files)}</code>\n"
        f"📊 <b>الحالة:</b> {'🟢 شغال' if running else '🔴 متوقف'}\n"
        f"⏱️ <b>المدة:</b> <code>{up}</code>\n"
        f"🆔 <b>PID:</b> <code>{p[14] or '—'}</code>\n"
    )

    if p[3] == "web" and p[12]:
        txt += f"\n🔗 <b>الرابط:</b>\n<code>{p[12]}</code>\n"

    m = types.InlineKeyboardMarkup(row_width=2)

    if running:
        m.row(btn_red("⏹️ ايقاف", f"stop_{pid}"), btn_blue("📄 السجلات", f"logs_{pid}"))
    else:
        m.row(btn_green("▶️ تشغيل", f"start_{pid}"), btn_blue("📄 السجلات", f"logs_{pid}"))

    if p[3] == "web" and p[12]:
        m.row(btn_blue("🌐 فتح الموقع", f"openweb_{pid}"))

    if p[9]:
        m.row(btn_blue("🤖 فتح البوت", url=f"https://t.me/{p[9]}"))

    m.row(btn_blue("📁 الملفات", f"files_{pid}"), btn_blue("➕ اضافة ملف", f"addfile_{pid}"))
    m.row(btn_blue("🔍 فحص", f"check_{pid}"), btn_blue("⚙️ مكتبة", f"addpkg_{pid}"))
    if p[5].endswith(".py"):
        m.row(btn_green("🪄 تثبيت تلقائي ذكي", f"smart_{pid}"))
    m.row(btn_blue("🔁 إعادة تشغيل", f"restart_{pid}"), btn_blue("📁 ملفات (محسّن)", f"pfiles_{pid}:0"))
    m.add(btn_red("🗑️ حذف", f"del_{pid}"))
    m.add(btn_gray("🔙 رجوع", "my_projs"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


# ============================================================
# تشغيل
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("start_"))
def cb_start(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[6:])
    except:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    msg = bot.send_message(call.message.chat.id, "⏳ جاري التجهيز...",
                          parse_mode="HTML")

    def do_start():
        for i in range(5, 0, -1):
            try:
                bar = "█" * (5 - i) + "░" * i
                bot.edit_message_text(
                    f"⏳ <b>جاري التجهيز...</b>\n\n"
                    f"⏱️ يبدا خلال: {i} ثانية\n\n{bar}",
                    msg.chat.id, msg.message_id, parse_mode="HTML"
                )
            except:
                pass
            time.sleep(1)

        ok, m, info = start_project(pid)

        if ok and info:
            text = make_success_message(info)

            markup = types.InlineKeyboardMarkup(row_width=1)
            if info.get("bot_username"):
                markup.add(btn_blue("🤖 فتح البوت",
                                   url=f"https://t.me/{info['bot_username']}"))
            if info.get("public_url"):
                markup.add(btn_blue("🌐 فتح الموقع", url=info["public_url"]))
            markup.row(
                btn_blue("📄 السجلات", f"logs_{pid}"),
                btn_gray("🔙 رجوع", f"proj_{pid}")
            )

            bot.edit_message_text(text, msg.chat.id, msg.message_id,
                                 reply_markup=markup, parse_mode="HTML")
        else:
            bot.edit_message_text(
                f"<b>❌ فشل التشغيل</b>\n\n{m}",
                msg.chat.id, msg.message_id, parse_mode="HTML"
            )

    threading.Thread(target=do_start, daemon=True).start()


# ============================================================
# ايقاف
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("stop_"))
def cb_stop(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[5:])
    except:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    ok, msg = stop_project(pid)
    bot.answer_callback_query(call.id, msg, show_alert=True)

    call.data = f"proj_{pid}"
    cb_proj(call)


# ============================================================
# السجلات
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("logs_"))
def cb_logs(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[5:])
    except:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    logs = get_logs(pid, 80)
    if len(logs) > 3500:
        logs = "...\n" + logs[-3500:]

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  <b>📄 سجلات {p[2]}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<pre>{logs}</pre>"
    )

    m = types.InlineKeyboardMarkup()
    m.row(btn_blue("🔄 تحديث", f"logs_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        bot.send_message(call.message.chat.id, txt, reply_markup=m,
                        parse_mode="HTML")


# ============================================================
# فتح الموقع
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("openweb_"))
def cb_openweb(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[8:])
    except:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid) or not p[12]:
        bot.answer_callback_query(call.id, "❌ لا يوجد رابط", show_alert=True)
        return

    m = types.InlineKeyboardMarkup()
    m.add(btn_blue("🌐 فتح الموقع", url=p[12]))
    m.add(btn_gray("🔙 رجوع", f"proj_{pid}"))

    bot.send_message(
        call.message.chat.id,
        f"🔗 <b>رابط موقعك:</b>\n\n<code>{p[12]}</code>",
        reply_markup=m, parse_mode="HTML"
    )
    bot.answer_callback_query(call.id)


# ============================================================
# فحص
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("check_"))
def cb_check(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[6:])
    except:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    fp = os.path.join(p[4], p[5])
    ok, msg = check_any_file(fp)

    if ok:
        bot.answer_callback_query(call.id, "✅ الفحص نجح", show_alert=True)
        bot.send_message(call.message.chat.id, msg, parse_mode="HTML")
    else:
        bot.send_message(call.message.chat.id, msg, parse_mode="HTML")


# ============================================================
# الملفات
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("files_"))
def cb_files(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[6:])
    except:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    files = list_dir_files(p[4])

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  <b>📁 ملفات {p[2]}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if not files:
        txt += "📭 لا توجد ملفات"
    else:
        txt += f"📊 <b>العدد:</b> <code>{len(files)}</code>\n\n"
        for f in files[:25]:
            fp = os.path.join(p[4], f)
            size = get_file_size_str(fp)
            txt += f"📄 <code>{f}</code> ({size})\n"
        if len(files) > 25:
            txt += f"\n... +{len(files) - 25} ملف"

    m = types.InlineKeyboardMarkup()
    m.add(btn_blue("➕ اضافة ملف", f"addfile_{pid}"))
    m.add(btn_gray("🔙 رجوع", f"proj_{pid}"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        bot.send_message(call.message.chat.id, txt, reply_markup=m,
                        parse_mode="HTML")


# ============================================================
# اضافة ملف
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("addfile_"))
def cb_addfile(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[8:])
    except:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    user_states[uid] = f"waiting_addfile_{pid}"

    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ الغاء", f"proj_{pid}"))

    bot.edit_message_text(
        "<b>📤 اضافة ملف</b>\n\n"
        "الانواع المدعومة:\n"
        "<code>.py .txt .json .env .sh .html .css .js .yml .yaml .zip .xml .md</code>\n\n"
        "📤 ارسل الملف الان",
        call.message.chat.id, call.message.message_id,
        reply_markup=m, parse_mode="HTML"
    )


# ============================================================
# اضافة مكتبة
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("addpkg_"))
def cb_addpkg(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[7:])
    except:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    user_states[uid] = f"waiting_pkg_{pid}"

    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ الغاء", f"proj_{pid}"))

    bot.edit_message_text(
        "<b>⚙️ تثبيت مكتبة</b>\n\n"
        "📝 ارسل اسم المكتبة\n\n"
        "مثال: <code>requests</code>",
        call.message.chat.id, call.message.message_id,
        reply_markup=m, parse_mode="HTML"
    )


@bot.message_handler(
    func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_pkg_")
)
def do_addpkg(msg):
    uid = msg.from_user.id
    try:
        pid = int(user_states[uid].replace("waiting_pkg_", ""))
    except:
        return

    pkg = msg.text.strip()
    if not pkg:
        bot.reply_to(msg, "❌ اسم غير صالح")
        return

    del user_states[uid]

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.reply_to(msg, "🚫 غير مصرح")
        return

    msg_wait = bot.reply_to(msg, f"⏳ جاري تثبيت: <code>{pkg}</code>...",
                           parse_mode="HTML")

    def do():
        ok, m = install_single_pkg(pid, pkg)

        if ok:
            txt = f"<b>✅ تم التثبيت</b>\n\n{m}"
        else:
            txt = f"<b>❌ فشل التثبيت</b>\n\n{m}"

        try:
            bot.edit_message_text(txt, msg_wait.chat.id, msg_wait.message_id,
                                 parse_mode="HTML")
        except:
            bot.send_message(msg.chat.id, txt, parse_mode="HTML")

        log_action(uid, "install_pkg", f"pid={pid} pkg={pkg} ok={ok}")

    threading.Thread(target=do, daemon=True).start()


# ============================================================
# حسابي
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "my_account")
def cb_account(call):
    uid = call.from_user.id
    u = get_user(uid)
    projs = get_projects_by_owner(uid)
    running = sum(1 for p in projs if is_running(p[0]))
    limit = get_max_projects(uid)

    premium_badge = "⭐ Premium" if is_premium(uid) else "👤 عادي"
    theme = get_user_theme(uid)

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>👤 حسابي</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 <b>ID:</b> <code>{uid}</code>\n"
        f"👤 <b>الاسم:</b> {call.from_user.first_name or '—'}\n"
        f"🏷️ <b>النوع:</b> {premium_badge}\n"
        f"🎨 <b>الثيم:</b> {theme}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b><u>📊 الاحصائيات:</u></b>\n\n"
        f"  📦 المشاريع: <code>{len(projs)}</code> / <code>{limit}</code>\n"
        f"  🟢 شغالة: <code>{running}</code>\n"
        f"  📤 الرفعات: <code>{u[7] if u and len(u) > 7 else 0}</code>\n"
    )

    m = types.InlineKeyboardMarkup()
    m.add(btn_blue("🎨 تخصيص الالوان", "my_theme"))
    m.add(btn_gray("🔙 رجوع", "main_menu"))

    bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                         reply_markup=m, parse_mode="HTML")


# ============================================================
# تخصيص الالوان
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "my_theme")
def cb_my_theme(call):
    uid = call.from_user.id
    current = get_user_theme(uid)

    txt = (
        "<b>🎨 تخصيص الالوان</b>\n\n"
        f"🎨 الثيم الحالي: <code>{current}</code>\n\n"
        "اختر اللون اللي تحبه:"
    )

    m = make_color_keyboard(current)
    m.add(btn_gray("🔙 رجوع", "my_account"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("set_theme_"))
def cb_set_theme(call):
    uid = call.from_user.id
    color = call.data.replace("set_theme_", "")

    valid = [c[0] for c in COLOR_OPTIONS]
    if color not in valid:
        bot.answer_callback_query(call.id, "لون غير صالح", show_alert=True)
        return

    set_user_theme(uid, color)
    log_action(uid, "set_theme", color)

    bot.answer_callback_query(call.id, "✅ تم تغيير الثيم", show_alert=True)
    cb_my_theme(call)


# ============================================================
# مساعدة + زر المطور
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "help")
def cb_help(call):
    uid = call.from_user.id

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b><u>📚 المساعدة</u></b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b><u>📝 خطوات إنشاء مشروع:</u></b>\n\n"

        "<b>1.</b> اضغط <b>➕ مشروع جديد</b>\n"
        "<b>2.</b> اختر النوع:\n"
        "     • <i>🐍 بوت Python</i>\n"
        "     • <i>🌐 موقع Web</i>\n"
        "<b>3.</b> ارسل اسم المشروع\n"
        "<b>4.</b> ارسل ملف <code>.py</code> الرئيسي\n"
        "<b>5.</b> ارسل <code>requirements.txt</code> (اختياري)\n"
        "<b>6.</b> اضغط <b>⚙️ ثبّت</b>\n"
        "<b>7.</b> اضغط <b>▶️ تشغيل</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b><u>🌐 للمشاريع الويب:</u></b>\n\n"
        "البوت هيطلعلك <b>رابط تلقائي</b>\n"
        "الرابط <u>شغال من أي مكان</u>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b><u>🔒 الأمان:</u></b>\n\n"
        "مشاريعك <b>خاصة</b> — محدش يشوفها\n"
        "كل مستخدم ليه رابط خاص بيه\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b><u>📞 التواصل:</u></b>\n\n"
        "للدعم أو الاستفسار:\n"
        f"👤 المطور: @{DEVELOPER_USERNAME}\n"
    )

    m = types.InlineKeyboardMarkup(row_width=1)

    if is_admin(uid):
        m.add(make_custom_btn("btn_admin", cb="admin_panel"))

    m.add(btn_gray("🔙 رجوع", "main_menu"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        bot.send_message(call.message.chat.id, txt, reply_markup=m,
                        parse_mode="HTML")


# ============================================================
# التواصل مع الدعم
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "contact_support")
def cb_contact_support(call):
    uid = call.from_user.id

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b><u>💬 التواصل مع الدعم</u></b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "📤 <b>اكتب رسالتك الآن</b>\n\n"
        "<i>سيتم إرسالها للمطور مباشرة</i>"
    )

    user_states[uid] = "waiting_support_msg"

    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ إلغاء", "main_menu"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


@bot.message_handler(
    func=lambda m: user_states.get(m.from_user.id) == "waiting_support_msg"
)
def do_support_msg(msg):
    uid = msg.from_user.id
    user = get_user(uid)
    name = user[2] if user else "مستخدم"

    del user_states[uid]

    support_txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>💬 رسالة دعم جديدة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>من:</b> {name}\n"
        f"🆔 <b>ID:</b> <code>{uid}</code>\n"
        f"🔗 <b>يوزر:</b> @{msg.from_user.username or 'None'}\n\n"
        f"📝 <b>الرسالة:</b>\n\n{msg.text}"
    )

    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, support_txt, parse_mode="HTML")
        except:
            pass

    bot.reply_to(msg, "✅ <b>تم إرسال رسالتك للمطور</b>\n\nسيتم الرد عليك قريباً",
                parse_mode="HTML")
    log_action(uid, "support_message", msg.text[:100])
    
# ============================================================
# مشروع جديد
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "new_proj")
def cb_new(call):
    uid = call.from_user.id

    ok, count, limit = check_user_limit(uid)
    if not ok:
        if is_paid_mode():
            cur = get_ui("currency_name") or "$"
            bal = get_balance(uid)
            sp = get_ui("slot_price") or "2.0"
            txt_lim = (
                f"{make_header('💎 الترقية أو شراء خانة مشروع')}\n\n"
                f"📦 <b>مشاريعك الحالية:</b> <code>{count}/{limit}</code>\n"
                f"💳 <b>رصيدك:</b> <code>{bal:.2f} {cur}</code>\n"
                f"🏷 <b>سعر الخانة الإضافية:</b> <code>{sp} {cur}</code>\n\n"
                "اشترِ خانة إضافية أو اشحن رصيدك أو ادعُ أصدقاءك للحصول على رصيد مجاني:"
            )
            mlim = types.InlineKeyboardMarkup(row_width=1)
            mlim.add(btn_green(f"🛒 شراء خانة مشروع ({sp} {cur})", "shop_buy_slot"))
            mlim.row(btn_blue("💳 شحن الرصيد", "wallet_recharge"), btn_blue("🤝 دعوة أصدقاء", "referral_menu"))
            mlim.add(btn_gray("🔙 رجوع", "main_menu"))
            try:
                bot.edit_message_text(txt_lim, call.message.chat.id, call.message.message_id, reply_markup=mlim, parse_mode="HTML")
            except Exception:
                bot.send_message(call.message.chat.id, txt_lim, reply_markup=mlim, parse_mode="HTML")
            return
        bot.answer_callback_query(call.id,
                                 f"⚠️ وصلت الحد الاقصى ({count}/{limit})",
                                 show_alert=True)
        return

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>➕ مشروع جديد</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>مشاريعك:</b> <code>{count}</code> / <code>{limit}</code>\n\n"
        "🎯 <b>اختر نوع الخدمة:</b>"
    )

    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_green("🐍 بوت Python", "type_bot"), btn_blue("🌐 موقع Web", "type_web"))
    m.row(btn_blue("📜 سكربت Python", "type_script"), btn_blue("🟨 بوت Node.js", "type_node"))
    m.add(btn_gray("❌ الغاء", "main_menu"))

    bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                         reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("type_"))
def cb_type(call):
    ptype = call.data[5:]
    uid = call.from_user.id
    user_states[uid] = f"waiting_name_{ptype}"

    type_name = type_label(ptype)

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>📝 اسم المشروع</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏷️ <b>النوع:</b> {type_name}\n\n"
        "<b><u>📝 الخطوة 1/3</u></b>\n\n"
        "📤 ارسل اسم المشروع\n\n"
        "💡 ملاحظات:\n"
        "  • بدون مسافات\n"
        "  • بالانجليزي افضل"
    )

    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ الغاء", "main_menu"))

    bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                         reply_markup=m, parse_mode="HTML")


@bot.message_handler(
    func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_name_")
)
def msg_name(msg):
    uid = msg.from_user.id
    ptype = user_states[uid].replace("waiting_name_", "")

    name = msg.text.strip().replace(" ", "_")[:30]
    if not name:
        bot.reply_to(msg, "❌ اسم غير صالح")
        return

    projs = get_projects_by_owner(uid)
    if any(p[2] == name for p in projs):
        bot.reply_to(msg, "⚠️ الاسم موجود بالفعل - اختر اسم تاني")
        return

    user_states[uid] = f"waiting_file_{ptype}_{name}"

    type_name = type_label(ptype)

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>📤 رفع الملف الرئيسي</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏷️ <b>النوع:</b> {type_name}\n"
        f"✅ <b>الاسم:</b> <code>{name}</code>\n\n"
        "<b><u>📝 الخطوة 2/3</u></b>\n\n"
        f"📤 ارسل الملف الرئيسي ({'<code>.js</code>' if ptype == 'node' else '<code>.py</code>'})\n\n"
        "💡 المسموح: <code>.py .js .zip</code>"
    )

    bot.reply_to(msg, txt, parse_mode="HTML")
    
# ============================================================
# استقبال الملفات
# ============================================================
ALLOWED_EXTS = [
    ".py", ".txt", ".json", ".env", ".sh", ".html", ".css",
    ".js", ".yml", ".yaml", ".zip", ".xml", ".md", ".toml",
    ".ini", ".cfg", ".csv",
]
ALLOWED_FILENAMES = [
    "Procfile", "Makefile", "Dockerfile", "LICENSE",
    "README", ".gitignore",
]
MAX_FILE_SIZE = 20 * 1024 * 1024


def is_allowed_file(filename):
    ext = os.path.splitext(filename)[1].lower()
    basename = os.path.basename(filename)
    return ext in ALLOWED_EXTS or basename in ALLOWED_FILENAMES


@bot.message_handler(content_types=['document'])
def handle_doc(msg):
    uid = msg.from_user.id
    state = user_states.get(uid, "")

    if is_banned(uid):
        return

    # 1) الملف الرئيسي
    if state.startswith("waiting_file_"):
        parts = state.replace("waiting_file_", "").split("_", 1)
        ptype = parts[0]
        name = parts[1] if len(parts) > 1 else "proj"

        filename = msg.document.file_name or "unknown"
        ext = os.path.splitext(filename)[1].lower()

        if not is_allowed_file(filename):
            bot.send_message(msg.chat.id,
                f"❌ نوع غير مدعوم: <code>{ext}</code>", parse_mode="HTML")
            return

        if msg.document.file_size and msg.document.file_size > MAX_FILE_SIZE:
            bot.send_message(msg.chat.id,
                "❌ الملف كبير جدا (الحد 20 MB)", parse_mode="HTML")
            return

        try:
            fi = bot.get_file(msg.document.file_id)
            data = bot.download_file(fi.file_path)

            pdir = os.path.join(PROJECTS_DIR, f"{uid}_{name}")
            os.makedirs(pdir, exist_ok=True)

            if ext == ".zip":
                zip_path = os.path.join(pdir, filename)
                with open(zip_path, "wb") as f:
                    f.write(data)

                try:
                    with zipfile.ZipFile(zip_path, "r") as z:
                        z.extractall(pdir)
                    os.remove(zip_path)

                    all_files = list_dir_files(pdir)
                    py_files = [f for f in all_files if f.endswith(".py")]

                    if not py_files:
                        bot.send_message(msg.chat.id,
                            "❌ لا يوجد ملف .py في الـ ZIP", parse_mode="HTML")
                        shutil.rmtree(pdir, ignore_errors=True)
                        return

                    entry = py_files[0]
                    if "main.py" in py_files: entry = "main.py"
                    elif "app.py" in py_files: entry = "app.py"
                    elif "bot.py" in py_files: entry = "bot.py"

                    bot.send_message(msg.chat.id,
                        f"📦 تم فك الـ ZIP ({len(all_files)} ملف)\n"
                        f"🎯 الملف الرئيسي: <code>{entry}</code>",
                        parse_mode="HTML")
                except Exception as e:
                    bot.send_message(msg.chat.id, f"❌ خطأ ZIP: <code>{e}</code>",
                                   parse_mode="HTML")
                    shutil.rmtree(pdir, ignore_errors=True)
                    return
            else:
                entry = filename
                fp = os.path.join(pdir, entry)
                with open(fp, "wb") as f:
                    f.write(data)

            entry_path = os.path.join(pdir, entry)

            ok, chk = check_any_file(entry_path)
            if not ok:
                bot.send_message(msg.chat.id,
                    f"⚠️ <b>فشل الفحص</b>\n\n{chk}\n\n📤 ارسل ملف معدل",
                    parse_mode="HTML")
                shutil.rmtree(pdir, ignore_errors=True)
                return

            info = {"token": None, "username": None, "name": None}
            if ptype == "bot":
                info = extract_bot_info(entry_path)

            pid = add_project(uid, name, ptype, pdir, entry)
            add_project_file(pid, entry)

            try:
                backup_project_to_storage(pid)
            except Exception as e:
                print(f"[Storage] {e}")

            try:
                if entry_path.endswith(".py"):
                    info_tok = extract_bot_info(entry_path)
                    if info_tok.get("username"):
                        update_project(pid, bot_token=info_tok.get("token"),
                                      bot_username=info_tok.get("username"), bot_name=info_tok.get("name"))
            except Exception as e:
                print(f"[TokenDetect] {e}")

            if info.get("token"):
                update_project(pid,
                    bot_token=info["token"],
                    bot_username=info["username"] or "",
                    bot_name=info["name"] or "")

            with DB_LOCK:
                conn = _conn()
                c = conn.cursor()
                c.execute("UPDATE users SET total_uploads = total_uploads + 1 WHERE user_id=?",
                         (uid,))
                conn.commit()
                conn.close()

            log_action(uid, "upload_file", f"pid={pid} file={entry}")

            txt = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "  <b>✅ تم رفع الملف</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 <b>المشروع:</b> <code>{name}</code>\n"
                f"📄 <b>الملف:</b> <code>{entry}</code>\n"
                f"📊 <b>الحجم:</b> <code>{get_file_size_str(entry_path)}</code>\n"
                f"🔍 <b>الفحص:</b> ✅ سليم\n\n"
            )

            if info.get("username"):
                txt += (
                    "<b><u>🤖 معلومات البوت:</u></b>\n"
                    f"  👤 اليوزر: @{info['username']}\n"
                    f"  🔗 الرابط: https://t.me/{info['username']}\n\n"
                )

            txt += (
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "<b><u>📝 الخطوة التالية:</u></b>\n\n"
                "هل عندك ملف <code>requirements.txt</code>؟"
            )

            user_states[uid] = f"waiting_req_{pid}"

            m = types.InlineKeyboardMarkup()
            m.row(
                btn_green("✅ نعم، سأرفعه", f"upload_req_{pid}"),
                btn_blue("⏭️ تخطي", f"proj_{pid}")
            )

            bot.send_message(msg.chat.id, txt, parse_mode="HTML",
                           reply_markup=m)

        except Exception as e:
            bot.send_message(msg.chat.id, f"❌ خطأ: <code>{str(e)[:300]}</code>",
                           parse_mode="HTML")

    # 2) requirements.txt
    elif state.startswith("waiting_req_"):
        try:
            pid = int(state.replace("waiting_req_", ""))
        except:
            return

        p = get_project(pid)
        if not p or not can_access_project(p, uid):
            bot.reply_to(msg, "🚫 غير مصرح")
            return

        try:
            fi = bot.get_file(msg.document.file_id)
            data = bot.download_file(fi.file_path)

            rp = os.path.join(p[4], "requirements.txt")
            with open(rp, "wb") as f:
                f.write(data)

            with open(rp, "r", encoding="utf-8", errors="ignore") as f:
                reqs = f.read().strip()

            if not reqs:
                bot.send_message(msg.chat.id, "⚠️ الملف فاضي")
                return

            update_project(pid, requirements=reqs)
            add_project_file(pid, "requirements.txt")
            del user_states[uid]

            txt = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "  <b>📋 requirements.txt</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 <b>عدد المكتبات:</b> <code>{len([l for l in reqs.splitlines() if l.strip()])}</code>\n\n"
                f"<pre>{reqs[:600]}</pre>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "<b><u>⚙️ الخطوة التالية:</u></b>\n\n"
                "هل تريد تثبيت المكتبات الان؟"
            )

            m = types.InlineKeyboardMarkup()
            m.row(
                btn_green("⚙️ نعم، ثبّت", f"install_{pid}"),
                btn_blue("⏭️ تخطي", f"proj_{pid}")
            )

            bot.send_message(msg.chat.id, txt, parse_mode="HTML",
                           reply_markup=m)

        except Exception as e:
            bot.reply_to(msg, f"❌ خطأ: <code>{e}</code>", parse_mode="HTML")

    # 3) ملف اضافي
    elif state.startswith("waiting_addfile_"):
        try:
            pid = int(state.replace("waiting_addfile_", ""))
        except:
            return

        p = get_project(pid)
        if not p or not can_access_project(p, uid):
            bot.reply_to(msg, "🚫 غير مصرح")
            return

        filename = msg.document.file_name or "unknown"
        if not is_allowed_file(filename):
            bot.send_message(msg.chat.id,
                f"❌ نوع غير مدعوم: <code>{filename}</code>", parse_mode="HTML")
            return

        try:
            fi = bot.get_file(msg.document.file_id)
            data = bot.download_file(fi.file_path)

            fp = os.path.join(p[4], filename)
            with open(fp, "wb") as f:
                f.write(data)

            ok, chk = check_any_file(fp)
            add_project_file(pid, filename)
            del user_states[uid]

            txt = (
                "<b>✅ تم اضافة الملف</b>\n\n"
                f"📄 الملف: <code>{filename}</code>\n"
                f"📊 الحجم: <code>{get_file_size_str(fp)}</code>\n\n"
            )

            if ok:
                txt += "🔍 الفحص: ✅ سليم"
            else:
                txt += f"⚠️ <b>تحذير:</b>\n{chk}"

            m = types.InlineKeyboardMarkup()
            m.add(btn_gray("🔙 رجوع", f"proj_{pid}"))

            bot.send_message(msg.chat.id, txt, parse_mode="HTML",
                           reply_markup=m)
            log_action(uid, "add_file", f"pid={pid} file={filename}")

        except Exception as e:
            bot.reply_to(msg, f"❌ خطأ: <code>{e}</code>", parse_mode="HTML")


# ============================================================
# زر رفع requirements
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("upload_req_"))
def cb_upload_req(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[11:])
    except:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    user_states[uid] = f"waiting_req_{pid}"

    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ الغاء", f"proj_{pid}"))

    bot.edit_message_text(
        "<b>📤 رفع requirements.txt</b>\n\n"
        "📋 ارسل ملف requirements.txt\n\n"
        "مثال:\n<code>requests\nflask\npyTelegramBotAPI</code>",
        call.message.chat.id, call.message.message_id,
        reply_markup=m, parse_mode="HTML"
    )


# ============================================================
# التثبيت المتدرج
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("smart_"))
def cb_smart(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[6:])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    entry_path = os.path.join(p[4], p[5])
    detected = []
    if p[5].endswith(".py") and os.path.exists(entry_path):
        try:
            detected = resolve_required_pkgs(entry_path)
        except Exception:
            detected = []
    for line in (p[7] or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line not in detected:
            detected.append(line)

    if not detected:
        bot.answer_callback_query(call.id, "✅ لا توجد مكتبات خارجية مطلوبة لهذا المشروع", show_alert=True)
        return

    bot.answer_callback_query(call.id, f"🪄 بدء التثبيت الذكي ({len(detected)} مكتبة)")
    init_items = [(f"<code>{pkg}</code> <i>(في الانتظار)</i>", False) for pkg in detected]
    try:
        bot.edit_message_text(
            render_progress(f"🪄 التثبيت الذكي — {p[2]}", "✅", "⏳", init_items,
                            footer="🧪 <i>جاري تجهيز البيئة المعزولة (.venv)...</i>"),
            call.message.chat.id, call.message.message_id, parse_mode="HTML"
        )
    except Exception:
        pass

    def do_smart():
        ensure_venv(p[4])
        items = []
        ok_n = fail_n = 0
        for i, pkg in enumerate(detected, 1):
            cur_items = list(items) + [(f"<code>{pkg}</code> <b>(جاري التثبيت...)</b>", False)]
            try:
                bot.edit_message_text(
                    render_progress(f"🪄 التثبيت الذكي ({i}/{len(detected)})", "✅", "⚙️", cur_items,
                                    footer="🛡️ <i>تثبيت معزول خاص بهذا المشروع فقط</i>"),
                    call.message.chat.id, call.message.message_id, parse_mode="HTML"
                )
            except Exception:
                pass
            ok, res_msg = pip_install_in(p[4], pkg)
            if ok:
                ok_n += 1
                items.append((res_msg, True))
            else:
                fail_n += 1
                items.append((res_msg, False))

        update_project(pid, requirements="\n".join(detected), installed=1)
        footer = f"✅ نجح: <code>{ok_n}</code> · ❌ فشل: <code>{fail_n}</code> · 🛡️ البيئة: <code>معزولة</code>"
        final_txt = render_progress(f"🪄 نتيجة التثبيت الذكي — {p[2]}", "✅", "❌", items, footer=footer)
        m = types.InlineKeyboardMarkup(row_width=2)
        m.add(btn_green("▶️ تشغيل المشروع", f"start_{pid}"))
        m.row(btn_blue("📄 السجلات", f"logs_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))
        try:
            bot.edit_message_text(final_txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
        except Exception:
            bot.send_message(call.message.chat.id, final_txt, reply_markup=m, parse_mode="HTML")
        log_action(uid, "smart_install", f"pid={pid} ok={ok_n} fail={fail_n}")

    threading.Thread(target=do_smart, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data.startswith("install_"))
def cb_install(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[8:])
    except:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    reqs = p[7] or ""
    lines = [l.strip() for l in reqs.split("\n") if l.strip() and not l.startswith("#")]

    if not lines:
        bot.answer_callback_query(call.id, "⚠️ لا توجد مكتبات", show_alert=True)
        return

    bot.answer_callback_query(call.id, f"⏳ بدء التثبيت ({len(lines)})")

    start_txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>⚙️ بدء التثبيت</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 <b>عدد المكتبات:</b> <code>{len(lines)}</code>\n\n"
        "⏳ جاري التثبيت..."
    )

    try:
        bot.edit_message_text(start_txt, call.message.chat.id,
                             call.message.message_id, parse_mode="HTML")
    except:
        pass

    def do_install():
        results = []
        success_count = 0
        fail_count = 0

        prog_items = []
        for i, pkg in enumerate(lines, 1):
            cur_view = list(prog_items) + [(f"<code>{pkg}</code> <b>(جاري التثبيت...)</b>", False)]
            try:
                bot.edit_message_text(
                    render_progress(f"⚙️ تثبيت المتطلبات ({i}/{len(lines)})", "✅", "⏳", cur_view),
                    call.message.chat.id, call.message.message_id, parse_mode="HTML"
                )
            except:
                pass

            ok, m = install_single_pkg(pid, pkg)

            if ok:
                success_count += 1
                results.append(f"✅ {m}")
                prog_items.append((m, True))
            else:
                fail_count += 1
                results.append(f"❌ {m}")
                prog_items.append((m, False))

        update_project(pid, installed=1)

        final = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  <b>📊 نتيجة التثبيت</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ <b>نجح:</b> <code>{success_count}</code>\n"
            f"❌ <b>فشل:</b> <code>{fail_count}</code>\n\n"
            "<b><u>📋 التفاصيل:</u></b>\n"
        )
        final += "\n".join(results[:30])
        if len(results) > 30:
            final += f"\n... +{len(results) - 30} نتيجة"

        final += "\n\n" + "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        final += "<b><u>🎯 الخطوة التالية:</u></b>"

        m = types.InlineKeyboardMarkup(row_width=1)
        m.add(btn_green("▶️ تشغيل المشروع", f"start_{pid}"))
        m.add(btn_blue("📄 السجلات", f"logs_{pid}"))
        m.add(btn_gray("🔙 رجوع", f"proj_{pid}"))

        try:
            bot.edit_message_text(final, call.message.chat.id,
                                 call.message.message_id,
                                 reply_markup=m, parse_mode="HTML")
        except:
            bot.send_message(call.message.chat.id, final,
                           reply_markup=m, parse_mode="HTML")

        log_action(uid, "install_all", f"pid={pid} ok={success_count} fail={fail_count}")

    threading.Thread(target=do_install, daemon=True).start()


# ============================================================
# حذف
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("del_"))
def cb_del(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[4:])
    except:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>⚠️ تاكيد الحذف</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 <b>المشروع:</b> <code>{p[2]}</code>\n"
        f"📄 <b>الملف:</b> <code>{p[5]}</code>\n\n"
        "⚠️ هذا الاجراء لا يمكن التراجع عنه"
    )

    m = types.InlineKeyboardMarkup()
    m.row(btn_red("✅ نعم، احذف", f"cdel_{pid}"), btn_green("❌ الغاء", f"proj_{pid}"))

    bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                         reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("cdel_"))
def cb_cdel(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[5:])
    except:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    stop_project(pid)

    if p[4] and PROJECTS_DIR in p[4]:
        try:
            shutil.rmtree(p[4], ignore_errors=True)
        except:
            pass

    delete_project(pid)
    log_action(uid, "delete_project", f"pid={pid} name={p[2]}")

    bot.answer_callback_query(call.id, "✅ تم الحذف")
    call.data = "my_projs"
    cb_my_projs(call)


# ============================================================
# لوحة المطور
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "admin_panel")
def cb_admin(call):
    uid = call.from_user.id
    if not is_admin(uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    projs = get_all_projects()
    running = sum(1 for p in projs if is_running(p[0]))
    banned_count = sum(1 for u in get_all_users_full() if u[4] == 1)

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>👑 لوحة المطور</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b><u>📊 إحصائيات:</u></b>\n\n"
        f"  👥 المستخدمين: <code>{get_user_count()}</code>\n"
        f"  🚫 المحظورين: <code>{banned_count}</code>\n"
        f"  📦 المشاريع: <code>{len(projs)}</code>\n"
        f"  🟢 شغالة: <code>{running}</code>\n"
        f"  ⏱️ Uptime: <code>{calc_uptime(START_TIME.strftime('%Y-%m-%d %H:%M:%S'))}</code>\n\n"
        f"  🌐 Tunnel: <code>{TUNNEL_URL or 'غير متصل'}</code>\n"
    )

    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_red("📢 اعلان", "broadcast"), btn_blue("📊 احصائيات", "admin_stats"))
    m.row(btn_blue("👥 المستخدمين", "admin_users"), btn_blue("📦 المشاريع", "admin_projs"))
    m.row(btn_blue("🔗 Tunnel", "admin_tunnel"), btn_green("🌐 ويب المراقبة (OK)", "admin_demo_web"))
    m.row(btn_blue("📥 تخزين المطوّر", "admin_storage_0"), btn_blue("💳 المدفوعات والباقات", "admin_billing"))
    m.row(btn_blue("📋 سجل الاحداث", "admin_logs"), btn_green("🔓 فك حظر", "admin_unban"))
    mode_lbl = "💰 الوضع: مدفوع 💎" if is_paid_mode() else "🆓 الوضع: مجاني"
    m.row(btn_green("🎨 تخصيص البوت (أزرار + نصوص + نسخ)", "admin_premium"))
    m.row(btn_blue("💽 نسخة DB كاملة", "admin_backup_db"), btn_blue("📥 استعادة DB كاملة", "admin_restore_db"))
    m.row(btn_blue("🔁 إعادة تشغيل الكل", "admin_restart_all"), btn_red("♻️ إعادة تشغيل الاستضافة", "admin_restart_host"))
    m.row(btn(f"{'▶️ تشغيل الاستضافة' if host_paused() else '⏸ إيقاف الاستضافة'}", "admin_toggle_pause",
             style="success" if host_paused() else "danger"))
    m.add(btn_gray("🔙 رجوع", "main_menu"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "admin_stats")
def cb_admin_stats(call):
    if not is_admin(call.from_user.id):
        return

    projs = get_all_projects()
    running = sum(1 for p in projs if is_running(p[0]))
    web_count = sum(1 for p in projs if p[3] == "web")
    bot_count = sum(1 for p in projs if p[3] == "bot")
    other_count = len(projs) - web_count - bot_count

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>📊 احصائيات</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>المستخدمين:</b> <code>{get_user_count()}</code>\n"
        f"📦 <b>المشاريع:</b> <code>{len(projs)}</code>\n"
        f"🐍 <b>بوتات:</b> <code>{bot_count}</code>\n"
        f"🌐 <b>مواقع:</b> <code>{web_count}</code>\n"
        f"✨ <b>أخرى (سكربت/Node):</b> <code>{other_count}</code>\n"
        f"🟢 <b>شغالة:</b> <code>{running}</code>\n"
    )

    m = types.InlineKeyboardMarkup()
    m.add(btn_gray("🔙 رجوع", "admin_panel"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "admin_users")
def cb_admin_users(call):
    if not is_admin(call.from_user.id):
        return

    users = get_all_users_full()
    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  <b>👥 المستخدمين ({len(users)})</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for u in users[:15]:
        projs = get_projects_by_owner(u[0])
        status = "🚫 BANNED" if u[4] == 1 else "✅ OK"
        txt += f"🆔 <code>{u[0]}</code> - {u[2] or 'N/A'} [{status}]\n"
        txt += f"  📦 مشاريع: {len(projs)}\n\n"

    m = types.InlineKeyboardMarkup()
    m.add(btn_gray("🔙 رجوع", "admin_panel"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "admin_projs")
def cb_admin_projs(call):
    if not is_admin(call.from_user.id):
        return

    projs = get_all_projects()
    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  <b>📦 المشاريع ({len(projs)})</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for p in projs[:15]:
        s = "🟢 شغال" if is_running(p[0]) else "🔴 متوقف"
        owner = get_user(p[1])
        owner_name = owner[2] if owner else "N/A"
        txt += f"<b>{p[2]}</b> (ID: {p[0]}) [{s}]\n"
        txt += f"  👤 المالك: {owner_name}\n\n"

    m = types.InlineKeyboardMarkup()
    m.add(btn_gray("🔙 رجوع", "admin_panel"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "admin_logs")
def cb_admin_logs(call):
    if not is_admin(call.from_user.id):
        return

    with DB_LOCK:
        conn = _conn()
        c = conn.cursor()
        c.execute("SELECT * FROM action_logs ORDER BY id DESC LIMIT 20")
        logs = c.fetchall()
        conn.close()

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>📋 سجل الاحداث</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if not logs:
        txt += "لا توجد احداث"
    else:
        for log in logs:
            l_id, u_id, action, details, ts = log
            txt += f"🕐 <code>{ts}</code>\n"
            txt += f"  {u_id} - {action}\n"
            if details:
                txt += f"  {details[:50]}\n"
            txt += "\n"

    m = types.InlineKeyboardMarkup()
    m.add(btn_gray("🔙 رجوع", "admin_panel"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


# ============================================================
# تعديل النصوص
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "admin_ui")
def cb_admin_ui(call):
    if not is_admin(call.from_user.id):
        return

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>📝 تخصيص النصوص</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "اختر النص الذي تريد تعديله:"
    )

    m = types.InlineKeyboardMarkup(row_width=1)
    for key, label in UI_TEMPLATES.items():
        m.add(btn_blue(f"📝 {label}", f"ui_edit_{key}"))
    m.add(btn_gray("🔙 رجوع", "admin_premium"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("ui_edit_"))
def cb_ui_edit(call):
    if not is_admin(call.from_user.id):
        return

    key = call.data.replace("ui_edit_", "")
    user_states[call.from_user.id] = f"waiting_ui_{key}"
    current = get_ui(key)

    txt = (
        f"<b>✏️ تعديل:</b> <code>{key}</code>\n\n"
        f"📄 القيمة الحالية:\n<pre>{html.escape(current)}</pre>\n\n"
        "📤 ارسل القيمة الجديدة (يدعم الإيموجي المميز والتنسيق):"
    )

    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ الغاء", "admin_ui"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


@bot.message_handler(
    func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_ui_")
)
def do_ui_edit(msg):
    if not is_admin(msg.from_user.id):
        return

    key = user_states[msg.from_user.id].replace("waiting_ui_", "")
    value = _entities_to_html(msg.text or "", msg.entities).strip()

    if not value:
        bot.reply_to(msg, "❌ القيمة فاضية")
        return
    if len(value) > 3000:
        bot.reply_to(msg, "❌ النص طويل جداً.")
        return

    set_ui(key, value)
    del user_states[msg.from_user.id]

    bot.reply_to(msg, f"✅ تم تحديث <code>{key}</code>", parse_mode="HTML")


# ============================================================
# تعديل الازرار
# ============================================================
def make_buttons_editor():
    all_btns = get_all_buttons()
    m = types.InlineKeyboardMarkup(row_width=1)

    for key, (name, style, row) in all_btns.items():
        display_style = style if style else "none"
        label = f"{name} | {display_style} | صف {row}"
        m.add(btn(label, f"edit_btn_{key}", style=style if style else None))

    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    return m


def edit_button_menu(key):
    name, style, row = get_button(key)

    m = types.InlineKeyboardMarkup(row_width=2)

    m.add(btn_blue("✏️ تغيير الاسم", f"chg_name_{key}"))

    m.row(
        btn_green("🟢 اخضر", f"chg_style_{key}_success"),
        btn_red("🔴 احمر", f"chg_style_{key}_danger"),
    )
    m.row(
        btn_blue("🔵 ازرق", f"chg_style_{key}_primary"),
        btn_gray("⚫ بدون", f"chg_style_{key}_none"),
    )

    m.row(
        btn_blue(f"⬆️ صف {row-1 if row > 0 else 0}", f"chg_row_{key}_{row-1 if row > 0 else 0}"),
        btn_blue(f"⬇️ صف {row+1}", f"chg_row_{key}_{row+1}"),
    )

    m.add(btn_gray("🔙 رجوع", "admin_buttons"))
    return m


@bot.callback_query_handler(func=lambda c: c.data == "admin_buttons")
def cb_admin_buttons(call):
    if not is_admin(call.from_user.id):
        return

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>🎨 تعديل الازرار</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "اختر زر لتعديله:\n\n"
        "<i>الصيغة: الاسم | اللون | الصف</i>"
    )

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=make_buttons_editor(),
                             parse_mode="HTML")
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("edit_btn_"))
def cb_edit_btn(call):
    if not is_admin(call.from_user.id):
        return

    key = call.data.replace("edit_btn_", "")
    name, style, row = get_button(key)

    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  <b>🎨 تعديل: {name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 <b>الاسم:</b> <code>{name}</code>\n"
        f"🎨 <b>اللون:</b> <code>{style or 'بدون'}</code>\n"
        f"📊 <b>الصف:</b> <code>{row}</code>\n\n"
        "اختر التعديل:"
    )

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=edit_button_menu(key),
                             parse_mode="HTML")
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("chg_name_"))
def cb_chg_name(call):
    if not is_admin(call.from_user.id):
        return

    key = call.data.replace("chg_name_", "")
    user_states[call.from_user.id] = f"waiting_btn_name_{key}"

    name, style, row = get_button(key)

    txt = (
        f"<b>✏️ تغيير اسم الزر</b>\n\n"
        f"📄 الاسم الحالي: <code>{name}</code>\n\n"
        f"📤 ارسل الاسم الجديد:"
    )

    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ الغاء", f"edit_btn_{key}"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


@bot.message_handler(
    func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_btn_name_")
)
def do_btn_name(msg):
    if not is_admin(msg.from_user.id):
        return

    key = user_states[msg.from_user.id].replace("waiting_btn_name_", "")
    new_name = msg.text.strip()[:30]

    if not new_name:
        bot.reply_to(msg, "❌ اسم غير صالح")
        return

    name, style, row = get_button(key)
    set_button(key, new_name, style, row)
    del user_states[msg.from_user.id]

    bot.reply_to(msg, f"✅ تم تغيير الاسم الي: <code>{new_name}</code>",
                parse_mode="HTML")
    log_action(msg.from_user.id, "edit_button_name", f"{key}={new_name}")


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

    name, old_style, row = get_button(key)
    set_button(key, name, style, row)

    bot.answer_callback_query(call.id, "✅ تم تغيير اللون", show_alert=True)
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
    except:
        return

    name, style, row = get_button(key)
    set_button(key, name, style, new_row)

    bot.answer_callback_query(call.id, "✅ تم التغيير", show_alert=True)
    cb_edit_btn(call)


# ============================================================
# تحديث Tunnel
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "admin_tunnel")
def cb_admin_tunnel(call):
    if not is_admin(call.from_user.id):
        return

    bot.answer_callback_query(call.id, "🔄 جاري تحديث Tunnel...")

    def refresh():
        url = start_tunnel()
        if url:
            set_ui("public_url", url)
            bot.send_message(call.message.chat.id,
                f"<b>✅ الرابط الجديد:</b>\n<code>{url}</code>",
                parse_mode="HTML")
        else:
            bot.send_message(call.message.chat.id, "❌ فشل تحديث Tunnel")

    threading.Thread(target=refresh, daemon=True).start()


# ============================================================
# فك حظر
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "admin_unban")
def cb_admin_unban(call):
    if not is_admin(call.from_user.id):
        return

    user_states[call.from_user.id] = "waiting_unban_id"

    txt = "<b>🔓 فك حظر</b>\n\n📤 ارسل ID المستخدم:"

    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ الغاء", "admin_panel"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_unban_id")
def do_unban(msg):
    if not is_admin(msg.from_user.id):
        return

    try:
        target = int(msg.text.strip())
    except:
        bot.reply_to(msg, "❌ ID غير صالح")
        return

    unban_user(target)
    del user_states[msg.from_user.id]

    bot.reply_to(msg, f"✅ تم فك حظر <code>{target}</code>", parse_mode="HTML")
    log_action(msg.from_user.id, "unban", f"target={target}")


# ============================================================
# اعلان
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "broadcast")
def cb_broadcast(call):
    if not is_admin(call.from_user.id):
        return

    user_states[call.from_user.id] = "broadcast_msg"

    txt = "<b>📢 اعلان عام</b>\n\n📤 ارسل الرسالة:"

    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ الغاء", "admin_panel"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except:
        pass


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "broadcast_msg")
def do_broadcast(msg):
    if not is_admin(msg.from_user.id):
        return

    text = msg.text
    del user_states[msg.from_user.id]

    users = get_all_users()
    sent = 0
    failed = 0

    bot.send_message(msg.chat.id, f"⏳ جاري الارسال لـ {len(users)} مستخدم...")

    for uid in users:
        try:
            bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
            time.sleep(0.05)
        except:
            failed += 1

    bot.send_message(msg.chat.id,
        f"✅ تم الارسال\nنجح: {sent}\nفشل: {failed}")
    log_action(msg.from_user.id, "broadcast", f"sent={sent} failed={failed}")


# ============================================================
# 💳 المحفظة + الشحن + الإحالات + الباقات (نظام المجاني / المدفوع)
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "wallet_menu")
def cb_wallet(call):
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
    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "wallet_recharge")
def cb_wallet_recharge(call):
    uid = call.from_user.id
    user_states[uid] = "waiting_recharge_amount"
    info = get_ui("recharge_info")
    cur = get_ui("currency_name") or "$"
    txt = (
        f"{make_header('💳 شحن الرصيد')}\n\n"
        f"{info}\n\n"
        f"📤 أرسل الآن المبلغ الذي تريد شحنه (بـ {cur})، مثال: <code>5</code>"
    )
    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ إلغاء", "wallet_menu"))
    bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_recharge_amount")
def do_recharge_amount(msg):
    uid = msg.from_user.id
    try:
        amt = float((msg.text or "").strip().replace(",", "."))
        if amt <= 0 or amt > 100000:
            raise ValueError()
    except Exception:
        bot.reply_to(msg, "❌ أرسل رقماً صحيحاً أكبر من 0.")
        return
    user_states[uid] = f"waiting_recharge_proof_{amt}"
    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ إلغاء", "wallet_menu"))
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
    user_states[call.from_user.id] = "waiting_gift_code"
    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ إلغاء", "wallet_menu"))
    bot.edit_message_text(f"{make_header('🎟 استخدام كود شحن')}\n\nأرسل الكود الآن:",
                          call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


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
    bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data in ("shop_buy_slot", "shop_buy_vip"))
def cb_shop_buy(call):
    uid = call.from_user.id
    cur = get_ui("currency_name") or "$"
    bal = get_balance(uid)
    if call.data == "shop_buy_slot":
        price = float(get_ui("slot_price") or 2.0)
        if bal < price:
            bot.answer_callback_query(call.id, f"❌ رصيدك غير كافٍ ({bal:.2f}/{price:.2f} {cur})", show_alert=True)
            return
        add_balance(uid, -price)
        add_max_projects(uid, 1)
        log_action(uid, "buy_slot", f"price={price}")
        bot.answer_callback_query(call.id, "✅ تم شراء خانة مشروع إضافية!", show_alert=True)
    else:
        price = float(get_ui("vip_price") or 5.0)
        if bal < price:
            bot.answer_callback_query(call.id, f"❌ رصيدك غير كافٍ ({bal:.2f}/{price:.2f} {cur})", show_alert=True)
            return
        add_balance(uid, -price)
        add_max_projects(uid, 5)
        with DB_LOCK:
            conn = _conn(); c = conn.cursor()
            c.execute("UPDATE users SET is_premium=1 WHERE user_id=?", (uid,))
            conn.commit(); conn.close()
        log_action(uid, "buy_vip", f"price={price}")
        bot.answer_callback_query(call.id, "⭐ مبروك! تمت ترقيتك إلى VIP (+5 مشاريع)", show_alert=True)
    cb_wallet(call)


@bot.callback_query_handler(func=lambda c: c.data == "referral_menu")
def cb_referral(call):
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
    bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_billing")
def cb_admin_billing(call):
    if not is_admin(call.from_user.id):
        return
    paid = is_paid_mode()
    cur = get_ui("currency_name") or "$"
    pending = get_pending_recharges()
    txt = (
        f"{make_header('💰 إدارة المدفوعات والباقات')}\n\n"
        f"⚙️ <b>الوضع الحالي:</b> {'💎 مدفوع' if paid else '🆓 مجاني'}\n"
        f"📦 <b>سعر الخانة:</b> <code>{get_ui('slot_price')} {cur}</code> · ⭐ <b>VIP:</b> <code>{get_ui('vip_price')} {cur}</code>\n"
        f"🤝 <b>مكافأة الإحالة:</b> <code>{get_ui('referral_bonus')} {cur}</code>\n"
        f"🎁 <b>مشاريع مجانية للمبتدئ:</b> <code>{get_ui('free_trial_projects')}</code>\n"
        f"⏳ <b>طلبات شحن معلقة:</b> <code>{len(pending)}</code>"
    )
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(btn("🔄 تبديل إلى: " + ("🆓 وضع مجاني" if paid else "💎 وضع مدفوع"),
              "adm_toggle_mode", style="success" if paid else "danger"))
    m.row(btn_blue(f"⏳ طلبات الشحن ({len(pending)})", "adm_recharges"), btn_green("🎟 إنشاء كود شحن", "adm_new_code"))
    m.row(btn_blue("💳 تعديل رصيد مستخدم", "adm_add_bal"), btn_blue("⚙️ تعديل الأسعار والمكافآت", "adm_prices"))
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "adm_toggle_mode")
def cb_adm_toggle_mode(call):
    if not is_admin(call.from_user.id):
        return
    new_mode = "free" if is_paid_mode() else "paid"
    set_ui("bot_mode", new_mode)
    bot.answer_callback_query(call.id, f"✅ تم التحويل إلى: {'💎 مدفوع' if new_mode == 'paid' else '🆓 مجاني'}", show_alert=True)
    cb_admin_billing(call)


@bot.callback_query_handler(func=lambda c: c.data == "adm_recharges")
def cb_adm_recharges(call):
    if not is_admin(call.from_user.id):
        return
    reqs = get_pending_recharges()
    cur = get_ui("currency_name") or "$"
    m = types.InlineKeyboardMarkup(row_width=2)
    if not reqs:
        txt = f"{make_header('⏳ طلبات الشحن')}\n\n📭 لا توجد طلبات معلقة."
    else:
        lines = [make_header(f"⏳ طلبات الشحن ({len(reqs)})"), ""]
        for r in reqs[:8]:
            rid, uid, amt, proof, _, ts = r
            lines.append(f"• <b>#{rid}</b> | <code>{uid}</code> | <code>{amt:.2f} {cur}</code>\n  {html.escape(proof[:50])}")
            m.row(btn_green(f"✅ قبول #{rid}", f"adm_rech_ok_{rid}"), btn_red(f"❌ رفض #{rid}", f"adm_rech_no_{rid}"))
        txt = "\n".join(lines)
    m.add(btn_gray("🔙 رجوع", "admin_billing"))
    bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith(("adm_rech_ok_", "adm_rech_no_")))
def cb_adm_rech_action(call):
    if not is_admin(call.from_user.id):
        return
    approve = call.data.startswith("adm_rech_ok_")
    rid = int(call.data.rsplit("_", 1)[1])
    req = get_recharge_request(rid)
    if not req or not resolve_recharge_request(rid, "approved" if approve else "rejected"):
        bot.answer_callback_query(call.id, "⚠️ الطلب معالَج مسبقاً أو غير موجود", show_alert=True)
        return
    _, uid, amt, _, _, _ = req
    cur = get_ui("currency_name") or "$"
    if approve:
        add_balance(uid, amt)
        bot.answer_callback_query(call.id, f"✅ تم قبول #{rid} وإضافة {amt:.2f} {cur}")
        try:
            bot.send_message(uid, f"✅ تم قبول طلب الشحن <code>#{rid}</code>! أُضيف <code>{amt:.2f} {cur}</code> لرصيدك.", parse_mode="HTML")
        except Exception:
            pass
    else:
        bot.answer_callback_query(call.id, f"❌ تم رفض #{rid}")
        try:
            bot.send_message(uid, f"❌ تم رفض طلب الشحن <code>#{rid}</code>.", parse_mode="HTML")
        except Exception:
            pass
    cb_adm_recharges(call)


@bot.callback_query_handler(func=lambda c: c.data == "adm_new_code")
def cb_adm_new_code(call):
    if not is_admin(call.from_user.id):
        return
    user_states[call.from_user.id] = "waiting_adm_code"
    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ إلغاء", "admin_billing"))
    bot.edit_message_text(f"{make_header('🎟 إنشاء كود شحن')}\n\nأرسل المبلغ وعدد الاستخدامات (مثال: <code>5 10</code>):",
                          call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


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
        bot.reply_to(msg, "❌ صيغة غير صحيحة. مثال: <code>5 1</code>", parse_mode="HTML")
        return
    code_str = "HOST-" + secrets.token_hex(4).upper()
    create_gift_code(code_str, amt, uses)
    cur = get_ui("currency_name") or "$"
    bot.reply_to(msg, f"✅ تم إنشاء الكود:\n<code>{code_str}</code>\n💰 القيمة: <code>{amt:.2f} {cur}</code> · 🔢 الاستخدامات: <code>{uses}</code>", parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "adm_add_bal")
def cb_adm_add_bal(call):
    if not is_admin(call.from_user.id):
        return
    user_states[call.from_user.id] = "waiting_adm_bal"
    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ إلغاء", "admin_billing"))
    bot.edit_message_text(f"{make_header('💳 تعديل رصيد مستخدم')}\n\nأرسل <code>USER_ID المبلغ</code> (مثال: <code>123456789 10</code>):",
                          call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_adm_bal")
def do_adm_add_bal(msg):
    if not is_admin(msg.from_user.id):
        return
    user_states.pop(msg.from_user.id, None)
    parts = (msg.text or "").split()
    try:
        t_uid, amt = int(parts[0]), float(parts[1])
    except Exception:
        bot.reply_to(msg, "❌ صيغة غير صحيحة.")
        return
    if not get_user(t_uid):
        bot.reply_to(msg, "❌ المستخدم غير موجود.")
        return
    add_balance(t_uid, amt)
    cur = get_ui("currency_name") or "$"
    bot.reply_to(msg, f"✅ تم تعديل رصيد <code>{t_uid}</code> بمقدار <code>{amt:+.2f} {cur}</code>.", parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "adm_prices")
def cb_adm_prices(call):
    if not is_admin(call.from_user.id):
        return
    m = types.InlineKeyboardMarkup(row_width=1)
    for k, lbl in [
        ("slot_price", "📦 سعر الخانة الإضافية"),
        ("vip_price", "⭐ سعر ترقية VIP"),
        ("referral_bonus", "🤝 مكافأة الإحالة"),
        ("free_trial_projects", "🎁 المشاريع المجانية في الوضع المدفوع"),
        ("currency_name", "💱 رمز/اسم العملة"),
        ("recharge_info", "📝 تعليمات وطرق الشحن"),
    ]:
        m.add(btn_blue(f"{lbl}: {get_ui(k)[:20]}", f"ui_edit_{k}"))
    m.add(btn_gray("🔙 رجوع", "admin_billing"))
    bot.edit_message_text(f"{make_header('⚙️ أسعار الباقات والإحالة')}", call.message.chat.id, call.message.message_id,
                          reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_demo_web")
def cb_admin_demo_web(call):
    if not is_admin(call.from_user.id):
        return
    base = get_public_url().rstrip("/")
    ping_on = get_ui("demo_web_ping") != "0"
    txt = (
        f"{make_header('🌐 ويب المراقبة التجريبي (OK)')}\n\n"
        f"📡 <b>حالة السيرفر:</b> <code>OK (200)</code>\n"
        f"🔄 <b>النبض التلقائي:</b> {'🟢 شغال (كل 10 دقائق)' if ping_on else '🔴 متوقف'}\n\n"
        f"🔗 <b>روابط المراقبة لـ UptimeRobot / Render:</b>\n"
        f"• الرئيسي: <code>{base}/</code>\n"
        f"• فحص سريع: <code>{base}/ping</code>\n"
        f"• الحالة JSON: <code>{base}/status</code>\n\n"
        "💡 <i>أضف أي رابط منها في UptimeRobot لمنع نوم السيرفر المفاجئ، أو شغّل الملف بـ <code>--web-only</code> لتشغيل الويب التجريبي فقط.</i>"
    )
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn("🔄 " + ("إيقاف النبض التلقائي" if ping_on else "تفعيل النبض التلقائي"),
              "admin_toggle_demo_ping", style="danger" if ping_on else "success"))
    m.add(btn_blue("🌐 فتح رابط /ping", url=f"{base}/ping"))
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_toggle_demo_ping")
def cb_toggle_demo_ping(call):
    if not is_admin(call.from_user.id):
        return
    cur_on = get_ui("demo_web_ping") != "0"
    set_ui("demo_web_ping", "0" if cur_on else "1")
    bot.answer_callback_query(call.id, "✅ تم " + ("إيقاف" if cur_on else "تفعيل") + " النبض التلقائي")
    cb_admin_demo_web(call)


# ============================================================
# Flask + Web Dashboard
# ============================================================
web = Flask(__name__)

# 👇 ضيف السطور دي هنا 👇
@web.route('/health', methods=['GET', 'HEAD'])
@web.route('/ping', methods=['GET', 'HEAD'])
@web.route('/demo', methods=['GET', 'HEAD'])
def health():
    if request.method == 'HEAD':
        return "", 200
    return "OK", 200


@web.route('/status', methods=['GET'])
def web_status():
    return {
        "status": "OK",
        "uptime": calc_uptime(START_TIME.strftime('%Y-%m-%d %H:%M:%S')),
        "started_at": START_TIME.strftime('%Y-%m-%d %H:%M:%S'),
        "self_ping": get_ui("demo_web_ping") != "0",
    }, 200
# 👆 لحد هنا 👆

# ============================================================
# Reverse Proxy
# ============================================================
@web.route('/proxy/status')
def proxy_status():
    return {"status": "ok", "message": "Reverse Proxy شغال"}


@web.route('/proxy/<int:pid>', defaults={'path': ''},
           methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
@web.route('/proxy/<int:pid>/<path:path>',
           methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
def proxy(pid, path):
    p = get_project(pid)
    if not p:
        return "<h1>المشروع غير موجود</h1>", 404
    if p[3] != "web":
        return "<h1>ده مش مشروع Web</h1>", 400
    if not is_running(pid):
        return "<body style='background:#111;color:#f55;text-align:center;padding:100px;font-family:sans-serif'><h1>المشروع متوقف حاليا</h1></body>", 503

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
            method=request.method, url=target,
            headers=headers,
            data=request.get_data(),
            cookies=request.cookies,
            params=request.args,
            allow_redirects=False,
            timeout=60,
            stream=False
        )
        excluded = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        out_headers = [(k, v) for k, v in resp.raw.headers.items()
                       if k.lower() not in excluded]
        return Response(resp.content, resp.status_code, out_headers)

    except req.exceptions.ConnectionError:
        return "<h1>فشل الاتصال بالمشروع</h1>", 502
    except Exception as e:
        return f"<h1>خطأ</h1><pre>{str(e)[:300]}</pre>", 500


# ============================================================
# Web Dashboard (مع SVG)
# ============================================================
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
.bg-svg { position:fixed; inset:0; width:100%; height:100%; z-index:-1; pointer-events:none; }
.container { max-width:1160px; margin:0 auto; position:relative; z-index:1; }
.topbar { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:14px; background:rgba(15,23,42,0.75); backdrop-filter:blur(14px); border:1px solid rgba(96,165,250,0.2); border-radius:18px; padding:16px 22px; margin-bottom:22px; box-shadow:0 10px 35px rgba(0,0,0,0.35); }
.brand { display:flex; align-items:center; gap:14px; }
.brand h1 { font-size:1.55em; background:linear-gradient(135deg,#60a5fa,#a78bfa,#34d399); -webkit-background-clip:text; -webkit-text-fill-color:transparent; font-weight:800; }
.pill-row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
.pill { background:rgba(30,41,59,0.9); border:1px solid #334155; padding:7px 14px; border-radius:999px; font-size:0.85em; display:inline-flex; align-items:center; gap:7px; color:#cbd5e1; }
.pill strong { color:#38bdf8; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:16px; margin-bottom:22px; }
.card { background:linear-gradient(160deg,rgba(30,41,59,0.88),rgba(15,23,42,0.92)); border:1px solid rgba(148,163,184,0.16); border-radius:16px; padding:20px; display:flex; align-items:center; justify-content:space-between; transition:transform .2s,border-color .2s; }
.card:hover { transform:translateY(-3px); border-color:#60a5fa; }
.card .num { font-size:2em; font-weight:800; color:#60a5fa; line-height:1.1; }
.card .lbl { color:#94a3b8; font-size:0.88em; margin-top:4px; }
.toolbar { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:18px; }
.search-box { flex:1; min-width:220px; position:relative; }
.search-box input { width:100%; background:rgba(15,23,42,0.85); border:1px solid #334155; color:#f1f5f9; padding:11px 16px; border-radius:12px; font-size:0.95em; outline:none; }
.search-box input:focus { border-color:#60a5fa; }
.filters { display:flex; gap:8px; flex-wrap:wrap; }
.fbtn { background:#1e293b; color:#cbd5e1; border:1px solid #334155; padding:8px 15px; border-radius:10px; cursor:pointer; font-size:0.85em; font-weight:600; transition:.2s; }
.fbtn.active, .fbtn:hover { background:#2563eb; border-color:#3b82f6; color:#fff; }
.cards-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:16px; }
.prj { background:rgba(15,23,42,0.85); backdrop-filter:blur(10px); border:1px solid rgba(148,163,184,0.18); border-radius:16px; padding:20px; display:flex; flex-direction:column; gap:12px; transition:transform .2s,border-color .2s; }
.prj:hover { transform:translateY(-2px); border-color:#60a5fa; }
.prj-head { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.prj-title { display:flex; align-items:center; gap:10px; }
.prj-title h3 { color:#f8fafc; font-size:1.1em; }
.badge { padding:4px 12px; border-radius:999px; font-size:0.78em; font-weight:700; display:inline-flex; align-items:center; gap:6px; }
.badge.run { background:rgba(22,163,74,0.2); color:#4ade80; border:1px solid rgba(74,222,128,0.35); }
.badge.stop { background:rgba(220,38,38,0.2); color:#f87171; border:1px solid rgba(248,113,113,0.35); }
.dot { width:8px; height:8px; border-radius:50%; background:currentColor; }
.meta { display:flex; flex-direction:column; gap:6px; font-size:0.88em; color:#cbd5e1; background:rgba(30,41,59,0.45); padding:12px; border-radius:10px; }
.meta-row { display:flex; justify-content:space-between; gap:8px; align-items:center; }
.meta-row code { color:#93c5fd; background:rgba(37,99,235,0.15); padding:2px 7px; border-radius:6px; }
.actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:auto; }
.btn { padding:8px 14px; border:none; border-radius:9px; text-decoration:none; color:#fff; font-weight:600; font-size:0.83em; cursor:pointer; display:inline-flex; align-items:center; gap:6px; transition:opacity .2s; }
.btn:hover { opacity:.86; }
.btn-s { background:#16a34a; } .btn-x { background:#dc2626; } .btn-l { background:#2563eb; } .btn-p { background:#7c3aed; }
.empty { text-align:center; color:#64748b; padding:70px 20px; grid-column:1/-1; }
</style>
</head>
<body>
<svg class="bg-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice">
  <defs>
    <radialGradient id="orb1" cx="15%" cy="15%" r="45%"><stop offset="0%" stop-color="#2563eb" stop-opacity="0.28"/><stop offset="100%" stop-color="#060a14" stop-opacity="0"/></radialGradient>
    <radialGradient id="orb2" cx="85%" cy="80%" r="45%"><stop offset="0%" stop-color="#7c3aed" stop-opacity="0.26"/><stop offset="100%" stop-color="#060a14" stop-opacity="0"/></radialGradient>
    <pattern id="grid" width="48" height="48" patternUnits="userSpaceOnUse"><path d="M 48 0 L 0 0 0 48" fill="none" stroke="#1e293b" stroke-width="0.8" opacity="0.5"/></pattern>
  </defs>
  <rect width="100%" height="100%" fill="url(#grid)"/>
  <rect width="100%" height="100%" fill="url(#orb1)"/>
  <rect width="100%" height="100%" fill="url(#orb2)"/>
  <path d="M0,780 Q360,700 720,760 T1440,720 L1440,900 L0,900 Z" fill="#0f172a" opacity="0.55"/>
</svg>

<div class="container">
  <div class="topbar">
    <div class="brand">
      <svg width="44" height="44" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="3" y="3" width="42" height="42" rx="12" fill="url(#lg1)" fill-opacity="0.18" stroke="#60a5fa" stroke-width="2"/>
        <defs><linearGradient id="lg1" x1="0" y1="0" x2="48" y2="48"><stop stop-color="#3b82f6"/><stop offset="1" stop-color="#a855f7"/></linearGradient></defs>
        <path d="M24 11L12 18L24 25L36 18L24 11Z" stroke="#60a5fa" stroke-width="2" stroke-linejoin="round"/>
        <path d="M12 24L24 31L36 24" stroke="#a78bfa" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M12 30L24 37L36 30" stroke="#34d399" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <div>
        <h1>{{ title }}</h1>
        <div style="font-size:0.8em;color:#94a3b8">لوحة الاستضافة الذكية</div>
      </div>
    </div>
    <div class="pill-row">
      <span class="pill">💳 الرصيد: <strong>{{ balance }} {{ currency }}</strong></span>
      <span class="pill">⚙️ الوضع: <strong>{{ 'مدفوع 💎' if paid_mode else 'مجاني 🆓' }}</strong></span>
      <span class="pill">🤝 الإحالات: <strong>{{ referrals }}</strong></span>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <div><div class="num">{{ total }}</div><div class="lbl">إجمالي المشاريع</div></div>
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="1.8"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
    </div>
    <div class="card">
      <div><div class="num" style="color:#4ade80">{{ running }}</div><div class="lbl">شغالة الآن</div></div>
      <svg width="48" height="48" viewBox="0 0 48 48">
        <circle cx="24" cy="24" r="19" fill="none" stroke="#1e293b" stroke-width="5"/>
        <circle cx="24" cy="24" r="19" fill="none" stroke="#22c55e" stroke-width="5" stroke-dasharray="{{ (119 * running // (total if total else 1)) }} 119" stroke-linecap="round" transform="rotate(-90 24 24)"/>
      </svg>
    </div>
    <div class="card">
      <div><div class="num" style="color:#f87171">{{ stopped }}</div><div class="lbl">متوقفة</div></div>
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="1.8"><circle cx="12" cy="12" r="10"/><rect x="9" y="9" width="6" height="6" rx="1"/></svg>
    </div>
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
  {% if projects %}
    {% for p in projects %}
    <div class="prj" data-name="{{ p[2]|lower }}" data-state="{{ 'run' if running_map[p[0]] else 'stop' }}">
      <div class="prj-head">
        <div class="prj-title">
          {% if p[3] == 'bot' %}
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="2"><rect x="3" y="10" width="18" height="10" rx="3"/><circle cx="12" cy="4" r="2"/><path d="M12 6v4"/><circle cx="9" cy="15" r="1" fill="#60a5fa"/><circle cx="15" cy="15" r="1" fill="#60a5fa"/></svg>
          {% elif p[3] == 'web' %}
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#a78bfa" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20"/></svg>
          {% else %}
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
          {% endif %}
          <h3>{{ p[2] }}</h3>
        </div>
        <span class="badge {{ 'run' if running_map[p[0]] else 'stop' }}"><span class="dot"></span>{{ 'شغال' if running_map[p[0]] else 'متوقف' }}</span>
      </div>
      <div class="meta">
        <div class="meta-row"><span>الملف الرئيسي</span><code>{{ p[5] }}</code></div>
        <div class="meta-row"><span>مدة التشغيل</span><span>{{ uptime(p[16]) if running_map[p[0]] else '—' }}</span></div>
        {% if p[12] %}<div class="meta-row"><span>الرابط</span><a href="{{ p[12] }}" target="_blank" style="color:#60a5fa;text-decoration:none">فتح ↗</a></div>{% endif %}
      </div>
      <div class="actions">
        <a href="/start/{{ p[0] }}?token={{ token }}" class="btn btn-s">▶ تشغيل</a>
        <a href="/stop/{{ p[0] }}?token={{ token }}" class="btn btn-x">⏹ إيقاف</a>
        <a href="/logs/{{ p[0] }}?token={{ token }}" class="btn btn-l">📄 السجلات</a>
        {% if p[3] == 'web' and p[12] %}<a href="{{ p[12] }}" target="_blank" class="btn btn-p">🌐 الموقع</a>{% endif %}
      </div>
    </div>
    {% endfor %}
  {% else %}
    <div class="empty">
      <svg width="84" height="84" viewBox="0 0 24 24" fill="none" stroke="#475569" stroke-width="1.4"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
      <div style="margin-top:12px">لا توجد مشاريع بعد — أنشئ مشروعك الأول من البوت</div>
    </div>
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
<html lang="ar" dir="rtl"><head><meta charset="UTF-8"><title>OK - Bot Online</title></head>
<body style="background:#060a14;color:#e2e8f0;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0">
<div style="background:#0f172a;border:1px solid #22c55e55;border-radius:16px;padding:32px 42px;text-align:center;box-shadow:0 12px 40px #0006">
<div style="font-size:2.4em;font-weight:800;color:#4ade80;letter-spacing:2px">OK</div>
<div style="margin-top:8px;color:#94a3b8;font-size:0.95em">Host Bot شغال وجاهز للمراقبة (Uptime: {up})</div>
</div></body></html>""", 200
    uid = get_dashboard_user(token)

    if not uid:
        return """
        <body style='background:#0f172a;color:#e2e8f0;text-align:center;padding:100px;font-family:sans-serif'>
        <h1>🔒 الرابط غير صالح</h1>
        <p>افتح الرابط من البوت من جديد</p>
        </body>
        """, 403

    projs = get_projects_by_owner(uid)
    running_map = {p[0]: is_running(p[0]) for p in projs}
    running = sum(1 for v in running_map.values() if v)

    u = get_user(uid)
    refs = u[11] if u and len(u) > 11 and u[11] else 0
    return render_template_string(
        WEB_HTML,
        title=get_ui("dashboard_title"),
        projects=projs, total=len(projs),
        running=running, stopped=len(projs) - running,
        running_map=running_map, uptime=calc_uptime,
        token=token,
        balance=f"{get_balance(uid):.2f}",
        currency=get_ui("currency_name") or "$",
        paid_mode=is_paid_mode(),
        referrals=refs
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

    ok, msg, _ = start_project(pid)
    return f"""<body style='background:#0f172a;color:#e2e8f0;text-align:center;padding:100px;font-family:sans-serif'>
    <h1>{msg}</h1><a href='/?token={token}' style='color:#60a5fa'>رجوع</a></body>"""


@web.route('/stop/<int:pid>')
def w_stop(pid):
    token = request.args.get("token")
    uid = get_dashboard_user(token)
    if not uid:
        return "🔒 غير مصرح", 403

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return "🚫 غير مصرح", 403

    ok, msg = stop_project(pid)
    return f"""<body style='background:#0f172a;color:#e2e8f0;text-align:center;padding:100px;font-family:sans-serif'>
    <h1>{msg}</h1><a href='/?token={token}' style='color:#60a5fa'>رجوع</a></body>"""


@web.route('/logs/<int:pid>')
def w_logs(pid):
    token = request.args.get("token")
    uid = get_dashboard_user(token)
    if not uid:
        return "🔒 غير مصرح", 403

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return "🚫 غير مصرح", 403

    logs = get_logs(pid, 200)
    return f"""<body style='background:#0f172a;color:#86efac;font-family:monospace;padding:20px'>
    <pre style='white-space:pre-wrap'>{logs}</pre>
    <a href='/?token={token}' style='color:#60a5fa'>رجوع</a></body>"""


# ============================================================
# Tunnel
# ============================================================
def start_ssh_tunnel():
    port = int(os.environ.get("PORT", 8080))
    print(f"[Tunnel/SSH] جاري التشغيل على البورت {port}...")
    try:
        proc = subprocess.Popen(
            ["ssh", "-o", "StrictHostKeyChecking=no",
             "-o", "ServerAliveInterval=30",
             "-R", f"80:localhost:{port}",
             "nokey@localhost.run"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1
        )
        start = time.time()
        for line in proc.stdout:
            if time.time() - start > 45:
                break
            m = re.search(r'https://[a-zA-Z0-9-]+\.lhr\.life', line)
            if m:
                url = m.group(0)
                print(f"[Tunnel/SSH] {url}")
                return url
        return None
    except FileNotFoundError:
        print("[Tunnel/SSH] ssh مش موجود")
        return None
    except Exception as e:
        print(f"[Tunnel/SSH] {e}")
        return None


def start_cloudflare_tunnel():
    cf_bin = "/tmp/cloudflared"
    tunnel_log = "/tmp/cloudflare_tunnel.log"
    print("[Tunnel/CF] جاري التشغيل...")
    try:
        if not os.path.exists(cf_bin):
            arch = platform.machine()
            if arch in ["aarch64", "arm64"]:
                url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
            else:
                url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
            try:
                urllib.request.urlretrieve(url, cf_bin)
                os.chmod(cf_bin, 0o755)
            except Exception as e:
                print(f"[Tunnel/CF] فشل التنزيل: {e}")
                return None

        port = int(os.environ.get("PORT", 8080))
        log_fp = open(tunnel_log, "w")
        subprocess.Popen(
            [cf_bin, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
            stdout=log_fp, stderr=subprocess.STDOUT
        )
        for _ in range(30):
            time.sleep(2)
            try:
                with open(tunnel_log, "r") as f:
                    content = f.read()
                m = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', content)
                if m:
                    url = m.group(0)
                    print(f"[Tunnel/CF] {url}")
                    return url
            except:
                pass
        return None
    except Exception as e:
        print(f"[Tunnel/CF] {e}")
        return None


def start_cloudflare_tunnel():
    cf_bin = "/tmp/cloudflared"
    tunnel_log = "/tmp/cloudflare_tunnel.log"
    print("[Tunnel/CF] جاري التشغيل...")
    try:
        if not os.path.exists(cf_bin):
            arch = platform.machine()
            if arch in ["aarch64", "arm64"]:
                url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
            else:
                url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
            try:
                urllib.request.urlretrieve(url, cf_bin)
                os.chmod(cf_bin, 0o755)
            except Exception as e:
                print(f"[Tunnel/CF] فشل التنزيل: {e}")
                return None

        port = int(os.environ.get("PORT", 8080))
        log_fp = open(tunnel_log, "w")
        subprocess.Popen(
            [cf_bin, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
            stdout=log_fp, stderr=subprocess.STDOUT
        )
        for _ in range(30):
            time.sleep(2)
            try:
                with open(tunnel_log, "r") as f:
                    content = f.read()
                m = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', content)
                if m:
                    url = m.group(0)
                    print(f"[Tunnel/CF] {url}")
                    return url
            except:
                pass
        return None
    except Exception as e:
        print(f"[Tunnel/CF] {e}")
        return None


def start_tunnel():
    global TUNNEL_URL
    print("[Tunnel] جاري التشغيل...")

    # Cloudflare الأول
    url = start_cloudflare_tunnel()
    if not url:
        url = start_ssh_tunnel()

    if url:
        TUNNEL_URL = url
        set_ui("public_url", url)
        print(f"[Tunnel] {url}")
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(
                    admin_id,
                    f"<b>🌐 الرابط العام جاهز</b>\n\n<code>{url}</code>\n\nافتحه من اي مكان",
                    parse_mode="HTML"
                )
            except:
                pass
        return url
    print("[Tunnel] فشل")
    return None


# ============================================================
# التشغيل
# ============================================================
def run_bot():
    print("[Bot] started")
    while True:
        try:
            bot.polling(non_stop=False, timeout=30)
        except Exception as e:
            print(f"[Bot] Polling error: {e}")
            time.sleep(5)


def run_web():
    port = int(os.environ.get("PORT", 8080))
    print(f"[Web] http://0.0.0.0:{port}")
    web.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)


# ============================================================
# نقطة البداية
# ============================================================
if __name__ == "__main__":
    if "--web-only" in sys.argv or os.environ.get("WEB_ONLY") == "1":
        print("=" * 55)
        print("HOST BOT v12.0 - DEMO WEB MONITORING ONLY (OK)")
        print("=" * 55)
        threading.Thread(target=keep_alive, daemon=True).start()
        run_web()
        sys.exit(0)

    print("=" * 55)
    print("HOST BOT v12.0 - STARTING")
    print("=" * 55)

    on_cloud = any([
        os.environ.get("RENDER_EXTERNAL_URL"),
        os.environ.get("KOYEB_PUBLIC_DOMAIN"),
        os.environ.get("RAILWAY_PUBLIC_DOMAIN"),
        os.environ.get("FLY_APP_NAME"),
    ])

    threading.Thread(target=keep_alive, daemon=True).start()
    print("[*] Heartbeat OK")

    threading.Thread(target=project_watchdog, daemon=True).start()
    print("[*] Watchdog OK")

    threading.Thread(target=run_bot, daemon=True).start()
    print("[*] Bot OK")

    if not on_cloud:
        def delayed_tunnel():
            time.sleep(5)
            start_tunnel()
        threading.Thread(target=delayed_tunnel, daemon=True).start()
        print("[*] Tunnel OK")
    else:
        print(f"[*] Cloud: {get_public_url()}")

    run_web()