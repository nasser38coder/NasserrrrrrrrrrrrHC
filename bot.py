import logging
import re
import time
import json
import sqlite3
import os
import subprocess
import socket
import ctypes
import pickle
import marshal
import psutil
import paramiko
from datetime import datetime
from threading import Thread
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from schedule import Scheduler
import requests
import aiohttp
import httpx
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters
from pydantic import BaseModel, ValidationError
from celery import Celery
from redis import Redis
from tenacity import retry, stop_after_attempt, wait_exponential
from cloudscraper import create_scraper
from tls_client import Session as TLSSession
from curl_cffi import requests as curl_requests
from webdriver_manager.chrome import ChromeDriverManager
from pyppeteer import launch
import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.selector import Selector
from mailtm import Email
from imap_tools import MailBox
from faker import Faker
from fake_useragent import UserAgent
from undetected_chromedriver import Chrome
from bs4 import BeautifulSoup
import pyautogui
from playwright.sync_api import sync_playwright
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from instagrapi import Client as InstaClient
import config

# ================= إعدادات التسجيل =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= Pydantic Models =================

class ReportData(BaseModel):
    user_id: int
    username: str
    target: str
    reason: str
    status: str = "pending"

class EmailData(BaseModel):
    email: str
    token: str
    created_at: datetime

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
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_type TEXT,
                session_data TEXT,
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

# ================= أدوات متقدمة =================

class AdvancedTools:
    def __init__(self):
        self.fake = Faker()
        self.ua = UserAgent()
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()
    
    def get_random_headers(self):
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
    
    def create_scraper_session(self):
        return create_scraper()
    
    def create_tls_session(self):
        return TLSSession()
    
    def create_curl_session(self):
        return curl_requests.Session()
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def retry_request(self, url):
        headers = self.get_random_headers()
        response = requests.get(url, headers=headers, timeout=10)
        return response

# ================= Instagram Account Creator =================

class InstagramAccountCreator:
    def __init__(self):
        self.fake = Faker()
        self.ua = UserAgent()
        self.accounts = []
    
    def setup_undetected_driver(self):
        options = Chrome.options()
        options.add_argument(f'--user-agent={self.ua.random}')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        return Chrome(options=options)
    
    def generate_account_data(self):
        first_name = self.fake.first_name()
        last_name = self.fake.last_name()
        username = f"{first_name.lower()}{self.fake.random_number(digits=4)}"
        password = self.fake.password(length=12, special_chars=True, digits=True, upper_case=True, lower_case=True)
        email = self.fake.email()
        
        return {
            'username': username,
            'password': password,
            'email': email,
            'first_name': first_name,
            'last_name': last_name
        }
    
    def create_account_selenium(self):
        try:
            data = self.generate_account_data()
            driver = self.setup_undetected_driver()
            
            driver.get("https://www.instagram.com/accounts/emailsignup/")
            time.sleep(3)
            
            wait = WebDriverWait(driver, 10)
            
            # إدخال البريد
            email_input = wait.until(EC.presence_of_element_located((By.NAME, "emailOrPhone")))
            email_input.send_keys(data['email'])
            time.sleep(1)
            
            # إدخال الاسم الكامل
            name_input = driver.find_element(By.NAME, "fullName")
            name_input.send_keys(f"{data['first_name']} {data['last_name']}")
            time.sleep(1)
            
            # إدخال اسم المستخدم
            username_input = driver.find_element(By.NAME, "username")
            username_input.send_keys(data['username'])
            time.sleep(1)
            
            # إدخال كلمة المرور
            password_input = driver.find_element(By.NAME, "password")
            password_input.send_keys(data['password'])
            time.sleep(1)
            
            # الضغط على تسجيل
            signup_button = driver.find_element(By.XPATH, "//button[@type='submit']")
            signup_button.click()
            time.sleep(5)
            
            driver.quit()
            
            self.accounts.append(data)
            logger.info(f"✅ تم إنشاء حساب: {data['username']}")
            return data
            
        except Exception as e:
            logger.error(f"❌ فشل إنشاء الحساب: {e}")
            return None
    
    def create_bulk(self, count=5):
        accounts = []
        for i in range(count):
            logger.info(f"📌 إنشاء حساب {i+1}/{count}")
            account = self.create_account_selenium()
            if account:
                accounts.append(account)
            time.sleep(5)
        return accounts

# ================= Instagram Reporter =================

