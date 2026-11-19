#!/usr/bin/env python3
# Public Telegram bot-host manager.
# Set BOT_TOKEN in Railway Variables. Owner username: @t7_m6

import os
import sys
import re
import json
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

TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_USERNAME = "t7_m6"  # بدون @
OWNER_ID = int(os.getenv("OWNER_ID", "0") or "0")  # اختياري كنسخة احتياطية
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))

BASE_DIR = Path(os.getenv("HOST_DATA_DIR", "host_data")).resolve()
BOTS_DIR = BASE_DIR / "bots"
LOGS_DIR = BASE_DIR / "logs"
ROLES_FILE = BASE_DIR / "roles.json"

BASE_DIR.mkdir(parents=True, exist_ok=True)
BOTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE_DIR / "host.log", encoding="utf-8")
    ],
)
log = logging.getLogger("public-host")

if not TOKEN:
    raise SystemExit("BOT_TOKEN مفقود. أضفه في Railway > Variables.")

bot = telebot.TeleBot(TOKEN, parse_mode=None)
running = {}


def load_roles():
    if not ROLES_FILE.exists():
        return {"admins": [], "members": [], "pending": [], "owner_chat_id": 0}
    try:
        data = json.loads(ROLES_FILE.read_text(encoding="utf-8"))
        return {
            "admins": [int(x) for x in data.get("admins", [])],
            "members": [int(x) for x in data.get("members", [])],
            "pending": [int(x) for x in data.get("pending", [])],
            "owner_chat_id": int(data.get("owner_chat_id", 0)),
        }
    except Exception:
        log.exception("Could not read roles file; starting with empty role lists")
        return {"admins": [], "members": [], "pending": [], "owner_chat_id": 0}


roles = load_roles()


def save_roles():
    temp = ROLES_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(roles, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(ROLES_FILE)


def is_owner(user):
    username = (getattr(user, "username", None) or "").lower()
    return username == OWNER_USERNAME.lower() or (
        OWNER_ID > 0 and getattr(user, "id", 0) == OWNER_ID
    )


def is_admin(user):
    return is_owner(user) or user.id in roles["admins"]


def is_member(user):
    return is_admin(user) or user.id in roles["members"]


def deny(message, text="هذه الخدمة متاحة للأعضاء المسجلين فقط."):
    bot.reply_to(message, text)


def admin_required(message):
    if not is_admin(message.from_user):
        deny(message, "ليس لديك صلاحية الإدارة. تواصل مع مالك البوت.")
        return False
    return True


def owner_required(message):
    if not is_owner(message.from_user):
        deny(message, "هذا الأمر خاص بمالك البوت.")
        return False
    return True


def safe_name(name):
    name = Path(name or "upload").name
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return name[:100] or "upload"


def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("📤 رفع ملف بوت", "📊 حالة البوتات")
    markup.row("⏹ إيقاف بوت", "🗑 حذف بوت")
    markup.row("❓ المساعدة")
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
            start_new_session=True,
        )
    finally:
        log_file.close()
    running[job_id] = {
        "process": proc, "name": display_name,
        "folder": job_folder, "log_path": log_path,
    }
    time.sleep(1)
    if proc.poll() is not None:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-2500:] if log_path.exists() else ""
        running.pop(job_id, None)
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
    shutil.rmtree(item["folder"], ignore_errors=True)
    running.pop(job_id, None)
    return True


def parse_target_id(message):
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "استعمل الأمر هكذا: /command USER_ID\nأرسل /id لمعرفة رقم حسابك.")
        return None
    target = int(parts[1])
    if target <= 0:
        bot.reply_to(message, "رقم المستخدم غير صحيح.")
        return None
    return target


@bot.message_handler(commands=["start"])
def start(message):
    user = message.from_user
    if is_owner(user):
        roles["owner_chat_id"] = message.chat.id
        save_roles()
        bot.send_message(
            message.chat.id,
            "أهلاً بك يا مالك البوت @t7_m6.\n"
            "كل مستخدم جديد يحتاج موافقتك قبل الدخول.\n"
            "استعمل /pending لمراجعة طلبات الانضمام.",
            reply_markup=main_menu(),
        )
        return

    if is_admin(user):
        bot.send_message(
            message.chat.id,
            "أهلاً بك في لوحة إدارة الاستضافة.\n"
            "يمكن للمشرفين رفع ملفات .py أو .zip وتشغيلها.",
            reply_markup=main_menu(),
        )
        return

    if is_member(user):
        bot.send_message(message.chat.id, "تم قبولك مسبقاً. مرحباً بك!")
        return

    uid = user.id
    if uid not in roles["pending"]:
        roles["pending"].append(uid)
        save_roles()

    bot.send_message(
        message.chat.id,
        "تم إرسال طلب الانضمام إلى المالك @t7_m6. لن تتمكن من استخدام البوت حتى يوافق."
    )
    owner_chat_id = roles.get("owner_chat_id", 0)
    if owner_chat_id:
        username = f"@{user.username}" if user.username else "بدون اسم مستخدم"
        full_name = (user.first_name or "") + (f" {user.last_name}" if user.last_name else "")
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ قبول", callback_data=f"approve:{uid}"),
            types.InlineKeyboardButton("❌ رفض", callback_data=f"reject:{uid}"),
        )
        try:
            bot.send_message(
                owner_chat_id,
                f"طلب انضمام جديد\nالاسم: {full_name}\nالمستخدم: {username}\nID: {uid}",
                reply_markup=markup,
            )
        except Exception:
            log.exception("Could not notify owner")
    else:
        bot.send_message(
            message.chat.id,
            "يجب على المالك @t7_m6 فتح البوت وإرسال /start أولاً حتى تصله طلبات الموافقة."
        )


