import asyncio
import os
import sqlite3
import ast
import operator
import logging
import html
import time
from datetime import datetime, timezone, timedelta

# تفعيل البروكسي لتجاوز حجب تيليجرام في السيرفرات
try:
    import andro_cfw
    andro_cfw.patch()
    print("✅ تم تفعيل البروكسي (andro-cfw) لتجاوز الحجب.")
except ImportError:
    print("⚠️ لم يتم العثور على andro-cfw، الاتصال سيكون مباشراً.")

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton,
    BusinessConnection, BusinessMessagesDeleted
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================== الإعدادات ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "9062665659:AAE2ubEi1CWrrsRM83Pp0XyKMlqlelpTCvQ")
OWNER_ID = int(os.getenv("OWNER_ID", "7325566792"))
BOT_CHANNEL = os.getenv("BOT_CHANNEL", "")
DEV_CHANNEL = os.getenv("DEV_CHANNEL", "")
DEV_BUTTON_TEXT = os.getenv("DEV_BUTTON_TEXT", "💲 | MODY .")
DEV_URL = os.getenv("DEV_URL", "")
WELCOME_PHOTO_URL = "https://i.ibb.co/6P0y7vG/welcome-bot.jpg" 

logging.basicConfig(level=logging.INFO)
db = sqlite3.connect("bot.db", check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS replies(kw TEXT PRIMARY KEY, resp TEXT);
CREATE TABLE IF NOT EXISTS banned(w TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS muted(uid INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS users(uid INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS bconns(id TEXT PRIMARY KEY, owner INTEGER, fn TEXT, ln TEXT);
CREATE TABLE IF NOT EXISTS customers(uid INTEGER PRIMARY KEY, chat INTEGER, conn TEXT);
CREATE TABLE IF NOT EXISTS cache(chat INTEGER, mid INTEGER, uid INTEGER, name TEXT, uname TEXT, text TEXT, PRIMARY KEY(chat, mid));
CREATE TABLE IF NOT EXISTS button_icons(key TEXT PRIMARY KEY, emoji_id TEXT);
""")
db.commit()

DEFAULT_ON = {"bot_on", "auto_on", "welcome_on", "calc_on", "log_edit", "log_del"}
router = Router()

class S(StatesGroup):
    wait = State()

def get(k, d=""):
    r = db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r[0] if r else d

def put(k, v):
    db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, v)); db.commit()

def q(sql, *a):
    return db.execute(sql, a).fetchall()

def ex(sql, *a):
    db.execute(sql, a); db.commit()

def on(k):
    return get(k, "1" if k in DEFAULT_ON else "0") == "1"

def esc(s):
    return html.escape(str(s or ""))

def get_icon(key, default_emoji):
    icon = db.execute("SELECT emoji_id FROM button_icons WHERE key=?", (key,)).fetchone()
    if icon:
        return f'<tg-emoji emoji-id="{icon[0]}">{default_emoji}</tg-emoji>'
    return default_emoji

# ================== أدوات ==================
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg, ast.Mod: operator.mod}

def calc(node):
    if isinstance(node, ast.Expression): return calc(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)): return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        l, r = calc(node.left), calc(node.right)
        if isinstance(node.op, ast.Pow) and abs(r) > 100: raise ValueError
        return OPS[type(node.op)](l, r)
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS: return OPS[type(node.op)](calc(node.operand))
    raise ValueError

def now_str():
    tz = timezone(timedelta(hours=float(get("tz", "3") or 3)))
    n = datetime.now(tz)
    return f"{n.strftime('%I:%M').lstrip('0')} {'م' if n.hour >= 12 else 'ص'}"

async def subscribed(bot: Bot, uid: int) -> bool:
    ch = get("force_channel")
    if not ch or uid == OWNER_ID: return True
    try:
        m = await bot.get_chat_member(ch, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception: return True

# ================== لوحة التحكم ==================
def panel_header():
    return (f"👑 <b>لوحة التحكم</b>\n\n"
            f"📌 حالة الحساب: {'✅ مفعل' if on('bot_on') else '⛔ معطل'}\n"
            f"🔗 الحسابات المربوطة: {q('SELECT COUNT(*) FROM bconns')[0][0]}")

def panel_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🔒 التفعيل والتعطيل", callback_data="toggle_bot"))
    b.row(
        InlineKeyboardButton(text="🤖 الردود التلقائية", callback_data="m:reply"),
        InlineKeyboardButton(text="👋 رسالة الترحيب", callback_data="m:welcome")
    )
    b.row(
        InlineKeyboardButton(text="🔕 المكتومين", callback_data="m:muted"),
        InlineKeyboardButton(text="⛔ الكلمات الممنوعة", callback_data="m:banned")
    )
    b.row(
        InlineKeyboardButton(text="📣 الإذاعة", callback_data="m:bc"),
        InlineKeyboardButton(text="📢 الاشتراك الإجباري", callback_data="m:force")
    )
    b.row(
        InlineKeyboardButton(text="💱 أسعار العملات", callback_data="m:rates"),
        InlineKeyboardButton(text="👛 طرق الدفع", callback_data="m:pay")
    )
    b.row(
        InlineKeyboardButton(text="🧮 الآلة الحاسبة", callback_data="m:calc"),
        InlineKeyboardButton(text="🏷 أكواد التحويل", callback_data="m:codes")
    )
    b.row(
        InlineKeyboardButton(text="⏱ الساعة الحية", callback_data="m:clock"),
        InlineKeyboardButton(text="🔄 البايو الحي", callback_data="m:bio")
    )
    b.row(
        InlineKeyboardButton(text="🛡 ضد الحذف والتعديل", callback_data="m:anti"),
        InlineKeyboardButton(text="🎨 تخصيص أزرار اللوحة", callback_data="m:icons")
    )
    return b.as_markup()

MENUS = {
    "reply": ("🤖 إدارة الردود التلقائية", [("t", "تفعيل الردود", "auto_on"), ("a", "➕ إضافة رد", "addrep"), ("a", "🗑 حذف رد", "delrep"), ("a", "📋 عرض الردود", "listrep")]),
    "welcome": ("👋 إدارة رسالة الترحيب", [("t", "ترحيب العملاء", "welcome_on"), ("a", "✏️ تعديل النص", "welcome"), ("a", "🖼 تعيين صورة", "welcomepic"), ("a", "🗑 حذف الصورة", "delpic")]),
    "muted": ("🔕 إدارة المكتومين", [("a", "🔇 كتم", "mute"), ("a", "🔊 فك الكتم", "unmute"), ("a", "📋 عرض", "listmute")]),
    "banned": ("⛔ الكلمات الممنوعة", [("a", "➕ إضافة", "addban"), ("a", "🗑 حذف", "delban"), ("a", "📋 عرض", "listban")]),
    "bc": ("📣 الإذاعة للعملاء", [("a", "📣 إرسال إذاعة", "bc"), ("a", "📊 الإحصائيات", "stats")]),
    "force": ("📢 اشتراك الإجباري", [("a", "✏️ تعيين قناة", "force"), ("a", "🗑 إلغاء الاشتراك", "unforce")]),
    "rates": ("💱 أسعار العملات", [("a", "✏️ تعديل", "set_rates"), ("a", "👁 عرض", "show_rates")]),
    "pay": ("👛 طرق الدفع", [("a", "✏️ تعديل", "set_pay"), ("a", "👁 عرض", "show_pay")]),
    "codes": ("🏷 أكواد التحويل", [("a", "✏️ تعديل", "set_codes"), ("a", "👁 عرض", "show_codes")]),
    "calc": ("🧮 الآلة الحاسبة", [("t", "تفعيلها للعملاء", "calc_on")]),
    "clock": ("⏱ الساعة الحية", [("t", "تفعيل الساعة", "clock_on"), ("a", "🌍 فرق التوقيت", "tz")]),
    "bio": ("🔄 البايو الحي", [("t", "تفعيل البايو", "bio_on"), ("a", "✏️ نص البايو", "bio")]),
    "anti": ("🛡 ضد الحذف والتعديل", [("t", "رصد التعديل", "log_edit"), ("t", "رصد الحذف", "log_del")]),
}

def menu_kb(name):
    _, items = MENUS[name]
    b = InlineKeyboardBuilder()
    for kind, label, key in items:
        if kind == "t":
            b.row(InlineKeyboardButton(text=f"{'✅' if on(key) else '❌'} {label}", callback_data=f"t:{key}:{name}"))
        else:
            b.row(InlineKeyboardButton(text=label, callback_data=f"a:{key}"))
    b.row(InlineKeyboardButton(text="🔙 رجوع", callback_data="p:home"))
    return b.as_markup()

async def show(c: CallbackQuery, text, kb):
    try: await c.message.edit_text(text, reply_markup=kb)
    except: await c.message.answer(text, reply_markup=kb)

# ================== أوامر عامة ==================
@router.message(CommandStart())
async def start(m: Message, bot: Bot):
    ex("INSERT OR IGNORE INTO users VALUES(?)", m.from_user.id)
    
    if not await subscribed(bot, m.from_user.id):
        ch = get("force_channel").lstrip("@")
        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(text="📢 اشترك بالقناة", url=f"https://t.me/{ch}"))
        b.row(InlineKeyboardButton(text="✅ تحققت", callback_data="check_sub"))
        return await m.answer("⚠️ لازم تشترك بالقناة أولاً لاستخدام البوت.", reply_markup=b.as_markup())

    bconns = q("SELECT * FROM bconns WHERE owner=?", m.from_user.id)
    is_connected = len(bconns) > 0

    text = (
        f"👋 أهلاً بك يا <b>{esc(m.from_user.full_name)}</b> في نظام الإدارة المتقدم.\n"
        f"<blockquote>أنا مساعدك الشخصي لتأمين وحماية حسابك وإدارة ردودك باحترافية تامة.</blockquote>"
    )

    b = InlineKeyboardBuilder()
    if BOT_CHANNEL: b.row(InlineKeyboardButton(text="🤖 قناة البوت ↗", url=BOT_CHANNEL))
    if DEV_CHANNEL: b.row(InlineKeyboardButton(text="💼 قناة المطور ↗", url=DEV_CHANNEL))
    if DEV_URL: b.row(InlineKeyboardButton(text=DEV_BUTTON_TEXT + " ↗", url=DEV_URL))
    
    b.row(InlineKeyboardButton(text="💱 أسعار العملات", callback_data="u:rates"), InlineKeyboardButton(text="👛 طرق الدفع", callback_data="u:pay"))
    b.row(InlineKeyboardButton(text="🏷 أكواد التحويل", callback_data="u:codes"), InlineKeyboardButton(text="🌐 السوشيال ميديا", callback_data="u:social"))
    
    if on("calc_on"): b.row(InlineKeyboardButton(text="🧮 الآلة الحاسبة", callback_data="u:calc"))
    
    if m.from_user.id == OWNER_ID and is_connected:
        b.row(InlineKeyboardButton(text="✏️ لوحة التحكم", callback_data="p:home"))
    elif m.from_user.id == OWNER_ID and not is_connected:
        b.row(InlineKeyboardButton(text="⚠️ ربط الحساب لفتح اللوحة", callback_data="connect_warning"))

    try:
        await bot.send_photo(chat_id=m.chat.id, photo=WELCOME_PHOTO_URL, caption=text, reply_markup=b.as_markup())
    except Exception:
        await m.answer(text, reply_markup=b.as_markup())

@router.callback_query(F.data == "connect_warning")
async def connect_warning(c: CallbackQuery):
    await c.answer("⚠️ يجب عليك ربط حسابك (المحادثة الآلية) أولاً لتتمكن من استخدام لوحة التحكم.", show_alert=True)

@router.callback_query(F.data == "check_sub")
async def check_sub(c: CallbackQuery, bot: Bot):
    if await subscribed(bot, c.from_user.id):
        await c.message.edit_text("✅ تم التحقق، أرسل /start")
    else:
        await c.answer("لسا ما اشتركت!", show_alert=True)

@router.callback_query(F.data.startswith("u:"))
async def user_cb(c: CallbackQuery, bot: Bot):
    if not await subscribed(bot, c.from_user.id):
        return await c.answer("اشترك بالقناة أولاً", show_alert=True)
    a = c.data[2:]
    if a == "calc":
        await c.message.answer("أرسل /calc ثم العملية، مثال:\n<code>/calc 25*4+10</code>")
    else:
        await c.message.answer(esc(get(a, "لم يتم ضبط هذا القسم بعد.")))
    await c.answer()

# ================== لوحة التحكم ==================
@router.callback_query(F.data == "p:home", F.from_user.id == OWNER_ID)
async def p_home(c: CallbackQuery):
    bconns = q("SELECT * FROM bconns WHERE owner=?", c.from_user.id)
    if not bconns:
        return await c.answer("⚠️ يجب ربط المحادثة الآلية أولاً.", show_alert=True)
    await show(c, panel_header(), panel_kb()); await c.answer()

@router.callback_query(F.data.startswith("m:"), F.from_user.id == OWNER_ID)
async def p_menu(c: CallbackQuery):
    name = c.data[2:]
    if name == "icons":
        b = InlineKeyboardBuilder()
        for k, v in {"btn_post": "✍️", "btn_welcome": "👋", "btn_reply": "🤖", "btn_bc": "📣", "btn_force": "📢", "btn_stats": "📊"}.items():
            icon = db.execute("SELECT emoji_id FROM button_icons WHERE key=?", (k,)).fetchone()
            status = "✅" if icon else "❌"
            b.row(InlineKeyboardButton(text=f"{status} {v} {k}", callback_data=f"ic_edit:{k}"))
        b.row(InlineKeyboardButton(text="🔙 رجوع", callback_data="p:home"))
        return await show(c, "<b>🎨 تخصيص أزرار اللوحة</b>\nاضغط على الزر لتغيير إيموجيه المميز.", b.as_markup())

    await show(c, f"<b>{MENUS[name][0]}</b>", menu_kb(name)); await c.answer()

@router.callback_query(F.data.startswith("t:"), F.from_user.id == OWNER_ID)
async def p_toggle(c: CallbackQuery, bot: Bot):
    _, key, back = c.data.split(":")
    put(key, "0" if on(key) else "1")
    if back == "home": await show(c, panel_header(), panel_kb())
    else: await c.message.edit_reply_markup(reply_markup=menu_kb(back))
    await c.answer()

PROMPTS = {
    "addrep": "أرسل الرد بهذا الشكل:\nالكلمة | الرد",
    "delrep": "أرسل الكلمة المراد حذف ردها:",
    "welcome": "أرسل نص رسالة الترحيب:",
    "welcomepic": "أرسل الصورة اللي تبيها في الترحيب:",
    "addban": "أرسل الكلمة الممنوعة:",
    "delban": "أرسل الكلمة المراد رفع منعها:",
    "mute": "أرسل آيدي المستخدم للكتم:",
    "unmute": "أرسل آيدي المستخدم لفك الكتم:",
    "force": "أرسل يوزر القناة مثل @channel:",
    "bc": "أرسل نص الإذاعة:",
    "tz": "أرسل فرق التوقيت عن UTC (مثال 3):",
    "bio": "أرسل نص البايو (استخدم {time} للوقت):",
    "set_rates": "أرسل نص أسعار العملات:",
    "set_pay": "أرسل نص طرق الدفع:",
    "set_codes": "أرسل نص أكواد التحويل:",
}

@router.callback_query(F.data.startswith("a:"), F.from_user.id == OWNER_ID)
async def p_action(c: CallbackQuery, state: FSMContext, bot: Bot):
    a = c.data[2:]
    if a in PROMPTS:
        await state.set_state(S.wait); await state.update_data(action=a)
        await c.message.answer(PROMPTS[a] + "\n(أرسل /cancel للإلغاء)")
    elif a == "stats":
        await c.message.answer(f"👥 المستخدمون: {q('SELECT COUNT(*) FROM users')[0][0]}\n🧾 العملاء: {q('SELECT COUNT(*) FROM customers')[0][0]}")
    elif a.startswith("show_"):
        await c.message.answer(esc(get(a[5:], "لم يُضبط بعد.")))
    await c.answer()

@router.callback_query(F.data.startswith("ic_edit:"), F.from_user.id == OWNER_ID)
async def icon_edit(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":")[1]
    await state.set_state(S.wait); await state.update_data(action="icon_edit", key=key)
    await c.message.answer(f"أرسل الإيموجي المميز الذي تريده لزر <code>{key}</code>:")

@router.message(StateFilter(S.wait), F.from_user.id == OWNER_ID, F.chat.type == "private")
async def got_input(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    a = data["action"]
    t = (m.text or "").strip()
    
    if a == "icon_edit":
        key = data["key"]
        if m.entities and m.entities[0].type == "custom_emoji":
            ex("INSERT OR REPLACE INTO button_icons VALUES(?,?)", key, m.entities[0].custom_emoji_id)
            await m.answer(f"✅ تم تعيين الإيموجي المميز للزر {key}.")
        else:
            await m.answer("⚠️ أرسل إيموجي مميز فقط.")
        await state.clear()
        return

    await state.clear()
    if a == "welcome": put("welcome", t); r = "✅ تم حفظ رسالة الترحيب."
    elif a == "addrep":
        if "|" not in t: return await m.answer("الصيغة: الكلمة | الرد")
        k, v = [x.strip() for x in t.split("|", 1)]
        ex("INSERT OR REPLACE INTO replies VALUES(?,?)", k.lower(), v); r = "✅ تمت إضافة الرد."
    elif a == "delrep": ex("DELETE FROM replies WHERE kw=?", t.lower()); r = "✅ تم الحذف."
    elif a == "addban": ex("INSERT OR IGNORE INTO banned VALUES(?)", t.lower()); r = "✅ تمت الإضافة."
    elif a == "delban": ex("DELETE FROM banned WHERE w=?", t.lower()); r = "✅ تم رفع المنع."
    elif a in ("mute", "unmute"):
        if not t.isdigit(): return await m.answer("أرسل آيدي رقمي.")
        if a == "mute": ex("INSERT OR IGNORE INTO muted VALUES(?)", int(t)); r = "🔇 تم الكتم."
        else: ex("DELETE FROM muted WHERE uid=?", int(t)); r = "🔊 تم فك الكتم."
    elif a == "force": put("force_channel", t); r = "✅ تم تعيين القناة."
    elif a == "tz":
        try: float(t); put("tz", t); r = "✅ تم."
        except: r = "رقم غير صحيح."
    elif a == "bio": put("bio_text", t[:70]); r = "✅ تم حفظ نص البايو."
    elif a.startswith("set_"): put(a[4:], t); r = "✅ تم الحفظ."
    elif a == "bc":
        ok = bad = 0
        for (uid,) in q("SELECT uid FROM users"):
            try:
                await bot.send_message(uid, esc(t)); ok += 1
            except: bad += 1
            await asyncio.sleep(0.05)
        for uid, chat, conn in q("SELECT uid, chat, conn FROM customers"):
            try:
                await bot.send_message(chat, esc(t), business_connection_id=conn); ok += 1
            except: bad += 1
            await asyncio.sleep(0.05)
        r = f"📣 تمت الإذاعة: نجح {ok} / فشل {bad}"
    else: r = "؟"
    await m.answer(r)

# ================== Telegram Business ==================
@router.business_connection()
async def on_conn(c: BusinessConnection, bot: Bot):
    if c.is_enabled:
        ex("INSERT OR REPLACE INTO bconns VALUES(?,?,?,?)", c.id, c.user.id, c.user.first_name, c.user.last_name or "")
        await bot.send_message(c.user.id, "✅ تم ربط حسابك بالبوت بنجاح. يمكنك الآن استخدام لوحة التحكم.")
    else:
        ex("DELETE FROM bconns WHERE id=?", c.id)
        await bot.send_message(c.user.id, "❌ تم فصل البوت عن حسابك.")

def conn_owner(cid):
    r = q("SELECT owner FROM bconns WHERE id=?", cid)
    return r[0][0] if r else None

def violates(m: Message, low: str):
    ents = (m.entities or []) + (m.caption_entities or [])
    kinds = {e.type for e in ents}
    if on("blk_link") and kinds & {"url", "text_link"}: return True
    if on("blk_mention") and kinds & {"mention", "text_mention"}: return True
    if on("blk_forward") and m.forward_origin: return True
    return any(w in low for (w,) in q("SELECT w FROM banned"))

@router.business_message()
async def on_biz(m: Message, bot: Bot):
    owner = conn_owner(m.business_connection_id)
    if owner is None or not m.from_user: return
    uid = m.from_user.id
    text = m.text or m.caption or ""
    ex("INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?,?)", m.chat.id, m.message_id, uid, m.from_user.full_name, m.from_user.username or "", text or f"[{m.content_type}]")
    if uid == owner or not on("bot_on"): return
    low = text.lower()
    if q("SELECT 1 FROM muted WHERE uid=?", uid) or violates(m, low):
        try: await bot.delete_business_messages(m.business_connection_id, [m.message_id])
        except: pass
        return
    if not q("SELECT 1 FROM customers WHERE uid=?", uid):
        ex("INSERT INTO customers VALUES(?,?,?)", uid, m.chat.id, m.business_connection_id)
        if on("welcome_on"):
            await m.answer("👋 أهلاً بك! كيف يمكنني مساعدتك اليوم؟")
    if on("auto_on") and text:
        for kw, resp in q("SELECT kw, resp FROM replies"):
            if kw in low:
                await m.answer(esc(resp)); break

@router.edited_business_message()
async def on_edit(m: Message, bot: Bot):
    owner = conn_owner(m.business_connection_id)
    if owner is None or not m.from_user or m.from_user.id == owner or not on("log_edit"): return
    new = m.text or m.caption or f"[{m.content_type}]"
    old = q("SELECT text FROM cache WHERE chat=? AND mid=?", m.chat.id, m.message_id)
    old = old[0][0] if old else "(غير معروف)"
    ex("INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?,?)", m.chat.id, m.message_id, m.from_user.id, m.from_user.full_name, m.from_user.username or "", new)
    await bot.send_message(owner, f"✏️ <b>تم تعديل رسالة</b>\n\n<blockquote>👤 {esc(m.from_user.full_name)}\n🆔 {m.from_user.id}\n\n💬 قبل التعديل:\n{esc(old)}\n\n💬 بعد التعديل:\n{esc(new)}</blockquote>")

@router.deleted_business_messages()
async def on_del(d: BusinessMessagesDeleted, bot: Bot):
    owner = conn_owner(d.business_connection_id)
    if owner is None or not on("log_del"): return
    for mid in d.message_ids:
        r = q("SELECT uid, name, uname, text FROM cache WHERE chat=? AND mid=?", d.chat.id, mid)
        if not r or r[0][0] == owner: continue
        uid, name, uname, text = r[0]
        await bot.send_message(owner, f"🗑 <b>تم حذف رسالة</b>\n\n<blockquote>👤 {esc(name)}\n🆔 {uid}\n\n💬 نص الرسالة:\n{esc(text)}</blockquote>")

# ================== الساعة الحية والبايو الحي ==================
async def live_loop(bot: Bot):
    while True:
        await asyncio.sleep(60)
        t = now_str()
        for cid, _o, fn, ln in q("SELECT * FROM bconns"):
            if on("clock_on"):
                try: await bot.set_business_account_name(cid, first_name=fn, last_name=(f"{ln} 🕐{t}").strip()[:64])
                except Exception as e: logging.warning("clock: %s", e)
            if on("bio_on"):
                try: await bot.set_business_account_bio(cid, bio=get("bio_text", "🕐 {time}").replace("{time}", t)[:70])
                except Exception as e: logging.warning("bio: %s", e)

# ================== التشغيل ==================
async def main():
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(live_loop(bot))
    print("✅ البوت يعمل الآن...")
    await dp.start_polling(
        bot, allowed_updates=["message", "callback_query", "business_connection",
                              "business_message", "edited_business_message",
                              "deleted_business_messages"]
    )

if __name__ == "__main__":
    asyncio.run(main())