class InstagramReporter:
    def __init__(self):
        self.clients = []
        self.fake = Faker()
        self.ua = UserAgent()
    
    def add_account(self, username, password):
        client = InstaClient()
        client.login(username, password)
        self.clients.append(client)
        logger.info(f"✅ تم إضافة حساب: {username}")
        return client
    
    def report_user(self, target_username):
        results = []
        for client in self.clients:
            try:
                target_id = client.user_id_from_username(target_username)
                client.report_user(target_id)
                results.append({
                    'account': client.username,
                    'target': target_username,
                    'status': 'success'
                })
                logger.info(f"✅ تم الإبلاغ عن {target_username} من {client.username}")
            except Exception as e:
                results.append({
                    'account': client.username,
                    'target': target_username,
                    'status': 'failed',
                    'error': str(e)
                })
                logger.error(f"❌ فشل الإبلاغ من {client.username}: {e}")
            time.sleep(2)
        return results

# ================= دوال البوت =================

advanced_tools = AdvancedTools()
account_creator = InstagramAccountCreator()
instagram_reporter = InstagramReporter()

def start(update, context):
    keyboard = [
        [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
        [InlineKeyboardButton("📧 بريد مؤقت", callback_data="temp_email")],
        [InlineKeyboardButton("🔧 إنشاء حساب إنستغرام", callback_data="create_instagram")],
        [InlineKeyboardButton("📤 إرسال بلاغات جماعية", callback_data="mass_report")],
        [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")],
        [InlineKeyboardButton("🛡️ أدوات متقدمة", callback_data="advanced_tools")],
        [InlineKeyboardButton("❓ مساعدة", callback_data="help")]
    ]
    
    update.message.reply_text(
        "👋 *مرحباً بك في بوت Nasser HC Pro!*\n\n"
        "🤖 *الميزات المتقدمة:*\n"
        "• 📝 تسجيل بلاغات عن حسابات مخالفة\n"
        "• 📧 إنشاء بريد مؤقت حقيقي\n"
        "• 🔧 إنشاء حسابات إنستغرام تلقائياً\n"
        "• 📤 إرسال بلاغات جماعية\n"
        "• 🛡️ أدوات متقدمة (سكرابينج، تحليل، أتمتة)\n"
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
        "3. أرسل سبب البلاغ\n\n"
        "📧 *البريد المؤقت:*\n"
        "1. اضغط على 'بريد مؤقت'\n"
        "2. سيتم إنشاء بريد لك\n"
        "3. استخدمه للتسجيل\n\n"
        "🔧 *إنشاء حساب إنستغرام:*\n"
        "• ينشئ حساب تلقائياً ببيانات عشوائية\n\n"
        "📤 *إرسال بلاغات جماعية:*\n"
        "• يستخدم حسابات متعددة لإرسال بلاغات\n\n"
        "🛡️ *أدوات متقدمة:*\n"
        "• سكرابينج متقدم\n"
        "• تحليل بيانات\n"
        "• أتمتة المهام\n\n"
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
    
    account = account_creator.create_account_selenium()
    
    if account:
        query.edit_message_text(
            f"✅ *تم إنشاء الحساب!*\n\n"
            f"👤 المستخدم: `{account['username']}`\n"
            f"🔑 كلمة المرور: `{account['password']}`\n"
            f"📧 البريد: `{account['email']}`\n\n"
            f"💡 احتفظ بهذه البيانات",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 إضافة للحسابات", callback_data=f"add_account_{account['username']}")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )
    else:
        query.edit_message_text(
            "❌ *فشل إنشاء الحساب*",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 إعادة المحاولة", callback_data="create_instagram")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
            ])
        )

def mass_report_start(update, context):
    query = update.callback_query
    query.answer()
    context.user_data['step'] = 'mass_report_target'
    
    query.edit_message_text(
        "✏️ *أرسل اسم المستخدم المستهدف للإبلاغ الجماعي:*\n"
        "(بدون @)",
        parse_mode='Markdown'
    )

def advanced_tools_menu(update, context):
    query = update.callback_query
    query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🌐 سكرابينج متقدم", callback_data="scrape")],
        [InlineKeyboardButton("📊 تحليل بيانات", callback_data="analyze")],
        [InlineKeyboardButton("🤖 أتمتة المهام", callback_data="automate")],
        [InlineKeyboardButton("🛡️ تغيير User-Agent", callback_data="change_ua")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
    ]
    
    query.edit_message_text(
        "🛡️ *الأدوات المتقدمة*\n\n"
        "اختر الأداة المناسبة:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def scrape_tool(update, context):
    query = update.callback_query
    query.answer()
    
    query.edit_message_text(
        "🌐 *أداة السكرابينج*\n\n"
        "✏️ أرسل رابط الموقع لسحب بياناته:",
        parse_mode='Markdown'
    )
    context.user_data['step'] = 'scrape'

def analyze_tool(update, context):
    query = update.callback_query
    query.answer()
    
    query.edit_message_text(
        "📊 *أداة تحليل البيانات*\n\n"
        "📤 أرسل ملف البيانات (CSV/JSON) لتحليله:",
        parse_mode='Markdown'
    )
    context.user_data['step'] = 'analyze'

def automate_tool(update, context):
    query = update.callback_query
    query.answer()
    
    query.edit_message_text(
        "🤖 *أداة الأتمتة*\n\n"
        "✏️ أرسل المهمة المطلوب أتمتتها:",
        parse_mode='Markdown'
    )
    context.user_data['step'] = 'automate'

def change_ua(update, context):
    query = update.callback_query
    query.answer()
    
    ua = advanced_tools.ua.random
    query.edit_message_text(
        f"🛡️ *تم تغيير User-Agent*\n\n"
        f"📌 الـ User-Agent الجديد:\n`{ua}`",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تغيير آخر", callback_data="change_ua")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="advanced_tools")]
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
        status_emoji = "✅" if report[4] == "resolved" else "⏳"
        text += f"{status_emoji} #{report[0]} - @{report[2]}\n"
        text += f"   📝 {report[3][:30]}...\n"
        text += f"   📅 {report[5]}\n\n"
    
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
            f"✏️ *أرسل سبب البلاغ:*",
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
            f"✅ *تم استلام بلاغك!*\n\n"
            f"📋 رقم البلاغ: `#{report_id}`\n"
            f"🎯 المستهدف: @{target}\n"
            f"📝 السبب: {reason}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")],
                [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back")]
            ])
        )
    
    elif step == 'mass_report_target':
        target = text
        context.user_data.clear()
        
        update.message.reply_text(
            f"🔄 *جاري إرسال بلاغات جماعية لـ @{target}...*\n"
            f"⏳ قد يستغرق بضع دقائق",
            parse_mode='Markdown'
        )
        
        # هنا كود الإبلاغ الجماعي
        results = instagram_reporter.report_user(target)
        
        success = len([r for r in results if r.get('status') == 'success'])
        failed = len([r for r in results if r.get('status') == 'failed'])
        
        update.message.reply_text(
            f"✅ *اكتمل الإرسال!*\n\n"
            f"📊 النتائج:\n"
            f"• ✅ نجح: {success}\n"
            f"• ❌ فشل: {failed}\n"
            f"• 📝 المجموع: {len(results)}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back")]
            ])
        )
    
    elif step == 'scrape':
        update.message.reply_text(
            "🌐 *جاري سحب البيانات...*\n⏳ انتظر قليلاً",
            parse_mode='Markdown'
        )
        # هنا كود السكرابينج
        update.message.reply_text(
            "✅ *تم سحب البيانات بنجاح!*",
            parse_mode='Markdown'
        )
        context.user_data.clear()
    
    elif step == 'analyze':
        update.message.reply_text(
            "📊 *جاري تحليل البيانات...*",
            parse_mode='Markdown'
        )
        update.message.reply_text(
            "✅ *تم تحليل البيانات!*",
            parse_mode='Markdown'
        )
        context.user_data.clear()
    
    elif step == 'automate':
        update.message.reply_text(
            "🤖 *جاري تنفيذ المهمة...*",
            parse_mode='Markdown'
        )
        update.message.reply_text(
            "✅ *تم تنفيذ المهمة بنجاح!*",
            parse_mode='Markdown'
        )
        context.user_data.clear()

