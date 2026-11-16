#  ╭───𓆩🛡️𓆪───╮
#  👨‍💻 𝘿𝙚𝙫: @avetaar  
#   📢 𝘾𝙝: @EgyCodes
import telebot
import subprocess
import os
import zipfile
import shutil
import re
from telebot import types
import time
from datetime import datetime
import psutil
import sqlite3
import logging
from logging import StreamHandler # تم إضافة هذا الاستيراد لحل مشكلة NameError
import threading
import sys
import atexit
import requests
from flask import Flask
from threading import Thread

# Flask Keep-Alive Setup
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is hosted by ATr"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# --- Configuration ---
TOKEN = '9176146724:AAFU0mJ6B0Cx4JH8St9HkpyR1-dSarWu5qM'
OWNER_ID = 6846407080 # Replace with your Telegram User ID
YOUR_USERNAME = '@t7_m6' # Replace with your Telegram Username
UPDATE_CHANNEL = 'https://t.me/Vl_HI'
FORCE_SUBSCRIBE_CHANNEL_ID = '@Vl_HI' # Replace with your channel ID (must start with '@')

# Absolute Paths for Directories
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')
MAIN_BOT_LOG_PATH = os.path.join(IROTECH_DIR, 'main_bot_log.log')

# Create necessary directories
os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)

# --- Data Structures ---
bot_scripts = {} # {script_key: {'process': Popen_obj, 'log_file': file_obj, 'file_name': str, 'chat_id': int, 'script_owner_id': int, 'start_time': datetime, 'user_folder': str, 'type': 'py', 'script_key': str}}
user_files = {} # {user_id: [(file_name, file_type, status, bot_token_id), ...]} - status: 'pending', 'approved', 'rejected'
user_pagination_state = {} # {user_id: {'current_page': int, 'total_pages': int, 'files': [(file_name, file_type, status, bot_token_id)]}}
admin_pagination_state = {} # {admin_id: {'current_page': int, 'total_pages': int, 'page_type': 'all_users_overview' or 'user_specific', 'target_user_id': int}}

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(MAIN_BOT_LOG_PATH, encoding='utf-8'),
        StreamHandler(sys.stdout) # تم إصلاح هذا هنا
    ]
)
logger = logging.getLogger(__name__)

# --- ReplyKeyboardMarkup Layouts ---
MAIN_MENU_BUTTONS_LAYOUT = [
    ["📢 قناتي"],
    ["📤 رفع ملف", "📂 ملفاتي"]
]
ADMIN_MENU_BUTTONS_LAYOUT = [
    ["📢 قناتي"],
    ["📤 رفع ملف", "📂 ملفاتي"],
    ["👑 لوحة تحكم المطور"]
]

# --- Database Setup ---
DB_LOCK = threading.Lock()