@bot.message_handler(commands=["pending"])
def pending_requests(message):
    if not owner_required(message):
        return
    if not roles["pending"]:
        bot.reply_to(message, "لا توجد طلبات انضمام معلقة.")
        return
    bot.reply_to(
        message,
        "طلبات الانضمام المعلقة (IDs):\\n" +
        "\\n".join(str(uid) for uid in roles["pending"]) +
        "\\nإذا لم تظهر أزرار القرار، اطلب من المستخدم إرسال /start مجدداً."
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith(("approve:", "reject:")))
def membership_decision(call):
    if not is_owner(call.from_user):
        bot.answer_callback_query(call.id, "هذا القرار خاص بمالك البوت.", show_alert=True)
        return
    action, raw_id = call.data.split(":", 1)
    if not raw_id.isdigit():
        bot.answer_callback_query(call.id, "رقم مستخدم غير صحيح.", show_alert=True)
        return
    target_id = int(raw_id)
    if target_id not in roles["pending"]:
        bot.answer_callback_query(call.id, "هذا الطلب تمت معالجته أو غير موجود.", show_alert=True)
        return

    roles["pending"].remove(target_id)
    if action == "approve":
        if target_id not in roles["members"] and target_id not in roles["admins"]:
            roles["members"].append(target_id)
        owner_reply = "تم قبول المستخدم وإضافته إلى الأعضاء."
        user_reply = "✅ وافق المالك على طلب انضمامك. مرحباً بك!"
    else:
        owner_reply = "تم رفض طلب الانضمام."
        user_reply = "❌ رفض المالك طلب انضمامك."
    save_roles()
    try:
        bot.send_message(target_id, user_reply)
    except Exception:
        log.info("Could not notify user %s", target_id)
    bot.answer_callback_query(call.id, owner_reply, show_alert=True)
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass


@bot.message_handler(commands=["id"])
def my_id(message):
    bot.reply_to(
        message,
        f"رقم حسابك: {message.from_user.id}\nاسم المستخدم: @{message.from_user.username or 'بدون اسم مستخدم'}"
    )


@bot.message_handler(commands=["help"])
def help_command(message):
    if is_admin(message.from_user):
        text = (
            "أوامر الإدارة:\n"
            "/start — القائمة\n/id — رقم حسابك\n"
            "/stop ID — إيقاف بوت\n/delete ID — حذف بوت\n/logs ID — السجل\n\n"
            "أوامر المالك @t7_m6:\n"
            "/addadmin USER_ID — إضافة مشرف\n"
            "/deladmin USER_ID — حذف مشرف\n"
            "/addmember USER_ID — إضافة عضو\n"
            "/delmember USER_ID — حذف عضو\n"
            "/admins — قائمة المشرفين\n/members — قائمة الأعضاء"
        )
    elif is_member(message.from_user):
        text = "أنت عضو. أرسل /id لمعرفة رقم حسابك. رفع وتشغيل الملفات للمشرفين فقط."
    else:
        text = "بوت استضافة عام. للانضمام أرسل /id ثم أرسل رقمك إلى @t7_m6."
    bot.reply_to(message, text)


@bot.message_handler(commands=["addadmin"])
def add_admin(message):
    if not owner_required(message):
        return
    target = parse_target_id(message)
    if target is None:
        return
    if target not in roles["admins"]:
        roles["admins"].append(target)
    if target in roles["members"]:
        roles["members"].remove(target)
    save_roles()
    bot.reply_to(message, f"تمت إضافة {target} كمشرف.")


@bot.message_handler(commands=["deladmin"])
def del_admin(message):
    if not owner_required(message):
        return
    target = parse_target_id(message)
    if target is None:
        return
    if target in roles["admins"]:
        roles["admins"].remove(target)
        save_roles()
        bot.reply_to(message, f"تم حذف المشرف {target}.")
    else:
        bot.reply_to(message, "هذا المستخدم ليس مشرفاً.")


