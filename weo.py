# -*- coding: utf-8 -*-
"""
💎 Business Bot v2 — منصّة إدارة حسابات تيليجرام (Telegram Business) متعددة العملاء
aiogram >= 3.25  |  Python 3.10+

• كل شخص يربط حسابه بالبوت (Business) تصله لوحته الخاصة بإعداداته وبياناته المستقلة.
• المالك يملك لوحة مخفية (/admin) للتحكم بكل شيء: العملاء، الأزرار، النصوص، الإذاعة...

المتغيرات البيئية:
  BOT_TOKEN (إجباري) | OWNER_ID (إجباري) | ADMIN_IDS | DB_PATH | PORT | REQUIRE_CONNECTION
  BOT_CHANNEL | DEV_CHANNEL | DEV_URL | DEV_BUTTON_TEXT | WELCOME_PHOTO_URL
"""
import asyncio
import ast
import html
import io
import logging
import operator
import os
import re
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone

try:
    import andro_cfw  # type: ignore

    andro_cfw.patch()
    print("✅ تم تفعيل البروكسي (andro-cfw).")
except ImportError:
    pass

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    BusinessConnection,
    BusinessMessagesDeleted,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("business-bot")

# ════════════════════════ الإعدادات ════════════════════════
BOT_TOKEN = os.getenv("BOT_TOKEN", "9062665659:AAE2ubEi1CWrrsRM83Pp0XyKMlqlelpTCvQ").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "7325566792") or 0)
EXTRA_ADMINS = {int(x) for x in re.findall(r"\d+", os.getenv("ADMIN_IDS", ""))}
ADMIN_IDS = frozenset(({OWNER_ID} | EXTRA_ADMINS) - {0})
DB_PATH = os.getenv("DB_PATH", "business_bot.db")
PORT = int(os.getenv("PORT", "8000"))
REQUIRE_CONNECTION = os.getenv("REQUIRE_CONNECTION", "1") != "0"
BOT_CHANNEL = os.getenv("BOT_CHANNEL", "")
DEV_CHANNEL = os.getenv("DEV_CHANNEL", "")
DEV_URL = os.getenv("DEV_URL", "")
DEV_BUTTON_TEXT = os.getenv("DEV_BUTTON_TEXT", "💲 | MODY .")
DEFAULT_PHOTO = os.getenv("WELCOME_PHOTO_URL", "")

DEFAULT_ON = {"bot_on", "auto_on", "welcome_on", "calc_on", "log_edit", "log_del", "notify_block", "icons_on",
              "require_conn"}

router = Router()
BG = set()
_prompted = {}  # (conn, uid) -> وقت آخر تنبيه اشتراك


class Form(StatesGroup):
    wait = State()


class U(StatesGroup):
    calc = State()


# ════════════════════════ قاعدة البيانات ════════════════════════
SCHEMA = """
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS users(uid INTEGER PRIMARY KEY, name TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS admins(uid INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS bconns(id TEXT PRIMARY KEY, owner INTEGER, chat INTEGER, fn TEXT, ln TEXT);
CREATE TABLE IF NOT EXISTS customers(conn TEXT, uid INTEGER, chat INTEGER, name TEXT, ts INTEGER, PRIMARY KEY(conn, uid));
CREATE TABLE IF NOT EXISTS cache(chat INTEGER, mid INTEGER, uid INTEGER, name TEXT, uname TEXT, text TEXT, ts INTEGER, PRIMARY KEY(chat, mid));
CREATE TABLE IF NOT EXISTS btn_cfg(key TEXT PRIMARY KEY, emoji_id TEXT, label TEXT, style TEXT);
CREATE TABLE IF NOT EXISTS tenants(tid INTEGER PRIMARY KEY, name TEXT, joined INTEGER, expires INTEGER DEFAULT 0, plan TEXT DEFAULT 'unlimited', blocked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS t_replies(id INTEGER PRIMARY KEY AUTOINCREMENT, tid INTEGER, kw TEXT, resp TEXT, UNIQUE(tid, kw));
CREATE TABLE IF NOT EXISTS t_banned(id INTEGER PRIMARY KEY AUTOINCREMENT, tid INTEGER, w TEXT, UNIQUE(tid, w));
CREATE TABLE IF NOT EXISTS t_muted(tid INTEGER, uid INTEGER, PRIMARY KEY(tid, uid));
CREATE TABLE IF NOT EXISTS t_media(id INTEGER PRIMARY KEY AUTOINCREMENT, tid INTEGER, kw TEXT, ctype TEXT, fid TEXT, cap TEXT, UNIQUE(tid, kw));
CREATE TABLE IF NOT EXISTS t_socials(id INTEGER PRIMARY KEY AUTOINCREMENT, tid INTEGER, name TEXT, url TEXT);
"""

db = None
_kv = {}
_cfg = {}
_admins = set()


def open_db():
    global db
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    db.commit()
    load_caches()
    migrate_v1()


def load_caches():
    _kv.clear()
    _kv.update({k: v for k, v in db.execute("SELECT k, v FROM kv")})
    _admins.clear()
    _admins.update(r[0] for r in db.execute("SELECT uid FROM admins"))
    _cfg.clear()
    for k, e, l, s in db.execute("SELECT key, emoji_id, label, style FROM btn_cfg"):
        _cfg[k] = {"emoji_id": e, "label": l, "style": s}


def q(sql, *a):
    return db.execute(sql, a).fetchall()


def ex(sql, *a):
    cur = db.execute(sql, a)
    db.commit()
    return cur.rowcount


def get(k, d=""):
    return _kv.get(k, d)


def put(k, v):
    _kv[k] = v
    db.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?, ?)", (k, v))
    db.commit()


def kv_del(k):
    _kv.pop(k, None)
    ex("DELETE FROM kv WHERE k=?", k)


def on(k, pre=""):
    return get(pre + k, "1" if k in DEFAULT_ON else "0") == "1"


def pfx(tid):
    return f"t{tid}:"


def esc(s):
    return html.escape(str(s or ""), quote=False)


def cut(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


# ---- ترحيل بيانات النسخة الأولى (إن وُجدت) إلى حساب المالك ----
TENANT_KV = ["bot_on", "auto_on", "welcome_on", "calc_on", "log_edit", "log_del", "notify_block", "blk_link",
             "blk_mention", "blk_forward", "blk_sticker", "blk_voice", "blk_media", "blk_doc", "blk_contact",
             "clock_on", "bio_on", "tz", "clock_fmt", "bio_text"]
TENANT_TXT = ["biz_welcome", "rates", "pay", "codes", "social"]


def migrate_v1():
    if _kv.get("migrated_v2") == "1":
        return
    owner = cur_owner()
    if owner:
        pre = pfx(owner)
        for k in TENANT_KV + ["txt_" + x for x in TENANT_TXT]:
            if k in _kv and pre + k not in _kv:
                put(pre + k, _kv[k])
        for sql in ("INSERT OR IGNORE INTO t_replies(tid,kw,resp) SELECT ?,kw,resp FROM replies",
                    "INSERT OR IGNORE INTO t_banned(tid,w) SELECT ?,w FROM banned",
                    "INSERT OR IGNORE INTO t_muted(tid,uid) SELECT ?,uid FROM muted",
                    "INSERT OR IGNORE INTO t_socials(tid,name,url) SELECT ?,name,url FROM socials",
                    "INSERT OR IGNORE INTO t_media(tid,kw,ctype,fid,cap) SELECT ?,kw,ctype,fid,cap FROM media"):
            try:
                db.execute(sql, (owner,))
            except sqlite3.Error:
                pass
        db.commit()
    put("migrated_v2", "1")


# ════════════════════════ الصلاحيات والعملاء (Tenants) ════════════════════════
def cur_owner():
    v = _kv.get("owner_id", "")
    return int(v) if v.isdigit() else OWNER_ID


def is_admin(uid):
    return uid in ADMIN_IDS or uid in _admins or uid == cur_owner()


def is_owner(uid):
    return uid == cur_owner() or uid in EXTRA_ADMINS


def need_conn():
    return REQUIRE_CONNECTION and _kv.get("require_conn", "1") == "1"


def is_connected(uid):
    return bool(q("SELECT 1 FROM bconns WHERE owner=?", uid))


def can_tenant(uid):
    """يحق له فتح لوحة العميل: مربوط بـ Business (أو مشرف ومشترط الربط معطّل)."""
    return is_connected(uid) or (is_admin(uid) and not need_conn())


class IsAdmin(Filter):
    async def __call__(self, event) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u and is_admin(u.id))


ADMIN = IsAdmin()


def t_row(tid):
    r = q("SELECT name, joined, expires, plan, blocked FROM tenants WHERE tid=?", tid)
    return r[0] if r else None


def ensure_tenant(tid, name=""):
    r = t_row(tid)
    if r:
        return r
    td = get("trial_days", "0")
    days = int(td) if td.isdigit() else 0
    if is_admin(tid) or days <= 0:
        exp, plan = 0, "unlimited"
    else:
        exp, plan = int(time.time()) + days * 86400, "trial"
    ex("INSERT OR IGNORE INTO tenants(tid, name, joined, expires, plan, blocked) VALUES(?,?,?,?,?,0)",
       tid, name, int(time.time()), exp, plan)
    return t_row(tid)


def active(tid):
    if is_admin(tid):
        return True
    _n, _j, exp, _p, blocked = t_row(tid) or ensure_tenant(tid)
    return not blocked and (exp == 0 or exp > time.time())


def fmt_left(sec):
    sec = max(0, int(sec))
    d, h = sec // 86400, (sec % 86400) // 3600
    parts = []
    if d:
        parts.append(f"{d} يوم")
    if h or not d:
        parts.append(f"{h} ساعة")
    return " و ".join(parts)


def status_info(tid):
    if is_admin(tid):
        return "👑 مالك (بلا حدود)", ""
    _n, _j, exp, plan, blocked = t_row(tid) or ensure_tenant(tid)
    if blocked:
        return "🚫 محظور", ""
    if exp and exp <= time.time():
        return "⌛ منتهي", ""
    label = {"trial": "🧪 نسخة تجريبية", "paid": "💎 اشتراك مفعّل"}.get(plan, "✅ مفعّل (بلا حدود)")
    return label, (fmt_left(exp - time.time()) if exp else "")


def tcount(table, tid):
    return q(f"SELECT COUNT(*) FROM {table} WHERE tid=?", tid)[0][0]


def my_customers(tid):
    return q("SELECT COUNT(*) FROM customers c JOIN bconns b ON b.id=c.conn WHERE b.owner=?", tid)[0][0]


