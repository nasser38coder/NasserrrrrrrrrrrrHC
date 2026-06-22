import logging
import re
import time
import sqlite3
import random
import string
from datetime import datetime
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters
from fake_useragent import UserAgent
from instagrapi import Client as InstaClient
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import config

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


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
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                password TEXT,
                email TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()
    
    def add_report(self, user_id, username, target):
        self.cursor.execute("INSERT INTO reports (user_id, username, target) VALUES (?, ?, ?)", (user_id, username, target))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def save_temp_email(self, email, token):
        self.cursor.execute("INSERT INTO temp_emails (email, token) VALUES (?, ?)", (email, token))
        self.conn.commit()
    
    def save_account(self, username, password, email):
        self.cursor.execute("INSERT INTO accounts (username, password, email) VALUES (?, ?, ?)", (username, password, email))
        self.conn.commit()
    
    def get_accounts(self):
        self.cursor.execute("SELECT username, password, email FROM accounts ORDER BY created_at DESC")
        return self.cursor.fetchall()
    
    def get_reports(self, user_id=None):
        if user_id:
            self.cursor.execute("SELECT * FROM reports WHERE user_id = ? ORDER BY timestamp DESC", (user_id,))
        else:
            self.cursor.execute("SELECT * FROM reports ORDER BY timestamp DESC LIMIT 20")
        return self.cursor.fetchall()


db = Database()


class TempEmail:
    def __init__(self):
        self.email = None
        self.token = None
    
    def create(self):
        try:
            url = config.TEMP_EMAIL_API
            params = {'f': 'get_email_address', 'ip': '127.0.0.1', 'agent': 'Mozilla/5.0'}
            response = requests.get(url, params=params)
            data = response.json()
            if data.get('email_addr'):
                self.email = data['email_addr']
                self.token = data.get('sid_token')
                db.save_temp_email(self.email, self.token)
                return True
            return False
        except:
            return False
    
    def check_inbox(self):
        try:
            url = config.TEMP_EMAIL_API
            params = {'f': 'get_email_list', 'sid_token': self.token, 'seq': 0}
            response = requests.get(url, params=params)
            data = response.json()
            return data.get('list', [])
        except:
            return []
    
    def get_verification_code(self, max_wait=90):
        start = time.time()
        while time.time() - start < max_wait:
            emails = self.check_inbox()
            for email in emails:
                body = email.get('mail_body', '')
                subject = email.get('mail_subject', '')
                match = re.search(r'\b\d{6}\b', body + subject)
                if match:
                    return match.group()
            time.sleep(5)
        return None