@bot.message_handler(commands=["addmember"])
def add_member(message):
    if not owner_required(message):
        return
    target = parse_target_id(message)
    if target is None:
        return
    if target not in roles["admins"] and target not in roles["members"]:
        roles["members"].append(target)
    save_roles()
    bot.reply_to(message, f"تمت إضافة {target} كعضو.")


@bot.message_handler(commands=["delmember"])
def del_member(message):
    if not owner_required(message):
        return
    target = parse_target_id(message)
    if target is None:
        return
    if target in roles["members"]:
        roles["members"].remove(target)
        save_roles()
        bot.reply_to(message, f"تم حذف العضو {target}.")
    else:
        bot.reply_to(message, "هذا المستخدم ليس ضمن الأعضاء.")


@bot.message_handler(commands=["admins"])
def list_admins(message):
    if not owner_required(message):
        return
    ids = [str(x) for x in roles["admins"]]
    bot.reply_to(message, "المشرفون:\n" + ("\n".join(ids) if ids else "لا يوجد مشرفون إضافيون."))


@bot.message_handler(commands=["members"])
def list_members(message):
    if not owner_required(message):
        return
    ids = [str(x) for x in roles["members"]]
    bot.reply_to(message, "الأعضاء:\n" + ("\n".join(ids) if ids else "لا يوجد أعضاء."))


@bot.message_handler(func=lambda m: m.text == "📊 حالة البوتات")
def status(message):
    if not admin_required(message):
        return
    bot.reply_to(message, bots_status_text(), reply_markup=main_menu())


@bot.message_handler(func=lambda m: m.text == "📤 رفع ملف بوت")
def ask_upload(message):
    if not admin_required(message):
        return
    bot.reply_to(message, "أرسل ملف .py أو .zip الآن.")


@bot.message_handler(content_types=["document"])
def handle_document(message):
    if not admin_required(message):
        deny(message, "رفع وتشغيل الملفات للمشرفين فقط.")
        return
    document = message.document
    filename = safe_name(document.file_name or "upload")
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
            types.InlineKeyboardButton("🗑 حذف", callback_data=f"delete:{job_id}"),
        )
        bot.send_message(
            message.chat.id,
            f"🟢 تم تشغيل الملف.\nالاسم: {filename}\nمعرّف التشغيل: {job_id}",
            reply_markup=markup,
        )
    except Exception as exc:
        log.exception("Upload/run failed")
        shutil.rmtree(job_folder, ignore_errors=True)
        bot.send_message(message.chat.id, f"❌ تعذر تشغيل الملف:\n{str(exc)[:3500]}")


@bot.message_handler(func=lambda m: m.text == "⏹ إيقاف بوت")
def ask_stop(message):
    if not admin_required(message):
        return
    bot.reply_to(message, "أرسل: /stop معرّف_التشغيل\n\n" + bots_status_text())


@bot.message_handler(commands=["stop"])
def stop_command(message):
    if not admin_required(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        bot.reply_to(message, "الاستخدام: /stop معرّف_التشغيل")
    elif stop_job(parts[1]):
        bot.reply_to(message, f"⏹ تم إرسال أمر الإيقاف للبوت {parts[1]}.")
    else:
        bot.reply_to(message, "لم أجد هذا المعرّف.")


@bot.message_handler(func=lambda m: m.text == "🗑 حذف بوت")
def ask_delete(message):
    if not admin_required(message):
        return
    bot.reply_to(message, "أرسل: /delete معرّف_التشغيل\n\n" + bots_status_text())


@bot.message_handler(commands=["delete"])
def delete_command(message):
    if not admin_required(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        bot.reply_to(message, "الاستخدام: /delete معرّف_التشغيل")
    elif remove_job(parts[1]):
        bot.reply_to(message, f"🗑 تم إيقاف وحذف البوت {parts[1]} وملفاته.")
    else:
        bot.reply_to(message, "لم أجد هذا المعرّف.")


@bot.callback_query_handler(func=lambda call: call.data.startswith(("stop:", "delete:")))
def job_callback(call):
    if not is_admin(call.from_user):
        bot.answer_callback_query(call.id, "ليس لديك صلاحية الإدارة.", show_alert=True)
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
    if not admin_required(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or parts[1] not in running:
        bot.reply_to(message, "الاستخدام: /logs معرّف_التشغيل")
        return
    path = running[parts[1]]["log_path"]
    content = path.read_text(encoding="utf-8", errors="replace")[-3500:] if path.exists() else "لا يوجد سجل."
    bot.reply_to(message, content or "السجل فارغ.")


log.info("Public host manager started")
bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