# ════════════════════════ النصوص القابلة للتعديل ════════════════════════
EMPTY = "<i>لم يتم ضبط هذا القسم بعد.</i>"
TEXTS = {
    # ---- نصوص عامة (يعدّلها المالك) ----
    "welcome": ("رسالة ترحيب البوت", "👋 أهلاً بك يا <b>{name}</b> في نظام الإدارة المتقدم."),
    "intro": ("جملة التعريف (الاقتباس)", "أنا مساعدك الشخصي لتأمين وحماية حسابك وإدارة ردودك باحترافية تامة."),
    "force": ("رسالة الاشتراك الإجباري للبوت", "⚠️ لازم تشترك بالقناة أولاً لاستخدام البوت."),
    "panel": ("عنوان لوحة العميل", "👑 <b>لوحة التحكم</b>"),
    "howto": ("شرح ربط الحساب", "🔌 <b>كيف تربط حسابك؟</b>\n\n1️⃣ الإعدادات ← Business ← المحادثة الآلية\n2️⃣ اختر هذا البوت وأضفه\n3️⃣ ارجع هنا وستظهر لوحتك فوراً\n\n<i>يتطلب اشتراك Telegram Premium.</i>"),
    "expired": ("رسالة انتهاء الاشتراك", "⌛ انتهت مدة اشتراكك أو تم إيقاف حسابك. تواصل مع المطور للتجديد."),
    "calc": ("رسالة الآلة الحاسبة", "🧮 <b>الآلة الحاسبة</b>\n\nأرسل أي عملية حسابية وسأحسبها لك.\nمثال: <code>25*4+10</code>"),
    "no_connect": ("تنبيه عدم الربط", "⚠️ اربط البوت بحسابك أولاً من: الإعدادات ← Business ← المحادثة الآلية."),
    "connected": ("رسالة نجاح الربط", "✅ تم ربط حسابك بالبوت بنجاح! هذه لوحة التحكم الخاصة بك 👇"),
    "disconnected": ("رسالة فصل الربط", "❌ تم فصل البوت عن حسابك."),
    "on_label": ("كلمة «مفعّل» على الأزرار", "مفعّل"),
    "off_label": ("كلمة «معطّل» على الأزرار", "معطّل"),
    "panel_hint": ("سطر التوجيه أسفل اللوحة", "<i>اختر القسم الذي تريد إدارته 👇</i>"),
    "sub_first": ("تنبيه: اشترك أولاً", "اشترك بالقناة أولاً 📢"),
    "sub_not_yet": ("تنبيه: لم تشترك بعد", "لسا ما اشتركت بالقناة! 📢"),
    "no_socials": ("تنبيه: لا توجد حسابات", "لم تُضف حسابات بعد."),
    "calc_bad": ("خطأ الحاسبة (عملية غير صحيحة)", "⚠️ عملية غير صحيحة، مثال: <code>25*4+10</code>"),
    "calc_div0": ("خطأ الحاسبة (قسمة على صفر)", "⚠️ لا يمكن القسمة على صفر."),
    # ---- نصوص خاصة بكل عميل ----
    "biz_welcome": ("ترحيب عملائك", "👋 أهلاً بك <b>{name}</b>!\nكيف يمكنني مساعدتك اليوم؟"),
    "force_t": ("تنبيه اشتراك عملائك", "⚠️ للتواصل معنا يرجى الاشتراك أولاً في القناة: {channel}"),
    "rates": ("قسم أسعار العملات", "💱 <b>أسعار العملات</b>\n\n" + EMPTY),
    "pay": ("قسم طرق الدفع", "👛 <b>طرق الدفع</b>\n\n" + EMPTY),
    "codes": ("قسم أكواد التحويل", "🏷 <b>أكواد التحويل</b>\n\n" + EMPTY),
    "social": ("عنوان السوشيال ميديا", "🌐 <b>حساباتنا على السوشيال ميديا</b>"),
}
TENANT_TEXT_KEYS = {"biz_welcome", "force_t", "rates", "pay", "codes", "social"}
GLOBAL_TEXT_KEYS = [k for k in TEXTS if k not in TENANT_TEXT_KEYS]


def T(key, pre=""):
    return _kv.get(pre + "txt_" + key) or TEXTS[key][1]


def plain(key, pre=""):
    return html.unescape(re.sub(r"<[^>]+>", "", T(key, pre))).strip()


def render(text, name=""):
    return text.replace("{name}", esc(name))


# ════════════════════════ سجل الأزرار + دالة btn() ════════════════════════
BTN = {}  # key -> (label, emoji, default_style, group)


def reg(key, label, emoji="", style=None, group="general"):
    BTN[key] = (label, emoji, style, group)


reg("bot_channel", "قناة البوت", "🤖", "primary", "home")
reg("dev_channel", "قناة المطور", "💼", "primary", "home")
reg("dev_btn", DEV_BUTTON_TEXT, "", None, "home")
reg("u_rates", "أسعار العملات", "💱", "primary", "home")
reg("u_pay", "طرق الدفع", "👛", "primary", "home")
reg("u_codes", "أكواد التحويل", "🏷", "primary", "home")
reg("u_social", "السوشيال ميديا", "🌐", "primary", "home")
reg("u_calc", "الآلة الحاسبة", "🧮", "primary", "home")
reg("u_panel", "لوحة التحكم", "✏️", "primary", "home")
reg("u_connect", "ربط الحساب لفتح اللوحة", "⚠️", "danger", "home")
reg("u_howto", "ربط حسابي بالبوت", "🔌", "success", "home")
reg("back", "رجوع", "🔙", "danger", "general")
reg("close", "إغلاق", "✖️", "danger", "general")
reg("cancel", "إلغاء", "✖️", "danger", "general")
reg("del", "حذف", "🗑", "danger", "general")
reg("to_panel", "لوحة التحكم", "🎛", "primary", "general")
reg("check_sub", "تحققت", "✅", "success", "general")
reg("subscribe", "اشترك بالقناة", "📢", "primary", "general")
reg("social_item", "رابط", "🔗", "primary", "general")
for _k, _l, _e, _s in [
    ("cz_item", "عنصر", "", None), ("prev", "السابق", "◀️", None), ("next", "التالي", "▶️", None),
    ("c_emoji", "تعيين إيموجي مميز", "✨", "success"), ("c_label", "تعديل اسم الزر", "✏️", "primary"),
    ("c_noemoji", "حذف الإيموجي", "❌", "danger"), ("c_reset", "إعادة ضبط الزر", "♻️", "danger"),
    ("c_resetall", "إعادة ضبط كل الأزرار", "♻️", "danger"),
]:
    reg(_k, _l, _e, _s, "internal")

# لوحة العميل (تطابق الصورة) — الأول = يمين في الواجهة العربية
TENANT_PANEL = [
    [("p_toggle", "التفعيل والتعطيل", "🔒", "t:bot_on:home")],
    [("p_reply", "إدارة الردود التلقائية", "🤖", "m:reply"), ("p_welcome", "إدارة رسالة الترحيب", "👋", "m:welcome")],
    [("p_muted", "إدارة المكتومين", "🔕", "m:muted"), ("p_banned", "الكلمات الممنوعة", "⛔", "m:banned")],
    [("p_bc", "الإذاعة للعملاء", "📣", "m:bc"), ("p_force", "الاشتراك الإجباري", "📢", "m:force")],
    [("p_media", "إدارة الميديا", "🎞", "m:media"), ("p_social", "إدارة السوشيال ميديا", "🌐", "m:social")],
    [("p_rates", "أسعار العملات", "💱", "m:rates"), ("p_pay", "طرق الدفع", "👛", "m:pay")],
    [("p_calc", "الآلة الحاسبة", "🧮", "m:calc"), ("p_codes", "أكواد التحويل", "🏷", "m:codes")],
    [("p_clock", "الساعة الحية", "⏱", "m:clock"), ("p_bio", "البايو الحي", "🔄", "m:bio")],
    [("p_anti", "ضد الحذف والتعديل", "🛡", "m:anti"), ("p_shapes", "فحص أشكال الرسائل", "🔍", "m:shapes")],
]
# لوحة المالك المخفية
ADMIN_PANEL = [
    [("ad_tenants", "إدارة العملاء", "👥", "m:tenants"), ("ad_gbc", "الإذاعة العامة", "📣", "m:gbc")],
    [("ad_gwelcome", "ترحيب البوت وصورته", "👋", "m:gwelcome"), ("ad_gforce", "اشتراك البوت الإجباري", "📢", "m:gforce")],
    [("ad_custom", "تخصيص الأزرار (إيموجي مميز)", "🎨", "m:custom"), ("ad_texts", "نصوص البوت", "📝", "m:texts")],
    [("ad_links", "الروابط", "🔗", "m:links"), ("ad_admins", "المشرفون", "🛡", "m:admins")],
    [("ad_settings", "الإعدادات العامة", "⚙️", "m:settings"), ("ad_data", "النسخ الاحتياطي", "💾", "m:data")],
    [("ad_mine", "لوحتي كعميل", "🎛", "p:home")],
]
for _row in TENANT_PANEL:
    for _k, _l, _e, _d in _row:
        reg(_k, _l, _e, "primary", "panel")
for _row in ADMIN_PANEL:
    for _k, _l, _e, _d in _row:
        reg(_k, _l, _e, "primary", "admin")

TRIG_DEFAULT = {"rates": "اسعار,أسعار,الاسعار,الأسعار,اسعار العملات,أسعار العملات",
                "pay": "طرق الدفع,الدفع,دفع", "codes": "اكواد,أكواد,اكواد التحويل,أكواد التحويل",
                "social": "سوشيال,السوشيال,حساباتنا,السوشيال ميديا"}
SHAPE_ITEMS = [("t", "blk_link", "منع الروابط", "🔗"), ("t", "blk_mention", "منع المنشن", "📛"),
               ("t", "blk_forward", "منع المحوّلة", "↪️"), ("t", "blk_sticker", "منع الملصقات", "🎭"),
               ("t", "blk_voice", "منع الصوتيات", "🎙"), ("t", "blk_media", "منع الصور والفيديو", "🖼"),
               ("t", "blk_doc", "منع الملفات", "📎"), ("t", "blk_contact", "منع الجهات والمواقع", "📍"),
               ("t", "notify_block", "إشعاري عند الحذف", "🔔")]

