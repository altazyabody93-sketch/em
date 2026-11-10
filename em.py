#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MATARI MAIL PRO
المطري لإدارة البريد الذكي
نظام إدارة بريد متعدد الحسابات عبر Telegram
"""

import os
import sys
import json
import sqlite3
import hashlib
import hmac
import base64
import re
import time
import select
import threading
import imaplib
import smtplib
import ssl
import email
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Optional, List, Dict, Any, Tuple

import telebot
from flask import Flask
import os
from telebot import types

# ============================================================
# Configuration Manager
# ============================================================


# ============================================================
# EMBEDDED CONFIGURATION - (MANDATORY FOR STANDALONE RUN)
# ============================================================
EMBEDDED_CONFIG = {
    'MATARI_BOT_TOKEN': '7798949296:AAHovsjr4H3Ny49leS6JSR58FJ9gDKL17bM',
    'MATARI_OWNER_ID': '7325566792',
    'MATARI_AUTHORIZED_USERS': '7325566792',
    'MATARI_ENCRYPTION_KEY': 'Ii4Y05dLzcH7osApFrn4WYMng2w0P_a8HqkGJxmFeoM=',
    'MATARI_DB_PATH': 'matari_mail.db',
    'MATARI_IMAP_TIMEOUT': '30',
    'MATARI_SMTP_TIMEOUT': '30',
}

def _bootstrap_env():
    for k, v in EMBEDDED_CONFIG.items():
        os.environ[k] = v

_bootstrap_env()
# ============================================================


def check_wa_new(phone: str) -> dict:
    import urllib.request, json
    clean_phone = ''.join(filter(str.isdigit, phone))
    if len(clean_phone) < 10: return {"ok": False, "msg": "❌ الرقم غير صالح"}
    try:
        req = urllib.request.Request("http://127.0.0.1:3000/check", 
                                     data=json.dumps({"phone": clean_phone}).encode(), 
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode())
            if data.get("ok") is True:
                if data.get("exists") is True:
                    return {"ok": True, "msg": "✅ الرقم مسجل في واتساب"}
                else:
                    return {"ok": False, "msg": "❌ الرقم غير مسجل في واتساب"}
    except Exception as e:
        return {"ok": False, "msg": "⚠️ جسر الواتساب لم يتصل بعد، انتظر قليلاً."}
    return {"ok": False, "msg": "⚠️ جسر الواتساب لم يتصل بعد، انتظر قليلاً."}

class Config:
    """مدير التكوين - يقرأ من متغيرات البيئة"""
    
    def __init__(self):
        self.bot_token = os.environ.get('MATARI_BOT_TOKEN', '')
        self.owner_id = int(os.environ.get('MATARI_OWNER_ID', '0'))
        self.authorized_users = self._parse_authorized_users()
        self.db_path = os.environ.get('MATARI_DB_PATH', 'matari_mail.db')
        self.encryption_key = os.environ.get('MATARI_ENCRYPTION_KEY', '')
        self.imap_timeout = int(os.environ.get('MATARI_IMAP_TIMEOUT', '30'))
        self.smtp_timeout = int(os.environ.get('MATARI_SMTP_TIMEOUT', '30'))
        self.max_attachment_mb = int(os.environ.get('MATARI_MAX_ATTACHMENT_MB', '10'))
        self.poll_interval = int(os.environ.get('MATARI_POLL_INTERVAL', '30'))
        
        if not self.bot_token:
            print("خطأ: MATARI_BOT_TOKEN غير محدد")
            sys.exit(1)
        if not self.encryption_key:
            print("خطأ: MATARI_ENCRYPTION_KEY غير محدد")
            sys.exit(1)
        # التحقق من طول المفتاح (Fernet يحتاج 32 بايت base64)
        try:
            key_bytes = base64.urlsafe_b64decode(self.encryption_key)
            if len(key_bytes) != 32:
                raise ValueError("طول المفتاح غير صحيح")
        except Exception as e:
            print(f"خطأ: MATARI_ENCRYPTION_KEY غير صالح: {e}")
            sys.exit(1)
    
    def _parse_authorized_users(self) -> List[int]:
        raw = os.environ.get('MATARI_AUTHORIZED_USERS', '')
        if not raw:
            return []
        return [int(x.strip()) for x in raw.split(',') if x.strip().isdigit()]


# ============================================================
# Provider Auto-Detect
# ============================================================

PROVIDER_CONFIG = {
    'gmail.com':    {'imap': ('imap.gmail.com', 993),      'smtp': ('smtp.gmail.com', 465),      'name': 'Gmail'},
    'googlemail.com': {'imap': ('imap.gmail.com', 993),    'smtp': ('smtp.gmail.com', 465),      'name': 'Gmail'},
    'outlook.com':  {'imap': ('outlook.office365.com', 993),'smtp': ('smtp.office365.com', 587), 'name': 'Outlook'},
    'hotmail.com':  {'imap': ('outlook.office365.com', 993),'smtp': ('smtp.office365.com', 587), 'name': 'Outlook'},
    'live.com':     {'imap': ('outlook.office365.com', 993),'smtp': ('smtp.office365.com', 587), 'name': 'Outlook'},
    'yahoo.com':    {'imap': ('imap.mail.yahoo.com', 993), 'smtp': ('smtp.mail.yahoo.com', 465), 'name': 'Yahoo'},
    'icloud.com':   {'imap': ('imap.mail.me.com', 993),    'smtp': ('smtp.mail.me.com', 587),    'name': 'iCloud'},
    'me.com':       {'imap': ('imap.mail.me.com', 993),    'smtp': ('smtp.mail.me.com', 587),    'name': 'iCloud'},
    'zoho.com':     {'imap': ('imap.zoho.com', 993),       'smtp': ('smtp.zoho.com', 465),       'name': 'Zoho'},
    'yandex.com':   {'imap': ('imap.yandex.com', 993),     'smtp': ('smtp.yandex.com', 465),     'name': 'Yandex'},
    'protonmail.com':{'imap': ('127.0.0.1', 1143),         'smtp': ('127.0.0.1', 1025),          'name': 'ProtonMail (يحتاج Bridge)'},
    'gmx.com':      {'imap': ('imap.gmx.com', 993),        'smtp': ('mail.gmx.com', 587),        'name': 'GMX'},
    'aol.com':      {'imap': ('imap.aol.com', 993),        'smtp': ('smtp.aol.com', 465),        'name': 'AOL'},
}

def detect_provider(email_addr: str) -> dict:
    """يكتشف إعدادات المزود من نطاق البريد"""
    try:
        domain = email_addr.split('@')[1].lower().strip()
    except Exception:
        return None
    return PROVIDER_CONFIG.get(domain)

# ============================================================
# Security Manager
# ============================================================

class SecurityManager:
    """إدارة التشفير والتحقق من المدخلات"""
    
    def __init__(self, key_b64: str):
        self.key = key_b64.encode() if isinstance(key_b64, str) else key_b64
    
    def _xor_encrypt(self, data: bytes) -> bytes:
        """تشفير XOR بسيط مع HMAC للتحقق من السلامة"""
        key_bytes = base64.urlsafe_b64decode(self.key)
        result = bytearray()
        for i, b in enumerate(data):
            result.append(b ^ key_bytes[i % len(key_bytes)])
        return bytes(result)
    
    def encrypt(self, plaintext: str) -> str:
        """تشفير نص مع HMAC"""
        if not plaintext:
            return ''
        data = plaintext.encode('utf-8')
        encrypted = self._xor_encrypt(data)
        # HMAC للتحقق من السلامة
        mac = hmac.new(base64.urlsafe_b64decode(self.key), encrypted, hashlib.sha256).digest()[:16]
        combined = mac + encrypted
        return base64.urlsafe_b64encode(combined).decode('ascii')
    
    def decrypt(self, ciphertext: str) -> str:
        """فك تشفير نص مع التحقق"""
        if not ciphertext:
            return ''
        try:
            combined = base64.urlsafe_b64decode(ciphertext.encode('ascii'))
            mac = combined[:16]
            encrypted = combined[16:]
            expected_mac = hmac.new(base64.urlsafe_b64decode(self.key), encrypted, hashlib.sha256).digest()[:16]
            if not hmac.compare_digest(mac, expected_mac):
                raise ValueError("فشل التحقق من سلامة البيانات")
            decrypted = self._xor_encrypt(encrypted)
            return decrypted.decode('utf-8')
        except Exception as e:
            raise ValueError(f"فشل فك التشفير: {e}")
    
    @staticmethod
    def sanitize_html(text: str) -> str:
        """تنظيف HTML من الوسوم الخطرة"""
        if not text:
            return ''
        # إزالة script, iframe, object, embed
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<iframe[^>]*>.*?</iframe>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<object[^>]*>.*?</object>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<embed[^>]*>', '', text, flags=re.IGNORECASE)
        return text
    
    @staticmethod
    def escape_html(text: str) -> str:
        """تنظيف النص من رموز HTML قبل إرساله لتيليجرام"""
        if not text:
            return ""
        # استبدال الرموز التي تكسر تنسيق تيليجرام
        text = str(text).replace('&', '&amp;')
        text = text.replace('<', '&lt;')
        text = text.replace('>', '&gt;')
        return text

    @staticmethod
    def validate_email(email_addr: str) -> bool:
        """التحقق من صحة صيغة البريد"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email_addr.strip()))


class HTMLTextExtractor(HTMLParser):
    """استخراج النص من HTML بأمان"""
    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text = []
        self.skip_tags = {'script', 'style', 'iframe', 'object', 'embed'}
        self.current_skip = None
    
    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.skip_tags:
            self.current_skip = tag.lower()
    
    def handle_endtag(self, tag):
        if self.current_skip and tag.lower() == self.current_skip:
            self.current_skip = None
    
    def handle_data(self, data):
        if not self.current_skip and data.strip():
            self.text.append(data.strip())
    
    def get_text(self) -> str:
        return ' '.join(self.text)


def extract_text_from_html(html: str) -> str:
    """استخراج النص من HTML"""
    try:
        extractor = HTMLTextExtractor()
        extractor.feed(html)
        return extractor.get_text()
    except:
        return re.sub(r'<[^>]+>', ' ', html)


# ============================================================
# Database Manager
# ============================================================