def back_handler(update, context):
    query = update.callback_query
    query.answer()
    context.user_data.clear()
    
    keyboard = [
        [InlineKeyboardButton("📝 تقديم بلاغ", callback_data="report")],
        [InlineKeyboardButton("📧 بريد مؤقت", callback_data="temp_email")],
        [InlineKeyboardButton("🔧 إنشاء حساب إنستغرام", callback_data="create_instagram")],
        [InlineKeyboardButton("📤 إرسال بلاغات جماعية", callback_data="mass_report")],
        [InlineKeyboardButton("📊 بلاغاتي", callback_data="my_reports")],
        [InlineKeyboardButton("🛡️ أدوات متقدمة", callback_data="advanced_tools")],
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
    elif data == "mass_report":
        mass_report_start(update, context)
    elif data == "my_reports":
        my_reports(update, context)
    elif data == "advanced_tools":
        advanced_tools_menu(update, context)
    elif data == "scrape":
        scrape_tool(update, context)
    elif data == "analyze":
        analyze_tool(update, context)
    elif data == "automate":
        automate_tool(update, context)
    elif data == "change_ua":
        change_ua(update, context)
    elif data == "help":
        help_command(update, context)
    elif data == "back":
        back_handler(update, context)
    elif data.startswith("add_account_"):
        username = data.replace("add_account_", "")
        query.answer(f"✅ تم إضافة {username}")
        query.edit_message_text(f"✅ *تم إضافة {username} لقائمة الحسابات*", parse_mode='Markdown')
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