# (إيموجي، عنوان، وصف، عناصر[(نوع، مفتاح، اسم، إيموجي)]) — الأنواع: t تبديل، a إجراء، e تعديل نص، v عرض نص
MENUS = {
    # ===== قوائم العميل =====
    "reply": ("🤖", "إدارة الردود التلقائية", "ردود تلقائية على كلمات عملائك داخل محادثاتك.", [
        ("t", "auto_on", "تفعيل الردود", "⚡"), ("a", "addrep", "إضافة رد", "➕"), ("a", "listrep", "عرض وحذف الردود", "📋")]),
    "welcome": ("👋", "إدارة رسالة الترحيب", "رسالة تُرسل لكل عميل جديد يراسلك (مع صورة إن أردت).", [
        ("t", "welcome_on", "تفعيل الترحيب", "👋"), ("e", "biz_welcome", "تعديل نص الترحيب", "✏️"),
        ("a", "photo", "تعيين صورة الترحيب", "🖼"), ("a", "photo_del", "حذف الصورة", "🗑")]),
    "muted": ("🔕", "إدارة المكتومين", "المكتوم تُحذف رسائله تلقائياً من محادثتك.", [
        ("a", "mute", "كتم مستخدم", "🔇"), ("a", "listmute", "المكتومون / فك الكتم", "📋")]),
    "banned": ("⛔", "الكلمات الممنوعة", "أي رسالة تحتوي كلمة ممنوعة تُحذف تلقائياً.", [
        ("a", "addban", "إضافة كلمة", "➕"), ("a", "listban", "عرض وحذف الكلمات", "📋")]),
    "bc": ("📣", "الإذاعة للعملاء", "أرسل رسالة جماعية لعملائك فقط.", [
        ("a", "bc", "إرسال إذاعة", "📣"), ("a", "stats", "إحصائياتي", "📊")]),
    "force": ("📢", "الاشتراك الإجباري", "يُلزم عملاءك بالاشتراك في قناتك قبل مراسلتك (يجب أن يكون البوت مشرفاً في القناة).", [
        ("a", "force_set", "تعيين قناة", "✏️"), ("a", "force_off", "إلغاء الاشتراك الإجباري", "🗑"),
        ("e", "force_t", "تعديل نص التنبيه", "💬")]),
    "media": ("🎞", "إدارة الميديا", "ميديا تُرسل تلقائياً عندما يكتب العميل كلمة مفتاحية.", [
        ("a", "media_add", "إضافة ميديا", "➕"), ("a", "media_list", "عرض وحذف الميديا", "📋")]),
    "social": ("🌐", "إدارة السوشيال ميديا", "روابط حساباتك. تُرسل للعميل عند كتابة كلمة التفعيل.", [
        ("a", "social_add", "إضافة رابط", "➕"), ("a", "social_list", "عرض وحذف الروابط", "📋"),
        ("e", "social", "تعديل العنوان", "✏️"), ("a", "trg_social", "كلمات التفعيل", "🔑")]),
    "rates": ("💱", "أسعار العملات", "عندما يكتب العميل إحدى كلمات التفعيل يرسل البوت هذا النص.", [
        ("e", "rates", "تعديل النص", "✏️"), ("v", "rates", "معاينة", "👁"), ("a", "trg_rates", "كلمات التفعيل", "🔑")]),
    "pay": ("👛", "طرق الدفع", "عندما يكتب العميل إحدى كلمات التفعيل يرسل البوت هذا النص.", [
        ("e", "pay", "تعديل النص", "✏️"), ("v", "pay", "معاينة", "👁"), ("a", "trg_pay", "كلمات التفعيل", "🔑")]),
    "codes": ("🏷", "أكواد التحويل", "عندما يكتب العميل إحدى كلمات التفعيل يرسل البوت هذا النص.", [
        ("e", "codes", "تعديل النص", "✏️"), ("v", "codes", "معاينة", "👁"), ("a", "trg_codes", "كلمات التفعيل", "🔑")]),
    "calc": ("🧮", "الآلة الحاسبة", "يكتب العميل <code>= 25*4</code> أو <code>احسب 25*4</code> فيجيبه البوت بالنتيجة.", [
        ("t", "calc_on", "تفعيل الحاسبة لعملائك", "🧮")]),
    "clock": ("⏱", "الساعة الحية", "يظهر الوقت بجانب اسمك ويتحدث كل دقيقة.", [
        ("t", "clock_on", "تفعيل الساعة", "⏱"), ("a", "tz", "فرق التوقيت", "🌍"), ("a", "clock_fmt", "صيغة الساعة", "🕐")]),
    "bio": ("🔄", "البايو الحي", "يتحدث البايو تلقائياً مع الوقت.", [
        ("t", "bio_on", "تفعيل البايو", "🔄"), ("a", "bio_set", "نص البايو", "✏️")]),
    "anti": ("🛡", "ضد الحذف والتعديل", "يصلك إشعار عند حذف أو تعديل العميل لرسالته.", [
        ("t", "log_edit", "رصد التعديل", "✏️"), ("t", "log_del", "رصد الحذف", "🗑")]),
    "shapes": ("🔍", "فحص أشكال الرسائل", "اختر أنواع الرسائل التي تُحذف من عملائك تلقائياً.", SHAPE_ITEMS),
    # ===== قوائم المالك =====
    "tenants": ("👥", "إدارة العملاء", "كل من ربط حسابه بالبوت. تحكّم بمدة اشتراكه وحظره.", [
        ("a", "tenants_list", "قائمة العملاء", "📋"), ("a", "trial_set", "مدة التجربة (أيام)", "🧪")]),
    "gbc": ("📣", "الإذاعة العامة", "رسالة لكل مستخدمي البوت والعملاء المربوطين.", [
        ("a", "gbc", "إرسال إذاعة عامة", "📣"), ("a", "gstats", "الإحصائيات العامة", "📊")]),
    "gwelcome": ("👋", "ترحيب البوت وصورته", "ما يراه كل مستخدم عند /start.", [
        ("e", "welcome", "تعديل ترحيب البوت", "📝"), ("e", "intro", "تعديل جملة التعريف", "💬"),
        ("a", "photo", "تغيير صورة الترحيب", "🖼"), ("a", "photo_del", "حذف الصورة", "🗑"),
        ("t", "calc_on", "الحاسبة في الواجهة", "🧮")]),
    "gforce": ("📢", "اشتراك البوت الإجباري", "إلزام مستخدمي البوت بالاشتراك في قناة قبل استخدامه.", [
        ("a", "force_set", "تعيين قناة", "✏️"), ("a", "force_off", "إلغاء الاشتراك الإجباري", "🗑")]),
    "links": ("🔗", "الروابط", "روابط أزرار الواجهة الرئيسية.", [
        ("a", "set_botch", "رابط قناة البوت", "🤖"), ("a", "set_devch", "رابط قناة المطور", "💼"),
        ("a", "set_devurl", "رابط زر المطور", "👤")]),
    "admins": ("🛡", "المشرفون", "أضف أو احذف مشرفين يدخلون لوحة المالك (للمالك فقط).", [
        ("a", "admin_add", "إضافة مشرف", "➕"), ("a", "admin_list", "عرض وحذف المشرفين", "📋")]),
    "settings": ("⚙️", "الإعدادات العامة", "إعدادات النظام. تغيير المالك وإعادة الضبط للمالك فقط.", [
        ("t", "require_conn", "اشتراط ربط Business للوحة", "🔗"), ("t", "bot_on", "تشغيل البوت للجميع", "🔒"),
        ("a", "owner_set", "تغيير المالك", "👑"), ("a", "reset_all", "إعادة ضبط كل الإعدادات", "♻️")]),
    "data": ("💾", "النسخ الاحتياطي", "احفظ نسخة من كل البيانات (مهم على الاستضافات المجانية).", [
        ("a", "backup", "تحميل نسخة", "📥"), ("a", "restore", "استرجاع نسخة", "📤")]),
}
ADMIN_MENUS = {"tenants", "gbc", "gwelcome", "gforce", "links", "admins", "settings", "data", "texts", "custom", "admin"}
for _m, (_me, _mt, _md, _items) in MENUS.items():
    for _kind, _key, _label, _em in _items:
        reg(f"{_m}_{_key}", _label, _em, None if _kind == "t" else "primary", "sub")
for _k, (_l, _d) in TEXTS.items():
    reg(f"texts_{_k}", _l, "📝", "primary", "internal")

GROUPS = {"home": "🏠 الواجهة الرئيسية", "panel": "🎛 لوحة العميل", "admin": "👑 لوحة المالك",
          "sub": "📂 القوائم الفرعية", "general": "⚙️ أزرار عامة"}
STYLE_NAMES = {"primary": "🔵 أزرق", "success": "🟢 أخضر", "danger": "🔴 أحمر", "none": "⚪ بدون لون"}


def pre_for(menu, uid):
    return "" if menu in ADMIN_MENUS else pfx(uid)


def allowed(uid, menu):
    return is_admin(uid) if menu in ADMIN_MENUS else can_tenant(uid)


def btn(key, data=None, style=None, url=None, text=None, suffix="", eid=None):
    """يبني InlineKeyboardButton مع (لون + إيموجي مميز + اسم) من إعدادات المالك."""
    label, emoji, dstyle, _ = BTN.get(key, (text or key, "", None, ""))
    cfg = _cfg.get(key) or {}
    label = (text or cfg.get("label") or label) + suffix
    icon = eid or (cfg.get("emoji_id") if on("icons_on") else None)
    cs = cfg.get("style")
    st = style or (None if cs == "none" else (cs or dstyle))
    kw = {"text": label if icon else (f"{emoji} {label}".strip() if emoji else label)}
    if url:
        kw["url"] = url
    else:
        kw["callback_data"] = data or "noop"
    if st:
        kw["style"] = st
    if icon:
        kw["icon_custom_emoji_id"] = icon
    return InlineKeyboardButton(**kw)


def raw(text, data, style=None):
    kw = {"text": text, "callback_data": data}
    if style:
        kw["style"] = style
    return InlineKeyboardButton(**kw)


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pairs(items):
    return [items[i:i + 2] for i in range(0, len(items), 2)]


def strip_icons(markup):
    rows = [[b.model_copy(update={"icon_custom_emoji_id": None}) for b in row] for row in markup.inline_keyboard]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def with_fallback(call, markup, **kw):
    """يرسل/يعدّل؛ وإذا رفض تيليجرام الإيموجي المميز يعيد المحاولة بدونه."""
    try:
        return await call(reply_markup=markup, **kw)
    except TelegramBadRequest as e:
        s = str(e).lower()
        if markup is not None and any(w in s for w in ("emoji", "icon", "style")):
            log.warning("تم رفض الإيموجي المميز، إعادة المحاولة بدونه: %s", e)
            return await call(reply_markup=strip_icons(markup), **kw)
        raise


def cfg_set(key, **fields):
    cur = dict(_cfg.get(key) or {"emoji_id": None, "label": None, "style": None})
    cur.update(fields)
    if not any(cur.values()):
        _cfg.pop(key, None)
        ex("DELETE FROM btn_cfg WHERE key=?", key)
        return
    _cfg[key] = cur
    ex("INSERT OR REPLACE INTO btn_cfg(key, emoji_id, label, style) VALUES(?,?,?,?)",
       key, cur["emoji_id"], cur["label"], cur["style"])


# ════════════════════════ أدوات ════════════════════════
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Mod: operator.mod,
       ast.FloorDiv: operator.floordiv}
_TR = {ord(a): b for a, b in zip("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹×÷٫،^", "01234567890123456789*/.,**")}


def _calc(node):
    if isinstance(node, ast.Expression):
        return _calc(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        l, r = _calc(node.left), _calc(node.right)
        if isinstance(node.op, ast.Pow) and (abs(r) > 100 or abs(l) > 1e6):
            raise ValueError("pow")
        return OPS[type(node.op)](l, r)
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_calc(node.operand))
    raise ValueError("bad")


def safe_calc(expr):
    expr = expr.translate(_TR).replace(",", ".").strip()
    expr = re.sub(r"(?<=[\d)])\s*[xX]\s*(?=[\d(])", "*", expr)
    if not expr or len(expr) > 120:
        raise ValueError("len")
    res = _calc(ast.parse(expr, mode="eval"))
    if isinstance(res, float):
        res = round(res, 10)
        if res.is_integer():
            res = int(res)
    return f"{res:,}" if isinstance(res, int) else str(res)


def now_str(pre=""):
    try:
        off = float(get(pre + "tz", "3") or 3)
    except ValueError:
        off = 3.0
    n = datetime.now(timezone(timedelta(hours=off)))
    return f"{(n.hour % 12) or 12}:{n.minute:02d} {'م' if n.hour >= 12 else 'ص'}"


def norm_url(u):
    u = (u or "").strip()
    if u.startswith("@"):
        return "https://t.me/" + u[1:]
    if re.match(r"^(https?://|tg://)", u, re.I):
        return u
    if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", u, re.I):
        return "https://" + u
    return ""


def channel_ref(raw_in):
    s = (raw_in or "").strip()
    m = re.search(r"t\.me/([A-Za-z0-9_]{4,})", s)
    if m:
        return "@" + m.group(1)
    if re.fullmatch(r"@?[A-Za-z0-9_]{4,}", s):
        return "@" + s.lstrip("@")
    return ""


def media_of(m):
    for t in ("photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker"):
        v = getattr(m, t, None)
        if v:
            return t, (v[-1].file_id if t == "photo" else v.file_id)
    return None, None


def extract_emoji_id(m):
    for e in (m.entities or []):
        if e.type == "custom_emoji":
            return e.custom_emoji_id
    if m.sticker and getattr(m.sticker, "custom_emoji_id", None):
        return m.sticker.custom_emoji_id
    t = (m.text or "").strip()
    return t if t.isdigit() and len(t) >= 10 else None


async def member_of(bot, channel, uid):
    try:
        mem = await bot.get_chat_member(channel, uid)
        return mem.status in ("member", "administrator", "creator")
    except Exception:
        return True  # إن تعذّر التحقق لا نمنع أحداً


async def subscribed(bot, uid):
    ch = get("force_channel")
    if not ch or is_admin(uid):
        return True
    return await member_of(bot, ch, uid)


async def send_text(bot, chat_id, text, conn=None, markup=None):
    kw = {"chat_id": chat_id, "text": text}
    if conn:
        kw["business_connection_id"] = conn
    try:
        return await with_fallback(bot.send_message, markup, **kw)
    except TelegramBadRequest as e:
        if any(w in str(e).lower() for w in ("parse", "entit")):
            kw["parse_mode"] = None
            return await with_fallback(bot.send_message, markup, **kw)
        raise


async def send_media(bot, chat_id, ctype, fid, caption=None, conn=None):
    kw = {"chat_id": chat_id, ctype: fid}
    if conn:
        kw["business_connection_id"] = conn
    if caption and ctype not in ("sticker", "video_note"):
        kw["caption"] = caption
    return await getattr(bot, "send_" + ctype)(**kw)


async def show(c, text, markup=None):
    bot, msg = c.bot, c.message
    if msg is None or not getattr(msg, "text", None):
        return await with_fallback(bot.send_message, markup, chat_id=c.from_user.id, text=text)
    try:
        await with_fallback(msg.edit_text, markup, text=text)
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        await with_fallback(bot.send_message, markup, chat_id=c.from_user.id, text=text)


async def deny(c):
    await c.answer("🚫 غير مصرّح لك.", show_alert=True)


# ════════════════════════ الواجهة الرئيسية (للجميع) ════════════════════════
def home_text(user):
    return render(T("welcome"), user.full_name) + f"\n<blockquote>{T('intro')}</blockquote>"


