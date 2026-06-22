import logging
import re
import time
import sqlite3
import random
import string
from datetime import datetime, timedelta
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
from bs4 import BeautifulSoup
import cloudscraper
import config
import json

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
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_creation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                last_attempt DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'idle'
            )
        ''')
        self.conn.commit()
        self.cursor.execute("SELECT COUNT(*) FROM account_creation_log")
        if self.cursor.fetchone()[0] == 0:
            self.cursor.execute("INSERT INTO account_creation_log (last_attempt, status) VALUES (?, ?)", 
                               (datetime.now() - timedelta(days=1), 'idle'))
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
    
    def can_create_account(self):
        self.cursor.execute("SELECT last_attempt, status FROM account_creation_log ORDER BY id DESC LIMIT 1")
        result = self.cursor.fetchone()
        if not result:
            return True
        last_attempt = datetime.fromisoformat(result[0])
        status = result[1]
        if status == 'success':
            return False
        if datetime.now() - last_attempt < timedelta(hours=24):
            return False
        return True
    
    def log_creation_attempt(self, status):
        self.cursor.execute("UPDATE account_creation_log SET last_attempt = ?, status = ?", 
                           (datetime.now().isoformat(), status))
        self.conn.commit()
    
    def get_creation_status(self):
        self.cursor.execute("SELECT last_attempt, status FROM account_creation_log ORDER BY id DESC LIMIT 1")
        result = self.cursor.fetchone()
        if result:
            return {
                'last_attempt': datetime.fromisoformat(result[0]),
                'status': result[1]
            }
        return None


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
    
    def get_verification_code(self, max_wait=120):
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
        self.scraper = cloudscraper.create_scraper()
        self.ua = UserAgent()
    
    def get_profile_info(self, username):
        """جلب البروفايل باستخدام cloudscraper + BeautifulSoup"""
        try:
            headers = {
                'User-Agent': self.ua.random,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'DNT': '1',
                'Upgrade-Insecure-Requests': '1'
            }
            
            url = f"https://www.instagram.com/{username}/"
            response = self.scraper.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'lxml')
                
                # استخراج البيانات من script
                scripts = soup.find_all('script')
                for script in scripts:
                    if script.string and 'window._sharedData' in script.string:
                        match = re.search(r'window\._sharedData = (.*?);</script>', str(script))
                        if match:
                            try:
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
                                        'profile_pic_url': user_data.get('profile_pic_url_hd', user_data.get('profile_pic_url', '')),
                                        'is_private': user_data.get('is_private', False),
                                        'is_verified': user_data.get('is_verified', False)
                                    }
                            except:
                                pass
                
                # محاولة استخراج الصورة من HTML
                img_tag = soup.find('img', {'alt': username})
                if img_tag:
                    profile_pic = img_tag.get('src', '')
            
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
        
        username = f"{first.lower()}{random.randint(100, 999)}{random.randint(10, 99)}"
        password = ''.join(random.choices(string.ascii_letters + string.digits + "!@#$%", k=12))
        email = f"{username}_{random.randint(1000, 9999)}@gmail.com"
        
        return {
            'username': username,
            'password': password,
            'email': email,
            'full_name': f"{first} {last}"
        }
    
    def create_account_with_email(self):
        try:
            if not db.can_create_account():
                status = db.get_creation_status()
                if status:
                    wait_time = 24 - (datetime.now() - status['last_attempt']).seconds // 3600
                    return None, f"⚠️ يرجى الانتظار {wait_time} ساعة"
            
            data = self.generate_random_data()
            
            temp_email = TempEmail()
            if not temp_email.create():
                db.log_creation_attempt('failed')
                return None, "فشل إنشاء البريد المؤقت"
            
            email = temp_email.email
            
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
            email_input.send_keys(email)
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
            
            code = temp_email.get_verification_code(max_wait=120)
            
            if not code:
                driver.quit()
                db.log_creation_attempt('failed')
                return None, "لم يتم استلام كود التفعيل"
            
            try:
                code_input = wait.until(EC.presence_of_element_located((By.NAME, "code")))
                code_input.send_keys(code)
                time.sleep(1)
                
                verify_button = driver.find_element(By.XPATH, "//button[@type='submit']")
                verify_button.click()
                time.sleep(5)
                
                db.save_account(data['username'], data['password'], email)
                db.log_creation_attempt('success')
                
                driver.quit()
                return data, "✅ تم إنشاء الحساب وتفعيله!"
                
            except Exception as e:
                driver.quit()
                db.log_creation_attempt('failed')
                return None, f"فشل إدخال الكود"
            
        except Exception as e:
            logger.error(f"❌ فشل إنشاء الحساب: {e}")
            db.log_creation_attempt('failed')
            return None, str(e)


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


profile_fetcher = InstagramProfile()
reporter = AutoReporter()
creator = InstagramCreator()


def start(update, context):
    keyboard = [
        [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
        [InlineKeyboardButton("🔍 تحقق من حساب", callback_data="check_profile")],
        [InlineKeyboardButton("🔧 إنشاء حساب مفعل", callback_data="create_accounts")],
        [InlineKeyboardButton("📤 عرض حساب عشوائي", callback_data="random_account")],
        [InlineKeyboardButton("📋 حساباتي", callback_data="my_accounts")],
        [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")]
    ]
    
    update.message.reply_text(
        "👋 *مرحباً بك في بوت Nasser HC!*\n\n"
        "📌 *اختر أحد الأزرار:*",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


def report_start(update, context):
    query = update.callback_query
    query.answer()
    context.user_data['step'] = 'report_target'
    query.edit_message_text(
        "✏️ *أرسل اسم المستخدم المستهدف:*",
        parse_mode='Markdown'
    )


def check_profile_start(update, context):
    query = update.callback_query
    query.answer()
    context.user_data['step'] = 'check_profile'
    query.edit_message_text(
        "✏️ *أرسل اسم المستخدم للتحقق:*",
        parse_mode='Markdown'
    )


def create_accounts(update, context):
    query = update.callback_query
    query.answer()
    
    if not db.can_create_account():
        status = db.get_creation_status()
        if status:
            wait_time = 24 - (datetime.now() - status['last_attempt']).seconds // 3600
            query.edit_message_text(
                f"⚠️ *انتظر {wait_time} ساعة*",
                parse_mode='Markdown'
            )
            return
    
    query.edit_message_text(
        "🔄 *جاري إنشاء حساب مفعل...*\n⏳ 2-5 دقائق",
        parse_mode='Markdown'
    )
    
    account, msg = creator.create_account_with_email()
    
    if account:
        text = f"✅ *تم إنشاء حساب مفعل!*\n\n"
        text += f"👤 `{account['username']}`\n"
        text += f"🔑 `{account['password']}`\n"
        text += f"📧 `{account['email']}`"
        
        query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 حساباتي", callback_data="my_accounts")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )
    else:
        query.edit_message_text(
            f"❌ *فشل*\n{msg}",
            parse_mode='Markdown'
        )


def random_account(update, context):
    query = update.callback_query
    query.answer()
    accounts = db.get_accounts()
    if not accounts:
        query.edit_message_text("📭 *لا توجد حسابات*", parse_mode='Markdown')
        return
    account = random.choice(accounts)
    query.edit_message_text(
        f"🎲 *حساب عشوائي*\n\n👤 `{account[0]}`\n🔑 `{account[1]}`\n📧 `{account[2]}`",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎲 عرض آخر", callback_data="random_account")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
        ])
    )


def my_accounts(update, context):
    query = update.callback_query
    query.answer()
    accounts = db.get_accounts()
    if not accounts:
        query.edit_message_text("📭 *لا توجد حسابات*", parse_mode='Markdown')
        return
    text = "📋 *حساباتي*\n\n"
    for i, acc in enumerate(accounts[:10], 1):
        text += f"{i}. 👤 `{acc[0]}`\n"
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
        text += f"{status} #{report[0]} - @{report[2]}\n"
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
    text = re.sub(r'[^a-zA-Z0-9_.]', '', text)
    return text


def show_profile(update, context, target):
    profile = profile_fetcher.get_profile_info(target)
    if not profile:
        update.message.reply_text("❌ *مفيش حساب*", parse_mode='Markdown')
        return
    
    text = f"📸 *معلومات البروفايل*\n\n"
    text += f"👤 @{profile['username']}\n"
    text += f"📛 {profile['full_name']}\n"
    text += f"📝 {profile['bio'][:100]}\n"
    text += f"👥 {profile['follower_count']:,}\n"
    text += f"📌 {profile['following_count']:,}\n"
    text += f"📷 {profile['media_count']:,}\n"
    
    keyboard = [
        [InlineKeyboardButton("✅ أرسل بلاغات", callback_data=f"confirm_{target}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_report")]
    ]
    update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))


def handle_message(update, context):
    text = update.message.text
    step = context.user_data.get('step')
    
    if step == 'report_target':
        target = extract_username(text)
        if not target or len(target) < 3:
            update.message.reply_text("❌ *اسم غير صالح*", parse_mode='Markdown')
            return
        show_profile(update, context, target)
        context.user_data['step'] = 'confirm_report'
    
    elif step == 'check_profile':
        target = extract_username(text)
        if not target or len(target) < 3:
            update.message.reply_text("❌ *اسم غير صالح*", parse_mode='Markdown')
            return
        profile = profile_fetcher.get_profile_info(target)
        if profile:
            text = f"📸 *معلومات البروفايل*\n\n"
            text += f"👤 @{profile['username']}\n"
            text += f"📛 {profile['full_name']}\n"
            text += f"📝 {profile['bio'][:100]}\n"
            text += f"👥 {profile['follower_count']:,}\n"
            text += f"📌 {profile['following_count']:,}\n"
            text += f"📷 {profile['media_count']:,}"
            update.message.reply_text(text, parse_mode='Markdown')
        else:
            update.message.reply_text("❌ *مفيش حساب*", parse_mode='Markdown')
        context.user_data.clear()


def confirm_report(update, context):
    query = update.callback_query
    target = query.data.replace("confirm_", "")
    query.answer()
    
    query.edit_message_text(
        f"🎯 *المستهدف:* @{target}\n\n🔄 *جاري الإرسال...*",
        parse_mode='Markdown'
    )
    
    results = reporter.report_user(target)
    success = sum(1 for r in results if r.get('status') == 'success')
    failed = len(results) - success
    
    report_id = db.add_report(query.from_user.id, query.from_user.username or "N/A", target)
    
    response = f"✅ *اكتمل!*\n\n"
    response += f"📋 #{report_id}\n"
    response += f"🎯 @{target}\n"
    response += f"✅ نجح: {success}\n"
    response += f"❌ فشل: {failed}"
    
    query.edit_message_text(
        response,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
        ])
    )
    context.user_data.clear()


def cancel_report(update, context):
    query = update.callback_query
    query.answer()
    context.user_data.clear()
    query.edit_message_text("❌ *تم الإلغاء*", parse_mode='Markdown')


def back_handler(update, context):
    query = update.callback_query
    query.answer()
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
        [InlineKeyboardButton("🔍 تحقق من حساب", callback_data="check_profile")],
        [InlineKeyboardButton("🔧 إنشاء حساب مفعل", callback_data="create_accounts")],
        [InlineKeyboardButton("📤 عرض حساب عشوائي", callback_data="random_account")],
        [InlineKeyboardButton("📋 حساباتي", callback_data="my_accounts")],
        [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")]
    ]
    query.edit_message_text(
        "🤖 *القائمة الرئيسية*",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


def callback_handler(update, context):
    query = update.callback_query
    data = query.data
    
    if data == "report":
        report_start(update, context)
    elif data == "check_profile":
        check_profile_start(update, context)
    elif data == "create_accounts":
        create_accounts(update, context)
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
        query.answer("❌")


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
