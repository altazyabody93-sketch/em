# ============================================================
# HOST BOT v13 — ULTRA STABLE
# ------------------------------------------------------------
# - مدير عمليات مستقل لكل مشروع (قفل لكل مشروع، شجرة عمليات، إيقاف آمن)
# - مراقب واحد واضح: إعادة تشغيل تلقائية بمهلة متدرجة وحد للمحاولات
# - تشخيص أعطال حقيقي (Python / Node / مكتبات / سجلات) بدون ادعاءات
# - نسخ احتياطي موثّق (تحقق قبل الحذف) + استعادة آمنة مع تراجع
# - عزل بيئة المشاريع (لا يصلها توكن البوت ولا أسرار الاستضافة)
# - رسائل ونصوص قابلة للتخصيص ومحفوظة في قاعدة البيانات
# ============================================================

import os, sys, json, time, shutil, signal, sqlite3, subprocess
import threading, socket, re, random, zipfile, platform, urllib.request
import secrets, ast, html, io, tempfile, hashlib, traceback, stat
from types import SimpleNamespace
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor


# ============================================================
# ضمان المكتبات (يجب أن يسبق استيراد telebot و flask)
# ============================================================
def ensure_packages():
    pkgs = {"telebot": "pyTelegramBotAPI", "flask": "flask", "requests": "requests", "psutil": "psutil"}
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
                                "--no-cache-dir", "--user", "--break-system-packages", pip_name],
                               timeout=300, check=False)
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
# الإعدادات
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DATA_DIR: مجلد البيانات. على الاستضافات التي تمسح الملفات اجعله مجلداً دائماً (Volume) عبر متغير DATA_DIR
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)


def _load_local_config():
    """ملف اختياري host_config.json بجانب البرنامج: {"BOT_TOKEN": "...", "ADMIN_IDS": "123,456"}"""
    for base in (DATA_DIR, BASE_DIR):
        fp = os.path.join(base, "host_config.json")
        if os.path.isfile(fp):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception as e:
                print(f"[Config] تعذّرت قراءة {fp}: {e}")
    return {}


_CFG = _load_local_config()


# التوكن الافتراضي (كما كان في v12). متغير البيئة BOT_TOKEN أو host_config.json لهما الأولوية.
_DEFAULT_BOT_TOKEN = "9062665659:AAE2ubEi1CWrrsRM83Pp0XyKMlqlelpTCvQ"


def _resolve_bot_token():
    tok = (os.environ.get("BOT_TOKEN") or _CFG.get("BOT_TOKEN") or _DEFAULT_BOT_TOKEN or "").strip()
    if not tok and sys.stdin and sys.stdin.isatty():
        try:
            tok = input("أدخل BOT_TOKEN الخاص ببوت الاستضافة: ").strip()
        except Exception:
            tok = ""
    if not tok:
        print("=" * 60)
        print("❌ لا يوجد BOT_TOKEN.")
        print("   عيّنه كمتغير بيئة:  export BOT_TOKEN='123456:ABC...'")
        print("   أو ضعه في ملف host_config.json بجانب البرنامج:")
        print('   {"BOT_TOKEN": "123456:ABC..."}')
        print("   (لم يعد التوكن مكتوباً داخل الكود حفاظاً على الأمان)")
        print("=" * 60)
        sys.exit(1)
    if not re.match(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$", tok):
        print("[Config] ⚠️ شكل BOT_TOKEN غير معتاد، تأكد من صحته.")
    return tok


def _parse_admin_ids(raw):
    ids = []
    for x in str(raw or "").replace(";", ",").split(","):
        x = x.strip()
        if x.lstrip("-").isdigit():
            ids.append(int(x))
    return ids


BOT_TOKEN = _resolve_bot_token()
ADMIN_IDS = _parse_admin_ids(os.environ.get("ADMIN_IDS") or _CFG.get("ADMIN_IDS") or "7325566792") or [7325566792]
DEVELOPER_USERNAME = os.environ.get("DEVELOPER_USERNAME", "E_E_72")
VERSION = "13.0"
VERSION_NAME = "HOST BOT v13 — ULTRA STABLE"

PROJECTS_DIR = os.path.join(DATA_DIR, "hosted_projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)
# اسم ملف القاعدة بقي كما في v12 للحفاظ على بياناتك الحالية دون ترحيل.
DB_PATH = os.path.join(DATA_DIR, os.environ.get("DB_FILENAME", "host_v12.db"))

RUNNING = {}
user_states = {}
START_TIME = datetime.now()
PORT_COUNTER = 7000
_PORT_LOCK = threading.Lock()
TUNNEL_URL = None
DB_LOCK = threading.RLock()

BACKUPS_24H_DIR = os.path.join(DATA_DIR, "backups_24h")
os.makedirs(BACKUPS_24H_DIR, exist_ok=True)
SESSIONS_BACKUP_DIR = os.path.join(DATA_DIR, "session_backups")
os.makedirs(SESSIONS_BACKUP_DIR, exist_ok=True)
PROJECT_BACKUPS_DIR = os.path.join(DATA_DIR, "project_backups")
os.makedirs(PROJECT_BACKUPS_DIR, exist_ok=True)
TRASH_DIR = os.path.join(DATA_DIR, "restore_trash")
os.makedirs(TRASH_DIR, exist_ok=True)


def _env_num(name, default, cast=int):
    try:
        return cast(float(os.environ.get(name, default)))
    except Exception:
        return default


# ---- حدود الأمان والموارد (تُضبط من متغيرات البيئة) ----
MAX_ZIP_FILES = _env_num("MAX_ZIP_FILES", 4000)
MAX_ZIP_TOTAL_MB = _env_num("MAX_ZIP_TOTAL_MB", 300)
MAX_ZIP_FILE_MB = _env_num("MAX_ZIP_FILE_MB", 100)
MAX_ZIP_RATIO = _env_num("MAX_ZIP_RATIO", 300)
MAX_PROJECT_MB = _env_num("MAX_PROJECT_MB", 500)
LOG_MAX_MB = _env_num("PROJECT_LOG_MAX_MB", 10)
DASH_TOKEN_TTL_H = _env_num("DASH_TOKEN_TTL_H", 24)

PLANS = {
    "free": {"name": "المجانية (Free)", "emoji": "🆓", "max_projects": 3, "price": 0.0, "vip": 0},
    "pro":  {"name": "الاحترافية (Pro)", "emoji": "⚡", "max_projects": 8, "price": 5.0, "vip": 0},
    "vip":  {"name": "الفاخرة (VIP)", "emoji": "👑", "max_projects": 25, "price": 12.0, "vip": 1},
}


# ============================================================
# 🛡️ عدّة الأمان (مسارات، أسماء، ZIP، حزم pip، إخفاء الأسرار)
# ============================================================
class Raw(str):
    """نص HTML جاهز لا يُهرَّب داخل msg()."""


def esc(x):
    return html.escape(str(x if x is not None else ""), quote=False)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fmt_size(n):
    try:
        n = float(n)
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def is_inside(base, path):
    """هل path داخل base فعلاً (بعد حل الروابط الرمزية)؟"""
    try:
        b = os.path.realpath(base)
        p = os.path.realpath(path)
        return p == b or p.startswith(b + os.sep)
    except Exception:
        return False


def safe_join(base, *parts):
    """يدمج المسارات ويرفض أي خروج من base. يرمي ValueError عند المحاولة."""
    target = os.path.join(base, *[str(p) for p in parts])
    if not is_inside(base, target):
        raise ValueError("مسار خارج مجلد المشروع")
    return target


def safe_project_name(raw, limit=30):
    """اسم مشروع آمن: حروف/أرقام/_/-/. فقط، لا يبدأ بنقطة."""
    s = re.sub(r"\s+", "_", str(raw or "").strip())
    s = re.sub(r"[^A-Za-z0-9_.\-]", "", s)
    s = s.lstrip(".-_")[:limit]
    return s


def safe_filename(raw):
    """اسم ملف آمن (بدون مسارات)."""
    name = os.path.basename(str(raw or "").replace("\\", "/")).strip()
    name = re.sub(r"[\x00-\x1f]", "", name)
    if name in ("", ".", ".."):
        return ""
    return name[:150]


_TG_TOKEN_RX = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,50}\b")
_SECRET_KV_RX = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)[A-Z0-9_]*)"
    r"(\s*[=:]\s*)(['\"]?)([^\s'\"]{4,})(['\"]?)")


def mask_secrets(text):
    """يخفي التوكنات والأسرار من أي نص يُعرض للمستخدم."""
    if not isinstance(text, str) or not text:
        return text
    out = _SECRET_KV_RX.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}‹مخفي›{m.group(5)}", text)
    out = _TG_TOKEN_RX.sub("‹توكن مخفي›", out)
    if BOT_TOKEN:
        out = out.replace(BOT_TOKEN, "‹توكن مخفي›")
    return out


_PIP_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_PIP_VER = r"(?:===|==|>=|<=|~=|!=|>|<)\s*[A-Za-z0-9.*+!_-]+"
_PIP_SPEC_RX = re.compile(
    rf"^{_PIP_NAME}(?:\[[A-Za-z0-9,._-]+\])?\s*(?:{_PIP_VER}(?:\s*,\s*{_PIP_VER})*)?"
    rf"(?:\s*;\s*[A-Za-z0-9_.\"'<>=!~ ]+)?$")


def valid_pip_spec(spec):
    """يسمح باسم حزمة (مع إصدار/extras) فقط — يمنع خيارات pip والروابط والمسارات."""
    s = (spec or "").strip()
    if not s or len(s) > 150 or s.startswith("-"):
        return False
    return bool(_PIP_SPEC_RX.match(s))


def sanitize_requirements(text):
    """يرجع (أسطر صالحة, أسطر مرفوضة) من محتوى requirements.txt."""
    good, bad = [], []
    for raw in (text or "").splitlines():
        line = raw.split(" #")[0].strip()
        if not line or line.startswith("#"):
            continue
        (good if valid_pip_spec(line) else bad).append(line)
    return good, bad


def dir_size(path, limit=None):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except Exception:
                pass
            if limit and total > limit:
                return total
    return total


def atomic_write_text(path, content, encoding="utf-8"):
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding=encoding) as f:
        f.write(content)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