def home_kb(uid):
    rows = []
    bc, dc, du = get("bot_channel", BOT_CHANNEL), get("dev_channel", DEV_CHANNEL), get("dev_url", DEV_URL)
    top = []
    if norm_url(bc):
        top.append(btn("bot_channel", url=norm_url(bc), suffix=" ↗"))
    if norm_url(dc):
        top.append(btn("dev_channel", url=norm_url(dc), suffix=" ↗"))
    if top:
        rows.append(top)
    if norm_url(du):
        rows.append([btn("dev_btn", url=norm_url(du), suffix=" ↗")])
    rows.append([btn("u_rates", "u:rates"), btn("u_pay", "u:pay")])
    rows.append([btn("u_codes", "u:codes"), btn("u_social", "u:social")])
    if on("calc_on"):
        rows.append([btn("u_calc", "u:calc")])
    if can_tenant(uid):
        rows.append([btn("u_panel", "p:home")])
    elif is_admin(uid):
        rows.append([btn("u_connect", "connect_warning")])
    else:
        rows.append([btn("u_howto", "howto")])
    return kb(rows)


async def send_home(bot, chat_id, user):
    text, markup = home_text(user), home_kb(user.id)
    photo = get("welcome_photo", "")
    photo = None if photo == "-" else (photo or DEFAULT_PHOTO)
    if photo:
        try:
            return await with_fallback(bot.send_photo, markup, chat_id=chat_id, photo=photo, caption=text)
        except TelegramBadRequest as e:
            log.warning("فشل إرسال صورة الترحيب: %s", e)
    return await send_text(bot, chat_id, text, markup=markup)


async def send_force(bot, chat_id):
    ch = get("force_channel")
    rows = []
    if ch:
        rows.append([btn("subscribe", url=f"https://t.me/{ch.lstrip('@')}")])
    rows.append([btn("check_sub", "check_sub")])
    await send_text(bot, chat_id, T("force"), markup=kb(rows))


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(m: Message, bot: Bot, state: FSMContext):
    await state.clear()
    ex("INSERT OR REPLACE INTO users(uid, name, ts) VALUES(?,?,?)", m.from_user.id, m.from_user.full_name, int(time.time()))
    if not await subscribed(bot, m.from_user.id):
        return await send_force(bot, m.chat.id)
    await send_home(bot, m.chat.id, m.from_user)


@router.message(Command("cancel"), F.chat.type == "private")
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("✖️ تم الإلغاء.")


@router.message(Command("panel"), F.chat.type == "private")
async def cmd_panel(m: Message, state: FSMContext):
    await state.clear()
    if not can_tenant(m.from_user.id):
        return await m.answer(T("howto"))
    await with_fallback(m.answer, tenant_kb(), text=tenant_header(m.from_user.id))


@router.message(Command("admin"), F.chat.type == "private", ADMIN)
async def cmd_admin(m: Message, state: FSMContext):
    """أمر مخفي: لا يرد على غير المشرفين إطلاقاً."""
    await state.clear()
    await with_fallback(m.answer, admin_kb(), text=admin_header())


@router.message(F.photo, F.caption.startswith("/setwelcome"), F.chat.type == "private", ADMIN)
async def cmd_setwelcome(m: Message, bot: Bot):
    put("welcome_photo", m.photo[-1].file_id)
    await m.answer("✅ تم تعيين صورة الترحيب. هذه معاينتها:")
    await send_home(bot, m.chat.id, m.from_user)


@router.message(Command("calc"), F.chat.type == "private")
async def cmd_calc(m: Message):
    args = (m.text or "").split(maxsplit=1)
    if len(args) < 2:
        return await m.answer("اكتب العملية بعد الأمر، مثال:\n<code>/calc 25*4+10</code>")
    try:
        await m.answer(f"🧮 <code>{esc(args[1])}</code>\n✅ <b>{safe_calc(args[1])}</b>")
    except Exception:
        await m.answer(T("calc_bad"))


@router.callback_query(F.data == "check_sub")
async def cb_check_sub(c: CallbackQuery, bot: Bot):
    if await subscribed(bot, c.from_user.id):
        try:
            await c.message.delete()
        except Exception:
            pass
        await send_home(bot, c.from_user.id, c.from_user)
        await c.answer()
    else:
        await c.answer(plain("sub_not_yet"), show_alert=True)


@router.callback_query(F.data == "connect_warning")
async def cb_connect_warning(c: CallbackQuery):
    await c.answer(cut(plain("no_connect"), 190), show_alert=True)


@router.callback_query(F.data == "howto")
async def cb_howto(c: CallbackQuery, bot: Bot):
    await send_text(bot, c.from_user.id, T("howto"), markup=kb([[btn("close", "close")]]))
    await c.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(c: CallbackQuery):
    await c.answer()


