#!/usr/bin/env python3
# Owner-only Telegram bot host.
# Configure BOT_TOKEN and ADMIN_ID as environment variables before starting.

import os
import sys
import re
import uuid
import time
import signal
import zipfile
import shutil
import logging
import subprocess
from pathlib import Path

import telebot
from telebot import types

# ---- Configuration ----
# ضع BOT_TOKEN و ADMIN_ID في Variables داخل Railway.
TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
BASE_DIR = Path(os.getenv("HOST_DATA_DIR", "host_data")).resolve()
BOTS_DIR = BASE_DIR / "bots"
LOGS_DIR = BASE_DIR / "logs"

BASE_DIR.mkdir(parents=True, exist_ok=True)
BOTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(BASE_DIR / "host.log", encoding="utf-8")]
)
log = logging.getLogger("owner-host")

if not TOKEN:
    raise SystemExit("Missing BOT_TOKEN. Add BOT_TOKEN in Railway Variables.")
if ADMIN_ID <= 0:
    raise SystemExit("Missing/invalid ADMIN_ID. Add your numeric Telegram user ID in Railway Variables.")

bot = telebot.TeleBot(TOKEN, parse_mode=None)
running = {}  # job_id -> {process, name, folder, log_path}


def is_owner(user_id):
    return user_id == ADMIN_ID


def safe_name(name):
    # Do not allow user-supplied paths.
    name = Path(name or "upload").name
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return name[:100] or "upload"


def owner_only(message_or_call):
    user = message_or_call.from_user
    if not is_owner(user.id):
        if hasattr(message_or_call, "message"):
            bot.answer_callback_query(message_or_call.id, "هذا البوت خاص بالمالك فقط.", show_alert=True)
        else:
            bot.reply_to(message_or_call, "هذا البوت خاص بالمالك فقط.")
        return False
    return True


def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("📤 رفع ملف بوت", "📊 حالة البوتات")
    markup.row("⏹ إيقاف بوت", "🗑 حذف بوت")
    return markup


def process_alive(item):
    return item["process"].poll() is None


def bots_status_text():
    if not running:
        return "لا توجد بوتات مسجلة حالياً."
    lines = []
    for job_id, item in list(running.items()):
        state = "🟢 يعمل" if process_alive(item) else f"🔴 متوقف (رمز {item['process'].returncode})"
        lines.append(f"{job_id} — {item['name']}\n{state}")
    return "\n\n".join(lines)


def safe_extract_zip(zip_path, destination):
    """Extract only regular files inside destination; reject path traversal and symlinks."""
    dest = destination.resolve()
    total_size = 0
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > 500:
            raise ValueError("الأرشيف يحتوي على عدد كبير جداً من الملفات.")
        for member in members:
            mode = (member.external_attr >> 16) & 0xFFFF
            if mode and (mode & 0o170000) == 0o120000:
                raise ValueError("الأرشيف يحتوي على رابط رمزي غير مسموح.")
            target = (dest / member.filename).resolve()
            if target != dest and dest not in target.parents:
                raise ValueError("مسار غير آمن داخل ملف ZIP.")
            total_size += member.file_size
            if total_size > 100 * 1024 * 1024:
                raise ValueError("حجم الملفات بعد فك الضغط يتجاوز 100MB.")
        archive.extractall(dest)


def choose_entrypoint(folder):
    py_files = [p for p in folder.rglob("*.py") if p.is_file()]
    if not py_files:
        raise ValueError("لم أجد أي ملف Python داخل الرفع.")
    preferred = ("main.py", "bot.py", "app.py", "run.py")
    for name in preferred:
        for path in py_files:
            if path.name.lower() == name:
                return path
    return py_files[0]


def install_requirements(folder, chat_id):
    requirements = folder / "requirements.txt"
    if not requirements.is_file():
        return
    bot.send_message(chat_id, "📦 وجدت requirements.txt، جارٍ تثبيت المكتبات...")
    # Only the owner can upload jobs. Requirements are still third-party code:
    # install only packages you trust.
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
         "-r", str(requirements)],
        cwd=str(folder), capture_output=True, text=True, timeout=600
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "خطأ غير معروف")[-3000:]
        raise RuntimeError("فشل تثبيت المكتبات:\n" + details)


def launch_job(job_folder, app_folder, entrypoint, display_name, chat_id):
    install_requirements(app_folder, chat_id)
    job_id = uuid.uuid4().hex[:8]
    log_path = LOGS_DIR / f"{job_id}.log"
    log_file = open(log_path, "ab", buffering=0)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(app_folder) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(entrypoint)],
            cwd=str(app_folder),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True
        )
    finally:
        log_file.close()
    running[job_id] = {
        "process": proc,
        "name": display_name,
        "folder": job_folder,
        "log_path": log_path
    }
    time.sleep(1)
    if proc.poll() is not None:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-2500:] if log_path.exists() else ""
        raise RuntimeError(f"الملف توقف مباشرة بعد التشغيل.\n\nآخر السجل:\n{tail or 'لا يوجد سجل.'}")
    return job_id


def stop_job(job_id):
    item = running.get(job_id)
    if not item:
        return False
    proc = item["process"]
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass
    return True


def remove_job(job_id):
    item = running.get(job_id)
    if not item:
        return False
    stop_job(job_id)
    folder = item["folder"]
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    running.pop(job_id, None)
    return True


@bot.message_handler(commands=["start"])
def start(message):
    if not owner_only(message):
        return
    bot.send_message(
        message.chat.id,
        "أهلاً بك في بوت الاستضافة الخاص بك.\n"
        "ارسل ملف .py أو أرشيف .zip يحتوي على بوت بايثون لتشغيله.\n"
        "يمكن أن يحتوي ZIP على requirements.txt لتثبيت المكتبات.",
        reply_markup=main_menu()
    )