def safe_extract_zip(zf, dest, max_files=None, max_total_mb=None, max_file_mb=None, max_ratio=None,
                     skip_top=("__MACOSX",)):
    """
    فك ضغط آمن: يرفض المسارات المطلقة و .. والروابط الرمزية، ويفرض حدوداً على
    عدد الملفات والحجم الكلي (بالقياس الفعلي وليس المعلن فقط) ونسبة الضغط.
    يرمي ValueError برسالة مفهومة. يرجع قائمة الملفات المفكوكة (نسبية).
    """
    max_files = max_files or MAX_ZIP_FILES
    max_total = (max_total_mb or MAX_ZIP_TOTAL_MB) * 1024 * 1024
    max_file = (max_file_mb or MAX_ZIP_FILE_MB) * 1024 * 1024
    max_ratio = max_ratio or MAX_ZIP_RATIO
    os.makedirs(dest, exist_ok=True)
    dest_real = os.path.realpath(dest)
    infos = zf.infolist()
    if len(infos) > max_files:
        raise ValueError(f"عدد ملفات الأرشيف ({len(infos)}) يتجاوز الحد ({max_files})")
    declared = 0
    plan = []
    for zi in infos:
        name = zi.filename.replace("\\", "/")
        if not name or "\x00" in name:
            raise ValueError("اسم ملف غير صالح داخل الأرشيف")
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            raise ValueError(f"مسار مطلق غير مسموح: {name[:60]}")
        parts = [p for p in name.split("/") if p not in ("", ".")]
        if ".." in parts:
            raise ValueError(f"مسار خطير (..) غير مسموح: {name[:60]}")
        if parts and parts[0] in skip_top:
            continue
        if any(len(p) > 200 for p in parts):
            raise ValueError("اسم ملف طويل جداً داخل الأرشيف")
        mode = (zi.external_attr >> 16) & 0xFFFF
        if mode and stat.S_ISLNK(mode):
            raise ValueError(f"روابط رمزية غير مسموحة: {name[:60]}")
        if mode and (stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
            raise ValueError(f"ملف خاص غير مسموح: {name[:60]}")
        if not parts:
            continue
        is_dir = name.endswith("/")
        if not is_dir:
            if zi.file_size > max_file:
                raise ValueError(f"ملف كبير جداً داخل الأرشيف: {parts[-1][:40]}")
            declared += zi.file_size
            if declared > max_total:
                raise ValueError(f"الحجم بعد فك الضغط يتجاوز {max_total // 1048576}MB")
            if zi.compress_size and zi.file_size / max(zi.compress_size, 1) > max_ratio and zi.file_size > 1024 * 1024:
                raise ValueError("نسبة ضغط مشبوهة (قنبلة ضغط محتملة)")
        plan.append((zi, parts, is_dir))
    extracted, written_total = [], 0
    for zi, parts, is_dir in plan:
        target = os.path.join(dest_real, *parts)
        if not is_inside(dest_real, os.path.dirname(target) if not is_dir else target):
            raise ValueError("محاولة الخروج من مجلد الوجهة")
        if is_dir:
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.islink(target):
            raise ValueError("الهدف رابط رمزي")
        with zf.open(zi, "r") as src, open(target, "wb") as out:
            copied = 0
            while True:
                chunk = src.read(1024 * 256)
                if not chunk:
                    break
                copied += len(chunk)
                written_total += len(chunk)
                if copied > max_file or written_total > max_total:
                    out.close()
                    try:
                        os.remove(target)
                    except Exception:
                        pass
                    raise ValueError("تجاوز حد الحجم أثناء فك الضغط")
                out.write(chunk)
        extracted.append("/".join(parts))
    return extracted


def flatten_single_top_dir(pdir, ignore=("__MACOSX",)):
    """إذا كان الأرشيف يحوي مجلداً واحداً فقط (مثل GitHub ZIP) ارفع محتواه للأعلى."""
    items = [x for x in os.listdir(pdir) if x not in ignore]
    if len(items) == 1 and os.path.isdir(os.path.join(pdir, items[0])) and not os.path.islink(os.path.join(pdir, items[0])):
        inner = os.path.join(pdir, items[0])
        for sub in os.listdir(inner):
            shutil.move(os.path.join(inner, sub), os.path.join(pdir, sub))
        shutil.rmtree(inner, ignore_errors=True)
        return True
    return False


# ---- مجمّع خيوط للأعمال الثقيلة (تثبيت/نسخ/فحص) كي لا تُجمَّد معالجات تيليجرام ----
BG_POOL = ThreadPoolExecutor(max_workers=_env_num("BG_WORKERS", 8), thread_name_prefix="hb-bg")


def run_bg(fn, *args, **kwargs):
    """يشغّل fn في الخلفية ويلتقط أي استثناء حتى لا يموت شيء بصمت."""
    def _wrap():
        try:
            fn(*args, **kwargs)
        except Exception as e:
            print(f"[BG] {getattr(fn, '__name__', fn)}: {e}\n{traceback.format_exc()[-600:]}")
    try:
        return BG_POOL.submit(_wrap)
    except Exception as e:
        print(f"[BG] submit failed: {e}")
        return None




NODE_BUILTINS = {
    "fs", "path", "os", "http", "https", "url", "util", "crypto", "stream",
    "events", "child_process", "cluster", "dns", "net", "tls", "dgram",
    "readline", "zlib", "buffer", "assert", "querystring", "timers", "tty",
    "process", "worker_threads", "perf_hooks", "async_hooks", "vm",
}

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
                    env_vars[k.strip()] = v.strip().strip('"').strip("'")
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
            if main_f and os.path.isfile(os.path.join(pdir, main_f)):
                return "node", main_f
            sc = (pkg.get("scripts") or {}).get("start", "")
            m = re.search(r"node\s+([^\s&|;]+)", sc)
            if m and os.path.isfile(os.path.join(pdir, m.group(1))):
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
        "name": re.sub(r"[^a-z0-9-]", "-", os.path.basename(pdir).lower()),
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
        r = req.get(f"https://api.telegram.org/bot{found}/getMe", timeout=8)
        if r.status_code == 200 and r.json().get("ok"):
            res = r.json()["result"]
            info["username"] = res.get("username")
            info["name"] = res.get("first_name")
    except Exception:
        pass
    return info

def get_server_metrics():
    if not psutil:
        return {"cpu": 0.0, "ram_pct": 0.0, "ram_used": 0.0, "ram_total": 0.0, "disk_pct": 0.0, "disk_used": 0.0, "disk_total": 0.0}
    vm = psutil.virtual_memory()
    dk = psutil.disk_usage("/")
    return {
        "cpu": round(psutil.cpu_percent(interval=0.1), 1),
        "ram_pct": round(vm.percent, 1),
        "ram_used": round(vm.used / (1024**3), 2),
        "ram_total": round(vm.total / (1024**3), 2),
        "disk_pct": round(dk.percent, 1),
        "disk_used": round(dk.used / (1024**3), 2),
        "disk_total": round(dk.total / (1024**3), 2),
    }

def get_proc_metrics(pid_num):
    if not psutil or not pid_num:
        return 0.0, 0.0
    try:
        ps = psutil.Process(int(pid_num))
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
            approved INTEGER DEFAULT 0,
            FOREIGN KEY (owner_id) REFERENCES users(user_id) ON DELETE CASCADE)''')
        try:
            c.execute("ALTER TABLE projects ADD COLUMN approved INTEGER DEFAULT 1")
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
        c.execute('''CREATE TABLE IF NOT EXISTS project_state (pid INTEGER PRIMARY KEY, data TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS project_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, ts TEXT, kind TEXT, detail TEXT)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_events_pid ON project_events (pid, id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects (owner_id)')
        conn.commit()
        conn.close()

init_db()


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass
    return conn


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
        try:
            c.execute("INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)", (owner_id, now_str()))
        except Exception:
            pass
        c.execute("""INSERT INTO projects (owner_id, name, ptype, path, entry, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""", (owner_id, name, ptype, path, entry, now_str()))
        pid = c.lastrowid
        conn.commit(); conn.close()
        return pid

def update_project(project_id, **kw):
    """تحديث ذرّي بمعاملة واحدة، وبأسماء أعمدة مسموحة فقط."""
    kw = {k: v for k, v in kw.items() if k in _PROJECT_COLS}
    if not kw:
        return
    with DB_LOCK:
        conn = _conn()
        try:
            sets = ", ".join(f"{k}=?" for k in kw)
            conn.execute(f"UPDATE projects SET {sets} WHERE id=?", (*kw.values(), project_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

def delete_project(pid):
    with DB_LOCK:
        conn = _conn()
        try:
            conn.execute("DELETE FROM projects WHERE id=?", (pid,))
            conn.commit()
        finally:
            conn.close()
    purge_project_records(pid)


# ============================================================
# 🧬 حالة تشغيل المشاريع (مراقبة + إعادة تشغيل) وسجل الأحداث — v13
# ============================================================
_PSTATE_COLS = (
    "auto_restart", "max_restarts", "restarts", "total_restarts", "crash_count",
    "last_exit_code", "last_exit_reason", "last_crash_at", "next_retry_at",
    "last_attempt", "last_attempt_ok", "install_status", "install_detail",
    "desired", "exhausted", "last_alert_at", "last_backup_at",
)


def _global_auto_restart_default():
    return get_ui("auto_restart_default") != "0"


def _global_max_restarts():
    try:
        return max(1, min(50, int(float(get_ui("auto_restart_max") or 5))))
    except Exception:
        return 5


def pstate_get(pid):
    """حالة المراقبة لمشروع (مع قيم افتراضية آمنة). لا ترمي استثناءات."""
    st = {
        "auto_restart": None, "max_restarts": None, "restarts": [], "total_restarts": 0,
        "crash_count": 0, "last_exit_code": None, "last_exit_reason": "", "last_crash_at": "",
        "next_retry_at": 0.0, "last_attempt": "", "last_attempt_ok": None,
        "install_status": "none", "install_detail": "", "desired": None, "exhausted": False,
        "last_alert_at": 0.0, "last_backup_at": "",
    }
    try:
        with DB_LOCK:
            conn = _conn(); c = conn.cursor()
            c.execute("SELECT data FROM project_state WHERE pid=?", (int(pid),))
            r = c.fetchone()
            conn.close()
        if r and r[0]:
            st.update({k: v for k, v in json.loads(r[0]).items() if k in st})
    except Exception as e:
        print(f"[PState] قراءة {pid}: {e}")
    if st["auto_restart"] is None:
        st["auto_restart"] = _global_auto_restart_default()
    if st["max_restarts"] is None:
        st["max_restarts"] = _global_max_restarts()
    if st["desired"] is None:
        p = get_project(pid)
        st["desired"] = "running" if (p and p[15] == "running") else "stopped"
    return st


def pstate_set(pid, **kw):
    kw = {k: v for k, v in kw.items() if k in _PSTATE_COLS}
    if not kw:
        return
    try:
        with DB_LOCK:
            conn = _conn(); c = conn.cursor()
            c.execute("SELECT data FROM project_state WHERE pid=?", (int(pid),))
            r = c.fetchone()
            cur = {}
            if r and r[0]:
                try:
                    cur = json.loads(r[0])
                except Exception:
                    cur = {}
            cur.update(kw)
            c.execute("REPLACE INTO project_state (pid, data) VALUES (?, ?)", (int(pid), json.dumps(cur, ensure_ascii=False)))
            conn.commit(); conn.close()
    except Exception as e:
        print(f"[PState] كتابة {pid}: {e}")


def pstate_reset_counters(pid):
    pstate_set(pid, restarts=[], exhausted=False, next_retry_at=0.0)


def add_event(pid, kind, detail=""):
    """يسجّل حدثاً لمشروع (تشغيل/إيقاف/انهيار/إعادة تشغيل/نسخ...) مع تقليم تلقائي."""
    try:
        with DB_LOCK:
            conn = _conn(); c = conn.cursor()
            c.execute("INSERT INTO project_events (pid, ts, kind, detail) VALUES (?, ?, ?, ?)",
                      (int(pid), now_str(), str(kind)[:40], mask_secrets(str(detail))[:400]))
            c.execute("DELETE FROM project_events WHERE pid=? AND id NOT IN "
                      "(SELECT id FROM project_events WHERE pid=? ORDER BY id DESC LIMIT 200)", (int(pid), int(pid)))
            conn.commit(); conn.close()
    except Exception as e:
        print(f"[Events] {e}")


def get_events(pid, limit=15, kinds=None):
    try:
        with DB_LOCK:
            conn = _conn(); c = conn.cursor()
            if kinds:
                q = ",".join("?" * len(kinds))
                c.execute(f"SELECT ts, kind, detail FROM project_events WHERE pid=? AND kind IN ({q}) "
                          "ORDER BY id DESC LIMIT ?", (int(pid), *kinds, int(limit)))
            else:
                c.execute("SELECT ts, kind, detail FROM project_events WHERE pid=? ORDER BY id DESC LIMIT ?",
                          (int(pid), int(limit)))
            r = c.fetchall(); conn.close()
        return r
    except Exception:
        return []


def purge_project_records(pid):
    try:
        with DB_LOCK:
            conn = _conn(); c = conn.cursor()
            c.execute("DELETE FROM project_state WHERE pid=?", (int(pid),))
            c.execute("DELETE FROM project_events WHERE pid=?", (int(pid),))
            conn.commit(); conn.close()
    except Exception as e:
        print(f"[Purge] {e}")
    try:
        shutil.rmtree(os.path.join(PROJECT_BACKUPS_DIR, str(int(pid))), ignore_errors=True)
    except Exception:
        pass


def count_user_projects(uid):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM projects WHERE owner_id=?", (uid,))
        r = c.fetchone()[0]; conn.close(); return r


def is_trusted_user(uid):
    if is_admin(uid):
        return True
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT 1 FROM trusted_users WHERE user_id=?", (uid,))
        r = c.fetchone(); conn.close()
        return bool(r)


def add_trusted_user(uid, username="", first_name="", added_by=0):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute(
            "REPLACE INTO trusted_users (user_id, username, first_name, added_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (int(uid), username or "", first_name or "", int(added_by), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit(); conn.close()


def remove_trusted_user(uid):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("DELETE FROM trusted_users WHERE user_id=?", (int(uid),))
        changed = c.rowcount
        conn.commit(); conn.close()
        return changed > 0


def get_all_trusted_users():
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT user_id, username, first_name, added_by, created_at FROM trusted_users ORDER BY created_at DESC")
        r = c.fetchall(); conn.close(); return r


def find_user_by_username(uname):
    uname = (uname or "").strip().lstrip("@").lower()
    if not uname:
        return None
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT user_id, username, first_name FROM users WHERE LOWER(username)=?", (uname,))
        r = c.fetchone(); conn.close(); return r


def get_project_approved(pid):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT COALESCE(approved, 1) FROM projects WHERE id=?", (pid,))
        r = c.fetchone(); conn.close()
        return int(r[0]) if r else 0


def set_project_approved(pid, val):
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("UPDATE projects SET approved=? WHERE id=?", (int(val), pid))
        conn.commit(); conn.close()


def notify_admins_project_approval(pid, owner_uid, tg_user, file_path, extra_filename=None):
    p = get_project(pid)
    if not p:
        return
    uname = f"@{tg_user.username}" if getattr(tg_user, "username", None) else "لا يوجد"
    fname = html.escape(getattr(tg_user, "first_name", None) or "مستخدم")
    title = "📎 ملف إضافي لمشروع" if extra_filename else "📥 طلب تشغيل ملف جديد"
    shown_file = extra_filename or p[5]
    cap = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  <b>{title}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>المرسل:</b> {fname} ({uname})\n"
        f"🆔 <b>الآيدي:</b> <code>{owner_uid}</code>\n"
        f"📦 <b>المشروع:</b> <code>{html.escape(p[2])}</code> (ID: <code>{pid}</code>)\n"
        f"🏷️ <b>النوع:</b> {type_label(p[3])}\n"
        f"📄 <b>الملف:</b> <code>{html.escape(shown_file)}</code>\n"
        f"📊 <b>الحجم:</b> <code>{get_file_size_str(file_path)}</code>"
    )
    am = types.InlineKeyboardMarkup(row_width=2)
    am.row(
        btn_green("✅ قبول التشغيل", f"adm_proj_ok_{pid}"),
        btn_red("🛑 إيقاف / رفض", f"adm_proj_no_{pid}"),
    )
    send_path = file_path
    tmp_zip = None
    try:
        all_files = [f for f in list_dir_files(p[4]) if not f.startswith(VENV_DIRNAME + os.sep) and f != ".deps_installed"]
        if not extra_filename and len(all_files) > 1 and os.path.isdir(p[4]):
            fd, tmp_zip = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            zip_project_dir(p[4], tmp_zip, exclude_dirnames=tuple(_BK_EXCL_DIRS))
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


# ============================================================
# إعدادات الواجهة + الأزرار (بإيموجي معبّر)
# ============================================================
DEFAULT_UI = {
    "welcome_text": "اهلا بك في Host Bot v13",
    "dashboard_title": "HOST BOT v13",
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
    with _PORT_LOCK:
        while PORT_COUNTER < 65000:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(("0.0.0.0", PORT_COUNTER))
                s.close()
                p = PORT_COUNTER
                PORT_COUNTER += 1
                return p
            except Exception:
                PORT_COUNTER += 1
            finally:
                try:
                    s.close()
                except Exception:
                    pass
        PORT_COUNTER = 7000
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



def check_js_file(fp):
    try:
        node_bin = shutil.which("node")
        if node_bin:
            r = subprocess.run([node_bin, "--check", fp], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return False, f"<b>❌ خطأ JavaScript</b>\n<pre>{html.escape((r.stderr or r.stdout)[-400:])}</pre>"
        return True, f"<b>✅ JavaScript سليم</b>\n📄 <code>{os.path.basename(fp)}</code> ({get_file_size_str(fp)})"
    except Exception as e:
        return True, f"ℹ️ JS: {e}"

def check_any_file(fp):
    if not os.path.exists(fp): return False, "<b>❌ الملف غير موجود</b>"
    name = os.path.basename(fp).lower()
    if name == "requirements.txt": return check_requirements_file(fp)
    elif name.endswith(".py"): return check_python_file(fp)
    elif name.endswith(".json"): return check_json_file(fp)
    elif name.endswith(".sh"): return check_shell_file(fp)
    elif name.endswith((".js", ".mjs", ".cjs")): return check_js_file(fp)
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
    """رمز لوحة الويب: يُعاد استخدامه أثناء نصف عمره ثم يُجدَّد؛ صلاحيته DASH_TOKEN_TTL_H ساعة."""
    ttl = DASH_TOKEN_TTL_H * 3600
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("SELECT token, created_at FROM dashboard_tokens WHERE user_id=?", (uid,))
        r = c.fetchone()
        if r:
            age = _age_seconds(r[1])
            if age is not None and age < ttl / 2:
                conn.close()
                return r[0]
        token = secrets.token_urlsafe(32)
        c.execute("DELETE FROM dashboard_tokens WHERE user_id=?", (uid,))
        c.execute("INSERT INTO dashboard_tokens (token, user_id, created_at) VALUES (?, ?, ?)", (token, uid, now_str()))
        conn.commit(); conn.close()
    return token

def get_dashboard_user(token):
    if not token or len(token) > 200:
        return None
    try:
        with DB_LOCK:
            conn = _conn(); c = conn.cursor()
            c.execute("SELECT user_id, created_at FROM dashboard_tokens WHERE token=?", (token,))
            r = c.fetchone()
            if not r:
                conn.close()
                return None
            age = _age_seconds(r[1])
            if age is None or age > DASH_TOKEN_TTL_H * 3600:
                c.execute("DELETE FROM dashboard_tokens WHERE token=?", (token,))
                conn.commit(); conn.close()
                return None
            conn.close()
            return r[0]
    except Exception:
        return None

def get_dashboard_link(uid):
    token = create_dashboard_token(uid)
    base = get_public_url()
    return f"{base}/?token={token}"
    
# ============================================================
# تشغيل / إيقاف المشاريع — مدير العمليات (Supervisor) v13
# ============================================================
# المبادئ:
#   1) قفل مستقل لكل مشروع: لا تشغيل مزدوج ولا إيقاف أثناء التشغيل.
#   2) كل مشروع في جلسة/مجموعة عمليات خاصة: إيقافه يقتل شجرته فقط ولا يمسّ البوت الرئيسي.
#   3) بيئة مشروع نظيفة: لا يصله BOT_TOKEN ولا أسرار الاستضافة.
#   4) مراقب واحد (Supervisor) يقرر إعادة التشغيل بسياسة واضحة: مهلة متدرجة + حد للمحاولات.
#   5) أي خطأ داخل مشروع يُسجَّل ويُعزل — لا يصل أبداً لحلقة البوت الرئيسية.
# ============================================================
START_WATCH_SECONDS = _env_num("START_WATCH_SECONDS", 20)
MONITOR_INTERVAL = max(2, _env_num("MONITOR_INTERVAL", 5))
RESTART_BACKOFF = [5, 15, 45, 120, 300]          # ثوانٍ بين محاولات إعادة التشغيل (تدريجية)
RESTART_WINDOW_SEC = _env_num("RESTART_WINDOW_SEC", 1800)   # نافذة احتساب المحاولات
STABLE_SECONDS = _env_num("STABLE_SECONDS", 600)            # بعدها يُعتبر المشروع مستقراً وتُصفَّر المحاولات
MAX_RAM_MB = _env_num("PROJECT_MAX_RAM_MB", 512)
MAX_CPU_PCT = _env_num("PROJECT_MAX_CPU_PCT", 95)
CPU_STRIKES = _env_num("PROJECT_CPU_STRIKES", 6)
MAX_FILE_MB = _env_num("PROJECT_MAX_FILE_MB", 200)
STRICT_AS_MB = _env_num("PROJECT_STRICT_AS_MB", 0)
HOST_RAM_DANGER = _env_num("HOST_RAM_DANGER_PCT", 93)
MAX_AUTO_RESTARTS = 5            # يُبقى للتوافق؛ القيمة الفعلية لكل مشروع في pstate
CRASH_WINDOW_SEC = RESTART_WINDOW_SEC

_START_PROGRESS = {}
_START_TS = {}
_PLOCKS = {}
_PLOCKS_G = threading.Lock()
_RESTARTING = set()
_RESTARTING_G = threading.Lock()
_MONITOR = {"alive": False, "last_tick": 0.0, "ticks": 0, "errors": 0, "last_error": "", "boot_done": False}

_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)

try:
    import resource as _resource
except Exception:                      # ويندوز
    _resource = None


def _plock(pid):
    with _PLOCKS_G:
        lk = _PLOCKS.get(pid)
        if lk is None:
            lk = _PLOCKS[pid] = threading.Lock()
        return lk


def _sp(pid, **kw):
    d = _START_PROGRESS.setdefault(pid, {"notes": [], "phase": "check", "t0": time.time()})
    note = kw.pop("note", None)
    if note:
        d["notes"].append(note)
    d.update(kw)


# ------------------------------------------------------------
# قراءة السجل بكفاءة + تدوير + تصنيف الأعطال
# ------------------------------------------------------------
def read_tail(path, max_bytes=65536):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            data = f.read()
        return data.decode("utf-8", "replace")
    except Exception:
        return ""


def rotate_log(path, max_mb=None, keep_kb=512):
    """يقصّ السجل إذا تضخّم (آمن مع عملية تكتب بوضع الإلحاق)."""
    max_bytes = (max_mb or LOG_MAX_MB) * 1024 * 1024
    try:
        if os.path.getsize(path) <= max_bytes:
            return False
        tail = read_tail(path, keep_kb * 1024)
        os.truncate(path, 0)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[… تم اقتطاع السجل القديم تلقائياً {now_str()} …]\n{tail}")
        return True
    except Exception:
        return False


_MISSING_RX = re.compile(r"No module named ['\"]([\w\.\-]+)['\"]")
_ERR_HINTS = [
    ("SyntaxError", "خطأ كتابة في الكود. راجع السطر المذكور أعلاه."),
    ("Unauthorized", "التوكن غير صحيح أو ملغي. أنشئ توكن جديد من @BotFather."),
    ("401", "التوكن غير صحيح أو ملغي. أنشئ توكن جديد من @BotFather."),
    ("terminated by other getUpdates", "نفس التوكن شغال في مكان آخر. أوقف النسخة الثانية أو غيّر التوكن."),
    ("409", "نفس التوكن شغال في مكان آخر. أوقف النسخة الثانية أو غيّر التوكن."),
    ("Address already in use", "المنفذ مستخدم. لا تثبّت المنفذ يدوياً واستخدم متغير PORT."),
    ("PermissionError", "لا توجد صلاحية لهذا الملف أو المجلد."),
    ("FileNotFoundError", "ملف مطلوب غير موجود. تأكد أنك رفعت كل ملفات المشروع."),
    ("KeyError", "متغير بيئة أو مفتاح ناقص. افتح محرر المتغيرات (ENV) وأضفه."),
]

# (نمط، وصف السبب، الحل المقترح) — بالترتيب من الأخص إلى الأعم
_FAIL_RULES = [
    (r"ModuleNotFoundError: No module named ['\"]([\w\.\-]+)", "مكتبة ناقصة: {0}",
     "ثبّتها من زر «⚙️ مكتبة» أو استخدم «🪄 تثبيت تلقائي ذكي»."),
    (r"ImportError: cannot import name ['\"]?(\w+)", "استيراد غير صحيح: {0}",
     "غالباً إصدار المكتبة لا يطابق الكود. ثبّت الإصدار المناسب في requirements.txt."),
    (r"(SyntaxError|IndentationError|TabError)", "خطأ كتابة في الكود ({0})",
     "راجع السطر المذكور في السجل وصحّحه ثم ارفع الملف من جديد."),
    (r"(terminated by other getUpdates|Conflict: terminated|error_code.{0,6}409)", "نفس التوكن يعمل في مكان آخر",
     "أوقف النسخة الأخرى من البوت أو استخدم توكناً مختلفاً."),
    (r"(Unauthorized|InvalidToken|error_code.{0,6}401|Token is invalid|token.*invalid)", "التوكن غير صحيح أو ملغي",
     "أنشئ توكناً جديداً من @BotFather وحدّثه في ملفاتك أو في محرر .env."),
    (r"(Address already in use|EADDRINUSE|Errno 98)", "المنفذ مستخدم",
     "لا تثبّت المنفذ يدوياً داخل الكود؛ استخدم متغير البيئة PORT."),
    (r"KeyError: ['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", "متغير/مفتاح ناقص: {0}",
     "إن كان متغير بيئة فأضفه من «🔐 محرر .env»."),
    (r"(PermissionError|EACCES)", "لا توجد صلاحية للملف أو المجلد", "تأكد أن المشروع يكتب فقط داخل مجلده."),
    (r"(FileNotFoundError|ENOENT|Cannot find module)", "ملف أو وحدة مطلوبة غير موجودة",
     "تأكد أنك رفعت كل ملفات المشروع (وأن مسارات الملفات صحيحة)."),
    (r"(MemoryError|Cannot allocate memory|JavaScript heap out of memory)", "نفدت الذاكرة",
     "قلّل استهلاك الذاكرة أو اطلب باقة أعلى."),
    (r"(File size limit exceeded|SIGXFSZ)", "تجاوز الحد الأقصى لحجم ملف يكتبه المشروع", "لا تكتب ملفات ضخمة داخل المشروع."),
    (r"database is locked", "قاعدة بيانات SQLite مقفلة", "لا تفتح نفس ملف قاعدة البيانات من أكثر من عملية."),
    (r"(Name or service not known|Temporary failure in name resolution|getaddrinfo failed|ConnectionError|ECONNREFUSED)",
     "مشكلة اتصال بالشبكة أو بخدمة خارجية", "تحقق من الروابط/المفاتيح المستخدمة ومن توفر الإنترنت."),
]


def classify_failure(tail, rc=None):
    """يرجع (سبب مختصر، حل مقترح) من آخر السجل ورمز الخروج. لا يدّعي ما لا يعرفه."""
    tail = tail or ""
    for pat, reason, fix in _FAIL_RULES:
        m = re.search(pat, tail)
        if m:
            try:
                return reason.format(*[g for g in m.groups()] or [""]), fix
            except Exception:
                return reason, fix
    if rc in (-9, 137):
        return "قُتلت العملية بإشارة KILL (غالباً نفاد الذاكرة أو إيقاف خارجي)", "راجع استهلاك الذاكرة وحدود الباقة."
    if rc in (-15, 143):
        return "أُوقفت العملية بإشارة إيقاف (SIGTERM)", ""
    if rc == -11:
        return "انهيار داخلي في مكتبة (Segmentation fault)", "جرّب إصداراً مختلفاً من المكتبات الثقيلة."
    if rc == 127:
        return "الأمر المطلوب للتشغيل غير موجود على السيرفر", ""
    last = last_error_line(tail)
    if last:
        return last[:160], ""
    if rc not in (None, 0):
        return f"خرجت العملية برمز {rc} دون رسالة خطأ واضحة في السجل", "افتح السجل الكامل وراجع آخر الأسطر."
    return "", ""


def last_error_line(tail):
    """آخر سطر يشبه استثناءً (Error/Exception) في السجل، مخفي الأسرار."""
    for line in reversed((tail or "").splitlines()):
        s = line.strip()
        if re.match(r"^[\w\.]*(Error|Exception|Exit|Interrupt)\b.*", s) or "Traceback" in s or s.startswith("Error:"):
            return mask_secrets(s)
    return ""


def _log_tail(p, n=40):
    """آخر n سطر من التشغيل الحالي (بعد آخر علامة START)."""
    try:
        txt = read_tail(os.path.join(p[4], "output.log"), 131072)
        txt = txt.split("===== START")[-1]
        return "\n".join(txt.splitlines()[-n:])
    except Exception:
        return ""


def _pretty_fail(msg, tail):
    hint = next((h for k, h in _ERR_HINTS if k in (tail or "")), "")
    out = msg
    if hint:
        out += f"\n\n💡 <b>الحل المقترح:</b> {hint}"
    return out


# ------------------------------------------------------------
# فحص ما قبل التشغيل + بيئة نظيفة + عزل
# ------------------------------------------------------------
_RUNNABLE_EXT = (".py", ".js", ".mjs", ".cjs", ".sh")


def _precheck_entry(entry_path):
    """(ok, رسالة HTML) — فحص سريع للصيغة قبل التشغيل."""
    name = entry_path.lower()
    if not name.endswith(_RUNNABLE_EXT):
        return False, "الملف الرئيسي ليس ملفاً قابلاً للتشغيل (.py / .js / .sh). غيّر الملف الرئيسي أو ارفع الملف الصحيح."
    if name.endswith(".py"):
        ok, report = check_python_file(entry_path)
        if not ok:
            return False, f"<b>فشل الفحص:</b>\n\n{report[:800]}"
    elif name.endswith((".js", ".mjs", ".cjs")):
        ok, report = check_js_file(entry_path)
        if not ok:
            return False, f"<b>فشل الفحص:</b>\n\n{report[:800]}"
    return True, ""


_ENV_ALLOW = {
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP",
    "SYSTEMROOT", "COMSPEC", "PATHEXT", "SHELL", "TERM", "PYTHONIOENCODING", "PYTHONUTF8", "LD_LIBRARY_PATH",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "NODE_OPTIONS", "NPM_CONFIG_CACHE", "XDG_CACHE_HOME",
}
_ENV_BLOCK = {"BOT_TOKEN", "ADMIN_IDS", "DATA_DIR", "DB_FILENAME", "DEVELOPER_USERNAME"}


def build_child_env(p, port=0):
    """بيئة نظيفة للمشروع: لا أسرار للاستضافة. أضف متغيرات مسموحة عبر HOST_PASS_ENV=NAME1,NAME2."""
    extra = {x.strip() for x in os.environ.get("HOST_PASS_ENV", "").split(",") if x.strip()}
    env = {k: v for k, v in os.environ.items() if (k in _ENV_ALLOW or k in extra) and k not in _ENV_BLOCK}
    pdir = p[4]
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = os.path.join(pdir, PYLIBS_DIRNAME)
    env["NODE_PATH"] = os.path.join(pdir, "node_modules")
    vbin = os.path.join(pdir, VENV_DIRNAME, "Scripts" if os.name == "nt" else "bin")
    if os.path.isdir(vbin):
        env["VIRTUAL_ENV"] = os.path.join(pdir, VENV_DIRNAME)
        env["PATH"] = vbin + os.pathsep + env.get("PATH", "")
    env.update(load_project_env_dict(pdir))          # متغيرات المشروع نفسه (.env)
    if p[3] == "web" and port:
        env["PORT"] = str(port)
        env["FLASK_RUN_PORT"] = str(port)
        env["HOST"] = "0.0.0.0"
    return env


_LAUNCHER = (
    "import os, sys\n"
    "try:\n"
    "    import resource\n"
    "    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
    "    fs = int(os.environ.pop('_HB_FSIZE', '0') or 0)\n"
    "    if fs > 0: resource.setrlimit(resource.RLIMIT_FSIZE, (fs, fs))\n"
    "    asz = int(os.environ.pop('_HB_AS', '0') or 0)\n"
    "    if asz > 0: resource.setrlimit(resource.RLIMIT_AS, (asz, asz))\n"
    "except Exception:\n"
    "    pass\n"
    "os.execvp(sys.argv[1], sys.argv[1:])\n"
)


def _popen_isolated(cmd, cwd, log_fh, env):
    """تشغيل في جلسة مستقلة مع حدود نظام (بدون preexec_fn غير الآمن مع الخيوط)."""
    kw = {}
    full = list(cmd)
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kw["start_new_session"] = True
        if _resource is not None and sys.executable:
            env["_HB_FSIZE"] = str(MAX_FILE_MB * 1024 * 1024)
            if STRICT_AS_MB > 0:
                env["_HB_AS"] = str(STRICT_AS_MB * 1024 * 1024)
            full = [sys.executable, "-c", _LAUNCHER] + full
    return subprocess.Popen(full, cwd=cwd, stdout=log_fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, close_fds=True, **kw)


def _run_logged(cmd, cwd, log_fh, env, timeout):
    """يشغّل أمر تثبيت مع مهلة، ويقتل مجموعته كاملة عند تجاوز المهلة."""
    kw = {"start_new_session": True} if os.name != "nt" else {}
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=log_fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, **kw)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid, _SIGKILL, proc)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        return -9


def _kill_group(root_pid, sig, proc=None):
    """يرسل إشارة لمجموعة عمليات المشروع فقط (ولا يلمس مجموعة البوت الرئيسي أبداً)."""
    try:
        if os.name == "nt":
            if proc is not None:
                proc.terminate() if sig == signal.SIGTERM else proc.kill()
            elif psutil:
                psutil.Process(root_pid).kill()
            return
        pgid = os.getpgid(root_pid)
        if pgid == root_pid and pgid != os.getpgrp():
            os.killpg(pgid, sig)
        else:
            os.kill(root_pid, sig)
    except ProcessLookupError:
        pass
    except Exception:
        try:
            os.kill(root_pid, sig)
        except Exception:
            pass


def _pid_alive(pid):
    if not pid:
        return False
    try:
        if psutil:
            if not psutil.pid_exists(int(pid)):
                return False
            return psutil.Process(int(pid)).status() != psutil.STATUS_ZOMBIE
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


class _Adopted:
    """يتبنّى عملية مشروع كانت شغّالة قبل إعادة تشغيل الاستضافة (بدون إيقافها)."""
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None

    def poll(self):
        if _pid_alive(self.pid):
            return None
        self.returncode = -1
        return -1

    def wait(self, timeout=None):
        end = time.time() + (timeout if timeout is not None else 10 ** 9)
        while time.time() < end:
            if self.poll() is not None:
                return self.returncode
            time.sleep(0.2)
        raise subprocess.TimeoutExpired("adopted", timeout)

    def terminate(self):
        _kill_group(self.pid, signal.SIGTERM)

    def kill(self):
        _kill_group(self.pid, _SIGKILL)


def _alive_pid(p):
    """PID المسجّل للمشروع إن كان حياً **وعمله داخل مجلد المشروع** (يمنع قتل عملية غريبة بسبب إعادة استخدام PID)."""
    pidv = p[14]
    if not pidv:
        return None
    try:
        pidv = int(pidv)
        if not _pid_alive(pidv):
            return None
        root = os.path.realpath(p[4])
        if psutil:
            cwd = os.path.realpath(psutil.Process(pidv).cwd())
        else:
            cwd = os.path.realpath(os.readlink(f"/proc/{pidv}/cwd"))
        if cwd == root or cwd.startswith(root + os.sep):
            return pidv
    except Exception:
        pass
    return None


def _tree_usage(pid_os):
    """(rss_MB, مجموع وقت المعالج) لشجرة العمليات."""
    if not psutil:
        return 0.0, 0.0
    root = psutil.Process(pid_os)
    rss, secs = 0, 0.0
    for pr in [root] + root.children(recursive=True):
        try:
            rss += pr.memory_info().rss
            t = pr.cpu_times()
            secs += t.user + t.system
        except Exception:
            pass
    return rss / (1024 * 1024), secs


# ------------------------------------------------------------
# بناء أمر التشغيل (تجهيز البيئة والمكتبات)
# ------------------------------------------------------------
def _ensure_deps(pid, p, entry_path, log_line):
    """يثبّت المكتبات المطلوبة (مرة لكل تغيير) في البيئة المعزولة، ويسجّل الحالة فعلياً."""
    deps_marker = os.path.join(p[4], ".deps_installed")
    needed_now = set()
    try:
        needed_now = set(resolve_required_pkgs(entry_path))
    except Exception:
        pass
    good, bad = sanitize_requirements(p[7] or "")
    needed_now.update(good)
    for b in bad:
        log_line(f"⚠️ سطر مرفوض في المتطلبات (غير آمن/غير صالح): {b[:80]}")
    sig = "|".join(sorted(needed_now))
    old_sig = ""
    if os.path.exists(deps_marker):
        try:
            with open(deps_marker, "r", encoding="utf-8") as f:
                old_sig = f.read()
        except Exception:
            pass
    if not needed_now:
        pstate_set(pid, install_status="ok", install_detail="لا مكتبات خارجية مطلوبة")
        return
    if sig == old_sig:
        if pstate_get(pid).get("install_status") in ("none", "running"):
            pstate_set(pid, install_status="ok", install_detail="المكتبات مثبتة")
        return
    pstate_set(pid, install_status="running", install_detail=f"تثبيت {len(needed_now)} مكتبة")
    log_line(f"🪄 تثبيت تلقائي ذكي ({len(needed_now)} مكتبة)...")
    failed = []
    for pkg in sorted(needed_now):
        ok, msg_ = pip_install_in(p[4], pkg)
        log_line(f"  {'✅' if ok else '❌'} {pkg}")
        _sp(pid, note=f"{'✅' if ok else '❌'} {pkg}")
        if not ok:
            failed.append(pkg)
    try:
        with open(deps_marker, "w", encoding="utf-8") as f:
            f.write(sig)
    except Exception:
        pass
    if failed:
        pstate_set(pid, install_status="partial" if len(failed) < len(needed_now) else "failed",
                   install_detail="فشل: " + ", ".join(failed[:6]))
    else:
        pstate_set(pid, install_status="ok", install_detail=f"تم تثبيت {len(needed_now)} مكتبة")


def _build_cmd(pid, p, entry_path, log_fh, log_line, env):
    """يرجع (cmd, خطأ_HTML). يجهّز venv/المكتبات (python) أو npm (node)."""
    entry = p[5]
    if entry.endswith(".py"):
        _sp(pid, phase="install")
        ensure_venv(p[4], log=log_line)
        py_exe = venv_python(p[4]) or sys.executable
        _ensure_deps(pid, p, entry_path, log_line)
        return [py_exe, "-u", entry], None
    if entry.endswith((".js", ".mjs", ".cjs")):
        node_bin = shutil.which("node")
        if not node_bin:
            return None, ("❌ Node.js غير مثبت على السيرفر.\n"
                          "ثبّته أولاً: <code>pkg install nodejs</code> (Termux) "
                          "أو <code>apt install nodejs npm</code>")
        _sp(pid, phase="install", note="🟨 تجهيز بيئة Node.js...")
        pstate_set(pid, install_status="running", install_detail="تجهيز Node.js")
        if ensure_node_package_json(p[4], entry):
            log_line("📝 تم إنشاء package.json تلقائياً")
            _sp(pid, note="📝 تم توليد package.json")
        if not os.path.exists(os.path.join(p[4], "node_modules")):
            use_yarn = os.path.isfile(os.path.join(p[4], "yarn.lock")) and shutil.which("yarn")
            inst = ["yarn", "install"] if use_yarn else ["npm", "install", "--no-audit", "--no-fund"]
            log_line(f"📦 {' '.join(inst)} ...")
            _sp(pid, note=f"📦 جاري تشغيل {inst[0]} install...")
            try:
                rc = _run_logged(inst, p[4], log_fh, env, timeout=600)
                _sp(pid, note="✅ اكتمل تثبيت حزم Node.js" if rc == 0 else "⚠️ تحذير أثناء التثبيت")
                pstate_set(pid, install_status="ok" if rc == 0 else "failed",
                           install_detail="npm install" + (" نجح" if rc == 0 else f" فشل (رمز {rc})"))
            except Exception as e:
                log_line(f"⚠️ npm install: {e}")
                pstate_set(pid, install_status="failed", install_detail=f"npm: {str(e)[:80]}")
        else:
            pstate_set(pid, install_status="ok", install_detail="node_modules موجودة")
        return [node_bin, entry], None
    if entry.endswith(".sh"):
        return ["bash", entry], None
    return None, "نوع الملف الرئيسي غير قابل للتشغيل."


def _start_project_once(pid, watch=START_WATCH_SECONDS, source="manual"):
    """محاولة تشغيل واحدة (تُستدعى تحت قفل المشروع). ترجع (ok, رسالة HTML, info)."""
    p = get_project(pid)
    if not p:
        return False, "المشروع غير موجود", None
    _sp(pid, phase="check")
    pdir, entry = p[4], p[5]
    if not pdir or not is_inside(PROJECTS_DIR, pdir) or not os.path.isdir(pdir):
        return False, "مجلد المشروع غير موجود أو خارج مجلد الاستضافة.", None
    try:
        entry_path = safe_join(pdir, entry)
    except ValueError:
        return False, "الملف الرئيسي خارج مجلد المشروع.", None
    if not os.path.isfile(entry_path):
        return False, f"الملف <code>{esc(entry)}</code> غير موجود", None
    ok, why = _precheck_entry(entry_path)
    if not ok:
        return False, why, None
    try:
        if dir_size(pdir, limit=MAX_PROJECT_MB * 1024 * 1024 * 2) > MAX_PROJECT_MB * 1024 * 1024 * 2:
            return False, f"حجم المشروع يتجاوز حد الاستضافة ({MAX_PROJECT_MB}MB). احذف الملفات الكبيرة.", None
    except Exception:
        pass

    key = f"p{pid}"
    port, public_url = 0, ""
    try:
        if p[3] == "web":
            port = get_free_port()
            public_url = f"{get_public_url()}/proxy/{pid}"
        log_path = os.path.join(pdir, "output.log")
        rotate_log(log_path)
        env = build_child_env(p, port)
        lf = open(log_path, "a", encoding="utf-8")
        try:
            lf.write(f"\n\n===== START {datetime.now()} ({source}) =====\n")
            lf.flush()

            def _log_line(line):
                try:
                    lf.write(line + "\n")
                    lf.flush()
                except Exception:
                    pass

            cmd, err = _build_cmd(pid, p, entry_path, lf, _log_line, env)
            if err:
                return False, err, None
            _sp(pid, phase="launch")
            proc = _popen_isolated(cmd, pdir, lf, env)
        finally:
            try:
                lf.close()                       # لا نترك مقبض ملف السجل مفتوحاً في العملية الرئيسية
            except Exception:
                pass
        RUNNING[key] = proc
        _START_TS[pid] = time.time()
        update_project(pid, pid=proc.pid, status="running", port=port, public_url=public_url, last_start=now_str())

        _sp(pid, phase="watch", watch_t0=time.time(), watch_total=int(watch))
        for _ in range(int(watch)):
            time.sleep(1)
            if proc.poll() is not None:
                break

        rc = proc.poll()
        if rc is not None:
            if RUNNING.get(key) is proc:
                RUNNING.pop(key, None)
            _START_TS.pop(pid, None)
            tail = _log_tail(p, 25)
            reason, hint = classify_failure(tail, rc)
            now_ = now_str()
            if rc == 0 and p[3] == "script":
                update_project(pid, status="stopped", pid=0, last_stop=now_)
                pstate_set(pid, last_exit_code=0, last_exit_reason="اكتمل السكربت بنجاح", desired="stopped",
                           last_attempt=now_, last_attempt_ok=True)
                add_event(pid, "finished", "اكتمل السكربت (رمز 0)")
                return True, "اكتمل تنفيذ السكربت بنجاح", {
                    "project": p[2], "entry": entry, "ptype": p[3], "pid": 0, "port": 0, "public_url": "",
                    "bot_username": p[9] or "", "bot_name": p[10] or "", "finished": True}
            update_project(pid, status="crashed", pid=0, last_stop=now_)
            pstate_set(pid, last_exit_code=rc, last_exit_reason=reason or "توقف فور التشغيل",
                       last_crash_at=now_, last_attempt=now_, last_attempt_ok=False)
            add_event(pid, "start_failed", f"rc={rc} {reason}")
            _sp(pid, fail={"reason": reason, "hint": hint, "rc": rc})
            shown = mask_secrets(tail)[-1000:] or "(لا توجد تفاصيل)"
            msg_ = (f"<b>❌ المشروع توقف فور التشغيل</b>\n\n"
                    f"📄 الملف: <code>{esc(entry)}</code>\n"
                    + (f"🔎 <b>السبب:</b> {esc(reason)}\n" if reason else "")
                    + (f"💡 <b>الحل المقترح:</b> {esc(hint)}\n" if hint else "")
                    + f"\n<b><u>🔤 آخر سطور:</u></b>\n<pre>{esc(shown)}</pre>")
            return False, msg_, None

        now_ = now_str()
        pstate_set(pid, last_attempt=now_, last_attempt_ok=True, last_exit_reason="", last_exit_code=None)
        add_event(pid, "started", f"pid={proc.pid} via {source}")
        info = {
            "project": p[2], "entry": entry, "ptype": p[3], "pid": proc.pid, "port": port,
            "public_url": public_url, "bot_username": p[9] or "", "bot_name": p[10] or "",
        }
        return True, "تم التشغيل", info
    except Exception as e:
        print(f"[Start] مشروع {pid}: {e}\n{traceback.format_exc()[-500:]}")
        add_event(pid, "start_error", str(e)[:200])
        return False, f"خطأ: <code>{esc(str(e)[:300])}</code>", None


def _start_locked(pid, watch, source):
    """التشغيل الفعلي (يُستدعى والقفل ممسوك). يثبّت المكتبة الناقصة ويعيد المحاولة حتى 5 مرات."""
    _START_PROGRESS[pid] = {"notes": [], "phase": "check", "t0": time.time()}
    old_proc = RUNNING.get(f"p{pid}")
    if old_proc is not None and old_proc.poll() is not None:
        RUNNING.pop(f"p{pid}", None)
        add_event(pid, "crash", f"خرجت العملية السابقة (رمز {old_proc.poll()}) قبل هذا التشغيل")
    installed, tried = [], set()
    ok, msg_, info = False, "", None
    try:
        for _ in range(5):
            ok, msg_, info = _start_project_once(pid, watch, source)
            if ok:
                if installed:
                    msg_ += "\n\n📦 ثُبّتت تلقائياً: " + ", ".join(installed)
                    if info is not None:
                        info["auto_installed"] = installed
                return ok, msg_, info
            p = get_project(pid)
            if not p:
                return False, msg_, None
            tail = _log_tail(p)
            miss = _MISSING_RX.findall(tail)
            if not miss:
                return False, _pretty_fail(msg_, tail), None
            mod = miss[-1].split(".")[0]
            if mod in _local_module_names(p[4]):
                return False, msg_ + f"\n\n💡 <b>الحل:</b> الملف المحلي <code>{esc(mod)}</code> غير مرفوع أو اسمه مختلف.", None
            pkg = IMPORT_TO_PIP.get(mod, mod)
            if not pkg or pkg in tried:
                return False, msg_ + f"\n\n💡 تعذّر تثبيت <code>{esc(pkg)}</code> تلقائياً.", None
            if not valid_pip_spec(pkg):
                return False, msg_ + f"\n\n💡 اسم مكتبة غير صالح: <code>{esc(pkg)}</code>.", None
            tried.add(pkg)
            _sp(pid, phase="install", note=f"🔧 مكتبة ناقصة: {pkg}")
            ok2, m2 = pip_install_in(p[4], pkg)
            _sp(pid, note=f"{'✅' if ok2 else '❌'} {pkg}")
            if not ok2:
                pstate_set(pid, install_status="failed", install_detail=f"فشل تثبيت {pkg}")
                return False, msg_ + f"\n\n❌ فشل تثبيت <code>{esc(pkg)}</code>:\n{m2}", None
            installed.append(pkg)
        return False, msg_, None
    finally:
        _START_PROGRESS.setdefault(pid, {})["done"] = True


def start_project(pid, watch=START_WATCH_SECONDS, source="manual"):
    """
    يشغّل المشروع. source: manual | auto | boot.
    آمن ضد التشغيل المزدوج (قفل لكل مشروع) ولا يرمي استثناءات للخارج.
    """
    try:
        appr = get_project_approved(pid)
        if appr != 1:
            return False, ("⏳ هذا المشروع بانتظار موافقة الإدارة قبل التشغيل." if appr == 0
                           else "❌ تم رفض تشغيل هذا المشروع من قبل الإدارة."), None
        lk = _plock(pid)
        if not lk.acquire(blocking=False):
            return False, "⏳ هناك عملية تشغيل أو إيقاف جارية لهذا المشروع، حاول بعد قليل.", None
        try:
            if is_running(pid):
                return False, "ℹ️ المشروع شغّال بالفعل.", None
            if source == "manual":
                pstate_reset_counters(pid)
                pstate_set(pid, desired="running")
            res = _start_locked(pid, watch, source)
            if source == "manual" and not res[0]:
                pstate_set(pid, desired="stopped")      # فشل يدوي: لا نعيد المحاولة عند كل إقلاع
            return res
        finally:
            lk.release()
    except Exception as e:
        print(f"[start_project] {pid}: {e}\n{traceback.format_exc()[-500:]}")
        return False, f"خطأ غير متوقع: <code>{esc(str(e)[:200])}</code>", None


def _stop_locked(pid, reason="manual", grace=6):
    """يوقف المشروع وكل عملياته الفرعية (والقفل ممسوك). يرجع True إن أوقف شيئاً."""
    p = get_project(pid)
    key = f"p{pid}"
    proc = RUNNING.get(key)
    root_pid = None
    if proc is not None and proc.poll() is None:
        root_pid = proc.pid
    elif p:
        root_pid = _alive_pid(p)               # عملية يتيمة معروفة (يُتحقق من مجلد عملها)
    children = []
    if root_pid and psutil:
        try:
            children = psutil.Process(root_pid).children(recursive=True)
        except Exception:
            children = []
    stopped = False
    if root_pid:
        _kill_group(root_pid, signal.SIGTERM, proc if isinstance(proc, subprocess.Popen) else None)
        end = time.time() + grace
        while time.time() < end and _pid_alive(root_pid) and (proc is None or proc.poll() is None):
            time.sleep(0.2)
        if _pid_alive(root_pid) and (proc is None or proc.poll() is None):
            _kill_group(root_pid, _SIGKILL, proc if isinstance(proc, subprocess.Popen) else None)
            time.sleep(0.5)
        stopped = True
    for ch in children:                            # أي ابن هرب من المجموعة
        try:
            if ch.is_running() and ch.status() != psutil.STATUS_ZOMBIE:
                ch.kill()
        except Exception:
            pass
    if proc is not None:
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
    RUNNING.pop(key, None)
    _START_TS.pop(pid, None)
    reasons = {"manual": "أوقفه المستخدم", "restart": "إعادة تشغيل", "resource": "تجاوز حدود الموارد",
               "delete": "حُذف المشروع", "admin": "أوقفه المطوّر", "restore": "استعادة نسخة احتياطية",
               "shutdown": "إيقاف الاستضافة"}
    update_project(pid, pid=0, status="stopped", last_stop=now_str())
    kw = {"next_retry_at": 0.0, "last_exit_reason": reasons.get(reason, reason)}
    if reason in ("manual", "resource", "delete", "admin", "restore"):
        kw["desired"] = "stopped"
    pstate_set(pid, **kw)
    add_event(pid, "stopped", reasons.get(reason, reason))
    return stopped


def stop_project(pid, reason="manual"):
    """إيقاف آمن للمشروع وعملياته التابعة فقط. لا يؤثر على أي مشروع آخر ولا على البوت الرئيسي."""
    try:
        p = get_project(pid)
        if not p:
            return False, "المشروع غير موجود"
        lk = _plock(pid)
        if not lk.acquire(timeout=25):
            return False, "عملية أخرى جارية على هذا المشروع، حاول بعد قليل."
        try:
            stopped = _stop_locked(pid, reason)
        finally:
            lk.release()
        return True, "تم الإيقاف" if stopped else "كان متوقف"
    except Exception as e:
        print(f"[stop_project] {pid}: {e}")
        return False, f"تعذّر الإيقاف: {str(e)[:120]}"


def restart_project(pid, watch=START_WATCH_SECONDS, source="manual"):
    """إيقاف ثم تشغيل تحت قفل واحد (لا يتخلله مراقب ولا نقرة مزدوجة)."""
    try:
        appr = get_project_approved(pid)
        if appr != 1:
            return False, ("⏳ بانتظار موافقة الإدارة." if appr == 0 else "❌ مرفوض من الإدارة."), None
        lk = _plock(pid)
        if not lk.acquire(timeout=25):
            return False, "عملية أخرى جارية على هذا المشروع، حاول بعد قليل.", None
        try:
            _stop_locked(pid, "restart")
            time.sleep(0.5)
            if source == "manual":
                pstate_reset_counters(pid)
            pstate_set(pid, desired="running")
            add_event(pid, "restart", f"إعادة تشغيل ({source})")
            res = _start_locked(pid, watch, source)
            if source == "manual" and not res[0]:
                pstate_set(pid, desired="stopped")
            return res
        finally:
            lk.release()
    except Exception as e:
        print(f"[restart_project] {pid}: {e}")
        return False, f"خطأ: <code>{esc(str(e)[:200])}</code>", None


def is_running(pid):
    """حالة حيّة بدون آثار جانبية على القاعدة (المراقب هو من يسجّل الانهيار)."""
    proc = RUNNING.get(f"p{pid}")
    return proc is not None and proc.poll() is None


def get_logs(pid, n=100, errors_only=False):
    p = get_project(pid)
    if not p:
        return "المشروع غير موجود"
    lp = os.path.join(p[4], "output.log")
    if not os.path.exists(lp):
        return "لا توجد سجلات بعد"
    try:
        txt = read_tail(lp, 262144)
        lines = txt.splitlines()
        if errors_only:
            keep, take = [], 0
            pat = re.compile(r"(Traceback|Error|Exception|CRITICAL|FATAL|❌|Killed|rror:)")
            for i, ln in enumerate(lines):
                if pat.search(ln):
                    take = 4
                if take > 0:
                    keep.append(ln)
                    take -= 1
            lines = keep or ["لا توجد أخطاء ظاهرة في آخر السجل ✅"]
        return mask_secrets("\n".join(lines[-n:]))
    except Exception as e:
        return f"خطأ: {e}"


def get_live_uptime(pid):
    p = get_project(pid)
    if not p:
        return "—"
    if not is_running(pid):
        return "متوقف"
    return calc_uptime(p[17])


def render_start_card(pid, name=""):
    d = _START_PROGRESS.get(pid, {})
    phase = d.get("phase", "check")
    steps = [("check", "🔍 فحص الملفات"), ("install", "📦 تجهيز البيئة والمكتبات"),
             ("launch", "▶️ إطلاق المشروع"), ("watch", "👁 مراقبة الاستقرار")]
    order = [k for k, _ in steps]
    idx = order.index(phase) if phase in order else 0
    base = {0: 5, 1: 30, 2: 60}.get(idx, 75)
    if phase == "watch":
        total = max(int(d.get("watch_total", START_WATCH_SECONDS)), 1)
        done = min(int(time.time() - d.get("watch_t0", time.time())), total)
        pct = 75 + int(25 * done / total)
    else:
        pct = base
    lines = []
    for i, (_, label) in enumerate(steps):
        mark = "✅" if i < idx else ("⏳" if i == idx else "▫️")
        lines.append(f"{mark} {label}")
    name_line = (f"📦 <b>{esc(name)}</b>\n") if name else ""
    out = (
        "<blockquote><b>🚀 جاري تشغيل المشروع</b></blockquote>\n"
        f"<blockquote>{name_line}{_bar(pct)}  <b>{pct}%</b></blockquote>\n"
        "<blockquote>" + "\n".join(lines) + "</blockquote>"
    )
    notes = d.get("notes", [])[-6:]
    if notes:
        out += "\n<blockquote expandable>" + esc("\n".join(notes)) + "</blockquote>"
    return out


# ------------------------------------------------------------
# 🧭 المراقب (Supervisor): كشف الانهيار + سياسة إعادة التشغيل + حماية الموارد
# ------------------------------------------------------------
def monitor_enabled():
    return get_ui("monitor_enabled") != "0"


def _owner_ids(p, with_admins=False):
    ids = [p[1]]
    if with_admins:
        ids += list(ADMIN_IDS)
    return list(dict.fromkeys(ids))


def _notify_project(p, key, with_admins=False, **vals):
    """إشعار قابل للتخصيص (msg_alert_*) — لا يرمي استثناءات."""
    try:
        text = msg(key, **vals)
    except Exception as e:
        print(f"[Notify] قالب {key}: {e}")
        return
    for uid in _owner_ids(p, with_admins):
        try:
            bot.send_message(uid, text, parse_mode="HTML")
        except Exception:
            pass


def _backoff_for(n_attempts):
    return RESTART_BACKOFF[max(0, min(n_attempts, len(RESTART_BACKOFF) - 1))]


def _schedule_or_exhaust(pid, p, hist, reason, ran_for=None, first_alert=True):
    """يقرر: جدولة محاولة تالية بمهلة متدرجة، أو إعلان التوقف النهائي. (سياسة واحدة واضحة)."""
    st = pstate_get(pid)
    now = time.time()
    if not monitor_enabled() or not st["auto_restart"] or st["desired"] != "running":
        pstate_set(pid, next_retry_at=0.0, restarts=hist)
        add_event(pid, "no_restart", "الإعادة التلقائية معطّلة أو الإيقاف مطلوب")
        if first_alert:
            _notify_project(p, "msg_alert_crash", name=p[2], reason=reason or "غير معروف",
                            restart_info="الإعادة التلقائية معطّلة لهذا المشروع — شغّله يدوياً بعد المراجعة.",
                            attempt="0", max=str(st["max_restarts"]), delay="0")
        return "disabled"
    if len(hist) >= st["max_restarts"]:
        pstate_set(pid, exhausted=True, next_retry_at=0.0, restarts=hist)
        add_event(pid, "restart_exhausted", f"{len(hist)} محاولات خلال النافذة")
        _notify_project(p, "msg_alert_failed", with_admins=True, name=p[2], id=str(pid),
                        attempts=str(len(hist)), reason=reason or "غير معروف")
        return "exhausted"
    delay = _backoff_for(len(hist))
    pstate_set(pid, next_retry_at=now + delay, restarts=hist, exhausted=False)
    add_event(pid, "restart_scheduled", f"المحاولة {len(hist) + 1}/{st['max_restarts']} بعد {delay}ث")
    if first_alert and (now - float(st.get("last_alert_at") or 0) > 120 or len(hist) == 0):
        pstate_set(pid, last_alert_at=now)
        _notify_project(p, "msg_alert_crash", name=p[2], reason=reason or "غير معروف",
                        restart_info=f"سيُعاد تشغيله تلقائياً بعد {delay} ثانية (المحاولة {len(hist) + 1} من {st['max_restarts']}).",
                        attempt=str(len(hist) + 1), max=str(st["max_restarts"]), delay=str(delay))
    return "scheduled"


def _on_process_exit(pid, proc, rc):
    """يُستدعى (والقفل ممسوك) عند اكتشاف خروج عملية مشروع كان يعمل."""
    key = f"p{pid}"
    if RUNNING.get(key) is proc:
        RUNNING.pop(key, None)
    p = get_project(pid)
    started = _START_TS.pop(pid, None)
    if not p:
        return
    tail = _log_tail(p, 60)
    reason, _hint = classify_failure(tail, rc)
    now_ = now_str()
    st = pstate_get(pid)
    if rc == 0:
        update_project(pid, status="stopped", pid=0, last_stop=now_)
        pstate_set(pid, last_exit_code=0, last_exit_reason="انتهى تنفيذه بشكل طبيعي", next_retry_at=0.0, desired="stopped")
        add_event(pid, "finished", "انتهى بشكل طبيعي (رمز 0)")
        return
    update_project(pid, status="crashed", pid=0, last_stop=now_)
    pstate_set(pid, crash_count=int(st["crash_count"]) + 1, last_exit_code=rc, last_exit_reason=reason or "توقف غير متوقع",
               last_crash_at=now_)
    add_event(pid, "crash", f"rc={rc} {reason}")
    now = time.time()
    ran_for = (now - started) if started else None
    hist = [t for t in st["restarts"] if now - t < RESTART_WINDOW_SEC]
    if ran_for is not None and ran_for >= STABLE_SECONDS:
        hist = []                                   # كان مستقراً طويلاً: ابدأ العدّ من جديد
    _schedule_or_exhaust(pid, p, hist, reason, ran_for)


def _auto_restart(pid):
    """محاولة إعادة تشغيل مجدولة (تُنفَّذ في خيط خلفي، وليس داخل حلقة المراقب)."""
    try:
        p = get_project(pid)
        st = pstate_get(pid)
        if (not p or not monitor_enabled() or st["desired"] != "running" or not st["auto_restart"]
                or is_running(pid) or get_project_approved(pid) != 1):
            pstate_set(pid, next_retry_at=0.0)
            return
        now = time.time()
        hist = [t for t in st["restarts"] if now - t < RESTART_WINDOW_SEC] + [now]
        pstate_set(pid, restarts=hist, total_restarts=int(st["total_restarts"]) + 1, next_retry_at=0.0)
        add_event(pid, "auto_restart", f"المحاولة {len(hist)}/{st['max_restarts']}")
        ok, m, _info = start_project(pid, watch=8, source="auto")
        if ok:
            pstate_set(pid, exhausted=False)
            add_event(pid, "recovered", "عاد للعمل بعد إعادة التشغيل التلقائية")
            _notify_project(p, "msg_alert_recovered", name=p[2], attempt=str(len(hist)))
        else:
            st2 = pstate_get(pid)
            if "شغّال بالفعل" in (m or "") or "جارية" in (m or ""):
                return
            _schedule_or_exhaust(pid, p, hist, st2.get("last_exit_reason") or "فشل التشغيل", first_alert=False)
    except Exception as e:
        print(f"[AutoRestart] {pid}: {e}\n{traceback.format_exc()[-400:]}")
    finally:
        with _RESTARTING_G:
            _RESTARTING.discard(pid)


def _kill_for_limit(pid, p, reason):
    try:
        stop_project(pid, reason="resource")
        log_action(0, "resource_limit", f"pid={pid} {reason}")
    except Exception as e:
        print(f"[Guard] {e}")
    _notify_project(p, "msg_alert_resource", with_admins=True, name=p[2], id=str(pid), owner=str(p[1]), reason=reason)


_cpu_last = {}
_cpu_strikes = {}
_last_host_kill = [0.0]


def _limit_ram(pid, owner):
    try:
        custom = int(float(get_ui(f"limit_ram_{pid}") or 0))
        if custom > 0:
            return custom
    except Exception:
        pass
    return MAX_RAM_MB * (2 if is_premium(owner) else 1)


def _guard_tick():
    """حماية الموارد: رام/معالج لكل مشروع + حماية الاستضافة نفسها عند ضغط الذاكرة."""
    if not psutil:
        return
    usage = {}
    for key, proc in list(RUNNING.items()):
        try:
            pid = int(key[1:])
        except Exception:
            continue
        try:
            if proc.poll() is not None:
                continue
            p = get_project(pid)
            if not p or is_admin(p[1]):
                continue
            rss, secs = _tree_usage(proc.pid)
        except Exception:
            continue
        usage[pid] = (rss, p)
        now = time.time()
        prev = _cpu_last.get(pid)
        _cpu_last[pid] = (now, secs)
        limit = _limit_ram(pid, p[1])
        if rss > limit:
            _kill_for_limit(pid, p, f"تجاوز الذاكرة: {rss:.0f}MB من {limit}MB")
            continue
        if prev and now > prev[0]:
            cpu = (secs - prev[1]) / (now - prev[0]) * 100.0
            _cpu_strikes[pid] = (_cpu_strikes.get(pid, 0) + 1) if cpu > MAX_CPU_PCT else 0
            if _cpu_strikes.get(pid, 0) >= CPU_STRIKES:
                _cpu_strikes[pid] = 0
                _kill_for_limit(pid, p, f"استهلاك معالج مستمر: {cpu:.0f}%")
    try:
        vm = psutil.virtual_memory()
        if vm.percent >= HOST_RAM_DANGER and usage and time.time() - _last_host_kill[0] > 60:
            _last_host_kill[0] = time.time()
            pid = max(usage, key=lambda x: usage[x][0])
            _kill_for_limit(pid, usage[pid][1], f"ضغط على ذاكرة السيرفر ({vm.percent}%)")
    except Exception:
        pass


def supervisor_tick(guard=False):
    """دورة مراقبة واحدة (قابلة للاختبار)."""
    # 1) اكتشاف العمليات التي خرجت
    for key, proc in list(RUNNING.items()):
        try:
            pid = int(key[1:])
        except Exception:
            continue
        try:
            rc = proc.poll()
        except Exception:
            rc = -1
        if rc is None:
            continue
        lk = _plock(pid)
        if not lk.acquire(blocking=False):
            continue                                  # عملية تشغيل/إيقاف جارية؛ هي المسؤولة
        try:
            if RUNNING.get(key) is proc:
                _on_process_exit(pid, proc, rc)
        except Exception as e:
            print(f"[Monitor] خروج {pid}: {e}\n{traceback.format_exc()[-400:]}")
        finally:
            lk.release()
    # 2) مزامنة: القاعدة تقول "يعمل" والعملية غير موجودة (تُنفَّذ بعد اكتمال استعادة الإقلاع فقط)
    for p in (get_all_projects() if _MONITOR.get("boot_done") else []):
        pid = p[0]
        if p[15] == "running" and f"p{pid}" not in RUNNING:
            lk = _plock(pid)
            if not lk.acquire(blocking=False):
                continue
            try:
                if f"p{pid}" in RUNNING:
                    continue
                alive = _alive_pid(p)
                if alive:
                    RUNNING[f"p{pid}"] = _Adopted(alive)
                else:
                    _on_process_exit(pid, None, -1)
            except Exception as e:
                print(f"[Monitor] مزامنة {pid}: {e}")
            finally:
                lk.release()
    # 3) تنفيذ إعادة التشغيل المجدولة
    now = time.time()
    for p in get_all_projects():
        pid = p[0]
        st = pstate_get(pid)
        nxt = float(st.get("next_retry_at") or 0)
        if nxt and nxt <= now:
            with _RESTARTING_G:
                if pid in _RESTARTING:
                    continue
                _RESTARTING.add(pid)
            if run_bg(_auto_restart, pid) is None:
                with _RESTARTING_G:
                    _RESTARTING.discard(pid)
    # 4) تدوير السجلات + حماية الموارد
    if guard:
        for key in list(RUNNING.keys()):
            try:
                pp = get_project(int(key[1:]))
                if pp:
                    rotate_log(os.path.join(pp[4], "output.log"), LOG_MAX_MB * 3)
            except Exception:
                pass
        _guard_tick()


def supervisor_loop():
    """حلقة المراقبة الدائمة: أي استثناء يُسجَّل ولا يوقف المراقب."""
    _MONITOR["alive"] = True
    n = 0
    while True:
        try:
            n += 1
            supervisor_tick(guard=(n % max(1, 15 // MONITOR_INTERVAL) == 0))
            _MONITOR["last_tick"] = time.time()
            _MONITOR["ticks"] += 1
        except Exception as e:
            _MONITOR["errors"] += 1
            _MONITOR["last_error"] = str(e)[:200]
            print(f"[Supervisor] {e}\n{traceback.format_exc()[-500:]}")
        time.sleep(MONITOR_INTERVAL)


def restore_desired_states(delay=3):
    """بعد الإقلاع: تبنّي ما بقي شغّالاً، وتشغيل المشاريع التي كانت مطلوبة شغّالة."""
    time.sleep(delay)
    restored, failed = [], []
    try:
        for p in get_all_projects():
            pid = p[0]
            try:
                st = pstate_get(pid)
                if st["desired"] != "running" or get_project_approved(pid) != 1 or is_running(pid):
                    continue
                alive = _alive_pid(p)
                if alive:
                    RUNNING[f"p{pid}"] = _Adopted(alive)
                    _START_TS[pid] = time.time()
                    restored.append(p[2])
                    add_event(pid, "adopted", f"تبنّي العملية {alive} بعد إعادة تشغيل الاستضافة")
                    continue
                ok, _m, _i = start_project(pid, watch=8, source="boot")
                (restored if ok else failed).append(p[2])
                add_event(pid, "boot_start", "نجح" if ok else "فشل")
                time.sleep(2)
            except Exception as e:
                print(f"[Boot] {pid}: {e}")
        if restored or failed:
            text = msg("msg_alert_boot_restore", ok=Raw("\n".join("✅ " + esc(n) for n in restored[:25]) or "—"),
                       failed=Raw("\n".join("❌ " + esc(n) for n in failed[:25]) or "—"))
            for aid in ADMIN_IDS:
                try:
                    bot.send_message(aid, text, parse_mode="HTML")
                except Exception:
                    pass
    except Exception as e:
        print(f"[Boot] {e}")
    finally:
        _MONITOR["boot_done"] = True


def stop_all_projects(reason="shutdown"):
    n = 0
    for p in get_all_projects():
        if is_running(p[0]):
            stop_project(p[0], reason=reason)
            n += 1
    return n


# ------------------------------------------------------------
# 🧠 تشخيص الأعطال (يفحص فعلياً ولا يدّعي إصلاحاً لم يحدث)
# ------------------------------------------------------------
_DIAG_ICON = {"error": "🔴", "warn": "🟡", "ok": "🟢", "info": "🔵"}


def _py_import_names(pdir):
    """أسماء الاستيراد العليا (غير المدمجة وغير المحلية) في كل ملفات بايثون بالمشروع."""
    std = _stdlib_names()
    local = _local_module_names(pdir)
    return sorted(n for n in _collect_imports(pdir) if n not in std and n not in local and not n.startswith("_"))


_FIND_SPEC_SNIPPET = (
    "import sys, json, importlib.util\n"
    "miss = []\n"
    "for n in json.loads(sys.argv[1]):\n"
    "    try:\n"
    "        if importlib.util.find_spec(n) is None: miss.append(n)\n"
    "    except Exception:\n"
    "        miss.append(n)\n"
    "print(json.dumps(miss))\n"
)


def find_missing_modules(pdir, entry_path):
    """أسماء وحدات مستوردة لكنها غير مثبتة في بيئة المشروع (فحص فعلي بمفسّر المشروع)."""
    names = _py_import_names(pdir)
    if not names:
        return [], None
    py = venv_python(pdir)
    if not py:
        return None, "لم تُنشأ البيئة المعزولة بعد (تُنشأ تلقائياً عند أول تشغيل)."
    env = build_child_env(("", "", "", "bot", pdir), 0)
    libs = os.path.join(pdir, PYLIBS_DIRNAME)
    env["PYTHONPATH"] = libs
    try:
        r = subprocess.run([py, "-c", _FIND_SPEC_SNIPPET, json.dumps(names)], capture_output=True, text=True,
                           timeout=30, env=env, cwd=pdir)
        out = (r.stdout or "").strip().splitlines()
        return (json.loads(out[-1]) if out else []), None
    except subprocess.TimeoutExpired:
        return None, "انتهت مهلة فحص المكتبات."
    except Exception as e:
        return None, f"تعذّر فحص المكتبات: {str(e)[:80]}"


def diagnose_project(pid):
    """
    يرجع {"project": اسم, "findings": [{sev, title, detail, fix}], "errors": n, "warns": n}.
    يعمل بالكامل داخل try — لا يرمي استثناءات. (يُستدعى من خيط خلفي.)
    """
    out = {"project": "", "findings": [], "errors": 0, "warns": 0}

    def add(sev, title, detail="", fix="", block=False):
        out["findings"].append({"sev": sev, "title": title, "detail": detail, "fix": fix, "block": block})
        if sev == "error":
            out["errors"] += 1
        elif sev == "warn":
            out["warns"] += 1

    p = get_project(pid)
    if not p:
        add("error", "المشروع غير موجود")
        return out
    out["project"] = p[2]
    pdir, entry = p[4], p[5]
    try:
        if not pdir or not os.path.isdir(pdir):
            add("error", "مجلد المشروع مفقود", pdir or "", "أعد رفع المشروع أو استعده من نسخة احتياطية.", block=True)
            return out
        if not is_inside(PROJECTS_DIR, pdir):
            add("error", "مجلد المشروع خارج مجلد الاستضافة", "", "تواصل مع المطوّر.")
            return out
        # 1) الموافقة
        appr = get_project_approved(pid)
        if appr == 0:
            add("warn", "المشروع بانتظار موافقة الإدارة", "", "لن يعمل قبل الموافقة.")
        elif appr == 2:
            add("error", "تم رفض تشغيل المشروع من الإدارة", "", "تواصل مع الدعم.", block=True)
        # 2) الملف الرئيسي
        entry_path = os.path.join(pdir, entry or "")
        if not entry or not os.path.isfile(entry_path):
            det, cands = "", []
            try:
                _t, cand = detect_entry_and_type(pdir, p[3])
                if cand:
                    cands.append(cand)
            except Exception:
                pass
            add("error", f"الملف الرئيسي غير موجود: {entry}", "",
                (f"وُجد ملف محتمل: {cands[0]}. ارفع الملف الصحيح أو أعد إنشاء المشروع." if cands
                 else "ارفع الملف الرئيسي من «📁 مدير الملفات»."), block=True)
        else:
            if not entry.endswith(_RUNNABLE_EXT):
                add("error", "الملف الرئيسي غير قابل للتشغيل", entry, "يجب أن يكون .py أو .js أو .sh", block=True)
            elif entry.endswith(".py"):
                try:
                    with open(entry_path, "r", encoding="utf-8", errors="replace") as f:
                        src = f.read()
                    compile(src, entry, "exec")
                    add("ok", "صيغة Python سليمة")
                except SyntaxError as e:
                    add("error", f"خطأ كتابة Python في السطر {e.lineno}", f"{e.msg}: {(e.text or '').strip()[:80]}",
                        "صحّح السطر ثم ارفع الملف من جديد.", block=True)
                # متغيرات بيئة مطلوبة بصيغة os.environ["X"]
                try:
                    envd = load_project_env_dict(pdir)
                    req_env = set()
                    for node in ast.walk(ast.parse(src)):
                        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
                                and node.value.attr == "environ" and isinstance(node.slice, ast.Constant)
                                and isinstance(node.slice.value, str)):
                            req_env.add(node.slice.value)
                    miss_env = sorted(x for x in req_env if x not in envd and x not in os.environ)
                    if miss_env:
                        add("warn", "متغيرات بيئة مطلوبة غير معرّفة", ", ".join(miss_env[:6]),
                            "أضفها من «🔐 محرر .env».")
                except Exception:
                    pass
                # المكتبات
                miss, why = find_missing_modules(pdir, entry_path)
                if miss is None:
                    add("info", "تعذّر فحص المكتبات", why or "")
                elif miss:
                    add("error", "مكتبات مستوردة وغير مثبتة", ", ".join(miss[:8]),
                        "اضغط «🪄 تثبيت تلقائي ذكي» (يُثبّت داخل بيئة المشروع فقط).")
                else:
                    add("ok", "كل المكتبات المستوردة مثبتة")
            elif entry.endswith((".js", ".mjs", ".cjs")):
                if not shutil.which("node"):
                    add("warn", "Node.js غير مثبت على السيرفر", "", "لا يمكن تشغيل مشاريع JavaScript قبل تثبيته.")
                else:
                    okj, _rep = check_js_file(entry_path)
                    add("ok" if okj else "error", "صيغة JavaScript سليمة" if okj else "خطأ كتابة في JavaScript",
                        "" if okj else re.sub(r"<[^>]+>", "", _rep)[:300], "" if okj else "صحّح الخطأ وأعد الرفع.", block=not okj)
                    if not os.path.isdir(os.path.join(pdir, "node_modules")):
                        add("info", "حزم Node غير مثبتة بعد", "", "ستُثبَّت تلقائياً عند التشغيل (npm install).")
        # 3) requirements
        rq = os.path.join(pdir, "requirements.txt")
        reqs_text = p[7] or ""
        if os.path.isfile(rq):
            try:
                with open(rq, "r", encoding="utf-8", errors="ignore") as f:
                    reqs_text = reqs_text or f.read()
            except Exception:
                pass
        if reqs_text.strip():
            good, bad = sanitize_requirements(reqs_text)
            if bad:
                add("warn", f"{len(bad)} سطر في requirements غير صالح/غير آمن", "; ".join(b[:40] for b in bad[:3]),
                    "اكتب أسماء الحزم فقط (مثل requests==2.31.0). الخيارات والروابط مرفوضة لأمن الاستضافة.")
        # 4) حالة التثبيت
        st = pstate_get(pid)
        ins = st.get("install_status")
        if ins in ("failed", "partial"):
            add("error" if ins == "failed" else "warn", "تثبيت المكتبات لم يكتمل", st.get("install_detail") or "",
                "جرّب «🪄 تثبيت تلقائي ذكي» أو ثبّت المكتبة الفاشلة يدوياً.")
        elif ins == "ok":
            add("ok", "تثبيت المكتبات: " + (st.get("install_detail") or "سليم"))
        # 5) آخر تشغيل من السجل
        tail = _log_tail(p, 80)
        if tail.strip():
            reason, fix = classify_failure(tail, st.get("last_exit_code"))
            if reason and (not is_running(pid) or "Traceback" in tail):
                add("error" if not is_running(pid) else "warn", f"من السجل: {reason}", last_error_line(tail)[:200], fix)
        elif st.get("last_exit_reason") and not is_running(pid):
            add("warn", f"آخر توقف: {st['last_exit_reason']}")
        # 6) الموارد والمساحة
        try:
            size = dir_size(pdir, limit=MAX_PROJECT_MB * 1024 * 1024 * 3)
            if size > MAX_PROJECT_MB * 1024 * 1024:
                add("error", f"حجم المشروع {fmt_size(size)} يتجاوز الحد ({MAX_PROJECT_MB}MB)", "", "احذف الملفات الكبيرة.", block=True)
            free = shutil.disk_usage(DATA_DIR).free
            if free < 100 * 1024 * 1024:
                add("warn", f"المساحة الحرة على السيرفر منخفضة ({fmt_size(free)})", "", "اطلب من المطوّر تفريغ مساحة.")
        except Exception:
            pass
        # 7) المراقبة
        if st.get("exhausted"):
            add("error", "توقفت إعادة التشغيل التلقائية بعد استنفاد المحاولات", st.get("last_exit_reason") or "",
                "أصلح السبب ثم اضغط «🔄 إعادة محاولة التشغيل».")
        if not out["findings"]:
            add("ok", "لا توجد مشاكل ظاهرة")
        elif out["errors"] == 0 and out["warns"] == 0 and not any(f["sev"] == "ok" for f in out["findings"]):
            add("ok", "لا توجد مشاكل ظاهرة")
    except Exception as e:
        add("error", "تعذّر إكمال الفحص", str(e)[:150])
    return out


def render_diagnosis(res):
    lines = []
    for f in res["findings"][:14]:
        line = f"{_DIAG_ICON.get(f['sev'], '•')} <b>{esc(f['title'])}</b>"
        if f.get("detail"):
            line += f"\n   <code>{esc(mask_secrets(f['detail']))[:220]}</code>"
        if f.get("fix"):
            line += f"\n   💡 {esc(f['fix'])}"
        lines.append(line)
    if res["errors"]:
        summary = f"🔴 {res['errors']} مشكلة تمنع/تعطّل التشغيل · 🟡 {res['warns']} تنبيه"
    elif res["warns"]:
        summary = f"🟡 {res['warns']} تنبيه — لا مشاكل تمنع التشغيل"
    else:
        summary = "🟢 لا توجد مشاكل ظاهرة"
    return msg("msg_project_diagnosis", name=res.get("project") or "—", summary=summary, findings=Raw("\n\n".join(lines)))


# ============================================================
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


_PIP_LOCKS = {}


_PIP_LOCKS_G = threading.Lock()


def _pip_lock(pdir):
    """قفل لكل مشروع: لا تثبيتان متزامنان على نفس البيئة (RLock لأن ensure_venv يستدعى من داخل التثبيت)."""
    key = os.path.realpath(pdir)
    with _PIP_LOCKS_G:
        lk = _PIP_LOCKS.get(key)
        if lk is None:
            lk = _PIP_LOCKS[key] = threading.RLock()
        return lk


_SKIP_SCAN_DIRS = {VENV_DIRNAME, ".pylibs", "node_modules", ".git", "__pycache__"}


def _local_module_names(pdir):
    """أسماء الملفات/المجلدات المحلية بالمشروع (حتى عمق 4) كي لا تُثبَّت كأنها مكتبات PyPI."""
    names = set()
    base_depth = pdir.rstrip(os.sep).count(os.sep)
    try:
        for root, dirs, files in os.walk(pdir):
            dirs[:] = [d for d in dirs if d not in _SKIP_SCAN_DIRS]
            if root.count(os.sep) - base_depth >= 4:
                dirs[:] = []
            for f in files:
                if f.endswith(".py"):
                    names.add(f[:-3])
            names.update(dirs)
    except Exception:
        pass
    return names


def _collect_imports(pdir, max_files=300):
    """أسماء الاستيراد العليا من كل ملفات بايثون بالمشروع (حد 300 ملف و1MB لكل ملف)."""
    names, n = set(), 0
    for root, dirs, files in os.walk(pdir):
        dirs[:] = [d for d in dirs if d not in _SKIP_SCAN_DIRS]
        for f in files:
            if not f.endswith(".py"):
                continue
            n += 1
            if n > max_files:
                return names
            fp = os.path.join(root, f)
            try:
                if os.path.islink(fp) or os.path.getsize(fp) > 1024 * 1024:
                    continue
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    tree = ast.parse(fh.read())
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        names.add(a.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names.add(node.module.split(".")[0])
    return names


_PROJECT_COLS = {"name", "ptype", "path", "entry", "files", "requirements", "bot_token", "bot_username",
                 "bot_name", "port", "public_url", "installed", "pid", "status", "last_start", "last_stop", "approved"}


def _age_seconds(ts):
    try:
        return (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds()
    except Exception:
        return None


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
    """ينشئ بيئة بايثون معزولة لهذا المشروع إن لم تكن موجودة (مع قفل ومهلة وتنظيف عند الفشل)."""
    if venv_python(pdir):
        return True, "موجودة"
    with _pip_lock(pdir):
        if venv_python(pdir):
            return True, "موجودة"
        try:
            if log:
                log("🧪 إنشاء بيئة بايثون معزولة لهذا المشروع...")
            rc, out = _run_pip([sys.executable, "-m", "venv", venv_dir(pdir)], 180)
            if rc != 0 or not venv_python(pdir):
                shutil.rmtree(venv_dir(pdir), ignore_errors=True)      # لا نترك بيئة نصف مكتملة
                msg_ = (out or "")[-300:]
                if log:
                    log(f"⚠️ تعذّر إنشاء بيئة معزولة ({msg_}) — ستُستخدم مكتبات محلية داخل مجلد المشروع.")
                return False, msg_
            if log:
                log("✅ البيئة المعزولة جاهزة.")
            return True, "تم الإنشاء"
        except Exception as e:
            if log:
                log(f"⚠️ خطأ في إنشاء البيئة المعزولة: {e}")
            return False, str(e)


PYLIBS_DIRNAME = ".pylibs"


def _run_pip(cmd, timeout):
    """يشغّل أمر pip/venv بمهلة وفي مجموعة عمليات مستقلة (تُقتل كاملة عند المهلة)."""
    env = {k: v for k, v in os.environ.items() if k not in _ENV_BLOCK}
    env["PIP_NO_INPUT"] = "1"
    kw = {"start_new_session": True} if os.name != "nt" else {}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                stdin=subprocess.DEVNULL, env=env, **kw)
        try:
            out, _ = proc.communicate(timeout=timeout)
            return proc.returncode, out or ""
        except subprocess.TimeoutExpired:
            _kill_group(proc.pid, _SIGKILL, proc)
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return -9, "انتهت المهلة"
    except Exception as e:
        return -1, str(e)


def _py_has_pip(py):
    rc, _ = _run_pip([py, "-m", "pip", "--version"], 30)
    return rc == 0


def pip_install_in(pdir, pkg, timeout=300):
    """
    تثبيت مكتبة لمشروع واحد فقط: بيئته المعزولة ← أو مجلد .pylibs داخل المشروع.
    لا يلمس بايثون الاستضافة أبداً. يرفض الصيغ الخطرة (خيارات pip، روابط، مسارات).
    """
    pkg = (pkg or "").strip()
    if not pkg:
        return False, "اسم المكتبة فاضي"
    if not valid_pip_spec(pkg):
        return False, (f"<code>{esc(pkg[:60])}</code>\nصيغة غير مسموحة. اكتب اسم الحزمة فقط "
                       "(مثل <code>requests</code> أو <code>requests==2.31.0</code>).")
    pkg_name = re.split(r"[=<>!;\[~ ]", pkg)[0].strip()
    lk = _pip_lock(pdir)
    if not lk.acquire(timeout=timeout + 60):
        return False, "تثبيت آخر جارٍ لهذا المشروع، حاول بعد قليل."
    try:
        ensure_venv(pdir)
        common = ["-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", "--no-input"]
        attempts = []
        vpy = venv_python(pdir)
        if vpy and _py_has_pip(vpy):
            attempts.append(("معزولة", [vpy] + common + [pkg]))
        if not _py_has_pip(sys.executable):
            _run_pip([sys.executable, "-m", "ensurepip", "--upgrade"], 120)
        libs = os.path.join(pdir, PYLIBS_DIRNAME)
        os.makedirs(libs, exist_ok=True)
        attempts.append(("محلية", [sys.executable] + common + ["--target", libs, "--upgrade", pkg]))
        last_err = ""
        for tag, cmd in attempts:
            rc, out = _run_pip(cmd, timeout)
            if rc == 0:
                ver = re.search(rf"Successfully installed .*?{re.escape(pkg_name)}[- ](\S+)", out)
                v = f" v{esc(ver.group(1))}" if ver else ""
                return True, f"<code>{esc(pkg_name)}</code>{v} ({tag})"
            errs = [l for l in out.splitlines() if "ERROR" in l.upper()]
            last_err = errs[-1] if errs else out[-200:]
        return False, f"<code>{esc(pkg_name)}</code>\n<code>{esc(mask_secrets(last_err)[:200])}</code>"
    finally:
        lk.release()


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


def resolve_required_pkgs(entry_path, pdir=None):
    """أسماء حزم pip المطلوبة، مستنتجة من استيرادات كل ملفات المشروع (لا من الملف الرئيسي فقط)."""
    pdir = pdir or os.path.dirname(entry_path)
    imports = set(_collect_imports(pdir))
    try:
        issues = analyze_python_file(entry_path)
        imports |= set(issues.get("stats", {}).get("imports") or [])
    except Exception:
        pass
    stdlib = _stdlib_names()
    local_mods = _local_module_names(pdir)
    pkgs = []
    for imp in imports:
        if imp in stdlib or imp in local_mods or imp.startswith("_"):
            continue
        mapped = IMPORT_TO_PIP.get(imp, imp)
        if mapped and valid_pip_spec(mapped):
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
ADMIN_STORAGE_DIR = os.path.join(DATA_DIR, "admin_storage")
os.makedirs(ADMIN_STORAGE_DIR, exist_ok=True)


def zip_project_dir(pdir, dest_zip, exclude_dirnames=(VENV_DIRNAME, "__pycache__", ".git"), exclude_files=(),
                    skip_logs=False):
    """يضغط ملفات المشروع: يتخطى الروابط الرمزية (لا تسريب لملفات الاستضافة) والملفات التي تختفي أثناء النسخ."""
    count = 0
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(pdir):
            dirs[:] = [d for d in dirs if d not in exclude_dirnames and not os.path.islink(os.path.join(root, d))]
            for f in files:
                full = os.path.join(root, f)
                if os.path.islink(full) or f in exclude_files or (skip_logs and f.endswith(".log")):
                    continue
                try:
                    z.write(full, os.path.relpath(full, pdir))
                    count += 1
                except (OSError, ValueError):
                    continue
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

# -*- coding: utf-8 -*-
# ============================================================
# 📝 نظام الرسائل القابلة للتخصيص (محفوظة في قاعدة البيانات) — v13
# كل رسالة لها مفتاح ثابت + نص افتراضي + متغيرات {name}. التعديل من: لوحة المطور ← تخصيص ← رسائل النظام.
# ============================================================
_LN = "━━━━━━━━━━━━━━━━━━━━━━━━━"

# key: (وصف للأدمن, النص الافتراضي, وصف المتغيرات)
MSG_DEFS = {
    "msg_project_started": (
        "✅ رسالة نجاح تشغيل المشروع",
        f"{_LN}\n  <b>🎉 تم التشغيل بنجاح</b>\n{_LN}\n\n"
        "📦 <b>المشروع:</b> <code>{name}</code>\n🏷️ <b>النوع:</b> {type}\n📄 <b>الملف:</b> <code>{entry}</code>\n"
        "🆔 <b>PID:</b> <code>{pid}</code>\n⏱️ <b>بدأ في:</b> <code>{time}</code>\n{extra}\n"
        f"{_LN}\n<b>✅ المشروع شغّال حالياً</b>",
        "{name} {type} {entry} {pid} {time} {extra}"),
    "msg_project_stopped": (
        "⏹ رسالة إيقاف المشروع",
        "⏹ <b>تم إيقاف المشروع</b>\n\n📦 <code>{name}</code>\n🕒 {time}",
        "{name} {time}"),
    "msg_project_restarted": (
        "🔁 رسالة إعادة تشغيل المشروع",
        "🔁 <b>تمت إعادة تشغيل المشروع</b>\n\n📦 <code>{name}</code>\n🆔 PID: <code>{pid}</code>\n🕒 {time}",
        "{name} {pid} {time}"),
    "msg_project_error": (
        "❌ رسالة فشل تشغيل المشروع",
        "<b>❌ فشل التشغيل</b>\n\n📦 <code>{name}</code>\n\n{details}",
        "{name} {details}"),
    "msg_project_status": (
        "📊 بطاقة حالة المشروع",
        f"{_LN}\n  <b>📦 {{name}}</b>\n{_LN}\n\n"
        "🆔 <b>المعرّف:</b> <code>{id}</code>\n🏷️ <b>النوع:</b> {type}\n📄 <b>الملف:</b> <code>{entry}</code>\n"
        "📡 <b>الحالة:</b> {state}\n⏱️ <b>مدة التشغيل:</b> <code>{uptime}</code>\n"
        "▶️ <b>آخر تشغيل:</b> <code>{last_start}</code>\n⏹ <b>آخر توقف:</b> <code>{last_stop}</code>\n"
        "{stop_reason}"
        "🔁 <b>إعادة التشغيل:</b> {restarts}\n🛡️ <b>المراقبة:</b> {monitor}\n"
        "📚 <b>المكتبات:</b> {install}\n💾 <b>النسخ الاحتياطي:</b> {backup}\n{resources}{last_error}{url}",
        "{name} {id} {type} {entry} {state} {uptime} {last_start} {last_stop} {stop_reason} {restarts} {monitor} {install} {backup} {resources} {last_error} {url}"),
    "msg_project_diagnosis": (
        "🧠 رسالة تشخيص المشروع",
        "<b>🧠 تشخيص: {name}</b>\n{summary}\n\n{findings}\n\n<i>الفحص يعرض الحالة الفعلية ولا يصلح شيئاً تلقائياً.</i>",
        "{name} {summary} {findings}"),
    "msg_alert_crash": (
        "🚨 تنبيه: توقف مشروع (انهيار)",
        "🚨 <b>توقف مشروعك بشكل غير متوقع</b>\n\n📦 <code>{name}</code>\n🔎 <b>السبب:</b> {reason}\n🔁 {restart_info}",
        "{name} {reason} {restart_info} {attempt} {max} {delay}"),
    "msg_alert_recovered": (
        "✅ تنبيه: عاد المشروع للعمل",
        "✅ <b>عاد مشروعك للعمل تلقائياً</b>\n\n📦 <code>{name}</code>\n🔁 بعد المحاولة رقم {attempt}",
        "{name} {attempt}"),
    "msg_alert_failed": (
        "🛑 تنبيه: استنفاد محاولات إعادة التشغيل",
        "🛑 <b>تعذّر استعادة المشروع بعد {attempts} محاولات</b>\n\n📦 <code>{name}</code> (ID: {id})\n🔎 <b>آخر سبب:</b> {reason}\n\n"
        "لن تتكرر المحاولة تلقائياً. افتح المشروع ← 🧠 تشخيص ثم 🔄 إعادة محاولة التشغيل.",
        "{name} {id} {attempts} {reason}"),
    "msg_alert_resource": (
        "🛡️ تنبيه: إيقاف بسبب الموارد",
        "🛡️ <b>أُوقف مشروع لتجاوزه حدود الموارد</b>\n\n📦 <code>{name}</code> (ID: {id}) · المالك <code>{owner}</code>\n⚠️ {reason}",
        "{name} {id} {owner} {reason}"),
    "msg_alert_boot_restore": (
        "♻️ تنبيه: استعادة المشاريع بعد إعادة تشغيل الاستضافة",
        "♻️ <b>استعادة المشاريع بعد إعادة تشغيل الاستضافة</b>\n\n<b>عادت للعمل:</b>\n{ok}\n\n<b>تعذّر تشغيلها:</b>\n{failed}",
        "{ok} {failed}"),
    "msg_backup_ok": (
        "💾 رسالة نجاح النسخ الاحتياطي",
        "💾 <b>تم إنشاء النسخة الاحتياطية وتوثيقها</b>\n\n🕒 {time}\n📦 المشاريع: {projects} · 📄 الملفات: {files}\n💽 الحجم: {size}\n🔐 SHA256: <code>{sha}</code>",
        "{time} {projects} {files} {size} {sha}"),
    "msg_backup_fail": (
        "⚠️ رسالة فشل النسخ الاحتياطي",
        "⚠️ <b>فشل النسخ الاحتياطي</b>\n\n{reason}\n\n<i>لم يُحذف أي نسخة سليمة سابقة، والبوت يعمل بشكل طبيعي.</i>",
        "{reason}"),
    "msg_restore_ok": (
        "♻️ رسالة نجاح الاستعادة",
        "♻️ <b>تمت الاستعادة بنجاح</b>\n\n{details}",
        "{details}"),
    "msg_restore_fail": (
        "❌ رسالة فشل الاستعادة",
        "❌ <b>فشلت الاستعادة</b>\n\n{reason}\n\n<i>لم يتغيّر شيء في بياناتك الحالية.</i>",
        "{reason}"),
    "help_text": (
        "📚 نص المساعدة",
        f"{_LN}\n  <b><u>📚 المساعدة</u></b>\n{_LN}\n\n"
        "<b><u>📝 خطوات إنشاء مشروع:</u></b>\n\n<b>1.</b> اضغط <b>➕ مشروع جديد</b>\n<b>2.</b> اختر النوع\n"
        "<b>3.</b> أرسل اسم المشروع\n<b>4.</b> أرسل الملف الرئيسي (أو ملف ZIP)\n"
        "<b>5.</b> أرسل <code>requirements.txt</code> (اختياري)\n<b>6.</b> اضغط <b>⚙️ ثبّت</b>\n<b>7.</b> اضغط <b>▶️ تشغيل</b>\n\n"
        f"{_LN}\n<b><u>🛡️ الاستقرار:</u></b>\n\nمشروعك يُراقَب تلقائياً، ويُعاد تشغيله عند التوقف (يمكنك تعطيل ذلك من ⚙️ إعدادات المشروع).\n"
        "من لوحة المشروع: 📄 السجلات · 🧠 تشخيص · 💾 نسخ احتياطي · ♻️ استعادة.\n\n"
        f"{_LN}\n<b><u>🔒 الأمان:</u></b>\n\nمشاريعك خاصة عن باقي المستخدمين (إدارة الاستضافة قد تصل للملفات للصيانة).\n\n"
        f"{_LN}\n<b><u>📞 التواصل:</u></b>\n\nللدعم أو الاستفسار:\n👤 المطور: @{{developer}}",
        "{developer}"),
    "support_text": (
        "💬 نص شاشة التواصل مع الدعم",
        f"{_LN}\n  <b><u>💬 التواصل مع الدعم</u></b>\n{_LN}\n\n📤 <b>اكتب رسالتك الآن</b>\n\n<i>سيتم إرسالها للمطور مباشرة</i>",
        "(بدون متغيرات)"),
    "support_sent_text": (
        "✅ رسالة تأكيد إرسال الدعم",
        "✅ <b>تم إرسال رسالتك للمطور</b>\n\nسيتم الرد عليك قريباً",
        "(بدون متغيرات)"),
    "projects_header": (
        "📦 عنوان شاشة «مشاريعي»",
        "📦 <b>مشاريعي</b>\n\n🟢 شغّال: <b>{running}</b> · 🔴 متوقف: <b>{stopped}</b> · 📦 الإجمالي: <b>{total}</b>",
        "{running} {stopped} {total}"),
}


def _msg_default(key):
    d = MSG_DEFS.get(key)
    return d[1] if d else ""


for _k, (_label, _default, _ph) in MSG_DEFS.items():
    DEFAULT_UI.setdefault(_k, _default)
    UI_TEMPLATES[_k] = _label
DEFAULT_UI["welcome_text"] = DEFAULT_UI.get("welcome_text", "")
DEFAULT_UI.setdefault("auto_restart_default", "1")
DEFAULT_UI.setdefault("auto_restart_max", "5")
DEFAULT_UI.setdefault("monitor_enabled", "1")
DEFAULT_UI.setdefault("backup_keep", "5")
DEFAULT_UI.setdefault("proj_backup_keep", "3")
DEFAULT_UI.setdefault("cloud_auto", "1")


def _fmt_vals(text, vals):
    """استبدال {name} بأمان: المتغير غير المعروف يبقى كما هو ولا يكسر الرسالة أبداً."""
    def _r(m):
        k = m.group(1)
        if k in vals:
            return str(vals[k])
        return m.group(0)
    return re.sub(r"\{(\w+)\}", _r, text)


def msg(key, **vals):
    """رسالة قابلة للتخصيص: قيم النصوص تُهرَّب تلقائياً إلا إذا لُفّت بـ Raw(...)."""
    safe = {}
    for k, v in vals.items():
        safe[k] = v if isinstance(v, Raw) else esc(v)
    text = get_ui(key) or _msg_default(key)
    try:
        return _fmt_vals(text, safe)
    except Exception:
        return _fmt_vals(_msg_default(key), safe)


def ui_has_custom(key):
    with DB_LOCK:
        conn = _conn()
        try:
            return conn.execute("SELECT 1 FROM ui_settings WHERE key=?", (key,)).fetchone() is not None
        finally:
            conn.close()


def ui_reset(key):
    """استعادة النص الافتراضي (حذف التخصيص من قاعدة البيانات)."""
    with DB_LOCK:
        conn = _conn()
        try:
            conn.execute("DELETE FROM ui_settings WHERE key=?", (key,))
            conn.commit()
        finally:
            conn.close()


def ui_reset_all_messages():
    n = 0
    for k in MSG_DEFS:
        if ui_has_custom(k):
            ui_reset(k)
            n += 1
    return n


_TG_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a", "blockquote",
                    "tg-spoiler", "tg-emoji", "span"}


def validate_telegram_html(text):
    """فحص توازن وسوم HTML المدعومة في تيليجرام. يرجع (صالح؟, سبب)."""
    stack = []
    for m in re.finditer(r"<(/?)([A-Za-z][\w-]*)([^<>]*)>", text):
        closing, tag = m.group(1) == "/", m.group(2).lower()
        if tag not in _TG_ALLOWED_TAGS:
            return False, f"وسم غير مدعوم: <{tag}>"
        if closing:
            if not stack or stack[-1] != tag:
                return False, f"وسم إغلاق غير متوازن: </{tag}>"
            stack.pop()
        else:
            stack.append(tag)
    if stack:
        return False, f"وسم غير مغلق: <{stack[-1]}>"
    if re.search(r"&(?!(amp|lt|gt|quot|#\d+);)", text):
        return False, "يوجد رمز & غير مهرَّب (استخدم &amp;)"
    return True, ""


# ============================================================
# 🎨 تجميل النصوص (فواصل/صناديق) — دمج طبقة التصحيح القديمة داخل الملف الرئيسي
# ============================================================
ORIG_LINE = "━" * 25
SHORT_LINE = "━" * 14
_TL = threading.local()
_HDR_RX = re.compile(r"━{8,}[ \t]*\n[ \t]*(<b>[^\n]*?</b>)[ \t]*\n[ \t]*━{8,}[ \t]*")
_LINE_RX = re.compile(r"━{8,}")
_PROTECT_RX = re.compile(r"(<pre.*?</pre>|<code>.*?</code>|<blockquote.*?</blockquote>)", re.S)


def _divider():
    try:
        custom = get_ui("divider_line")
        if custom and custom != ORIG_LINE:
            return custom
    except Exception:
        pass
    return SHORT_LINE


def beautify(text, html_mode=True):
    """(خط ━ + عنوان + خط ━) ← صندوق blockquote، ويقصّر الخطوط المنفردة (لا تنكسر في RTL)."""
    if not isinstance(text, str) or ("━" not in text and "\\n" not in text):
        return text
    if html_mode:
        text = _HDR_RX.sub(lambda m: "<blockquote>" + m.group(1) + "</blockquote>", text)
        parts = _PROTECT_RX.split(text)
        for i in range(0, len(parts), 2):
            seg = parts[i].replace("\\n", "\n")
            parts[i] = _LINE_RX.sub(_divider(), seg)
        return "".join(parts)
    return _LINE_RX.sub(_divider(), text)


def _bar(pct, width=12):
    try:
        pct = max(0.0, min(100.0, float(pct)))
    except Exception:
        pct = 0.0
    filled = int(round(width * pct / 100.0))
    return "▰" * filled + "▱" * (width - filled)


def make_header(title):
    return "<blockquote><b>" + str(title) + "</b></blockquote>"


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
    return (
        f"{make_header('💥 ' + str(title))}\n\n"
        f"<pre>{esc(mask_secrets(str(detail))[:900])}</pre>"
        + (f"\n💡 <i>{esc(hint)}</i>" if hint else "")
    )


def apply_divider(text):
    if not isinstance(text, str):
        return text
    try:
        return beautify(text, html_mode=getattr(_TL, "html", False))
    except Exception:
        return text


def _fix_markup(k):
    """تنظيف لوحات الأزرار: حذف الأزرار المكررة (نفس callback) + زر الدليل للأدمن."""
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
        if "admin_toggle_pause" in seen and "adm_guide" not in seen:
            rows.append([btn_green("📖 دليل الأدمن (علّمني)", "adm_guide")])
        rm.keyboard = rows
    except Exception as e:
        print(f"[UI] fix_markup: {e}")


def _plain_from_html(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


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
    extra = ""
    if ptype == "web" and info.get("public_url"):
        extra += f"\n🔗 <b>الرابط:</b>\n<code>{esc(info['public_url'])}</code>\n"
    if info.get("bot_username"):
        extra += f"\n🤖 <b>يوزر البوت:</b> @{esc(info['bot_username'])}\n👤 <b>اسم البوت:</b> {esc(info.get('bot_name') or '—')}\n"
    if info.get("auto_installed"):
        extra += "\n📦 <b>ثُبّتت تلقائياً:</b> " + esc(", ".join(info["auto_installed"])) + "\n"
    if info.get("finished"):
        return msg("msg_project_started", name=info["project"], type=type_label(ptype), entry=info["entry"],
                   pid="—", time=datetime.now().strftime("%H:%M:%S"), extra=Raw(extra)).replace(
            "المشروع شغّال حالياً", "اكتمل تنفيذ السكربت")
    return msg("msg_project_started", name=info["project"], type=type_label(ptype), entry=info["entry"],
               pid=info["pid"], time=datetime.now().strftime("%H:%M:%S"), extra=Raw(extra))


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
bot = telebot.TeleBot(BOT_TOKEN, num_threads=_env_num("BOT_THREADS", 8))

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
            if len(static.strip()) < 6 or len(re.findall(r"[\u0600-\u06FFA-Za-z]", static)) < 3:
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
        if key.startswith(_self_prefixes) or fn in _PREMIUM_SELF_FUNCS or fn.startswith(("_dx", "cb_dx", "cmd_dx")):
            continue
        if "*" in key:
            glabel = _derive_clean_label(label, key, fn)
            g = groups.setdefault(key, {"key": key, "label": glabel, "count": 0})
            g["count"] += 1
            # Add pattern button to its screen too so every screen shows all its buttons
            bid = _ui_id(key, 8)
            fid, fo = fobj(fn)
            if bid not in buttons:
                buttons[bid] = {"key": key, "label": glabel, "fid": fid}
                bkeys[key] = bid
            if bid not in fo["btns"]:
                fo["btns"].append(bid)
            continue
        bid = _ui_id(key, 8)
        fid, fo = fobj(fn)
        if bid not in buttons:
            buttons[bid] = {"key": key, "label": label, "fid": fid}
            bkeys[key] = bid
        if bid not in fo["btns"]:
            fo["btns"].append(bid)

    # Ensure all main_menu buttons are registered in exact order
    _mm_fid, _mm_fo = fobj("main_menu")
    _mm_defs = [
        ("btn_dashboard", "🌐 لوحة التحكم"),
        ("my_projs", "📦 مشاريعي"),
        ("new_proj", "➕ مشروع جديد"),
        ("my_account", "👤 حسابي"),
        ("help", "❓ مساعدة"),
        ("wallet_menu", "💳 محفظتي والشحن"),
        ("contact_support", "💬 تواصل مع الدعم"),
        ("admin_panel", "👑 لوحة المطور"),
    ]
    _mm_rows = [
        ["btn_dashboard"],
        ["my_projs", "new_proj"],
        ["my_account", "help"],
        ["wallet_menu", "contact_support"],
        ["admin_panel"],
    ]
    for _mk, _ml in _mm_defs:
        _mbid = _ui_id(_mk, 8)
        if _mbid not in buttons:
            buttons[_mbid] = {"key": _mk, "label": _ml, "fid": _mm_fid}
            bkeys[_mk] = _mbid
        if _mbid not in _mm_fo["btns"]:
            _mm_fo["btns"].append(_mbid)
    _mm_fo["rows"] = [[bkeys[k] for k in r if k in bkeys] for r in _mm_rows]
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
        "cb_adm_recharges": "⏳ طلبات الشحن",
        "cb_adm_prices": "⚙️ أسعار الباقات والإحالة",
        "cb_adm_new_code": "🎟 إنشاء كود شحن",
        "cb_adm_add_bal": "💳 تعديل رصيد مستخدم",
        "cb_admin_demo_web": "🌐 ويب المراقبة (OK)",
        "cb_admin_bot_name": "✏️ تغيير اسم البوت",
        "cb_admin_bot_photo": "🖼 تغيير صورة البوت",
        "cb_pull": "📤 سحب ملفات المستخدمين",
        "cb_wallet_recharge": "💳 شحن الرصيد",
        "cb_wallet_redeem": "🎟 استخدام كود شحن",
        "cb_admin_storage": "📥 تخزين المطوّر",
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
    'cb_env_editor', '_do_env_save', 'cb_toggle_pause', '_paused_message', '_paused_callback',
    'cb_dx_actions', 'cb_dx_intercept', 'cb_dx_toggle', 'cmd_dx_edit', '_do_dx_input',
    '_dx_on_message', '_dx_editor_markup', '_dx_attach_page_btn'
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


def apply_cosmetics(text):
    return apply_divider(apply_templates(text))


# ============================================================
# 🪝 اعتراض عام لكل الرسائل الصادرة (يطبّق القوالب + الفاصل الزخرفي تلقائياً)
# ============================================================
_orig_send_message = bot.send_message
_orig_edit_message_text = bot.edit_message_text
_orig_reply_to = bot.reply_to
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
    _fix_markup(k)
    tid, fid = _dx_attach_page_btn(chat_id, text, k)
    out = _cosmetic(text, k)
    try:
        if _too_long(out):
            raise ValueError("message is too long")
        res = _orig_send_message(chat_id, out, *a, **k)
    except Exception as e:
        if not (_is_parse_error(e) or "too long" in str(e).lower()):
            raise
        kk = dict(k)
        kk.pop("parse_mode", None)
        plain = _plain_from_html(out) if isinstance(out, str) else out
        res = _orig_send_message(chat_id, plain[:4000] if isinstance(plain, str) else plain, *a, **kk)
    if (tid or fid) and res and getattr(res, "message_id", None):
        _DX_MSG_PAGE[(chat_id, res.message_id)] = (tid, fid)
    return res


def _edit_message_text(text, *a, **k):
    chat_id = k.get("chat_id") or (a[0] if len(a) > 0 else None)
    _fix_markup(k)
    tid, fid = _dx_attach_page_btn(chat_id, text, k)
    out = _cosmetic(text, k)
    res = None
    try:
        if _too_long(out):
            raise ValueError("message is too long")
        res = _orig_edit_message_text(out, *a, **k)
    except Exception as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return None                      # لا شيء تغيّر: ليس خطأ ولا يجب إرسال رسالة بديلة
        if not (_is_parse_error(e) or "too long" in err):
            raise
        kk = dict(k)
        kk.pop("parse_mode", None)
        plain = _plain_from_html(out) if isinstance(out, str) else out
        try:
            res = _orig_edit_message_text(plain[:4000] if isinstance(plain, str) else plain, *a, **kk)
        except Exception as e2:
            if "message is not modified" in str(e2).lower():
                return None
            raise
    msg_id = k.get("message_id") or (a[1] if len(a) > 1 else None)
    if (tid or fid) and chat_id and msg_id:
        _DX_MSG_PAGE[(chat_id, msg_id)] = (tid, fid)
    return res


def _reply_to(message, text, *a, **k):
    _fix_markup(k)
    out = _cosmetic(text, k)
    try:
        return _orig_reply_to(message, out, *a, **k)
    except Exception as e:
        if not _is_parse_error(e):
            raise
        kk = dict(k)
        kk.pop("parse_mode", None)
        return _orig_reply_to(message, _plain_from_html(out), *a, **kk)


bot.send_message = _send_message
bot.edit_message_text = _edit_message_text
bot.reply_to = _reply_to


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
        if fo.get("tpls"):
            rows.append([btn_green("⚡ تعيين دفعي لإيموجيات الشاشة", f"pbtn_batch_{fid}"), btn_blue("📝 نصوص هذه الشاشة", f"ptpl_scr_{fid}_0")])
        else:
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
        if fo.get("btns"):
            m.add(btn_green("🎛 أزرار هذه الشاشة", f"pbtn_scr_{fid}_0"))
        _scr_back = _DX_PTPL_BACK.get(uid) or "ptpl_root:0"
        for r in _nav_row(f"ptpl_scr_{fid}", page, pages, _scr_back):
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
        _v_back = _DX_PTPL_BACK.get(uid) or f"ptpl_scr_{info['fid']}_0"
        m.add(btn_gray("🔙 رجوع", _v_back))

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



_PTPL_PREVIEW = {}

@bot.callback_query_handler(func=lambda c: c.data.startswith("ptpl_save_"))
def cb_ptpl_save(call):
    if not is_admin(call.from_user.id):
        return
    tid = call.data[len("ptpl_save_"):]
    new = _PTPL_PREVIEW.pop(tid, None)
    if not new:
        bot.answer_callback_query(call.id, "⚠️ انتهت المعاينة، أرسل النص مجدداً", show_alert=True)
        return
    with DB_LOCK:
        conn = _conn(); c = conn.cursor()
        c.execute("REPLACE INTO tpl_custom (tid, text) VALUES (?, ?)", (tid, new))
        conn.commit(); conn.close()
    _load_tpl_cache()
    bot.answer_callback_query(call.id, "✅ تم الحفظ!")
    bot.edit_message_text(
        "✅ تم حفظ القالب وتفعيله فوراً.",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=types.InlineKeyboardMarkup().add(btn_green("📝 عرض القالب", f"ptpl_v_{tid}")),
    )

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
    _PTPL_PREVIEW[tid] = new
    sample = {n: f"[{n}]" for n in info["names"]}
    rendered = _render_tpl(new, sample)
    pm = types.InlineKeyboardMarkup(row_width=2)
    pm.row(btn_green("✅ تأكيد وحفظ", f"ptpl_save_{tid}"), btn_blue("✏️ تعديل مرة أخرى", f"ptpl_e_{tid}"))
    pm.add(btn_red("❌ إلغاء", f"ptpl_v_{tid}"))
    bot.send_message(
        msg.chat.id,
        f"{make_header('👁️ معاينة حية قبل الحفظ')}\n\n{rendered}",
        reply_markup=pm,
        parse_mode="HTML",
    )


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
def _do_restore_db(msg_):
    if not is_admin(msg_.from_user.id):
        return
    user_states.pop(msg_.from_user.id, None)
    tmp = DB_PATH + ".restore_tmp"
    try:
        if (msg_.document.file_size or 0) > TG_DOWNLOAD_LIMIT:
            bot.reply_to(msg_, "❌ الملف أكبر من 20MB (حد تيليجرام للبوتات).")
            return
        fi = bot.get_file(msg_.document.file_id)
        with open(tmp, "wb") as f:
            f.write(bot.download_file(fi.file_path))
        ok, details = restore_db_file(tmp)
        bot.send_message(msg_.chat.id, msg("msg_restore_ok", details=Raw(details)) if ok
                         else msg("msg_restore_fail", reason=Raw(details)), parse_mode="HTML")
    except Exception as e:
        bot.reply_to(msg_, render_error("فشلت الاستعادة", e), parse_mode="HTML")
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


# ============================================================
# 🔁 إعادة التشغيل الشاملة
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("restart_"))
def cb_restart_project(call):
    pid = _parse_pid(call.data, "restart_")
    p = _owned_project(call, pid)
    if not p:
        return
    appr = get_project_approved(pid)
    if appr != 1:
        bot.answer_callback_query(call.id, "⏳ مشروعك بانتظار موافقة الإدارة قبل التشغيل." if appr == 0
                                  else "❌ تم رفض تشغيل هذا المشروع من قبل الإدارة.", show_alert=True)
        return
    if _plock(pid).locked():
        bot.answer_callback_query(call.id, "⏳ هناك عملية جارية على هذا المشروع، انتظر قليلاً.", show_alert=True)
        return
    bot.answer_callback_query(call.id, "🔁 جاري إعادة التشغيل...")
    threading.Thread(target=_start_flow, args=(call.message.chat.id, call.message.message_id, pid, p[2], "restart"),
                     daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == "admin_restart_all")
def cb_restart_all(call):
    if not is_admin(call.from_user.id):
        return
    bot.answer_callback_query(call.id, "🔁 جاري إعادة تشغيل كل المشاريع الشغّالة...")
    m0 = bot.send_message(call.message.chat.id, "⏳ ...")

    def do():
        projs = [p for p in get_all_projects() if is_running(p[0])]
        items = []
        for p in projs:
            ok, _m, _i = restart_project(p[0], watch=8)
            items.append((esc(p[2]), ok))
        txt = render_progress("🔁 نتيجة إعادة التشغيل", "✅", "❌", items) if items else "ℹ️ لا توجد مشاريع شغّالة حالياً."
        bot.edit_message_text(txt, m0.chat.id, m0.message_id, parse_mode="HTML",
                              reply_markup=types.InlineKeyboardMarkup().add(btn_gray("🔙 رجوع", "admin_panel")))
    run_bg(do)


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
    rest = call.data[len("pfiles_"):]
    pid_s, _, page_s = rest.partition(":")
    try:
        pid, page = int(pid_s), int(page_s or 0)
    except Exception:
        return
    p = _owned_project(call, pid)
    if not p:
        return
    bot.answer_callback_query(call.id)
    files = _visible_files(p)
    pages = max(1, (len(files) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    m = types.InlineKeyboardMarkup(row_width=1)
    for f in files[page * PER_PAGE:(page + 1) * PER_PAGE]:
        size = get_file_size_str(os.path.join(p[4], f))
        label = f if len(f) <= 38 else "…" + f[-37:]
        m.row(btn_blue(f"📄 {label} ({size})", f"pfview_{pid}_{_fkey(f)}"))
    m.add(btn_green("➕ اضافة ملف", f"addfile_{pid}"))
    if os.path.exists(os.path.join(p[4], ".env")):
        m.add(btn_blue("🔐 تعديل .env", f"penv_{pid}"))
    else:
        m.add(btn_gray("🔐 إنشاء .env", f"penv_{pid}"))
    for r in _nav_row(f"pfiles_{pid}", page, pages, f"proj_{pid}"):
        m.row(*r)
    txt = f"{make_header('📁 ملفات ' + esc(p[2]))}\n\n📊 العدد: <code>{len(files)}</code> — صفحة {page + 1}/{pages}"
    _show(call, txt, m)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfview_"))
def cb_file_view(call):
    pid_s, _, key = call.data[len("pfview_"):].partition("_")
    try:
        pid = int(pid_s)
    except Exception:
        return
    p = _owned_project(call, pid)
    if not p:
        return
    rel, fp = _resolve_file(p, key)
    if not fp:
        bot.answer_callback_query(call.id, "❌ الملف غير موجود", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_blue("📥 تحميل", f"pfdl_{pid}_{key}"), btn_red("🗑 حذف", f"pfdel_{pid}_{key}"))
    m.add(btn_gray("🔙 رجوع", f"pfiles_{pid}:0"))
    txt = f"{make_header(esc(rel))}\n\n📊 الحجم: <code>{get_file_size_str(fp)}</code>"
    _show(call, txt, m)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfdl_"))
def cb_file_download(call):
    pid_s, _, key = call.data[len("pfdl_"):].partition("_")
    try:
        pid = int(pid_s)
    except Exception:
        return
    p = _owned_project(call, pid)
    if not p:
        return
    rel, fp = _resolve_file(p, key)
    if not fp or not os.path.isfile(fp):
        bot.answer_callback_query(call.id, "❌ الملف غير موجود", show_alert=True)
        return
    if os.path.getsize(fp) > TG_UPLOAD_LIMIT:
        bot.answer_callback_query(call.id, "❌ الملف أكبر من حد تيليجرام (49MB)", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    with open(fp, "rb") as f:
        bot.send_document(call.message.chat.id, f, visible_file_name=os.path.basename(rel))


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfdel_"))
def cb_file_delete_confirm(call):
    pid_s, _, key = call.data[len("pfdel_"):].partition("_")
    try:
        pid = int(pid_s)
    except Exception:
        return
    p = _owned_project(call, pid)
    if not p:
        return
    rel, fp = _resolve_file(p, key)
    if not fp:
        bot.answer_callback_query(call.id, "❌ الملف غير موجود", show_alert=True)
        return
    if rel == p[5]:
        bot.answer_callback_query(call.id, "⚠️ هذا الملف الرئيسي، لا يمكن حذفه (احذف المشروع كله بدلاً من ذلك).", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_red("✅ نعم احذف", f"pfdelok_{pid}_{key}"), btn_gray("❌ الغاء", f"pfview_{pid}_{key}"))
    _show(call, f"⚠️ حذف <code>{esc(rel)}</code>؟", m)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pfdelok_"))
def cb_file_delete_go(call):
    pid_s, _, key = call.data[len("pfdelok_"):].partition("_")
    try:
        pid = int(pid_s)
    except Exception:
        return
    p = _owned_project(call, pid)
    if not p:
        return
    rel, fp = _resolve_file(p, key)
    if fp and rel != p[5]:
        try:
            os.remove(fp)
            add_event(pid, "file_deleted", rel)
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ تعذّر الحذف: {str(e)[:60]}", show_alert=True)
            return
    call.data = f"pfiles_{pid}:0"
    cb_files_v2(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("penv_"))
def cb_env_editor(call):
    pid = _parse_pid(call.data, "penv_")
    p = _owned_project(call, pid)
    if not p:
        return
    bot.answer_callback_query(call.id)
    fp = os.path.join(p[4], ".env")
    content = ""
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(10000)
        except Exception:
            pass
    user_states[call.from_user.id] = f"waiting_env_{pid}"
    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ الغاء", f"pfiles_{pid}:0"))
    shown = content if content.strip() else "KEY=VALUE"
    txt = (
        f"{make_header('🔐 تعديل .env')}\n\n"
        f"📄 المحتوى الحالي:\n<pre>{esc(shown)}</pre>\n\n"
        "👆 انسخه وعدّل عليه، ثم أرسل المحتوى الكامل الجديد."
    )
    _show(call, txt, m)


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_env_"))
def _do_env_save(msg_):
    uid = msg_.from_user.id
    try:
        pid = int(user_states.pop(uid)[len("waiting_env_"):])
    except Exception:
        return
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return
    content = msg_.text or ""
    if len(content) > 10000:
        bot.reply_to(msg_, "❌ المحتوى طويل جداً.")
        return
    try:
        with open(os.path.join(p[4], ".env"), "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        bot.reply_to(msg_, render_error("فشل الحفظ", e), parse_mode="HTML")
        return
    add_event(pid, "env_saved", ".env")
    m = types.InlineKeyboardMarkup()
    m.row(btn_green("▶️ تشغيل/إعادة تشغيل", f"restart_{pid}" if is_running(pid) else f"start_{pid}"),
          btn_gray("🔙 رجوع", f"pfiles_{pid}:0"))
    bot.reply_to(msg_, "✅ تم حفظ ملف <code>.env</code>\n<i>التغييرات تُطبَّق عند التشغيل التالي للمشروع.</i>",
                 parse_mode="HTML", reply_markup=m)


# ============================================================
# 🧯 مراقب محسّن: إعادة تشغيل تلقائي عند الانهيار (بحد أقصى للمحاولات)
# ============================================================




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
            b = types.InlineKeyboardButton(
                _dname,
                web_app=types.WebAppInfo(url=dashboard_url)
            )
            if _dstyle:
                try:
                    b.style = _dstyle
                except:
                    pass
            if _dcust.get("icon"):
                try:
                    b.icon_custom_emoji_id = _dcust["icon"]
                except:
                    pass
            m.add(b)
        except:
            m.add(_orig_btn(_dname, url=dashboard_url, style=_dstyle))
    else:
        b = _orig_btn(_dname, url=dashboard_url, style=_dstyle)
        if _dcust.get("icon"):
            try:
                b.icon_custom_emoji_id = _dcust["icon"]
            except:
                pass
        m.add(b)

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

    m.add(btn_blue("📊 حالة السيرفر", "server_monitor"))
    if is_admin(uid):
        m.add(make_custom_btn("btn_admin", cb="admin_panel"))

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
        "  <b>🚀 HOST BOT v13</b>\n"
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
        _show(call, txt, m)
        return
    run_map = {p[0]: is_running(p[0]) for p in projs}
    running = sum(1 for v in run_map.values() if v)
    txt = msg("projects_header", running=str(running), stopped=str(len(projs) - running), total=str(len(projs)))
    m = types.InlineKeyboardMarkup(row_width=1)
    now = time.time()
    for p in projs[:60]:
        pid = p[0]
        icon = {"bot": "🐍", "web": "🌐", "node": "🟨", "script": "📜"}.get(p[3], "📦")
        name = p[2][:22]
        if run_map[pid]:
            m.add(btn_green(f"{icon} {name} | ⏱️ {calc_uptime(p[17])}", f"proj_{pid}"))
        else:
            st = pstate_get(pid)
            nxt = float(st.get("next_retry_at") or 0)
            if nxt and nxt > now:
                m.add(btn_blue(f"{icon} {name} | 🟡 إعادة تشغيل", f"proj_{pid}"))
            elif st.get("exhausted"):
                m.add(btn_red(f"{icon} {name} | 🛑 توقف نهائي", f"proj_{pid}"))
            else:
                m.add(btn_red(f"{icon} {name} | 🔴 متوقف", f"proj_{pid}"))
    m.add(btn_green("➕ مشروع جديد", "new_proj"))
    m.add(btn_gray("🔙 رجوع", "main_menu"))
    _show(call, txt, m)


# ============================================================
# تفاصيل مشروع
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("proj_"))
def cb_proj(call):
    pid = _parse_pid(call.data, "proj_")
    p = _owned_project(call, pid)
    if not p:
        return
    vals, st, running = project_status_vals(p)
    txt = msg("msg_project_status", **vals)
    if not p[8]:
        txt += "\n⚠️ <i>لم يتم اكتشاف توكن بوت تيليجرام في المشروع</i>" if p[3] == "bot" else ""
    _show(call, txt, project_panel_markup(p, st, running))


# ============================================================
# تشغيل
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("start_"))
def cb_start(call):
    pid = _parse_pid(call.data, "start_")
    p = _owned_project(call, pid)
    if not p:
        return
    appr = get_project_approved(pid)
    if appr != 1:
        bot.answer_callback_query(call.id, "⏳ مشروعك بانتظار موافقة الإدارة قبل التشغيل." if appr == 0
                                  else "❌ تم رفض تشغيل هذا المشروع من قبل الإدارة.", show_alert=True)
        return
    if is_running(pid):
        bot.answer_callback_query(call.id, "ℹ️ المشروع شغّال بالفعل")
        _refresh_panel(call, pid)
        return
    if _plock(pid).locked():
        bot.answer_callback_query(call.id, "⏳ هناك عملية جارية على هذا المشروع، انتظر قليلاً.", show_alert=True)
        return
    bot.answer_callback_query(call.id, "⏳ جاري التشغيل...")
    m0 = bot.send_message(call.message.chat.id, "⏳ جاري التجهيز...", parse_mode="HTML")
    threading.Thread(target=_start_flow, args=(m0.chat.id, m0.message_id, pid, p[2], "start"), daemon=True).start()


# ============================================================
# ايقاف
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("stop_"))
def cb_stop(call):
    pid = _parse_pid(call.data, "stop_")
    p = _owned_project(call, pid)
    if not p:
        return
    bot.answer_callback_query(call.id, "⏹ جاري الإيقاف...")

    def go():
        ok, m_ = stop_project(pid)
        try:
            bot.send_message(call.message.chat.id, msg("msg_project_stopped", name=p[2], time=datetime.now().strftime("%H:%M:%S"))
                             if ok else f"⚠️ {esc(m_)}", parse_mode="HTML")
        except Exception:
            pass
        _refresh_panel(call, pid)

    threading.Thread(target=go, daemon=True).start()


# ============================================================
# السجلات
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith(("logs_", "logs_live_", "logs_dl_", "logs_err_")))
def cb_logs(call):
    is_dl = call.data.startswith("logs_dl_")
    is_live = call.data.startswith("logs_live_")
    is_err = call.data.startswith("logs_err_")
    pid = _parse_pid(call.data, "logs_dl_" if is_dl else "logs_live_" if is_live else "logs_err_" if is_err else "logs_")
    p = _owned_project(call, pid)
    if not p:
        return
    if is_dl:
        lp = os.path.join(p[4], "output.log")
        if not os.path.isfile(lp):
            return bot.answer_callback_query(call.id, "لا يوجد ملف سجل بعد", show_alert=True)
        bot.answer_callback_query(call.id)
        try:
            data = mask_secrets(read_tail(lp, 2 * 1024 * 1024))
            bio = io.BytesIO(data.encode("utf-8", "replace"))
            bio.name = f"{safe_project_name(p[2]) or 'project'}_logs.txt"
            bot.send_document(call.message.chat.id, bio, caption=f"📜 سجل {esc(p[2])} (آخر 2MB، الأسرار مخفية)", parse_mode="HTML")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ تعذّر إرسال السجل: {esc(str(e)[:120])}", parse_mode="HTML")
        return
    mode = "err" if is_err else "all"
    if is_live:
        bot.answer_callback_query(call.id, "📡 جاري المراقبة الحية لـ 15 ثانية...")

        def _live_loop():
            for _ in range(3):
                t, mk = _build_log_msg(p, "all")
                try:
                    bot.edit_message_text(t, call.message.chat.id, call.message.message_id, reply_markup=mk, parse_mode="HTML")
                except Exception:
                    pass
                time.sleep(5)
        run_bg(_live_loop)
        return
    bot.answer_callback_query(call.id)
    txt, m = _build_log_msg(p, mode)
    _show(call, txt, m)


# ============================================================
# فتح الموقع
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("openweb_"))
def cb_openweb(call):
    pid = _parse_pid(call.data, "openweb_")
    p = get_project(pid) if pid is not None else None
    if not p or not can_access_project(p, call.from_user.id) or not p[12]:
        bot.answer_callback_query(call.id, "❌ لا يوجد رابط", show_alert=True)
        return
    m = types.InlineKeyboardMarkup()
    m.add(btn_blue("🌐 فتح الموقع", url=p[12]))
    m.add(btn_gray("🔙 رجوع", f"proj_{pid}"))
    bot.send_message(call.message.chat.id, f"🔗 <b>رابط موقعك:</b>\n\n<code>{esc(p[12])}</code>",
                     reply_markup=m, parse_mode="HTML")
    bot.answer_callback_query(call.id)


# ============================================================
# فحص
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("check_"))
def cb_check(call):
    pid = _parse_pid(call.data, "check_")
    p = _owned_project(call, pid)
    if not p:
        return
    bot.answer_callback_query(call.id, "🔍 جاري الفحص...")

    def go():
        try:
            fp = safe_join(p[4], p[5])
            ok, msg_ = check_any_file(fp)
        except Exception as e:
            ok, msg_ = False, f"❌ تعذّر الفحص: {esc(str(e)[:150])}"
        mk = types.InlineKeyboardMarkup()
        mk.row(btn_blue("🧠 تشخيص شامل", f"pdiag_{pid}"), btn_gray("🔙 رجوع", f"pset_{pid}"))
        bot.send_message(call.message.chat.id, msg_, reply_markup=mk, parse_mode="HTML")
    run_bg(go)


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


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_pkg_"))
def do_addpkg(msg_):
    uid = msg_.from_user.id
    try:
        pid = int(user_states[uid].replace("waiting_pkg_", ""))
    except Exception:
        user_states.pop(uid, None)
        return
    pkg = (msg_.text or "").strip()
    if not pkg:
        bot.reply_to(msg_, "❌ أرسل اسم المكتبة كنص (مثال: requests)")
        return
    user_states.pop(uid, None)
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        bot.reply_to(msg_, "🚫 غير مصرح")
        return
    if not valid_pip_spec(pkg):
        bot.reply_to(msg_, "❌ صيغة غير مسموحة. اكتب اسم الحزمة فقط، مثل <code>requests</code> أو "
                           "<code>requests==2.31.0</code> (الخيارات والروابط مرفوضة لأمن الاستضافة).", parse_mode="HTML")
        return
    msg_wait = bot.reply_to(msg_, f"⏳ جاري تثبيت: <code>{esc(pkg)}</code>...", parse_mode="HTML")

    def do():
        ok, m = install_single_pkg(pid, pkg)
        txt = f"<b>✅ تم التثبيت</b>\n\n{m}" if ok else f"<b>❌ فشل التثبيت</b>\n\n{m}"
        try:
            bot.edit_message_text(txt, msg_wait.chat.id, msg_wait.message_id, parse_mode="HTML")
        except Exception:
            bot.send_message(msg_.chat.id, txt, parse_mode="HTML")
        pstate_set(pid, install_status="ok" if ok else "partial", install_detail=f"{pkg}: {'تم' if ok else 'فشل'}")
        log_action(uid, "install_pkg", f"pid={pid} pkg={pkg} ok={ok}")
    run_bg(do)


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
    bot.answer_callback_query(call.id)
    txt = msg("help_text", developer=DEVELOPER_USERNAME)
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_gray("🔙 رجوع", "main_menu"))
    _show(call, txt, m)


# ============================================================
# التواصل مع الدعم
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "contact_support")
def cb_contact_support(call):
    uid = call.from_user.id
    bot.answer_callback_query(call.id)
    user_states[uid] = "waiting_support_msg"
    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ إلغاء", "main_menu"))
    _show(call, msg("support_text"), m)


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_support_msg")
def do_support_msg(msg_):
    uid = msg_.from_user.id
    text = (msg_.text or msg_.caption or "").strip()
    if not text:
        bot.reply_to(msg_, "📝 أرسل رسالتك كنص من فضلك.")
        return
    user = get_user(uid)
    name = user[2] if user else "مستخدم"
    user_states.pop(uid, None)
    support_txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>💬 رسالة دعم جديدة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>من:</b> {esc(name)}\n"
        f"🆔 <b>ID:</b> <code>{uid}</code>\n"
        f"🔗 <b>يوزر:</b> @{esc(msg_.from_user.username or 'None')}\n\n"
        f"📝 <b>الرسالة:</b>\n\n{esc(text[:3000])}"
    )
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, support_txt, parse_mode="HTML")
        except Exception:
            pass
    bot.reply_to(msg_, msg("support_sent_text"), parse_mode="HTML")
    log_action(uid, "support_message", text[:100])
    
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
    m.add(btn_green("🐙 استيراد من GitHub", "new_github"))
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


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_name_"))
def msg_name(msg_):
    uid = msg_.from_user.id
    ptype = user_states[uid].replace("waiting_name_", "")
    name = safe_project_name(msg_.text or "")
    if not name:
        bot.reply_to(msg_, "❌ اسم غير صالح — استخدم حروفاً إنجليزية وأرقاماً و _ - فقط (بدون رموز أو مسافات).")
        return
    projs = get_projects_by_owner(uid)
    if any(p[2] == name for p in projs):
        bot.reply_to(msg_, "⚠️ الاسم موجود بالفعل - اختر اسم تاني")
        return
    user_states[uid] = f"waiting_file_{ptype}_{name}"
    type_name = type_label(ptype)
    txt = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>📤 رفع الملف الرئيسي</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏷️ <b>النوع:</b> {type_name}\n"
        f"✅ <b>الاسم:</b> <code>{esc(name)}</code>\n\n"
        "<b><u>📝 الخطوة 2/3</u></b>\n\n"
        f"📤 ارسل الملف الرئيسي ({'<code>.js</code>' if ptype == 'node' else '<code>.py</code>'})\n\n"
        "💡 المسموح: <code>.py .js .zip</code>"
    )
    bot.reply_to(msg_, txt, parse_mode="HTML")
    
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