@router.callback_query(F.data == "close")
async def cb_close(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.answer()


@router.callback_query(F.data.startswith("u:"))
async def cb_user(c: CallbackQuery, bot: Bot, state: FSMContext):
    if not await subscribed(bot, c.from_user.id):
        return await c.answer(plain("sub_first"), show_alert=True)
    sec, op = c.data[2:], pfx(cur_owner())
    close = [btn("close", "close")]
    if sec == "calc":
        await state.set_state(U.calc)
        await send_text(bot, c.from_user.id, T("calc"), markup=kb([close]))
    elif sec in ("rates", "pay", "codes"):
        await send_text(bot, c.from_user.id, T(sec, op), markup=kb([close]))
    elif sec == "social":
        items = [btn("social_item", url=norm_url(u), text=n)
                 for n, u in q("SELECT name, url FROM t_socials WHERE tid=? ORDER BY id", cur_owner()) if norm_url(u)]
        if not items:
            return await c.answer(plain("no_socials"), show_alert=True)
        await send_text(bot, c.from_user.id, T("social", op), markup=kb(pairs(items) + [close]))
    await c.answer()


@router.message(StateFilter(U.calc), F.chat.type == "private", F.text)
async def calc_input(m: Message):
    t = m.text.strip()
    if t.startswith("/"):
        return await m.answer("أرسل عملية حسابية فقط، أو /start للعودة.")
    try:
        await m.answer(f"🧮 <code>{esc(t)}</code>\n✅ <b>{safe_calc(t)}</b>")
    except ZeroDivisionError:
        await m.answer(T("calc_div0"))
    except Exception:
        await m.answer(T("calc_bad"))


# ════════════════════════ اللوحات ════════════════════════
def tenant_header(uid):
    label, left = status_info(uid)
    running = on("bot_on") and on("bot_on", pfx(uid))
    lines = [f"📌 حالة البوت: <b>{'✅ مفعّل' if running else '⛔ معطّل'}</b>", f"🪪 حالة الحساب: <b>{label}</b>"]
    if left:
        lines.append(f"⏳ المدة المتبقية: <b>{left}</b>")
    lines.append(f"👥 عملاؤك: <b>{my_customers(uid)}</b>")
    text = f"{T('panel')}\n\n<blockquote>" + "\n".join(lines) + "</blockquote>\n\n" + T("panel_hint")
    if not active(uid):
        text += "\n\n" + T("expired")
    return text


def tenant_kb():
    return kb([[btn(k, d) for k, _, _, d in row] for row in TENANT_PANEL])


def admin_header():
    n = lambda s: q(s)[0][0]
    act = sum(1 for (t,) in q("SELECT tid FROM tenants") if active(t))
    return ("🕶 <b>لوحة المالك (مخفية)</b>\n\n<blockquote>"
            f"👥 مستخدمو البوت: <b>{n('SELECT COUNT(*) FROM users')}</b>\n"
            f"🔗 حسابات مربوطة: <b>{n('SELECT COUNT(*) FROM bconns')}</b>\n"
            f"🧾 العملاء المسجلون: <b>{n('SELECT COUNT(*) FROM tenants')}</b> (فعّال: {act})\n"
            f"🛡 المشرفون: <b>{len(_admins) + 1}</b></blockquote>\n\n"
            "<i>هذه اللوحة لا تظهر لأحد غيرك. افتحها بالأمر /admin</i>")


def admin_kb():
    return kb([[btn(k, d) for k, _, _, d in row] for row in ADMIN_PANEL])


def menu_extra(name, uid):
    pre = pre_for(name, uid)
    if name == "force":
        return f"📢 القناة الحالية: <b>{esc(get(pre + 'force_channel')) or 'غير مضبوطة'}</b>"
    if name == "gforce":
        return f"📢 القناة الحالية: <b>{esc(get('force_channel')) or 'غير مضبوطة'}</b>"
    if name == "clock":
        return f"🌍 فرق التوقيت: <b>UTC{esc(get(pre + 'tz', '3'))}</b>\n🕐 الصيغة: <code>{esc(get(pre + 'clock_fmt', '🕐 {time}'))}</code>"
    if name == "bio":
        return f"📝 النص الحالي: <code>{esc(get(pre + 'bio_text', '🕐 {time}'))}</code>"
    if name == "links":
        return (f"🤖 قناة البوت: {esc(get('bot_channel', BOT_CHANNEL)) or '—'}\n"
                f"💼 قناة المطور: {esc(get('dev_channel', DEV_CHANNEL)) or '—'}\n"
                f"👤 زر المطور: {esc(get('dev_url', DEV_URL)) or '—'}")
    if name == "welcome":
        p = get(pre + "welcome_photo", "")
        return "🖼 الصورة: " + ("<b>مخصصة ✅</b>" if p and p != "-" else "<b>بدون صورة</b>")
    if name == "gwelcome":
        p = get("welcome_photo", "")
        return ("🖼 الصورة: " + ("<b>محذوفة</b>" if p == "-" else "<b>مخصصة ✅</b>" if p else "<b>افتراضية</b>")
                + "\n💡 نصيحة: أرسل أي صورة للبوت بتعليق <code>/setwelcome</code> لتعيينها فوراً.")
    if name in ("rates", "pay", "codes", "social"):
        trg = get(pre + "trg_" + name, TRIG_DEFAULT[name])
        return f"🔑 كلمات التفعيل: <code>{esc(trg)}</code>"
    if name == "tenants":
        return f"🧪 مدة التجربة للعملاء الجدد: <b>{esc(get('trial_days', '0'))}</b> يوم (0 = بلا حدود)"
    if name == "settings":
        return (f"👑 المالك: <code>{cur_owner()}</code>\n🔗 شرط الربط: <b>{'مفعّل' if need_conn() else 'معطّل'}</b>"
                + ("" if REQUIRE_CONNECTION else "\n<i>(معطّل بالمتغير REQUIRE_CONNECTION=0)</i>"))
    if name == "admins":
        return f"👑 المالك: <code>{cur_owner()}</code>\n🛡 المشرفون المضافون: <b>{len(_admins)}</b>"
    counts = {"reply": "t_replies", "banned": "t_banned", "muted": "t_muted", "media": "t_media"}
    if name in counts:
        return f"📦 العدد الحالي: <b>{tcount(counts[name], uid)}</b>"
    return ""


def menu_view(name, uid):
    emoji, title, desc, items = MENUS[name]
    pre = pre_for(name, uid)
    extra = menu_extra(name, uid)
    text = f"{emoji} <b>{title}</b>\n\n{desc}" + (f"\n\n{extra}" if extra else "")
    rows, pend = [], []
    for kind, key, _label, _em in items:
        bk = f"{name}_{key}"
        if kind == "t":
            if pend:
                rows.append(pend)
                pend = []
            st = on(key, pre)
            rows.append([btn(bk, f"t:{key}:{name}", style="success" if st else "danger",
                             suffix=": " + plain("on_label" if st else "off_label"))])
            continue
        pend.append(btn(bk, {"a": f"a:{key}:{name}", "e": f"e:{key}:{name}", "v": f"v:{key}:{name}"}[kind]))
        if len(pend) == 2:
            rows.append(pend)
            pend = []
    if pend:
        rows.append(pend)
    rows.append([btn("back", "p:admin" if name in ADMIN_MENUS else "p:home")])
    return text, kb(rows)


def texts_view():
    items = [btn(f"texts_{k}", f"e:{k}:texts") for k in GLOBAL_TEXT_KEYS]
    text = ("📝 <b>نصوص البوت</b>\n\nكل النصوص العامة قابلة للتعديل. تدعم التنسيق والإيموجي المميز، "
            "والمتغير <code>{name}</code> يُستبدل باسم المستخدم.")
    return text, kb(pairs(items) + [[btn("back", "p:admin")]])


def custom_home():
    text = ("🎨 <b>تخصيص أزرار البوت</b>\n\n"
            "اختر مجموعة، ثم الزر لتعيين <b>إيموجي مميز</b> أو تغيير اسمه ولونه.\n"
            "التغييرات تُطبّق فوراً على <b>كل لوحات العملاء</b> وكل أماكن الزر.\n\n"
            "⚠️ الإيموجي المميز على الأزرار يتطلب أن يكون <b>مالك البوت مشتركاً في Telegram Premium</b>.")
    rows = pairs([btn("cz_item", f"cz:{g}:0", text=t) for g, t in GROUPS.items()])
    ion = on("icons_on")
    rows.append([btn("cz_item", "t:icons_on:custom", style="success" if ion else "danger",
                     text="الإيموجي المميز: " + ("مفعّل" if ion else "معطّل"))])
    rows.append([btn("c_resetall", "cr_all"), btn("back", "p:admin")])
    return text, kb(rows)


def view_of(name, uid):
    if name == "home":
        return tenant_header(uid), tenant_kb()
    if name == "admin":
        return admin_header(), admin_kb()
    if name == "custom":
        return custom_home()
    if name == "texts":
        return texts_view()
    return menu_view(name, uid)


@router.callback_query(F.data == "p:home")
async def cb_panel_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    if not can_tenant(c.from_user.id):
        return await c.answer(cut(plain("no_connect"), 190), show_alert=True)
    await show(c, tenant_header(c.from_user.id), tenant_kb())
    await c.answer()


@router.callback_query(F.data == "p:admin", ADMIN)
async def cb_panel_admin(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, admin_header(), admin_kb())
    await c.answer()


@router.callback_query(F.data.startswith("m:"))
async def cb_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    name = c.data[2:]
    if name not in MENUS and name not in ("custom", "texts"):
        return await c.answer()
    if not allowed(c.from_user.id, name):
        return await deny(c)
    text, markup = view_of(name, c.from_user.id)
    await show(c, text, markup)
    await c.answer()


# ---- الساعة والبايو ----
async def reset_names(bot, uid):
    for cid, fn, ln in q("SELECT id, fn, ln FROM bconns WHERE owner=?", uid):
        try:
            await bot.set_business_account_name(business_connection_id=cid, first_name=fn, last_name=ln or None)
        except Exception as e:
            log.warning("reset name: %s", e)


async def bio_enable(bot, uid):
    for (cid,) in q("SELECT id FROM bconns WHERE owner=?", uid):
        try:
            ch = await bot.get_chat(uid)
            put(f"orig_bio_{cid}", getattr(ch, "bio", None) or "")
        except Exception as e:
            log.warning("orig bio: %s", e)


async def bio_restore(bot, uid):
    for (cid,) in q("SELECT id FROM bconns WHERE owner=?", uid):
        try:
            await bot.set_business_account_bio(business_connection_id=cid, bio=get(f"orig_bio_{cid}") or None)
        except Exception as e:
            log.warning("restore bio: %s", e)


@router.callback_query(F.data.startswith("t:"))
async def cb_toggle(c: CallbackQuery, bot: Bot):
    _, key, back = c.data.split(":", 2)
    uid = c.from_user.id
    if not allowed(uid, back):
        return await deny(c)
    pre = pre_for(back, uid)
    new = not on(key, pre)
    put(pre + key, "1" if new else "0")
    if pre:
        if key == "clock_on" and not new:
            await reset_names(bot, uid)
        if key == "bio_on":
            await (bio_enable(bot, uid) if new else bio_restore(bot, uid))
    text, markup = view_of(back, uid)
    await show(c, text, markup)
    await c.answer("✅ مفعّل" if new else "⛔ معطّل")


# ---- القوائم القابلة للحذف ----
LISTS = {
    "listrep": ("rep", "🤖 الردود التلقائية", "SELECT id, kw FROM t_replies WHERE tid=? ORDER BY id DESC LIMIT 40", True),
    "listban": ("ban", "⛔ الكلمات الممنوعة", "SELECT id, w FROM t_banned WHERE tid=? ORDER BY id DESC LIMIT 40", True),
    "listmute": ("mute", "🔕 المكتومون",
                 "SELECT m.uid, COALESCE((SELECT name FROM customers c WHERE c.uid=m.uid LIMIT 1), m.uid) FROM t_muted m WHERE m.tid=? LIMIT 40", True),
    "social_list": ("soc", "🌐 روابط السوشيال", "SELECT id, name FROM t_socials WHERE tid=? ORDER BY id LIMIT 40", True),
    "media_list": ("med", "🎞 الميديا", "SELECT id, kw FROM t_media WHERE tid=? ORDER BY id DESC LIMIT 40", True),
    "admin_list": ("adm", "👥 المشرفون", "SELECT uid, uid FROM admins ORDER BY uid LIMIT 40", False),
}
# kind -> (table, col, list action, menu, scoped)
DELS = {"rep": ("t_replies", "id", "listrep", "reply", True), "ban": ("t_banned", "id", "listban", "banned", True),
        "mute": ("t_muted", "uid", "listmute", "muted", True), "soc": ("t_socials", "id", "social_list", "social", True),
        "med": ("t_media", "id", "media_list", "media", True), "adm": ("admins", "uid", "admin_list", "admins", False)}


def list_view(action, uid):
    kind, title, sql, scoped = LISTS[action]
    menu = DELS[kind][3]
    rows_db = q(sql, uid) if scoped else q(sql)
    if not rows_db:
        return f"{title}\n\n<i>لا توجد عناصر.</i>", kb([[btn("back", f"m:{menu}")]])
    items = [btn("del", f"x:{kind}:{i}", text=cut(n, 26)) for i, n in rows_db]
    return f"{title}\n\n<i>اضغط على أي عنصر لحذفه 👇</i>", kb(pairs(items) + [[btn("back", f"m:{menu}")]])


@router.callback_query(F.data.startswith("x:"))
async def cb_delete_item(c: CallbackQuery):
    _, kind, ident = c.data.split(":", 2)
    if kind not in DELS:
        return await c.answer()
    uid = c.from_user.id
    table, col, action, menu, scoped = DELS[kind]
    if not allowed(uid, menu) or (kind == "adm" and not is_owner(uid)):
        return await deny(c)
    if scoped:
        ex(f"DELETE FROM {table} WHERE {col}=? AND tid=?", int(ident), uid)
    else:
        ex(f"DELETE FROM {table} WHERE {col}=?", int(ident))
        if kind == "adm":
            _admins.discard(int(ident))
    text, markup = list_view(action, uid)
    await show(c, text, markup)
    await c.answer("🗑 تم الحذف")


def stats_text(uid):
    return ("📊 <b>إحصائياتي</b>\n\n"
            f"🧾 عملاؤك: <b>{my_customers(uid)}</b>\n"
            f"🤖 الردود: <b>{tcount('t_replies', uid)}</b>\n"
            f"⛔ الكلمات الممنوعة: <b>{tcount('t_banned', uid)}</b>\n"
            f"🔕 المكتومون: <b>{tcount('t_muted', uid)}</b>\n"
            f"🎞 الميديا: <b>{tcount('t_media', uid)}</b>\n"
            f"🌐 الروابط: <b>{tcount('t_socials', uid)}</b>")


def gstats_text():
    n = lambda s: q(s)[0][0]
    return ("📊 <b>الإحصائيات العامة</b>\n\n"
            f"👥 مستخدمو البوت: <b>{n('SELECT COUNT(*) FROM users')}</b>\n"
            f"🧾 العملاء المسجلون: <b>{n('SELECT COUNT(*) FROM tenants')}</b>\n"
            f"🔗 حسابات مربوطة: <b>{n('SELECT COUNT(*) FROM bconns')}</b>\n"
            f"👤 عملاء الحسابات (Business): <b>{n('SELECT COUNT(*) FROM customers')}</b>\n"
            f"🤖 كل الردود: <b>{n('SELECT COUNT(*) FROM t_replies')}</b>")


PROMPTS = {
    "addrep": "➕ <b>إضافة رد تلقائي</b>\nأرسل بالصيغة:\n<code>الكلمة | الرد</code>\n\nالرد يدعم التنسيق والإيموجي المميز.",
    "addban": "⛔ أرسل الكلمة أو العبارة الممنوعة:",
    "mute": "🔇 أرسل آيدي المستخدم (رقم) المراد كتمه:",
    "bc": "📣 أرسل نص الإذاعة لعملائك (يدعم التنسيق):",
    "gbc": "📣 أرسل نص الإذاعة العامة (يدعم التنسيق):",
    "force_set": "📢 أرسل يوزر القناة مثل <code>@channel</code> (يجب أن يكون البوت مشرفاً فيها):",
    "set_botch": "🤖 أرسل رابط قناة البوت (<code>@user</code> أو رابط https):",
    "set_devch": "💼 أرسل رابط قناة المطور (<code>@user</code> أو رابط https):",
    "set_devurl": "👤 أرسل رابط زر المطور (<code>@user</code> أو رابط https):",
    "social_add": "🌐 أرسل بالصيغة:\n<code>الاسم | الرابط</code>\nمثال: <code>انستغرام | https://instagram.com/user</code>",
    "media_add": "🎞 أرسل الميديا (صورة / فيديو / GIF / ملف / صوت / ملصق):",
    "tz": "🌍 أرسل فرق التوقيت عن UTC (مثال: <code>3</code> أو <code>5.5</code> أو <code>-4</code>):",
    "clock_fmt": "⏱ أرسل صيغة الساعة، واستخدم <code>{time}</code> للوقت.\nمثال: <code>🕐 {time}</code>",
    "bio_set": "🔄 أرسل نص البايو (حتى 70 حرفاً) واستخدم <code>{time}</code> للوقت:",
    "photo": "🖼 أرسل الصورة المطلوبة (أو رابط صورة مباشر):",
    "restore": "♻️ أرسل ملف النسخة الاحتياطية (<code>.db</code>)\n⚠️ سيتم استبدال كل البيانات الحالية.",
    "admin_add": "🛡 أرسل آيدي المشرف الجديد (رقم). ليستلم الإشعار يجب أن يكون قد بدأ البوت:",
    "owner_set": "👑 أرسل آيدي المالك الجديد (رقم). ⚠️ ستفقد صلاحية المالك فوراً:",
    "trial_set": "🧪 أرسل عدد أيام التجربة للعملاء الجدد (<code>0</code> = بلا حدود):",
}
for _k in ("rates", "pay", "codes", "social"):
    PROMPTS["trg_" + _k] = ("🔑 أرسل كلمات التفعيل مفصولة بفاصلة. عندما يكتب العميل إحداها يرد البوت بهذا القسم.\n"
                            f"الافتراضي: <code>{TRIG_DEFAULT[_k]}</code>")
OWNER_ACTS = {"admin_add", "admin_list", "owner_set", "reset_all", "reset_do", "restore", "backup"}
GLOBAL_ACTS = {"set_botch", "set_devch", "set_devurl", "gbc", "owner_set", "admin_add", "trial_set", "restore",
               "label", "icon"}


async def ask(c, state, action, prompt, back, **extra):
    await state.set_state(Form.wait)
    await state.update_data(action=action, back=back, **extra)
    await send_text(c.bot, c.from_user.id, prompt, markup=kb([[btn("cancel", "cancel")]]))


@router.callback_query(F.data == "cancel")
async def cb_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "✖️ تم الإلغاء.", kb([[btn("to_panel", "p:home")]]))
    await c.answer()