class DatabaseManager:
    """مدير قاعدة البيانات SQLite"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        self.init_db()
    
    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, timeout=30)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn
    
    def close(self):
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
    
    def init_db(self):
        """إنشاء الجداول تلقائياً"""
        conn = self._get_conn()
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_active TEXT
            );
            
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                email TEXT NOT NULL,
                display_name TEXT,
                imap_host TEXT NOT NULL,
                imap_port INTEGER DEFAULT 993,
                smtp_host TEXT NOT NULL,
                smtp_port INTEGER DEFAULT 465,
                password_enc TEXT NOT NULL,
                is_default INTEGER DEFAULT 0,
                notifications_on INTEGER DEFAULT 1,
                last_checked TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );
            
            CREATE TABLE IF NOT EXISTS allowed_users (
                telegram_id INTEGER PRIMARY KEY,
                role TEXT DEFAULT 'user',
                added_by INTEGER,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                description TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );
            
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                uid TEXT NOT NULL,
                sender TEXT,
                sender_email TEXT,
                subject TEXT,
                body_preview TEXT,
                body_full TEXT,
                received_date TEXT,
                is_read INTEGER DEFAULT 0,
                is_archived INTEGER DEFAULT 0,
                is_deleted INTEGER DEFAULT 0,
                has_attachments INTEGER DEFAULT 0,
                attachments_info TEXT,
                folder TEXT DEFAULT 'INBOX',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, uid),
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );
            
            CREATE TABLE IF NOT EXISTS sent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT,
                body TEXT,
                sent_date TEXT,
                status TEXT,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );
            
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                account_id INTEGER,
                recipient TEXT,
                subject TEXT,
                body TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );
            
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );
            
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER PRIMARY KEY,
                default_account_id INTEGER,
                notifications_enabled INTEGER DEFAULT 1,
                poll_interval INTEGER DEFAULT 60,
                privacy_mode INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );
            
            CREATE TABLE IF NOT EXISTS blocked_senders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                email TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );
            
            CREATE TABLE IF NOT EXISTS notification_settings (
                account_id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 1,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );
        ''')
        conn.commit()
    
    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        conn = self._get_conn()
        cur = conn.execute(query, params)
        conn.commit()
        return cur
    
    def fetchone(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.execute(query, params)
        return cur.fetchone()
    
    def fetchall(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        conn = self._get_conn()
        cur = conn.execute(query, params)
        return cur.fetchall()
    
    # ---------- Users ----------
    def ensure_user(self, telegram_id: int, username: str = '', first_name: str = ''):
        self.execute('''
            INSERT INTO users (telegram_id, username, first_name, last_active)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = COALESCE(NULLIF(excluded.username, ''), users.username),
                first_name = COALESCE(NULLIF(excluded.first_name, ''), users.first_name),
                last_active = CURRENT_TIMESTAMP
        ''', (telegram_id, username, first_name))
        # إنشاء إعدادات افتراضية
        self.execute('''
            INSERT OR IGNORE INTO settings (user_id) VALUES (?)
        ''', (telegram_id,))
    
    # ---------- Accounts ----------
    def add_account(self, user_id: int, email_addr: str, display_name: str,
                    imap_host: str, imap_port: int, smtp_host: str, smtp_port: int,
                    password_enc: str) -> int:
        # إذا كان أول حساب، اجعله افتراضي
        existing = self.fetchone('SELECT COUNT(*) as c FROM accounts WHERE user_id = ?', (user_id,))
        is_default = 1 if existing['c'] == 0 else 0
        cur = self.execute('''
            INSERT INTO accounts (user_id, email, display_name, imap_host, imap_port,
                                  smtp_host, smtp_port, password_enc, is_default)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, email_addr, display_name, imap_host, imap_port,
              smtp_host, smtp_port, password_enc, is_default))
        account_id = cur.lastrowid
        self.execute('''
            INSERT OR IGNORE INTO notification_settings (account_id, enabled) VALUES (?, 1)
        ''', (account_id,))
        return account_id
    
    def get_accounts(self, user_id: int) -> List[sqlite3.Row]:
        return self.fetchall('SELECT * FROM accounts WHERE user_id = ? ORDER BY is_default DESC, created_at DESC', (user_id,))
    
    def get_account(self, account_id: int) -> Optional[sqlite3.Row]:
        return self.fetchone('SELECT * FROM accounts WHERE id = ?', (account_id,))
    
    def get_default_account(self, user_id: int) -> Optional[sqlite3.Row]:
        row = self.fetchone('SELECT * FROM accounts WHERE user_id = ? AND is_default = 1', (user_id,))
        if not row:
            row = self.fetchone('SELECT * FROM accounts WHERE user_id = ? ORDER BY created_at ASC LIMIT 1', (user_id,))
        return row
    
    def set_default_account(self, user_id: int, account_id: int):
        self.execute('UPDATE accounts SET is_default = 0 WHERE user_id = ?', (user_id,))
        self.execute('UPDATE accounts SET is_default = 1 WHERE id = ? AND user_id = ?', (account_id, user_id))
    
    def delete_account(self, account_id: int, user_id: int):
        self.execute('DELETE FROM notification_settings WHERE account_id = ?', (account_id,))
        self.execute('DELETE FROM messages WHERE account_id = ?', (account_id,))
        self.execute('DELETE FROM sent_messages WHERE account_id = ?', (account_id,))
        self.execute('DELETE FROM accounts WHERE id = ? AND user_id = ?', (account_id, user_id))
        # إعادة تعيين الحساب الافتراضي
        remaining = self.get_accounts(user_id)
        if remaining and not any(a['is_default'] for a in remaining):
            self.set_default_account(user_id, remaining[0]['id'])
    
    # ---------- Messages ----------
    def save_message(self, account_id: int, uid: str, sender: str, sender_email: str,
                     subject: str, body_preview: str, body_full: str, received_date: str,
                     has_attachments: int, attachments_info: str) -> bool:
        """حفظ رسالة، يعيد True إذا كانت جديدة"""
        existing = self.fetchone(
            'SELECT id FROM messages WHERE account_id = ? AND uid = ?',
            (account_id, uid)
        )
        if existing:
            return False
        self.execute('''
            INSERT OR IGNORE INTO messages
            (account_id, uid, sender, sender_email, subject, body_preview, body_full,
             received_date, has_attachments, attachments_info)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (account_id, uid, sender, sender_email, subject, body_preview, body_full,
              received_date, has_attachments, attachments_info))
        return True
    
    def get_messages(self, account_id: int, folder: str = 'INBOX', page: int = 0, per_page: int = 5,
                     search: str = '') -> Tuple[List[sqlite3.Row], int]:
        where = 'account_id = ? AND is_deleted = 0'
        params = [account_id]
        
        if folder == 'INBOX':
            where += ' AND is_archived = 0'
        elif folder == 'ARCHIVE':
            where += ' AND is_archived = 1'
        elif folder == 'UNREAD':
            where += ' AND is_read = 0 AND is_archived = 0'
        
        if search:
            where += ' AND (subject LIKE ? OR sender LIKE ? OR sender_email LIKE ? OR body_preview LIKE ?)'
            like = f'%{search}%'
            params.extend([like, like, like, like])
        
        count_row = self.fetchone(f'SELECT COUNT(*) as c FROM messages WHERE {where}', tuple(params))
        total = count_row['c'] if count_row else 0
        
        params.extend([per_page, page * per_page])
        rows = self.fetchall(f'''
            SELECT * FROM messages WHERE {where}
            ORDER BY is_read ASC, received_date DESC LIMIT ? OFFSET ?
        ''', tuple(params))
        return rows, total
    
    def get_message(self, message_id: int) -> Optional[sqlite3.Row]:
        return self.fetchone('SELECT * FROM messages WHERE id = ?', (message_id,))
    
    def mark_read(self, message_id: int):
        self.execute('UPDATE messages SET is_read = 1 WHERE id = ?', (message_id,))
    
    def archive_message(self, message_id: int):
        self.execute('UPDATE messages SET is_archived = 1 WHERE id = ?', (message_id,))
    
    def delete_message(self, message_id: int):
        self.execute('UPDATE messages SET is_deleted = 1 WHERE id = ?', (message_id,))
    
    def count_unread(self, account_id: int = None, user_id: int = None) -> int:
        if account_id:
            row = self.fetchone('SELECT COUNT(*) as c FROM messages WHERE account_id = ? AND is_read = 0 AND is_deleted = 0', (account_id,))
        elif user_id:
            row = self.fetchone('''
                SELECT COUNT(*) as c FROM messages m
                JOIN accounts a ON m.account_id = a.id
                WHERE a.user_id = ? AND m.is_read = 0 AND m.is_deleted = 0
            ''', (user_id,))
        else:
            return 0
        return row['c'] if row else 0
    
    # ---------- Sent ----------
    def save_sent(self, account_id: int, recipient: str, subject: str, body: str, status: str):
        self.execute('''
            INSERT INTO sent_messages (account_id, recipient, subject, body, sent_date, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (account_id, recipient, subject, body, datetime.now().isoformat(), status))
    
    def get_sent(self, account_id: int, page: int = 0, per_page: int = 5) -> Tuple[List[sqlite3.Row], int]:
        count_row = self.fetchone('SELECT COUNT(*) as c FROM sent_messages WHERE account_id = ?', (account_id,))
        total = count_row['c'] if count_row else 0
        rows = self.fetchall('''
            SELECT * FROM sent_messages WHERE account_id = ?
            ORDER BY sent_date DESC LIMIT ? OFFSET ?
        ''', (account_id, per_page, page * per_page))
        return rows, total
    
    # ---------- Drafts ----------
    def save_draft(self, user_id: int, recipient: str, subject: str, body: str, draft_id: int = None, account_id: int = None) -> int:
        if draft_id:
            self.execute('''
                UPDATE drafts SET recipient = ?, subject = ?, body = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
            ''', (recipient, subject, body, draft_id, user_id))
            return draft_id
        cur = self.execute('''
            INSERT INTO drafts (user_id, account_id, recipient, subject, body)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, account_id, recipient, subject, body))
        return cur.lastrowid
    
    def get_drafts(self, user_id: int) -> List[sqlite3.Row]:
        return self.fetchall('SELECT * FROM drafts WHERE user_id = ? ORDER BY updated_at DESC', (user_id,))
    
    def get_draft(self, draft_id: int, user_id: int) -> Optional[sqlite3.Row]:
        return self.fetchone('SELECT * FROM drafts WHERE id = ? AND user_id = ?', (draft_id, user_id))
    
    def delete_draft(self, draft_id: int, user_id: int):
        self.execute('DELETE FROM drafts WHERE id = ? AND user_id = ?', (draft_id, user_id))
    
    # ---------- Contacts ----------
    def add_contact(self, user_id: int, name: str, email_addr: str, description: str = '') -> int:
        cur = self.execute('''
            INSERT INTO contacts (user_id, name, email, description) VALUES (?, ?, ?, ?)
        ''', (user_id, name, email_addr, description))
        return cur.lastrowid
    
    def get_contacts(self, user_id: int, search: str = '') -> List[sqlite3.Row]:
        if search:
            like = f'%{search}%'
            return self.fetchall('''
                SELECT * FROM contacts WHERE user_id = ? AND (name LIKE ? OR email LIKE ?)
                ORDER BY name ASC
            ''', (user_id, like, like))
        return self.fetchall('SELECT * FROM contacts WHERE user_id = ? ORDER BY name ASC', (user_id,))
    
    def delete_contact(self, contact_id: int, user_id: int):
        self.execute('DELETE FROM contacts WHERE id = ? AND user_id = ?', (contact_id, user_id))
    
    # ---------- History ----------
    def log_action(self, user_id: int, action: str, details: str = ''):
        self.execute('''
            INSERT INTO history (user_id, action, details) VALUES (?, ?, ?)
        ''', (user_id, action, details))
    
    def get_history(self, user_id: int, page: int = 0, per_page: int = 10) -> Tuple[List[sqlite3.Row], int]:
        count_row = self.fetchone('SELECT COUNT(*) as c FROM history WHERE user_id = ?', (user_id,))
        total = count_row['c'] if count_row else 0
        rows = self.fetchall('''
            SELECT * FROM history WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ? OFFSET ?
        ''', (user_id, per_page, page * per_page))
        return rows, total
    
    # ---------- Settings ----------
    def get_settings(self, user_id: int) -> Optional[sqlite3.Row]:
        return self.fetchone('SELECT * FROM settings WHERE user_id = ?', (user_id,))
    
    def update_settings(self, user_id: int, **kwargs):
        allowed = ['notifications_enabled', 'poll_interval', 'privacy_mode']
        sets = []
        params = []
        for key, val in kwargs.items():
            if key in allowed:
                sets.append(f'{key} = ?')
                params.append(val)
        if sets:
            params.append(user_id)
            self.execute(f'UPDATE settings SET {", ".join(sets)} WHERE user_id = ?', tuple(params))
    
    def get_notification_accounts(self) -> List[sqlite3.Row]:
        return self.fetchall('''
            SELECT a.* FROM accounts a
            JOIN notification_settings ns ON a.id = ns.account_id
            WHERE ns.enabled = 1
        ''')

# ============================================================
# Email IMAP Manager
# ============================================================

class IMAPManager:
    """إدارة اتصالات IMAP"""
    
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
    
    def connect(self, host: str, port: int, email_addr: str, password: str) -> imaplib.IMAP4_SSL:
        """إنشاء اتصال IMAP آمن"""
        context = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=self.timeout)
        conn.login(email_addr, password)
        return conn
    
    def test_connection(self, host: str, port: int, email_addr: str, password: str) -> Tuple[bool, str]:
        try:
            conn = self.connect(host, port, email_addr, password)
            conn.logout()
            return True, "اتصال IMAP ناجح"
        except Exception as e:
            return False, f"فشل اتصال IMAP: {str(e)}"
    
    def fetch_messages(self, conn: imaplib.IMAP4_SSL, folder: str = 'INBOX',
                       limit: int = 20) -> List[Dict[str, Any]]:
        """جلب أحدث الرسائل من المجلد"""
        try:
            status, _ = conn.select(folder, readonly=True)
            if status != 'OK':
                return []
            
            status, data = conn.search(None, 'ALL')
            if status != 'OK':
                return []
            
            uids = data[0].split()
            if not uids:
                return []
            
            # أحدث الرسائل أولاً
            uids = uids[-limit:]
            uids.reverse()
            
            messages = []
            for uid in uids:
                try:
                    msg = self._fetch_single(conn, uid)
                    if msg:
                        messages.append(msg)
                except Exception:
                    continue
            return messages
        except Exception:
            return []
    
    def _fetch_single(self, conn: imaplib.IMAP4_SSL, uid: bytes) -> Optional[Dict[str, Any]]:
        """جلب رسالة واحدة"""
        status, data = conn.fetch(uid, '(RFC822)')
        if status != 'OK' or not data or not data[0]:
            return None
        
        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)
        
        # فك ترميز الموضوع
        subject = self._decode_header(msg.get('Subject', ''))
        sender = self._decode_header(msg.get('From', ''))
        
        # استخراج البريد الإلكتروني للمرسل
        sender_email = ''
        if '<' in sender and '>' in sender:
            sender_email = sender.split('<')[1].split('>')[0].strip()
            sender_name = sender.split('<')[0].strip().strip('"')
        else:
            sender_email = sender.strip()
            sender_name = sender_email
        
        # تاريخ الاستلام
        date_str = msg.get('Date', '')
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_str)
            received_date = dt.isoformat()
        except:
            received_date = datetime.now().isoformat()
        
        # استخراج المحتوى
        body_text = ''
        body_html = ''
        attachments = []
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition', ''))
                
                if 'attachment' in content_disposition:
                    filename = part.get_filename()
                    if filename:
                        filename = self._decode_header(filename)
                        size = len(part.get_payload(decode=True) or b'')
                        attachments.append({'filename': filename, 'size': size})
                    continue
                
                try:
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue
                    charset = part.get_content_charset() or 'utf-8'
                    decoded = payload.decode(charset, errors='replace')
                    
                    if content_type == 'text/plain' and not body_text:
                        body_text = decoded
                    elif content_type == 'text/html' and not body_html:
                        body_html = decoded
                except:
                    continue
        else:
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or 'utf-8'
                    body_text = payload.decode(charset, errors='replace')
            except:
                pass
        
        # استخدام النص أو استخراج من HTML
        if body_text:
            body_full = body_text
        elif body_html:
            body_full = extract_text_from_html(body_html)
        else:
            body_full = '(لا يوجد محتوى نصي)'
        
        preview = body_full[:200].replace('\n', ' ').replace('\r', '')
        
        return {
            'uid': uid.decode('ascii') if isinstance(uid, bytes) else str(uid),
            'sender': sender_name,
            'sender_email': sender_email,
            'subject': subject or '(بدون موضوع)',
            'body_preview': preview,
            'body_full': body_full,
            'received_date': received_date,
            'has_attachments': 1 if attachments else 0,
            'attachments_info': json.dumps(attachments, ensure_ascii=False) if attachments else ''
        }
    
    def _decode_header(self, header: str) -> str:
        """فك ترميز الترويسات"""
        if not header:
            return ''
        try:
            parts = decode_header(header)
            result = []
            for part, charset in parts:
                if isinstance(part, bytes):
                    result.append(part.decode(charset or 'utf-8', errors='replace'))
                else:
                    result.append(str(part))
            return ' '.join(result)
        except:
            return header


# ============================================================
# Email SMTP Manager
# ============================================================

class SMTPManager:
    """إدارة إرسال البريد"""
    
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
    
    def send(self, host: str, port: int, email_addr: str, password: str,
             to: str, subject: str, body: str, is_html: bool = False,
             reply_to: str = None, in_reply_to: str = None,
             references: str = None) -> Tuple[bool, str]:
        """إرسال بريد إلكتروني"""
        try:
            msg = MIMEMultipart('alternative') if is_html else MIMEText(body, 'plain', 'utf-8')
            
            if not is_html:
                msg = MIMEText(body, 'plain', 'utf-8')
            else:
                msg = MIMEMultipart('alternative')
                msg.attach(MIMEText(extract_text_from_html(body), 'plain', 'utf-8'))
                msg.attach(MIMEText(body, 'html', 'utf-8'))
            
            msg['From'] = email_addr
            msg['To'] = to
            msg['Subject'] = subject
            msg['Date'] = email.utils.formatdate(localtime=True)
            msg['Message-ID'] = email.utils.make_msgid()
            
            if reply_to:
                msg['Reply-To'] = reply_to
            if in_reply_to:
                msg['In-Reply-To'] = in_reply_to
            if references:
                msg['References'] = references
            
            # الاتصال والإرسال
            context = ssl.create_default_context()
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=self.timeout, context=context)
            else:
                server = smtplib.SMTP(host, port, timeout=self.timeout)
                server.starttls(context=context)
            
            server.login(email_addr, password)
            server.send_message(msg)
            server.quit()
            
            return True, "تم قبول الرسالة من خادم البريد"
        except smtplib.SMTPAuthenticationError:
            return False, "فشل المصادقة - تحقق من كلمة مرور التطبيق"
        except smtplib.SMTPException as e:
            return False, f"خطأ SMTP: {str(e)}"
        except Exception as e:
            return False, f"فشل الإرسال: {str(e)}"
    
    def test_connection(self, host: str, port: int, email_addr: str, password: str) -> Tuple[bool, str]:
        try:
            context = ssl.create_default_context()
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=self.timeout, context=context)
            else:
                server = smtplib.SMTP(host, port, timeout=self.timeout)
                server.starttls(context=context)
            server.login(email_addr, password)
            server.quit()
            return True, "اتصال SMTP ناجح"
        except Exception as e:
            return False, f"فشل اتصال SMTP: {str(e)}"

# ============================================================
# Account Manager
# ============================================================

class AccountManager:
    def __init__(self, db: DatabaseManager, sec: SecurityManager, imap: IMAPManager, smtp: SMTPManager):
        self.db = db
        self.sec = sec
        self.imap = imap
        self.smtp = smtp
    
    def add_gmail_account(self, user_id: int, email_addr: str, app_password: str) -> Tuple[bool, str]:
        if not self.sec.validate_email(email_addr):
            return False, "صيغة البريد غير صحيحة"
        
        # اختبار الاتصال
        ok_imap, msg_imap = self.imap.test_connection('imap.gmail.com', 993, email_addr, app_password)
        if not ok_imap:
            return False, msg_imap
        
        ok_smtp, msg_smtp = self.smtp.test_connection('smtp.gmail.com', 465, email_addr, app_password)
        if not ok_smtp:
            return False, msg_smtp
        
        enc_pass = self.sec.encrypt(app_password)
        self.db.add_account(user_id, email_addr, email_addr.split('@')[0],
                           'imap.gmail.com', 993, 'smtp.gmail.com', 465, enc_pass)
        self.db.log_action(user_id, 'إضافة حساب', email_addr)
        return True, f"تم إضافة الحساب {email_addr} بنجاح"
    
    def add_custom_account(self, user_id: int, email_addr: str, display_name: str,
                           imap_host: str, imap_port: int, smtp_host: str, smtp_port: int,
                           password: str) -> Tuple[bool, str]:
        if not self.sec.validate_email(email_addr):
            return False, "صيغة البريد غير صحيحة"
        
        ok_imap, msg_imap = self.imap.test_connection(imap_host, imap_port, email_addr, password)
        if not ok_imap:
            return False, msg_imap
        
        ok_smtp, msg_smtp = self.smtp.test_connection(smtp_host, smtp_port, email_addr, password)
        if not ok_smtp:
            return False, msg_smtp
        
        enc_pass = self.sec.encrypt(password)
        self.db.add_account(user_id, email_addr, display_name or email_addr.split('@')[0],
                           imap_host, imap_port, smtp_host, smtp_port, enc_pass)
        self.db.log_action(user_id, 'إضافة حساب', email_addr)
        return True, f"تم إضافة الحساب {email_addr} بنجاح"
    
    def get_account_credentials(self, account_id: int) -> Optional[Dict[str, str]]:
        acc = self.db.get_account(account_id)
        if not acc:
            return None
        try:
            password = self.sec.decrypt(acc['password_enc'])
        except:
            return None
        return {
            'email': acc['email'],
            'password': password,
            'imap_host': acc['imap_host'],
            'imap_port': acc['imap_port'],
            'smtp_host': acc['smtp_host'],
            'smtp_port': acc['smtp_port']
        }


# ============================================================
# Message Manager
# ============================================================

class MessageManager:
    def __init__(self, db: DatabaseManager, imap: IMAPManager, smtp: SMTPManager, sec: SecurityManager):
        self.db = db
        self.imap = imap
        self.smtp = smtp
        self.sec = sec
    
    def sync_account(self, account_id: int) -> Tuple[int, str]:
        """مزامنة حساب - جلب رسائل جديدة"""
        acc = self.db.get_account(account_id)
        if not acc:
            return 0, "الحساب غير موجود"
        
        try:
            password = self.sec.decrypt(acc['password_enc'])
        except:
            return 0, "فشل فك تشفير كلمة المرور"
        
        try:
            conn = self.imap.connect(acc['imap_host'], acc['imap_port'], acc['email'], password)
        except Exception as e:
            return 0, f"فشل الاتصال: {str(e)}"
        
        new_count = 0
        try:
            messages = self.imap.fetch_messages(conn, 'INBOX', limit=30)
            for msg in messages:
                is_new = self.db.save_message(
                    account_id, msg['uid'], msg['sender'], msg['sender_email'],
                    msg['subject'], msg['body_preview'], msg['body_full'],
                    msg['received_date'], msg['has_attachments'], msg['attachments_info']
                )
                if is_new:
                    new_count += 1
        finally:
            try:
                conn.logout()
            except:
                pass
        
        self.db.execute('UPDATE accounts SET last_checked = ? WHERE id = ?',
                       (datetime.now().isoformat(), account_id))
        
        return new_count, f"تم جلب {new_count} رسالة جديدة"
    
    def send_email(self, account_id: int, to: str, subject: str, body: str,
                   reply_to: str = None, in_reply_to: str = None) -> Tuple[bool, str, Dict]:
        """إرسال بريد"""
        acc = self.db.get_account(account_id)
        if not acc:
            return False, "الحساب غير موجود", {}
        
        try:
            password = self.sec.decrypt(acc['password_enc'])
        except:
            return False, "فشل فك تشفير كلمة المرور", {}
        
        ok, msg = self.smtp.send(
            acc['smtp_host'], acc['smtp_port'], acc['email'], password,
            to, subject, body, reply_to=reply_to, in_reply_to=in_reply_to
        )
        
        if ok:
            self.db.save_sent(account_id, to, subject, body, 'accepted')
            info = {
                'recipient': to,
                'subject': subject,
                'account': acc['email'],
                'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'status': 'قبل خادم البريد الرسالة'
            }
            return True, msg, info
        return False, msg, {}


# ============================================================
# Notification Manager
# ============================================================

class NotificationManager:
    def __init__(self, db: DatabaseManager, bot: telebot.TeleBot, imap: IMAPManager, sec: SecurityManager):
        self.db = db
        self.bot = bot
        self.imap = imap
        self.sec = sec
        self._running = False
        self._thread = None
        self._notified_uids = set()  # لمنع التكرار
        self._lock = threading.Lock()
    
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop_idle, daemon=True)
        self._thread.start()
    
    def stop(self):
        self._running = False
    
    def _loop_idle(self):
        """محرك IDLE الفوري لكل حساب - يعمل في الخلفية"""
        while self._running:
            accounts = self.db.get_notification_accounts()
            for acc in accounts:
                if not self._running: break
                threading.Thread(target=self._idle_worker, args=(acc,), daemon=True).start()
            
            # إعادة الفحص كل 5 دقائق للتأكد من الحسابات الجديدة
            for _ in range(300):
                if not self._running: break
                time.sleep(1)
    
    def _idle_worker(self, acc):
        """مراقب IDLE لحساب واحد"""
        while self._running:
            try:
                password = self.sec.decrypt(acc['password_enc'])
                context = ssl.create_default_context()
                conn = imaplib.IMAP4_SSL(acc['imap_host'], acc['imap_port'], ssl_context=context)
                conn.login(acc['email'], password)
                conn.select('INBOX')
                
                # تفعيل IDLE
                tag = conn._new_tag()
                conn.send(tag + b' IDLE\r\n')
                response = conn.readline()
                
                if b'+ idling' in response.lower():
                    # انتظار الإشعار
                    while self._running:
                        r, _, _ = select.select([conn.sock], [], [], 29)
                        if r:
                            line = conn.readline()
                            if b'EXISTS' in line:
                                conn.send(b'DONE\r\n')
                                conn.readline()
                                # جلب الرسائل الجديدة فوراً
                                self._check_account(acc, conn)
                                break
                        else:
                            # تجديد IDLE قبل انتهاء المهلة
                            conn.send(b'DONE\r\n')
                            conn.readline()
                            tag = conn._new_tag()
                            conn.send(tag + b' IDLE\r\n')
                            conn.readline()
            except Exception:
                pass
            finally:
                try: conn.logout()
                except: pass
            time.sleep(10)
    
    def _check_all(self):
        accounts = self.db.get_notification_accounts()
        for acc in accounts:
            if not self._running:
                break
            try:
                self._check_account(acc)
            except Exception:
                continue
    
    def _check_account(self, acc, existing_conn=None):
        # التحقق من إعدادات المستخدم
        settings = self.db.get_settings(acc['user_id'])
        if settings and not settings['notifications_enabled']:
            return
        
        # التحقق من إعدادات الإشعارات للحساب
        ns = self.db.fetchone('SELECT enabled FROM notification_settings WHERE account_id = ?', (acc['id'],))
        if ns and not ns['enabled']:
            return
        
        conn = existing_conn
        own_conn = False
        if not conn:
            try:
                password = self.sec.decrypt(acc['password_enc'])
                conn = self.imap.connect(acc['imap_host'], acc['imap_port'], acc['email'], password)
                own_conn = True
            except:
                return
        
        try:
            messages = self.imap.fetch_messages(conn, 'INBOX', limit=5) # جلب الأحدث فقط
            for msg in reversed(messages):
                uid_key = f"{acc['id']}:{msg['uid']}"
                with self._lock:
                    if uid_key in self._notified_uids:
                        continue
                    self._notified_uids.add(uid_key)
                
                is_new = self.db.save_message(
                    acc['id'], msg['uid'], msg['sender'], msg['sender_email'],
                    msg['subject'], msg['body_preview'], msg['body_full'],
                    msg['received_date'], msg['has_attachments'], msg['attachments_info']
                )
                
                if is_new:
                    self._send_notification(acc['user_id'], acc, msg)
        finally:
            if own_conn:
                try: conn.logout()
                except: pass
    
    def _send_notification(self, user_id: int, acc, msg):
        try:
            # تصميم إشعار فني فخم (Premium UI)
            text = (
                f"<b>⚡ MATARI MAIL PRO ⚡</b>\n"
                f"<i>إشعار فوري (Real-time)</i>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>📩 وصل بريد جديد</b>\n\n"
                f"👤 <b>من:</b> {self.sec.escape_html(msg['sender'])}\n"
                f"📧 <code>{msg['sender_email']}</code>\n"
                f"📌 <b>الموضوع:</b> {self.sec.escape_html(msg['subject'])}\n"
                f"🕐 <b>الوقت:</b> {datetime.now().strftime('%I:%M %p')}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📝 <i>{self.sec.escape_html(msg['body_preview'][:200])}...</i>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📮 <i>في حساب: {acc['email']}</i>"
            )
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("📖 قراءة الآن", callback_data=f"msg_view_{acc['id']}"),
                types.InlineKeyboardButton("🗑 تجاهل", callback_data="notif_dismiss")
            )
            
            self.bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup)
        except Exception:
            pass

# ============================================================
# Telegram UI Manager
# ============================================================


def make_btn(text, callback_data, color="primary"):
    try:
        return types.InlineKeyboardButton(text, callback_data=callback_data, style=color)
    except TypeError:
        return types.InlineKeyboardButton(text, callback_data=callback_data)

def check_whatsapp_number(phone: str) -> dict:
    """فحص دقيق لرقم الواتساب"""
    import urllib.request
    import urllib.error
    import json
    
    clean_phone = ''.join(filter(str.isdigit, phone))
    if len(clean_phone) < 10:
        return {"ok": False, "msg": "❌ الرقم قصير جداً أو غير صالح"}
    
    # استخدام API عام للفحص (أكثر دقة من wa.me)
    url = f"https://api.whatsapp.com/send?phone={clean_phone}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')
            
            # إذا كان الرقم غير مسجل، الصفحة تحتوي على "Phone number shared via url is invalid" أو "الرقم غير صالح"
            if "invalid" in html.lower() or "غير صالح" in html:
                return {"ok": False, "msg": "❌ الرقم غير مسجل في واتساب (أو غير صالح)"}
            
            # إذا كان مسجلاً، تظهر صفحة الدردشة وتحتوي على "Continue to Chat"
            if "continue to chat" in html.lower() or "متابعة" in html:
                return {"ok": True, "msg": "✅ الرقم مسجل في واتساب"}
                
            return {"ok": False, "msg": "❌ الرقم غير مسجل في واتساب"}
            
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "msg": "❌ الرقم غير مسجل في واتساب"}
        return {"ok": False, "msg": f"⚠️ خطأ: {e.code}"}
    except Exception as e:
        return {"ok": False, "msg": f"⚠️ تعذر الاتصال: {str(e)[:50]}"}

class TelegramUI:
    def __init__(self, bot: telebot.TeleBot, db: DatabaseManager, config: Config):
        self.bot = bot
        self.db = db
        self.config = config
    
    def check_auth(self, message) -> bool:
        uid = message.from_user.id
        if uid == self.config.owner_id:
            self.db.ensure_user(uid, message.from_user.username or '', message.from_user.first_name or '')
            return True
        if uid in self.config.authorized_users:
            self.db.ensure_user(uid, message.from_user.username or '', message.from_user.first_name or '')
            return True
        try:
            row = self.db.fetchone('SELECT * FROM allowed_users WHERE telegram_id = ? AND is_active = 1', (uid,))
            if row:
                self.db.ensure_user(uid, message.from_user.username or '', message.from_user.first_name or '')
                return True
        except Exception:
            pass
        try:
            self.bot.reply_to(message, "⛔ غير مصرح لك باستخدام هذا البوت.\n\nاطلب من المدير إضافتك.")
        except:
            pass
        return False
    
    def check_callback_auth(self, call) -> bool:
        uid = call.from_user.id
        if uid == self.config.owner_id:
            return True
        if uid in self.config.authorized_users:
            return True
        try:
            row = self.db.fetchone('SELECT * FROM allowed_users WHERE telegram_id = ? AND is_active = 1', (uid,))
            if row:
                return True
        except Exception:
            pass
        try:
            self.bot.answer_callback_query(call.id, "⛔ غير مصرح", show_alert=True)
        except:
            pass
        return False
    
    def is_owner(self, user_id: int) -> bool:
        return bool(self.config.owner_id) and user_id == self.config.owner_id
    
    def main_menu(self, user_id: int) -> Tuple[str, types.InlineKeyboardMarkup]:
        """لوحة التحكم الرئيسية"""
        accounts = self.db.get_accounts(user_id)
        acc_count = len(accounts)
        
        active_acc = self.db.get_default_account(user_id)
        active_name = active_acc['email'] if active_acc else "لا يوجد"
        
        total_unread = self.db.count_unread(user_id=user_id)
        
        # عدد المرسل
        sent_row = self.db.fetchone('''
            SELECT COUNT(*) as c FROM sent_messages sm
            JOIN accounts a ON sm.account_id = a.id
            WHERE a.user_id = ?
        ''', (user_id,))
        sent_count = sent_row['c'] if sent_row else 0
        
        text = (
            f"📬 <b>MATARI MAIL PRO</b>\n"
            f"<i>المطري لإدارة البريد الذكي</i>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>الإحصائيات</b>\n"
            f"├ 👥 الحسابات: <b>{acc_count}</b>\n"
            f"├ 📮 النشط: <code>{active_name}</code>\n"
            f"├ 📩 غير المقروءة: <b>{total_unread}</b>\n"
            f"└ 📤 المرسلة: <b>{sent_count}</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🕐 آخر تحديث: {datetime.now().strftime('%H:%M')}"
        )
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            make_btn("📥 صندوق الوارد", "menu_inbox", "success"),  # أخضر
            make_btn("✉️ إرسال رسالة", "menu_compose", "primary"), # أزرق
        )
        markup.add(
            make_btn("📤 المرسلة", "menu_sent", "primary"),        # أزرق
            make_btn("📝 المسودات", "menu_drafts", "primary"),     # أزرق
        )
        markup.add(
            make_btn("👥 جهات الاتصال", "menu_contacts", "primary"),# أزرق
            make_btn("🔍 البحث", "menu_search", "primary"),        # أزرق
        )
        markup.add(
            make_btn("⚙️ إدارة الحسابات", "menu_accounts", "primary"), # أزرق
            make_btn("🔔 الإشعارات", "menu_notifications", "primary"), # أزرق
        )
        markup.add(
            make_btn("📋 سجل النشاطات", "menu_history", "primary"), # أزرق
            make_btn("❓ المساعدة", "menu_help", "primary"),       # أزرق
        )
        markup.add(make_btn("📱 فحص واتساب", "menu_wa_check", "success"))
        markup.add(make_btn("📨 إرسال إشعار واتساب", "menu_wa_send", "primary")) # أخضر
        
        if self.config.owner_id and user_id == self.config.owner_id:
            markup.add(types.InlineKeyboardButton("👑 إدارة المستخدمين", callback_data="menu_users"))
        
        return text, markup
    
    def inbox_view(self, user_id: int, page: int = 0) -> Tuple[str, types.InlineKeyboardMarkup]:
        acc = self.db.get_default_account(user_id)
        if not acc:
            return "⚠️ لا يوجد حساب مرتبط. أضف حساباً أولاً.", self._back_markup()
        
        messages, total = self.db.get_messages(acc['id'], 'INBOX', page, 5)
        
        if not messages:
            return f"📭 صندوق الوارد فارغ.\n\n📮 {acc['email']}", self._inbox_markup(page, total, acc['id'])
        
        text = f"📥 <b>صندوق الوارد</b> — {acc['email']}\n"
        text += f"📊 إجمالي: {total} رسالة | صفحة {page + 1}\n"
        text += "━━━━━━━━━━━━━━━━━\n\n"
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for msg in messages:
            att_icon = " 📎" if msg['has_attachments'] else ""
            subject = msg['subject'][:28] if msg['subject'] else 'بدون عنوان'
            
            if not msg['is_read']:
                btn_text = f"🟢 جديد | {subject}{att_icon}"
                # كود اللون الأخضر
                try:
                    btn = types.InlineKeyboardButton(btn_text, callback_data=f"msg_view_{msg['id']}", style="success")
                except TypeError:
                    btn = types.InlineKeyboardButton(btn_text, callback_data=f"msg_view_{msg['id']}")
            else:
                btn_text = f"🔴 مقروء | {subject}{att_icon}"
                # كود اللون الأحمر
                try:
                    btn = types.InlineKeyboardButton(btn_text, callback_data=f"msg_view_{msg['id']}", style="danger")
                except TypeError:
                    btn = types.InlineKeyboardButton(btn_text, callback_data=f"msg_view_{msg['id']}")
            
            markup.add(btn)
        
        # أزرار التنقل
        nav = []
        if page > 0:
            nav.append(types.InlineKeyboardButton("◀️ السابق", callback_data=f"inbox_page_{page-1}"))
        if (page + 1) * 5 < total:
            nav.append(types.InlineKeyboardButton("التالي ▶️", callback_data=f"inbox_page_{page+1}"))
        if nav:
            markup.row(*nav)
        
        markup.add(types.InlineKeyboardButton("🔄 تحديث", callback_data="inbox_refresh"))
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        
        return text, markup
    
    def _inbox_markup(self, page, total, account_id):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔄 تحديث", callback_data="inbox_refresh"))
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        return markup
    
    def _back_markup(self):
        markup = types.InlineKeyboardMarkup()
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        return markup


# ============================================================
# Callback Manager - معالج الأزرار
# ============================================================

class CallbackManager:
    def __init__(self, bot: telebot.TeleBot, db: DatabaseManager, config: Config,
                 sec: SecurityManager, ui: TelegramUI, acc_mgr: AccountManager,
                 msg_mgr: MessageManager, imap: IMAPManager, smtp: SMTPManager):
        self.bot = bot
        self.db = db
        self.config = config
        self.sec = sec
        self.ui = ui
        self.acc_mgr = acc_mgr
        self.msg_mgr = msg_mgr
        self.imap = imap
        self.smtp = smtp
        # حالة المستخدمين للعمليات متعددة الخطوات
        self.user_states: Dict[int, Dict] = {}
        self._state_lock = threading.Lock()
    
    def set_state(self, user_id: int, state: str, **kwargs):
        with self._state_lock:
            self.user_states[user_id] = {'state': state, **kwargs}
    
    def get_state(self, user_id: int) -> Optional[Dict]:
        with self._state_lock:
            return self.user_states.get(user_id)
    
    def clear_state(self, user_id: int):
        with self._state_lock:
            self.user_states.pop(user_id, None)
    
    def handle_callback(self, call):
        if not self.ui.check_callback_auth(call):
            return
        
        uid = call.from_user.id
        data = call.data
        
        try:
            self.bot.answer_callback_query(call.id)
        except:
            pass
        
        # توجيه الأزرار
        if data == "menu_main":
            self._main_menu(call, uid)
        elif data == "menu_inbox":
            self._inbox(call, uid)
        elif data.startswith("inbox_page_"):
            page = int(data.split('_')[-1])
            self._inbox(call, uid, page)
        elif data == "inbox_refresh":
            self._refresh_inbox(call, uid)
        elif data.startswith("msg_view_"):
            msg_id = int(data.split('_')[-1])
            self._view_message(call, uid, msg_id)
        elif data.startswith("msg_reply_"):
            msg_id = int(data.split('_')[-1])
            self._start_reply(call, uid, msg_id)
        elif data.startswith("msg_forward_"):
            msg_id = int(data.split('_')[-1])
            self._start_forward(call, uid, msg_id)
        elif data.startswith("msg_archive_"):
            msg_id = int(data.split('_')[-1])
            self._archive_message(call, uid, msg_id)
        elif data.startswith("msg_delete_"):
            msg_id = int(data.split('_')[-1])
            self._delete_message(call, uid, msg_id)
        elif data.startswith("msg_detail_"):
            msg_id = int(data.split('_')[-1])
            self._message_detail(call, uid, msg_id)
        elif data == "menu_compose":
            self._start_compose(call, uid)
        elif data == "skip_subject":
            state = self.get_state(uid)
            if state and state.get('state') == 'compose_subject':
                self.set_state(uid, 'compose_body', to=state['to'], subject="(بدون موضوع)")
                try:
                    self.bot.edit_message_text("📝 أرسل محتوى الرسالة:", 
                                               chat_id=call.message.chat.id, 
                                               message_id=call.message.message_id)
                except:
                    self.bot.send_message(call.message.chat.id, "📝 أرسل محتوى الرسالة:")
        elif data.startswith("compose_to_"):
            if data == "compose_to_new":
                # طلب بريد جديد يدوياً
                try:
                    self.bot.edit_message_text("📧 أرسل البريد الإلكتروني للمستلم:", 
                                               chat_id=call.message.chat.id, 
                                               message_id=call.message.message_id,
                                               reply_markup=self.ui._back_markup())
                except: pass
            else:
                # تم اختيار جهة اتصال
                to_email = data.replace("compose_to_", "")
                state = self.get_state(uid)
                if state:
                    self.set_state(uid, 'compose_subject', to=to_email, account_id=state.get('account_id'))
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("⏭️ تخطي الموضوع", callback_data="skip_subject"))
                    self.bot.edit_message_text(f"📧 إلى: {to_email}\n\n📌 أرسل عنوان الرسالة أو تخطه:", 
                                               chat_id=call.message.chat.id, 
                                               message_id=call.message.message_id,
                                               reply_markup=markup)
        elif data.startswith("compose_from_"):
            acc_id = int(data.split('_')[-1])
            acc = self.db.get_account(acc_id)
            if acc and acc['user_id'] == uid:
                self._begin_compose_with_account(call, uid, acc)
        elif data == "menu_sent":
            self._sent_view(call, uid)
        elif data.startswith("sent_page_"):
            page = int(data.split('_')[-1])
            self._sent_view(call, uid, page)
        elif data == "menu_drafts":
            self._drafts_view(call, uid)
        elif data.startswith("draft_view_"):
            draft_id = int(data.split('_')[-1])
            self._view_draft(call, uid, draft_id)
        elif data.startswith("draft_delete_"):
            draft_id = int(data.split('_')[-1])
            self.db.delete_draft(draft_id, uid)
            self._drafts_view(call, uid)
        elif data == "menu_accounts":
            self._accounts_view(call, uid)
        elif data == "account_add_gmail":
            self.set_state(uid, 'add_gmail_email')
            self.bot.edit_message_text(
                "📧 <b>إضافة حساب Gmail</b>\n\n"
                "أرسل بريد Gmail الخاص بك:\n"
                "مثال: <code>user@gmail.com</code>\n\n"
                "⚠️ استخدم <b>كلمة مرور التطبيق</b> وليس كلمة المرور العادية.\n"
                "🔗 أنشئها من: myaccount.google.com/apppasswords\n\n"
                "أرسل /cancel للإلغاء",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode='HTML',
                reply_markup=self.ui._back_markup()
            )
        elif data == "account_add_custom":
            self.set_state(uid, 'auto_email')
            self.bot.edit_message_text(
                "📧 <b>إضافة حساب بريد</b>\n\n"
                "أرسل بريدك الإلكتروني فقط:\n"
                "مثال: <code>user@gmail.com</code>\n\n"
                "✅ يدعم تلقائياً: Gmail, Outlook, Yahoo, iCloud, Zoho, Yandex, GMX, AOL\n"
                "⚠️ لـ Gmail استخدم <b>كلمة مرور التطبيق</b>\n"
                "🔗 myaccount.google.com/apppasswords\n\n"
                "أرسل /cancel للإلغاء",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode='HTML',
                reply_markup=self.ui._back_markup()
            )
        elif data.startswith("account_default_"):
            acc_id = int(data.split('_')[-1])
            self.db.set_default_account(uid, acc_id)
            self.db.log_action(uid, 'تغيير الحساب النشط', str(acc_id))
            self._accounts_view(call, uid)
        elif data.startswith("account_del_"):
            acc_id = int(data.split('_')[-1])
            self.db.delete_account(acc_id, uid)
            self.db.log_action(uid, 'حذف حساب', str(acc_id))
            self._accounts_view(call, uid)
        elif data == "menu_notifications":
            self._notifications_view(call, uid)
        elif data.startswith("notif_toggle_"):
            acc_id = int(data.split('_')[-1])
            ns = self.db.fetchone('SELECT enabled FROM notification_settings WHERE account_id = ?', (acc_id,))
            new_val = 0 if ns and ns['enabled'] else 1
            self.db.execute('UPDATE notification_settings SET enabled = ? WHERE account_id = ?', (new_val, acc_id))
            self._notifications_view(call, uid)
        elif data.startswith("notif_global_"):
            val = int(data.split('_')[-1])
            self.db.update_settings(uid, notifications_enabled=val)
            self._notifications_view(call, uid)
        elif data == "menu_contacts":
            self._contacts_view(call, uid)
        elif data == "contact_add":
            self.set_state(uid, 'add_contact_name')
            self.bot.edit_message_text(
                "👤 <b>إضافة جهة اتصال</b>\n\nأرسل الاسم:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode='HTML',
                reply_markup=self.ui._back_markup()
            )
        elif data.startswith("contact_del_"):
            cid = int(data.split('_')[-1])
            self.db.delete_contact(cid, uid)
            self._contacts_view(call, uid)
        elif data == "menu_search":
            self.set_state(uid, 'search_query')
            self.bot.edit_message_text(
                "🔍 <b>البحث الذكي</b>\n\n"
                "أرسل كلمة البحث (الموضوع، المرسل، أو المحتوى):",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode='HTML',
                reply_markup=self.ui._back_markup()
            )
        elif data == "search_cancel":
            self.clear_state(uid)
            self._main_menu(call, uid)
        elif data.startswith("search_page_"):
            page = int(data.split('_')[-1])
            state = self.get_state(uid)
            if state and 'query' in state:
                self._search_results(call, uid, state['query'], page)
        elif data == "menu_history":
            self._history_view(call, uid)
        elif data.startswith("history_page_"):
            page = int(data.split('_')[-1])
            self._history_view(call, uid, page)
        elif data == "menu_users":
            if not self.ui.is_owner(uid):
                try:
                    self.bot.answer_callback_query(call.id, "⛔ للمدير فقط", show_alert=True)
                except:
                    pass
                return
            self._users_view(call, uid)
        elif data == "user_add_start":
            if not self.ui.is_owner(uid):
                return
            self.set_state(uid, 'admin_add_user')
            self.bot.edit_message_text(
                "👑 <b>إضافة مستخدم</b>\n\n"
                "أرسل معرف تيليجرام للمستخدم الجديد:\n"
                "مثال: <code>123456789</code>\n\n"
                "💡 يحصل على معرفه من @userinfobot\n\n"
                "أرسل /cancel للإلغاء",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode='HTML',
                reply_markup=self.ui._back_markup()
            )
        elif data.startswith("user_toggle_"):
            if not self.ui.is_owner(uid):
                return
            target_id = int(data.split('_')[-1])
            row = self.db.fetchone('SELECT is_active FROM allowed_users WHERE telegram_id = ?', (target_id,))
            if row:
                new_val = 0 if row['is_active'] else 1
                self.db.execute('UPDATE allowed_users SET is_active = ? WHERE telegram_id = ?', (new_val, target_id))
            self._users_view(call, uid)
        elif data.startswith("user_del_"):
            if not self.ui.is_owner(uid):
                return
            target_id = int(data.split('_')[-1])
            if target_id == self.config.owner_id:
                try:
                    self.bot.answer_callback_query(call.id, "لا يمكن حذف المدير", show_alert=True)
                except:
                    pass
                return
            self.db.execute('DELETE FROM allowed_users WHERE telegram_id = ?', (target_id,))
            self._users_view(call, uid)
        elif data == "menu_wa_send":
            self.set_state(uid, 'wa_send_phone')
            self.bot.edit_message_text(
                "📨 <b>إرسال إشعار واتساب</b>\n\nأرسل رقم الهاتف (بدون +):",
                chat_id=call.message.chat.id, message_id=call.message.message_id,
                parse_mode='HTML', reply_markup=self.ui._back_markup()
            )
        elif data == "menu_wa_check":
            self.set_state(uid, 'wa_check_phone')
            self.bot.edit_message_text(
                "📱 <b>فحص رقم واتساب</b>\n\n"
                "أرسل رقم الهاتف مع رمز الدولة (بدون + أو مسافات):\n"
                "مثال: <code>967771234567</code>\n\n"
                "أرسل /cancel للإلغاء",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode='HTML',
                reply_markup=self.ui._back_markup()
            )
        elif data == "menu_help":
            self._help_view(call, uid)
        elif data.startswith("compose_confirm"):
            self._confirm_send(call, uid)
        elif data.startswith("compose_cancel"):
            self.clear_state(uid)
            self._main_menu(call, uid)
        elif data == "notif_dismiss":
            try:
                self.bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
    
    def _main_menu(self, call, uid):
        text, markup = self.ui.main_menu(uid)
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            self.bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    
    def _inbox(self, call, uid, page=0):
        text, markup = self.ui.inbox_view(uid, page)
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            self.bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    
    def _refresh_inbox(self, call, uid):
        acc = self.db.get_default_account(uid)
        if not acc:
            self._inbox(call, uid)
            return
        new_count, msg = self.msg_mgr.sync_account(acc['id'])
        try:
            self.bot.answer_callback_query(call.id, msg, show_alert=False)
        except:
            pass
        self._inbox(call, uid)
    
    def _view_message(self, call, uid, msg_id):
        msg = self.db.get_message(msg_id)
        if not msg:
            try:
                self.bot.answer_callback_query(call.id, "الرسالة غير موجودة", show_alert=True)
            except:
                pass
            return
        
        self.db.mark_read(msg_id)
        
        acc = self.db.get_account(msg['account_id'])
        acc_email = acc['email'] if acc else ''
        
        # استخراج النص بأمان وتنظيفه من رموز HTML التي تكسر تيليجرام
        body_text = msg['body_full'] if msg['body_full'] else msg['body_preview']
        if not body_text or body_text.strip() == '':
            body_text = "(لا يوجد نص في هذه الرسالة، قد تكون مرفقاً فقط)"
        
        safe_body = self.sec.escape_html(body_text[:1500])
        safe_subject = self.sec.escape_html(msg['subject'] if msg['subject'] else 'بدون عنوان')
        safe_sender = self.sec.escape_html(msg['sender'])

        text = (
            f"📧 <b>{safe_subject}</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>من:</b> {safe_sender}\n"
            f"📧 <code>{msg['sender_email']}</code>\n"
            f"📅 {msg['received_date'][:16] if msg['received_date'] else ''}\n"
            f"📮 {acc_email}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📝 {safe_body}\n"
            f"━━━━━━━━━━━━━━━━━"
        )
        
        if msg['has_attachments']:
            try:
                atts = json.loads(msg['attachments_info'] or '[]')
                text += f"\n📎 <b>المرفقات ({len(atts)}):</b>\n"
                for a in atts[:5]:
                    size_kb = a.get('size', 0) / 1024
                    text += f"• {a.get('filename', 'ملف')} ({size_kb:.1f} KB)\n"
            except:
                pass
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("↩️ رد", callback_data=f"msg_reply_{msg_id}"),
            types.InlineKeyboardButton("➡️ إعادة توجيه", callback_data=f"msg_forward_{msg_id}"),
        )
        markup.add(
            types.InlineKeyboardButton("📦 أرشفة", callback_data=f"msg_archive_{msg_id}"),
            types.InlineKeyboardButton("🗑 حذف", callback_data=f"msg_delete_{msg_id}"),
        )
        markup.add(types.InlineKeyboardButton("📋 تفاصيل", callback_data=f"msg_detail_{msg_id}"))
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_inbox"))
        
        try:
            # محاولة تعديل الرسالة الحالية
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            # إذا فشل التعديل (مثلاً النص طويل جداً أو نفس النص)، أرسل رسالة جديدة
            try:
                self.bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
            except Exception as e2:
                # إذا فشل الإرسال أيضاً، أرسل تنبيهاً بسيطاً
                self.bot.send_message(call.message.chat.id, f"⚠️ تعذر عرض الرسالة.\nالخطأ: {str(e2)[:100]}")
    
    def _message_detail(self, call, uid, msg_id):
        msg = self.db.get_message(msg_id)
        if not msg:
            return
        text = (
            f"📋 <b>تفاصيل الرسالة</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🆔 UID: <code>{msg['uid']}</code>\n"
            f"📅 التاريخ: {msg['received_date']}\n"
            f"👤 المرسل: {msg['sender']}\n"
            f"📧 البريد: {msg['sender_email']}\n"
            f"📌 الموضوع: {msg['subject']}\n"
            f"📎 مرفقات: {'نعم' if msg['has_attachments'] else 'لا'}\n"
            f"📖 مقروءة: {'نعم' if msg['is_read'] else 'لا'}\n"
            f"📦 مؤرشفة: {'نعم' if msg['is_archived'] else 'لا'}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📝 المحتوى الكامل:\n{msg['body_full'] or msg['body_preview']}"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data=f"msg_view_{msg_id}"))
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass
    
    def _start_reply(self, call, uid, msg_id):
        msg = self.db.get_message(msg_id)
        if not msg:
            return
        clean_subj = msg['subject'].strip()
        if not clean_subj or clean_subj == '.' or clean_subj == 'Re: .':
            clean_subj = '(بدون عنوان)'
            
        self.set_state(uid, 'reply_body', reply_to=msg['sender_email'],
                       subject=clean_subj, msg_id=msg_id)
        self.bot.edit_message_text(
            f"↩️ <b>رد على:</b> {msg['sender_email']}\n"
            f"📌 <b>الموضوع:</b> Re: {msg['subject']}\n\n"
            f"أرسل نص الرد:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode='HTML',
            reply_markup=self.ui._back_markup()
        )
    
    def _start_forward(self, call, uid, msg_id):
        msg = self.db.get_message(msg_id)
        if not msg:
            return
        self.set_state(uid, 'forward_to', msg_id=msg_id, subject=msg['subject'])
        self.bot.edit_message_text(
            f"➡️ <b>إعادة توجيه:</b> {msg['subject']}\n\n"
            f"أرسل البريد الإلكتروني للمستلم:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode='HTML',
            reply_markup=self.ui._back_markup()
        )
    
    def _archive_message(self, call, uid, msg_id):
        self.db.archive_message(msg_id)
        self.db.log_action(uid, 'أرشفة رسالة', str(msg_id))
        try:
            self.bot.answer_callback_query(call.id, "✅ تم الأرشفة")
        except:
            pass
        self._inbox(call, uid)
    
    def _delete_message(self, call, uid, msg_id):
        self.db.delete_message(msg_id)
        self.db.log_action(uid, 'حذف رسالة', str(msg_id))
        try:
            self.bot.answer_callback_query(call.id, "🗑 تم الحذف")
        except:
            pass
        self._inbox(call, uid)
    
    def _start_compose(self, call, uid):
        accounts = self.db.get_accounts(uid)
        if not accounts:
            try:
                self.bot.answer_callback_query(call.id, "⚠️ أضف حساباً أولاً", show_alert=True)
            except:
                pass
            return
        
        # إذا كان لديه حساب واحد فقط، ابدأ الإرسال مباشرة
        if len(accounts) == 1:
            self._begin_compose_with_account(call, uid, accounts[0])
            return
        
        # إذا كان لديه عدة حسابات، اعرض قائمة للاختيار
        text = (
            f"✉️ <b>إرسال رسالة جديدة</b>\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"📮 <b>اختر الحساب الذي تريد الإرسال منه:</b>"
        )
        markup = types.InlineKeyboardMarkup(row_width=1)
        
        for a in accounts:
            default_star = "⭐ " if a['is_default'] else "📧 "
            markup.add(types.InlineKeyboardButton(
                f"{default_star}{a['email']}",
                callback_data=f"compose_from_{a['id']}"
            ))
            
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass

    def _begin_compose_with_account(self, call, uid, acc):
        self.set_state(uid, 'compose_to', account_id=acc['id'])
        
        # جلب جهات الاتصال
        contacts = self.db.get_contacts(uid)[:8] # أول 8 جهات اتصال
        
        text = (
            f"✉️ <b>إرسال رسالة جديدة</b>\n"
            f"📮 من: <code>{acc['email']}</code>\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"📧 <b>اختر جهة اتصال:</b>"
        )
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for c in contacts:
            markup.add(types.InlineKeyboardButton(
                f"👤 {c['name']} ({c['email']})",
                callback_data=f"compose_to_{c['email']}"
            ))
            
        markup.add(types.InlineKeyboardButton("✍️ كتابة بريد جديد", callback_data="compose_to_new"))
        markup.add(types.InlineKeyboardButton("🔙 القائمة", callback_data="menu_main"))
        
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass
    
    def _confirm_send(self, call, uid):
        state = self.get_state(uid)
        if not state or state.get('state') != 'compose_confirm':
            return
        
        # استخدام الحساب المختار أو الافتراضي
        acc_id = state.get('account_id')
        if acc_id:
            acc = self.db.get_account(acc_id)
        else:
            acc = self.db.get_default_account(uid)
            
        if not acc:
            self.clear_state(uid)
            self._main_menu(call, uid)
            return
        
        # منع الإرسال المكرر
        if state.get('sending'):
            try:
                self.bot.answer_callback_query(call.id, "⏳ جاري الإرسال...", show_alert=False)
            except:
                pass
            return
        
        state['sending'] = True
        
        ok, msg, info = self.msg_mgr.send_email(
            acc['id'], state['to'], state['subject'], state['body']
        )
        
        self.db.log_action(uid, 'إرسال رسالة', f"إلى: {state['to']}")
        self.clear_state(uid)
        
        if ok:
            text = (
                f"✅ <b>تم قبول الرسالة</b>\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"📧 المستلم: {info['recipient']}\n"
                f"📌 الموضوع: {info['subject']}\n"
                f"📮 الحساب: {info['account']}\n"
                f"📅 التاريخ: {info['date']}\n"
                f"📊 الحالة: {info['status']}\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"⚠️ <i>هذا يعني قبول SMTP فقط، وليس تأكيد التسليم النهائي.</i>"
            )
        else:
            text = f"❌ <b>فشل الإرسال</b>\n\n{msg}"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            self.bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    
    def _sent_view(self, call, uid, page=0):
        acc = self.db.get_default_account(uid)
        if not acc:
            self._main_menu(call, uid)
            return
        
        rows, total = self.db.get_sent(acc['id'], page, 5)
        
        if not rows:
            text = "📤 لا توجد رسائل مرسلة."
            markup = types.InlineKeyboardMarkup()
            markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        else:
            text = f"📤 <b>الرسائل المرسلة</b> — {acc['email']}\n"
            text += f"📊 إجمالي: {total} | صفحة {page + 1}\n"
            text += "━━━━━━━━━━━━━━━━━\n\n"
            for r in rows:
                text += f"📧 {r['recipient']}\n📌 {r['subject'][:40]}\n📅 {r['sent_date'][:16]}\n\n"
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            nav = []
            if page > 0:
                nav.append(types.InlineKeyboardButton("◀️", callback_data=f"sent_page_{page-1}"))
            if (page + 1) * 5 < total:
                nav.append(types.InlineKeyboardButton("▶️", callback_data=f"sent_page_{page+1}"))
            if nav:
                markup.row(*nav)
            markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass
    
    def _drafts_view(self, call, uid):
        drafts = self.db.get_drafts(uid)
        
        if not drafts:
            text = "📝 لا توجد مسودات."
            markup = types.InlineKeyboardMarkup()
            markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        else:
            text = "📝 <b>المسودات</b>\n━━━━━━━━━━━━━━━━━\n\n"
            markup = types.InlineKeyboardMarkup(row_width=1)
            for d in drafts:
                text += f"📌 {d['subject'][:40] if d['subject'] else 'بدون موضوع'}\n"
                text += f"📧 {d['recipient'] or 'بدون مستلم'}\n\n"
                markup.add(types.InlineKeyboardButton(
                    f"✏️ {d['subject'][:30] if d['subject'] else 'مسودة ' + str(d['id'])}",
                    callback_data=f"draft_view_{d['id']}"
                ))
            markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass
    
    def _view_draft(self, call, uid, draft_id):
        d = self.db.get_draft(draft_id, uid)
        if not d:
            self._drafts_view(call, uid)
            return
        text = (
            f"📝 <b>مسودة</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📧 إلى: {d['recipient'] or 'غير محدد'}\n"
            f"📌 الموضوع: {d['subject'] or 'بدون'}\n"
            f"📅 {d['updated_at'][:16]}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📝 {d['body'] or ''}"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🗑 حذف", callback_data=f"draft_delete_{draft_id}"))
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="menu_drafts"))
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass
    
    def _accounts_view(self, call, uid):
        accounts = self.db.get_accounts(uid)
        
        text = f"⚙️ <b>إدارة الحسابات</b>\n━━━━━━━━━━━━━━━━━\n"
        if accounts:
            for a in accounts:
                default_mark = "⭐ " if a['is_default'] else ""
                last_checked = a['last_checked'][:16] if a['last_checked'] else 'لم يتم الفحص'
                text += f"{default_mark}📧 <code>{a['email']}</code>\n"
                text += f"   IMAP: {a['imap_host']}:{a['imap_port']}\n"
                text += f"   SMTP: {a['smtp_host']}:{a['smtp_port']}\n"
                text += f"   آخر فحص: {last_checked}\n\n"
        else:
            text += "لا توجد حسابات مرتبطة.\n"
        
        text += "━━━━━━━━━━━━━━━━━"
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for a in accounts:
            if not a['is_default']:
                markup.add(types.InlineKeyboardButton(
                    f"⭐ تعيين {a['email']} افتراضي",
                    callback_data=f"account_default_{a['id']}"
                ))
            markup.add(types.InlineKeyboardButton(
                f"🗑 حذف {a['email']}",
                callback_data=f"account_del_{a['id']}"
            ))
        
        markup.add(make_btn("➕ إضافة Gmail", "account_add_gmail", "success"))
        markup.add(make_btn("➕ إضافة حساب مخصص", "account_add_custom", "primary"))
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass
    
    def _notifications_view(self, call, uid):
        settings = self.db.get_settings(uid)
        accounts = self.db.get_accounts(uid)
        
        global_on = settings['notifications_enabled'] if settings else 1
        
        text = f"🔔 <b>إعدادات الإشعارات</b>\n━━━━━━━━━━━━━━━━━\n"
        text += f"📢 الإشعارات العامة: {'✅ مفعلة' if global_on else '❌ معطلة'}\n\n"
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        
        if global_on:
            markup.add(types.InlineKeyboardButton("❌ تعطيل الإشعارات العامة", callback_data="notif_global_0"))
        else:
            markup.add(types.InlineKeyboardButton("✅ تفعيل الإشعارات العامة", callback_data="notif_global_1"))
        
        for a in accounts:
            ns = self.db.fetchone('SELECT enabled FROM notification_settings WHERE account_id = ?', (a['id'],))
            enabled = ns['enabled'] if ns else 1
            icon = "✅" if enabled else "❌"
            markup.add(types.InlineKeyboardButton(
                f"{icon} {a['email']}",
                callback_data=f"notif_toggle_{a['id']}"
            ))
        
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass
    
    def _contacts_view(self, call, uid):
        contacts = self.db.get_contacts(uid)
        
        text = f"👥 <b>جهات الاتصال</b> ({len(contacts)})\n━━━━━━━━━━━━━━━━━\n\n"
        markup = types.InlineKeyboardMarkup(row_width=1)
        
        for c in contacts[:10]:
            text += f"👤 {c['name']}\n📧 {c['email']}\n\n"
            markup.add(types.InlineKeyboardButton(
                f"🗑 {c['name'][:20]}",
                callback_data=f"contact_del_{c['id']}"
            ))
        
        markup.add(types.InlineKeyboardButton("➕ إضافة جهة اتصال", callback_data="contact_add"))
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass
    
    def _search_results(self, call, uid, query, page=0):
        acc = self.db.get_default_account(uid)
        if not acc:
            self._main_menu(call, uid)
            return
        
        results, total = self.db.get_messages(acc['id'], 'INBOX', page, 5, query)
        
        text = f"🔍 <b>نتائج البحث:</b> {query}\n"
        text += f"📊 النتائج: {total} | صفحة {page + 1}\n━━━━━━━━━━━━━━━━━\n\n"
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        
        if results:
            for r in results:
                markup.add(types.InlineKeyboardButton(
                    f"📧 {r['subject'][:35]}",
                    callback_data=f"msg_view_{r['id']}"
                ))
        else:
            text += "لا توجد نتائج."
        
        nav = []
        if page > 0:
            nav.append(types.InlineKeyboardButton("◀️", callback_data=f"search_page_{page-1}"))
        if (page + 1) * 5 < total:
            nav.append(types.InlineKeyboardButton("▶️", callback_data=f"search_page_{page+1}"))
        if nav:
            markup.row(*nav)
        
        # زر إلغاء البحث يعيد الحالة للطبيعي
        markup.add(types.InlineKeyboardButton("❌ إلغاء البحث", callback_data="search_cancel"))
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass
    
    def _history_view(self, call, uid, page=0):
        rows, total = self.db.get_history(uid, page, 10)
        
        text = f"📋 <b>سجل النشاطات</b>\n📊 إجمالي: {total}\n━━━━━━━━━━━━━━━━━\n\n"
        
        for r in rows:
            text += f"• {r['action']}\n  {r['details'][:50] if r['details'] else ''}\n  🕐 {r['created_at'][:16]}\n\n"
        
        markup = types.InlineKeyboardMarkup()
        nav = []
        if page > 0:
            nav.append(types.InlineKeyboardButton("◀️", callback_data=f"history_page_{page-1}"))
        if (page + 1) * 10 < total:
            nav.append(types.InlineKeyboardButton("▶️", callback_data=f"history_page_{page+1}"))
        if nav:
            markup.row(*nav)
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass
    
    def _users_view(self, call, uid):
        if not self.ui.is_owner(uid):
            return
        users = self.db.fetchall('SELECT * FROM allowed_users ORDER BY created_at DESC')
        text = (
            f"👑 <b>إدارة المستخدمين</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"👤 المدير: <code>{self.config.owner_id}</code>\n"
            f"📊 المضافون: {len(users)}\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
        )
        if users:
            for u in users:
                status = "✅" if u['is_active'] else "🚫"
                text += f"{status} <code>{u['telegram_id']}</code> — {u['role']}\n"
        else:
            text += "لا يوجد مستخدمون مضافون.\n"
        text += "\n━━━━━━━━━━━━━━━━━"
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("➕ إضافة مستخدم", callback_data="user_add_start"))
        for u in users:
            if u['telegram_id'] == self.config.owner_id:
                continue
            icon = "🚫 تعطيل" if u['is_active'] else "✅ تفعيل"
            markup.add(types.InlineKeyboardButton(
                f"{icon} {u['telegram_id']}",
                callback_data=f"user_toggle_{u['telegram_id']}"
            ))
            markup.add(types.InlineKeyboardButton(
                f"🗑 حذف {u['telegram_id']}",
                callback_data=f"user_del_{u['telegram_id']}"
            ))
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass
    
    def _help_view(self, call, uid):
        text = (
            "❓ <b>المساعدة — MATARI MAIL PRO</b>\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            "📥 <b>صندوق الوارد</b>\n"
            "عرض الرسائل الواردة مع إمكانية الرد والأرشفة والحذف.\n\n"
            "✉️ <b>إرسال رسالة</b>\n"
            "إرسال بريد جديد عبر خطوات بسيطة.\n\n"
            "⚙️ <b>إدارة الحسابات</b>\n"
            "إضافة حسابات Gmail أو حسابات مخصصة تدعم IMAP/SMTP.\n"
            "⚠️ لحسابات Gmail استخدم <b>كلمة مرور التطبيق</b>.\n\n"
            "🔔 <b>الإشعارات</b>\n"
            "تفعيل/تعطيل إشعارات الرسائل الجديدة لكل حساب.\n\n"
            "🔐 <b>الأمان</b>\n"
            "كلمات المرور مشفرة ولا تُسجل في السجلات.\n\n"
            "📌 <b>أوامر سريعة:</b>\n"
            "/start - القائمة الرئيسية\n"
            "/cancel - إلغاء العملية الحالية\n"
            "/help - هذه المساعدة\n\n"
            "━━━━━━━━━━━━━━━━━\n"
            "💡 <i>للاستفسارات: راجع مطور النظام</i>"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        try:
            self.bot.edit_message_text(text, chat_id=call.message.chat.id,
                                       message_id=call.message.message_id,
                                       parse_mode='HTML', reply_markup=markup)
        except:
            pass
    
    # ---------- معالجة النصوص (الخطوات متعددة) ----------
    def handle_text(self, message):
        if not self.ui.check_auth(message):
            return
        
        uid = message.from_user.id
        state = self.get_state(uid)
        if not state:
            return
        
        text = message.text.strip() if message.text else ''
        st = state.get('state')
        
        try:
            if st == 'add_gmail_email':
                if not self.sec.validate_email(text):
                    self.bot.reply_to(message, "❌ صيغة البريد غير صحيحة. أعد المحاولة أو /cancel")
                    return
                self.set_state(uid, 'add_gmail_password', email=text)
                self.bot.reply_to(message, "🔑 أرسل كلمة مرور التطبيق (App Password):\n\n"
                                          "⚠️ احذف الرسالة بعد الإرسال إن أردت.")
            
            elif st == 'add_gmail_password':
                email_addr = state['email']
                ok, msg = self.acc_mgr.add_gmail_account(uid, email_addr, text)
                self.clear_state(uid)
                if ok:
                    self.bot.reply_to(message, f"✅ {msg}")
                    self._send_main_menu(message.chat.id, uid)
                else:
                    self.bot.reply_to(message, f"❌ {msg}")
                    self._send_main_menu(message.chat.id, uid)
            
            elif st == 'auto_email':
                if not self.sec.validate_email(text):
                    self.bot.reply_to(message, "❌ صيغة البريد غير صحيحة. أعد المحاولة أو /cancel")
                    return
                provider = detect_provider(text)
                if not provider:
                    self.bot.reply_to(
                        message,
                        "❌ مزود البريد غير معروف.\n\n"
                        "المزودون المدعومون تلقائياً:\n"
                        "Gmail, Outlook, Yahoo, iCloud, Zoho, Yandex, GMX, AOL\n\n"
                        "لمزود آخر، استخدم زر «إضافة Gmail» واضبط الإعدادات يدوياً.\n"
                        "أو /cancel"
                    )
                    return
                self.set_state(uid, 'auto_password', email=text, provider=provider)
                self.bot.reply_to(
                    message,
                    f"✅ تم اكتشاف المزود: <b>{provider['name']}</b>\n\n"
                    f"📮 {text}\n\n"
                    f"🔑 أرسل كلمة مرور التطبيق:\n"
                    f"⚠️ لـ Gmail/Outlook: استخدم App Password وليس كلمة المرور العادية.\n\n"
                    f"💡 بعد الإضافة، احذف رسالة كلمة المرور من المحادثة.",
                    parse_mode='HTML'
                )
            
            elif st == 'auto_password':
                email_addr = state['email']
                provider = state['provider']
                password = text
                
                ok, msg = self.acc_mgr.add_custom_account(
                    uid, email_addr, email_addr.split('@')[0],
                    provider['imap'][0], provider['imap'][1],
                    provider['smtp'][0], provider['smtp'][1],
                    password
                )
                self.clear_state(uid)
                
                if ok:
                    self.bot.reply_to(
                        message,
                        f"✅ {msg}\n\n"
                        f"📮 {email_addr}\n"
                        f"🌐 {provider['name']}\n\n"
                        f"🗑 <b>احذف رسالة كلمة المرور من المحادثة الآن للأمان.</b>",
                        parse_mode='HTML'
                    )
                else:
                    # ترجمة الخطأ ليكون مفهوماً
                    if "AUTHENTICATIONFAILED" in msg or "Invalid credentials" in msg:
                        err_msg = (
                            f"❌ <b>فشل تسجيل الدخول</b>\n"
                            f"━━━━━━━━━━━━━━━━━\n"
                            f"يبدو أن كلمة المرور غير صحيحة.\n\n"
                            f"<b>الحل:</b>\n"
                            f"1️⃣ تأكد من تفعيل (التحقق بخطوتين) في حسابك.\n"
                            f"2️⃣ اذهب إلى: <a href='https://myaccount.google.com/apppasswords'>myaccount.google.com/apppasswords</a>\n"
                            f"3️⃣ أنشئ (كلمة مرور التطبيق) App Password.\n"
                            f"4️⃣ استخدم الرمز المكون من 16 حرفاً هنا.\n\n"
                            f"⚠️ لا تستخدم كلمة مرور جوجل العادية."
                        )
                    else:
                        err_msg = f"❌ {msg}"
                    self.bot.reply_to(message, err_msg, parse_mode='HTML', disable_web_page_preview=True)
                
                self._send_main_menu(message.chat.id, uid)
            
            elif st == 'add_contact_name':
                self.set_state(uid, 'add_contact_email', name=text)
                self.bot.reply_to(message, "📧 أرسل البريد الإلكتروني:")
            
            elif st == 'add_contact_email':
                if not self.sec.validate_email(text):
                    self.bot.reply_to(message, "❌ صيغة غير صحيحة. أعد المحاولة أو /cancel")
                    return
                self.db.add_contact(uid, state['name'], text)
                self.db.log_action(uid, 'إضافة جهة اتصال', text)
                self.clear_state(uid)
                self.bot.reply_to(message, f"✅ تم إضافة {state['name']}")
                self._send_main_menu(message.chat.id, uid)
            
            elif st == 'wa_send_phone':
                self.set_state(uid, 'wa_send_msg', phone=text)
                self.bot.reply_to(message, "✍️ أرسل نص الرسالة:")
            elif st == 'wa_send_msg':
                import urllib.request, json
                try:
                    req = urllib.request.Request("http://127.0.0.1:3000/send-message", 
                                                 data=json.dumps({"phone": ''.join(filter(str.isdigit, state['phone'])), "text": text}).encode(), 
                                                 headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(req, timeout=15) as res:
                        if json.loads(res.read().decode()).get("ok"):
                            self.bot.reply_to(message, "✅ تم إرسال الرسالة بنجاح!")
                        else:
                            self.bot.reply_to(message, "❌ فشل الإرسال")
                except Exception:
                    self.bot.reply_to(message, "⚠️ جسر الواتساب غير متصل")
                self.clear_state(uid)
                t, m = self.ui.main_menu(uid)
                self.bot.send_message(message.chat.id, t, parse_mode='HTML', reply_markup=m)
            elif st == 'wa_check_phone':
                self.bot.reply_to(message, "⏳ جاري الفحص...")
                result = check_wa_new(text)
                self.clear_state(uid)
                self.bot.reply_to(message, result['msg'], parse_mode='HTML')
                t, m = self.ui.main_menu(uid)
                self.bot.send_message(message.chat.id, t, parse_mode='HTML', reply_markup=m)
            
            elif st == 'admin_add_user':
                if not self.ui.is_owner(uid):
                    self.clear_state(uid)
                    return
                if not text.isdigit():
                    self.bot.reply_to(message, "❌ أرسل رقم معرف صحيح (أرقام فقط). /cancel")
                    return
                new_uid = int(text)
                if new_uid == self.config.owner_id:
                    self.bot.reply_to(message, "⚠️ هذا معرفك أنت (المدير).")
                    self.clear_state(uid)
                    return
                existing = self.db.fetchone('SELECT telegram_id FROM allowed_users WHERE telegram_id = ?', (new_uid,))
                if existing:
                    self.bot.reply_to(message, "⚠️ المستخدم موجود بالفعل.")
                    self.clear_state(uid)
                    return
                self.db.execute('INSERT INTO allowed_users (telegram_id, role, added_by) VALUES (?, ?, ?)',
                               (new_uid, 'user', uid))
                self.db.log_action(uid, 'إضافة مستخدم', str(new_uid))
                self.clear_state(uid)
                self.bot.reply_to(message, f"✅ تم إضافة المستخدم <code>{new_uid}</code>", parse_mode='HTML')
                t, m = self.ui.main_menu(uid)
                self.bot.send_message(message.chat.id, t, parse_mode='HTML', reply_markup=m)
            
            elif st == 'search_query':
                self.set_state(uid, 'search_active', query=text)
                self._send_search_results(message.chat.id, uid, text, 0)
            
            elif st == 'reply_body':
                reply_to = state['reply_to']
                subject = state['subject']
                if not subject.startswith('Re:'):
                    subject = f"Re: {subject}"
                self.set_state(uid, 'reply_confirm', reply_to=reply_to, subject=subject, body=text)
                self._show_compose_confirm(message.chat.id, uid)
            
            elif st == 'reply_confirm':
                self._do_send(message.chat.id, uid, state['reply_to'], state['subject'], state['body'])
            
            elif st == 'forward_to':
                if not self.sec.validate_email(text):
                    self.bot.reply_to(message, "❌ صيغة غير صحيحة. أعد المحاولة أو /cancel")
                    return
                self.set_state(uid, 'forward_confirm', to=text, subject=state['subject'])
                self._show_compose_confirm(message.chat.id, uid)
            
            elif st == 'forward_confirm':
                self._do_send(message.chat.id, uid, state['to'], state['subject'], state.get('body', ''))
            
            elif st == 'compose_to':
                if not self.sec.validate_email(text):
                    self.bot.reply_to(message, "❌ صيغة البريد غير صحيحة. أعد المحاولة أو /cancel")
                    return
                self.set_state(uid, 'compose_subject', to=text)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("⏭️ تخطي الموضوع", callback_data="skip_subject"))
                self.bot.send_message(message.chat.id, "📌 أرسل عنوان الرسالة، أو اضغط (تخطي):", reply_markup=markup)
            
            elif st == 'compose_subject':
                self.set_state(uid, 'compose_body', to=state['to'], subject=text)
                self.bot.reply_to(message, "📝 أرسل محتوى الرسالة:")
            
            elif st == 'compose_body':
                self.set_state(uid, 'compose_confirm', to=state['to'], subject=state['subject'], body=text)
                self._show_compose_confirm(message.chat.id, uid)
            
            elif st == 'compose_to_skip_subject':
                # هذه حالة وهمية سنستخدمها للتحقق فقط
                pass
            
            elif st == 'compose_body':
                self.set_state(uid, 'compose_confirm', to=state['to'], subject=state['subject'], body=text)
                self._show_compose_confirm(message.chat.id, uid)
            
            elif st == 'compose_confirm':
                self._do_send(message.chat.id, uid, state['to'], state['subject'], state['body'])
        
        except Exception as e:
            self.bot.reply_to(message, f"❌ خطأ: {str(e)}\nأرسل /cancel للمحاولة مجدداً.")
    
    def _show_compose_confirm(self, chat_id, uid):
        state = self.get_state(uid)
        if not state:
            return
        acc = self.db.get_default_account(uid)
        acc_email = acc['email'] if acc else ''
        
        text = (
            f"📋 <b>معاينة الرسالة</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📮 من: {acc_email}\n"
            f"📧 إلى: {state.get('to') or state.get('reply_to')}\n"
            f"📌 الموضوع: {state.get('subject')}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📝 {state.get('body', '')[:500]}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"هل تريد الإرسال؟"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            make_btn("✅ إرسال", "compose_confirm", "success"),
            make_btn("❌ إلغاء", "compose_cancel", "danger"),
        )
        self.bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=markup)
    
    def _do_send(self, chat_id, uid, to, subject, body):
        state = self.get_state(uid)
        if state and state.get('sending'):
            self.bot.send_message(chat_id, "⏳ جاري الإرسال...")
            return
        
        if state:
            state['sending'] = True
        
        acc_id = state.get('account_id') if state else None
        if acc_id:
            acc = self.db.get_account(acc_id)
        else:
            acc = self.db.get_default_account(uid)
            
        if not acc:
            self.bot.send_message(chat_id, "❌ لا يوجد حساب.")
            self.clear_state(uid)
            return
        
        ok, msg, info = self.msg_mgr.send_email(acc['id'], to, subject, body)
        self.db.log_action(uid, 'إرسال رسالة', f"إلى: {to}")
        self.clear_state(uid)
        
        if ok:
            text = (
                f"✅ <b>تم قبول الرسالة</b>\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"📧 {info['recipient']}\n"
                f"📌 {info['subject']}\n"
                f"📮 {info['account']}\n"
                f"📅 {info['date']}\n"
                f"📊 {info['status']}\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"⚠️ <i>قبول SMTP فقط، ليس تأكيد تسليم نهائي.</i>"
            )
        else:
            text = f"❌ <b>فشل الإرسال</b>\n\n{msg}"
        
        self.bot.send_message(chat_id, text, parse_mode='HTML')
        self._send_main_menu(chat_id, uid)
    
    def _send_main_menu(self, chat_id, uid):
        text, markup = self.ui.main_menu(uid)
        self.bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=markup)
    
    def _send_search_results(self, chat_id, uid, query, page):
        acc = self.db.get_default_account(uid)
        if not acc:
            return
        results, total = self.db.get_messages(acc['id'], 'INBOX', page, 5, query)
        
        text = f"🔍 <b>نتائج البحث:</b> {query}\n"
        text += f"📊 النتائج: {total} | صفحة {page + 1}\n━━━━━━━━━━━━━━━━━\n\n"
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for r in results:
            markup.add(types.InlineKeyboardButton(
                f"📧 {r['subject'][:35]}",
                callback_data=f"msg_view_{r['id']}"
            ))
        
        nav = []
        if page > 0:
            nav.append(types.InlineKeyboardButton("◀️", callback_data=f"search_page_{page-1}"))
        if (page + 1) * 5 < total:
            nav.append(types.InlineKeyboardButton("▶️", callback_data=f"search_page_{page+1}"))
        if nav:
            markup.row(*nav)
        
        markup.add(make_btn("🔙 القائمة", "menu_main", "danger"))
        self.bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=markup)

# ============================================================
# Main Application
# ============================================================


def run_health_check():
    import threading
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return "OK", 200
        
    def run():
        port = int(os.environ.get("PORT", 8080))
        app.run(host="0.0.0.0", port=port)
        
    threading.Thread(target=run, daemon=True).start()

def start_wa_bridge():
    import subprocess, os, time
    # إنشاء ملف wa.js تلقائياً إذا لم يكن موجوداً
    if not os.path.exists('wa.js'):
        js_content = r"""
