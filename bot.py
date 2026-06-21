import logging
import re
import time
import json
import sqlite3
from datetime import datetime
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters
import config

# ================= إعدادات التسجيل =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= قاعدة البيانات =================

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(config.DATABASE_FILE, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
    
    def create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                target TEXT,
                reason TEXT,
                status TEXT DEFAULT 'pending',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS temp_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                token TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()
    
    def add_report(self, user_id, username, target, reason):
        self.cursor.execute(
            "INSERT INTO reports (user_id, username, target, reason) VALUES (?, ?, ?, ?)",
            (user_id, username, target, reason)
        )
        self.conn.commit()
        return self.cursor.lastrowid
    
    def save_temp_email(self, email, token):
        self.cursor.execute(
            "INSERT INTO temp_emails (email, token) VALUES (?, ?)",
            (email, token)
        )
        self.conn.commit()
    
    def get_reports(self):
        self.cursor.execute("SELECT * FROM reports ORDER BY timestamp DESC LIMIT 20")
        return self.cursor.fetchall()

db = Database()

# ================= البريد المؤقت =================

class TempEmail:
    def __init__(self):
        self.email = None
        self.token = None
    
    def create(self):
        try:
            url = config.TEMP_EMAIL_API
            params = {
                'f': 'get_email_address',
                'ip': '127.0.0.1',
                'agent': 'Mozilla/5.0'
            }
            response = requests.get(url, params=params)
            data = response.json()
            
            if data.get('email_addr'):
                self.email = data['email_addr']
                self.token = data.get('sid_token')
                db.save_temp_email(self.email, self.token)
                logger.info(f"✅ تم إنشاء بريد: {self.email}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ فشل إنشاء البريد: {e}")
            return False
    
    def check_inbox(self):
        try:
            url = config.TEMP_EMAIL_API
            params = {
                'f': 'get_email_list',
                'sid_token': self.token,
                'seq': 0
            }
            response = requests.get(url, params=params)
            data = response.json()
            
            if data.get('list'):
                return data['list']
            return []
        except:
            return []
    
    def get_verification_code(self, max_wait=90):
        start = time.time()
        
        while time.time() - start < max_wait:
            emails = self.check_inbox()
            
            if emails:
                for email in emails:
                    body = email.get('mail_body', '')
                    subject = email.get('mail_subject', '')
                    
                    patterns = [r'\b\d{6}\b', r'code:?\s*(\d{6})', r'verification code:?\s*(\d{6})']
                    
                    for pattern in patterns:
                        match = re.search(pattern, body + subject, re.IGNORECASE)
                        if match:
                            code = match.group(1) if match.group(1) else match.group(0)
                            logger.info(f"✅ تم العثور على كود: {code}")
                            return code
            
            time.sleep(5)
        
        return None

# ================= دوال البوت =================

def start(update, context):
    keyboard = [
        [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
        [InlineKeyboardButton("📧 بريد مؤقت", callback_data="temp_email")],
        [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")],
        [InlineKeyboardButton("❓ مساعدة", callback_data="help")]
    ]
    
    update.message.reply_text(
        "👋 *مرحباً بك في بوت Nasser HC!*\n\n"
        "🤖 *الميزات:*\n"
        "• 📝 تسجيل بلاغات عن حسابات مخالفة\n"
        "• 📧 إنشاء بريد مؤقت حقيقي\n"
        "• 📊 متابعة حالة بلاغاتك\n\n"
        "📌 *اختر أحد الأزرار:*",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def help_command(update, context):
    keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back")]]
    
    update.message.reply_text(
        "❓ *المساعدة*\n\n"
        "📝 *تقديم بلاغ:*\n"
        "1. اضغط على 'تقديم بلاغ'\n"
        "2. أرسل اسم المستخدم المستهدف\n"
        "3. أرسل سبب البلاغ للإدارة\n\n"
        "📧 *البريد المؤقت:*\n"
        "1. اضغط على 'بريد مؤقت'\n"
        "2. سيتم إنشاء بريد لك\n"
        "3. استخدمه للتسجيل\n\n"
        "📊 *بلاغاتي:*\n"
        "• عرض جميع بلاغاتك السابقة\n\n"
        "للتواصل: @abdonaser27",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def report_start(update, context):
    query = update.callback_query
    query.answer()
    context.user_data['step'] = 'report_target'
    
    query.edit_message_text(
        "✏️ *أرسل اسم المستخدم المستهدف:*\n"
        "(بدون @)",
        parse_mode='Markdown'
    )

def temp_email(update, context):
    query = update.callback_query
    query.answer()
    
    email = TempEmail()
    
    if email.create():
        context.user_data['temp_email'] = email
        context.user_data['step'] = 'temp_email_waiting'
        
        query.edit_message_text(
            f"📧 *تم إنشاء بريدك المؤقت!*\n\n"
            f"📨 البريد: `{email.email}`\n\n"
            f"⏳ جاري انتظار كود التفعيل...\n"
            f"(سيتم تحديثه تلقائياً)",
            parse_mode='Markdown'
        )
        
        check_code(update, context)
    else:
        query.edit_message_text(
            "❌ *فشل إنشاء البريد*\n"
            "حاول مرة أخرى لاحقاً.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )

def check_code(update, context):
    email = context.user_data.get('temp_email')
    if not email:
        return
    
    code = email.get_verification_code(max_wait=90)
    
    if code:
        context.user_data['verification_code'] = code
        context.user_data['step'] = 'temp_email_code'
        
        update.callback_query.edit_message_text(
            f"✅ *تم استلام كود التفعيل!*\n\n"
            f"🔑 الكود: `{code}`\n\n"
            f"📨 البريد: `{email.email}`\n\n"
            f"💡 استخدم هذا الكود للتسجيل في إنستغرام أو غيره",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )
    else:
        update.callback_query.edit_message_text(
            "❌ *لم يتم استلام كود التفعيل*\n\n"
            f"📨 البريد: `{email.email}`\n\n"
            "💡 يمكنك فتح البريد يدوياً:\n"
            f"🔗 https://www.guerrillamail.com/inbox/{email.token}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 إعادة المحاولة", callback_data="temp_email")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )

def my_reports(update, context):
    query = update.callback_query
    query.answer()
    
    user_id = update.effective_user.id
    reports = db.cursor.execute(
        "SELECT id, target, reason, status, timestamp FROM reports WHERE user_id = ? ORDER BY timestamp DESC",
        (user_id,)
    ).fetchall()
    
    if not reports:
        query.edit_message_text(
            "📭 *لا توجد بلاغات مسجلة*",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )
        return
    
    text = "📊 *سجل بلاغاتي*\n\n"
    for report in reports[:10]:
        status_emoji = "✅" if report[3] == "resolved" else "⏳"
        text += f"{status_emoji} #{report[0]} - @{report[1]}\n"
        text += f"   📝 {report[2][:30]}...\n"
        text += f"   📅 {report[4]}\n\n"
    
    if len(reports) > 10:
        text += f"*و {len(reports)-10} بلاغات أخرى*"
    
    query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
        ])
    )

def handle_message(update, context):
    text = update.message.text
    step = context.user_data.get('step')
    user = update.effective_user
    
    if step == 'report_target':
        context.user_data['target'] = text
        context.user_data['step'] = 'report_reason'
        
        update.message.reply_text(
            f"🎯 المستهدف: @{text}\n\n"
            f"✏️ *أرسل سبب البلاغ للإدارة:*\n"
            f"(مثال: احتيال، مضايقة، محتوى غير لائق)",
            parse_mode='Markdown'
        )
    
    elif step == 'report_reason':
        target = context.user_data.get('target', 'غير معروف')
        reason = text
        
        report_id = db.add_report(
            user.id,
            user.username or "N/A",
            target,
            reason
        )
        
        context.user_data.clear()
        
        update.message.reply_text(
            f"✅ *تم استلام بلاغك وإرساله للإدارة!*\n\n"
            f"📋 رقم البلاغ: `#{report_id}`\n"
            f"🎯 المستهدف: @{target}\n"
            f"📝 السبب: {reason}\n\n"
            f"شكراً لك! 🙏",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")],
                [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back")]
            ])
        )

def back_handler(update, context):
    query = update.callback_query
    query.answer()
    context.user_data.clear()
    
    keyboard = [
        [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
        [InlineKeyboardButton("📧 بريد مؤقت", callback_data="temp_email")],
        [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")],
        [InlineKeyboardButton("❓ مساعدة", callback_data="help")]
    ]
    
    query.edit_message_text(
        "🤖 *القائمة الرئيسية*\n\nاختر الإجراء المناسب:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def callback_handler(update, context):
    query = update.callback_query
    data = query.data
    
    if data == "report":
        report_start(update, context)
    elif data == "temp_email":
        temp_email(update, context)
    elif data == "my_reports":
        my_reports(update, context)
    elif data == "help":
        help_command(update, context)
    elif data == "back":
        back_handler(update, context)
    else:
        query.answer("❌ خيار غير معروف")

def main():
    updater = Updater(config.BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CallbackQueryHandler(callback_handler))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    
    logger.info("🚀 تشغيل بوت Nasser HC...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