@router.callback_query(F.data.startswith("a:"))
async def cb_action(c: CallbackQuery, state: FSMContext, bot: Bot):
    _, a, menu = c.data.split(":", 2)
    uid = c.from_user.id
    if not allowed(uid, menu) or (a in OWNER_ACTS and not is_owner(uid)):
        return await deny(c)
    pre, back = pre_for(menu, uid), f"m:{menu}"
    if a in PROMPTS:
        await ask(c, state, a, PROMPTS[a], back, pre=pre, menu=menu)
    elif a in LISTS:
        text, markup = list_view(a, uid)
        await show(c, text, markup)
    elif a == "stats":
        await show(c, stats_text(uid), kb([[btn("back", back)]]))
    elif a == "gstats":
        await show(c, gstats_text(), kb([[btn("back", back)]]))
    elif a == "tenants_list":
        text, markup = tenants_list_view(0)
        await show(c, text, markup)
    elif a == "photo_del":
        put(pre + "welcome_photo", "-")
        text, markup = view_of(menu, uid)
        await show(c, text, markup)
    elif a == "force_off":
        kv_del(pre + "force_channel")
        text, markup = view_of(menu, uid)
        await show(c, text, markup)
    elif a == "reset_all":
        await show(c, "⚠️ <b>إعادة ضبط كل الإعدادات العامة؟</b>\n\nسيُحذف: النصوص المعدّلة، تخصيص الأزرار، الروابط، "
                      "الاشتراك الإجباري، وصورة الترحيب، وإعدادات كل العملاء.\nلن يُحذف: بيانات العملاء (الردود، المكتومون...) والمشرفون.",
                   kb([[raw("✅ نعم، أعد الضبط", "a:reset_do:settings", "danger"), btn("back", "m:settings")]]))
    elif a == "reset_do":
        keep = {k: v for k, v in _kv.items() if k in ("owner_id", "migrated_v2")}
        ex("DELETE FROM kv")
        ex("DELETE FROM btn_cfg")
        _kv.clear()
        _cfg.clear()
        for k, v in keep.items():
            put(k, v)
        text, markup = view_of("settings", uid)
        await show(c, text, markup)
    elif a == "backup":
        tmp = os.path.join(tempfile.gettempdir(), f"bk_{int(time.time())}.db")
        dst = sqlite3.connect(tmp)
        db.backup(dst)
        dst.close()
        with open(tmp, "rb") as f:
            data = f.read()
        os.remove(tmp)
        await bot.send_document(uid, BufferedInputFile(data, f"business_bot_{datetime.now().strftime('%Y%m%d_%H%M')}.db"),
                                caption="💾 نسخة احتياطية كاملة. احتفظ بها في مكان آمن.")
    await c.answer()


@router.callback_query(F.data.startswith("e:"))
async def cb_edit_text(c: CallbackQuery, state: FSMContext):
    _, key, back = c.data.split(":", 2)
    uid = c.from_user.id
    if key not in TEXTS or not allowed(uid, back):
        return await deny(c) if key in TEXTS else await c.answer()
    prompt = (f"✏️ <b>{TEXTS[key][0]}</b>\n\nأرسل النص الجديد (يدعم التنسيق والإيموجي المميز).\n"
              "المتغير <code>{name}</code> = اسم المستخدم.\n↩️ أرسل /reset لاستعادة النص الافتراضي.")
    await ask(c, state, "text", prompt, f"m:{back}", key=key, pre=pre_for(back, uid), menu=back)
    await c.answer()


@router.callback_query(F.data.startswith("v:"))
async def cb_view_text(c: CallbackQuery):
    _, key, back = c.data.split(":", 2)
    uid = c.from_user.id
    if key not in TEXTS or not allowed(uid, back):
        return await deny(c)
    await show(c, render(T(key, pre_for(back, uid)), c.from_user.full_name), kb([[btn("back", f"m:{back}")]]))
    await c.answer()