# ============================================================
# ✏️ تغيير اسم البوت + 🖼 تغيير صورة البوت (من لوحة المطور)
# ============================================================
def _tg_bot_api(method, json_data=None, data=None, files=None, timeout=25):
    try:
        import requests as req
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
        r = req.post(url, json=json_data, data=data, files=files, timeout=timeout)
        try:
            res = r.json()
        except Exception:
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        if r.status_code == 200 and res.get("ok"):
            return True, res.get("result")
        return False, res.get("description") or f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:200]


@bot.callback_query_handler(func=lambda c: c.data == "admin_bot_name")
def cb_admin_bot_name(call):
    uid = call.from_user.id
    if not is_admin(uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    cur_name = "—"
    ok, res = _tg_bot_api("getMyName", json_data={})
    if ok and isinstance(res, dict) and res.get("name"):
        cur_name = res["name"]
    else:
        try:
            cur_name = bot.get_me().first_name or "—"
        except Exception:
            pass

    user_states[uid] = "waiting_bot_name"
    txt = (
        f"{make_header('✏️ تغيير اسم البوت')}\n\n"
        f"🤖 <b>الاسم الحالي:</b> <code>{html.escape(str(cur_name))}</code>\n\n"
        "📤 أرسل الآن الاسم الجديد للبوت (من 1 إلى 64 حرفاً):"
    )
    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ إلغاء", "admin_panel"))
    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                              reply_markup=m, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id) == "waiting_bot_name")