class InstagramProfile:
    def __init__(self):
        self.ua = UserAgent()
    
    def get_profile_info(self, username):
        try:
            url = f"https://www.instagram.com/{username}/?__a=1&__d=dis"
            headers = {
                'User-Agent': self.ua.random,
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Connection': 'keep-alive',
                'DNT': '1'
            }
            response = requests.get(url, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                user_data = data.get('graphql', {}).get('user', {})
                if user_data:
                    return {
                        'username': user_data.get('username', 'N/A'),
                        'full_name': user_data.get('full_name', 'N/A'),
                        'bio': user_data.get('biography', 'لا يوجد'),
                        'follower_count': user_data.get('edge_followed_by', {}).get('count', 0),
                        'following_count': user_data.get('edge_follow', {}).get('count', 0),
                        'media_count': user_data.get('edge_owner_to_timeline_media', {}).get('count', 0),
                        'profile_pic_url': user_data.get('profile_pic_url_hd', ''),
                        'is_private': user_data.get('is_private', False),
                        'is_verified': user_data.get('is_verified', False)
                    }
            
            response = requests.get(f"https://www.instagram.com/{username}/", headers=headers)
            if response.status_code == 200:
                import json
                match = re.search(r'window\._sharedData = (.*?);</script>', response.text)
                if match:
                    data = json.loads(match.group(1))
                    user_data = data.get('entry_data', {}).get('ProfilePage', [{}])[0].get('graphql', {}).get('user', {})
                    if user_data:
                        return {
                            'username': user_data.get('username', 'N/A'),
                            'full_name': user_data.get('full_name', 'N/A'),
                            'bio': user_data.get('biography', 'لا يوجد'),
                            'follower_count': user_data.get('edge_followed_by', {}).get('count', 0),
                            'following_count': user_data.get('edge_follow', {}).get('count', 0),
                            'media_count': user_data.get('edge_owner_to_timeline_media', {}).get('count', 0),
                            'profile_pic_url': user_data.get('profile_pic_url_hd', ''),
                            'is_private': user_data.get('is_private', False),
                            'is_verified': user_data.get('is_verified', False)
                        }
            return None
        except Exception as e:
            logger.error(f"❌ فشل جلب البروفايل: {e}")
            return None


class InstagramCreator:
    def __init__(self):
        self.ua = UserAgent()
    
    def generate_random_data(self):
        first_names = ['Ahmed', 'Mohamed', 'Sara', 'Nora', 'Omar', 'Layla', 'Khalid', 'Nadia', 'Youssef', 'Amina']
        last_names = ['Ali', 'Hassan', 'Khalid', 'Saeed', 'Omar', 'Nasser', 'Ibrahim', 'Sultan', 'Rashid', 'Salem']
        first = random.choice(first_names)
        last = random.choice(last_names)
        username = f"{first.lower()}{random.randint(100, 999)}"
        password = ''.join(random.choices(string.ascii_letters + string.digits + "!@#$%", k=12))
        email = f"{username}_{random.randint(1000, 9999)}@gmail.com"
        return {'username': username, 'password': password, 'email': email, 'full_name': f"{first} {last}"}
    
    def create_account(self):
        try:
            data = self.generate_random_data()
            
            options = webdriver.ChromeOptions()
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--headless')
            options.add_argument(f'--user-agent={self.ua.random}')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            options.add_argument('--window-size=1920,1080')
            
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            driver.get("https://www.instagram.com/accounts/emailsignup/")
            time.sleep(3)
            wait = WebDriverWait(driver, 10)
            
            email_input = wait.until(EC.presence_of_element_located((By.NAME, "emailOrPhone")))
            email_input.send_keys(data['email'])
            time.sleep(1)
            name_input = driver.find_element(By.NAME, "fullName")
            name_input.send_keys(data['full_name'])
            time.sleep(1)
            username_input = driver.find_element(By.NAME, "username")
            username_input.send_keys(data['username'])
            time.sleep(1)
            password_input = driver.find_element(By.NAME, "password")
            password_input.send_keys(data['password'])
            time.sleep(1)
            signup_button = driver.find_element(By.XPATH, "//button[@type='submit']")
            signup_button.click()
            time.sleep(5)
            driver.quit()
            
            db.save_account(data['username'], data['password'], data['email'])
            return data
        except Exception as e:
            logger.error(f"❌ فشل إنشاء الحساب: {e}")
            return None


class AutoReporter:
    def report_user(self, target_username):
        results = []
        accounts = db.get_accounts()
        if not accounts:
            return [{'status': 'error', 'message': 'لا توجد حسابات مخزنة'}]
        
        for acc in accounts:
            try:
                client = InstaClient()
                client.login(acc[0], acc[1])
                target_id = client.user_id_from_username(target_username)
                client.report_user(target_id)
                results.append({'account': acc[0], 'target': target_username, 'status': 'success'})
                client.logout()
            except Exception as e:
                results.append({'account': acc[0], 'target': target_username, 'status': 'failed', 'error': str(e)})
            time.sleep(random.randint(3, 7))
        return results


creator = InstagramCreator()
reporter = AutoReporter()
profile_fetcher = InstagramProfile()


def start(update, context):
    keyboard = [
        [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
        [InlineKeyboardButton("📧 بريد مؤقت", callback_data="temp_email")],
        [InlineKeyboardButton("🔧 إنشاء حساب إنستغرام", callback_data="create_instagram")],
        [InlineKeyboardButton("📤 عرض حساب عشوائي", callback_data="random_account")],
        [InlineKeyboardButton("📋 حساباتي", callback_data="my_accounts")],
        [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")]
    ]
    
    update.message.reply_text(
        "👋 *مرحباً بك في بوت Nasser HC Pro!*\n\n"
        "🤖 *الميزات:*\n"
        "• 📝 إرسال بلاغات تلقائية\n"
        "• 📧 إنشاء بريد مؤقت\n"
        "• 🔧 إنشاء حساب إنستغرام\n"
        "• 📤 عرض حساب عشوائي\n"
        "• 📋 عرض الحسابات المخزنة\n"
        "• 📊 متابعة بلاغاتك\n\n"
        "📌 *اختر أحد الأزرار:*",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


def report_start(update, context):
    query = update.callback_query
    query.answer()
    context.user_data['step'] = 'report_target'
    query.edit_message_text(
        "✏️ *أرسل رابط الحساب أو اسم المستخدم:*\n"
        "(مثال: https://instagram.com/username أو @username)",
        parse_mode='Markdown'
    )


def temp_email(update, context):
    query = update.callback_query
    query.answer()
    email = TempEmail()
    if email.create():
        context.user_data['temp_email'] = email
        query.edit_message_text(
            f"📧 *تم إنشاء بريدك المؤقت!*\n\n📨 البريد: `{email.email}`\n\n⏳ جاري انتظار كود التفعيل...",
            parse_mode='Markdown'
        )
        code = email.get_verification_code(max_wait=90)
        if code:
            query.edit_message_text(
                f"✅ *تم استلام كود التفعيل!*\n\n🔑 الكود: `{code}`\n\n📨 البريد: `{email.email}`",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
                    [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
                ])
            )
        else:
            query.edit_message_text(
                f"❌ *لم يتم استلام كود التفعيل*\n\n📨 البريد: `{email.email}`\n🔗 https://www.guerrillamail.com/inbox/{email.token}",
                parse_mode='Markdown'
            )
    else:
        query.edit_message_text("❌ *فشل إنشاء البريد*", parse_mode='Markdown')


def create_instagram_account(update, context):
    query = update.callback_query
    query.answer()
    query.edit_message_text("🔄 *جاري إنشاء حساب إنستغرام...*\n⏳ قد يستغرق بضع دقائق", parse_mode='Markdown')
    account = creator.create_account()
    if account:
        query.edit_message_text(
            f"✅ *تم إنشاء الحساب!*\n\n👤 المستخدم: `{account['username']}`\n🔑 كلمة المرور: `{account['password']}`\n📧 البريد: `{account['email']}`",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 عرض الحسابات", callback_data="my_accounts")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )
    else:
        query.edit_message_text("❌ *فشل إنشاء الحساب*", parse_mode='Markdown')


def random_account(update, context):
    query = update.callback_query
    query.answer()
    accounts = db.get_accounts()
    if not accounts:
        query.edit_message_text("📭 *لا توجد حسابات مخزنة*", parse_mode='Markdown')
        return
    account = random.choice(accounts)
    query.edit_message_text(
        f"🎲 *حساب عشوائي*\n\n👤 المستخدم: `{account[0]}`\n🔑 كلمة المرور: `{account[1]}`\n📧 البريد: `{account[2]}`",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎲 عرض آخر", callback_data="random_account")],
            [InlineKeyboardButton("📋 عرض الكل", callback_data="my_accounts")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
        ])
    )


def my_accounts(update, context):
    query = update.callback_query
    query.answer()
    accounts = db.get_accounts()
    if not accounts:
        query.edit_message_text("📭 *لا توجد حسابات مخزنة*", parse_mode='Markdown')
        return
    text = "📋 *حساباتي*\n\n"
    for i, acc in enumerate(accounts[:10], 1):
        text += f"{i}. 👤 `{acc[0]}`\n   📧 {acc[2]}\n\n"
    query.edit_message_text(text, parse_mode='Markdown')


def my_reports(update, context):
    query = update.callback_query
    query.answer()
    user_id = update.effective_user.id
    reports = db.get_reports(user_id)
    if not reports:
        query.edit_message_text("📭 *لا توجد بلاغات*", parse_mode='Markdown')
        return
    text = "📊 *بلاغاتي*\n\n"
    for report in reports[:10]:
        status = "✅" if report[3] == "resolved" else "⏳"
        text += f"{status} #{report[0]} - @{report[2]}\n   📅 {report[4]}\n\n"
    query.edit_message_text(text, parse_mode='Markdown')


def extract_username(text):
    text = text.strip()
    if 'instagram.com/' in text:
        parts = text.split('/')
        for part in parts:
            if part and part not in ['http:', 'https:', 'instagram.com', 'www']:
                return part.split('?')[0]
    if text.startswith('@'):
        text = text[1:]
    return text


def show_profile(update, context, target):
    profile = profile_fetcher.get_profile_info(target)
    if not profile:
        update.message.reply_text("❌ *لا يمكن جلب معلومات البروفايل*\nتأكد من صحة اسم المستخدم.", parse_mode='Markdown')
        return
    
    text = f"📸 *معلومات البروفايل*\n\n"
    text += f"👤 *المستخدم:* @{profile['username']}\n"
    text += f"📛 *الاسم:* {profile['full_name']}\n"
    text += f"📝 *البايو:* {profile['bio'][:100]}...\n"
    text += f"👥 *متابعون:* {profile['follower_count']:,}\n"
    text += f"📌 *متابعة:* {profile['following_count']:,}\n"
    text += f"📷 *منشورات:* {profile['media_count']:,}\n"
    text += f"🔒 *خاص:* {'✅' if profile['is_private'] else '❌'}\n"
    text += f"✅ *موثق:* {'✅' if profile['is_verified'] else '❌'}\n"
    if profile.get('profile_pic_url'):
        text += f"\n🖼️ [صورة البروفايل]({profile['profile_pic_url']})"
    
    text += f"\n\n❓ *هل هذا هو الحساب المستهدف؟*"
    
    keyboard = [
        [InlineKeyboardButton("✅ نعم، أرسل بلاغات", callback_data=f"confirm_{target}")],
        [InlineKeyboardButton("❌ لا، خطأ", callback_data="cancel_report")]
    ]
    update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))


def handle_message(update, context):
    text = update.message.text
    step = context.user_data.get('step')
    
    if step == 'report_target':
        target = extract_username(text)
        if not target:
            update.message.reply_text("❌ *اسم مستخدم غير صالح*", parse_mode='Markdown')
            return
        show_profile(update, context, target)
        context.user_data['step'] = 'confirm_report'


def confirm_report(update, context):
    query = update.callback_query
    target = query.data.replace("confirm_", "")
    query.answer()
    
    query.edit_message_text(f"🎯 *المستهدف:* @{target}\n\n🔄 *جاري إرسال البلاغات...*\n⏳ قد يستغرق بضع دقائق", parse_mode='Markdown')
    
    results = reporter.report_user(target)
    success = sum(1 for r in results if r.get('status') == 'success')
    failed = len(results) - success
    
    report_id = db.add_report(query.from_user.id, query.from_user.username or "N/A", target)
    
    response = f"✅ *اكتمل الإرسال!*\n\n📋 رقم البلاغ: `#{report_id}`\n🎯 المستهدف: @{target}\n📊 النتائج:\n• ✅ نجح: {success}\n• ❌ فشل: {failed}\n• 📝 المجموع: {len(results)}"
    if failed > 0:
        response += "\n\n⚠️ *فشل بعض البلاغات*"
    
    query.edit_message_text(
        response,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")],
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back")]
        ])
    )
    context.user_data.clear()