# ---- إدارة العملاء (للمالك) ----
def tenants_list_view(page):
    rows_db = q("SELECT tid, name FROM tenants ORDER BY joined DESC")
    pages = max(1, (len(rows_db) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    items = []
    for tid, name in rows_db[page * PAGE:(page + 1) * PAGE]:
        icon = "👑" if is_admin(tid) else ("🟢" if active(tid) else "🔴")
        items.append(btn("cz_item", f"tn:{tid}", text=f"{icon} {cut(name or tid, 18)}"))
    rows = pairs(items)
    nav = []
    if page > 0:
        nav.append(btn("prev", f"tl:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("next", f"tl:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("back", "m:tenants")])
    return f"👥 <b>العملاء</b> ({len(rows_db)})\nالصفحة {page + 1}/{pages}\n\n🟢 فعّال  🔴 منتهي/محظور  👑 مشرف", kb(rows)


def tn_view(tid):
    r = t_row(tid)
    if not r:
        return "❌ العميل غير موجود.", kb([[btn("back", "tl:0")]])
    name = r[0]
    label, left = status_info(tid)
    text = (f"👤 <b>{esc(name) or tid}</b>\n🆔 <code>{tid}</code>\n🪪 {label}" + (f"\n⏳ المتبقي: {left}" if left else "")
            + f"\n👥 عملاؤه: <b>{my_customers(tid)}</b>\n🔗 مربوط الآن: <b>{'نعم' if is_connected(tid) else 'لا'}</b>")
    rows = [[raw("➕ 1 يوم", f"tx:{tid}:1", "primary"), raw("➕ 7 أيام", f"tx:{tid}:7", "primary"),
             raw("➕ 30 يوم", f"tx:{tid}:30", "primary")],
            [raw("♾ بلا حدود", f"tu:{tid}", "success"), raw("🚫 حظر / فك الحظر", f"tb:{tid}", "danger")],
            [btn("back", "tl:0")]]
    return text, kb(rows)


@router.callback_query(F.data.startswith("tl:"), ADMIN)
async def cb_tl(c: CallbackQuery):
    text, markup = tenants_list_view(int(c.data[3:]))
    await show(c, text, markup)
    await c.answer()


@router.callback_query(F.data.startswith("tn:"), ADMIN)
async def cb_tn(c: CallbackQuery):
    text, markup = tn_view(int(c.data[3:]))
    await show(c, text, markup)
    await c.answer()


async def _notify_tenant(bot, tid, text):
    try:
        await send_text(bot, tid, text)
    except Exception:
        pass


@router.callback_query(F.data.startswith("tx:"), ADMIN)
async def cb_tx(c: CallbackQuery, bot: Bot):
    _, tid, days = c.data.split(":")
    tid, days = int(tid), int(days)
    r = t_row(tid)
    if r:
        base = max(time.time(), r[2]) if r[2] else time.time()
        ex("UPDATE tenants SET expires=?, plan='paid', blocked=0 WHERE tid=?", int(base + days * 86400), tid)
        await _notify_tenant(bot, tid, f"✅ تم تمديد اشتراكك <b>{days}</b> يوم. شكراً لك 💎")
    text, markup = tn_view(tid)
    await show(c, text, markup)
    await c.answer("✅ تم التمديد")


@router.callback_query(F.data.startswith("tu:"), ADMIN)
async def cb_tu(c: CallbackQuery, bot: Bot):
    tid = int(c.data[3:])
    ex("UPDATE tenants SET expires=0, plan='unlimited', blocked=0 WHERE tid=?", tid)
    await _notify_tenant(bot, tid, "✅ تم تفعيل حسابك بدون مدة محدودة 💎")
    text, markup = tn_view(tid)
    await show(c, text, markup)
    await c.answer("♾ تم")


@router.callback_query(F.data.startswith("tb:"), ADMIN)
async def cb_tb(c: CallbackQuery):
    tid = int(c.data[3:])
    ex("UPDATE tenants SET blocked = 1 - blocked WHERE tid=?", tid)
    text, markup = tn_view(tid)
    await show(c, text, markup)
    await c.answer("🚫 تم")


# ---- تخصيص الأزرار (المالك فقط) ----
PAGE = 10


def keys_of(group):
    return [k for k, v in BTN.items() if v[3] == group]


def cz_view(group, page):
    keys = keys_of(group)
    pages = max(1, (len(keys) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    items = []
    for k in keys[page * PAGE:(page + 1) * PAGE]:
        cfg = _cfg.get(k)
        label = (cfg or {}).get("label") or BTN[k][0]
        items.append(btn("cz_item", f"cd:{k}", text=f"{'✅' if cfg else '▫️'} {cut(label, 22)}"))
    rows = pairs(items)
    nav = []
    if page > 0:
        nav.append(btn("prev", f"cz:{group}:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("next", f"cz:{group}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("back", "m:custom")])
    text = (f"🎨 <b>{GROUPS.get(group, group)}</b>\nالصفحة {page + 1}/{pages}\n\n"
            "✅ = مخصص  |  ▫️ = افتراضي\nاختر الزر الذي تريد تخصيصه 👇")
    return text, kb(rows)


def detail_view(key):
    label, emoji, dstyle, group = BTN[key]
    cfg = _cfg.get(key) or {}
    eid = cfg.get("emoji_id")
    st = cfg.get("style") or dstyle or "none"
    text = (f"🎨 <b>تخصيص الزر</b>\n\n🔹 الاسم: <b>{esc(cfg.get('label') or label)}</b>\n"
            f"✨ الإيموجي: {('مميز ✅ <code>' + esc(eid) + '</code>') if eid else 'افتراضي ' + (emoji or '—')}\n"
            f"🎨 اللون: <b>{STYLE_NAMES.get(st, st)}</b>")
    rows = [
        [btn("c_emoji", f"ce:{key}"), btn("c_label", f"cl:{key}")],
        [raw("🔵 أزرق", f"cc:{key}:primary", "primary"), raw("🟢 أخضر", f"cc:{key}:success", "success"),
         raw("🔴 أحمر", f"cc:{key}:danger", "danger"), raw("⚪ بدون", f"cc:{key}:none")],
        [btn("c_noemoji", f"cx:{key}"), btn("c_reset", f"cr:{key}")],
        [btn("back", f"cz:{group}:0")],
    ]
    return text, kb(rows)


@router.callback_query(F.data.startswith("cz:"), ADMIN)
async def cb_cz(c: CallbackQuery):
    _, group, page = c.data.split(":")
    text, markup = cz_view(group, int(page))
    await show(c, text, markup)
    await c.answer()


@router.callback_query(F.data.startswith("cd:"), ADMIN)
async def cb_cd(c: CallbackQuery, state: FSMContext):
    await state.clear()
    key = c.data[3:]
    if key not in BTN:
        return await c.answer()
    text, markup = detail_view(key)
    await show(c, text, markup)
    await c.answer()


@router.callback_query(F.data.startswith("ce:"), ADMIN)
async def cb_ce(c: CallbackQuery, state: FSMContext):
    key = c.data[3:]
    prompt = ("✨ <b>إيموجي مميز للزر</b>\n\nأرسل <b>إيموجي مميز (Premium)</b> واحداً.\n"
              "• إن لم تكن مشتركاً، أرسل <b>ملصق إيموجي</b> من أي حزمة مميزة، أو أرسل <b>رقم الإيموجي (ID)</b>.\n"
              "• سأُجرّب الزر أمامك قبل الحفظ.")
    await ask(c, state, "icon", prompt, f"cd:{key}", key=key, pre="")
    await c.answer()


@router.callback_query(F.data.startswith("cl:"), ADMIN)
async def cb_cl(c: CallbackQuery, state: FSMContext):
    key = c.data[3:]
    await ask(c, state, "label", "✏️ أرسل الاسم الجديد للزر (حتى 40 حرفاً):", f"cd:{key}", key=key, pre="")
    await c.answer()


@router.callback_query(F.data.startswith("cc:"), ADMIN)
async def cb_cc(c: CallbackQuery):
    _, key, style = c.data.split(":")
    if key in BTN:
        cfg_set(key, style=None if style == "none" and BTN[key][2] is None else style)
    text, markup = detail_view(key)
    await show(c, text, markup)
    await c.answer("🎨 تم")


@router.callback_query(F.data.startswith("cx:"), ADMIN)
async def cb_cx(c: CallbackQuery):
    key = c.data[3:]
    if key in BTN:
        cfg_set(key, emoji_id=None)
    text, markup = detail_view(key)
    await show(c, text, markup)
    await c.answer("❌ تم حذف الإيموجي")


@router.callback_query(F.data.startswith("cr:"), ADMIN)
async def cb_cr(c: CallbackQuery):
    key = c.data[3:]
    _cfg.pop(key, None)
    ex("DELETE FROM btn_cfg WHERE key=?", key)
    text, markup = detail_view(key)
    await show(c, text, markup)
    await c.answer("♻️ تمت إعادة الضبط")


@router.callback_query(F.data == "cr_all", ADMIN)
async def cb_cr_all(c: CallbackQuery):
    ex("DELETE FROM btn_cfg")
    _cfg.clear()
    text, markup = custom_home()
    await show(c, text, markup)
    await c.answer("♻️ تمت إعادة ضبط كل الأزرار")


# ════════════════════════ استقبال المدخلات ════════════════════════
async def finish(m, state, text, back="p:home"):
    await state.clear()
    home = "p:admin" if back.startswith("m:") and back[2:] in ADMIN_MENUS else "p:home"
    rows = [[btn("back", back), btn("to_panel", home)]] if back not in ("p:home", "p:admin") else [[btn("to_panel", home)]]
    await with_fallback(m.answer, kb(rows), text=text)


async def do_broadcast(bot, admin_chat, text, targets):
    ok = bad = 0
    for chat, conn in targets:
        try:
            await send_text(bot, chat, text, conn=conn)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await send_text(bot, chat, text, conn=conn)
                ok += 1
            except Exception:
                bad += 1
        except Exception:
            bad += 1
        await asyncio.sleep(0.05)
    await bot.send_message(admin_chat, f"📣 <b>انتهت الإذاعة</b>\n✅ نجح: {ok}\n❌ فشل: {bad}")


def spawn(coro):
    task = asyncio.create_task(coro)
    BG.add(task)
    task.add_done_callback(BG.discard)


async def send_biz_welcome(bot, chat_id, conn, pre, name):
    text = render(T("biz_welcome", pre), name)
    photo = get(pre + "welcome_photo", "")
    if photo and photo != "-":
        try:
            return await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, business_connection_id=conn)
        except TelegramBadRequest as e:
            log.warning("welcome photo: %s", e)
    return await send_text(bot, chat_id, text, conn=conn)


TEXT_ONLY = {"addrep", "addban", "mute", "bc", "gbc", "force_set", "set_botch", "set_devch", "set_devurl", "social_add",
             "tz", "clock_fmt", "bio_set", "label", "text", "media_kw", "admin_add", "owner_set", "trial_set"}


@router.message(StateFilter(Form.wait), F.chat.type == "private")
async def got_input(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    a, back, pre = d.get("action"), d.get("back", "p:home"), d.get("pre", "")
    uid = m.from_user.id
    t = (m.text or "").strip()
    ht = m.html_text if m.text else ""

    # حماية: الإجراءات العامة للمشرفين فقط، وبقية الإجراءات لمن يملك لوحة
    if (a in GLOBAL_ACTS or pre == "") and not is_admin(uid):
        await state.clear()
        return
    if pre and not can_tenant(uid):
        await state.clear()
        return
    if a in TEXT_ONLY or (a or "").startswith("trg_"):
        if not m.text:
            return await m.answer("⚠️ أرسل نصاً فقط.")

    if a == "text":
        key = d["key"]
        if t.lower() == "/reset":
            kv_del(pre + "txt_" + key)
            return await finish(m, state, "↩️ تمت استعادة النص الافتراضي.", back)
        put(pre + "txt_" + key, ht)
        return await finish(m, state, "✅ تم حفظ النص.", back)

    if a == "label":
        cfg_set(d["key"], label=t[:40])
        await state.clear()
        text, markup = detail_view(d["key"])
        return await m.answer(text, reply_markup=markup)

    if a == "icon":
        key = d["key"]
        eid = extract_emoji_id(m)
        if not eid:
            return await m.answer("⚠️ لم أجد إيموجي مميزاً. أرسل إيموجي Premium، أو ملصق إيموجي، أو رقم الإيموجي.")
        try:
            await bot.send_message(m.chat.id, "👁 معاينة الزر بعد التخصيص:",
                                   reply_markup=kb([[btn(key, "noop", eid=eid)]]))
        except TelegramBadRequest as e:
            return await m.answer(
                "❌ رفض تيليجرام هذا الإيموجي على الزر.\n"
                "تأكد أن <b>مالك البوت مشترك في Telegram Premium</b> وأن الإيموجي صحيح.\n"
                f"<code>{esc(cut(str(e), 150))}</code>")
        cfg_set(key, emoji_id=eid)
        await state.clear()
        text, markup = detail_view(key)
        return await m.answer("✅ تم حفظ الإيموجي المميز.\n\n" + text, reply_markup=markup)

    if a == "media_add":
        ctype, fid = media_of(m)
        if not ctype:
            return await m.answer("⚠️ أرسل ميديا (صورة/فيديو/ملف/صوت/ملصق).")
        await state.update_data(action="media_kw", ctype=ctype, fid=fid,
                                cap=m.html_text if (m.text or m.caption) else "")
        return await m.answer("🔑 الآن أرسل <b>الكلمة المفتاحية</b> التي تُرسل عندها هذه الميديا:")

    if a == "media_kw":
        ex("INSERT OR REPLACE INTO t_media(tid, kw, ctype, fid, cap) VALUES(?,?,?,?,?)",
           uid, t.lower(), d["ctype"], d["fid"], d.get("cap", ""))
        return await finish(m, state, f"✅ تمت إضافة الميديا للكلمة: <b>{esc(t)}</b>", back)

    if a == "photo":
        if m.photo:
            put(pre + "welcome_photo", m.photo[-1].file_id)
        elif m.text and t.startswith("http"):
            put(pre + "welcome_photo", t)
        else:
            return await m.answer("⚠️ أرسل صورة أو رابط صورة مباشر.")
        await state.clear()
        await m.answer("✅ تم الحفظ. هذه معاينة الترحيب:")
        if pre:
            return await send_biz_welcome(bot, m.chat.id, None, pre, m.from_user.full_name)
        return await send_home(bot, m.chat.id, m.from_user)

    if a == "restore":
        if not is_owner(uid):
            return
        if not m.document:
            return await m.answer("⚠️ أرسل ملف النسخة (.db).")
        buf = io.BytesIO()
        await bot.download(m.document, destination=buf)
        data = buf.getvalue()
        if data[:16] != b"SQLite format 3\x00":
            return await m.answer("❌ هذا الملف ليس قاعدة بيانات صالحة.")
        tmp = os.path.join(tempfile.gettempdir(), f"restore_{int(time.time())}.db")
        with open(tmp, "wb") as f:
            f.write(data)
        try:
            chk = sqlite3.connect(tmp)
            tables = {r[0] for r in chk.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            chk.close()
            if "kv" not in tables:
                raise ValueError("no kv")
        except Exception:
            os.remove(tmp)
            return await m.answer("❌ الملف لا يخص هذا البوت.")
        db.close()
        for ext in ("-wal", "-shm"):
            if os.path.exists(DB_PATH + ext):
                os.remove(DB_PATH + ext)
        os.replace(tmp, DB_PATH)
        open_db()
        return await finish(m, state, "✅ تم استرجاع النسخة بنجاح.", back)

    if a == "bc":
        await state.clear()
        targets = [(chat, conn) for chat, conn in q(
            "SELECT c.chat, c.conn FROM customers c JOIN bconns b ON b.id=c.conn WHERE b.owner=?", uid)]
        spawn(do_broadcast(bot, m.chat.id, ht, targets))
        return await m.answer(f"⏳ بدأت الإذاعة لـ {len(targets)} عميل، سأخبرك عند الانتهاء.")

    if a == "gbc":
        await state.clear()
        targets = {(u, None) for (u,) in q("SELECT uid FROM users")} | {(ch, None) for (ch,) in q("SELECT chat FROM bconns")}
        spawn(do_broadcast(bot, m.chat.id, ht, list(targets)))
        return await m.answer(f"⏳ بدأت الإذاعة العامة لـ {len(targets)} مستلم، سأخبرك عند الانتهاء.")

    r = "✅ تم الحفظ."
    if a == "addrep":
        if "|" not in t:
            return await m.answer("⚠️ الصيغة: <code>الكلمة | الرد</code>")
        kw = t.split("|", 1)[0].strip().lower()
        resp = ht.split("|", 1)[1].strip() if "|" in ht else ""
        if not kw or not resp:
            return await m.answer("⚠️ الكلمة أو الرد فارغ.")
        ex("INSERT OR REPLACE INTO t_replies(tid, kw, resp) VALUES(?,?,?)", uid, kw, resp)
        r = f"✅ تمت إضافة الرد للكلمة: <b>{esc(kw)}</b>"
    elif a == "addban":
        ex("INSERT OR IGNORE INTO t_banned(tid, w) VALUES(?,?)", uid, t.lower())
        r = "✅ تمت إضافة الكلمة الممنوعة."
    elif a == "mute":
        if not t.isdigit():
            return await m.answer("⚠️ أرسل آيدي رقمياً.")
        ex("INSERT OR IGNORE INTO t_muted(tid, uid) VALUES(?,?)", uid, int(t))
        r = "🔇 تم الكتم."
    elif a == "force_set":
        ch = channel_ref(t)
        if not ch:
            return await m.answer("⚠️ أرسل يوزر صحيحاً مثل <code>@channel</code>")
        put(pre + "force_channel", ch)
        try:
            await bot.get_chat(ch)
            r = f"✅ تم تعيين القناة {esc(ch)}\nتأكد أن البوت مشرف فيها ليعمل التحقق."
        except Exception:
            r = f"⚠️ حُفظت القناة {esc(ch)} لكن لم أتمكن من الوصول إليها. أضف البوت مشرفاً فيها."
    elif a in ("set_botch", "set_devch", "set_devurl"):
        if not norm_url(t):
            return await m.answer("⚠️ رابط غير صالح.")
        put({"set_botch": "bot_channel", "set_devch": "dev_channel", "set_devurl": "dev_url"}[a], t)
        r = "✅ تم حفظ الرابط."
    elif a == "social_add":
        if "|" not in t:
            return await m.answer("⚠️ الصيغة: <code>الاسم | الرابط</code>")
        n, u = [x.strip() for x in t.split("|", 1)]
        if not n or not norm_url(u):
            return await m.answer("⚠️ الاسم أو الرابط غير صالح.")
        ex("INSERT INTO t_socials(tid, name, url) VALUES(?,?,?)", uid, n, norm_url(u))
        r = "✅ تمت إضافة الرابط."
    elif a and a.startswith("trg_"):
        words = ",".join(x.strip().lower() for x in re.split(r"[,،\n]", t) if x.strip())
        if not words:
            return await m.answer("⚠️ أرسل كلمة واحدة على الأقل.")
        put(pre + a, words)
        r = "✅ تم حفظ كلمات التفعيل."
    elif a == "tz":
        try:
            float(t)
        except ValueError:
            return await m.answer("⚠️ أرسل رقماً مثل 3 أو -4 أو 5.5")
        put(pre + "tz", t)
        r = "✅ تم حفظ فرق التوقيت."
    elif a == "clock_fmt":
        put(pre + "clock_fmt", t)
        r = "✅ تم حفظ صيغة الساعة."
    elif a == "bio_set":
        put(pre + "bio_text", t[:70])
        r = "✅ تم حفظ نص البايو."
    elif a == "trial_set":
        if not t.isdigit():
            return await m.answer("⚠️ أرسل رقماً (0 = بلا حدود).")
        put("trial_days", t)
        r = f"✅ مدة التجربة للعملاء الجدد: {t} يوم."
    elif a == "admin_add":
        if not is_owner(uid):
            return
        if not t.isdigit():
            return await m.answer("⚠️ أرسل آيدي رقمياً.")
        ex("INSERT OR IGNORE INTO admins(uid) VALUES(?)", int(t))
        _admins.add(int(t))
        r = f"✅ تمت إضافة المشرف <code>{int(t)}</code>"
        try:
            await bot.send_message(int(t), "🛡 تمت إضافتك مشرفاً في البوت. أرسل /admin لفتح لوحة المالك.")
        except Exception:
            r += "\n(لم أستطع إشعاره، لأنه لم يبدأ البوت بعد)"
    elif a == "owner_set":
        if not is_owner(uid):
            return
        if not t.isdigit():
            return await m.answer("⚠️ أرسل آيدي رقمياً.")
        put("owner_id", t)
        r = f"✅ المالك الجديد: <code>{t}</code>"
        try:
            await bot.send_message(int(t), "👑 أصبحت مالك البوت. أرسل /admin لفتح لوحة المالك.")
        except Exception:
            r += "\n(لم أستطع إشعاره، يجب أن يبدأ البوت أولاً)"
    else:
        r = "؟"
    await finish(m, state, r, back)


# ════════════════════════ Telegram Business ════════════════════════
@router.business_connection()
async def on_conn(c: BusinessConnection, bot: Bot):
    chat = getattr(c, "user_chat_id", None) or c.user.id
    uid = c.user.id
    if c.is_enabled:
        is_new = t_row(uid) is None
        ensure_tenant(uid, c.user.full_name)
        ex("INSERT OR REPLACE INTO bconns(id, owner, chat, fn, ln) VALUES(?,?,?,?,?)",
           c.id, uid, chat, c.user.first_name, c.user.last_name or "")
        try:
            await send_text(bot, chat, T("connected"))
            await with_fallback(bot.send_message, tenant_kb(), chat_id=chat, text=tenant_header(uid))
        except Exception as e:
            log.warning("connected msg: %s", e)
        if is_new:
            for adm in set(ADMIN_IDS) | set(_admins) | {cur_owner()}:
                try:
                    await send_text(bot, adm, f"🔔 <b>عميل جديد ربط حسابه</b>\n👤 {esc(c.user.full_name)}\n🆔 <code>{uid}</code>")
                except Exception:
                    pass
    else:
        ex("DELETE FROM bconns WHERE id=?", c.id)
        try:
            await send_text(bot, chat, T("disconnected"))
        except Exception:
            pass


def conn_owner(cid):
    r = q("SELECT owner FROM bconns WHERE id=?", cid)
    return r[0][0] if r else None


def violation(m, low, pre, tid):
    kinds = {e.type for e in ((m.entities or []) + (m.caption_entities or []))}
    if on("blk_link", pre) and kinds & {"url", "text_link"}:
        return "رابط"
    if on("blk_mention", pre) and kinds & {"mention", "text_mention"}:
        return "منشن"
    if on("blk_forward", pre) and m.forward_origin:
        return "رسالة محوّلة"
    if on("blk_sticker", pre) and m.sticker:
        return "ملصق"
    if on("blk_voice", pre) and (m.voice or m.video_note):
        return "رسالة صوتية"
    if on("blk_media", pre) and (m.photo or m.video or m.animation):
        return "صورة/فيديو"
    if on("blk_doc", pre) and m.document and not m.animation:
        return "ملف"
    if on("blk_contact", pre) and (m.contact or m.location):
        return "جهة اتصال/موقع"
    for (w,) in q("SELECT w FROM t_banned WHERE tid=?", tid):
        if w and w in low:
            return f"كلمة ممنوعة ({w})"
    return None


def cache_put(m, text):
    ex("INSERT OR REPLACE INTO cache(chat, mid, uid, name, uname, text, ts) VALUES(?,?,?,?,?,?,?)",
       m.chat.id, m.message_id, m.from_user.id, m.from_user.full_name, m.from_user.username or "",
       text or f"[{m.content_type}]", int(time.time()))


def triggers(pre, k):
    return [x.strip().lower() for x in get(pre + "trg_" + k, TRIG_DEFAULT[k]).split(",") if x.strip()]


async def handle_triggers(bot, m, pre, tid, low, conn):
    for k in ("rates", "pay", "codes"):
        if low in triggers(pre, k):
            txt = get(pre + "txt_" + k)
            if txt:
                await send_text(bot, m.chat.id, txt, conn=conn)
                return True
    if low in triggers(pre, "social"):
        socials = [(n, norm_url(u)) for n, u in q("SELECT name, url FROM t_socials WHERE tid=? ORDER BY id", tid) if norm_url(u)]
        if socials:
            title = T("social", pre)
            try:
                await send_text(bot, m.chat.id, title, conn=conn,
                                markup=kb(pairs([btn("social_item", url=u, text=n) for n, u in socials])))
            except Exception:
                await send_text(bot, m.chat.id, title + "\n" + "\n".join(f"• {esc(n)}: {u}" for n, u in socials), conn=conn)
            return True
    if on("calc_on", pre) and (low.startswith("=") or low.startswith("احسب")):
        expr = low[1:] if low.startswith("=") else low[4:]
        try:
            await send_text(bot, m.chat.id, f"🧮 <code>{esc(expr.strip())}</code>\n✅ <b>{safe_calc(expr)}</b>", conn=conn)
        except Exception:
            pass
        return True
    return False


@router.business_message()
async def on_biz(m: Message, bot: Bot):
    owner = conn_owner(m.business_connection_id)
    if owner is None or not m.from_user:
        return
    if not active(owner):
        return
    pre, uid, conn = pfx(owner), m.from_user.id, m.business_connection_id
    text = m.text or m.caption or ""
    cache_put(m, text)
    if uid == owner or not on("bot_on") or not on("bot_on", pre):
        return
    low = text.lower().strip()

    muted = bool(q("SELECT 1 FROM t_muted WHERE tid=? AND uid=?", owner, uid))
    reason = "مكتوم" if muted else violation(m, low, pre, owner)
    if reason:
        try:
            await bot.delete_business_messages(business_connection_id=conn, message_ids=[m.message_id])
        except Exception as e:
            log.warning("delete msg: %s", e)
        if not muted and on("notify_block", pre):
            try:
                await send_text(bot, owner, f"🚫 <b>حُذفت رسالة مخالفة</b>\n<blockquote>👤 {esc(m.from_user.full_name)}\n"
                                            f"🆔 <code>{uid}</code>\n📌 السبب: {esc(reason)}</blockquote>")
            except Exception:
                pass
        return

    tch = get(pre + "force_channel")
    if tch and not await member_of(bot, tch, uid):
        try:
            await bot.delete_business_messages(business_connection_id=conn, message_ids=[m.message_id])
        except Exception as e:
            log.warning("force delete: %s", e)
        if time.time() - _prompted.get((conn, uid), 0) > 600:
            _prompted[(conn, uid)] = time.time()
            try:
                await send_text(bot, m.chat.id, T("force_t", pre).replace("{channel}", esc(tch)), conn=conn,
                                markup=kb([[btn("subscribe", url=f"https://t.me/{tch.lstrip('@')}")]]))
            except Exception as e:
                log.warning("force prompt: %s", e)
        return

    if ex("INSERT OR IGNORE INTO customers(conn, uid, chat, name, ts) VALUES(?,?,?,?,?)",
          conn, uid, m.chat.id, m.from_user.full_name, int(time.time())) and on("welcome_on", pre):
        try:
            await send_biz_welcome(bot, m.chat.id, conn, pre, m.from_user.full_name)
        except Exception as e:
            log.warning("welcome: %s", e)

    if not text:
        return
    try:
        if await handle_triggers(bot, m, pre, owner, low, conn):
            return
    except Exception as e:
        log.warning("triggers: %s", e)

    if on("auto_on", pre):
        for kw, resp in q("SELECT kw, resp FROM t_replies WHERE tid=?", owner):
            if kw in low:
                try:
                    await send_text(bot, m.chat.id, resp, conn=conn)
                except Exception as e:
                    log.warning("auto reply: %s", e)
                return
        for kw, ctype, fid, cap in q("SELECT kw, ctype, fid, cap FROM t_media WHERE tid=?", owner):
            if kw in low:
                try:
                    await send_media(bot, m.chat.id, ctype, fid, cap, conn)
                except Exception as e:
                    log.warning("auto media: %s", e)
                return


@router.edited_business_message()
async def on_edit(m: Message, bot: Bot):
    owner = conn_owner(m.business_connection_id)
    if owner is None or not m.from_user or m.from_user.id == owner or not active(owner):
        return
    if not on("log_edit", pfx(owner)):
        return
    new = m.text or m.caption or f"[{m.content_type}]"
    old = q("SELECT text FROM cache WHERE chat=? AND mid=?", m.chat.id, m.message_id)
    old = old[0][0] if old else "(غير معروف)"
    cache_put(m, new)
    if old == new:
        return
    u = m.from_user
    await send_text(bot, owner,
                    f"✏️ <b>تم تعديل رسالة</b>\n\n<blockquote>👤 الاسم: {esc(u.full_name)}\n"
                    f"🔗 اليوزر: {('@' + esc(u.username)) if u.username else '—'}\n🆔 آيدي: <code>{u.id}</code>\n\n"
                    f"💬 قبل التعديل:\n{esc(cut(old, 1200))}\n\n✅ بعد التعديل:\n{esc(cut(new, 1200))}</blockquote>")


@router.deleted_business_messages()
async def on_del(d: BusinessMessagesDeleted, bot: Bot):
    owner = conn_owner(d.business_connection_id)
    if owner is None or not active(owner) or not on("log_del", pfx(owner)):
        return
    for mid in d.message_ids:
        r = q("SELECT uid, name, uname, text FROM cache WHERE chat=? AND mid=?", d.chat.id, mid)
        if not r or r[0][0] == owner:
            continue
        uid, name, uname, text = r[0]
        await send_text(bot, owner,
                        f"🗑 <b>تم حذف رسالة</b>\n\n<blockquote>👤 الاسم: {esc(name)}\n"
                        f"🔗 اليوزر: {('@' + esc(uname)) if uname else '—'}\n🆔 آيدي: <code>{uid}</code>\n\n"
                        f"💬 نص الرسالة:\n{esc(cut(text, 1500))}</blockquote>")


# ════════════════════════ ردّ احتياطي ════════════════════════
@router.callback_query()
async def cb_fallback(c: CallbackQuery):
    if c.data and c.data.split(":", 1)[0] in {"p", "m", "t", "a", "e", "v", "x", "cz", "cd", "ce", "cl", "cc", "cx",
                                              "cr", "cr_all", "cancel", "tl", "tn", "tx", "tu", "tb"}:
        return await c.answer("🚫 غير مصرّح لك.", show_alert=True)
    await c.answer()


# ════════════════════════ الساعة الحية والبايو الحي ════════════════════════
async def live_loop(bot: Bot):
    last_min, last_prune = 0, 0
    while True:
        await asyncio.sleep(20)
        try:
            if time.time() - last_prune > 3600:
                last_prune = time.time()
                ex("DELETE FROM cache WHERE ts < ?", int(time.time()) - 7 * 86400)
            minute = int(time.time() // 60)
            if minute == last_min:
                continue
            last_min = minute
            if not on("bot_on"):
                continue
            for cid, owner, fn, ln in q("SELECT id, owner, fn, ln FROM bconns"):
                if not active(owner):
                    continue
                pre = pfx(owner)
                t = now_str(pre)
                if on("clock_on", pre):
                    fmt = get(pre + "clock_fmt", "🕐 {time}").replace("{time}", t)
                    try:
                        await bot.set_business_account_name(business_connection_id=cid, first_name=fn,
                                                            last_name=f"{ln} {fmt}".strip()[:64])
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after)
                    except Exception as e:
                        log.debug("clock: %s", e)
                if on("bio_on", pre):
                    try:
                        await bot.set_business_account_bio(business_connection_id=cid,
                                                           bio=get(pre + "bio_text", "🕐 {time}").replace("{time}", t)[:70])
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after)
                    except Exception as e:
                        log.debug("bio: %s", e)
        except Exception as e:
            log.warning("live loop: %s", e)


# ════════════════════════ التشغيل ════════════════════════
async def start_web():
    app = web.Application()

    async def ok(_):
        return web.Response(text="OK")

    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("🌐 خادم الصحة يعمل على المنفذ %s", PORT)


async def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ ضع BOT_TOKEN في المتغيرات البيئية.")
    if not ADMIN_IDS:
        raise SystemExit("❌ ضع OWNER_ID (آيدي المالك) في المتغيرات البيئية.")
    open_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = Dispatcher()
    dp.include_router(router)
    await start_web()
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    if not getattr(me, "can_connect_to_business", False):
        log.warning("⚠️ وضع Business غير مفعّل! BotFather ← Bot Settings ← Secretary Mode")
    log.info("✅ البوت @%s يعمل الآن...", me.username)
    task = asyncio.create_task(live_loop(bot))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