def do_bot_name(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    new_name = (msg.text or "").strip()
    if not new_name or len(new_name) > 64:
        bot.reply_to(msg, "❌ الاسم يجب أن يكون بين 1 و 64 حرفاً.")
        return

    ok, res = _tg_bot_api("setMyName", json_data={"name": new_name})
    if ok:
        user_states.pop(uid, None)
        log_action(uid, "change_bot_name", new_name)
        m = types.InlineKeyboardMarkup()
        m.add(btn_gray("🔙 رجوع للوحة المطور", "admin_panel"))
        bot.reply_to(
            msg,
            f"✅ <b>تم تغيير اسم البوت بنجاح!</b>\n\n🤖 الاسم الجديد: <code>{html.escape(new_name)}</code>",
            parse_mode="HTML",
            reply_markup=m,
        )
    else:
        bot.reply_to(msg, f"❌ فشل تغيير اسم البوت:\n<code>{html.escape(str(res))}</code>", parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_bot_photo")
def cb_admin_bot_photo(call):
    uid = call.from_user.id
    if not is_admin(uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    user_states[uid] = "waiting_bot_photo"
    txt = (
        f"{make_header('🖼 تغيير صورة البوت')}\n\n"
        "📤 أرسل الآن الصورة الجديدة للبوت:\n"
        "  • صورة مباشرة من المعرض\n"
        "  • أو ملف صورة (JPG / PNG)\n"
        "  • أو فيديو MP4 قصير (حتى 5 ثوانٍ)\n"
    )
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_red("🗑 حذف صورة البوت الحالية", "admin_bot_photo_remove"))
    m.add(btn_gray("❌ إلغاء", "admin_panel"))
    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                              reply_markup=m, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "admin_bot_photo_remove")
def cb_admin_bot_photo_remove(call):
    uid = call.from_user.id
    if not is_admin(uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return

    user_states.pop(uid, None)
    ok, res = _tg_bot_api("removeMyProfilePhoto", json_data={})
    if ok:
        log_action(uid, "remove_bot_photo", "")
        bot.answer_callback_query(call.id, "✅ تم حذف صورة البوت", show_alert=True)
        cb_admin(call)
    else:
        bot.answer_callback_query(call.id, f"❌ فشل الحذف: {str(res)[:120]}", show_alert=True)


@bot.message_handler(
    func=lambda m: user_states.get(m.from_user.id) == "waiting_bot_photo",
    content_types=['photo', 'document', 'animation', 'video']
)
def do_bot_photo(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return

    try:
        is_animated = False
        file_bytes = None

        if msg.photo:
            fi = bot.get_file(msg.photo[-1].file_id)
            file_bytes = bot.download_file(fi.file_path)
        elif msg.animation or msg.video:
            media = msg.animation or msg.video
            if getattr(media, "file_size", 0) and media.file_size > 10 * 1024 * 1024:
                bot.reply_to(msg, "❌ حجم الملف كبير جداً (الحد 10 MB).")
                return
            fi = bot.get_file(media.file_id)
            file_bytes = bot.download_file(fi.file_path)
            is_animated = True
        elif msg.document:
            fname = (msg.document.file_name or "").lower()
            mime = (msg.document.mime_type or "").lower()
            if msg.document.file_size and msg.document.file_size > 10 * 1024 * 1024:
                bot.reply_to(msg, "❌ حجم الملف كبير جداً (الحد 10 MB).")
                return
            fi = bot.get_file(msg.document.file_id)
            raw_bytes = bot.download_file(fi.file_path)
            if fname.endswith(".mp4") or mime == "video/mp4":
                file_bytes = raw_bytes
                is_animated = True
            elif fname.endswith((".jpg", ".jpeg")) or mime in ("image/jpeg", "image/jpg"):
                file_bytes = raw_bytes
            elif fname.endswith((".png", ".webp", ".bmp")) or mime.startswith("image/"):
                try:
                    from PIL import Image
                    im = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
                    buf = io.BytesIO()
                    im.save(buf, format="JPEG", quality=92)
                    file_bytes = buf.getvalue()
                except ImportError:
                    bot.reply_to(msg, "⚠️ أرسل الصورة كصورة عادية (Photo) أو بصيغة JPG.")
                    return
            else:
                bot.reply_to(msg, "❌ صيغة غير مدعومة. أرسل صورة JPG/PNG أو فيديو MP4.")
                return
        else:
            bot.reply_to(msg, "❌ أرسل صورة صالحة.")
            return

        if is_animated:
            payload = {"photo": json.dumps({"type": "animated", "animation": "attach://avatar"})}
            files = {"avatar": ("avatar.mp4", file_bytes, "video/mp4")}
        else:
            payload = {"photo": json.dumps({"type": "static", "photo": "attach://avatar"})}
            files = {"avatar": ("avatar.jpg", file_bytes, "image/jpeg")}

        ok, res = _tg_bot_api("setMyProfilePhoto", data=payload, files=files, timeout=30)
        if ok:
            user_states.pop(uid, None)
            log_action(uid, "change_bot_photo", "animated" if is_animated else "static")
            m = types.InlineKeyboardMarkup()
            m.add(btn_gray("🔙 رجوع للوحة المطور", "admin_panel"))
            bot.reply_to(
                msg,
                "✅ <b>تم تغيير صورة البوت بنجاح!</b>",
                parse_mode="HTML",
                reply_markup=m,
            )
        else:
            bot.reply_to(
                msg,
                f"❌ فشل تغيير صورة البوت:\n<code>{html.escape(str(res))}</code>",
                parse_mode="HTML",
            )
    except Exception as e:
        bot.reply_to(msg, f"❌ خطأ: <code>{html.escape(str(e)[:200])}</code>", parse_mode="HTML")


@bot.message_handler(content_types=['document'],
                     func=lambda m: user_states.get(m.from_user.id, "").startswith(_DOC_STATES))
def handle_doc(msg_):
    uid = msg_.from_user.id
    state = user_states.get(uid, "")
    if is_banned(uid):
        return
    # 1) الملف الرئيسي
    if state.startswith("waiting_file_"):
        parts = state.replace("waiting_file_", "").split("_", 1)
        ptype = parts[0]
        name = safe_project_name(parts[1] if len(parts) > 1 else "proj") or "proj"
        filename = safe_filename(msg_.document.file_name or "") or "unknown"
        ext = os.path.splitext(filename)[1].lower()
        if not is_allowed_file(filename):
            bot.send_message(msg_.chat.id, f"❌ نوع غير مدعوم: <code>{esc(ext)}</code>", parse_mode="HTML")
            return
        if msg_.document.file_size and msg_.document.file_size > MAX_FILE_SIZE:
            bot.send_message(msg_.chat.id, "❌ الملف كبير جدا (الحد 20 MB)", parse_mode="HTML")
            return
        pdir = None
        try:
            fi = bot.get_file(msg_.document.file_id)
            data = bot.download_file(fi.file_path)
            pdir = _unique_project_dir(uid, name)
            os.makedirs(pdir)
            if ext == ".zip":
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as z:
                        safe_extract_zip(z, pdir)
                    flatten_single_top_dir(pdir)
                    all_files = list_dir_files(pdir)
                    det_ptype, entry = detect_entry_and_type(pdir, ptype)
                    if not entry:
                        bot.send_message(msg_.chat.id, "❌ لم يتم العثور على ملف تشغيل صالح (.py أو .js أو package.json) في الـ ZIP",
                                         parse_mode="HTML")
                        shutil.rmtree(pdir, ignore_errors=True)
                        return
                    ptype = det_ptype or ptype
                    bot.send_message(msg_.chat.id,
                                     f"📦 تم فك الـ ZIP ({len(all_files)} ملف)\n"
                                     f"🏷️ النوع المكتشف: {type_label(ptype)}\n"
                                     f"🎯 الملف الرئيسي: <code>{esc(entry)}</code>", parse_mode="HTML")
                except Exception as e:
                    bot.send_message(msg_.chat.id, f"❌ خطأ ZIP: <code>{esc(str(e)[:250])}</code>", parse_mode="HTML")
                    shutil.rmtree(pdir, ignore_errors=True)
                    return
            else:
                entry = filename
                with open(safe_join(pdir, entry), "wb") as f:
                    f.write(data)
            entry_path = safe_join(pdir, entry)
            ok, chk = check_any_file(entry_path)
            if not ok:
                bot.send_message(msg_.chat.id, f"⚠️ <b>فشل الفحص</b>\n\n{chk}\n\n📤 ارسل ملف معدل", parse_mode="HTML")
                shutil.rmtree(pdir, ignore_errors=True)
                return
            info = extract_bot_info_project(pdir)
            pid = add_project(uid, name, ptype, pdir, entry)
            add_project_file(pid, entry)
            _trusted_now = is_trusted_user(uid)
            set_project_approved(pid, 1 if _trusted_now else 0)
            pstate_set(pid, desired="stopped")
            try:
                backup_project_to_storage(pid)
            except Exception as e:
                print(f"[Storage] {e}")
            try:
                if entry_path.endswith(".py"):
                    info_tok = extract_bot_info(entry_path)
                    if info_tok.get("username"):
                        update_project(pid, bot_token=info_tok.get("token"), bot_username=info_tok.get("username"),
                                       bot_name=info_tok.get("name"))
            except Exception as e:
                print(f"[TokenDetect] {e}")
            if info.get("token"):
                update_project(pid, bot_token=info["token"], bot_username=info["username"] or "", bot_name=info["name"] or "")
            with DB_LOCK:
                conn = _conn()
                conn.execute("UPDATE users SET total_uploads = total_uploads + 1 WHERE user_id=?", (uid,))
                conn.commit()
                conn.close()
            log_action(uid, "upload_file", f"pid={pid} file={entry}")
            if not _trusted_now:
                notify_admins_project_approval(pid, uid, msg_.from_user, entry_path)
            txt = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "  <b>✅ تم رفع الملف</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 <b>المشروع:</b> <code>{esc(name)}</code>\n"
                f"📄 <b>الملف:</b> <code>{esc(entry)}</code>\n"
                f"📊 <b>الحجم:</b> <code>{get_file_size_str(entry_path)}</code>\n"
                f"🔍 <b>الفحص:</b> ✅ سليم\n\n"
            )
            if info.get("username"):
                txt += ("<b><u>🤖 معلومات البوت:</u></b>\n"
                        f"  👤 اليوزر: @{esc(info['username'])}\n"
                        f"  🔗 الرابط: https://t.me/{esc(info['username'])}\n\n")
            elif not info.get("token"):
                txt += "⚠️ <b>تنبيه:</b> لم يتم اكتشاف توكن بوت تيليجرام (BOT_TOKEN) في ملفات المشروع.\n\n"
            if not _trusted_now:
                txt += "⏳ <b>حالة الموافقة:</b> تم إرسال الملف للإدارة للمراجعة، ولن يتم تشغيله حتى تتم الموافقة عليه.\n\n"
            txt += ("━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "<b><u>📝 الخطوة التالية:</u></b>\n\n"
                    "هل عندك ملف <code>requirements.txt</code>؟")
            user_states[uid] = f"waiting_req_{pid}"
            m = types.InlineKeyboardMarkup()
            m.row(btn_green("✅ نعم، سأرفعه", f"upload_req_{pid}"), btn_blue("⏭️ تخطي", f"proj_{pid}"))
            bot.send_message(msg_.chat.id, txt, parse_mode="HTML", reply_markup=m)
        except Exception as e:
            print(f"[Upload] {e}\n{traceback.format_exc()[-400:]}")
            # لا نحذف المجلد إلا إذا أنشأناه نحن في هذه العملية ولم يُسجَّل كمشروع
            if pdir and os.path.isdir(pdir) and not any(p[4] == pdir for p in get_projects_by_owner(uid)):
                shutil.rmtree(pdir, ignore_errors=True)
            bot.send_message(msg_.chat.id, f"❌ خطأ: <code>{esc(str(e)[:300])}</code>", parse_mode="HTML")

    # 2) requirements.txt
    elif state.startswith("waiting_req_"):
        try:
            pid = int(state.replace("waiting_req_", ""))
        except Exception:
            return
        p = get_project(pid)
        if not p or not can_access_project(p, uid):
            bot.reply_to(msg_, "🚫 غير مصرح")
            return
        if (msg_.document.file_size or 0) > 200 * 1024:
            bot.reply_to(msg_, "❌ ملف المتطلبات كبير جداً (الحد 200KB)")
            return
        try:
            fi = bot.get_file(msg_.document.file_id)
            data = bot.download_file(fi.file_path)
            raw = data.decode("utf-8", "ignore")
            good, bad = sanitize_requirements(raw)
            if not raw.strip() or not (good or bad):
                bot.send_message(msg_.chat.id, "⚠️ الملف فاضي")
                return
            with open(os.path.join(p[4], "requirements.txt"), "w", encoding="utf-8") as f:
                f.write(raw)
            reqs = "\n".join(good)
            update_project(pid, requirements=reqs)
            add_project_file(pid, "requirements.txt")
            user_states.pop(uid, None)
            txt = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "  <b>📋 requirements.txt</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 <b>عدد المكتبات:</b> <code>{len(good)}</code>\n\n"
                f"<pre>{esc(reqs[:600])}</pre>\n\n"
            )
            if bad:
                txt += ("🛡️ <b>أسطر مرفوضة لأمن الاستضافة ({0}):</b>\n<pre>{1}</pre>\n"
                        "<i>المسموح: اسم الحزمة مع إصدار اختياري فقط (بدون خيارات pip أو روابط).</i>\n\n").format(
                    len(bad), esc("\n".join(bad[:8])))
            txt += ("━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "<b><u>⚙️ الخطوة التالية:</u></b>\n\n"
                    "هل تريد تثبيت المكتبات الان؟")
            m = types.InlineKeyboardMarkup()
            m.row(btn_green("⚙️ نعم، ثبّت", f"install_{pid}"), btn_blue("⏭️ تخطي", f"proj_{pid}"))
            bot.send_message(msg_.chat.id, txt, parse_mode="HTML", reply_markup=m)
        except Exception as e:
            bot.reply_to(msg_, f"❌ خطأ: <code>{esc(str(e)[:250])}</code>", parse_mode="HTML")

    # 3) ملف اضافي
    elif state.startswith("waiting_addfile_"):
        try:
            pid = int(state.replace("waiting_addfile_", ""))
        except Exception:
            return
        p = get_project(pid)
        if not p or not can_access_project(p, uid):
            bot.reply_to(msg_, "🚫 غير مصرح")
            return
        filename = safe_filename(msg_.document.file_name or "")
        if not filename or not is_allowed_file(filename):
            bot.send_message(msg_.chat.id, f"❌ نوع غير مدعوم: <code>{esc(filename or '?')}</code>", parse_mode="HTML")
            return
        if msg_.document.file_size and msg_.document.file_size > MAX_FILE_SIZE:
            bot.send_message(msg_.chat.id, "❌ الملف كبير جدا (الحد 20 MB)", parse_mode="HTML")
            return
        try:
            if dir_size(p[4], limit=MAX_PROJECT_MB * 1024 * 1024 * 2) > MAX_PROJECT_MB * 1024 * 1024:
                bot.send_message(msg_.chat.id, f"❌ حجم المشروع يتجاوز حد الاستضافة ({MAX_PROJECT_MB}MB).", parse_mode="HTML")
                return
            fi = bot.get_file(msg_.document.file_id)
            data = bot.download_file(fi.file_path)
            fp = safe_join(p[4], filename)
            with open(fp, "wb") as f:
                f.write(data)
            ok, chk = check_any_file(fp)
            add_project_file(pid, filename)
            user_states.pop(uid, None)
            _trusted_add = is_trusted_user(uid)
            if not _trusted_add:
                if is_running(pid):
                    stop_project(pid, reason="admin")
                set_project_approved(pid, 0)
                notify_admins_project_approval(pid, uid, msg_.from_user, fp, extra_filename=filename)
            txt = ("<b>✅ تم اضافة الملف</b>\n\n"
                   f"📄 الملف: <code>{esc(filename)}</code>\n"
                   f"📊 الحجم: <code>{get_file_size_str(fp)}</code>\n\n")
            txt += "🔍 الفحص: ✅ سليم" if ok else f"⚠️ <b>تحذير:</b>\n{chk}"
            if not _trusted_add:
                txt += "\n\n⏳ <b>تنبيه:</b> تم إرسال الملف المضاف للإدارة للمراجعة قبل التشغيل."
            m = types.InlineKeyboardMarkup()
            m.add(btn_gray("🔙 رجوع", f"proj_{pid}"))
            bot.send_message(msg_.chat.id, txt, parse_mode="HTML", reply_markup=m)
            log_action(uid, "add_file", f"pid={pid} file={filename}")
        except Exception as e:
            bot.reply_to(msg_, f"❌ خطأ: <code>{esc(str(e)[:250])}</code>", parse_mode="HTML")


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
    pid = _parse_pid(call.data, "cdel_")
    p = _owned_project(call, pid)
    if not p:
        return
    bot.answer_callback_query(call.id, "🗑 جاري الحذف...")

    def go():
        stop_project(pid, reason="delete")
        if p[4] and is_inside(PROJECTS_DIR, p[4]) and os.path.realpath(p[4]) != os.path.realpath(PROJECTS_DIR):
            try:
                shutil.rmtree(p[4], ignore_errors=True)
            except Exception:
                pass
        delete_project(pid)
        log_action(call.from_user.id, "delete_project", f"pid={pid} name={p[2]}")
        call.data = "my_projs"
        cb_my_projs(call)
    run_bg(go)


# ============================================================
# لوحة المطور
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "admin_panel")
def cb_admin(call):
    uid = call.from_user.id
    if not is_admin(uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return
    if user_states.get(uid) in ("waiting_bot_name", "waiting_bot_photo"):
        user_states.pop(uid, None)

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
    m.row(btn_green("💾 مركز النسخ الاحتياطي", "admin_bkc"), btn_blue("💳 المدفوعات والباقات", "admin_billing"))
    m.row(btn_blue("📋 سجل الاحداث", "admin_logs"), btn_green("🔓 فك حظر", "admin_unban"))
    m.row(btn_green("✅ مستخدمين بدون موافقة", "admin_trusted_users"))
    mode_lbl = "💰 الوضع: مدفوع 💎" if is_paid_mode() else "🆓 الوضع: مجاني"
    m.row(btn_green("🎨 تخصيص البوت (أزرار + نصوص + نسخ)", "admin_premium"))
    m.row(btn_blue("✏️ تغيير اسم البوت", "admin_bot_name"), btn_blue("🖼 تغيير صورة البوت", "admin_bot_photo"))
    m.row(btn_blue("💽 نسخة DB كاملة", "admin_backup_db"), btn_blue("📥 استعادة DB كاملة", "admin_restore_db"))
    m.row(btn_blue("🔁 إعادة تشغيل الكل", "admin_restart_all"), btn_red("♻️ إعادة تشغيل الاستضافة", "admin_restart_host"))
    m.row(btn(f"{'▶️ تشغيل الاستضافة' if host_paused() else '⏸ إيقاف الاستضافة'}", "admin_toggle_pause",
             style="success" if host_paused() else "danger"))
    m.row(btn_green("🎯 التخصيص المباشر", "dx_toggle"), btn_blue("📤 سحب ملفات مستخدمين", "pull_0"))
    m.row(btn_green("🩺 مراقبة المشاريع", "admin_mon_0"), btn_blue("📖 دليل الأدمن", "adm_guide"))
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
    bot.answer_callback_query(call.id)
    txt = ("━━━━━━━━━━━━━━━━━━━━━━━━━\n  <b>📝 تخصيص النصوص والرسائل</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
           "اختر النص الذي تريد تعديله. النصوص المعدّلة تظهر بعلامة ✏️ وتبقى محفوظة بعد إعادة التشغيل.")
    m = types.InlineKeyboardMarkup(row_width=1)
    for key, label in UI_TEMPLATES.items():
        mark = "✏️ " if ui_has_custom(key) else "📝 "
        m.add(btn_blue(f"{mark}{label}", f"ui_edit_{key}"))
    m.add(btn_red("♻️ استعادة كل الرسائل الافتراضية", "ui_reset_all"))
    m.add(btn_gray("🔙 رجوع", "admin_premium"))
    _show(call, txt, m)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ui_edit_"))
def cb_ui_edit(call):
    if not is_admin(call.from_user.id):
        return
    key = call.data.replace("ui_edit_", "")
    if key not in UI_TEMPLATES:
        bot.answer_callback_query(call.id, "❌ مفتاح غير معروف", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    user_states[call.from_user.id] = f"waiting_ui_{key}"
    current = get_ui(key)
    ph = MSG_DEFS.get(key, ("", "", ""))[2]
    txt = (f"<b>✏️ تعديل:</b> <code>{esc(key)}</code>\n"
           + (f"🔣 <b>المتغيرات المتاحة:</b> <code>{esc(ph)}</code>\n" if ph else "")
           + f"\n📄 القيمة الحالية:\n<pre>{esc(current)}</pre>\n\n"
           "📤 أرسل القيمة الجديدة (يدعم التنسيق HTML والإيموجي المميز).")
    m = types.InlineKeyboardMarkup(row_width=1)
    if ui_has_custom(key) and key in MSG_DEFS:
        m.add(btn_red("♻️ استعادة النص الافتراضي", f"ui_reset_{key}"))
    m.add(btn_red("❌ الغاء", "admin_ui"))
    _show(call, txt, m)


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, "").startswith("waiting_ui_"))
def do_ui_edit(msg_):
    if not is_admin(msg_.from_user.id):
        return
    key = user_states[msg_.from_user.id].replace("waiting_ui_", "")
    value = _entities_to_html(msg_.text or "", msg_.entities).strip()
    if not value:
        bot.reply_to(msg_, "❌ القيمة فاضية")
        return
    if len(value) > 3500:
        bot.reply_to(msg_, "❌ النص طويل جداً.")
        return
    if key in MSG_DEFS or key in ("welcome_text", "maintenance_text", "smart_install_intro", "storage_intro"):
        ok, why = validate_telegram_html(value)
        if not ok:
            bot.reply_to(msg_, f"❌ تنسيق HTML غير صالح: {esc(why)}\nصحّحه وأرسله مجدداً.", parse_mode="HTML")
            return
    set_ui(key, value)
    user_states.pop(msg_.from_user.id, None)
    m = types.InlineKeyboardMarkup(row_width=1)
    if key in MSG_DEFS:
        m.add(btn_red("♻️ استعادة النص الافتراضي", f"ui_reset_{key}"))
    m.add(btn_gray("🔙 قائمة النصوص", "admin_ui"))
    bot.reply_to(msg_, f"✅ تم تحديث <code>{esc(key)}</code>", parse_mode="HTML", reply_markup=m)


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
# ✅ نظام موافقة الأدمن على تشغيل الملفات + المستخدمين بدون موافقة
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith(("adm_proj_ok_", "adm_proj_no_")))
def cb_adm_proj_approval(call):
    uid = call.from_user.id
    if not is_admin(uid):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        return
    approve = call.data.startswith("adm_proj_ok_")
    try:
        pid = int(call.data.rsplit("_", 1)[1])
    except Exception:
        return
    p = get_project(pid)
    if not p:
        bot.answer_callback_query(call.id, "❌ المشروع غير موجود", show_alert=True)
        return

    chat_id = call.message.chat.id
    mid = call.message.message_id

    if not approve:
        set_project_approved(pid, -1)
        stop_project(pid)
        log_action(uid, "reject_project", f"pid={pid} owner={p[1]}")
        bot.answer_callback_query(call.id, "🛑 تم إيقاف / رفض التشغيل")
        status_line = f"🛑 <b>الحالة:</b> تم الإيقاف / الرفض بواسطة الإدارة\\n📦 <b>المشروع:</b> <code>{html.escape(p[2])}</code>"
        m = types.InlineKeyboardMarkup()
        m.add(btn_green("✅ قبول التشغيل", f"adm_proj_ok_{pid}"))
        try:
            if call.message.document:
                bot.edit_message_caption(status_line, chat_id, mid, parse_mode="HTML", reply_markup=m)
            else:
                bot.edit_message_text(status_line, chat_id, mid, parse_mode="HTML", reply_markup=m)
        except Exception:
            bot.send_message(chat_id, status_line, parse_mode="HTML", reply_markup=m)
        try:
            bot.send_message(p[1], f"🛑 تم إيقاف / رفض تشغيل مشروعك <b>{html.escape(p[2])}</b> من قبل الإدارة.", parse_mode="HTML")
        except Exception:
            pass
        return

    set_project_approved(pid, 1)
    log_action(uid, "approve_project", f"pid={pid} owner={p[1]}")
    bot.answer_callback_query(call.id, "✅ تم القبول وجاري التشغيل...")

    def _run_approved():
        ok, res_msg, info = start_project(pid)
        m = types.InlineKeyboardMarkup()
        m.add(btn_red("🛑 إيقاف التشغيل", f"adm_proj_no_{pid}"))
        if ok:
            status_line = f"✅ <b>الحالة:</b> تم القبول والتشغيل بنجاح\\n📦 <b>المشروع:</b> <code>{html.escape(p[2])}</code> (PID: <code>{info.get('pid') if info else '-'}</code>)"
            try:
                bot.send_message(p[1], f"✅ تمت الموافقة على مشروعك <b>{html.escape(p[2])}</b> وتم تشغيله بنجاح!", parse_mode="HTML")
            except Exception:
                pass
        else:
            status_line = f"✅ <b>الحالة:</b> تمت الموافقة على المشروع، لكن تعذّر التشغيل التلقائي:\\n{res_msg[:400]}"
            try:
                bot.send_message(p[1], f"✅ تمت الموافقة على مشروعك <b>{html.escape(p[2])}</b>! يمكنك الآن تشغيله أو فحص السجلات من قائمة مشاريعك.", parse_mode="HTML")
            except Exception:
                pass
        try:
            if call.message.document:
                bot.edit_message_caption(status_line, chat_id, mid, parse_mode="HTML", reply_markup=m)
            else:
                bot.edit_message_text(status_line, chat_id, mid, parse_mode="HTML", reply_markup=m)
        except Exception:
            bot.send_message(chat_id, status_line, parse_mode="HTML", reply_markup=m)

    threading.Thread(target=_run_approved, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == "admin_trusted_users")
