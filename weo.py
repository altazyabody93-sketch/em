# -*- coding: utf-8 -*-
"""
💎 Business Bot — مدير حسابات تيليجرام (Telegram Business)
aiogram >= 3.25  |  Python 3.10+

المتغيرات البيئية:
  BOT_TOKEN        (إجباري)  توكن البوت
  OWNER_ID         (إجباري)  آيدي المالك
  ADMIN_IDS        (اختياري) آيديات مشرفين إضافيين مفصولة بفاصلة
  BOT_CHANNEL / DEV_CHANNEL / DEV_URL / DEV_BUTTON_TEXT   (اختياري) روابط الواجهة
  WELCOME_PHOTO_URL (اختياري) رابط صورة ترحيب افتراضية (يمكنك رفع صورة من اللوحة)
  DB_PATH          (اختياري) مسار قاعدة البيانات، مثال: /data/business_bot.db
  PORT             (اختياري) منفذ خادم الصحة (Render/Koyeb)
  REQUIRE_CONNECTION (اختياري) "0" لفتح اللوحة بدون ربط Business
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

# تفعيل البروكسي (اختياري) لتجاوز حجب تيليجرام في بعض السيرفرات
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
from aiogram.filters import Command, CommandStart, StateFilter
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

DEFAULT_ON = {"bot_on", "auto_on", "welcome_on", "calc_on", "log_edit", "log_del", "notify_block", "icons_on", "require_conn"}

router = Router()
_admins = set()  # مشرفون مضافون من اللوحة (محفوظون في قاعدة البيانات)


def cur_owner():
    v = _kv.get("owner_id", "")
    return int(v) if v.isdigit() else OWNER_ID


def is_admin(uid):
    return uid in ADMIN_IDS or uid in _admins or uid == cur_owner()


def is_owner(uid):
    """المالك الحالي (قابل للتغيير من اللوحة) فقط يدير المشرفين والإعدادات العامة."""
    return uid == cur_owner() or uid in EXTRA_ADMINS


def need_conn():
    return REQUIRE_CONNECTION and _kv.get("require_conn", "1") == "1"


ADMIN = F.from_user.id.func(is_admin)
BG = set()  # مراجع للمهام الخلفية


class Form(StatesGroup):
    wait = State()


class U(StatesGroup):
    calc = State()


# ════════════════════════ قاعدة البيانات ════════════════════════
SCHEMA = """
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS replies(id INTEGER PRIMARY KEY AUTOINCREMENT, kw TEXT UNIQUE, resp TEXT);
CREATE TABLE IF NOT EXISTS banned(id INTEGER PRIMARY KEY AUTOINCREMENT, w TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS muted(uid INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS users(uid INTEGER PRIMARY KEY, name TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS bconns(id TEXT PRIMARY KEY, owner INTEGER, chat INTEGER, fn TEXT, ln TEXT);
CREATE TABLE IF NOT EXISTS customers(conn TEXT, uid INTEGER, chat INTEGER, name TEXT, ts INTEGER, PRIMARY KEY(conn, uid));
CREATE TABLE IF NOT EXISTS cache(chat INTEGER, mid INTEGER, uid INTEGER, name TEXT, uname TEXT, text TEXT, ts INTEGER, PRIMARY KEY(chat, mid));
CREATE TABLE IF NOT EXISTS btn_cfg(key TEXT PRIMARY KEY, emoji_id TEXT, label TEXT, style TEXT);
CREATE TABLE IF NOT EXISTS socials(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS admins(uid INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS media(id INTEGER PRIMARY KEY AUTOINCREMENT, kw TEXT UNIQUE, ctype TEXT, fid TEXT, cap TEXT);
"""

db = None
_kv = {}
_cfg = {}


def open_db():
    global db
    folder = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(folder, exist_ok=True)
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    db.commit()
    load_caches()


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


def on(k):
    return get(k, "1" if k in DEFAULT_ON else "0") == "1"


def esc(s):
    return html.escape(str(s or ""), quote=False)


def cut(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


# ════════════════════════ النصوص القابلة للتعديل ════════════════════════
EMPTY = "<i>لم يتم ضبط هذا القسم بعد.</i>"
TEXTS = {
    "welcome": ("رسالة ترحيب البوت", "👋 أهلاً بك يا <b>{name}</b> في نظام الإدارة المتقدم."),
    "intro": ("جملة التعريف (الاقتباس)", "أنا مساعدك الشخصي لتأمين وحماية حسابك وإدارة ردودك باحترافية تامة."),
    "biz_welcome": ("ترحيب العملاء (Business)", "👋 أهلاً بك <b>{name}</b>!\nكيف يمكنني مساعدتك اليوم؟"),
    "force": ("رسالة الاشتراك الإجباري", "⚠️ لازم تشترك بالقناة أولاً لاستخدام البوت."),
    "panel": ("عنوان لوحة التحكم", "👑 <b>لوحة التحكم</b>"),
    "rates": ("قسم أسعار العملات", "💱 <b>أسعار العملات</b>\n\n" + EMPTY),
    "pay": ("قسم طرق الدفع", "👛 <b>طرق الدفع</b>\n\n" + EMPTY),
    "codes": ("قسم أكواد التحويل", "🏷 <b>أكواد التحويل</b>\n\n" + EMPTY),
    "social": ("عنوان السوشيال ميديا", "🌐 <b>حساباتنا على السوشيال ميديا</b>"),
    "calc": ("رسالة الآلة الحاسبة", "🧮 <b>الآلة الحاسبة</b>\n\nأرسل أي عملية حسابية وسأحسبها لك.\nمثال: <code>25*4+10</code>"),
    "no_connect": ("تنبيه عدم الربط", "⚠️ اربط البوت بحسابك أولاً من: الإعدادات ← Business ← المحادثة الآلية."),
    "connected": ("رسالة نجاح الربط", "✅ تم ربط حسابك بالبوت بنجاح.\nيمكنك الآن فتح لوحة التحكم من /start"),
    "on_label": ("كلمة «مفعّل» على الأزرار", "مفعّل"),
    "off_label": ("كلمة «معطّل» على الأزرار", "معطّل"),
    "panel_hint": ("سطر التوجيه أسفل اللوحة", "<i>اختر القسم الذي تريد إدارته 👇</i>"),
    "sub_first": ("تنبيه: اشترك أولاً", "اشترك بالقناة أولاً 📢"),
    "sub_not_yet": ("تنبيه: لم تشترك بعد", "لسا ما اشتركت بالقناة! 📢"),
    "no_socials": ("تنبيه: لا توجد حسابات", "لم تُضف حسابات بعد."),
    "calc_bad": ("خطأ الحاسبة (عملية غير صحيحة)", "⚠️ عملية غير صحيحة، مثال: <code>25*4+10</code>"),
    "calc_div0": ("خطأ الحاسبة (قسمة على صفر)", "⚠️ لا يمكن القسمة على صفر."),
    "disconnected": ("رسالة فصل الربط", "❌ تم فصل البوت عن حسابك."),
}


def T(key):
    return _kv.get("txt_" + key) or TEXTS[key][1]


def plain(key):
    return html.unescape(re.sub(r"<[^>]+>", "", T(key))).strip()


def render(text, name=""):
    return text.replace("{name}", esc(name))


# ════════════════════════ سجل الأزرار + دالة btn() ════════════════════════
BTN = {}  # key -> (label, emoji, default_style, group)


def reg(key, label, emoji="", style=None, group="general"):
    BTN[key] = (label, emoji, style, group)


# أزرار الواجهة
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
# أزرار عامة
reg("back", "رجوع", "🔙", "danger", "general")
reg("close", "إغلاق", "✖️", "danger", "general")
reg("cancel", "إلغاء", "✖️", "danger", "general")
reg("del", "حذف", "🗑", "danger", "general")
reg("to_panel", "لوحة التحكم", "🎛", "primary", "general")
reg("check_sub", "تحققت", "✅", "success", "general")
reg("subscribe", "اشترك بالقناة", "📢", "primary", "general")
reg("social_item", "رابط", "🔗", "primary", "general")
# أزرار داخلية (لا تظهر في قائمة التخصيص)
for _k, _l, _e, _s in [
    ("cz_item", "عنصر", "", None), ("prev", "السابق", "◀️", None), ("next", "التالي", "▶️", None),
    ("c_emoji", "تعيين إيموجي مميز", "✨", "success"), ("c_label", "تعديل اسم الزر", "✏️", "primary"),
    ("c_noemoji", "حذف الإيموجي", "❌", "danger"), ("c_reset", "إعادة ضبط الزر", "♻️", "danger"),
    ("c_resetall", "إعادة ضبط كل الأزرار", "♻️", "danger"),
]:
    reg(_k, _l, _e, _s, "internal")

# تخطيط لوحة التحكم (الأول = يمين في الواجهة العربية)
PANEL_LAYOUT = [
    [("p_toggle", "التفعيل والتعطيل", "🔒", "t:bot_on:home")],
    [("p_reply", "إدارة الردود التلقائية", "🤖", "m:reply"), ("p_welcome", "إدارة رسالة الترحيب", "👋", "m:welcome")],
    [("p_muted", "إدارة المكتومين", "🔕", "m:muted"), ("p_banned", "الكلمات الممنوعة", "⛔", "m:banned")],
    [("p_bc", "الإذاعة للعملاء", "📣", "m:bc"), ("p_force", "الاشتراك الإجباري", "📢", "m:force")],
    [("p_media", "إدارة الميديا", "🎞", "m:media"), ("p_social", "إدارة السوشيال ميديا", "🌐", "m:social")],
    [("p_rates", "أسعار العملات", "💱", "m:rates"), ("p_pay", "طرق الدفع", "👛", "m:pay")],
    [("p_calc", "الآلة الحاسبة", "🧮", "m:calc"), ("p_codes", "أكواد التحويل", "🏷", "m:codes")],
    [("p_clock", "الساعة الحية", "⏱", "m:clock"), ("p_bio", "البايو الحي", "🔄", "m:bio")],
    [("p_anti", "ضد الحذف والتعديل", "🛡", "m:anti"), ("p_shapes", "فحص أشكال الرسائل", "🔍", "m:shapes")],
    [("p_custom", "تخصيص أزرار اللوحة", "🎨", "m:custom"), ("p_texts", "إدارة النصوص", "📝", "m:texts")],
    [("p_photo", "تغيير صورة الترحيب", "🖼", "a:photo"), ("p_links", "إدارة الروابط", "🔗", "m:links")],
    [("p_stats", "الإحصائيات", "📊", "a:stats"), ("p_data", "النسخ الاحتياطي", "💾", "m:data")],
    [("p_admins", "إدارة المشرفين", "👥", "m:admins"), ("p_settings", "الإعدادات العامة", "⚙️", "m:settings")],
]
for _row in PANEL_LAYOUT:
    for _k, _l, _e, _d in _row:
        reg(_k, _l, _e, "primary", "panel")

# القوائم الفرعية: (إيموجي، عنوان، وصف، عناصر[(نوع، مفتاح، اسم، إيموجي)])
# الأنواع: t = زر تبديل، a = إجراء، e = تعديل نص، v = عرض نص
MENUS = {
    "reply": ("🤖", "إدارة الردود التلقائية", "ردود تلقائية على كلمات العملاء داخل محادثات Business.", [
        ("t", "auto_on", "تفعيل الردود", "⚡"), ("a", "addrep", "إضافة رد", "➕"),
        ("a", "listrep", "عرض وحذف الردود", "📋")]),
    "welcome": ("👋", "إدارة رسالة الترحيب", "تحكم بكل نصوص الترحيب وصورة الواجهة.", [
        ("t", "welcome_on", "ترحيب العملاء (Business)", "👋"), ("e", "biz_welcome", "تعديل ترحيب العملاء", "✏️"),
        ("e", "welcome", "تعديل ترحيب البوت", "📝"), ("e", "intro", "تعديل جملة التعريف", "💬"),
        ("a", "photo", "تغيير صورة الترحيب", "🖼"), ("a", "photo_del", "حذف صورة الترحيب", "🗑")]),
    "muted": ("🔕", "إدارة المكتومين", "المكتوم تُحذف رسائله تلقائياً من محادثتك.", [
        ("a", "mute", "كتم مستخدم", "🔇"), ("a", "listmute", "المكتومون / فك الكتم", "📋")]),
    "banned": ("⛔", "الكلمات الممنوعة", "أي رسالة تحتوي كلمة ممنوعة تُحذف تلقائياً.", [
        ("a", "addban", "إضافة كلمة", "➕"), ("a", "listban", "عرض وحذف الكلمات", "📋")]),
    "bc": ("📣", "الإذاعة للعملاء", "أرسل رسالة جماعية لمستخدمي البوت وعملائك.", [
        ("a", "bc", "إرسال إذاعة", "📣"), ("a", "stats", "الإحصائيات", "📊")]),
    "force": ("📢", "الاشتراك الإجباري", "إلزام المستخدمين بالاشتراك في قناة قبل استخدام البوت.", [
        ("a", "force_set", "تعيين قناة", "✏️"), ("a", "force_off", "إلغاء الاشتراك الإجباري", "🗑")]),
    "media": ("🎞", "إدارة الميديا", "ميديا تُرسل تلقائياً عندما يكتب العميل كلمة مفتاحية.", [
        ("a", "media_add", "إضافة ميديا", "➕"), ("a", "media_list", "عرض وحذف الميديا", "📋")]),
    "social": ("🌐", "إدارة السوشيال ميديا", "روابط حساباتك التي تظهر للمستخدمين.", [
        ("a", "social_add", "إضافة رابط", "➕"), ("a", "social_list", "عرض وحذف الروابط", "📋")]),
    "rates": ("💱", "أسعار العملات", "النص الذي يراه المستخدم عند الضغط على الزر.", [
        ("e", "rates", "تعديل النص", "✏️"), ("v", "rates", "معاينة", "👁")]),
    "pay": ("👛", "طرق الدفع", "النص الذي يراه المستخدم عند الضغط على الزر.", [
        ("e", "pay", "تعديل النص", "✏️"), ("v", "pay", "معاينة", "👁")]),
    "codes": ("🏷", "أكواد التحويل", "النص الذي يراه المستخدم عند الضغط على الزر.", [
        ("e", "codes", "تعديل النص", "✏️"), ("v", "codes", "معاينة", "👁")]),
    "calc": ("🧮", "الآلة الحاسبة", "إتاحة الآلة الحاسبة للمستخدمين.", [
        ("t", "calc_on", "تفعيل للعملاء", "🧮"), ("e", "calc", "تعديل رسالة الحاسبة", "✏️")]),
    "clock": ("⏱", "الساعة الحية", "يظهر الوقت بجانب اسمك ويتحدث كل دقيقة.", [
        ("t", "clock_on", "تفعيل الساعة", "⏱"), ("a", "tz", "فرق التوقيت", "🌍"),
        ("a", "clock_fmt", "صيغة الساعة", "🕐")]),
    "bio": ("🔄", "البايو الحي", "يتحدث البايو تلقائياً مع الوقت.", [
        ("t", "bio_on", "تفعيل البايو", "🔄"), ("a", "bio_set", "نص البايو", "✏️")]),
    "anti": ("🛡", "ضد الحذف والتعديل", "يصلك إشعار عند حذف أو تعديل العميل لرسالته.", [
        ("t", "log_edit", "رصد التعديل", "✏️"), ("t", "log_del", "رصد الحذف", "🗑")]),
    "shapes": ("🔍", "فحص أشكال الرسائل", "اختر أنواع الرسائل التي تُحذف من العملاء تلقائياً.", [
        ("t", "blk_link", "منع الروابط", "🔗"), ("t", "blk_mention", "منع المنشن", "📛"),
        ("t", "blk_forward", "منع المحوّلة", "↪️"), ("t", "blk_sticker", "منع الملصقات", "🎭"),
        ("t", "blk_voice", "منع الصوتيات", "🎙"), ("t", "blk_media", "منع الصور والفيديو", "🖼"),
        ("t", "blk_doc", "منع الملفات", "📎"), ("t", "blk_contact", "منع الجهات والمواقع", "📍"),
        ("t", "notify_block", "إشعاري عند الحذف", "🔔")]),
    "links": ("🔗", "إدارة الروابط", "روابط أزرار الواجهة الرئيسية.", [
        ("a", "set_botch", "رابط قناة البوت", "🤖"), ("a", "set_devch", "رابط قناة المطور", "💼"),
        ("a", "set_devurl", "رابط زر المطور", "👤")]),
    "settings": ("⚙️", "الإعدادات العامة", "إعدادات النظام. تغيير المالك وإعادة الضبط للمالك فقط.", [
        ("t", "require_conn", "اشتراط ربط Business لفتح اللوحة", "🔗"), ("t", "bot_on", "تشغيل البوت", "🔒"),
        ("a", "owner_set", "تغيير المالك", "👑"), ("a", "reset_all", "إعادة ضبط كل الإعدادات", "♻️")]),
    "admins": ("👥", "إدارة المشرفين", "أضف أو احذف مشرفين يدخلون لوحة التحكم (للمالك فقط).", [
        ("a", "admin_add", "إضافة مشرف", "➕"), ("a", "admin_list", "عرض وحذف المشرفين", "📋")]),
    "data": ("💾", "النسخ الاحتياطي", "احفظ نسخة من بياناتك (مهم جداً على الاستضافات المجانية).", [
        ("a", "backup", "تحميل نسخة", "📥"), ("a", "restore", "استرجاع نسخة", "📤")]),
}
for _m, (_me, _mt, _md, _items) in MENUS.items():
    for _kind, _key, _label, _em in _items:
        reg(f"{_m}_{_key}", _label, _em, None if _kind == "t" else "primary", "sub")
for _k, (_l, _d) in TEXTS.items():
    reg(f"texts_{_k}", _l, "📝", "primary", "internal")

GROUPS = {"home": "🏠 الواجهة الرئيسية", "panel": "🎛 لوحة التحكم", "sub": "📂 القوائم الفرعية", "general": "⚙️ أزرار عامة"}
STYLE_NAMES = {"primary": "🔵 أزرق", "success": "🟢 أخضر", "danger": "🔴 أحمر", "none": "⚪ بدون لون"}


def btn(key, data=None, style=None, url=None, text=None, suffix="", eid=None):
    """يبني InlineKeyboardButton مع (لون + إيموجي مميز + اسم) من إعدادات الأدمن."""
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


def now_str():
    try:
        off = float(get("tz", "3") or 3)
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


def is_connected(uid):
    return bool(q("SELECT 1 FROM bconns WHERE owner=?", uid))


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


async def subscribed(bot, uid):
    ch = get("force_channel")
    if not ch or is_admin(uid):
        return True
    try:
        mem = await bot.get_chat_member(ch, uid)
        return mem.status in ("member", "administrator", "creator")
    except Exception:
        return True


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
    fn = getattr(bot, "send_" + ctype)
    return await fn(**kw)


async def show(c, text, markup=None):
    """يعدّل رسالة اللوحة (أو يرسل جديدة إن كانت الرسالة صورة)."""
    bot, msg = c.bot, c.message
    if msg is None or not getattr(msg, "text", None):
        return await with_fallback(bot.send_message, markup, chat_id=c.from_user.id, text=text)
    try:
        await with_fallback(msg.edit_text, markup, text=text)
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        await with_fallback(bot.send_message, markup, chat_id=c.from_user.id, text=text)


# ════════════════════════ الواجهة الرئيسية ════════════════════════
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
    if is_admin(uid):
        if is_connected(uid) or not need_conn():
            rows.append([btn("u_panel", "p:home")])
        else:
            rows.append([btn("u_connect", "connect_warning")])
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


@router.message(Command("panel"), F.chat.type == "private", ADMIN)
async def cmd_panel(m: Message, state: FSMContext):
    await state.clear()
    if need_conn() and not is_connected(m.from_user.id):
        return await m.answer(T("no_connect"))
    await with_fallback(m.answer, panel_kb(), text=panel_header())


@router.message(Command("calc"), F.chat.type == "private")
async def cmd_calc(m: Message, command=None):
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
    await c.answer(cut(re.sub(r"<[^>]+>", "", T("no_connect")), 190), show_alert=True)


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
    sec = c.data[2:]
    close = [btn("close", "close")]
    if sec == "calc":
        await state.set_state(U.calc)
        await send_text(bot, c.from_user.id, T("calc"), markup=kb([close]))
    elif sec in ("rates", "pay", "codes"):
        await send_text(bot, c.from_user.id, T(sec), markup=kb([close]))
    elif sec == "social":
        items = [btn("social_item", url=norm_url(u), text=n) for n, u in q("SELECT name, url FROM socials ORDER BY id") if norm_url(u)]
        if not items:
            return await c.answer(plain("no_socials"), show_alert=True)
        await send_text(bot, c.from_user.id, T("social"), markup=kb(pairs(items) + [close]))
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


# ════════════════════════ لوحة التحكم ════════════════════════
def panel_header():
    return (f"{T('panel')}\n\n"
            f"📌 حالة البوت: <b>{'✅ مفعّل' if on('bot_on') else '⛔ معطّل'}</b>\n"
            f"🔗 الحسابات المربوطة: <b>{q('SELECT COUNT(*) FROM bconns')[0][0]}</b>\n"
            f"👥 المستخدمون: <b>{q('SELECT COUNT(*) FROM users')[0][0]}</b>\n\n"
            f"{T('panel_hint')}")


def panel_kb():
    return kb([[btn(k, d) for k, _, _, d in row] for row in PANEL_LAYOUT])


def menu_extra(name):
    n = lambda sql: q(sql)[0][0]
    if name == "force":
        return f"📢 القناة الحالية: <b>{esc(get('force_channel')) or 'غير مضبوطة'}</b>"
    if name == "clock":
        return f"🌍 فرق التوقيت: <b>UTC{esc(get('tz', '3'))}</b>\n🕐 الصيغة: <code>{esc(get('clock_fmt', '🕐 {time}'))}</code>"
    if name == "bio":
        return f"📝 النص الحالي: <code>{esc(get('bio_text', '🕐 {time}'))}</code>"
    if name == "links":
        return (f"🤖 قناة البوت: {esc(get('bot_channel', BOT_CHANNEL)) or '—'}\n"
                f"💼 قناة المطور: {esc(get('dev_channel', DEV_CHANNEL)) or '—'}\n"
                f"👤 زر المطور: {esc(get('dev_url', DEV_URL)) or '—'}")
    if name == "welcome":
        p = get("welcome_photo", "")
        return "🖼 الصورة: " + ("<b>محذوفة</b>" if p == "-" else "<b>مخصصة ✅</b>" if p else "<b>افتراضية</b>")
    if name == "settings":
        return (f"👑 المالك: <code>{cur_owner()}</code>\n"
                f"🔗 شرط الربط: <b>{'مفعّل' if need_conn() else 'معطّل'}</b>"
                + ("" if REQUIRE_CONNECTION else "\n<i>(معطّل بالمتغير REQUIRE_CONNECTION=0)</i>"))
    if name == "admins":
        return f"👑 المالك: <code>{cur_owner()}</code>\n👥 المشرفون المضافون: <b>{len(_admins)}</b>"
    counts = {"reply": "SELECT COUNT(*) FROM replies", "banned": "SELECT COUNT(*) FROM banned",
              "muted": "SELECT COUNT(*) FROM muted", "media": "SELECT COUNT(*) FROM media",
              "social": "SELECT COUNT(*) FROM socials"}
    return f"📦 العدد الحالي: <b>{n(counts[name])}</b>" if name in counts else ""


def menu_view(name):
    emoji, title, desc, items = MENUS[name]
    extra = menu_extra(name)
    text = f"{emoji} <b>{title}</b>\n\n{desc}" + (f"\n\n{extra}" if extra else "")
    rows, pend = [], []
    for kind, key, _label, _em in items:
        bk = f"{name}_{key}"
        if kind == "t":
            if pend:
                rows.append(pend)
                pend = []
            st = on(key)
            rows.append([btn(bk, f"t:{key}:{name}", style="success" if st else "danger",
                             suffix=": " + plain("on_label" if st else "off_label"))])
            continue
        data = {"a": f"a:{key}", "e": f"e:{key}:{name}", "v": f"v:{key}:{name}"}[kind]
        pend.append(btn(bk, data))
        if len(pend) == 2:
            rows.append(pend)
            pend = []
    if pend:
        rows.append(pend)
    rows.append([btn("back", "p:home")])
    return text, kb(rows)


def texts_view():
    items = [btn(f"texts_{k}", f"e:{k}:texts") for k in TEXTS]
    text = ("📝 <b>إدارة النصوص</b>\n\nكل نصوص البوت قابلة للتعديل من هنا. "
            "تدعم التنسيق والإيموجي المميز، والمتغير <code>{name}</code> يُستبدل باسم المستخدم.")
    return text, kb(pairs(items) + [[btn("back", "p:home")]])


def custom_home():
    text = ("🎨 <b>تخصيص أزرار البوت</b>\n\n"
            "اختر مجموعة، ثم الزر لتعيين <b>إيموجي مميز</b> أو تغيير اسمه ولونه.\n"
            "كل التغييرات تُطبّق فوراً على كل أماكن ظهور الزر.\n\n"
            "⚠️ الإيموجي المميز على الأزرار يتطلب أن يكون <b>مالك البوت مشتركاً في Telegram Premium</b>.")
    items = [btn("cz_item", f"cz:{g}:0", text=t) for g, t in GROUPS.items()]
    ion = on("icons_on")
    rows = pairs(items)
    rows.append([btn("cz_item", "t:icons_on:custom", style="success" if ion else "danger",
                     text="الإيموجي المميز: " + ("مفعّل" if ion else "معطّل"))])
    rows.append([btn("c_resetall", "cr_all"), btn("back", "p:home")])
    return text, kb(rows)


def view_of(name):
    if name == "home":
        return panel_header(), panel_kb()
    if name == "custom":
        return custom_home()
    if name == "texts":
        return texts_view()
    return menu_view(name)


@router.callback_query(F.data == "p:home", ADMIN)
async def cb_panel_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    if need_conn() and not is_connected(c.from_user.id):
        return await c.answer(cut(re.sub(r"<[^>]+>", "", T("no_connect")), 190), show_alert=True)
    await show(c, panel_header(), panel_kb())
    await c.answer()


@router.callback_query(F.data.startswith("m:"), ADMIN)
async def cb_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    name = c.data[2:]
    if name == "admins" and not is_owner(c.from_user.id):
        return await c.answer("🚫 للمالك فقط.", show_alert=True)
    if name not in MENUS and name not in ("custom", "texts"):
        return await c.answer()
    text, markup = view_of(name)
    await show(c, text, markup)
    await c.answer()


async def reset_names(bot):
    for cid, _o, _ch, fn, ln in q("SELECT id, owner, chat, fn, ln FROM bconns"):
        try:
            await bot.set_business_account_name(business_connection_id=cid, first_name=fn, last_name=ln or None)
        except Exception as e:
            log.warning("reset name: %s", e)


async def bio_enable(bot):
    for cid, owner, *_ in q("SELECT id, owner FROM bconns"):
        try:
            ch = await bot.get_chat(owner)
            put(f"orig_bio_{cid}", getattr(ch, "bio", None) or "")
        except Exception as e:
            log.warning("orig bio: %s", e)


async def bio_restore(bot):
    for cid, *_ in q("SELECT id FROM bconns"):
        try:
            await bot.set_business_account_bio(business_connection_id=cid, bio=get(f"orig_bio_{cid}") or None)
        except Exception as e:
            log.warning("restore bio: %s", e)


@router.callback_query(F.data.startswith("t:"), ADMIN)
async def cb_toggle(c: CallbackQuery, bot: Bot):
    _, key, back = c.data.split(":", 2)
    new = not on(key)
    put(key, "1" if new else "0")
    if key == "clock_on" and not new:
        await reset_names(bot)
    if key == "bio_on":
        await (bio_enable(bot) if new else bio_restore(bot))
    text, markup = view_of(back)
    await show(c, text, markup)
    await c.answer("✅ مفعّل" if new else "⛔ معطّل")


# ---- القوائم القابلة للحذف ----
LISTS = {
    "listrep": ("rep", "🤖 الردود التلقائية", "SELECT id, kw FROM replies ORDER BY id DESC LIMIT 40", "reply"),
    "listban": ("ban", "⛔ الكلمات الممنوعة", "SELECT id, w FROM banned ORDER BY id DESC LIMIT 40", "banned"),
    "listmute": ("mute", "🔕 المكتومون",
                 "SELECT m.uid, COALESCE((SELECT name FROM customers c WHERE c.uid=m.uid LIMIT 1), m.uid) FROM muted m LIMIT 40",
                 "muted"),
    "social_list": ("soc", "🌐 روابط السوشيال", "SELECT id, name FROM socials ORDER BY id LIMIT 40", "social"),
    "admin_list": ("adm", "👥 المشرفون", "SELECT uid, uid FROM admins ORDER BY uid LIMIT 40", "admins"),
    "media_list": ("med", "🎞 الميديا", "SELECT id, kw FROM media ORDER BY id DESC LIMIT 40", "media"),
}
DELS = {"rep": ("replies", "id", "listrep"), "ban": ("banned", "id", "listban"), "mute": ("muted", "uid", "listmute"),
        "soc": ("socials", "id", "social_list"), "med": ("media", "id", "media_list"), "adm": ("admins", "uid", "admin_list")}


def list_view(action):
    kind, title, sql, menu = LISTS[action]
    rows_db = q(sql)
    if not rows_db:
        return f"{title}\n\n<i>لا توجد عناصر.</i>", kb([[btn("back", f"m:{menu}")]])
    items = [btn("del", f"x:{kind}:{i}", text=cut(n, 26)) for i, n in rows_db]
    return f"{title}\n\n<i>اضغط على أي عنصر لحذفه 👇</i>", kb(pairs(items) + [[btn("back", f"m:{menu}")]])


@router.callback_query(F.data.startswith("x:"), ADMIN)
async def cb_delete_item(c: CallbackQuery):
    _, kind, ident = c.data.split(":", 2)
    if kind not in DELS:
        return await c.answer()
    if kind == "adm" and not is_owner(c.from_user.id):
        return await c.answer("🚫 للمالك فقط.", show_alert=True)
    table, col, action = DELS[kind]
    ex(f"DELETE FROM {table} WHERE {col}=?", int(ident))
    if kind == "adm":
        _admins.discard(int(ident))
    text, markup = list_view(action)
    await show(c, text, markup)
    await c.answer("🗑 تم الحذف")


def stats_text():
    n = lambda s: q(s)[0][0]
    return ("📊 <b>الإحصائيات</b>\n\n"
            f"👥 مستخدمو البوت: <b>{n('SELECT COUNT(*) FROM users')}</b>\n"
            f"🧾 عملاء Business: <b>{n('SELECT COUNT(*) FROM customers')}</b>\n"
            f"🔗 حسابات مربوطة: <b>{n('SELECT COUNT(*) FROM bconns')}</b>\n"
            f"🤖 ردود تلقائية: <b>{n('SELECT COUNT(*) FROM replies')}</b>\n"
            f"⛔ كلمات ممنوعة: <b>{n('SELECT COUNT(*) FROM banned')}</b>\n"
            f"🔕 مكتومون: <b>{n('SELECT COUNT(*) FROM muted')}</b>\n"
            f"🎞 ميديا: <b>{n('SELECT COUNT(*) FROM media')}</b>")


PROMPTS = {
    "addrep": "➕ <b>إضافة رد تلقائي</b>\nأرسل بالصيغة:\n<code>الكلمة | الرد</code>\n\nالرد يدعم التنسيق والإيموجي المميز.",
    "addban": "⛔ أرسل الكلمة أو العبارة الممنوعة:",
    "admin_add": "👥 أرسل آيدي المشرف الجديد (رقم). ليستلم الإشعار يجب أن يكون قد بدأ البوت:",
    "owner_set": "👑 أرسل آيدي المالك الجديد (رقم). ⚠️ ستفقد صلاحية المالك فوراً:",
    "mute": "🔇 أرسل آيدي المستخدم (رقم) المراد كتمه:",
    "bc": "📣 أرسل نص الإذاعة (يدعم التنسيق):",
    "force_set": "📢 أرسل يوزر القناة مثل <code>@channel</code> (يجب أن يكون البوت مشرفاً فيها):",
    "set_botch": "🤖 أرسل رابط قناة البوت (<code>@user</code> أو رابط https):",
    "set_devch": "💼 أرسل رابط قناة المطور (<code>@user</code> أو رابط https):",
    "set_devurl": "👤 أرسل رابط زر المطور (<code>@user</code> أو رابط https):",
    "social_add": "🌐 أرسل بالصيغة:\n<code>الاسم | الرابط</code>\nمثال: <code>انستغرام | https://instagram.com/user</code>",
    "media_add": "🎞 أرسل الميديا (صورة / فيديو / GIF / ملف / صوت / ملصق):",
    "tz": "🌍 أرسل فرق التوقيت عن UTC (مثال: <code>3</code> أو <code>5.5</code> أو <code>-4</code>):",
    "clock_fmt": "⏱ أرسل صيغة الساعة، واستخدم <code>{time}</code> للوقت.\nمثال: <code>🕐 {time}</code>",
    "bio_set": "🔄 أرسل نص البايو (حتى 70 حرفاً) واستخدم <code>{time}</code> للوقت:",
    "photo": "🖼 أرسل الصورة التي تريدها كصورة ترحيب (أو رابط صورة مباشر):",
    "restore": "♻️ أرسل ملف النسخة الاحتياطية (<code>.db</code>)\n⚠️ سيتم استبدال كل البيانات الحالية.",
}
ACT_BACK = {"addrep": "reply", "addban": "banned", "mute": "muted", "bc": "bc", "force_set": "force",
            "set_botch": "links", "set_devch": "links", "set_devurl": "links", "social_add": "social",
            "media_add": "media", "tz": "clock", "clock_fmt": "clock", "bio_set": "bio", "photo": "welcome",
            "restore": "data", "admin_add": "admins", "owner_set": "settings"}
TEXT_ONLY = {"owner_set", "admin_add", "addrep", "addban", "mute", "bc", "force_set", "set_botch", "set_devch", "set_devurl",
             "social_add", "tz", "clock_fmt", "bio_set", "label", "text", "media_kw"}


async def ask(c, state, action, prompt, back, **extra):
    await state.set_state(Form.wait)
    await state.update_data(action=action, back=back, **extra)
    await send_text(c.bot, c.from_user.id, prompt, markup=kb([[btn("cancel", "cancel")]]))


@router.callback_query(F.data == "cancel", ADMIN)
async def cb_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "✖️ تم الإلغاء.", kb([[btn("to_panel", "p:home")]]))
    await c.answer()


@router.callback_query(F.data.startswith("a:"), ADMIN)
async def cb_action(c: CallbackQuery, state: FSMContext, bot: Bot):
    a = c.data[2:]
    if a in ("admin_add", "admin_list", "owner_set", "reset_all", "reset_do") and not is_owner(c.from_user.id):
        return await c.answer("🚫 للمالك فقط.", show_alert=True)
    if a in PROMPTS:
        await ask(c, state, a, PROMPTS[a], "m:" + ACT_BACK[a])
    elif a in LISTS:
        text, markup = list_view(a)
        await show(c, text, markup)
    elif a == "stats":
        await show(c, stats_text(), kb([[btn("back", "p:home")]]))
    elif a == "reset_all":
        await show(c, "⚠️ <b>إعادة ضبط كل الإعدادات؟</b>\n\nسيُحذف: كل النصوص المعدّلة، تخصيص الأزرار، الروابط، "
                      "الاشتراك الإجباري، وصورة الترحيب.\nلن يُحذف: الردود، الكلمات الممنوعة، المكتومون، المشرفون، الميديا.",
                   kb([[raw("✅ نعم، أعد الضبط", "a:reset_do", "danger"), btn("back", "m:settings")]]))
    elif a == "reset_do":
        keep = get("owner_id")
        ex("DELETE FROM kv")
        ex("DELETE FROM btn_cfg")
        _kv.clear()
        _cfg.clear()
        if keep:
            put("owner_id", keep)
        text, markup = view_of("settings")
        await show(c, text, markup)
    elif a == "photo_del":
        put("welcome_photo", "-")
        text, markup = view_of("welcome")
        await show(c, text, markup)
    elif a == "force_off":
        kv_del("force_channel")
        text, markup = view_of("force")
        await show(c, text, markup)
    elif a == "backup":
        tmp = os.path.join(tempfile.gettempdir(), f"bk_{int(time.time())}.db")
        dst = sqlite3.connect(tmp)
        db.backup(dst)
        dst.close()
        with open(tmp, "rb") as f:
            data = f.read()
        os.remove(tmp)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        await bot.send_document(c.from_user.id, BufferedInputFile(data, f"business_bot_{stamp}.db"),
                                caption="💾 نسخة احتياطية كاملة. احتفظ بها في مكان آمن.")
    await c.answer()


@router.callback_query(F.data.startswith("e:"), ADMIN)
async def cb_edit_text(c: CallbackQuery, state: FSMContext):
    _, key, back = c.data.split(":", 2)
    if key not in TEXTS:
        return await c.answer()
    prompt = (f"✏️ <b>{TEXTS[key][0]}</b>\n\nأرسل النص الجديد (يدعم التنسيق والإيموجي المميز).\n"
              "المتغير <code>{name}</code> = اسم المستخدم.\n↩️ أرسل /reset لاستعادة النص الافتراضي.")
    await ask(c, state, "text", prompt, f"m:{back}", key=key)
    await c.answer()


@router.callback_query(F.data.startswith("v:"), ADMIN)
async def cb_view_text(c: CallbackQuery):
    _, key, back = c.data.split(":", 2)
    if key not in TEXTS:
        return await c.answer()
    await show(c, render(T(key), c.from_user.full_name), kb([[btn("back", f"m:{back}")]]))
    await c.answer()


# ---- تخصيص الأزرار (الإيموجي المميز) ----
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
    text = (f"🎨 <b>تخصيص الزر</b>\n\n"
            f"🔹 الاسم: <b>{esc(cfg.get('label') or label)}</b>\n"
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
    await ask(c, state, "icon", prompt, f"cd:{key}", key=key)
    await c.answer()


@router.callback_query(F.data.startswith("cl:"), ADMIN)
async def cb_cl(c: CallbackQuery, state: FSMContext):
    key = c.data[3:]
    await ask(c, state, "label", "✏️ أرسل الاسم الجديد للزر (حتى 40 حرفاً):", f"cd:{key}", key=key)
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
    if key == "all":
        ex("DELETE FROM btn_cfg")
        _cfg.clear()
        text, markup = custom_home()
    else:
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


# ════════════════════════ استقبال مدخلات الأدمن ════════════════════════
async def finish(m, state, text, back="p:home"):
    await state.clear()
    rows = [[btn("back", back), btn("to_panel", "p:home")]] if back != "p:home" else [[btn("to_panel", "p:home")]]
    await with_fallback(m.answer, kb(rows), text=text)


async def do_broadcast(bot, admin_chat, text):
    ok = bad = 0
    targets = [(uid, None) for (uid,) in q("SELECT uid FROM users")]
    targets += [(chat, conn) for chat, conn in q("SELECT chat, conn FROM customers")]
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


@router.message(StateFilter(Form.wait), F.chat.type == "private", ADMIN)
async def got_input(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    a, back = d.get("action"), d.get("back", "p:home")
    t = (m.text or "").strip()
    ht = m.html_text if m.text else ""

    if a in TEXT_ONLY and not m.text:
        return await m.answer("⚠️ أرسل نصاً فقط.")

    if a == "text":
        key = d["key"]
        if t.lower() == "/reset":
            kv_del("txt_" + key)
            return await finish(m, state, "↩️ تمت استعادة النص الافتراضي.", back)
        put("txt_" + key, ht)
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
                                   reply_markup=kb([[btn(key, "noop", eid=eid, text=None)]]))
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
        ex("INSERT OR REPLACE INTO media(kw, ctype, fid, cap) VALUES(?,?,?,?)", t.lower(), d["ctype"], d["fid"], d.get("cap", ""))
        return await finish(m, state, f"✅ تمت إضافة الميديا للكلمة: <b>{esc(t)}</b>", back)

    if a == "photo":
        if m.photo:
            put("welcome_photo", m.photo[-1].file_id)
        elif m.text and t.startswith("http"):
            put("welcome_photo", t)
        else:
            return await m.answer("⚠️ أرسل صورة أو رابط صورة مباشر.")
        await state.clear()
        await m.answer("✅ تم تغيير صورة الترحيب. هذه معاينتها:")
        return await send_home(bot, m.chat.id, m.from_user)

    if a == "restore":
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
        task = asyncio.create_task(do_broadcast(bot, m.chat.id, ht))
        BG.add(task)
        task.add_done_callback(BG.discard)
        return await m.answer("⏳ بدأت الإذاعة، سأخبرك عند الانتهاء.")

    # ---- بقية الإجراءات النصية ----
    r = "✅ تم الحفظ."
    if a == "addrep":
        if "|" not in t:
            return await m.answer("⚠️ الصيغة: <code>الكلمة | الرد</code>")
        kw = t.split("|", 1)[0].strip().lower()
        resp = ht.split("|", 1)[1].strip() if "|" in ht else ""
        if not kw or not resp:
            return await m.answer("⚠️ الكلمة أو الرد فارغ.")
        ex("INSERT OR REPLACE INTO replies(kw, resp) VALUES(?,?)", kw, resp)
        r = f"✅ تمت إضافة الرد للكلمة: <b>{esc(kw)}</b>"
    elif a == "addban":
        ex("INSERT OR IGNORE INTO banned(w) VALUES(?)", t.lower())
        r = "✅ تمت إضافة الكلمة الممنوعة."
    elif a == "admin_add":
        if not is_owner(m.from_user.id):
            return await m.answer("🚫 للمالك فقط.")
        if not t.isdigit():
            return await m.answer("⚠️ أرسل آيدي رقمياً.")
        ex("INSERT OR IGNORE INTO admins(uid) VALUES(?)", int(t))
        _admins.add(int(t))
        r = f"✅ تمت إضافة المشرف <code>{int(t)}</code>"
        try:
            await bot.send_message(int(t), "👑 تمت إضافتك مشرفاً في البوت. أرسل /start لفتح لوحة التحكم.")
        except Exception:
            r += "\n(لم أستطع إشعاره، لأنه لم يبدأ البوت بعد)"
    elif a == "owner_set":
        if not is_owner(m.from_user.id):
            return await m.answer("🚫 للمالك فقط.")
        if not t.isdigit():
            return await m.answer("⚠️ أرسل آيدي رقمياً.")
        put("owner_id", t)
        r = f"✅ المالك الجديد: <code>{t}</code>"
        try:
            await bot.send_message(int(t), "👑 أصبحت مالك البوت. أرسل /start لفتح لوحة التحكم.")
        except Exception:
            r += "\n(لم أستطع إشعاره، يجب أن يبدأ البوت أولاً)"
    elif a == "mute":
        if not t.isdigit():
            return await m.answer("⚠️ أرسل آيدي رقمياً.")
        ex("INSERT OR IGNORE INTO muted(uid) VALUES(?)", int(t))
        r = "🔇 تم الكتم."
    elif a == "force_set":
        ch = channel_ref(t)
        if not ch:
            return await m.answer("⚠️ أرسل يوزر صحيحاً مثل <code>@channel</code>")
        put("force_channel", ch)
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
        ex("INSERT INTO socials(name, url) VALUES(?,?)", n, norm_url(u))
        r = "✅ تمت إضافة الرابط."
    elif a == "tz":
        try:
            float(t)
        except ValueError:
            return await m.answer("⚠️ أرسل رقماً مثل 3 أو -4 أو 5.5")
        put("tz", t)
        r = "✅ تم حفظ فرق التوقيت."
    elif a == "clock_fmt":
        put("clock_fmt", t)
        r = "✅ تم حفظ صيغة الساعة."
    elif a == "bio_set":
        put("bio_text", t[:70])
        r = "✅ تم حفظ نص البايو."
    else:
        r = "؟"
    await finish(m, state, r, back)


# ════════════════════════ Telegram Business ════════════════════════
@router.business_connection()
async def on_conn(c: BusinessConnection, bot: Bot):
    chat = getattr(c, "user_chat_id", None) or c.user.id
    if c.is_enabled:
        ex("INSERT OR REPLACE INTO bconns(id, owner, chat, fn, ln) VALUES(?,?,?,?,?)",
           c.id, c.user.id, chat, c.user.first_name, c.user.last_name or "")
        try:
            await send_text(bot, chat, T("connected"))
        except Exception as e:
            log.warning("connected msg: %s", e)
    else:
        ex("DELETE FROM bconns WHERE id=?", c.id)
        try:
            await send_text(bot, chat, T("disconnected"))
        except Exception:
            pass


def conn_owner(cid):
    r = q("SELECT owner FROM bconns WHERE id=?", cid)
    return r[0][0] if r else None


def violation(m, low):
    kinds = {e.type for e in ((m.entities or []) + (m.caption_entities or []))}
    if on("blk_link") and kinds & {"url", "text_link"}:
        return "رابط"
    if on("blk_mention") and kinds & {"mention", "text_mention"}:
        return "منشن"
    if on("blk_forward") and m.forward_origin:
        return "رسالة محوّلة"
    if on("blk_sticker") and m.sticker:
        return "ملصق"
    if on("blk_voice") and (m.voice or m.video_note):
        return "رسالة صوتية"
    if on("blk_media") and (m.photo or m.video or m.animation):
        return "صورة/فيديو"
    if on("blk_doc") and m.document and not m.animation:
        return "ملف"
    if on("blk_contact") and (m.contact or m.location):
        return "جهة اتصال/موقع"
    for (w,) in q("SELECT w FROM banned"):
        if w and w in low:
            return f"كلمة ممنوعة ({w})"
    return None


def cache_put(m, text):
    ex("INSERT OR REPLACE INTO cache(chat, mid, uid, name, uname, text, ts) VALUES(?,?,?,?,?,?,?)",
       m.chat.id, m.message_id, m.from_user.id, m.from_user.full_name, m.from_user.username or "",
       text or f"[{m.content_type}]", int(time.time()))


@router.business_message()
async def on_biz(m: Message, bot: Bot):
    owner = conn_owner(m.business_connection_id)
    if owner is None or not m.from_user:
        return
    uid = m.from_user.id
    text = m.text or m.caption or ""
    cache_put(m, text)
    if uid == owner or not on("bot_on"):
        return
    low = text.lower()
    conn = m.business_connection_id

    muted = bool(q("SELECT 1 FROM muted WHERE uid=?", uid))
    reason = "مكتوم" if muted else violation(m, low)
    if reason:
        try:
            await bot.delete_business_messages(business_connection_id=conn, message_ids=[m.message_id])
        except Exception as e:
            log.warning("delete msg: %s", e)
        if not muted and on("notify_block"):
            try:
                await send_text(bot, owner, f"🚫 <b>حُذفت رسالة مخالفة</b>\n<blockquote>👤 {esc(m.from_user.full_name)}\n"
                                            f"🆔 <code>{uid}</code>\n📌 السبب: {esc(reason)}</blockquote>")
            except Exception:
                pass
        return

    if ex("INSERT OR IGNORE INTO customers(conn, uid, chat, name, ts) VALUES(?,?,?,?,?)",
          conn, uid, m.chat.id, m.from_user.full_name, int(time.time())) and on("welcome_on"):
        try:
            await send_text(bot, m.chat.id, render(T("biz_welcome"), m.from_user.full_name), conn=conn)
        except Exception as e:
            log.warning("welcome: %s", e)

    if on("auto_on") and text:
        for kw, resp in q("SELECT kw, resp FROM replies"):
            if kw in low:
                try:
                    await send_text(bot, m.chat.id, resp, conn=conn)
                except Exception as e:
                    log.warning("auto reply: %s", e)
                return
        for kw, ctype, fid, cap in q("SELECT kw, ctype, fid, cap FROM media"):
            if kw in low:
                try:
                    await send_media(bot, m.chat.id, ctype, fid, cap, conn)
                except Exception as e:
                    log.warning("auto media: %s", e)
                return


@router.edited_business_message()
async def on_edit(m: Message, bot: Bot):
    owner = conn_owner(m.business_connection_id)
    if owner is None or not m.from_user or m.from_user.id == owner or not on("log_edit"):
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
    if owner is None or not on("log_del"):
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


# ════════════════════════ ردّ احتياطي للأزرار غير المصرّح بها ════════════════════════
@router.callback_query()
async def cb_fallback(c: CallbackQuery):
    if c.data and c.data.split(":", 1)[0] in {"p", "m", "t", "a", "e", "v", "x", "cz", "cd", "ce", "cl", "cc", "cx", "cr", "cr_all", "cancel"}:
        return await c.answer("🚫 هذا الزر للمالك فقط.", show_alert=True)
    await c.answer()


# ════════════════════════ الساعة الحية والبايو الحي ════════════════════════
async def live_loop(bot: Bot):
    last, last_prune = "", 0
    while True:
        await asyncio.sleep(20)
        try:
            if time.time() - last_prune > 3600:
                last_prune = time.time()
                ex("DELETE FROM cache WHERE ts < ?", int(time.time()) - 7 * 86400)
            t = now_str()
            if t == last:
                continue
            last = t
            for cid, _o, _c, fn, ln in q("SELECT id, owner, chat, fn, ln FROM bconns"):
                if on("clock_on"):
                    fmt = get("clock_fmt", "🕐 {time}").replace("{time}", t)
                    try:
                        await bot.set_business_account_name(business_connection_id=cid, first_name=fn,
                                                            last_name=f"{ln} {fmt}".strip()[:64])
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after)
                    except Exception as e:
                        log.debug("clock: %s", e)
                if on("bio_on"):
                    try:
                        await bot.set_business_account_bio(business_connection_id=cid,
                                                           bio=get("bio_text", "🕐 {time}").replace("{time}", t)[:70])
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
        log.warning("⚠️ وضع Business غير مفعّل! BotFather ← Bot Settings ← Business Mode")
    log.info("✅ البوت @%s يعمل الآن...", me.username)
    task = asyncio.create_task(live_loop(bot))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