def init_db():
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, file_name TEXT, file_type TEXT, status TEXT, bot_token_id TEXT,
                      PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY)''')
        conn.commit()
        conn.close()

def load_data():
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, file_name, file_type, status, bot_token_id FROM user_files')
        for user_id, file_name, file_type, status, bot_token_id in c.fetchall():
            user_files.setdefault(user_id, []).append((file_name, file_type, status, bot_token_id))
        conn.close()

def add_user_to_db(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
        conn.commit()
        conn.close()

def update_user_file_db(user_id, file_name, file_type, status, bot_token_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type, status, bot_token_id) VALUES (?, ?, ?, ?, ?)',
                  (user_id, file_name, file_type, status, bot_token_id))
        conn.commit()
        conn.close()

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
        conn.commit()
        conn.close()

def get_all_user_files_from_db():
    all_files = []
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, file_name, file_type, status, bot_token_id FROM user_files')
        for user_id, file_name, file_type, status, bot_token_id in c.fetchall():
            all_files.append({
                'user_id': user_id,
                'file_name': file_name,
                'file_type': file_type,
                'status': status,
                'bot_token_id': bot_token_id
            })
        conn.close()
    return all_files

init_db()
load_data()

# --- Helper Functions for Script Management ---
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if not script_info or not script_info.get('process'):
        return False
    
    try:
        proc = psutil.Process(script_info['process'].pid)
        is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        if not is_running:
            _cleanup_stale_script_entry(script_key, script_info)
        return is_running
    except psutil.NoSuchProcess:
        _cleanup_stale_script_entry(script_key, script_info)
        return False
    except Exception as e:
        logger.error(f"Error checking process status for {script_key}: {e}", exc_info=True)
        return False

def _cleanup_stale_script_entry(script_key, script_info):
    if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
        try: script_info['log_file'].close()
        except Exception as log_e: logger.error(f"Error closing log file for stale script {script_key}: {log_e}")
    if script_key in bot_scripts: del bot_scripts[script_key]

def kill_process_tree(process_info):
    pid = None
    if 'log_file' in process_info and hasattr(process_info['log_file'], 'close') and not process_info['log_file'].closed:
        try: process_info['log_file'].close()
        except Exception as log_e: logger.error(f"Error closing log file during termination for {process_info.get('script_key', 'N/A')}: {log_e}")

    process = process_info.get('process')
    if not process or not hasattr(process, 'pid'):
        return

    pid = process.pid
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        
        for child in children:
            try: child.terminate()
            except (psutil.NoSuchProcess, Exception) as e:
                try: child.kill()
                except Exception as e2: logger.error(f"Failed to kill child process {child.pid} forcefully: {e2}")

        psutil.wait_procs(children, timeout=1)

        try:
            parent.terminate()
            parent.wait(timeout=1)
        except psutil.TimeoutExpired:
            parent.kill()
        except (psutil.NoSuchProcess, Exception) as e:
            try: parent.kill()
            except Exception as e2: logger.error(f"Failed to kill main process {pid} forcefully: {e2}")

    except psutil.NoSuchProcess: pass
    except Exception as e:
        logger.error(f"Unexpected error during process tree termination for PID {pid}: {e}", exc_info=True)

def run_script(script_path, script_owner_id, user_folder, file_name, chat_id_for_reply):
    script_key = f"{script_owner_id}_{file_name}"

    if not os.path.exists(script_path):
        bot.send_message(chat_id_for_reply, f"❌ خطأ: لم يتم العثور على السكربت '{file_name}' في '{script_path}'!")
        return

    if is_bot_running(script_owner_id, file_name):
        bot.send_message(chat_id_for_reply, f"ℹ️ سكربت '{file_name}' قيد التشغيل بالفعل.")
        return

    log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
    log_file = None; process = None
    
    try: log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
    except Exception as e:
        bot.send_message(chat_id_for_reply, f"❌ فشل في فتح ملف السجل '{log_file_path}': {e}", parse_mode='Markdown')
        return

    try:
        startupinfo = None; creationflags = 0
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            [sys.executable, script_path], cwd=user_folder, stdout=log_file, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE, startupinfo=startupinfo, creationflags=creationflags,
            encoding='utf-8', errors='ignore',
            preexec_fn=os.setsid
        )
        bot_scripts[script_key] = {
            'process': process, 'log_file': log_file, 'file_name': file_name,
            'chat_id': chat_id_for_reply,
            'script_owner_id': script_owner_id,
            'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'py', 'script_key': script_key
        }
        bot.send_message(chat_id_for_reply, f"✅ تم بدء سكربت بايثون '{file_name}'! (PID: {process.pid})")
    except FileNotFoundError:
        if log_file and not log_file.closed: log_file.close()
        bot.send_message(chat_id_for_reply, f"❌ خطأ: لم يتم العثور على مترجم بايثون '{sys.executable}'.", parse_mode='Markdown')
        if script_key in bot_scripts: del bot_scripts[script_key]
    except Exception as e:
        if log_file and not log_file.closed: log_file.close()
        bot.send_message(chat_id_for_reply, f"❌ خطأ في بدء سكربت بايثون '{file_name}': {str(e)}", parse_mode='Markdown')
        if process and process.poll() is None:
            kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
        if script_key in bot_scripts: del bot_scripts[script_key]

# --- Script Monitoring ---
def monitor_scripts():
    while True:
        keys_to_check = list(bot_scripts.keys())
        for script_key in keys_to_check:
            if script_key not in bot_scripts:
                continue
            script_info = bot_scripts[script_key]
            process = script_info['process']
            
            if process.poll() is not None:
                exit_code = process.poll()
                log_file_path = os.path.join(script_info['user_folder'], f"{os.path.splitext(script_info['file_name'])[0]}.log")
                error_details = "لا توجد تفاصيل إضافية في السجل."
                if os.path.exists(log_file_path):
                    try:
                        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            log_content = f.read()
                            error_details = log_content[-1500:] if len(log_content) > 1500 else log_content
                            if not error_details.strip():
                                error_details = "ملف السجل فارغ أو لم يتم كتابة أي شيء."
                    except Exception as e:
                        logger.error(f"Error reading log for stopped script: {e}")
                        error_details = f"فشل في قراءة ملف السجل: {e}"

                message_text = (
                    f"❌ توقف سكربتك '{script_info['file_name']}' بشكل غير متوقع!\n"
                    f"رمز الخروج: {exit_code}\n"
                    f"تفاصيل من السجل:\n"
                    f"```\n{error_details[:1500]}...\n```\n"
                    f"جارٍ محاولة إعادة تشغيله..."
                )
                try:
                    bot.send_message(script_info['chat_id'], message_text, parse_mode='Markdown')
                except Exception as e:
                    logger.error(f"Failed to send script stop notification to user {script_info['chat_id']}: {e}")

                kill_process_tree(script_info)
                if script_key in bot_scripts:
                    del bot_scripts[script_key]

                threading.Thread(target=run_script, args=(
                    os.path.join(script_info['user_folder'], script_info['file_name']),
                    script_info['script_owner_id'],
                    script_info['user_folder'],
                    script_info['file_name'],
                    script_info['chat_id']
                )).start()
            
        time.sleep(10)

threading.Thread(target=monitor_scripts, daemon=True).start()

# --- Force Subscribe Check ---
def is_subscribed(user_id):
    if user_id == OWNER_ID:
        return True
    try:
        member = bot.get_chat_member(FORCE_SUBSCRIBE_CHANNEL_ID, user_id)
        return member.status in ['member', 'creator', 'administrator']
    except telebot.apihelper.ApiException as e:
        logger.error(f"Error checking channel membership for {FORCE_SUBSCRIBE_CHANNEL_ID} for user {user_id}: {e}")
        return False

def send_force_subscribe_message(chat_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("اشترك في القناة", url=f"https://t.me/{FORCE_SUBSCRIBE_CHANNEL_ID.replace('@', '')}"))
    bot.send_message(
        chat_id,
        f"عليك الاشتراك في قناة المطور {FORCE_SUBSCRIBE_CHANNEL_ID} لكي تتمكن من استخدام البوت.",
        reply_markup=markup
    )

def check_subscription_wrapper(handler_function):
    def wrapper(message):
        if message.from_user.id == OWNER_ID:
            handler_function(message)
            return
        if not is_subscribed(message.from_user.id):
            send_force_subscribe_message(message.chat.id)
            return
        handler_function(message)
    return wrapper

# --- Get Bot ID from Token ---
def get_bot_id_from_token(token_string):
    try:
        parts = token_string.split(':')
        if len(parts) > 0:
            return parts[0]
    except Exception:
        pass
    return None

# --- Message Handlers ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    add_user_to_db(user_id)

    if not is_subscribed(user_id):
        send_force_subscribe_message(message.chat.id)
        return

    markup = get_user_keyboard(user_id)
    if user_id == OWNER_ID:
        bot.send_message(message.chat.id, "أهلاً بك يا مطور! 👑 اختر من لوحة التحكم:", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "أهلاً بك! 👋 اختر من القائمة الرئيسية:", reply_markup=markup)

def get_user_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    layout = ADMIN_MENU_BUTTONS_LAYOUT if user_id == OWNER_ID else MAIN_MENU_BUTTONS_LAYOUT
    for row in layout:
        markup.add(*row)
    return markup

@bot.message_handler(func=lambda message: message.text == "📢 قناتي")
def send_update_channel_handler(message):
    bot.reply_to(message, f"تفضل بزيارة قناتي لآخر التحديثات: {UPDATE_CHANNEL}")

@bot.message_handler(func=lambda message: message.text == "📤 رفع ملف")
@check_subscription_wrapper
def upload_file_instruction(message):
    bot.reply_to(message, "يرجى إرسال ملف البوت الخاص بك (.py أو .zip) الآن. سيتم مراجعته من قبل المطور.")

@bot.message_handler(func=lambda message: message.text == "📂 ملفاتي")
@check_subscription_wrapper
def list_user_files(message):
    user_id = message.from_user.id
    files = user_files.get(user_id, [])
    
    if not files:
        bot.reply_to(message, "ليس لديك أي ملفات مرفوعة حالياً.")
        return

    files_per_page = 5
    total_files = len(files)
    total_pages = (total_files + files_per_page - 1) // files_per_page if total_files > 0 else 0
    current_page = user_pagination_state.get(user_id, {}).get('current_page', 1)
    
    if total_files == 0: current_page = 0
    elif current_page > total_pages: current_page = total_pages
    elif current_page < 1: current_page = 1

    start_idx = (current_page - 1) * files_per_page
    end_idx = start_idx + files_per_page
    paginated_files = files[start_idx:end_idx]

    user_pagination_state[user_id] = {
        'current_page': current_page,
        'total_pages': total_pages,
        'files': files
    }

    response = f"ملفاتك المرفوعة (الصفحة {current_page}/{total_pages}):\n"
    if not paginated_files:
        response += "لا توجد ملفات في هذه الصفحة."
        markup = types.InlineKeyboardMarkup()
        if total_pages > 1:
            if current_page > 1:
                markup.add(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"user_prev_page_{user_id}"))
            if current_page < total_pages:
                markup.add(types.InlineKeyboardButton("التالي ➡️", callback_data=f"user_next_page_{user_id}"))
        bot.reply_to(message, response, parse_mode='Markdown', reply_markup=markup)
        return

    markup = types.InlineKeyboardMarkup()
    for idx, (file_name, file_type, status, bot_token_id) in enumerate(paginated_files):
        script_key = f"{user_id}_{file_name}"
        is_running = is_bot_running(user_id, file_name) and status == 'approved'
        
        status_emoji = "⏳ معلق" if status == 'pending' else \
                       "✅ موافق عليه" if status == 'approved' else \
                       "❌ مرفوض" if status == 'rejected' else "❓ غير معروف"
        
        running_status_emoji = "🟢 يعمل" if is_running else "🔴 متوقف"
        
        bot_id_display = f" (معرف البوت: <code>{bot_token_id}</code>)" if bot_token_id else ""
        response += f"{start_idx + idx + 1}. `{file_name}` ({file_type}) - {status_emoji} - {running_status_emoji}{bot_id_display}\n"
        
        if status == 'approved':
            start_stop_button_text = "■ إيقاف" if is_running else "▶ تشغيل"
            markup.add(
                types.InlineKeyboardButton(f"{start_stop_button_text} {file_name}", callback_data=f"toggle_{script_key}"),
                types.InlineKeyboardButton(f"🗑️ حذف {file_name}", callback_data=f"delete_{script_key}"),
                types.InlineKeyboardButton(f"📄 سجل {file_name}", callback_data=f"log_{script_key}")
            )
        else:
            markup.add(types.InlineKeyboardButton(f"🗑️ حذف {file_name}", callback_data=f"delete_{script_key}"))

    if total_pages > 1:
        pagination_buttons = []
        if current_page > 1:
            pagination_buttons.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"user_prev_page_{user_id}"))
        if current_page < total_pages:
            pagination_buttons.append(types.InlineKeyboardButton("التالي ➡️", callback_data=f"user_next_page_{user_id}"))
        if pagination_buttons:
            markup.add(*pagination_buttons)

    bot.reply_to(message, response, parse_mode='Markdown', reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "👑 لوحة تحكم المطور")
def developer_panel(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "عذراً، هذه المنطقة مخصصة للمطور فقط.")
        return
    
    display_all_user_files(message.chat.id, 1, message.message_id)

@bot.message_handler(content_types=['document'])
@check_subscription_wrapper
def handle_document(message):
    user_id = message.from_user.id
    file_info = message.document
    file_name = file_info.file_name
    file_id = file_info.file_id
    file_extension = os.path.splitext(file_name)[1].lower()

    if file_extension not in ['.py', '.zip']:
        bot.reply_to(
            message,
            "❌ نوع الملف غير مدعوم.\nيرجى إرسال ملف بايثون (`.py`) أو ملف مضغوط (`.zip`).",
            parse_mode="Markdown"
        )
        return

    bot.reply_to(message, "جارٍ تنزيل ملفك... ⏳")
    user_folder = get_user_folder(user_id)
    local_file_path = os.path.join(user_folder, file_name)

    try:
        file_path_on_telegram = bot.get_file(file_id).file_path
        downloaded_file_content = bot.download_file(file_path_on_telegram)

        with open(local_file_path, 'wb') as f:
            f.write(downloaded_file_content)

        bot_id_from_token = None
        if file_extension == '.py':
            try:
                with open(local_file_path, 'r', encoding='utf-8', errors='ignore') as f_py:
                    content = f_py.read()
                    token_match = re.search(r'(?:bot|Bot|BOT|token|Token|TOKEN)\s*=\s*[\'"]([0-9]{9}:[a-zA-Z0-9_-]{35})[\'"]', content)
                    if token_match:
                        bot_id_from_token = get_bot_id_from_token(token_match.group(1))
            except Exception as e:
                logger.warning(f"Failed to extract token from .py file {file_name}: {e}")
        elif file_extension == '.zip':
            try:
                with zipfile.ZipFile(local_file_path, 'r') as zip_ref:
                    for member in zip_ref.namelist():
                        if member.endswith('.py'):
                            with zip_ref.open(member, 'r') as py_file_in_zip:
                                content = py_file_in_zip.read().decode('utf-8', errors='ignore')
                                token_match = re.search(r'(?:bot|Bot|BOT|token|Token|TOKEN)\s*=\s*[\'"]([0-9]{9}:[a-zA-Z0-9_-]{35})[\'"]', content)
                                if token_match:
                                    bot_id_from_token = get_bot_id_from_token(token_match.group(1))
                                    break
            except Exception as e:
                logger.warning(f"Failed to extract token from .zip file {file_name}: {e}")

        # Update in-memory cache and DB
        current_user_files = user_files.setdefault(user_id, [])
        found_existing = False
        for i, (fname, ftype, fstatus, f_bot_id) in enumerate(current_user_files):
            if fname == file_name:
                current_user_files[i] = (file_name, file_extension, 'pending', bot_id_from_token)
                found_existing = True
                break
        if not found_existing:
            current_user_files.append((file_name, file_extension, 'pending', bot_id_from_token))
            
        update_user_file_db(user_id, file_name, file_extension, 'pending', bot_id_from_token)

        bot.reply_to(
            message,
            "✅ تم استلام ملفك بنجاح. سيتم مراجعته من قبل المطور قريباً.\n"
            "سوف تتلقى إشعاراً عند الموافقة أو الرفض."
        )

        bot_id_text = f"\nمعرف البوت المستخرج: <code>{bot_id_from_token}</code>" if bot_id_from_token else ""
        developer_message_text = (
            f"📥 ملف جديد للتحقق!\n"
            f"المستخدم: <a href='tg://user?id={user_id}'>{message.from_user.first_name or 'لا يوجد اسم'}</a> (<code>{user_id}</code>)\n"
            f"اسم الملف: <code>{file_name}</code>\n"
            f"نوع الملف: <code>{file_extension}</code>\n"
            f"يوزر البوت الذي تم رفعه: @{message.from_user.username or 'غير متوفر'}"
            f"{bot_id_text}"
        )

        markup = types.InlineKeyboardMarkup()
        callback_key = f"{user_id}_{file_name}"
        markup.add(
            types.InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{callback_key}"),
            types.InlineKeyboardButton("❌ رفض", callback_data=f"reject_{callback_key}")
        )
        
        with open(local_file_path, 'rb') as doc_file:
            bot.send_document(OWNER_ID, doc_file, caption=developer_message_text, parse_mode='HTML', reply_markup=markup)

    except Exception as e:
        bot.reply_to(message, f"❌ حدث خطأ أثناء تنزيل الملف أو معالجته: {e}")
        logger.error(f"General error processing uploaded file for user {user_id}: {e}", exc_info=True)

# --- Callback Handlers for Approval/Rejection ---
@bot.callback_query_handler(func=lambda call: call.data.startswith(('approve_', 'reject_')))
def handle_approval_callbacks(call):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "غير مصرح لك بهذه العملية.")
        return

    action, callback_key = call.data.split('_', 1)

    user_id_str, file_name = callback_key.split('_', 1)
    target_user_id = int(user_id_str)
    
    file_data_entry = None
    if target_user_id in user_files:
        for i, (f_name, f_type, f_status, bot_id) in enumerate(user_files[target_user_id]):
            if f_name == file_name:
                file_data_entry = {'user_id': target_user_id, 'file_name': f_name, 'file_type': f_type, 'status': f_status, 'bot_token_id': bot_id}
                break

    if not file_data_entry or file_data_entry['status'] != 'pending':
        bot.answer_callback_query(call.id, "لم يتم العثور على هذا الملف المعلق أو تمت معالجته بالفعل.")
        original_caption = call.message.caption or ""
        if "📥 ملف جديد للتحقق!" in original_caption:
            bot.edit_message_caption(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                caption=f"تمت معالجة الطلب لـ <code>{file_name}</code> (للمستخدم <code>{target_user_id}</code>) بالفعل أو لم يعد متاحاً.",
                parse_mode='HTML',
                reply_markup=None
            )
        return

    user_id = file_data_entry['user_id']
    file_name = file_data_entry['file_name']
    file_path = os.path.join(get_user_folder(user_id), file_name)
    file_extension = file_data_entry['file_type']
    bot_token_id = file_data_entry['bot_token_id']

    if action == 'approve':
        status = 'approved'
        bot.edit_message_caption(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            caption=f"✅ تم الموافقة على ملف <code>{file_name}</code> للمستخدم <a href='tg://user?id={user_id}'>{user_id}</a>.",
            parse_mode='HTML',
            reply_markup=None
        )
        try:
            bot.send_message(user_id, f"✅ تهانينا! تمت الموافقة على ملفك <code>{file_name}</code>. جارٍ تشغيله/فك ضغطه...", parse_mode='HTML')
        except Exception as e:
            logger.error(f"Failed to send approval notification to user {user_id}: {e}")
            bot.send_message(OWNER_ID, f"⚠️ فشل إرسال إشعار الموافقة للمستخدم {user_id} لـ {file_name}: {e}", parse_mode='HTML')

        if user_id in user_files:
            user_files[user_id] = [(f_name, f_type, status, bot_id) if f_name == file_name else (f_name, f_type, old_status, bot_id) for f_name, f_type, old_status, bot_id in user_files[user_id]]
        update_user_file_db(user_id, file_name, file_extension, status, bot_token_id)
        
        if file_extension == '.py':
            user_folder = get_user_folder(user_id)
            threading.Thread(
                target=run_script,
                args=(file_path, user_id, user_folder, file_name, user_id)
            ).start()
        elif file_extension == '.zip':
            try:
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    for member in zip_ref.namelist():
                        if os.path.isabs(member) or ".." in member:
                            raise ValueError(f"Invalid path inside ZIP: {member}")
                    zip_ref.extractall(os.path.dirname(file_path))
                os.remove(file_path)
                bot.send_message(user_id, "✅ تم فك الضغط بنجاح. يمكنك الآن تشغيل السكربتات المستخرجة (إذا كانت ملفات .py) من قائمة 'ملفاتي'.", parse_mode='HTML')
            except zipfile.BadZipFile:
                bot.send_message(user_id, "❌ فشل فك ضغط ملف ZIP الخاص بك. يبدو أنه تالف.", parse_mode='HTML')
            except ValueError as ve:
                bot.send_message(user_id, f"❌ فشل فك الضغط: {ve}\nDetected unsafe path inside archive.", parse_mode='HTML')
            except Exception as e:
                bot.send_message(user_id, f"❌ حدث خطأ أثناء فك ضغط ملف ZIP الخاص بك: {e}", parse_mode='HTML')
        
    elif action == 'reject':
        status = 'rejected'
        bot.edit_message_caption(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            caption=f"❌ تم رفض ملف <code>{file_name}</code> للمستخدم <a href='tg://user?id={user_id}'>{user_id}</a>.",
            parse_mode='HTML',
            reply_markup=None
        )
        try:
            bot.send_message(user_id, f"❌ نعتذر، تم رفض ملفك <code>{file_name}</code>.", parse_mode='HTML')
        except Exception as e:
            logger.error(f"Failed to send rejection notification to user {user_id}: {e}")
            bot.send_message(OWNER_ID, f"⚠️ فشل إرسال إشعار الرفض للمستخدم {user_id} لـ {file_name}: {e}", parse_mode='HTML')
        
        if user_id in user_files:
            user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
        remove_user_file_db(user_id, file_name)
        if os.path.exists(file_path):
            os.remove(file_path)

    bot.answer_callback_query(call.id, f"تم معالجة الطلب: {status}.")


# --- Callback Handlers for User File Management (Toggle/Delete/Log) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith(('toggle_', 'delete_', 'log_')))
def handle_file_action_callbacks(call):
    action_type, script_key_full = call.data.split('_', 1)
    
    try:
        script_owner_id_str, file_name = script_key_full.split('_', 1)
        script_owner_id = int(script_owner_id_str)
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "خطأ في مفتاح السكربت (تنسيق غير صالح).")
        return

    # Permission check: Only owner or the script's owner can act
    if call.from_user.id != script_owner_id and call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "لا تملك الإذن للتحكم في هذا السكربت.")
        return
    
    # Subscription check for regular users
    if call.from_user.id != OWNER_ID and not is_subscribed(call.from_user.id):
        send_force_subscribe_message(call.message.chat.id)
        bot.answer_callback_query(call.id, "عليك الاشتراك في القناة أولاً.")
        return

    # Retrieve file status
    file_status = 'unknown'
    if script_owner_id in user_files:
        for f_name, _, status, _ in user_files[script_owner_id]:
            if f_name == file_name:
                file_status = status
                break
    
    if action_type == 'toggle':
        if file_status != 'approved':
            bot.answer_callback_query(call.id, f"لا يمكن تشغيل/إيقاف هذا الملف. حالته هي: {file_status}")
            return
        
        script_key = f"{script_owner_id}_{file_name}"
        if is_bot_running(script_owner_id, file_name):
            if script_key in bot_scripts:
                bot.answer_callback_query(call.id, f"جارٍ إيقاف '{file_name}'...")
                kill_process_tree(bot_scripts[script_key])
                del bot_scripts[script_key]
                bot.send_message(call.message.chat.id, f"■ تم إيقاف سكربت '{file_name}'.")
            else:
                bot.answer_callback_query(call.id, "السكربت ليس قيد التشغيل أو تمت إزالة بالفعل.")
        else:
            user_folder = get_user_folder(script_owner_id)
            script_path = os.path.join(user_folder, file_name)
            if os.path.exists(script_path):
                bot.answer_callback_query(call.id, f"جارٍ تشغيل '{file_name}'...")
                threading.Thread(
                    target=run_script,
                    args=(script_path, script_owner_id, user_folder, file_name, call.message.chat.id)
                ).start()
            else:
                bot.answer_callback_query(call.id, f"❌ لم يتم العثور على الملف '{file_name}'. ربما تم حذفه.")
                _remove_file_from_cache_and_db(script_owner_id, file_name)

    elif action_type == 'delete':
        if is_bot_running(script_owner_id, file_name):
            bot.answer_callback_query(call.id, f"يرجى إيقاف '{file_name}' أولاً قبل الحذف.")
            return

        user_folder = get_user_folder(script_owner_id)
        script_path = os.path.join(user_folder, file_name)

        try:
            if os.path.exists(script_path):
                os.remove(script_path)
                log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
                if os.path.exists(log_file_path):
                    os.remove(log_file_path)

                _remove_file_from_cache_and_db(script_owner_id, file_name)

                bot.answer_callback_query(call.id, f"✅ تم حذف '{file_name}' بنجاح.")
                bot.send_message(call.message.chat.id, f"🗑️ تم حذف سكربت '{file_name}'.")
            else:
                bot.answer_callback_query(call.id, f"❌ لم يتم العثور على الملف '{file_name}'. ربما تم حذفه بالفعل.")
                _remove_file_from_cache_and_db(script_owner_id, file_name)
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ خطأ في حذف الملف: {e}")
            logger.error(f"Error deleting file {script_path} for user {script_owner_id}: {e}", exc_info=True)
    
    elif action_type == 'log':
        user_folder = get_user_folder(script_owner_id)
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")

        if not os.path.exists(log_file_path):
            bot.answer_callback_query(call.id, "❌ لا يوجد ملف سجل لهذا السكربت بعد.")
            bot.send_message(call.message.chat.id, f"لا يوجد ملف سجل للسكربت `{file_name}`. قد لا يكون قد تم تشغيله بعد أو لم يكتب أي شيء في السجل.", parse_mode='Markdown')
            return

        bot.answer_callback_query(call.id, f"جارٍ جلب سجل '{file_name}'...")
        try:
            with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                log_content = f.read()
            
            if not log_content.strip():
                bot.send_message(call.message.chat.id, f"ملف سجل `{file_name}` فارغ. لم يكتب السكربت أي شيء في السجل بعد.", parse_mode='Markdown')
                return

            max_length_per_message = 4000 
            if len(log_content) > max_length_per_message:
                parts = [log_content[i:i+max_length_per_message] for i in range(0, len(log_content), max_length_per_message)]
                for i, chunk in enumerate(parts):
                    bot.send_message(call.message.chat.id, f"📜 سجل '{file_name}' (جزء {i+1} من {len(parts)}):\n```\n{chunk}\n```", parse_mode='Markdown')
                    time.sleep(0.5)
            else:
                bot.send_message(call.message.chat.id, f"📜 سجل '{file_name}':\n```\n{log_content}\n```", parse_mode='Markdown')

        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ حدث خطأ أثناء قراءة ملف السجل: {e}")
            logger.error(f"Error reading log file {log_file_path} for user {script_owner_id}: {e}", exc_info=True)

    # Refresh the file list message after action
    if call.from_user.id == OWNER_ID:
        admin_state = admin_pagination_state.get(OWNER_ID, {})
        if admin_state.get('page_type') == 'user_specific' and admin_state.get('target_user_id') == script_owner_id:
            display_user_files_for_admin(OWNER_ID, script_owner_id, admin_state['current_page'], call.message.message_id)
        else:
            display_all_user_files(OWNER_ID, admin_state.get('current_page', 1), call.message.message_id)
    else:
        list_user_files(call.message)

def _remove_file_from_cache_and_db(user_id, file_name):
    if user_id in user_files:
        user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
    remove_user_file_db(user_id, file_name)

# --- Admin Pagination and Navigation ---
def display_all_user_files(chat_id, page_number, message_id=None):
    files_per_page = 5
    all_files_data = get_all_user_files_from_db()
    
    user_file_groups = {}
    for file_info in all_files_data:
        user_id = file_info['user_id']
        if user_id not in user_file_groups:
            user_file_groups[user_id] = []
        user_file_groups[user_id].append(file_info)

    users_with_files = list(user_file_groups.items())
    
    total_users_with_files = len(users_with_files)
    total_pages = (total_users_with_files + files_per_page - 1) // files_per_page if total_users_with_files > 0 else 0
    
    current_page = page_number
    if total_users_with_files == 0: current_page = 0
    elif current_page > total_pages: current_page = total_pages
    elif current_page < 1: current_page = 1

    start_idx = (current_page - 1) * files_per_page
    end_idx = start_idx + files_per_page
    paginated_users_with_files = users_with_files[start_idx:end_idx]

    admin_pagination_state[OWNER_ID] = {
        'current_page': current_page,
        'total_pages': total_pages,
        'page_type': 'all_users_overview'
    }

    response = f"👑 لوحة تحكم المطور - جميع المستخدمين (الصفحة {current_page}/{total_pages}):\n\n"
    if not paginated_users_with_files:
        response += "لا توجد ملفات مرفوعة من المستخدمين حتى الآن."
    else:
        for user_id, files_list in paginated_users_with_files:
            response += f"👤 المستخدم: <a href='tg://user?id={user_id}'>{user_id}</a> - عدد الملفات: {len(files_list)}\n"
            for file_info in files_list[:2]:
                status_emoji = "⏳" if file_info['status'] == 'pending' else \
                               "✅" if file_info['status'] == 'approved' else "❌"
                bot_id_display = f" (<code>{file_info['bot_token_id']}</code>)" if file_info['bot_token_id'] else ""
                response += f"  - `{file_info['file_name']}` {status_emoji}{bot_id_display}\n"
            if len(files_list) > 2:
                response += f"  ...و {len(files_list) - 2} ملفات أخرى.\n"
            response += "\n"

    markup = types.InlineKeyboardMarkup()
    for user_id, _ in paginated_users_with_files:
        # Fixed callback_data format for viewing user files
        markup.add(types.InlineKeyboardButton(f"عرض ملفات المستخدم {user_id}", callback_data=f"admin_view_user_files_{user_id}_page_1"))

    pagination_buttons = []
    if current_page > 1:
        pagination_buttons.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"admin_prev_page_all_users"))
    if current_page < total_pages:
        pagination_buttons.append(types.InlineKeyboardButton("التالي ➡️", callback_data=f"admin_next_page_all_users"))
    if pagination_buttons:
        markup.add(*pagination_buttons)

    if message_id:
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=response,
                parse_mode='HTML',
                reply_markup=markup,
                disable_web_page_preview=True
            )
        except telebot.apihelper.ApiException as e:
            if "message is not modified" not in str(e):
                bot.send_message(chat_id, response, parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)
    else:
        bot.send_message(chat_id, response, parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)

def display_user_files_for_admin(admin_chat_id, target_user_id, page_number, message_id=None):
    files_per_page = 5
    files = user_files.get(target_user_id, [])
    
    total_files = len(files)
    total_pages = (total_files + files_per_page - 1) // files_per_page if total_files > 0 else 0
    current_page = page_number

    if total_files == 0: current_page = 0
    elif current_page > total_pages: current_page = total_pages
    elif current_page < 1: current_page = 1

    start_idx = (current_page - 1) * files_per_page
    end_idx = start_idx + files_per_page
    paginated_files = files[start_idx:end_idx]

    admin_pagination_state[OWNER_ID] = {
        'current_page': current_page,
        'total_pages': total_pages,
        'target_user_id': target_user_id,
        'page_type': 'user_specific'
    }

    response = f"👑 ملفات المستخدم <a href='tg://user?id={target_user_id}'>{target_user_id}</a> (الصفحة {current_page}/{total_pages}):\n"
    if not paginated_files:
        response += "لا توجد ملفات لهذا المستخدم في هذه الصفحة."
    
    markup = types.InlineKeyboardMarkup()
    for idx, (file_name, file_type, status, bot_token_id) in enumerate(paginated_files):
        script_key = f"{target_user_id}_{file_name}"
        is_running = is_bot_running(target_user_id, file_name) and status == 'approved'
        
        status_emoji = "⏳ معلق" if status == 'pending' else \
                       "✅ موافق عليه" if status == 'approved' else \
                       "❌ مرفوض" if status == 'rejected' else "❓ غير معروف"
        
        running_status_emoji = "🟢 يعمل" if is_running else "🔴 متوقف"
        
        bot_id_display = f" (معرف البوت: <code>{bot_token_id}</code>)" if bot_token_id else ""
        response += f"{start_idx + idx + 1}. `{file_name}` ({file_type}) - {status_emoji} - {running_status_emoji}{bot_id_display}\n"
        
        # Ensure buttons are created correctly for admin to control
        if status == 'approved':
            start_stop_button_text = "■ إيقاف" if is_running else "▶ تشغيل"
            markup.add(
                types.InlineKeyboardButton(f"{start_stop_button_text} {file_name}", callback_data=f"toggle_{script_key}"),
                types.InlineKeyboardButton(f"🗑️ حذف {file_name}", callback_data=f"delete_{script_key}"),
                types.InlineKeyboardButton(f"📄 سجل {file_name}", callback_data=f"log_{script_key}")
            )
        else: # For pending/rejected files, only offer deletion
            markup.add(types.InlineKeyboardButton(f"🗑️ حذف {file_name}", callback_data=f"delete_{script_key}"))

    pagination_buttons = []
    if current_page > 1:
        pagination_buttons.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"admin_view_user_files_{target_user_id}_page_{current_page - 1}"))
    if current_page < total_pages:
        pagination_buttons.append(types.InlineKeyboardButton("التالي ➡️", callback_data=f"admin_view_user_files_{target_user_id}_page_{current_page + 1}"))
    if pagination_buttons:
        markup.add(*pagination_buttons)

    markup.add(types.InlineKeyboardButton("🔙 العودة لجميع المستخدمين", callback_data=f"admin_back_to_all_users"))

    try:
        bot.edit_message_text(
            chat_id=admin_chat_id,
            message_id=message_id,
            text=response,
            parse_mode='HTML',
            reply_markup=markup,
            disable_web_page_preview=True
        )
    except telebot.apihelper.ApiException as e:
        if "message is not modified" not in str(e):
            bot.send_message(admin_chat_id, response, parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith(('user_prev_page_', 'user_next_page_')))
def handle_user_pagination(call):
    action_type, user_id_str, _ = call.data.split('_', 2)
    user_id = int(user_id_str)

    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "لا تملك الإذن للتنقل في صفحات مستخدم آخر.")
        return
    
    if not is_subscribed(user_id):
        send_force_subscribe_message(call.message.chat.id)
        bot.answer_callback_query(call.id, "عليك الاشتراك في القناة أولاً.")
        return

    current_state = user_pagination_state.get(user_id)
    if not current_state:
        bot.answer_callback_query(call.id, "خطأ: لم يتم العثور على حالة التصفح.")
        list_user_files(call.message)
        return

    current_page = current_state['current_page']
    total_pages = current_state['total_pages']

    if 'prev' in action_type:
        new_page = max(1, current_page - 1)
    else:
        new_page = min(total_pages, current_page + 1)

    if new_page == current_page:
        bot.answer_callback_query(call.id, "لا توجد صفحات أخرى.")
        return

    user_pagination_state[user_id]['current_page'] = new_page
    bot.answer_callback_query(call.id)
    list_user_files(call.message)

@bot.callback_query_handler(func=lambda call: call.data.startswith(('admin_prev_page_', 'admin_next_page_', 'admin_view_user_files_', 'admin_back_to_all_users')))
def handle_admin_pagination(call):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "غير مصرح لك بهذه العملية.")
        return
    
    admin_id = OWNER_ID
    current_admin_state = admin_pagination_state.get(admin_id)

    if call.data == 'admin_back_to_all_users':
        bot.answer_callback_query(call.id, "جارٍ العودة لجميع المستخدمين...")
        display_all_user_files(admin_id, 1, call.message.message_id)
        return
    
    if call.data.startswith('admin_view_user_files_'):
        # Correctly parse target_user_id and page_number
        parts = call.data.split('_')
        target_user_id = int(parts[3])
        page_number = int(parts[5])
        
        # Update admin_pagination_state for the user_specific view
        admin_pagination_state[OWNER_ID] = {
            'current_page': page_number,
            'target_user_id': target_user_id,
            'page_type': 'user_specific'
        }
        
        bot.answer_callback_query(call.id, f"عرض ملفات المستخدم {target_user_id} صفحة {page_number}...")
        display_user_files_for_admin(admin_id, target_user_id, page_number, call.message.message_id)
        return

    if not current_admin_state:
        bot.answer_callback_query(call.id, "خطأ: لم يتم العثور على حالة التصفح للمطور.")
        display_all_user_files(admin_id, 1, call.message.message_id)
        return

    page_type = current_admin_state['page_type']
    current_page = current_admin_state['current_page']
    
    files_per_page = 5
    total_items = 0
    if page_type == 'all_users_overview':
        all_files_data = get_all_user_files_from_db()
        user_file_groups = {}
        for file_info in all_files_data:
            user_id = file_info['user_id']
            if user_id not in user_file_groups:
                user_file_groups[user_id] = []
            user_file_groups[user_id].append(file_info)
        total_items = len(list(user_file_groups.items()))
    elif page_type == 'user_specific':
        target_user_id = current_admin_state.get('target_user_id')
        if target_user_id:
            files = user_files.get(target_user_id, [])
            total_items = len(files)

    total_pages = (total_items + files_per_page - 1) // files_per_page if total_items > 0 else 0


    action_type = call.data.split('_')[1]

    if 'prev' in action_type:
        new_page = max(1, current_page - 1)
    else:
        new_page = min(total_pages, current_page + 1)

    if new_page == current_page:
        bot.answer_callback_query(call.id, "لا توجد صفحات أخرى.")
        return

    bot.answer_callback_query(call.id)
    if page_type == 'all_users_overview':
        display_all_user_files(admin_id, new_page, call.message.message_id)
    elif page_type == 'user_specific':
        target_user_id = current_admin_state.get('target_user_id')
        if target_user_id: # Ensure target_user_id exists
            display_user_files_for_admin(admin_id, target_user_id, new_page, call.message.message_id)

# --- Cleanup on Exit ---
def cleanup():
    script_keys_to_stop = list(bot_scripts.keys())
    for key in script_keys_to_stop:
        if key in bot_scripts: 
            script_info_to_kill = bot_scripts[key]
            kill_process_tree(script_info_to_kill)
            if key in bot_scripts:
                del bot_scripts[key]
atexit.register(cleanup)

# --- Main Execution ---
if __name__ == '__main__':
    keep_alive()
    bot.infinity_polling()

#  ╭───𓆩🛡️𓆪───╮
#  👨‍💻 𝘿𝙚𝙫: @avetaar  
#   📢 𝘾𝙝: @EgyCodes