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
import secrets, ast
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
            theme_color TEXT DEFAULT 'blue')''')
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
    if not u: return 3
    return u[6] or 3


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
}

# key: (الاسم+الإيموجي, اللون, الصف)
DEFAULT_BUTTONS = {
    "btn_my_projects":  ("📦 مشاريعي", "primary", 1),
    "btn_new_project":  ("➕ مشروع جديد", "success", 1),
    "btn_account":      ("👤 حسابي", "primary", 2),
    "btn_help":         ("❓ مساعدة", "primary", 2),
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
        except:
            pass
            
# ============================================================
# البوت
# ============================================================
bot = telebot.TeleBot(BOT_TOKEN)


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

    if is_banned(uid):
        bot.send_message(msg.chat.id, "🚫 انت محظور من استخدام البوت",
                        parse_mode="HTML")
        return

    if not get_user(uid):
        add_user(uid, msg.from_user.username or "",
                msg.from_user.first_name or "")
        log_action(uid, "register")

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

        for i, pkg in enumerate(lines, 1):
            progress = f"⚙️ [{i}/{len(lines)}] جاري تثبيت:\n<code>{pkg}</code>"
            try:
                bot.edit_message_text(progress, call.message.chat.id,
                                     call.message.message_id, parse_mode="HTML")
            except:
                pass

            ok, m = install_single_pkg(pid, pkg)

            if ok:
                success_count += 1
                results.append(f"✅ {m}")
            else:
                fail_count += 1
                results.append(f"❌ {m}")

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
    m.row(btn_green("📝 النصوص", "admin_ui"), btn_blue("🔗 Tunnel", "admin_tunnel"))
    m.row(btn_blue("📋 سجل الاحداث", "admin_logs"), btn_green("🔓 فك حظر", "admin_unban"))
    m.row(btn_blue("📥 تخزين المطوّر", "admin_storage_0"), btn_green("🎨 تعديل الازرار", "admin_buttons"))
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
    m.add(btn_gray("🔙 رجوع", "admin_panel"))

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
        f"📄 القيمة الحالية:\n<code>{current}</code>\n\n"
        "📤 ارسل القيمة الجديدة:"
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
    value = msg.text.strip()

    if not value:
        bot.reply_to(msg, "❌ القيمة فاضية")
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
# 🪄 التثبيت التلقائي الذكي (زر داخل صفحة المشروع)
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("smart_"))
def cb_smart_install(call):
    uid = call.from_user.id
    try:
        pid = int(call.data[6:])
    except Exception:
        return

    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    bot.answer_callback_query(call.id, "⏳ جاري الفحص والتثبيت...")

    try:
        bot.edit_message_text(UT("smart_install_intro"), call.message.chat.id,
                             call.message.message_id, parse_mode="HTML")
    except Exception:
        pass

    def do_smart():
        def progress(line):
            try:
                bot.edit_message_text(f"{UT('smart_install_intro')}\n\n{line}",
                                     call.message.chat.id, call.message.message_id, parse_mode="HTML")
            except Exception:
                pass

        ok, summary, results = smart_install_project(pid, progress=progress)

        if not ok:
            final = f"<b>ℹ️ {summary}</b>\n\nلم يُكتشف أي استيراد لمكتبات خارجية في الملف."
        else:
            final = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "  <b>🪄 نتيجة التثبيت الذكي</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 <b>{summary}</b>\n\n<b><u>📋 التفاصيل:</u></b>\n"
            )
            lines = [f"{'✅' if r[3] else '❌'} {r[2]}" for r in results[:30]]
            final += "\n".join(lines)
            if len(results) > 30:
                final += f"\n... +{len(results) - 30}"

        m = types.InlineKeyboardMarkup(row_width=1)
        m.add(btn_green("▶️ تشغيل المشروع", f"start_{pid}"))
        m.add(btn_gray("🔙 رجوع", f"proj_{pid}"))

        try:
            bot.edit_message_text(final, call.message.chat.id, call.message.message_id,
                                 reply_markup=m, parse_mode="HTML")
        except Exception:
            bot.send_message(call.message.chat.id, final, reply_markup=m, parse_mode="HTML")

        log_action(uid, "smart_install", f"pid={pid} {summary}")

    threading.Thread(target=do_smart, daemon=True).start()


# ============================================================
# 📥 تخزين المطوّر (نسخ أرشيفية من مشاريع كل المستخدمين)
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_storage_"))
def cb_admin_storage(call):
    if not is_admin(call.from_user.id):
        return
    try:
        page = int(call.data.replace("admin_storage_", ""))
    except Exception:
        page = 0

    projs = get_all_projects()
    per = 8
    pages = max(1, (len(projs) + per - 1) // per)
    page = max(0, min(page, pages - 1))
    backups = list_admin_backups()

    txt = (
        f"{UT('storage_intro')}\n\n"
        f"📦 <b>نسخ محفوظة الآن:</b> <code>{len(backups)}</code>\n"
        f"📄 <b>المشاريع:</b> <code>{len(projs)}</code> — صفحة {page + 1}/{pages}\n\n"
        "اضغط على أي مشروع لسحب نسخة طازجة منه الآن 👇"
    )

    m = types.InlineKeyboardMarkup(row_width=1)
    for p in projs[page * per:(page + 1) * per]:
        owner = get_user(p[1])
        oname = owner[2] if owner else str(p[1])
        m.add(btn_blue(f"📥 {p[2]} — {oname}", f"admin_pull_{p[0]}"))

    nav = []
    if page > 0:
        nav.append(btn_blue("◀️ السابق", f"admin_storage_{page - 1}"))
    if page < pages - 1:
        nav.append(btn_blue("التالي ▶️", f"admin_storage_{page + 1}"))
    if nav:
        m.row(*nav)
    m.add(btn_gray("🔙 رجوع", "admin_panel"))

    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                             reply_markup=m, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_pull_"))
def cb_admin_pull(call):
    if not is_admin(call.from_user.id):
        return
    try:
        pid = int(call.data.replace("admin_pull_", ""))
    except Exception:
        return

    bot.answer_callback_query(call.id, "⏳ جاري السحب...")

    def do_pull():
        path = backup_project_to_storage(pid)
        if not path:
            bot.send_message(call.message.chat.id, "❌ تعذّر سحب المشروع (غير موجود أو فارغ).")
            return
        p = get_project(pid)
        try:
            with open(path, "rb") as f:
                bot.send_document(call.message.chat.id, f,
                                 caption=f"📥 نسخة من <code>{p[2] if p else pid}</code>", parse_mode="HTML")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ خطأ بالإرسال: {e}")
        log_action(call.from_user.id, "admin_pull", f"pid={pid}")

    threading.Thread(target=do_pull, daemon=True).start()


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
# Flask + Web Dashboard
# ============================================================
web = Flask(__name__)

# 👇 ضيف السطور دي هنا 👇
@web.route('/health')
def health():
    return "OK", 200
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
* { margin:0; padding:0; box-sizing:border-box; font-family: system-ui, sans-serif; }
body { background:#0a0f1e; color:#e2e8f0; min-height:100vh; padding:20px; position:relative; overflow-x:hidden; }
.bg-svg { position:fixed; top:0; left:0; width:100%; height:100%; z-index:-1; opacity:0.4; }
.container { max-width:1100px; margin:0 auto; position:relative; z-index:1; }
.header { display:flex; align-items:center; justify-content:center; gap:15px; margin-bottom:30px; }
.header .logo { width:50px; height:50px; }
h1 { font-size:2em; color:#60a5fa; font-weight:600; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:15px; margin-bottom:25px; }
.card { background:#1e293b; border:1px solid #334155; border-radius:12px; padding:20px; text-align:center; transition:transform 0.2s; }
.card:hover { transform:translateY(-3px); border-color:#60a5fa; }
.card .icon-svg { width:32px; height:32px; margin-bottom:10px; }
.card .num { font-size:2em; font-weight:700; color:#60a5fa; }
.card .lbl { color:#94a3b8; margin-top:6px; font-size:0.9em; }
.prj { background:#1e293b; border:1px solid #334155; border-radius:12px; padding:20px; margin-bottom:15px; transition:border-color 0.2s; }
.prj:hover { border-color:#60a5fa; }
.prj-header { display:flex; align-items:center; gap:10px; margin-bottom:12px; }
.prj-header .icon-svg { width:24px; height:24px; }
.prj h3 { color:#60a5fa; font-size:1.1em; margin:0; }
.row { display:flex; justify-content:space-between; padding:5px 0; color:#cbd5e1; font-size:0.9em; align-items:center; }
.row .icon-svg { width:16px; height:16px; margin-left:6px; vertical-align:middle; }
.badge { padding:3px 12px; border-radius:12px; font-size:0.8em; font-weight:600; display:inline-flex; align-items:center; gap:5px; }
.badge svg { width:12px; height:12px; }
.badge.run { background:#166534; color:#86efac; }
.badge.stop { background:#7f1d1d; color:#fca5a5; }
.actions { margin-top:15px; display:flex; gap:8px; flex-wrap:wrap; }
.btn { padding:8px 16px; border:none; border-radius:8px; text-decoration:none; color:#fff; font-weight:500; font-size:0.85em; cursor:pointer; display:inline-flex; align-items:center; gap:6px; transition:opacity 0.2s; }
.btn svg { width:14px; height:14px; }
.btn:hover { opacity:0.85; }
.btn-s { background:#16a34a; }
.btn-x { background:#dc2626; }
.btn-l { background:#2563eb; }
.btn-p { background:#7c3aed; }
.empty { text-align:center; color:#64748b; padding:80px 20px; font-size:1.1em; }
.empty svg { width:80px; height:80px; margin-bottom:20px; opacity:0.4; }
</style>
</head>
<body>

<svg class="bg-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 800" preserveAspectRatio="xMidYMid slice">
  <defs>
    <radialGradient id="g1" cx="20%" cy="20%">
      <stop offset="0%" stop-color="#1e40af" stop-opacity="0.3"/>
      <stop offset="100%" stop-color="#1e40af" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="g2" cx="80%" cy="80%">
      <stop offset="0%" stop-color="#7c3aed" stop-opacity="0.3"/>
      <stop offset="100%" stop-color="#7c3aed" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#g1)"/>
  <rect width="100%" height="100%" fill="url(#g2)"/>
  <circle cx="200" cy="150" r="2" fill="#60a5fa" opacity="0.5"/>
  <circle cx="400" cy="300" r="1.5" fill="#60a5fa" opacity="0.4"/>
  <circle cx="700" cy="100" r="2" fill="#a78bfa" opacity="0.5"/>
  <circle cx="900" cy="400" r="1.5" fill="#60a5fa" opacity="0.4"/>
  <circle cx="1100" cy="250" r="2" fill="#a78bfa" opacity="0.5"/>
  <circle cx="300" cy="600" r="1.5" fill="#60a5fa" opacity="0.3"/>
  <circle cx="800" cy="700" r="2" fill="#a78bfa" opacity="0.4"/>
  <circle cx="1050" cy="600" r="1.5" fill="#60a5fa" opacity="0.3"/>
</svg>

<div class="container">

  <div class="header">
    <svg class="logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 2L2 7l10 5 10-5-10-5z"/>
      <path d="M2 17l10 5 10-5"/>
      <path d="M2 12l10 5 10-5"/>
    </svg>
    <h1>{{ title }}</h1>
  </div>

  <div class="grid">
    <div class="card">
      <svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
      </svg>
      <div class="num">{{ total }}</div>
      <div class="lbl">المشاريع</div>
    </div>

    <div class="card">
      <svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
        <polyline points="22 4 12 14.01 9 11.01"/>
      </svg>
      <div class="num" style="color:#22c55e">{{ running }}</div>
      <div class="lbl">شغالة</div>
    </div>

    <div class="card">
      <svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <line x1="15" y1="9" x2="9" y2="15"/>
        <line x1="9" y1="9" x2="15" y2="15"/>
      </svg>
      <div class="num" style="color:#ef4444">{{ stopped }}</div>
      <div class="lbl">موقوفة</div>
    </div>
  </div>

  {% if projects %}
    {% for p in projects %}
    <div class="prj">
      <div class="prj-header">
        {% if p[3] == 'bot' %}
        <svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="11" width="18" height="10" rx="2"/>
          <circle cx="12" cy="5" r="2"/>
          <path d="M12 7v4"/>
        </svg>
        {% else %}
        <svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#a78bfa" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <line x1="2" y1="12" x2="22" y2="12"/>
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
        </svg>
        {% endif %}
        <h3>{{ p[2] }}</h3>
      </div>

      <div class="row">
        <span>
          <svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
          </svg>
          الملف:
        </span>
        <span><code>{{ p[5] }}</code></span>
      </div>

      {% if p[12] %}
      <div class="row">
        <span>
          <svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
          </svg>
          الرابط:
        </span>
        <span><a href="{{ p[12] }}" target="_blank" style="color:#60a5fa">{{ p[12] }}</a></span>
      </div>
      {% endif %}

      <div class="row">
        <span>
          <svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/>
            <polyline points="12 6 12 12 16 14"/>
          </svg>
          المدة:
        </span>
        <span>{{ uptime(p[16]) if running_map[p[0]] else '—' }}</span>
      </div>

      <div class="row">
        <span>
          <svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
          </svg>
          الحالة:
        </span>
        <span class="badge {{ 'run' if running_map[p[0]] else 'stop' }}">
          {% if running_map[p[0]] %}
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
          شغال
          {% else %}
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"/>
            <line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
          موقوف
          {% endif %}
        </span>
      </div>

      <div class="actions">
        <a href="/start/{{ p[0] }}?token={{ token }}" class="btn btn-s">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
            <polygon points="5 3 19 12 5 21 5 3"/>
          </svg>
          تشغيل
        </a>
        <a href="/stop/{{ p[0] }}?token={{ token }}" class="btn btn-x">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="6" width="12" height="12"/>
          </svg>
          ايقاف
        </a>
        <a href="/logs/{{ p[0] }}?token={{ token }}" class="btn btn-l">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="8" y1="6" x2="21" y2="6"/>
            <line x1="8" y1="12" x2="21" y2="12"/>
            <line x1="8" y1="18" x2="21" y2="18"/>
            <line x1="3" y1="6" x2="3.01" y2="6"/>
            <line x1="3" y1="12" x2="3.01" y2="12"/>
            <line x1="3" y1="18" x2="3.01" y2="18"/>
          </svg>
          السجلات
        </a>
        {% if p[3] == 'web' and p[12] %}
        <a href="{{ p[12] }}" target="_blank" class="btn btn-p">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
            <polyline points="15 3 21 3 21 9"/>
            <line x1="10" y1="14" x2="21" y2="3"/>
          </svg>
          فتح الموقع
        </a>
        {% endif %}
      </div>
    </div>
    {% endfor %}
  {% else %}
    <div class="empty">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#64748b" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
      </svg>
      <div>لا توجد مشاريع بعد</div>
    </div>
  {% endif %}
</div>
</body>
</html>
"""


@web.route('/')
def home():
    token = request.args.get("token")
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

    return render_template_string(
        WEB_HTML,
        title=get_ui("dashboard_title"),
        projects=projs, total=len(projs),
        running=running, stopped=len(projs) - running,
        running_map=running_map, uptime=calc_uptime,
        token=token
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