def cancel_report(update, context):
    query = update.callback_query
    query.answer()
    context.user_data.clear()
    query.edit_message_text("❌ *تم إلغاء البلاغ*", parse_mode='Markdown')


def back_handler(update, context):
    query = update.callback_query
    query.answer()
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
        [InlineKeyboardButton("📧 بريد مؤقت", callback_data="temp_email")],
        [InlineKeyboardButton("🔧 إنشاء حساب إنستغرام", callback_data="create_instagram")],
        [InlineKeyboardButton("📤 عرض حساب عشوائي", callback_data="random_account")],
        [InlineKeyboardButton("📋 حساباتي", callback_data="my_accounts")],
        [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")]
    ]
    query.edit_message_text("🤖 *القائمة الرئيسية*\n\nاختر الإجراء المناسب:", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))


def callback_handler(update, context):
    query = update.callback_query
    data = query.data
    
    if data == "report":
        report_start(update, context)
    elif data == "temp_email":
        temp_email(update, context)
    elif data == "create_instagram":
        create_instagram_account(update, context)
    elif data == "random_account":
        random_account(update, context)
    elif data == "my_reports":
        my_reports(update, context)
    elif data == "my_accounts":
        my_accounts(update, context)
    elif data == "back":
        back_handler(update, context)
    elif data == "cancel_report":
        cancel_report(update, context)
    elif data.startswith("confirm_"):
        confirm_report(update, context)
    else:
        query.answer("❌ خيار غير معروف")


def main():
    updater = Updater(config.BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CallbackQueryHandler(callback_handler))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