@bot.message_handler(func=lambda m: m.text == "📊 حالة البوتات")
def status(message):
    if not owner_only(message):
        return
    bot.reply_to(message, bots_status_text(), reply_markup=main_menu())


@bot.message_handler(func=lambda m: m.text == "📤 رفع ملف بوت")
def ask_upload(message):
    if not owner_only(message):
        return
    bot.reply_to(message, "أرسل ملف .py أو .zip الآن.")


@bot.message_handler(content_types=["document"])
def handle_document(message):
    if not owner_only(message):
        return
    document = message.document
    original = document.file_name or "upload"
    filename = safe_name(original)
    if not (filename.lower().endswith(".py") or filename.lower().endswith(".zip")):
        bot.reply_to(message, "ارفع ملف .py أو .zip فقط.")
        return
    if document.file_size and document.file_size > MAX_UPLOAD_MB * 1024 * 1024:
        bot.reply_to(message, f"حجم الملف أكبر من الحد المسموح ({MAX_UPLOAD_MB}MB).")
        return

    job_folder = BOTS_DIR / uuid.uuid4().hex
    job_folder.mkdir(parents=True, exist_ok=False)
    try:
        file_info = bot.get_file(document.file_id)
        data = bot.download_file(file_info.file_path)
        saved = job_folder / filename
        saved.write_bytes(data)

        if filename.lower().endswith(".zip"):
            extract_dir = job_folder / "app"
            extract_dir.mkdir()
            safe_extract_zip(saved, extract_dir)
            saved.unlink(missing_ok=True)
            app_folder = extract_dir
        else:
            app_folder = job_folder

        entrypoint = choose_entrypoint(app_folder)
        bot.send_message(message.chat.id, f"⏳ جارٍ تجهيز وتشغيل {filename}...")
        job_id = launch_job(job_folder, app_folder, entrypoint, filename, message.chat.id)
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("⏹ إيقاف", callback_data=f"stop:{job_id}"),
            types.InlineKeyboardButton("🗑 حذف", callback_data=f"delete:{job_id}")
        )
        bot.send_message(
            message.chat.id,
            f"🟢 تم تشغيل الملف.\nالاسم: {filename}\nمعرّف التشغيل: {job_id}",
            reply_markup=markup
        )
    except Exception as exc:
        log.exception("Upload/run failed")
        shutil.rmtree(job_folder, ignore_errors=True)
        bot.send_message(message.chat.id, f"❌ تعذر تشغيل الملف:\n{str(exc)[:3500]}")


@bot.message_handler(func=lambda m: m.text == "⏹ إيقاف بوت")
def ask_stop(message):
    if not owner_only(message):
        return
    if not running:
        bot.reply_to(message, "لا توجد بوتات مسجلة.")
        return
    lines = ["أرسل الأمر بهذا الشكل لإيقاف بوت:\n/stop معرّف_التشغيل", "", bots_status_text()]
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["stop"])
def stop_command(message):
    if not owner_only(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        bot.reply_to(message, "الاستخدام: /stop معرّف_التشغيل")
        return
    job_id = parts[1]
    if stop_job(job_id):
        bot.reply_to(message, f"⏹ تم إرسال أمر الإيقاف للبوت {job_id}.")
    else:
        bot.reply_to(message, "لم أجد هذا المعرّف.")


@bot.message_handler(func=lambda m: m.text == "🗑 حذف بوت")
def ask_delete(message):
    if not owner_only(message):
        return
    if not running:
        bot.reply_to(message, "لا توجد بوتات مسجلة.")
        return
    bot.reply_to(message, "أرسل الأمر بهذا الشكل لحذف بوت وملفاته:\n/delete معرّف_التشغيل\n\n" + bots_status_text())


@bot.message_handler(commands=["delete"])
def delete_command(message):
    if not owner_only(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        bot.reply_to(message, "الاستخدام: /delete معرّف_التشغيل")
        return
    if remove_job(parts[1]):
        bot.reply_to(message, f"🗑 تم إيقاف وحذف البوت {parts[1]} وملفاته.")
    else:
        bot.reply_to(message, "لم أجد هذا المعرّف.")


@bot.callback_query_handler(func=lambda call: call.data.startswith(("stop:", "delete:")))
def job_callback(call):
    if not is_owner(call.from_user.id):
        bot.answer_callback_query(call.id, "هذا البوت خاص بالمالك فقط.", show_alert=True)
        return
    action, job_id = call.data.split(":", 1)
    if action == "stop":
        ok = stop_job(job_id)
        answer = "تم إيقاف البوت." if ok else "لم أجد هذا البوت."
    else:
        ok = remove_job(job_id)
        answer = "تم إيقاف البوت وحذف ملفاته." if ok else "لم أجد هذا البوت."
    bot.answer_callback_query(call.id, answer, show_alert=True)


@bot.message_handler(commands=["logs"])
def logs_command(message):
    if not owner_only(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or parts[1] not in running:
        bot.reply_to(message, "الاستخدام: /logs معرّف_التشغيل")
        return
    path = running[parts[1]]["log_path"]
    content = path.read_text(encoding="utf-8", errors="replace")[-3500:] if path.exists() else "لا يوجد سجل."
    bot.reply_to(message, content or "السجل فارغ.")


@bot.message_handler(commands=["help"])
def help_command(message):
    if not owner_only(message):
        return
    bot.reply_to(
        message,
        "الأوامر:\n"
        "/start — القائمة\n"
        "/stop ID — إيقاف بوت\n"
        "/delete ID — إيقاف وحذف بوت\n"
        "/logs ID — عرض آخر سجل\n\n"
        "ارفع ملف .py أو .zip لتشغيله."
    )


log.info("Owner-only host started")
bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