const express = require('express');
const fs = require('fs');
const path = require('path');
const pino = require('pino');
const { default: makeWASocket, useMultiFileAuthState, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');

const AUTH_DIR = path.join(__dirname, 'auth_info');
const PORT = 3000;
const logger = pino({ level: 'silent' });
let sock = null;
let waReady = false;

async function startWA() {
  try {
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: undefined }));
    sock = makeWASocket({ auth: state, version, logger, browser: ['Ubuntu', 'Chrome', '20.0.04'], markOnlineOnConnect: false });
    sock.ev.on('creds.update', saveCreds);
    
    if (!state.creds.registered) {
      setTimeout(async () => {
        try {
          const code = await sock.requestPairingCode('972567860000');
          console.log('🔑 كود الربط:', code);
        } catch (e) { console.error('خطأ طلب الكود:', e.message); }
      }, 3000);
    }
    
    sock.ev.on('connection.update', async (update) => {
      const { connection } = update;
      if (connection === 'open') { 
          waReady = true; 
          console.log('✅ واتساب متصل'); 
          // إرسال رسالة تجريبية
          try {
              await sock.sendMessage('972567860000@s.whatsapp.net', { text: '✅ تم تشغيل البوت وربط واتساب بنجاح (رسالة تجريبية).' });
              console.log('📩 تم إرسال رسالة تجريبية');
          } catch (e) { console.log('فشل إرسال الرسالة:', e.message); }
      }
      if (connection === 'close') { 
          waReady = false; 
          console.log('❌ انقطع الاتصال، جاري إعادة المحاولة...'); 
          setTimeout(startWA, 3000); 
      }
    });
  } catch (e) { setTimeout(startWA, 5000); }
}

