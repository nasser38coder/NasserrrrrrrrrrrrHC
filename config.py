import os

# ================= توكن البوت =================
BOT_TOKEN = "8854067469:AAECoNTDQlnV7V6FAhUnDwsdxbrDeLdYRso"
ADMIN_IDS = [5100562548]

# ================= إعدادات البريد المؤقت =================
TEMP_EMAIL_API = "https://api.guerrillamail.com/ajax.php"

# ================= إعدادات قاعدة البيانات =================
DATABASE_FILE = "reports.db"

# ================= إعدادات التشغيل =================
DEBUG = False
PORT = int(os.environ.get("PORT", 8080))