def cb_admin_trusted(call):
    if not is_admin(call.from_user.id):
        return
    user_states.pop(call.from_user.id, None)
    trusted = get_all_trusted_users()
    lines = [
        make_header("✅ مستخدمين بدون موافقة"),
        "",
        "الأعضاء في هذه القائمة تعمل ملفاتهم على الاستضافة مباشرة بدون انتظار موافقة الأدمن:",
        "",
    ]
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_green("➕ إضافة عضو (آيدي أو يوزر)", "adm_add_trusted"))
    if not trusted:
        lines.append("📭 لا يوجد أعضاء مضافون حالياً.")
    else:
        for t_uid, t_uname, t_fname, _, t_at in trusted[:25]:
            u_db = get_user(t_uid)
            disp_name = t_fname or (u_db[2] if u_db and u_db[2] else "")
            disp_user = t_uname or (u_db[1] if u_db and u_db[1] else "")
            tag = f" (@{disp_user})" if disp_user else ""
            lines.append(f"• <code>{t_uid}</code> {html.escape(disp_name)}{tag}")
            m.add(btn_red(f"🗑 إزالة {t_uid} {disp_name[:15]}".strip(), f"adm_del_trusted_{t_uid}"))
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    txt = "\\n".join(lines)
    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "adm_add_trusted")
def cb_adm_add_trusted(call):
    if not is_admin(call.from_user.id):
        return
    user_states[call.from_user.id] = "waiting_trusted_user"
    m = types.InlineKeyboardMarkup()
    m.add(btn_red("❌ إلغاء", "admin_trusted_users"))
    txt = (
        f"{make_header('➕ إضافة عضو بدون موافقة')}\\n\\n"
        "📤 أرسل الآن <b>آيدي المستخدم</b> (مثال: <code>123456789</code>)\\n"
        "أو <b>اليوزر</b> (مثال: <code>@username</code>) أو قم بتحويل رسالة منه:"
    )
    bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")


@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id) == "waiting_trusted_user")
def do_add_trusted(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    target_id = None
    uname = ""
    fname = ""
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
                uname = u_db[1] or ""
                fname = u_db[2] or ""
        else:
            found = find_user_by_username(raw)
            if found:
                target_id, uname, fname = found[0], found[1] or "", found[2] or ""
            else:
                bot.reply_to(
                    msg,
                    "❌ لم يتم العثور على هذا اليوزر في قاعدة البيانات (يجب أن يكون قد بدأ البوت مسبقاً، أو أرسل <b>الآيدي الرقمي</b> مباشرة).",
                    parse_mode="HTML",
                )
                return

    user_states.pop(uid, None)
    add_trusted_user(target_id, uname, fname, added_by=uid)
    log_action(uid, "add_trusted_user", f"target={target_id}")
    m = types.InlineKeyboardMarkup()
    m.add(btn_gray("🔙 رجوع للقائمة", "admin_trusted_users"))
    bot.reply_to(
        msg,
        f"✅ تم تفعيل الاستضافة بدون موافقة للعضو <code>{target_id}</code> {html.escape(fname)}.",
        parse_mode="HTML",
        reply_markup=m,
    )
    try:
        bot.send_message(target_id, "🎉 تم تفعيل التشغيل المباشر لحسابك! يمكنك الآن رفع وتشغيل ملفاتك بدون انتظار موافقة الإدارة.")
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_del_trusted_"))
def cb_adm_del_trusted(call):
    if not is_admin(call.from_user.id):
        return
    try:
        target_id = int(call.data[len("adm_del_trusted_"):])
    except Exception:
        return
    remove_trusted_user(target_id)
    log_action(call.from_user.id, "remove_trusted_user", f"target={target_id}")
    bot.answer_callback_query(call.id, "✅ تم إزالة العضو")
    cb_admin_trusted(call)



# ============================================================
# 🐙 الاستيراد المباشر من GitHub + 💾 إدارة الجلسات + 📊 مراقبة السيرفر
# ============================================================
@bot.callback_query_handler(func=lambda c: c.data == "new_github")
def cb_new_github(call):
    uid = call.from_user.id
    ok, count, limit = check_user_limit(uid)
    if not ok:
        return bot.answer_callback_query(call.id, f"⚠️ وصلت للحد الأقصى ({count}/{limit})", show_alert=True)
    user_states[uid] = "waiting_github_repo"
    m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", "main_menu"))
    bot.edit_message_text(
        f"{make_header('🐙 استيراد مشروع من GitHub')}\n\n"
        "أرسل رابط المستودع العام الآن (مثال: <code>https://github.com/user/repo</code> أو <code>user/repo</code>).\n\n"
        "⚡ سيتم سحب الكود واكتشاف Python أو Node.js والمكتبات والتوكن تلقائياً.",
        call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id) == "waiting_github_repo")
def do_github_clone(msg):
    uid = msg.from_user.id
    user_states.pop(uid, None)
    repo = (msg.text or "").strip()
    if not repo.startswith("http"):
        repo = f"https://github.com/{repo.lstrip('/')}"
    if not repo.startswith("https://github.com/"):
        return bot.reply_to(msg, "❌ يرجى إرسال رابط GitHub صحيح.")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", repo.rstrip("/").split("/")[-1].replace(".git", ""))[:30] or "github_proj"
    pdir = os.path.join(PROJECTS_DIR, f"{uid}_{name}_{secrets.token_hex(2)}")
    wait = bot.reply_to(msg, "🐙 جاري سحب المستودع عبر <code>git clone</code>...", parse_mode="HTML")
    try:
        res = subprocess.run(["git", "clone", "--depth", "1", repo, pdir], capture_output=True, text=True, timeout=90)
        if res.returncode != 0:
            shutil.rmtree(pdir, ignore_errors=True)
            return bot.edit_message_text(f"❌ فشل الاستنساخ:\n<pre>{html.escape(res.stderr[:400])}</pre>", msg.chat.id, wait.message_id, parse_mode="HTML")
        ptype, entry = detect_entry_and_type(pdir)
        if not entry:
            shutil.rmtree(pdir, ignore_errors=True)
            return bot.edit_message_text("❌ لم يتم العثور على ملف تشغيل (.py أو .js) في المستودع.", msg.chat.id, wait.message_id)
        pid = add_project(uid, name, ptype, pdir, entry)
        add_project_file(pid, entry)
        info = extract_bot_info_project(pdir)
        if info.get("token"):
            update_project(pid, bot_token=info["token"], bot_username=info.get("username") or "", bot_name=info.get("name") or "")
        trusted = is_trusted_user(uid)
        set_project_approved(pid, 1 if trusted else 0)
        if not trusted:
            notify_admins_project_approval(pid, uid, msg.from_user, os.path.join(pdir, entry))
        m = types.InlineKeyboardMarkup(row_width=1)
        m.add(btn_green("▶️ فتح لوحة المشروع", f"proj_{pid}"))
        bot.edit_message_text(
            f"✅ <b>تم استيراد المشروع من GitHub!</b>\n\n"
            f"📦 الاسم: <code>{name}</code>\n"
            f"🏷️ النوع: {type_label(ptype)}\n"
            f"📄 الملف الرئيسي: <code>{entry}</code>\n"
            + ("⏳ بانتظار موافقة المطور للتشغيل." if not trusted else "🚀 جاهز للتشغيل الفوري!"),
            msg.chat.id, wait.message_id, reply_markup=m, parse_mode="HTML"
        )
    except Exception as e:
        shutil.rmtree(pdir, ignore_errors=True)
        bot.edit_message_text(f"❌ خطأ: <code>{html.escape(str(e)[:250])}</code>", msg.chat.id, wait.message_id, parse_mode="HTML")

@bot.callback_query_handler(func=lambda c: c.data.startswith(("sess_save_", "sess_reset_")))
def cb_session_manage(call):
    uid = call.from_user.id
    is_reset = call.data.startswith("sess_reset_")
    pid = int(call.data.split("_")[-1])
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
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
            return bot.answer_callback_query(call.id, "⚠️ لم يتم العثور على ملفات جلسة", show_alert=True)
        bot.answer_callback_query(call.id, f"💾 تم حفظ {cnt} ملف جلسة!")
        with open(zpath, "rb") as f:
            bot.send_document(call.message.chat.id, f, caption=f"💾 نسخة جلسة {p[2]}", visible_file_name=f"{p[2]}_session.zip")
    else:
        stop_project(pid)
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
        bot.answer_callback_query(call.id, f"🔄 تم إعادة تعيين الجلسة ({removed} عنصر)", show_alert=True)

@bot.callback_query_handler(func=lambda c: c.data == "server_monitor")
def cb_server_monitor(call):
    uid = call.from_user.id
    bot.answer_callback_query(call.id)
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
        "<blockquote><b>📊 مراقبة السيرفر الحية</b></blockquote>\n"
        f"<blockquote>{box}</blockquote>\n"
        f"<blockquote>🟢 النشطة: <code>{len(run)}/{len(projs)}</code>\n⏱ التشغيل: <code>{up}</code></blockquote>"
    )
    if run:
        lines = []
        for p in run[:8]:
            cpu, mem = get_proc_metrics(p[14])
            lines.append(f"• <b>{esc(p[2])}</b> — RAM <code>{mem} MB</code> · CPU <code>{cpu}%</code>")
        txt += "\n<blockquote expandable>" + "\n".join(lines) + "</blockquote>"
    txt += f"\n<i>🛡 حد كل مشروع: {MAX_RAM_MB}MB رام</i>"
    m = types.InlineKeyboardMarkup()
    m.row(btn_blue("🔄 تحديث", "server_monitor"), btn_gray("🔙 رجوع", "main_menu"))
    _show(call, txt, m)

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