const app = express();
app.use(express.json());
app.post('/check', async (req, res) => {
  if (!waReady || !sock) return res.status(503).json({ ok: false, error: 'غير متصل' });
  try {
    const phone = String(req.body.phone || '').replace(/\D/g, '');
    const results = await sock.onWhatsApp(`${phone}@s.whatsapp.net`);
    const r = results?.[0];
    res.json({ ok: true, exists: !!r?.exists });
  } catch (e) { res.status(500).json({ ok: false, error: e.message }); }
});
app.listen(PORT, '127.0.0.1');
startWA();
"""
        with open('wa.js', 'w', encoding='utf-8') as f:
            f.write(js_content)
    
    if os.path.exists('auth_info.tar.gz') and not os.path.exists('auth_info'):
        os.system('tar -xzf auth_info.tar.gz')
    
    print("🌐 جاري تشغيل جسر الواتساب الداخلي...")
    subprocess.Popen(['node', 'wa.js'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4)

def main():
    import os
    if os.path.exists('auth_info.tar.gz') and not os.path.exists('auth_info'):
        os.system('tar -xzf auth_info.tar.gz')
        print("✅ تم فك ضغط الجلسة بنجاح!")
    
    print("🚀 بدء MATARI MAIL PRO...")
    run_health_check()
    
    config = Config()
    print(f"✅ التكوين: {len(config.authorized_users)} مستخدم مصرح")
    
    db = DatabaseManager(config.db_path)
    print(f"✅ قاعدة البيانات: {config.db_path}")
    
    sec = SecurityManager(config.encryption_key)
    imap = IMAPManager(config.imap_timeout)
    smtp = SMTPManager(config.smtp_timeout)
    
    acc_mgr = AccountManager(db, sec, imap, smtp)
    msg_mgr = MessageManager(db, imap, smtp, sec)
    
    bot = telebot.TeleBot(config.bot_token, parse_mode='HTML')
    print("✅ بوت تيليجرام جاهز")
    
    ui = TelegramUI(bot, db, config)
    cb_mgr = CallbackManager(bot, db, config, sec, ui, acc_mgr, msg_mgr, imap, smtp)
    
    # مدير الإشعارات
    notif_mgr = NotificationManager(db, bot, imap, sec)
    notif_mgr.start()
    print("✅ مدير الإشعارات يعمل")
    
    # ========== Handlers ==========
    
    @bot.message_handler(commands=['start'])
    def cmd_start(message):
        if not ui.check_auth(message):
            return
        uid = message.from_user.id
        cb_mgr.clear_state(uid)
        text, markup = ui.main_menu(uid)
        bot.send_message(message.chat.id, text, reply_markup=markup)
    
    @bot.message_handler(commands=['cancel'])
    def cmd_cancel(message):
        if not ui.check_auth(message):
            return
        uid = message.from_user.id
        cb_mgr.clear_state(uid)
        bot.reply_to(message, "✅ تم الإلغاء.")
        text, markup = ui.main_menu(uid)
        bot.send_message(message.chat.id, text, reply_markup=markup)
    
    @bot.message_handler(commands=['help'])
    def cmd_help(message):
        if not ui.check_auth(message):
            return
        text = (
            "❓ <b>مساعدة MATARI MAIL PRO</b>\n\n"
            "استخدم /start لفتح القائمة الرئيسية.\n"
            "استخدم /cancel لإلغاء أي عملية.\n\n"
            "جميع الميزات متاحة من خلال الأزرار."
        )
        bot.reply_to(message, text)
    
    @bot.message_handler(content_types=['text'])
    def handle_text(message):
        if not ui.check_auth(message):
            return
        uid = message.from_user.id
        state = cb_mgr.get_state(uid)
        if state:
            cb_mgr.handle_text(message)
        else:
            # إذا لم تكن هناك حالة، أظهر القائمة
            text, markup = ui.main_menu(uid)
            bot.send_message(message.chat.id, text, reply_markup=markup)
    
    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback(call):
        cb_mgr.handle_callback(call)
    
    # ========== Start Polling ==========
    print("🔄 البوت يستمع للرسائل...")
    try:
        bot.infinity_polling(timeout=30, long_polling_timeout=20)
    except KeyboardInterrupt:
        print("\n👋 إيقاف البوت...")
        notif_mgr.stop()
        db.close()
    except Exception as e:
        print(f"❌ خطأ: {e}")
        notif_mgr.stop()
        db.close()
        sys.exit(1)


if __name__ == '__main__':
    main()


