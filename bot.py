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
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
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
        self.cursor.execute(
            "INSERT INTO reports (user_id, username, target) VALUES (?, ?, ?)",
            (user_id, username, target)
        )
        self.conn.commit()
        return self.cursor.lastrowid
    
    def save_temp_email(self, email, token):
        self.cursor.execute(
            "INSERT INTO temp_emails (email, token) VALUES (?, ?)",
            (email, token)
        )
        self.conn.commit()
    
    def save_account(self, username, password, email):
        self.cursor.execute(
            "INSERT INTO accounts (username, password, email) VALUES (?, ?, ?)",
            (username, password, email)
        )
        self.conn.commit()
    
    def get_accounts(self):
        self.cursor.execute("SELECT username, password, email FROM accounts ORDER BY created_at DESC")
        return self.cursor.fetchall()
    
    def delete_account(self, username):
        self.cursor.execute("DELETE FROM accounts WHERE username = ?", (username,))
        self.conn.commit()
    
    def get_reports(self, user_id=None):
        if user_id:
            self.cursor.execute("SELECT * FROM reports WHERE user_id = ? ORDER BY timestamp DESC", (user_id,))
        else:
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

# ================= جلب معلومات البروفايل =================

class InstagramProfile:
    def __init__(self):
        self.ua = UserAgent()
    
    def get_profile_info(self, username):
        """جلب معلومات البروفايل من إنستغرام"""
        try:
            client = InstaClient()
            
            accounts = db.get_accounts()
            if accounts:
                acc = accounts[0]
                client.login(acc[0], acc[1])
                user_id = client.user_id_from_username(username)
                user_info = client.user_info(user_id)
                client.logout()
                
                return {
                    'username': user_info.username,
                    'full_name': user_info.full_name,
                    'bio': user_info.biography,
                    'follower_count': user_info.follower_count,
                    'following_count': user_info.following_count,
                    'media_count': user_info.media_count,
                    'profile_pic_url': user_info.profile_pic_url,
                    'is_private': user_info.is_private,
                    'is_verified': user_info.is_verified
                }
            else:
                return self.get_profile_via_web(username)
                
        except Exception as e:
            logger.error(f"❌ فشل جلب معلومات البروفايل: {e}")
            return None
    
    def get_profile_via_web(self, username):
        """جلب المعلومات عبر الويب (بدون تسجيل دخول)"""
        try:
            url = f"https://www.instagram.com/{username}/"
            headers = {
                'User-Agent': self.ua.random
            }
            response = requests.get(url, headers=headers)
            
            if response.status_code == 200:
                import json
                import re
                
                match = re.search(r'window\._sharedData = (.*?);</script>', response.text)
                if match:
                    data = json.loads(match.group(1))
                    user_data = data['entry_data']['ProfilePage'][0]['graphql']['user']
                    
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
            logger.error(f"❌ فشل جلب المعلومات عبر الويب: {e}")
            return None

# ================= إنشاء حساب إنستغرام =================

class InstagramCreator:
    def __init__(self):
        self.ua = UserAgent()
        self.accounts = []
    
    def generate_random_data(self):
        first_names = ['Ahmed', 'Mohamed', 'Sara', 'Nora', 'Omar', 'Layla', 'Khalid', 'Nadia', 'Youssef', 'Amina']
        last_names = ['Ali', 'Hassan', 'Khalid', 'Saeed', 'Omar', 'Nasser', 'Ibrahim', 'Sultan', 'Rashid', 'Salem']
        
        first = random.choice(first_names)
        last = random.choice(last_names)
        
        username = f"{first.lower()}{random.randint(100, 999)}"
        password = ''.join(random.choices(string.ascii_letters + string.digits + "!@#$%", k=12))
        email = f"{username}_{random.randint(1000, 9999)}@gmail.com"
        
        return {
            'username': username,
            'password': password,
            'email': email,
            'full_name': f"{first} {last}"
        }
    
    def create_account(self):
        try:
            data = self.generate_random_data()
            
            options = uc.ChromeOptions()
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--headless')
            options.add_argument(f'--user-agent={self.ua.random}')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            options.add_argument('--window-size=1920,1080')
            
            driver = uc.Chrome(options=options)
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
            
            logger.info(f"✅ تم إنشاء حساب: {data['username']}")
            return data
            
        except Exception as e:
            logger.error(f"❌ فشل إنشاء الحساب: {e}")
            return None
    
    def create_bulk(self, count=5):
        accounts = []
        for i in range(count):
            logger.info(f"📌 إنشاء حساب {i+1}/{count}")
            account = self.create_account()
            if account:
                accounts.append(account)
            time.sleep(10)
        return accounts

# ================= إرسال البلاغات التلقائي =================