@web.route('/api/server_stats')
def api_server_stats():
    """الأرقام التفصيلية تتطلب رمز لوحة صالح؛ بدونه يُرجع حالة عامة فقط."""
    uid = get_dashboard_user(request.args.get("token"))
    if not uid:
        return jsonify({"status": "OK", "uptime": calc_uptime(START_TIME.strftime('%Y-%m-%d %H:%M:%S'))}), 200
    sm = get_server_metrics()
    projs = get_all_projects() if is_admin(uid) else get_projects_by_owner(uid)
    sm["running"] = sum(1 for pr in projs if is_running(pr[0]))
    sm["total"] = len(projs)
    return jsonify(sm), 200

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
button.btn{border:0;cursor:pointer;font:inherit}.inl{display:inline}
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
        <div class="meta-row"><span>مدة التشغيل</span><span>{{ uptime(p[17]) if running_map[p[0]] else '—' }}</span></div>
        <div class="meta-row"><span>آخر تشغيل</span><span>{{ info_map[p[0]].last_start }}</span></div>
        <div class="meta-row"><span>آخر توقف</span><span>{{ info_map[p[0]].last_stop }}</span></div>
        {% if info_map[p[0]].reason %}<div class="meta-row"><span>سبب التوقف</span><span>{{ info_map[p[0]].reason }}</span></div>{% endif %}
        <div class="meta-row"><span>المراقبة</span><span>{{ info_map[p[0]].monitor }}</span></div>
        <div class="meta-row"><span>إعادات التشغيل</span><span>{{ info_map[p[0]].restarts }}</span></div>
        <div class="meta-row"><span>المكتبات</span><span>{{ info_map[p[0]].install }}</span></div>
        <div class="meta-row"><span>آخر نسخة احتياطية</span><span>{{ info_map[p[0]].backup }}</span></div>
        {% if info_map[p[0]].last_error %}<div class="meta-row"><span>آخر خطأ</span><code style="max-width:60%;overflow:hidden;text-overflow:ellipsis">{{ info_map[p[0]].last_error }}</code></div>{% endif %}
        {% if p[12] %}<div class="meta-row"><span>الرابط</span><a href="{{ p[12] }}" target="_blank" style="color:#60a5fa;text-decoration:none">فتح ↗</a></div>{% endif %}
      </div>
      <div class="actions">
        {% if running_map[p[0]] %}
        <form method="post" action="/stop/{{ p[0] }}" class="inl"><input type="hidden" name="token" value="{{ token }}"><button class="btn btn-x">⏹ إيقاف</button></form>
        <form method="post" action="/restart/{{ p[0] }}" class="inl"><input type="hidden" name="token" value="{{ token }}"><button class="btn btn-s">🔁 إعادة تشغيل</button></form>
        {% else %}
        <form method="post" action="/start/{{ p[0] }}" class="inl"><input type="hidden" name="token" value="{{ token }}"><button class="btn btn-s">▶ تشغيل</button></form>
        {% endif %}
        <a href="/logs/{{ p[0] }}?token={{ token }}" class="btn btn-l">📄 السجلات</a>
        <a href="/diag/{{ p[0] }}?token={{ token }}" class="btn btn-l">🧠 تشخيص</a>
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
        <h1>🔒 الرابط غير صالح أو انتهت صلاحيته</h1>
        <p>افتح الرابط من البوت من جديد</p>
        </body>
        """, 403
    projs = get_projects_by_owner(uid)
    running_map = {p[0]: is_running(p[0]) for p in projs}
    info_map = {p[0]: web_project_info(p) for p in projs}
    running = sum(1 for v in running_map.values() if v)
    u = get_user(uid)
    refs = u[11] if u and len(u) > 11 and u[11] else 0
    return render_template_string(
        WEB_HTML,
        title=get_ui("dashboard_title"),
        projects=projs, total=len(projs),
        running=running, stopped=len(projs) - running,
        running_map=running_map, info_map=info_map, uptime=calc_uptime,
        token=token,
        balance=f"{get_balance(uid):.2f}",
        currency=get_ui("currency_name") or "$",
        paid_mode=is_paid_mode(),
        referrals=refs
    )


@web.route('/start/<int:pid>', methods=['GET', 'POST'])
def w_start(pid):
    return _web_action(pid, "تشغيل", lambda i: start_project(i), need_approval=True)


@web.route('/stop/<int:pid>', methods=['GET', 'POST'])
def w_stop(pid):
    return _web_action(pid, "إيقاف", lambda i: stop_project(i))


@web.route('/logs/<int:pid>')
def w_logs(pid):
    uid, p, token = _web_auth(pid)
    if uid is None:
        return p
    mode = request.args.get("mode")
    logs = esc(get_logs(pid, 200, errors_only=(mode == "err")))
    other = ("<a href='?token={t}' style='color:#60a5fa'>كل السجل</a>" if mode == "err"
             else "<a href='?token={t}&mode=err' style='color:#fbbf24'>الأخطاء فقط</a>").format(t=esc(token))
    return ("<body style='background:#0f172a;color:#86efac;font-family:monospace;padding:20px'>"
            f"<pre style='white-space:pre-wrap'>{logs}</pre>"
            f"{other} · <a href='/diag/{pid}?token={esc(token)}' style='color:#c4b5fd'>🧠 تشخيص</a> · "
            f"<a href='/?token={esc(token)}' style='color:#60a5fa'>رجوع</a></body>")


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
# 💾 (v13) نسخ احتياطي موثّق + استعادة آمنة
# ============================================================
# ============================================================
# 💾 نظام النسخ الاحتياطي الموثّق + الاستعادة الآمنة — v13
#  - كل نسخة تُبنى في ملف مؤقت ثم تُفحص (zip + manifest + سلامة القاعدة) قبل اعتمادها.
#  - لا تُحذف نسخة سليمة قديمة إلا بعد نجاح الجديدة.
#  - الاستعادة: فك في مجلد مؤقت ← تحقق ← لقطة أمان للحالة الحالية ← تبديل ذرّي ← تراجع تلقائي عند أي فشل.
# ============================================================
import tempfile
import hashlib

BACKUP_HOURS = float(os.environ.get("BACKUP_HOURS", "2"))
LOCAL_BACKUP_HOURS = float(os.environ.get("LOCAL_BACKUP_HOURS", "24"))
BACKUP_MIN_GAP_HOURS = float(os.environ.get("BACKUP_MIN_GAP_HOURS", "1"))
RESTORE_MAX_MB = _env_num("RESTORE_MAX_MB", 1024)
_BK_LOCK = threading.Lock()
_BK_PLOCKS = {}
_BK_EXCL_DIRS = {VENV_DIRNAME, PYLIBS_DIRNAME, "__pycache__", ".git", "node_modules"}
_BK_EXCL_FILES = {".deps_installed"}
_BK_VOLATILE_EXT = (".log", ".pyc", ".tmp", ".lock", ".pid", ".sock", "-journal", "-wal", "-shm")
TG_UPLOAD_LIMIT = 49 * 1024 * 1024
TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024
DB_ZIP_NAME = "db/host_v12.db"
_REQUIRED_TABLES = {"users", "projects", "ui_settings"}


def _sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _free_bytes(path):
    try:
        return shutil.disk_usage(path).free
    except Exception:
        return 1 << 60


def check_sqlite_file(path):
    """(ok, سبب, counts) — فحص سلامة قاعدة SQLite قبل الاعتماد عليها."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            r = conn.execute("PRAGMA integrity_check").fetchone()
            if not r or str(r[0]).lower() != "ok":
                return False, f"فشل فحص السلامة: {r[0] if r else '?'}", {}
            names = {x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            miss = _REQUIRED_TABLES - names
            if miss:
                return False, f"جداول ناقصة: {', '.join(sorted(miss))}", {}
            counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("users", "projects")}
            return True, "", counts
        finally:
            conn.close()
    except Exception as e:
        return False, f"ليست قاعدة بيانات صالحة: {str(e)[:100]}", {}


def _snapshot_db(dest_path):
    """لقطة متسقة من قاعدة البيانات الحالية (واجهة sqlite backup)."""
    with DB_LOCK:
        src = sqlite3.connect(DB_PATH, timeout=30)
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()


def _bk_files(root=None):
    root = root or PROJECTS_DIR
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _BK_EXCL_DIRS and not os.path.islink(os.path.join(r, d))
                   and ".restore_" not in d and ".old_" not in d]
        for f in files:
            full = os.path.join(r, f)
            if f in _BK_EXCL_FILES or f.endswith(_BK_VOLATILE_EXT) or os.path.islink(full):
                continue
            yield full


def _unsafe_name(n):
    n = n.replace("\\", "/")
    return n.startswith("/") or ".." in n.split("/") or bool(re.match(r"^[A-Za-z]:", n))


# ---------------------------------------------------------------
# نسخ المشروع الواحد
# ---------------------------------------------------------------
def _bk_plock(pid):
    with _PLOCKS_G:
        return _BK_PLOCKS.setdefault(pid, threading.Lock())


def _pbk_dir(pid):
    d = os.path.join(PROJECT_BACKUPS_DIR, str(int(pid)))
    os.makedirs(d, exist_ok=True)
    return d


def verify_project_backup(path):
    """(ok, info|سبب)."""
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad:
                return False, f"ملف تالف داخل النسخة: {bad}"
            names = z.namelist()
            if "manifest.json" not in names:
                return False, "النسخة بلا manifest (غير موثّقة)"
            if any(_unsafe_name(n) for n in names):
                return False, "النسخة تحوي مساراً غير آمن"
            mf = json.loads(z.read("manifest.json").decode("utf-8"))
            files = [n for n in names if n != "manifest.json" and not n.endswith("/")]
            if mf.get("files") is not None and int(mf["files"]) != len(files):
                return False, f"عدد الملفات لا يطابق (المتوقع {mf['files']}، الموجود {len(files)})"
            return True, {"files": len(files), "manifest": mf}
    except zipfile.BadZipFile:
        return False, "الملف ليس أرشيف ZIP صالحاً"
    except Exception as e:
        return False, f"تعذّر فحص النسخة: {str(e)[:100]}"


def list_project_backups(pid):
    out = []
    d = os.path.join(PROJECT_BACKUPS_DIR, str(int(pid)))
    try:
        names = sorted(x for x in os.listdir(d) if x.startswith("bk_") and x.endswith(".zip"))
    except Exception:
        return []
    for n in reversed(names):
        bid = n[3:-4]
        path = os.path.join(d, n)
        try:
            side = {}
            sp = path[:-4] + ".json"
            if os.path.isfile(sp):
                with open(sp, "r", encoding="utf-8") as f:
                    side = json.load(f)
            ts = f"{bid[0:4]}-{bid[4:6]}-{bid[6:8]} {bid[9:11]}:{bid[11:13]}" if len(bid) >= 15 else bid
            out.append({"id": bid, "ts": ts, "size": os.path.getsize(path), "files": side.get("files"),
                        "sha": side.get("sha256", ""), "path": path})
        except Exception:
            continue
    return out


def create_project_backup(pid, reason="manual"):
    """ينشئ نسخة موثّقة لمشروع. يرجع (ok, رسالة, info). لا يحذف شيئاً قبل التحقق من الجديدة."""
    p = get_project(pid)
    if not p:
        return False, "المشروع غير موجود", None
    pdir = p[4]
    if not pdir or not os.path.isdir(pdir) or not is_inside(PROJECTS_DIR, pdir):
        return False, "مجلد المشروع غير موجود", None
    lk = _bk_plock(pid)
    if not lk.acquire(blocking=False):
        return False, "نسخة احتياطية جارية لهذا المشروع بالفعل", None
    tmp = None
    try:
        size_est = dir_size(pdir, limit=MAX_PROJECT_MB * 1024 * 1024 * 3)
        if _free_bytes(DATA_DIR) < size_est * 1.2 + 50 * 1024 * 1024:
            return False, "المساحة الحرة على السيرفر غير كافية لإنشاء نسخة", None
        bid = datetime.now().strftime("%Y%m%d_%H%M%S")
        d = _pbk_dir(pid)
        final = os.path.join(d, f"bk_{bid}.zip")
        tmp = final + ".partial"
        count = 0
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for r, dirs, files in os.walk(pdir):
                dirs[:] = [x for x in dirs if x not in _BK_EXCL_DIRS and not os.path.islink(os.path.join(r, x))
                           and ".restore_" not in x and ".old_" not in x]
                for f in files:
                    full = os.path.join(r, f)
                    if f in _BK_EXCL_FILES or f.endswith(".log") or os.path.islink(full):
                        continue
                    try:
                        z.write(full, os.path.relpath(full, pdir).replace(os.sep, "/"))
                        count += 1
                    except (OSError, ValueError):
                        continue
            manifest = {"version": VERSION, "kind": "project", "pid": pid, "name": p[2], "entry": p[5],
                        "ptype": p[3], "created": now_str(), "files": count, "reason": reason}
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        ok, info = verify_project_backup(tmp)
        if not ok:
            raise RuntimeError(f"فشل التحقق من النسخة الجديدة: {info}")
        sha = _sha256_file(tmp)
        os.replace(tmp, final)
        tmp = None
        with open(final[:-4] + ".json", "w", encoding="utf-8") as f:
            json.dump({"id": bid, "created": manifest["created"], "files": count, "sha256": sha,
                       "size": os.path.getsize(final), "reason": reason}, f)
        # تقليم القديم بعد نجاح الجديدة فقط
        try:
            keep = max(1, int(float(get_ui("proj_backup_keep") or 3)))
        except Exception:
            keep = 3
        for old in list_project_backups(pid)[keep:]:
            for ext in (".zip", ".json"):
                try:
                    os.remove(old["path"][:-4] + ext)
                except Exception:
                    pass
        pstate_set(pid, last_backup_at=now_str())
        add_event(pid, "backup", f"{count} ملف · {fmt_size(os.path.getsize(final))} ({reason})")
        return True, "تم", {"id": bid, "files": count, "size": os.path.getsize(final), "sha": sha, "path": final}
    except Exception as e:
        print(f"[PBackup] {pid}: {e}")
        return False, str(e)[:200], None
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass
        lk.release()


def _prune_trash(keep_per_project=3, keep_full_runs=3, max_age_days=14):
    """يقلّم سلة الاستعادة: آخر 3 نسخ لكل مشروع، وآخر 3 عمليات استعادة كاملة، ويحذف ما تجاوز 14 يوماً."""
    try:
        now = time.time()
        proj_groups, full_stamps, others = {}, {}, []
        for n in os.listdir(TRASH_DIR):
            m = re.match(r"^(p\d+)_", n)
            mf = re.match(r"^full_(\d{8}_\d{6})_", n)
            if m:
                proj_groups.setdefault(m.group(1), []).append(n)
            elif mf:
                full_stamps.setdefault(mf.group(1), []).append(n)
            else:
                others.append(n)

        def _rm(name):
            path = os.path.join(TRASH_DIR, name)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    os.remove(path)
                except Exception:
                    pass
        for _g, names in proj_groups.items():
            names.sort(reverse=True)
            for i, n in enumerate(names):
                if i >= keep_per_project or now - os.path.getmtime(os.path.join(TRASH_DIR, n)) > max_age_days * 86400:
                    _rm(n)
        for i, stamp in enumerate(sorted(full_stamps, reverse=True)):
            if i >= keep_full_runs:
                for n in full_stamps[stamp]:
                    _rm(n)
        for n in others:
            if now - os.path.getmtime(os.path.join(TRASH_DIR, n)) > 2 * 86400:
                _rm(n)
    except Exception:
        pass


