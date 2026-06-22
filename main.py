import os
import sys
import logging
import threading
from flask import Flask, render_template
from bot import main as bot_main
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/status')
def status():
    return "✅ البوت شغال!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

if __name__ == "__main__":
    try:
        logger.info("🚀 بدء تشغيل خادم الويب...")
        threading.Thread(target=run_flask, daemon=True).start()
        
        logger.info("🤖 بدء تشغيل البوت...")
        bot_main()
    except Exception as e:
        logger.error(f"❌ خطأ: {e}")
        sys.exit(1)