class AutoReporter:
    def __init__(self):
        self.clients = []
    
    def add_account(self, username, password):
        try:
            client = InstaClient()
            client.login(username, password)
            self.clients.append(client)
            logger.info(f"✅ تم إضافة حساب: {username}")
            return client
        except Exception as e:
            logger.error(f"❌ فشل إضافة حساب {username}: {e}")
            return None
    
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
                results.append({
                    'account': acc[0],
                    'target': target_username,
                    'status': 'success'
                })
                logger.info(f"✅ تم الإبلاغ عن {target_username} من {acc[0]}")
                client.logout()
            except Exception as e:
                results.append({
                    'account': acc[0],
                    'target': target_username,
                    'status': 'failed',
                    'error': str(e)
                })
                logger.error(f"❌ فشل الإبلاغ من {acc[0]}: {e}")
            time.sleep(random.randint(3, 7))
        
        return results

# ================= دوال البوت =================

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
        [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")],
        [InlineKeyboardButton("❓ مساعدة", callback_data="help")]
    ]
    
    update.message.reply_text(
        "👋 *مرحباً بك في بوت Nasser HC Pro!*\n\n"
        "🤖 *الميزات:*\n"
        "• 📝 إرسال بلاغات تلقائية عن حسابات مخالفة\n"
        "• 📧 إنشاء بريد مؤقت حقيقي\n"
        "• 🔧 إنشاء حساب إنستغرام تلقائياً\n"
        "• 📤 عرض حساب عشوائي من المخزن\n"
        "• 📋 عرض جميع الحسابات المخزنة\n"
        "• 📊 متابعة بلاغاتك\n\n"
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
        "2. أرسل رابط أو اسم المستخدم\n"
        "3. البوت يعرض معلومات البروفايل\n"
        "4. تأكد من المستهدف ثم يرسل بلاغات تلقائية\n\n"
        "📧 *البريد المؤقت:*\n"
        "• إنشاء بريد مؤقت لاستقبال كود التفعيل\n\n"
        "🔧 *إنشاء حساب إنستغرام:*\n"
        "• ينشئ حساب تلقائياً ببيانات عشوائية\n\n"
        "📤 *حساب عشوائي:*\n"
        "• يعرض حساب عشوائي من المخزن\n\n"
        "📋 *حساباتي:*\n"
        "• عرض جميع الحسابات المخزنة",
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
        context.user_data['step'] = 'temp_email_waiting'
        
        query.edit_message_text(
            f"📧 *تم إنشاء بريدك المؤقت!*\n\n"
            f"📨 البريد: `{email.email}`\n\n"
            f"⏳ جاري انتظار كود التفعيل...",
            parse_mode='Markdown'
        )
        
        check_code(update, context)
    else:
        query.edit_message_text(
            "❌ *فشل إنشاء البريد*",
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
            f"📨 البريد: `{email.email}`",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )
    else:
        update.callback_query.edit_message_text(
            "❌ *لم يتم استلام كود التفعيل*\n\n"
            f"📨 البريد: `{email.email}`\n"
            f"🔗 https://www.guerrillamail.com/inbox/{email.token}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 إعادة المحاولة", callback_data="temp_email")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )

def create_instagram_account(update, context):
    query = update.callback_query
    query.answer()
    
    query.edit_message_text(
        "🔄 *جاري إنشاء حساب إنستغرام...*\n"
        "⏳ قد يستغرق بضع دقائق",
        parse_mode='Markdown'
    )
    
    account = creator.create_account()
    
    if account:
        query.edit_message_text(
            f"✅ *تم إنشاء الحساب!*\n\n"
            f"👤 المستخدم: `{account['username']}`\n"
            f"🔑 كلمة المرور: `{account['password']}`\n"
            f"📧 البريد: `{account['email']}`\n\n"
            f"💡 تم حفظه في قاعدة البيانات",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 عرض الحسابات", callback_data="my_accounts")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )
    else:
        query.edit_message_text(
            "❌ *فشل إنشاء الحساب*\n"
            "حاول مرة أخرى لاحقاً.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 إعادة المحاولة", callback_data="create_instagram")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )

def random_account(update, context):
    query = update.callback_query
    query.answer()
    
    accounts = db.get_accounts()
    
    if not accounts:
        query.edit_message_text(
            "📭 *لا توجد حسابات مخزنة*\n\n"
            "قم بإنشاء حساب أولاً",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔧 إنشاء حساب", callback_data="create_instagram")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )
        return
    
    account = random.choice(accounts)
    
    query.edit_message_text(
        f"🎲 *حساب عشوائي من المخزن*\n\n"
        f"👤 المستخدم: `{account[0]}`\n"
        f"🔑 كلمة المرور: `{account[1]}`\n"
        f"📧 البريد: `{account[2]}`",
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
        query.edit_message_text(
            "📭 *لا توجد حسابات مخزنة*",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔧 إنشاء حساب", callback_data="create_instagram")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )
        return
    
    text = "📋 *حساباتي المخزنة*\n\n"
    for i, acc in enumerate(accounts[:10], 1):
        text += f"{i}. 👤 `{acc[0]}`\n"
        text += f"   📧 {acc[2]}\n\n"
    
    if len(accounts) > 10:
        text += f"*و {len(accounts)-10} حسابات أخرى*"
    
    query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔧 إنشاء جديد", callback_data="create_instagram")],
            [InlineKeyboardButton("🎲 عرض عشوائي", callback_data="random_account")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
        ])
    )

def my_reports(update, context):
    query = update.callback_query
    query.answer()
    
    user_id = update.effective_user.id
    reports = db.get_reports(user_id)
    
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
        text += f"{status_emoji} #{report[0]} - @{report[2]}\n"
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

def extract_username(text):
    text = text.strip()
    
    if 'instagram.com/' in text:
        parts = text.split('/')
        for part in parts:
            if part and not part in ['http:', 'https:', 'instagram.com', 'www']:
                return part.split('?')[0]
    
    if text.startswith('@'):
        text = text[1:]
    
    return text

def show_profile(update, context, target):
    """عرض معلومات البروفايل مع أزرار تأكيد"""
    profile = profile_fetcher.get_profile_info(target)
    
    if not profile:
        update.message.reply_text(
            "❌ *لا يمكن جلب معلومات البروفايل*\n"
            "تأكد من صحة اسم المستخدم أو حاول مرة أخرى.",
            parse_mode='Markdown'
        )
        return
    
    profile_text = f"📸 *معلومات البروفايل*\n\n"
    profile_text += f"👤 *المستخدم:* @{profile['username']}\n"
    profile_text += f"📛 *الاسم:* {profile['full_name']}\n"
    profile_text += f"📝 *البايو:* {profile['bio'][:100]}...\n"
    profile_text += f"👥 *متابعون:* {profile['follower_count']:,}\n"
    profile_text += f"📌 *متابعة:* {profile['following_count']:,}\n"
    profile_text += f"📷 *منشورات:* {profile['media_count']:,}\n"
    profile_text += f"🔒 *حساب خاص:* {'✅' if profile['is_private'] else '❌'}\n"
    profile_text += f"✅ *موثق:* {'✅' if profile['is_verified'] else '❌'}\n"
    
    if profile.get('profile_pic_url'):
        profile_text += f"\n🖼️ [صورة البروفايل]({profile['profile_pic_url']})"
    
    profile_text += f"\n\n❓ *هل هذا هو الحساب المستهدف؟*"
    
    keyboard = [
        [InlineKeyboardButton("✅ نعم، هذا هو", callback_data=f"confirm_report_{target}")],
        [InlineKeyboardButton("❌ لا، خطأ", callback_data="cancel_report")]
    ]
    
    update.message.reply_text(
        profile_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True
    )
    
    context.user_data['target'] = target

def handle_message(update, context):
    text = update.message.text
    step = context.user_data.get('step')
    user = update.effective_user
    
    if step == 'report_target':
        target = extract_username(text)
        
        if not target:
            update.message.reply_text(
                "❌ *اسم مستخدم غير صالح*\n"
                "يرجى إرسال رابط أو اسم مستخدم صحيح.",
                parse_mode='Markdown'
            )
            return
        
        show_profile(update, context, target)
        context.user_data['step'] = 'confirm_report'

def confirm_report(update, context):
    query = update.callback_query
    target = query.data.replace("confirm_report_", "")
    query.answer()
    
    query.edit_message_text(
        f"🎯 *المستهدف:* @{target}\n\n"
        f"🔄 *جاري إرسال البلاغات التلقائية...*\n"
        f"⏳ قد يستغرق بضع دقائق",
        parse_mode='Markdown'
    )
    
    results = reporter.report_user(target)
    
    success = len([r for r in results if r.get('status') == 'success'])
    failed = len([r for r in results if r.get('status') == 'failed'])
    
    report_id = db.add_report(
        query.from_user.id,
        query.from_user.username or "N/A",
        target
    )
    
    response = f"✅ *اكتمل الإرسال!*\n\n"
    response += f"📋 رقم البلاغ: `#{report_id}`\n"
    response += f"🎯 المستهدف: @{target}\n"
    response += f"📊 النتائج:\n"
    response += f"• ✅ نجح: {success}\n"
    response += f"• ❌ فشل: {failed}\n"
    response += f"• 📝 المجموع: {len(results)}"
    
    if failed > 0:
        response += f"\n\n⚠️ *فشل بعض البلاغات*\nقد تكون الحسابات محظورة أو غير نشطة."
    
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
    
    query.edit_message_text(
        "❌ *تم إلغاء البلاغ*",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
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
        [InlineKeyboardButton("🔧 إنشاء حساب إنستغرام", callback_data="create_instagram")],
        [InlineKeyboardButton("📤 عرض حساب عشوائي", callback_data="random_account")],
        [InlineKeyboardButton("📋 حساباتي", callback_data="my_accounts")],
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
    elif data == "create_instagram":
        create_instagram_account(update, context)
    elif data == "random_account":
        random_account(update, context)
    elif data == "my_reports":
        my_reports(update, context)
    elif data == "my_accounts":
        my_accounts(update, context)
    elif data == "help":
        help_command(update, context)
    elif data == "back":
        back_handler(update, context)
    elif data == "cancel_report":
        cancel_report(update, context)
    elif data.startswith("confirm_report_"):
        confirm_report(update, context)
    else:
        query.answer("❌ خيار غير معروف")

def main():
    updater = Updater(config.BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CallbackQueryHandler(callback_handler))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    
    logger.info("🚀 تشغيل بوت Nasser HC Pro...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