def cleanup_stale_dirs():
    """يحذف بقايا استعادة غير مكتملة (مجلدات .restore_ أقدم من ساعة) عند الإقلاع."""
    try:
        for n in os.listdir(PROJECTS_DIR):
            if ".restore_" in n:
                path = os.path.join(PROJECTS_DIR, n)
                if os.path.isdir(path) and time.time() - os.path.getmtime(path) > 3600:
                    shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def restore_project_backup(pid, bid):
    """استعادة آمنة: فك مؤقت ← تحقق ← تبديل ذرّي مع تراجع. يرجع (ok, تفاصيل HTML)."""
    p = get_project(pid)
    if not p:
        return False, "المشروع غير موجود"
    path = os.path.join(_pbk_dir(pid), f"bk_{bid}.zip")
    if not re.match(r"^\d{8}_\d{6}$", str(bid)) or not os.path.isfile(path):
        return False, "النسخة المطلوبة غير موجودة"
    ok, info = verify_project_backup(path)
    if not ok:
        return False, f"النسخة غير سليمة: {esc(info)}"
    side = path[:-4] + ".json"
    try:
        if os.path.isfile(side):
            with open(side, "r", encoding="utf-8") as f:
                want = json.load(f).get("sha256")
            if want and want != _sha256_file(path):
                return False, "بصمة الملف لا تطابق بصمة النسخة (الملف تغيّر أو تلف)"
    except Exception:
        pass
    pdir = os.path.realpath(p[4])
    if not is_inside(PROJECTS_DIR, pdir) or pdir == os.path.realpath(PROJECTS_DIR):
        return False, "مسار المشروع غير آمن"
    lk = _plock(pid)
    if not lk.acquire(timeout=30):
        return False, "عملية أخرى جارية على المشروع"
    stage = f"{pdir}.restore_{int(time.time())}"
    trash = os.path.join(TRASH_DIR, f"p{pid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    was_running = is_running(pid) or pstate_get(pid)["desired"] == "running"
    swapped = False
    try:
        _stop_locked(pid, "restore")
        with zipfile.ZipFile(path) as z:
            extracted = safe_extract_zip(z, stage, max_total_mb=max(MAX_ZIP_TOTAL_MB, MAX_PROJECT_MB))
        mf = os.path.join(stage, "manifest.json")
        if os.path.isfile(mf):
            os.remove(mf)
        entry = p[5]
        if entry and not os.path.isfile(os.path.join(stage, entry)):
            raise RuntimeError(f"النسخة لا تحتوي الملف الرئيسي «{entry}»")
        for keep in (VENV_DIRNAME, PYLIBS_DIRNAME, "node_modules", ".deps_installed", "output.log"):
            src = os.path.join(pdir, keep)
            if os.path.exists(src) and not os.path.exists(os.path.join(stage, keep)):
                shutil.move(src, os.path.join(stage, keep))
        os.makedirs(TRASH_DIR, exist_ok=True)
        os.rename(pdir, trash)
        try:
            os.rename(stage, pdir)
            swapped = True
        except Exception:
            os.rename(trash, pdir)                      # تراجع فوري
            raise
        add_event(pid, "restore", f"استعادة النسخة {bid} ({len(extracted)} ملف)")
        update_project(pid, installed=0)
        pstate_set(pid, install_status="none", install_detail="", exhausted=False, next_retry_at=0.0, restarts=[])
        msg_extra = ""
        if was_running:
            pstate_set(pid, desired="running")
            ok2, m2, _i = _start_locked(pid, 8, "manual")
            msg_extra = ("\n▶️ أُعيد تشغيل المشروع بنجاح." if ok2 else "\n⚠️ تمت الاستعادة لكن تعذّر تشغيل المشروع تلقائياً — افتح 🧠 تشخيص.")
        _prune_trash()
        return True, (f"📦 المشروع: <code>{esc(p[2])}</code>\n🗂 النسخة: <code>{esc(bid)}</code>\n📄 الملفات المستعادة: <code>{len(extracted)}</code>\n"
                      f"🛟 نسخة الحالة السابقة محفوظة مؤقتاً للتراجع اليدوي.{msg_extra}")
    except Exception as e:
        print(f"[PRestore] {pid}: {e}")
        shutil.rmtree(stage, ignore_errors=True)
        if not swapped and not os.path.isdir(pdir) and os.path.isdir(trash):
            try:
                os.rename(trash, pdir)
            except Exception:
                pass
        if was_running:
            try:
                _start_locked(pid, 8, "manual")
            except Exception:
                pass
        return False, esc(str(e)[:250])
    finally:
        lk.release()


# ---------------------------------------------------------------
# النسخة الكاملة (قاعدة البيانات + كل المشاريع)
# ---------------------------------------------------------------
def _db_state_sig():
    """بصمة محتوى القاعدة المهم (بدون المفاتيح المتقلبة كحالة آخر نسخة) لمنع إعادة الإرسال بلا داعٍ."""
    h = hashlib.md5()
    try:
        with DB_LOCK:
            conn = _conn()
            try:
                for t, cols in (("users", "user_id,is_banned,is_premium,max_projects,balance,plan_expires"),
                                ("projects", "id,owner_id,name,entry,approved,bot_token"),
                                ("btn_custom", "bkey,label,style,icon"), ("tpl_custom", "tid,text"),
                                ("trusted_users", "user_id")):
                    try:
                        rows = conn.execute(f"SELECT {cols} FROM {t} ORDER BY 1").fetchall()
                        h.update(repr(rows).encode("utf-8", "ignore"))
                    except Exception:
                        pass
                rows = conn.execute("SELECT key, value FROM ui_settings WHERE key NOT LIKE 'last_backup%' "
                                    "AND key NOT IN ('public_url','host_paused') ORDER BY key").fetchall()
                h.update(repr(rows).encode("utf-8", "ignore"))
            finally:
                conn.close()
    except Exception:
        pass
    return h.hexdigest()


def _data_sig():
    h = hashlib.md5()
    for fp in sorted(_bk_files()):
        try:
            st = os.stat(fp)
            h.update(f"{os.path.relpath(fp, PROJECTS_DIR)}{st.st_size}{int(st.st_mtime)}".encode())
        except Exception:
            pass
    h.update(_db_state_sig().encode())
    return h.hexdigest()


def verify_backup_zip(path):
    """فحص نسخة كاملة: zip + manifest + مسارات + سلامة القاعدة. (ok, info|سبب)."""
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad:
                return False, f"ملف تالف داخل النسخة: {bad}"
            names = z.namelist()
            if DB_ZIP_NAME not in names:
                return False, "النسخة لا تحتوي قاعدة البيانات"
            if any(_unsafe_name(n) for n in names):
                return False, "النسخة تحتوي مساراً غير آمن"
            mf = None
            if "manifest.json" in names:
                mf = json.loads(z.read("manifest.json").decode("utf-8"))
            fd, tmpdb = tempfile.mkstemp(suffix=".db", dir=DATA_DIR)
            os.close(fd)
            try:
                with z.open(DB_ZIP_NAME) as src, open(tmpdb, "wb") as out:
                    shutil.copyfileobj(src, out)
                ok, why, counts = check_sqlite_file(tmpdb)
                if not ok:
                    return False, why
                if mf and mf.get("db_sha256") and mf["db_sha256"] != _sha256_file(tmpdb):
                    return False, "بصمة القاعدة داخل النسخة لا تطابق الـ manifest"
            finally:
                try:
                    os.remove(tmpdb)
                except Exception:
                    pass
            files = [n for n in names if n.startswith("projects/") and not n.endswith("/")]
            if mf and mf.get("files") is not None and int(mf["files"]) != len(files):
                return False, f"عدد ملفات المشاريع لا يطابق (المتوقع {mf['files']}، الموجود {len(files)})"
            return True, {"files": len(files), "projects": counts.get("projects", 0), "users": counts.get("users", 0),
                          "manifest": mf, "legacy": mf is None}
    except zipfile.BadZipFile:
        return False, "الملف ليس أرشيف ZIP صالحاً"
    except Exception as e:
        return False, f"تعذّر فحص النسخة: {str(e)[:100]}"


def list_full_backups():
    out = []
    try:
        for n in os.listdir(BACKUPS_24H_DIR):
            if n.startswith("hostbak_") and n.endswith(".zip"):
                p_ = os.path.join(BACKUPS_24H_DIR, n)
                out.append({"name": n, "path": p_, "size": os.path.getsize(p_), "mtime": os.path.getmtime(p_),
                            "legacy": False})
    except Exception:
        pass
    return sorted(out, key=lambda x: x["mtime"], reverse=True)


def build_full_backup(reason="auto"):
    """يبني نسخة كاملة موثّقة في BACKUPS_24H_DIR. يرجع (ok, رسالة|info). لا يحذف القديم إلا بعد نجاح الجديدة."""
    with _BK_LOCK:
        tmpdb = None
        tmp = None
        try:
            est = dir_size(PROJECTS_DIR, limit=4 * 1024 ** 3) + (os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0)
            if _free_bytes(DATA_DIR) < est * 1.2 + 50 * 1024 * 1024:
                return False, "المساحة الحرة غير كافية لإنشاء نسخة كاملة"
            fd, tmpdb = tempfile.mkstemp(suffix=".db", dir=DATA_DIR)
            os.close(fd)
            _snapshot_db(tmpdb)
            ok, why, counts = check_sqlite_file(tmpdb)
            if not ok:
                return False, f"قاعدة البيانات الحالية لم تجتز الفحص: {why}"
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            final = os.path.join(BACKUPS_24H_DIR, f"hostbak_{stamp}.zip")
            tmp = final + ".partial"
            count, total = 0, 0
            db_sha = _sha256_file(tmpdb)
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(tmpdb, DB_ZIP_NAME)
                for fp in _bk_files():
                    try:
                        z.write(fp, "projects/" + os.path.relpath(fp, PROJECTS_DIR).replace(os.sep, "/"))
                        count += 1
                        total += os.path.getsize(fp)
                    except (OSError, ValueError):
                        continue
                mf = {"version": VERSION, "kind": "full", "created": now_str(), "files": count, "bytes": total,
                      "projects": counts.get("projects", 0), "users": counts.get("users", 0), "db_sha256": db_sha,
                      "reason": reason}
                z.writestr("manifest.json", json.dumps(mf, ensure_ascii=False))
            ok, info = verify_backup_zip(tmp)
            if not ok:
                raise RuntimeError(f"فشل التحقق من النسخة الجديدة: {info}")
            sha = _sha256_file(tmp)
            os.replace(tmp, final)
            tmp = None
            with open(final + ".sha256", "w", encoding="utf-8") as f:
                f.write(sha)
            # التقليم بعد النجاح فقط
            try:
                keep = max(1, int(float(get_ui("backup_keep") or 5)))
            except Exception:
                keep = 5
            for old in list_full_backups()[keep:]:
                for ext in ("", ".sha256"):
                    try:
                        os.remove(old["path"] + ext)
                    except Exception:
                        pass
            legacy = sorted(x for x in os.listdir(BACKUPS_24H_DIR) if x.startswith("auto_24h_") and x.endswith(".zip"))
            for n in legacy[:-2]:
                try:
                    os.remove(os.path.join(BACKUPS_24H_DIR, n))
                except Exception:
                    pass
            return True, {"path": final, "size": os.path.getsize(final), "sha": sha, "files": count,
                          "projects": counts.get("projects", 0), "users": counts.get("users", 0), "time": mf["created"]}
        except Exception as e:
            print(f"[Backup] {e}")
            return False, str(e)[:250]
        finally:
            for t in (tmp, tmpdb):
                if t:
                    try:
                        os.remove(t)
                    except Exception:
                        pass


_last_fail_alert = [0.0]


def cloud_backup(force=False):
    """نسخة كاملة موثّقة + إرسالها للأدمن وتثبيتها. لا تُرسل نفس النسخة مرتين بلا تغيير. يرجع (ok, نص)."""
    if not ADMIN_IDS:
        return False, "لا يوجد أدمن"
    sig = _data_sig()
    if not force:
        if sig == get_ui("last_backup_sig"):
            return True, "لا تغييرات منذ آخر نسخة"
        try:
            last_ts = float(get_ui("last_backup_ts") or 0)
        except Exception:
            last_ts = 0
        if time.time() - last_ts < BACKUP_MIN_GAP_HOURS * 3600:
            return True, "نسخة حديثة موجودة (ضمن الحد الأدنى بين النسخ)"
    ok, info = build_full_backup("cloud" if not force else "manual")
    if not ok:
        return False, info
    path, size = info["path"], info["size"]
    if size > TG_UPLOAD_LIMIT:
        set_ui("last_backup_sig", sig)
        set_ui("last_backup_ts", str(time.time()))
        return False, (f"النسخة المحلية الموثّقة جاهزة ({fmt_size(size)}) لكنها أكبر من حد تيليجرام (49MB) فلم تُرسل. "
                       "احذف مشاريع كبيرة أو استخدم DATA_DIR دائم.")
    if get_ui("cloud_auto") == "0" and not force:
        set_ui("last_backup_sig", sig)
        set_ui("last_backup_ts", str(time.time()))
        return True, "نسخة محلية موثّقة (الإرسال التلقائي لتيليجرام معطّل)"
    aid = ADMIN_IDS[0]
    cap = msg("msg_backup_ok", time=info["time"], projects=str(info["projects"]), files=str(info["files"]),
              size=fmt_size(size), sha=info["sha"][:16])
    if size > TG_DOWNLOAD_LIMIT:
        cap += "\n⚠️ أكبر من 20MB: الاستعادة التلقائية عند الإقلاع لن تعمل، أرسلها يدوياً."
    with open(path, "rb") as f:
        m_ = bot.send_document(aid, f, caption=cap, parse_mode="HTML",
                               visible_file_name="hostbot_backup.zip", disable_notification=True)
    try:
        bot.pin_chat_message(aid, m_.message_id, disable_notification=True)
    except Exception as e:
        print(f"[Backup] pin: {e}")
    old = get_ui("last_backup_msg")
    if old:
        try:
            bot.delete_message(aid, int(old))
        except Exception:
            pass
    set_ui("last_backup_msg", str(m_.message_id))
    set_ui("last_backup_sig", sig)
    set_ui("last_backup_ts", str(time.time()))
    return True, "تم"


def cloud_backup_loop():
    time.sleep(120)
    while True:
        try:
            if len(get_all_projects()) or get_user_count() > 1:
                ok, m_ = cloud_backup()
                if not ok:
                    print(f"[Backup] {m_}")
                    if time.time() - _last_fail_alert[0] > 6 * 3600:
                        _last_fail_alert[0] = time.time()
                        try:
                            bot.send_message(ADMIN_IDS[0], msg("msg_backup_fail", reason=m_), parse_mode="HTML")
                        except Exception:
                            pass
        except Exception as e:
            print(f"[Backup] {e}")
        time.sleep(max(BACKUP_HOURS, 0.1) * 3600)


def local_backup_loop():
    """نسخة محلية موثّقة كل LOCAL_BACKUP_HOURS (وأولى بعد 10 دقائق إن لم توجد أي نسخة)."""
    time.sleep(600 if not list_full_backups() else LOCAL_BACKUP_HOURS * 3600)
    while True:
        try:
            ok, info = build_full_backup("auto")
            if ok:
                log_action(0, "auto_backup_local", os.path.basename(info["path"]))
            else:
                print(f"[LocalBackup] {info}")
        except Exception as e:
            print(f"[LocalBackup] {e}")
        time.sleep(max(LOCAL_BACKUP_HOURS, 0.5) * 3600)


def _ensure_extra_tables():
    with DB_LOCK:
        conn = _conn()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS btn_custom (bkey TEXT PRIMARY KEY, label TEXT, style TEXT, icon TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS tpl_custom (tid TEXT PRIMARY KEY, text TEXT)")
            conn.commit()
        finally:
            conn.close()


def _apply_backup_zip(data):
    """
    استعادة كاملة آمنة. data: bytes أو مسار ملف. يرجع (ok, نص).
    فك مؤقت ← تحقق ← لقطة أمان ← تبديل مجلدات المشاريع ثم القاعدة ← تراجع تلقائي عند الفشل.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stage = os.path.join(TRASH_DIR, f"stage_{stamp}")
    snap_db = os.path.join(BACKUPS_24H_DIR, f"pre_restore_{stamp}.db")
    moved = []                       # [(الجديد, القديم_في_السلة)] للتراجع
    was_running = [p[0] for p in get_all_projects() if is_running(p[0])]
    try:
        try:
            zf = zipfile.ZipFile(io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data)
        except Exception:
            return False, "الملف ليس zip صالحاً"
        names = zf.namelist()
        if DB_ZIP_NAME not in names:
            return False, "النسخة لا تحتوي قاعدة البيانات"
        for n in names:
            if _unsafe_name(n):
                return False, "النسخة تحتوي مساراً غير آمن"
        bad = zf.testzip()
        if bad:
            return False, f"ملف تالف داخل النسخة: {esc(bad)}"
        os.makedirs(stage, exist_ok=True)
        extracted = safe_extract_zip(zf, stage, max_files=MAX_ZIP_FILES * 5, max_total_mb=RESTORE_MAX_MB,
                                     max_file_mb=max(MAX_ZIP_FILE_MB, 400), max_ratio=2000)
        zf.close()
        new_db = os.path.join(stage, *DB_ZIP_NAME.split("/"))
        ok, why, counts = check_sqlite_file(new_db)
        if not ok:
            return False, f"قاعدة البيانات داخل النسخة غير صالحة: {esc(why)}"
        # لقطة أمان للحالة الحالية
        try:
            _snapshot_db(snap_db)
            for old in sorted(x for x in os.listdir(BACKUPS_24H_DIR) if x.startswith("pre_restore_"))[:-3]:
                os.remove(os.path.join(BACKUPS_24H_DIR, old))
        except Exception as e:
            return False, f"تعذّر أخذ لقطة الأمان قبل الاستعادة: {esc(str(e)[:100])}"
        for pid in was_running:
            stop_project(pid, reason="restore")
        # تبديل مجلدات المشاريع
        n_projects = 0
        stage_projects = os.path.join(stage, "projects")
        if os.path.isdir(stage_projects):
            for name in sorted(os.listdir(stage_projects)):
                src = os.path.join(stage_projects, name)
                if not os.path.isdir(src) or os.path.islink(src):
                    continue
                dest = os.path.join(PROJECTS_DIR, name)
                if not is_inside(PROJECTS_DIR, dest):
                    continue
                old_in_trash = None
                if os.path.exists(dest):
                    old_in_trash = os.path.join(TRASH_DIR, f"full_{stamp}_{name}")
                    for keep in (VENV_DIRNAME, PYLIBS_DIRNAME, "node_modules", ".deps_installed", "output.log"):
                        ks = os.path.join(dest, keep)
                        if os.path.exists(ks) and not os.path.exists(os.path.join(src, keep)):
                            shutil.move(ks, os.path.join(src, keep))
                    os.rename(dest, old_in_trash)
                os.rename(src, dest)
                moved.append((dest, old_in_trash))
                n_projects += 1
        # استبدال القاعدة
        with DB_LOCK:
            tmp = DB_PATH + ".restore"
            shutil.copyfile(new_db, tmp)
            os.replace(tmp, DB_PATH)
        init_db()
        _ensure_extra_tables()
        for p in get_all_projects():
            newp = os.path.join(PROJECTS_DIR, os.path.basename((p[4] or "").rstrip("/\\")))
            if os.path.isdir(newp):
                update_project(p[0], path=newp, pid=0, status="stopped")
        try:
            _load_btn_cache(); _load_tpl_cache()
        except Exception:
            pass
        set_ui("last_backup_sig", _data_sig())
        shutil.rmtree(stage, ignore_errors=True)
        _prune_trash()
        run_bg(restore_desired_states, 2)
        return True, (f"📦 المشاريع: <code>{n_projects}</code> · 📄 الملفات: <code>{len(extracted)}</code>\n"
                      f"👥 المستخدمين: <code>{counts.get('users', 0)}</code>\n"
                      f"🛟 لقطة أمان للحالة السابقة: <code>{esc(os.path.basename(snap_db))}</code>\n"
                      "▶️ المشاريع التي كانت مطلوبة شغّالة ستعود للعمل خلال لحظات.")
    except Exception as e:
        print(f"[Restore] فشل: {e}\n{traceback.format_exc()[-400:]}")
        # تراجع
        for dest, old in reversed(moved):
            try:
                if os.path.exists(dest):
                    shutil.rmtree(dest, ignore_errors=True)
                if old and os.path.exists(old):
                    os.rename(old, dest)
            except Exception:
                pass
        try:
            if os.path.isfile(snap_db):
                with DB_LOCK:
                    shutil.copyfile(snap_db, DB_PATH + ".rb")
                    os.replace(DB_PATH + ".rb", DB_PATH)
        except Exception:
            pass
        shutil.rmtree(stage, ignore_errors=True)
        for pid in was_running:
            run_bg(start_project, pid, 8, "boot")
        return False, esc(str(e)[:250]) + "\n(تم التراجع تلقائياً)"


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
    """عند الإقلاع: إذا القاعدة فارغة (الاستضافة مسحت الملفات) نستعيد آخر نسخة مثبّتة تلقائياً."""
    try:
        if len(get_all_projects()) == 0 and get_user_count() <= 1:
            ok, m_ = cloud_restore_from_pinned()
            if ok:
                print(f"[Recover] {re.sub(r'<[^>]+>', '', m_)}")
                try:
                    bot.send_message(ADMIN_IDS[0], msg("msg_restore_ok", details=Raw(
                        "♻️ اكتشفت أن الاستضافة مسحت الملفات واستعدتها تلقائياً.\n" + m_)), parse_mode="HTML")
                except Exception:
                    pass
            else:
                print(f"[Recover] {m_}")
    except Exception as e:
        print(f"[Recover] تعذّر: {e}")


def restore_db_file(tmp_path):
    """استعادة قاعدة بيانات فقط (ملف .db) بعد فحص السلامة ولقطة أمان. (ok, نص)."""
    ok, why, counts = check_sqlite_file(tmp_path)
    if not ok:
        return False, esc(why)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snap = os.path.join(BACKUPS_24H_DIR, f"pre_restore_{stamp}.db")
    try:
        _snapshot_db(snap)
        with DB_LOCK:
            shutil.copyfile(tmp_path, DB_PATH + ".restore")
            os.replace(DB_PATH + ".restore", DB_PATH)
        init_db()
        _ensure_extra_tables()
        try:
            _load_btn_cache(); _load_tpl_cache()
        except Exception:
            pass
        return True, f"👥 المستخدمين: <code>{counts.get('users', 0)}</code> · 📦 المشاريع: <code>{counts.get('projects', 0)}</code>"
    except Exception as e:
        try:
            if os.path.isfile(snap):
                with DB_LOCK:
                    shutil.copyfile(snap, DB_PATH)
        except Exception:
            pass
        return False, esc(str(e)[:200]) + "\n(تم التراجع)"



@bot.callback_query_handler(func=lambda c: c.data == "cb_now")
def cb_cloud_now(call):
    if not is_admin(call.from_user.id):
        return
    bot.answer_callback_query(call.id, "⏳ جاري إنشاء النسخة وفحصها...")
    chat_id = call.message.chat.id

    def work():
        try:
            ok, m_ = cloud_backup(force=True)
        except Exception as e:
            ok, m_ = False, str(e)[:200]
        bot.send_message(chat_id, "✅ تم الحفظ والتحقق وتثبيت النسخة في محادثتك." if ok else msg("msg_backup_fail", reason=m_),
                         parse_mode="HTML")
    run_bg(work)


@bot.callback_query_handler(func=lambda c: c.data == "cb_restore")
def cb_cloud_restore(call):
    if not is_admin(call.from_user.id):
        return
    user_states[call.from_user.id] = "waiting_restore_cloud"
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_green("📌 استعادة من النسخة المثبّتة", "cb_restore_pin"))
    m.add(btn_red("❌ إلغاء", "admin_panel"))
    bot.send_message(call.message.chat.id,
                     f"{make_header('♻️ استعادة نسخة سحابية')}\n\nأرسل ملف <code>hostbot_backup.zip</code> الآن، أو اضغط الزر لاستعادة آخر نسخة مثبّتة.\n"
                     "⚠️ سيُستبدل كل ما في البوت بمحتوى النسخة.", reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "cb_restore_pin")
def cb_cloud_restore_pin(call):
    if not is_admin(call.from_user.id):
        return
    user_states.pop(call.from_user.id, None)
    bot.answer_callback_query(call.id, "⏳")
    chat_id = call.message.chat.id

    def go():
        try:
            ok, m_ = cloud_restore_from_pinned()
        except Exception as e:
            ok, m_ = False, esc(str(e)[:200])
        bot.send_message(chat_id, msg("msg_restore_ok", details=Raw(m_)) if ok else msg("msg_restore_fail", reason=Raw(m_)),
                         parse_mode="HTML")
    run_bg(go)


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "waiting_restore_cloud", content_types=["document"])
def _do_restore_cloud(msg_):
    if not is_admin(msg_.from_user.id):
        return
    user_states.pop(msg_.from_user.id, None)
    chat_id = msg_.chat.id

    def go():
        try:
            if (msg_.document.file_size or 0) > TG_DOWNLOAD_LIMIT:
                bot.reply_to(msg_, "❌ الملف أكبر من 20MB (حد تيليجرام للبوتات).")
                return
            fi = bot.get_file(msg_.document.file_id)
            ok, m_ = _apply_backup_zip(bot.download_file(fi.file_path))
        except Exception as e:
            ok, m_ = False, esc(str(e)[:200])
        bot.send_message(chat_id, msg("msg_restore_ok", details=Raw(m_)) if ok else msg("msg_restore_fail", reason=Raw(m_)),
                         parse_mode="HTML")
    run_bg(go)


# ============================================================
# 📤 سحب ملفات المستخدمين (للأدمن)
# ============================================================
def _send_user_projects(chat_id, uid):
    sent = 0
    for p in get_projects_by_owner(uid):
        if not p[4] or not os.path.isdir(p[4]):
            continue
        fd, tmp = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        try:
            zip_project_dir(p[4], tmp, exclude_dirnames=tuple(_BK_EXCL_DIRS))
            if os.path.getsize(tmp) > TG_UPLOAD_LIMIT:
                bot.send_message(chat_id, f"⚠️ <b>{p[2]}</b> أكبر من 49MB — تخطيته.", parse_mode="HTML")
                continue
            with open(tmp, "rb") as f:
                bot.send_document(chat_id, f, visible_file_name=f"{uid}_{re.sub(r'[^A-Za-z0-9_.-]', '_', p[2])}.zip",
                                  caption=f"📦 {p[2]} · المالك <code>{uid}</code>", parse_mode="HTML")
            sent += 1
        except Exception as e:
            bot.send_message(chat_id, f"❌ {p[2]}: <code>{str(e)[:150]}</code>", parse_mode="HTML")
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
    page = int(call.data[5:] or 0)
    users = [u for u in get_all_users_full() if count_user_projects(u[0]) > 0]
    pages = max(1, (len(users) + 7) // 8)
    page = max(0, min(page, pages - 1))
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(btn_green(f"📦 سحب ملفات الكل ({len(users)})", "pullall"))
    for u in users[page * 8:(page + 1) * 8]:
        name = (u[2] or u[1] or str(u[0]))[:20]
        m.add(btn_blue(f"👤 {name} · {count_user_projects(u[0])} مشروع", f"pullu_{u[0]}"))
    nav = []
    if page > 0:
        nav.append(btn_blue("◀️", f"pull_{page - 1}"))
    if page < pages - 1:
        nav.append(btn_blue("▶️", f"pull_{page + 1}"))
    if nav:
        m.row(*nav)
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    txt = f"{make_header('📤 سحب ملفات المستخدمين')}\n\nاختر مستخدماً لتصلك ملفات مشاريعه كملفات zip.\nصفحة {page + 1}/{pages}"
    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("pullu_") or c.data == "pullall")
def cb_pull_go(call):
    if not is_admin(call.from_user.id):
        return
    bot.answer_callback_query(call.id, "⏳ جاري التجهيز...")
    chat_id = call.message.chat.id

    def work():
        if call.data == "pullall":
            total = sum(_send_user_projects(chat_id, u[0]) for u in get_all_users_full())
        else:
            total = _send_user_projects(chat_id, int(call.data[6:]))
        bot.send_message(chat_id, f"✅ تم إرسال <code>{total}</code> ملف.", parse_mode="HTML")
    threading.Thread(target=work, daemon=True).start()


# ============================================================
# 🎯 التخصيص المباشر: تنقّل داخل البوت واضغط أي زر ليظهر محرره فوراً
# ============================================================
_DX_ON = set()
_DX_KEYS = {}
_DX_LABELS = {}
_DX_BYPASS = set()
_DX_PTPL_BACK = {}
_DX_ORIGIN_PAGE = {}
_DX_SAFE = ("pbtn_", "pgrp_", "ptpl_", "dx", "admin_premium", "noop")


def _dx_id(key, label=""):
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
    _DX_KEYS[h] = key
    if label:
        _DX_LABELS[h] = label
    return h


def _dx_target_fid(key):
    if not key or key.startswith(("url:", "txt:")):
        return None
    sc = ui_scan()
    dummy = SimpleNamespace(data=key, from_user=SimpleNamespace(id=ADMIN_IDS[0] if ADMIN_IDS else 0))
    for hnd in bot.callback_query_handlers:
        fn = hnd.get("function")
        if not fn or fn is cb_dx_intercept or fn.__name__ in _PREMIUM_SELF_FUNCS or fn.__name__.startswith(("cb_dx", "_dx")):
            continue
        flt = (hnd.get("filters") or {}).get("func")
        try:
            if flt and flt(dummy):
                fid = _ui_id(fn.__name__, 8)
                if fid in sc["funcs"]:
                    return fid
        except Exception:
            pass
    return None


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
    txt = (f"{make_header('🎯 تخصيص هذا الزر')}\n\n"
           f"🏷 الاسم: <b>{label}</b>\n"
           f"🌈 اللون: {BTN_STYLE_NAMES.get(cust.get('style'), 'افتراضي')}\n"
           f"🎨 الإيموجي: {'✨ مخصص' if cust.get('icon') else 'الافتراضي'}\n\n"
           "هكذا يبدو الآن:")
    m = types.InlineKeyboardMarkup(row_width=3)
    m.add(prev)
    m.add(btn_green("⚡ تعيين سريع (إيموجي + اسم)", f"dxq_{h}"))
    m.row(btn_blue("✏️ الاسم", f"dxn_{h}"), btn_blue("🎨 الإيموجي", f"dxi_{h}"), btn_blue("🌈 اللون", f"dxc_{h}"))
    m.add(btn_red("♻️ إعادة الافتراضي", f"dxr_{h}"))
    m.add(btn_green("▶️ فتح الزر (تنقّل)", f"dxgo_{h}"))
    m.row(btn_blue("📝 تعديل نص هذه الصفحة", f"dxtpl_{h}"), btn_gray("📚 كل النصوص", "ptpl_root"))
    m.row(btn_gray("🔙 رجوع", f"dxback_{h}"), btn_red("🔴 إنهاء التخصيص", "dx_off"))
    return txt, m


def _dx_gate(c):
    uid = c.from_user.id
    if uid not in _DX_ON or not is_admin(uid):
        return False
    d = c.data or ""
    if d.startswith(_DX_SAFE):
        return False
    if (uid, d) in _DX_BYPASS:
        _DX_BYPASS.discard((uid, d))
        return False
    return True


@bot.callback_query_handler(func=_dx_gate)
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
    page_info = _DX_MSG_PAGE.get((call.message.chat.id, call.message.message_id))
    if page_info:
        _DX_ORIGIN_PAGE[h] = page_info
    try:
        bot.answer_callback_query(call.id, "🎯 وضع التخصيص")
    except Exception:
        pass
    txt, m = _dx_editor_markup(h)
    bot.send_message(call.message.chat.id, txt, reply_markup=m, parse_mode="HTML")


def _dx_on_message(uid, chat_id):
    _DX_ON.add(uid)
    bot.send_message(chat_id,
                     f"{make_header('🎯 التخصيص المباشر مفعّل')}\n\n"
                     "تنقّل في البوت كأنك مستخدم، واضغط <b>أي زر</b> فيظهر لك محرره (اسم · إيموجي مميز · لون) في رسالة جديدة.\n"
                     "من المحرر اضغط «▶️ فتح الزر» لتكمل التنقّل، وفي أي صفحة تدخلها ستجد زر «📝 تعديل نص هذه الصفحة» بالأسفل.\n"
                     "للإنهاء: «🔴 إنهاء» أو أرسل /edit مرة أخرى.\n\n"
                     "ℹ️ أزرار الروابط (URL) تُفتح مباشرة ولا يمكن اعتراضها؛ خصّصها من «🎨 تخصيص البوت».",
                     parse_mode="HTML", reply_markup=main_menu(uid))


@bot.callback_query_handler(func=lambda c: c.data in ("dx_toggle", "dx_off"))
def cb_dx_toggle(call):
    uid = call.from_user.id
    if not is_admin(uid):
        return
    bot.answer_callback_query(call.id)
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

    if d == "dxpclose":
        _DX_PTPL_BACK.pop(uid, None)
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(chat_id, mid)
        except Exception:
            bot.send_message(chat_id, "🏠 القائمة الرئيسية", reply_markup=main_menu(uid))
        return

    if d.startswith(("dxptpl_", "dxpscr_")):
        bot.answer_callback_query(call.id)
        sc = ui_scan()
        _DX_PTPL_BACK[uid] = "dxpclose"
        if d.startswith("dxptpl_"):
            tid = d[len("dxptpl_"):]
            target_cb = f"ptpl_v_{tid}"
        else:
            fid = d[len("dxpscr_"):]
            fo = sc["funcs"].get(fid)
            if fo and len(fo.get("tpls", [])) == 1:
                target_cb = f"ptpl_v_{fo['tpls'][0]}"
            else:
                target_cb = f"ptpl_scr_{fid}_0"
        fake_msg = _orig_send_message(chat_id, "⏳ ...")
        fake_call = SimpleNamespace(data=target_cb, from_user=call.from_user, message=fake_msg)
        return cb_ptpl(fake_call)

    act, _, rest = d.partition("_")
    if act == "dxs":
        h, _, style = rest.rpartition("_")
        key = _DX_KEYS.get(h)
        if key and style in BTN_STYLES:
            set_btn_custom(key, style=style)
        d, act, rest = f"dxe_{h}", "dxe", h
    h = rest
    key = _DX_KEYS.get(h)
    if not key:
        bot.answer_callback_query(call.id, "انتهت الجلسة، اضغط الزر مرة أخرى", show_alert=True)
        return
    chat_id, mid = call.message.chat.id, call.message.message_id

    def show():
        txt, m = _dx_editor_markup(h)
        try:
            bot.edit_message_text(txt, chat_id, mid, reply_markup=m, parse_mode="HTML")
        except Exception:
            bot.send_message(chat_id, txt, reply_markup=m, parse_mode="HTML")

    if act == "dxe":
        bot.answer_callback_query(call.id)
        show()
    elif act == "dxr":
        reset_btn_custom(key)
        bot.answer_callback_query(call.id, "♻️ تمت الإعادة")
        show()
    elif act == "dxc":
        bot.answer_callback_query(call.id)
        m = types.InlineKeyboardMarkup(row_width=1)
        for st in BTN_STYLES:
            m.add(btn(BTN_STYLE_NAMES[st], f"dxs_{h}_{st}", style=(st if st != "none" else None)))
        m.add(btn_gray("🔙 رجوع", f"dxe_{h}"))
        bot.edit_message_text(make_header("🌈 اختر اللون"), chat_id, mid, reply_markup=m, parse_mode="HTML")
    elif act in ("dxn", "dxi", "dxq"):
        bot.answer_callback_query(call.id)
        user_states[uid] = f"waiting_dx_{act[2]}_{h}"
        hint = {"dxn": "أرسل الاسم الجديد (حتى 30 حرفاً)، أو أرسل - لإرجاع الأصلي.",
                "dxi": "أرسل إيموجي مميز (Premium) ليصبح أيقونة الزر.",
                "dxq": "أرسل إيموجي مميز مع الاسم في رسالة واحدة، مثال: 🔥 مشاريعي"}[act]
        m = types.InlineKeyboardMarkup().add(btn_red("❌ إلغاء", f"dxe_{h}"))
        bot.edit_message_text(f"{make_header('✏️ تخصيص')}\n\n{hint}", chat_id, mid, reply_markup=m, parse_mode="HTML")
    elif act == "dxback":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(chat_id, mid)
        except Exception:
            try:
                bot.edit_message_text("🏠 القائمة الرئيسية", chat_id, mid, reply_markup=main_menu(uid))
            except Exception:
                bot.send_message(chat_id, "🏠 القائمة الرئيسية", reply_markup=main_menu(uid))
    elif act == "dxtpl":
        bot.answer_callback_query(call.id)
        sc = ui_scan()
        fid = _dx_target_fid(key)
        tid = None
        if not fid or not sc["funcs"].get(fid, {}).get("tpls"):
            orig_tid, orig_fid = _DX_ORIGIN_PAGE.get(h, (None, None))
            tid = orig_tid
            fid = orig_fid or fid
        _DX_PTPL_BACK[uid] = f"dxe_{h}"
        if tid:
            call.data = f"ptpl_v_{tid}"
        elif fid and sc["funcs"].get(fid, {}).get("tpls"):
            tpls_list = sc["funcs"][fid]["tpls"]
            call.data = f"ptpl_v_{tpls_list[0]}" if len(tpls_list) == 1 else f"ptpl_scr_{fid}_0"
        else:
            call.data = "ptpl_root"
        return cb_ptpl(call)
    elif act == "dxgo":
        _DX_BYPASS.add((uid, key))
        call.data = key
        try:
            bot.process_new_callback_query([call])
        except Exception as e:
            print(f"[DX] {e}")


@bot.message_handler(func=lambda m: not (m.text or "").startswith("/") and user_states.get(m.from_user.id, "").startswith("waiting_dx_"))
def _do_dx_input(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    state = user_states.pop(uid)
    _, _, kind, h = state.split("_", 3)
    key = _DX_KEYS.get(h)
    if not key:
        bot.reply_to(msg, "❌ انتهت الجلسة.")
        return
    icon, label = _entities_label_icon(msg)
    try:
        if kind == "n":
            txt = (msg.text or "").strip()
            if txt == "-":
                set_btn_custom(key, label="")
            else:
                set_btn_custom(key, label=txt[:30])
        elif kind == "i":
            if not icon:
                bot.reply_to(msg, "❌ لم أجد إيموجي مميزاً في رسالتك.")
                return
            set_btn_custom(key, icon=icon)
        else:
            if not icon:
                bot.reply_to(msg, "❌ أرسل إيموجي مميزاً مع الاسم.")
                return
            set_btn_custom(key, icon=icon, **({"label": label[:30]} if label else {}))
    except Exception as e:
        bot.reply_to(msg, f"❌ {str(e)[:150]}")
        return
    txt, m = _dx_editor_markup(h)
    bot.send_message(msg.chat.id, "✅ تم الحفظ\n\n" + txt, reply_markup=m, parse_mode="HTML")


# الاعتراض يجب أن يسبق كل المعالجات الأخرى
try:
    _gate_h = next(h for h in bot.callback_query_handlers if h["function"] is cb_dx_intercept)
    bot.callback_query_handlers.remove(_gate_h)
    bot.callback_query_handlers.insert(0, _gate_h)
    _inp_h = next(h for h in bot.message_handlers if h["function"] is _do_dx_input)
    bot.message_handlers.remove(_inp_h)
    bot.message_handlers.insert(0, _inp_h)
except Exception as _e:
    print(f"[DX] تعذّر ترتيب المعالجات: {_e}")


def _parse_pid(data, prefix):
    try:
        return int(str(data)[len(prefix):].split(":")[0].split("_")[0])
    except Exception:
        return None


def _owned_project(call, pid):
    """يرجع المشروع إن كان المستخدم يملكه (أو أدمن)، وإلا يرفض ويسجّل المحاولة."""
    p = get_project(pid) if pid is not None else None
    if not p:
        bot.answer_callback_query(call.id, "❌ المشروع غير موجود", show_alert=True)
        return None
    if not can_access_project(p, call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 غير مصرح", show_alert=True)
        log_action(call.from_user.id, "unauthorized_access", f"project={pid}")
        return None
    return p


def _show(call, text, markup=None):
    """يحدّث الرسالة الحالية بدل إرسال رسالة جديدة (وإن تعذّر يرسل واحدة)."""
    try:
        return bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                                     reply_markup=markup, parse_mode="HTML")
    except Exception:
        return bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")


def _short_ts(ts):
    return ts if ts else "—"


def project_status_vals(p):
    """يجمع كل معلومات بطاقة المشروع (حالة دقيقة + مراقبة + نسخ + مكتبات)."""
    pid = p[0]
    st = pstate_get(pid)
    running = is_running(pid)
    now = time.time()
    nxt = float(st.get("next_retry_at") or 0)
    window = [t for t in st["restarts"] if now - t < RESTART_WINDOW_SEC]
    if running:
        state = "🟢 شغّال"
    elif nxt and nxt > now:
        state = f"🟡 إعادة تشغيل خلال {int(nxt - now)}ث"
    elif st.get("exhausted"):
        state = "🛑 توقف بعد استنفاد المحاولات"
    elif p[15] == "crashed":
        state = "🔴 متوقف بعد خطأ"
    else:
        state = "🔴 متوقف"
    appr = get_project_approved(pid)
    if appr == 0:
        state += " · ⏳ بانتظار موافقة الإدارة"
    elif appr == 2:
        state += " · ❌ مرفوض من الإدارة"

    if not monitor_enabled():
        monitor = "⏸ المراقبة معطّلة عالمياً من المطوّر"
    elif running:
        monitor = "🟢 مراقَب" + (" · إعادة تلقائية مفعّلة" if st["auto_restart"] else " · الإعادة التلقائية معطّلة")
    elif nxt and nxt > now:
        monitor = f"⏳ المحاولة {len(window) + 1}/{st['max_restarts']}"
    elif st.get("exhausted"):
        monitor = f"🛑 توقفت بعد {len(window)} محاولات"
    elif st["desired"] != "running":
        monitor = "⏹ متوقف بطلبك (غير مراقَب)"
    elif not st["auto_restart"]:
        monitor = "💤 غير مراقَب (الإعادة التلقائية معطّلة)"
    else:
        monitor = "🟢 جاهز للمراقبة"

    restarts = (f"<code>{len(window)}</code> محاولة خلال {RESTART_WINDOW_SEC // 60}د · "
                f"الإجمالي <code>{st['total_restarts']}</code> · الحد <code>{st['max_restarts']}</code>")
    ins_map = {"none": "—", "ok": "✅ سليم", "running": "⏳ جارٍ التثبيت", "partial": "🟡 جزئي", "failed": "🔴 فشل"}
    install = ins_map.get(st.get("install_status"), "—")
    if st.get("install_detail") and st.get("install_status") in ("partial", "failed"):
        install += f" — {esc(st['install_detail'])[:80]}"
    bks = list_project_backups(pid)
    backup = (f"{esc(bks[0]['ts'])} ({fmt_size(bks[0]['size'])}) · {len(bks)} نسخة" if bks else "لا توجد نسخة بعد")

    resources = ""
    if running:
        try:
            cpu, mem = get_proc_metrics(p[14])
            resources = f"📊 <b>الموارد:</b> <code>CPU {cpu}% | RAM {mem} MB</code>\n"
        except Exception:
            pass
    last_error = ""
    if not running:
        le = last_error_line(_log_tail(p, 60))
        if le:
            last_error = f"⚠️ <b>آخر خطأ:</b> <code>{esc(le[:140])}</code>\n"
    stop_reason = ""
    if not running and st.get("last_exit_reason"):
        stop_reason = f"🔎 <b>سبب آخر توقف:</b> {esc(st['last_exit_reason'])}\n"
    url = f"\n🔗 <b>الرابط:</b>\n<code>{esc(p[12])}</code>\n" if (p[3] == "web" and p[12]) else ""
    return dict(
        name=p[2], id=str(pid), type=type_label(p[3]), entry=p[5], state=Raw(state), uptime=calc_uptime(p[17]) if running else "—",
        last_start=_short_ts(p[17]), last_stop=_short_ts(p[18]),
        stop_reason=Raw(stop_reason), restarts=Raw(restarts), monitor=Raw(monitor), install=Raw(install),
        backup=Raw(backup), resources=Raw(resources), last_error=Raw(last_error), url=Raw(url),
    ), st, running


def project_panel_markup(p, st, running):
    pid = p[0]
    m = types.InlineKeyboardMarkup(row_width=2)
    if running:
        m.row(btn_red("⏹️ ايقاف", f"stop_{pid}"), btn_blue("🔁 إعادة تشغيل", f"restart_{pid}"))
    else:
        m.row(btn_green("▶️ تشغيل", f"start_{pid}"))
        if p[15] == "crashed" or st.get("exhausted") or st.get("last_attempt_ok") is False:
            m.row(btn_green("🔄 إعادة محاولة التشغيل", f"pretry_{pid}"))
    m.row(btn_blue("📄 السجلات", f"logs_{pid}"), btn_blue("🧠 تشخيص", f"pdiag_{pid}"))
    m.row(btn_blue("💾 نسخ احتياطي", f"pbk_{pid}"), btn_blue("♻️ استعادة نسخة", f"pbkl_{pid}"))
    if p[3] == "web" and p[12]:
        m.row(btn_blue("🌐 فتح الموقع", f"openweb_{pid}"))
    if p[9]:
        m.row(btn_green("🚀 دخول للبوت", url=f"https://t.me/{p[9]}"))
    m.row(btn_blue("📁 مدير الملفات", f"pfiles_{pid}:0"), btn_blue("⚙️ الإعدادات", f"pset_{pid}"))
    m.add(btn_gray("🔙 رجوع", "my_projs"))
    return m


def _refresh_panel(call, pid):
    call.data = f"proj_{pid}"
    cb_proj(call)


def _start_flow(chat_id, mid, pid, name, mode="start"):
    """تشغيل/إعادة تشغيل مع بطاقة تقدّم حية، في خيط مستقل. لا يرمي استثناءات."""
    res = {}

    def work():
        try:
            if mode == "restart":
                res["r"] = restart_project(pid)
            else:
                res["r"] = start_project(pid)
        except Exception as e:
            res["r"] = (False, f"خطأ: <code>{esc(str(e)[:200])}</code>", None)

    t = threading.Thread(target=work, daemon=True, name=f"start-{pid}")
    t.start()
    last = ""
    while t.is_alive():
        card = render_start_card(pid, name)
        if card != last:
            try:
                bot.edit_message_text(card, chat_id, mid, parse_mode="HTML")
                last = card
            except Exception:
                pass
        time.sleep(2)
    ok, m_, info = res.get("r", (False, "خطأ غير متوقع", None))
    try:
        if ok and info:
            if mode == "restart" and not info.get("finished"):
                text = msg("msg_project_restarted", name=name, pid=info["pid"], time=datetime.now().strftime("%H:%M:%S"))
                text += "\n\n" + make_success_message(info)
            else:
                text = make_success_message(info)
            mk = types.InlineKeyboardMarkup(row_width=1)
            if info.get("bot_username"):
                mk.add(btn_blue("🤖 فتح البوت", url=f"https://t.me/{info['bot_username']}"))
            if info.get("public_url"):
                mk.add(btn_blue("🌐 فتح الموقع", url=info["public_url"]))
            mk.row(btn_blue("📄 السجلات", f"logs_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))
            bot.edit_message_text(text, chat_id, mid, reply_markup=mk, parse_mode="HTML")
        else:
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.row(btn_green("🔄 إعادة المحاولة", f"pretry_{pid}"), btn_blue("📄 السجلات", f"logs_{pid}"))
            mk.row(btn_blue("🧠 تشخيص", f"pdiag_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))
            bot.edit_message_text(msg("msg_project_error", name=name, details=Raw(m_)), chat_id, mid,
                                  reply_markup=mk, parse_mode="HTML")
    except Exception as e:
        print(f"[StartFlow] {pid}: {e}")


@bot.callback_query_handler(func=lambda c: c.data.startswith("pretry_"))
def cb_retry(call):
    """إعادة محاولة التشغيل بعد فحص الحالة: يمنع التشغيل فقط إن وُجد خطأ مانع حقيقي."""
    pid = _parse_pid(call.data, "pretry_")
    p = _owned_project(call, pid)
    if not p:
        return
    if get_project_approved(pid) != 1:
        bot.answer_callback_query(call.id, "⏳ المشروع بانتظار موافقة/مرفوض من الإدارة.", show_alert=True)
        return
    if is_running(pid):
        bot.answer_callback_query(call.id, "ℹ️ المشروع شغّال بالفعل")
        _refresh_panel(call, pid)
        return
    bot.answer_callback_query(call.id, "🧠 جاري فحص الحالة ثم إعادة المحاولة...")
    chat_id, mid = call.message.chat.id, call.message.message_id

    def go():
        try:
            bot.edit_message_text("🧠 <b>جاري فحص المشروع قبل إعادة المحاولة...</b>", chat_id, mid, parse_mode="HTML")
        except Exception:
            pass
        res = diagnose_project(pid)
        blocking = [f for f in res["findings"] if f.get("block")]
        if blocking:
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.row(btn_blue("📄 السجلات", f"logs_{pid}"), btn_blue("🔄 إعادة الفحص", f"pdiag_{pid}"))
            mk.row(btn_green("▶️ تشغيل على أي حال", f"start_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))
            txt = "🛑 <b>لم أُعد التشغيل لأن الفحص وجد مشكلة تمنعه — أصلحها أولاً.</b>\n\n" + render_diagnosis(res)
            bot.edit_message_text(txt, chat_id, mid, reply_markup=mk, parse_mode="HTML")
            return
        _start_flow(chat_id, mid, pid, p[2], "start")

    threading.Thread(target=go, daemon=True).start()


def _log_markup(pid, mode):
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.row(btn_blue("🔄 تحديث", f"logs_{pid}" if mode != "err" else f"logs_err_{pid}"),
           btn_green("📡 تحديث مباشر (15ث)", f"logs_live_{pid}"))
    mk.row(btn_blue("🚨 الأخطاء فقط" if mode != "err" else "📄 كل السجل", f"logs_err_{pid}" if mode != "err" else f"logs_{pid}"),
           btn_blue("🧠 تشخيص", f"pdiag_{pid}"))
    mk.row(btn_blue("📥 تحميل السجل", f"logs_dl_{pid}"), btn_blue("🕘 سجل الأحداث", f"pevt_{pid}"))
    mk.add(btn_gray("🔙 رجوع", f"proj_{pid}"))
    return mk


def _build_log_msg(p, mode="all"):
    pid = p[0]
    raw = get_logs(pid, 80, errors_only=(mode == "err"))
    if len(raw) > 2800:
        raw = "...\n" + raw[-2800:]
    reason, fix = classify_failure(raw, None)
    diag = f"\n🚨 <b>تشخيص تلقائي:</b> {esc(reason)}" + (f" — {esc(fix)}" if fix else "") + "\n" if reason else ""
    title = "🚨 أخطاء" if mode == "err" else "📄 السجلات"
    t = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  <b>{title} — {esc(p[2])}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{diag}\n<pre>{esc(raw)}</pre>"
    )
    return t, _log_markup(pid, mode)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pevt_"))
def cb_events(call):
    pid = _parse_pid(call.data, "pevt_")
    p = _owned_project(call, pid)
    if not p:
        return
    bot.answer_callback_query(call.id)
    icons = {"started": "▶️", "stopped": "⏹", "crash": "💥", "start_failed": "❌", "auto_restart": "🔁", "recovered": "✅",
             "restart_scheduled": "⏳", "restart_exhausted": "🛑", "finished": "🏁", "restart": "🔁", "adopted": "🔗",
             "backup": "💾", "restore": "♻️", "boot_start": "♻️", "no_restart": "💤", "start_error": "❌"}
    rows = get_events(pid, 25)
    body = "\n".join(f"{icons.get(k, '•')} <code>{esc(ts[5:16])}</code> {esc(k)} — {esc(d)[:90]}" for ts, k, d in rows) \
        or "لا توجد أحداث مسجّلة بعد."
    mk = types.InlineKeyboardMarkup()
    mk.row(btn_blue("📄 السجلات", f"logs_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))
    _show(call, f"<b>🕘 سجل أحداث {esc(p[2])}</b>\n\n{body}", mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pdiag_"))
def cb_diag(call):
    pid = _parse_pid(call.data, "pdiag_")
    p = _owned_project(call, pid)
    if not p:
        return
    bot.answer_callback_query(call.id, "🧠 جاري الفحص...")
    chat_id, mid = call.message.chat.id, call.message.message_id
    try:
        bot.edit_message_text("🧠 <b>جاري فحص المشروع...</b>\n<i>(قد يستغرق بضع ثوانٍ)</i>", chat_id, mid, parse_mode="HTML")
    except Exception:
        pass

    def go():
        res = diagnose_project(pid)
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.row(btn_blue("📄 السجلات", f"logs_{pid}"), btn_blue("🔄 إعادة الفحص", f"pdiag_{pid}"))
        if not is_running(pid):
            mk.row(btn_green("🔄 إعادة محاولة التشغيل", f"pretry_{pid}"))
        if p[5].endswith(".py") and any("مكتبات" in f["title"] for f in res["findings"]):
            mk.row(btn_green("🪄 تثبيت تلقائي ذكي", f"smart_{pid}"))
        mk.add(btn_gray("🔙 رجوع", f"proj_{pid}"))
        bot.edit_message_text(render_diagnosis(res), chat_id, mid, reply_markup=mk, parse_mode="HTML")

    run_bg(go)


_MAX_CHOICES = [3, 5, 10, 20]


def _settings_markup(p, st):
    pid = p[0]
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_green("🔁 الإعادة التلقائية: مفعّلة ✅", f"ptar_{pid}") if st["auto_restart"]
          else btn_red("🔁 الإعادة التلقائية: معطّلة ⛔", f"ptar_{pid}"))
    m.row(btn_blue(f"🔢 الحد الأقصى للمحاولات: {st['max_restarts']}", f"pmax_{pid}"),
          btn_blue("♻️ تصفير العدّاد", f"prst_{pid}"))
    m.row(btn_blue("🕘 سجل الأحداث", f"pevt_{pid}"), btn_blue("🔍 فحص الصيغة", f"check_{pid}"))
    m.row(btn_blue("🔐 محرر .env", f"penv_{pid}"), btn_blue("⚙️ مكتبة", f"addpkg_{pid}"))
    if p[5].endswith(".py"):
        m.row(btn_green("🪄 تثبيت تلقائي ذكي", f"smart_{pid}"))
    m.row(btn_blue("💾 حفظ الجلسة", f"sess_save_{pid}"), btn_red("🔄 إعادة تعيين الجلسة", f"sess_reset_{pid}"))
    m.add(btn_red("🗑️ حذف المشروع", f"del_{pid}"))
    m.add(btn_gray("🔙 رجوع", f"proj_{pid}"))
    return m


@bot.callback_query_handler(func=lambda c: c.data.startswith("pset_"))
def cb_pset(call):
    pid = _parse_pid(call.data, "pset_")
    p = _owned_project(call, pid)
    if not p:
        return
    bot.answer_callback_query(call.id)
    st = pstate_get(pid)
    txt = (f"<b>⚙️ إعدادات {esc(p[2])}</b>\n\n"
           f"🔁 <b>الإعادة التلقائية:</b> {'مفعّلة' if st['auto_restart'] else 'معطّلة'}\n"
           f"🔢 <b>الحد الأقصى للمحاولات:</b> <code>{st['max_restarts']}</code> خلال {RESTART_WINDOW_SEC // 60} دقيقة\n"
           f"⏳ <b>مهلة ما بين المحاولات:</b> {' ← '.join(str(x) + 'ث' for x in RESTART_BACKOFF)}\n\n"
           "<i>عند الوصول للحد يتوقف المشروع نهائياً ويصلك إشعار، ولا تُعاد المحاولة حتى تضغط «إعادة محاولة التشغيل».</i>")
    _show(call, txt, _settings_markup(p, st))


@bot.callback_query_handler(func=lambda c: c.data.startswith("ptar_"))
def cb_toggle_ar(call):
    pid = _parse_pid(call.data, "ptar_")
    p = _owned_project(call, pid)
    if not p:
        return
    st = pstate_get(pid)
    new = not st["auto_restart"]
    pstate_set(pid, auto_restart=new)
    add_event(pid, "settings", f"الإعادة التلقائية: {'مفعّلة' if new else 'معطّلة'}")
    bot.answer_callback_query(call.id, "🔁 تم تفعيل الإعادة التلقائية" if new else "⛔ تم تعطيل الإعادة التلقائية")
    call.data = f"pset_{pid}"
    cb_pset(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pmax_"))
def cb_pmax(call):
    pid = _parse_pid(call.data, "pmax_")
    p = _owned_project(call, pid)
    if not p:
        return
    cur = pstate_get(pid)["max_restarts"]
    nxt = next((x for x in _MAX_CHOICES if x > cur), _MAX_CHOICES[0])
    pstate_set(pid, max_restarts=nxt)
    bot.answer_callback_query(call.id, f"🔢 الحد الأقصى: {nxt}")
    call.data = f"pset_{pid}"
    cb_pset(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("prst_"))
def cb_preset(call):
    pid = _parse_pid(call.data, "prst_")
    p = _owned_project(call, pid)
    if not p:
        return
    pstate_reset_counters(pid)
    add_event(pid, "settings", "تصفير عدّاد محاولات إعادة التشغيل")
    bot.answer_callback_query(call.id, "♻️ تم تصفير العدّاد")
    call.data = f"pset_{pid}"
    cb_pset(call)


_DOC_STATES = ("waiting_file_", "waiting_req_", "waiting_addfile_")


def _unique_project_dir(uid, name):
    base = os.path.join(PROJECTS_DIR, f"{uid}_{name}")
    pdir, n = base, 1
    while os.path.exists(pdir):
        n += 1
        pdir = f"{base}_{n}"
    return pdir


def _fkey(rel):
    return hashlib.md5(rel.encode("utf-8", "ignore")).hexdigest()[:10]


def _visible_files(p):
    skip = (VENV_DIRNAME + os.sep, PYLIBS_DIRNAME + os.sep, "node_modules" + os.sep, "__pycache__" + os.sep)
    out = []
    for f in list_dir_files(p[4]):
        if f.startswith(skip) or f == ".deps_installed" or (os.sep + "__pycache__" + os.sep) in f:
            continue
        if os.path.islink(os.path.join(p[4], f)):
            continue
        out.append(f)
    return sorted(out)


def _resolve_file(p, key):
    """يرجع (المسار النسبي, المسار الكامل الآمن) أو (None, None)."""
    for f in _visible_files(p):
        if _fkey(f) == key:
            try:
                return f, safe_join(p[4], f)
            except ValueError:
                return None, None
    return None, None


@bot.callback_query_handler(func=lambda c: c.data.startswith("pbk_"))
def cb_pbk(call):
    pid = _parse_pid(call.data, "pbk_")
    p = _owned_project(call, pid)
    if not p:
        return
    bot.answer_callback_query(call.id, "💾 جاري إنشاء النسخة وفحصها...")
    chat_id, mid = call.message.chat.id, call.message.message_id

    def go():
        ok, m_, info = create_project_backup(pid, "manual")
        mk = types.InlineKeyboardMarkup(row_width=2)
        if ok:
            txt = msg("msg_backup_ok", time=now_str(), projects="1", files=str(info["files"]),
                      size=fmt_size(info["size"]), sha=info["sha"][:16])
            mk.row(btn_blue("📥 إرسالها لي", f"pbkd_{pid}_{info['id']}"), btn_blue("♻️ قائمة النسخ", f"pbkl_{pid}"))
        else:
            txt = msg("msg_backup_fail", reason=m_)
            mk.row(btn_blue("🔄 حاول مجدداً", f"pbk_{pid}"))
        mk.add(btn_gray("🔙 رجوع", f"proj_{pid}"))
        try:
            bot.edit_message_text(txt, chat_id, mid, reply_markup=mk, parse_mode="HTML")
        except Exception:
            bot.send_message(chat_id, txt, reply_markup=mk, parse_mode="HTML")
    run_bg(go)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pbkl_"))
def cb_pbk_list(call):
    pid = _parse_pid(call.data, "pbkl_")
    p = _owned_project(call, pid)
    if not p:
        return
    bot.answer_callback_query(call.id)
    bks = list_project_backups(pid)
    mk = types.InlineKeyboardMarkup(row_width=3)
    lines = []
    for b in bks[:6]:
        lines.append(f"💾 <code>{esc(b['ts'])}</code> · {fmt_size(b['size'])} · {b['files'] if b['files'] is not None else '?'} ملف")
        mk.row(btn_green(f"♻️ {b['ts'][5:]}", f"pbkr_{pid}_{b['id']}"), btn_blue("📥", f"pbkd_{pid}_{b['id']}"),
               btn_red("🗑", f"pbkx_{pid}_{b['id']}"))
    mk.add(btn_green("💾 نسخة جديدة الآن", f"pbk_{pid}"))
    mk.add(btn_gray("🔙 رجوع", f"proj_{pid}"))
    body = "\n".join(lines) if lines else "لا توجد نسخ احتياطية بعد. اضغط «نسخة جديدة الآن»."
    keep = get_ui("proj_backup_keep") or "3"
    _show(call, f"<b>♻️ نسخ {esc(p[2])}</b>\n\n{body}\n\n<i>يُحتفظ بآخر {esc(keep)} نسخ. كل نسخة تُفحص قبل اعتمادها.</i>", mk)


def _parse_pid_bid(data, prefix):
    rest = data[len(prefix):]
    pid_s, _, bid = rest.partition("_")
    try:
        return int(pid_s), bid
    except Exception:
        return None, None


@bot.callback_query_handler(func=lambda c: c.data.startswith("pbkr_"))
def cb_pbk_restore_confirm(call):
    pid, bid = _parse_pid_bid(call.data, "pbkr_")
    p = _owned_project(call, pid)
    if not p:
        return
    bot.answer_callback_query(call.id)
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.row(btn_red("✅ نعم، استعد", f"pbkg_{pid}_{bid}"), btn_gray("❌ إلغاء", f"pbkl_{pid}"))
    _show(call, (f"<b>⚠️ استعادة نسخة</b>\n\n📦 <code>{esc(p[2])}</code>\n🗂 النسخة: <code>{esc(bid)}</code>\n\n"
                 "سيُوقَف المشروع ويُستبدل محتواه بمحتوى النسخة، مع حفظ حالته الحالية مؤقتاً للتراجع."), mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pbkg_"))
def cb_pbk_restore_go(call):
    pid, bid = _parse_pid_bid(call.data, "pbkg_")
    p = _owned_project(call, pid)
    if not p:
        return
    bot.answer_callback_query(call.id, "♻️ جاري الاستعادة...")
    chat_id, mid = call.message.chat.id, call.message.message_id
    try:
        bot.edit_message_text("♻️ <b>جاري الاستعادة والتحقق...</b>", chat_id, mid, parse_mode="HTML")
    except Exception:
        pass

    def go():
        ok, details = restore_project_backup(pid, bid)
        txt = msg("msg_restore_ok", details=Raw(details)) if ok else msg("msg_restore_fail", reason=Raw(details))
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.row(btn_blue("🧠 تشخيص", f"pdiag_{pid}"), btn_gray("🔙 رجوع", f"proj_{pid}"))
        try:
            bot.edit_message_text(txt, chat_id, mid, reply_markup=mk, parse_mode="HTML")
        except Exception:
            bot.send_message(chat_id, txt, reply_markup=mk, parse_mode="HTML")
    run_bg(go)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pbkd_"))
def cb_pbk_download(call):
    pid, bid = _parse_pid_bid(call.data, "pbkd_")
    p = _owned_project(call, pid)
    if not p:
        return
    path = os.path.join(_pbk_dir(pid), f"bk_{bid}.zip")
    if not re.match(r"^\d{8}_\d{6}$", str(bid)) or not os.path.isfile(path):
        bot.answer_callback_query(call.id, "❌ النسخة غير موجودة", show_alert=True)
        return
    if os.path.getsize(path) > TG_UPLOAD_LIMIT:
        bot.answer_callback_query(call.id, "❌ النسخة أكبر من حد تيليجرام (49MB)", show_alert=True)
        return
    bot.answer_callback_query(call.id, "📥 جاري الإرسال...")

    def go():
        with open(path, "rb") as f:
            bot.send_document(call.message.chat.id, f, visible_file_name=f"{safe_project_name(p[2]) or 'project'}_{bid}.zip",
                              caption=f"💾 نسخة {esc(p[2])} — {esc(bid)}", parse_mode="HTML")
    run_bg(go)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pbkx_"))
def cb_pbk_delete(call):
    pid, bid = _parse_pid_bid(call.data, "pbkx_")
    p = _owned_project(call, pid)
    if not p or not re.match(r"^\d{8}_\d{6}$", str(bid)):
        return
    base = os.path.join(_pbk_dir(pid), f"bk_{bid}")
    for ext in (".zip", ".json"):
        try:
            os.remove(base + ext)
        except Exception:
            pass
    bot.answer_callback_query(call.id, "🗑 تم حذف النسخة")
    call.data = f"pbkl_{pid}"
    cb_pbk_list(call)


def _bk_center_text():
    fulls = list_full_backups()
    last = fulls[0] if fulls else None
    try:
        sent_ts = float(get_ui("last_backup_ts") or 0)
    except Exception:
        sent_ts = 0
    sent = datetime.fromtimestamp(sent_ts).strftime("%Y-%m-%d %H:%M") if sent_ts else "—"
    lines = [
        "<b>💾 مركز النسخ الاحتياطي</b>", "",
        f"🗂 النسخ المحلية الموثّقة: <code>{len(fulls)}</code> (يُحتفظ بآخر <code>{esc(get_ui('backup_keep') or '5')}</code>)",
        (f"🕒 آخر نسخة محلية: <code>{datetime.fromtimestamp(last['mtime']).strftime('%Y-%m-%d %H:%M')}</code> · {fmt_size(last['size'])}"
         if last else "🕒 آخر نسخة محلية: —"),
        f"☁️ آخر إرسال لتيليجرام: <code>{sent}</code>",
        f"📤 الإرسال التلقائي: {'مفعّل ✅' if get_ui('cloud_auto') != '0' else 'معطّل ⛔'}",
        f"⏱ الدورة: سحابي كل {BACKUP_HOURS:g}س · محلي كل {LOCAL_BACKUP_HOURS:g}س",
        "", "<i>كل نسخة تُفحص (zip + قاعدة البيانات) قبل اعتمادها، ولا تُحذف نسخة سليمة قبل نجاح الجديدة.</i>",
    ]
    return "\n".join(lines)


def _bk_center_markup():
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_green("💾 نسخة كاملة الآن + إرسال", "cb_now"), btn_blue("🗄 نسخة محلية فقط", "bkc_local"))
    m.row(btn_blue("🧪 فحص سلامة النسخ", "bkc_verify"), btn_blue("♻️ استعادة من نسخة محلية", "bkc_list"))
    m.row(btn_blue("♻️ استعادة نسخة سحابية", "cb_restore"), btn_blue("📥 تخزين المطوّر", "admin_storage_0"))
    m.row(btn_blue("💽 نسخة DB فقط", "admin_backup_db"), btn_blue("📥 استعادة DB", "admin_restore_db"))
    m.row(btn_blue(f"🔢 عدد النسخ: {get_ui('backup_keep') or '5'}", "bkc_keep"),
          btn_green("📤 إرسال تلقائي: مفعّل", "bkc_auto") if get_ui("cloud_auto") != "0" else btn_red("📤 إرسال تلقائي: معطّل", "bkc_auto"))
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    return m


@bot.callback_query_handler(func=lambda c: c.data == "admin_bkc")
def cb_bk_center(call):
    if not is_admin(call.from_user.id):
        return
    bot.answer_callback_query(call.id)
    _show(call, _bk_center_text(), _bk_center_markup())


@bot.callback_query_handler(func=lambda c: c.data == "bkc_keep")
def cb_bkc_keep(call):
    if not is_admin(call.from_user.id):
        return
    cur = int(float(get_ui("backup_keep") or 5))
    nxt = next((x for x in (3, 5, 10, 20) if x > cur), 3)
    set_ui("backup_keep", str(nxt))
    bot.answer_callback_query(call.id, f"🔢 سيُحتفظ بآخر {nxt} نسخة")
    _show(call, _bk_center_text(), _bk_center_markup())


@bot.callback_query_handler(func=lambda c: c.data == "bkc_auto")
def cb_bkc_auto(call):
    if not is_admin(call.from_user.id):
        return
    set_ui("cloud_auto", "0" if get_ui("cloud_auto") != "0" else "1")
    bot.answer_callback_query(call.id, "تم التغيير")
    _show(call, _bk_center_text(), _bk_center_markup())


@bot.callback_query_handler(func=lambda c: c.data == "bkc_local")
def cb_bkc_local(call):
    if not is_admin(call.from_user.id):
        return
    bot.answer_callback_query(call.id, "🗄 جاري إنشاء النسخة وفحصها...")
    chat_id = call.message.chat.id

    def go():
        ok, info = build_full_backup("manual-local")
        if ok:
            txt = msg("msg_backup_ok", time=info["time"], projects=str(info["projects"]), files=str(info["files"]),
                      size=fmt_size(info["size"]), sha=info["sha"][:16])
        else:
            txt = msg("msg_backup_fail", reason=info)
        bot.send_message(chat_id, txt, parse_mode="HTML")
    run_bg(go)


@bot.callback_query_handler(func=lambda c: c.data == "bkc_verify")
def cb_bkc_verify(call):
    if not is_admin(call.from_user.id):
        return
    bot.answer_callback_query(call.id, "🧪 جاري الفحص...")
    chat_id = call.message.chat.id

    def go():
        fulls = list_full_backups()
        if not fulls:
            bot.send_message(chat_id, "ℹ️ لا توجد نسخ محلية للفحص.")
            return
        items = []
        for b in fulls:
            ok, info = verify_backup_zip(b["path"])
            items.append((f"{esc(b['name'])} — {fmt_size(b['size'])}" + ("" if ok else f" — {esc(str(info)[:60])}"), ok))
        bot.send_message(chat_id, render_progress("🧪 نتيجة فحص النسخ", "✅", "❌", items), parse_mode="HTML")
    run_bg(go)


@bot.callback_query_handler(func=lambda c: c.data == "bkc_list")
def cb_bkc_list(call):
    if not is_admin(call.from_user.id):
        return
    bot.answer_callback_query(call.id)
    fulls = list_full_backups()[:8]
    m = types.InlineKeyboardMarkup(row_width=1)
    for b in fulls:
        when = datetime.fromtimestamp(b["mtime"]).strftime("%m-%d %H:%M")
        m.add(btn_blue(f"♻️ {when} · {fmt_size(b['size'])}", f"bkc_rc_{_fkey(b['name'])}"))
    m.add(btn_gray("🔙 رجوع", "admin_bkc"))
    _show(call, "<b>♻️ اختر نسخة محلية للاستعادة</b>\n\n" + ("" if fulls else "لا توجد نسخ محلية."), m)


def _find_full_by_key(key):
    for b in list_full_backups():
        if _fkey(b["name"]) == key:
            return b
    return None


@bot.callback_query_handler(func=lambda c: c.data.startswith("bkc_rc_"))
def cb_bkc_restore_confirm(call):
    if not is_admin(call.from_user.id):
        return
    b = _find_full_by_key(call.data[len("bkc_rc_"):])
    if not b:
        bot.answer_callback_query(call.id, "❌ النسخة غير موجودة", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn_red("✅ نعم، استعد", f"bkc_rg_{_fkey(b['name'])}"), btn_gray("❌ إلغاء", "bkc_list"))
    _show(call, (f"<b>⚠️ استعادة كاملة</b>\n\n🗂 <code>{esc(b['name'])}</code>\n\n"
                 "سيُستبدل كل ما في البوت (قاعدة البيانات + المشاريع) بمحتوى النسخة، "
                 "مع أخذ لقطة أمان قبل البدء وتراجع تلقائي عند أي فشل."), m)


@bot.callback_query_handler(func=lambda c: c.data.startswith("bkc_rg_"))
def cb_bkc_restore_go(call):
    if not is_admin(call.from_user.id):
        return
    b = _find_full_by_key(call.data[len("bkc_rg_"):])
    if not b:
        bot.answer_callback_query(call.id, "❌ النسخة غير موجودة", show_alert=True)
        return
    bot.answer_callback_query(call.id, "♻️ جاري التحقق والاستعادة...")
    chat_id = call.message.chat.id

    def go():
        ok, info = verify_backup_zip(b["path"])
        if not ok:
            bot.send_message(chat_id, msg("msg_restore_fail", reason=f"النسخة غير سليمة: {info}"), parse_mode="HTML")
            return
        ok, details = _apply_backup_zip(b["path"])
        bot.send_message(chat_id, msg("msg_restore_ok", details=Raw(details)) if ok
                         else msg("msg_restore_fail", reason=Raw(details)), parse_mode="HTML")
    run_bg(go)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_storage_"))
def cb_admin_storage(call):
    """تخزين المطوّر: كان الزر بلا معالج (زر ميت) — الآن يعرض الأرشيف ويرسل أي نسخة."""
    if not is_admin(call.from_user.id):
        return
    try:
        page = int(call.data[len("admin_storage_"):] or 0)
    except Exception:
        page = 0
    bot.answer_callback_query(call.id)
    files = list_admin_backups(400)
    pages = max(1, (len(files) + 7) // 8)
    page = max(0, min(page, pages - 1))
    m = types.InlineKeyboardMarkup(row_width=1)
    for n in files[page * 8:(page + 1) * 8]:
        try:
            size = fmt_size(os.path.getsize(os.path.join(ADMIN_STORAGE_DIR, n)))
        except Exception:
            size = "?"
        label = n if len(n) <= 40 else n[:18] + "…" + n[-20:]
        m.add(btn_blue(f"📦 {label} ({size})", f"astg_{_fkey(n)}"))
    nav = []
    if page > 0:
        nav.append(btn_blue("◀️", f"admin_storage_{page - 1}"))
    if page < pages - 1:
        nav.append(btn_blue("▶️", f"admin_storage_{page + 1}"))
    if nav:
        m.row(*nav)
    m.add(btn_gray("🔙 رجوع", "admin_bkc"))
    intro = get_ui("storage_intro")
    _show(call, f"{intro}\n\n📊 العدد: <code>{len(files)}</code> — صفحة {page + 1}/{pages}", m)


@bot.callback_query_handler(func=lambda c: c.data.startswith("astg_"))
def cb_admin_storage_get(call):
    if not is_admin(call.from_user.id):
        return
    key = call.data[len("astg_"):]
    name = next((n for n in list_admin_backups(2000) if _fkey(n) == key), None)
    if not name:
        bot.answer_callback_query(call.id, "❌ غير موجود", show_alert=True)
        return
    path = safe_join(ADMIN_STORAGE_DIR, name)
    if os.path.getsize(path) > TG_UPLOAD_LIMIT:
        bot.answer_callback_query(call.id, "❌ أكبر من حد تيليجرام (49MB)", show_alert=True)
        return
    bot.answer_callback_query(call.id, "📥 جاري الإرسال...")

    def go():
        with open(path, "rb") as f:
            bot.send_document(call.message.chat.id, f, visible_file_name=name)
    run_bg(go)


def _monitor_row_state(p):
    pid = p[0]
    st = pstate_get(pid)
    now = time.time()
    if is_running(pid):
        return "🟢", "شغّال"
    nxt = float(st.get("next_retry_at") or 0)
    if nxt and nxt > now:
        return "🟡", f"إعادة تشغيل {int(nxt - now)}ث"
    if st.get("exhausted"):
        return "🛑", "توقف نهائي"
    if p[15] == "crashed":
        return "🔴", "انهار"
    return "⚪", "متوقف"


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_mon"))
def cb_admin_monitor(call):
    if not is_admin(call.from_user.id):
        return
    try:
        page = int(call.data[len("admin_mon"):].lstrip("_") or 0)
    except Exception:
        page = 0
    bot.answer_callback_query(call.id)
    projs = get_all_projects()
    # الحرجة أولاً: توقف نهائي/انهيار/إعادة تشغيل ثم الشغّال
    order = {"🛑": 0, "🔴": 1, "🟡": 2, "🟢": 3, "⚪": 4}
    rows = sorted(((p, *_monitor_row_state(p)) for p in projs), key=lambda r: (order.get(r[1], 9), r[0][0]))
    pages = max(1, (len(rows) + 9) // 10)
    page = max(0, min(page, pages - 1))
    counts = {k: sum(1 for r in rows if r[1] == k) for k in order}
    lines = [f"<b>🩺 مراقبة المشاريع</b>", "",
             f"🟢 {counts['🟢']} · 🟡 {counts['🟡']} · 🔴 {counts['🔴']} · 🛑 {counts['🛑']} · ⚪ {counts['⚪']}  (الإجمالي {len(rows)})",
             f"🧭 المراقب: {'يعمل ✅' if _MONITOR['alive'] else 'متوقف ⚠️'} · دورات: {_MONITOR['ticks']} · أخطاء: {_MONITOR['errors']}",
             f"🔁 الإعادة التلقائية عالمياً: {'مفعّلة ✅' if monitor_enabled() else 'معطّلة ⛔'}", ""]
    m = types.InlineKeyboardMarkup(row_width=2)
    for p, icon, label in rows[page * 10:(page + 1) * 10]:
        st = pstate_get(p[0])
        lines.append(f"{icon} <b>{esc(p[2])}</b> (#{p[0]}) — {label} · إعادات {st['total_restarts']}")
        if icon in ("🟢",):
            m.row(btn_blue(f"🔁 {p[2][:14]}", f"admrs_{p[0]}"), btn_red("⏹", f"admst_{p[0]}"))
        else:
            m.row(btn_green(f"▶️ {p[2][:14]}", f"admgo_{p[0]}"), btn_blue("🧠", f"admdg_{p[0]}"))
    nav = []
    if page > 0:
        nav.append(btn_blue("◀️", f"admin_mon_{page - 1}"))
    if page < pages - 1:
        nav.append(btn_blue("▶️", f"admin_mon_{page + 1}"))
    if nav:
        m.row(*nav)
    m.row(btn_blue("🔄 تحديث", f"admin_mon_{page}"),
          btn_red("⛔ تعطيل الإعادة التلقائية", "admmon_toggle") if monitor_enabled() else btn_green("✅ تفعيل الإعادة التلقائية", "admmon_toggle"))
    m.add(btn_green("🔄 إعادة محاولة كل المتوقفة بخطأ", "admmon_retry"))
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    _show(call, "\n".join(lines), m)


@bot.callback_query_handler(func=lambda c: c.data == "admmon_toggle")
def cb_admmon_toggle(call):
    if not is_admin(call.from_user.id):
        return
    set_ui("monitor_enabled", "0" if monitor_enabled() else "1")
    bot.answer_callback_query(call.id, "تم التغيير")
    call.data = "admin_mon_0"
    cb_admin_monitor(call)


@bot.callback_query_handler(func=lambda c: c.data == "admmon_retry")
def cb_admmon_retry(call):
    if not is_admin(call.from_user.id):
        return
    targets = [p for p in get_all_projects()
               if not is_running(p[0]) and (p[15] == "crashed" or pstate_get(p[0]).get("exhausted"))
               and get_project_approved(p[0]) == 1]
    bot.answer_callback_query(call.id, f"🔄 إعادة محاولة {len(targets)} مشروع...")
    chat_id = call.message.chat.id

    def go():
        items = []
        for p in targets[:30]:
            ok, _m, _i = start_project(p[0], watch=8, source="manual")
            items.append((esc(p[2]), ok))
        if items:
            bot.send_message(chat_id, render_progress("🔄 نتيجة إعادة المحاولة", "✅", "❌", items), parse_mode="HTML")
        else:
            bot.send_message(chat_id, "ℹ️ لا توجد مشاريع متوقفة بخطأ.")
    run_bg(go)


def _admin_action(call, prefix, fn, ok_text):
    if not is_admin(call.from_user.id):
        return
    pid = _parse_pid(call.data, prefix)
    p = get_project(pid) if pid is not None else None
    if not p:
        bot.answer_callback_query(call.id, "❌ غير موجود", show_alert=True)
        return
    bot.answer_callback_query(call.id, ok_text)

    def go():
        try:
            fn(pid)
        except Exception as e:
            print(f"[AdminAction] {e}")
        call.data = "admin_mon_0"
        cb_admin_monitor(call)
    run_bg(go)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admrs_"))
def cb_adm_restart(call):
    _admin_action(call, "admrs_", lambda pid: restart_project(pid, watch=8), "🔁 جاري إعادة التشغيل...")


@bot.callback_query_handler(func=lambda c: c.data.startswith("admst_"))
def cb_adm_stop(call):
    _admin_action(call, "admst_", lambda pid: stop_project(pid, reason="admin"), "⏹ جاري الإيقاف...")


@bot.callback_query_handler(func=lambda c: c.data.startswith("admgo_"))
def cb_adm_go(call):
    _admin_action(call, "admgo_", lambda pid: start_project(pid, watch=8), "▶️ جاري التشغيل...")


@bot.callback_query_handler(func=lambda c: c.data.startswith("admdg_"))
def cb_adm_diag(call):
    if not is_admin(call.from_user.id):
        return
    pid = _parse_pid(call.data, "admdg_")
    if pid is None or not get_project(pid):
        return
    bot.answer_callback_query(call.id, "🧠 جاري الفحص...")
    chat_id = call.message.chat.id

    def go():
        bot.send_message(chat_id, render_diagnosis(diagnose_project(pid)), parse_mode="HTML")
    run_bg(go)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ui_reset_") and c.data != "ui_reset_all")
def cb_ui_reset(call):
    if not is_admin(call.from_user.id):
        return
    key = call.data.replace("ui_reset_", "")
    if key not in UI_TEMPLATES:
        return
    ui_reset(key)
    user_states.pop(call.from_user.id, None)
    bot.answer_callback_query(call.id, "♻️ تمت استعادة النص الافتراضي", show_alert=False)
    call.data = "admin_ui"
    cb_admin_ui(call)


@bot.callback_query_handler(func=lambda c: c.data == "ui_reset_all")
def cb_ui_reset_all(call):
    if not is_admin(call.from_user.id):
        return
    n = ui_reset_all_messages()
    bot.answer_callback_query(call.id, f"♻️ أُعيد {n} نصاً للوضع الافتراضي", show_alert=True)
    call.data = "admin_ui"
    cb_admin_ui(call)


def _guide_text():
    return (
        "<blockquote><b>📖 دليل الأدمن — خطوة بخطوة</b></blockquote>\n"
        "<blockquote><b>🎨 تخصيص الأزرار والنصوص</b>\n"
        "لوحة المطور ← 🎨 تخصيص البوت ← 🎛 الأزرار أو 📝 النصوص ← اختر الشاشة ← اختر العنصر.\n"
        "كل رسالة نظام (تشغيل/إيقاف/أخطاء/نسخ/استعادة/تنبيهات) لها زر ♻️ استعادة الافتراضي.</blockquote>\n"
        "<blockquote><b>🎯 التخصيص المباشر</b>\n"
        "اضغط 🎯 التخصيص المباشر أو أرسل /edit، ثم تنقّل واضغط أي زر فيظهر محرره. أرسل /edit مجدداً للإنهاء.</blockquote>\n"
        "<blockquote><b>✅ الموافقات</b>\n"
        "كل ملف جديد يصلك مع زرّي قبول/رفض. الأعضاء في «✅ مستخدمين بدون موافقة» يعملون مباشرة.</blockquote>\n"
        "<blockquote><b>🩺 المراقبة والاستقرار</b>\n"
        "مراقب واحد يكشف الانهيار ويعيد التشغيل بمهلة متدرجة (5ث ← 15ث ← 45ث ← 2د ← 5د) وبحد أقصى للمحاولات، "
        "ثم يتوقف نهائياً ويرسل إشعاراً. من «🩺 مراقبة المشاريع» ترى كل الحالات وتتحكم بها.</blockquote>\n"
        "<blockquote><b>🛡 حماية الموارد</b>\n"
        f"كل مشروع: رام <code>{MAX_RAM_MB}MB</code> (VIP ×2) ومعالج <code>{MAX_CPU_PCT}%</code>. "
        "عند التجاوز يُوقَف ويصلك إشعار. تُضبط بـ PROJECT_MAX_RAM_MB و PROJECT_MAX_CPU_PCT.</blockquote>\n"
        "<blockquote><b>💾 النسخ الاحتياطي</b>\n"
        "«💾 مركز النسخ الاحتياطي»: نسخ كاملة موثّقة (محلية + سحابية)، فحص سلامة، واستعادة آمنة مع لقطة أمان وتراجع تلقائي. "
        "ولكل مشروع نسخه الخاصة من لوحة المشروع.</blockquote>"
    )


@bot.callback_query_handler(func=lambda c: c.data == "adm_guide")
def _guide_cb(call):
    if not is_admin(call.from_user.id):
        return
    bot.answer_callback_query(call.id)
    m = types.InlineKeyboardMarkup()
    m.add(btn_gray("🔙 رجوع", "admin_panel"))
    _show(call, _guide_text(), m)


@bot.message_handler(commands=["guide"])
def _guide_cmd(msg_):
    if not is_admin(msg_.from_user.id):
        return
    m = types.InlineKeyboardMarkup()
    m.add(btn_gray("🔙 لوحة المطور", "admin_panel"))
    bot.send_message(msg_.chat.id, _guide_text(), reply_markup=m, parse_mode="HTML")


def _html_mode(k):
    return (k.get("parse_mode") or getattr(bot, "parse_mode", None)) == "HTML"


def _is_parse_error(e):
    s = str(e).lower()
    return ("can't parse entities" in s or "can't find end tag" in s or "unsupported start tag" in s
            or "unexpected end tag" in s or ("parse" in s and "entit" in s))


def _cosmetic(text, k):
    _TL.html = _html_mode(k)
    try:
        return apply_cosmetics(text)
    except Exception as e:
        print(f"[UI] cosmetics: {e}")
        return text


def _too_long(text):
    return isinstance(text, str) and len(text) > 4090


def web_project_info(p):
    """معلومات نصية صرفة لبطاقة المشروع في لوحة الويب (يهرّبها Jinja تلقائياً)."""
    pid = p[0]
    st = pstate_get(pid)
    running = is_running(pid)
    now = time.time()
    nxt = float(st.get("next_retry_at") or 0)
    if running:
        monitor = "مراقَب" + (" · إعادة تلقائية مفعّلة" if st["auto_restart"] else " · الإعادة التلقائية معطّلة")
    elif nxt and nxt > now:
        monitor = f"إعادة تشغيل خلال {int(nxt - now)} ثانية"
    elif st.get("exhausted"):
        monitor = "توقف نهائي بعد استنفاد المحاولات"
    elif st["desired"] != "running":
        monitor = "متوقف بطلب المستخدم"
    else:
        monitor = "غير مراقَب" if not st["auto_restart"] else "جاهز للمراقبة"
    ins_map = {"none": "—", "ok": "سليم", "running": "جارٍ التثبيت", "partial": "جزئي", "failed": "فشل"}
    bks = list_project_backups(pid)
    le = "" if running else last_error_line(_log_tail(p, 60))
    return {
        "monitor": monitor,
        "restarts": f"{st['total_restarts']} (الحد {st['max_restarts']})",
        "last_start": p[17] or "—", "last_stop": p[18] or "—",
        "reason": "" if running else (st.get("last_exit_reason") or ""),
        "install": ins_map.get(st.get("install_status"), "—"),
        "backup": (bks[0]["ts"] if bks else "لا توجد"),
        "last_error": le[:140],
        "state": ("running" if running else ("retry" if (nxt and nxt > now) else ("failed" if st.get("exhausted") else "stopped"))),
    }


@web.after_request
def _web_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path == "/" or request.path.startswith(("/start", "/stop", "/restart", "/logs", "/diag")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


_WEB_PAGE = ("<body style='background:#0f172a;color:#e2e8f0;text-align:center;padding:80px 20px;font-family:sans-serif'>"
             "{head}<h2>{body}</h2><a href='/?token={token}' style='color:#60a5fa'>رجوع للوحة</a></body>")


def _web_auth(pid):
    """(uid, project) أو (None, رد خطأ). الرمز من form (POST) أو query (GET للقراءة فقط)."""
    token = request.form.get("token") or request.args.get("token")
    uid = get_dashboard_user(token)
    if not uid:
        return None, ("🔒 غير مصرح", 403), token
    p = get_project(pid)
    if not p or not can_access_project(p, uid):
        return None, ("🚫 غير مصرح", 403), token
    return uid, p, token


def _web_action(pid, label, fn, need_approval=False):
    uid, p, token = _web_auth(pid)
    if uid is None:
        return p
    if request.method != "POST":
        # GET لا ينفّذ أي شيء: صفحة تأكيد بنموذج POST (يمنع التنفيذ عبر روابط مزروعة)
        return _WEB_PAGE.format(head="", token=esc(token or ""), body=(
            f"{esc(label)} «{esc(p[2])}»؟</h2><form method='post'><input type='hidden' name='token' value='{esc(token or '')}'>"
            "<button style='padding:12px 30px;border:0;border-radius:10px;background:#2563eb;color:#fff;font-size:1.1em;cursor:pointer'>تأكيد</button></form><h2>"))
    if need_approval and get_project_approved(pid) != 1:
        return _WEB_PAGE.format(head="", token=esc(token), body="⏳ المشروع بانتظار موافقة الإدارة أو مرفوض"), 403
    if _plock(pid).locked():
        return _WEB_PAGE.format(head="", token=esc(token), body="⏳ هناك عملية جارية على هذا المشروع"), 409
    run_bg(fn, pid)
    return (f"<meta http-equiv='refresh' content='6;url=/?token={esc(token)}'>" +
            _WEB_PAGE.format(head="", token=esc(token), body=f"⏳ {esc(label)} جارٍ… سيتحدّث الوضع خلال ثوانٍ"))


@web.route('/restart/<int:pid>', methods=['GET', 'POST'])
def w_restart(pid):
    return _web_action(pid, "إعادة تشغيل", lambda i: restart_project(i), need_approval=True)


@web.route('/diag/<int:pid>')
def w_diag(pid):
    uid, p, token = _web_auth(pid)
    if uid is None:
        return p
    res = diagnose_project(pid)
    rows = "".join(
        f"<div style='margin:10px 0;text-align:right'>{_DIAG_ICON.get(f['sev'], '•')} <b>{esc(f['title'])}</b>"
        + (f"<br><code style='color:#94a3b8'>{esc(mask_secrets(f['detail']))[:220]}</code>" if f.get('detail') else "")
        + (f"<br>💡 {esc(f['fix'])}" if f.get('fix') else "") + "</div>" for f in res["findings"])
    return ("<body style='background:#0f172a;color:#e2e8f0;font-family:sans-serif;padding:24px;max-width:760px;margin:auto'>"
            f"<h2>🧠 تشخيص {esc(res['project'])}</h2>{rows}"
            f"<p><a href='/logs/{pid}?token={esc(token)}' style='color:#86efac'>السجلات</a> · "
            f"<a href='/?token={esc(token)}' style='color:#60a5fa'>رجوع</a></p></body>")


import collections


import functools


_ANSWERED = collections.OrderedDict()


_orig_answer_callback_query = bot.answer_callback_query


def _answer_callback_query(callback_query_id, *a, **k):
    """يمنع إجابة الـ callback مرتين ويتجاهل أخطاء انتهاء الصلاحية (query is too old)."""
    cid = str(callback_query_id)
    if cid in _ANSWERED:
        return True
    _ANSWERED[cid] = 1
    while len(_ANSWERED) > 4000:
        _ANSWERED.popitem(last=False)
    try:
        return _orig_answer_callback_query(callback_query_id, *a, **k)
    except Exception as e:
        print(f"[CB] answer: {str(e)[:120]}")
        return False


bot.answer_callback_query = _answer_callback_query


def _log_handler_error(kind, fn, e):
    print(f"[Handler:{kind}] {getattr(fn, '__name__', fn)}: {e}\n{traceback.format_exc()[-700:]}")


def harden_handlers():
    """يلفّ كل المعالجات: استثناء داخل زر/رسالة يُسجَّل ويُعالَج بدل أن يُسقط حلقة الاستقصاء."""
    def wrap_cb(fn):
        if getattr(fn, "_hb_wrapped", False):
            return fn

        @functools.wraps(fn)
        def w(call):
            try:
                return fn(call)
            except Exception as e:
                _log_handler_error("cb", fn, e)
                try:
                    bot.answer_callback_query(call.id, "⚠️ حدث خطأ مؤقت، حاول مرة أخرى.")
                except Exception:
                    pass
            finally:
                try:
                    if str(call.id) not in _ANSWERED:
                        bot.answer_callback_query(call.id)
                except Exception:
                    pass
        w._hb_wrapped = True
        return w

    def wrap_msg(fn):
        if getattr(fn, "_hb_wrapped", False):
            return fn

        @functools.wraps(fn)
        def w(m):
            try:
                return fn(m)
            except Exception as e:
                _log_handler_error("msg", fn, e)
                try:
                    bot.send_message(m.chat.id, "⚠️ حدث خطأ مؤقت أثناء المعالجة، حاول مرة أخرى.")
                except Exception:
                    pass
        w._hb_wrapped = True
        return w

    n = 0
    for h in bot.callback_query_handlers:
        h["function"] = wrap_cb(h["function"]); n += 1
    for h in bot.message_handlers:
        h["function"] = wrap_msg(h["function"]); n += 1
    return n


@bot.callback_query_handler(func=lambda c: c.data == "noop")
def cb_noop(call):
    bot.answer_callback_query(call.id)


# ============================================================
# التشغيل
# ============================================================
def run_bot():
    print("[Bot] started")
    try:
        telebot.apihelper.RETRY_ON_ERROR = True
        telebot.apihelper.CONNECT_TIMEOUT = 15
        telebot.apihelper.READ_TIMEOUT = 40
    except Exception:
        pass
    try:
        bot.delete_webhook()
    except Exception:
        pass
    delay = 5
    while True:
        try:
            try:
                bot.infinity_polling(timeout=20, long_polling_timeout=25, logger_level=None)
            except TypeError:
                bot.polling(non_stop=True, timeout=30)
            delay = 5
        except Exception as e:
            print(f"[Bot] Polling error: {e}")
            time.sleep(delay)
            delay = min(60, delay * 2)


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
        fallback = get_free_port()
        print(f"[Web] Port {port} in use, switching to {fallback}")
        port = fallback
    for _ in range(10):
        try:
            print(f"[Web] http://0.0.0.0:{port}")
            web.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
            break
        except OSError as e:
            if "Address already in use" in str(e) or getattr(e, "errno", None) in (98, 48, 10048):
                port = get_free_port()
                print(f"[Web] Port busy, retrying on {port}")
                continue
            print(f"[Web] Error: {e}")
            break


# ============================================================
# نقطة البداية
# ============================================================
def _start_thread(fn, name, *a):
    t = threading.Thread(target=fn, args=a, daemon=True, name=name)
    t.start()
    return t


def main():
    web_only = "--web-only" in sys.argv or os.environ.get("WEB_ONLY") == "1"
    n_wrapped = harden_handlers()

    if web_only:
        print("=" * 55)
        print(f"{VERSION_NAME} - DEMO WEB MONITORING ONLY (OK)")
        print("=" * 55)
        _start_thread(keep_alive, "keep_alive")
        run_web()
        return

    print("=" * 55)
    print(f"{VERSION_NAME} - STARTING")
    print("=" * 55)
    print(f"[*] Handlers protected: {n_wrapped}")

    on_cloud = any([
        os.environ.get("RENDER_EXTERNAL_URL"),
        os.environ.get("KOYEB_PUBLIC_DOMAIN"),
        os.environ.get("RAILWAY_PUBLIC_DOMAIN"),
        os.environ.get("FLY_APP_NAME"),
    ])

    try:
        boot_recover()
    except Exception as e:
        print(f"[Recover] {e}")
    cleanup_stale_dirs()

    _start_thread(cloud_backup_loop, "cloud_backup")
    _start_thread(local_backup_loop, "local_backup")
    print("[*] Backup OK (verified)")
    _start_thread(keep_alive, "keep_alive")
    print("[*] Heartbeat OK")

    # مراقب واحد لكل المشاريع + استعادة ما كان شغّالاً قبل إعادة التشغيل
    _start_thread(supervisor_loop, "supervisor")
    _start_thread(restore_desired_states, "boot_restore", 3)
    print("[*] Supervisor OK")

    _start_thread(run_bot, "telegram_polling")
    print("[*] Bot OK")

    if not on_cloud:
        def delayed_tunnel():
            time.sleep(5)
            start_tunnel()
        _start_thread(delayed_tunnel, "tunnel")
        print("[*] Tunnel OK")
    else:
        print(f"[*] Cloud: {get_public_url()}")

    while True:                      # الويب هو الخيط الرئيسي؛ لا نسمح بأن يخرج البرنامج بسبب خطأ فيه
        try:
            run_web()
        except Exception as e:
            print(f"[Web] crashed: {e}")
        time.sleep(5)


if __name__ == "__main__":
    main()
