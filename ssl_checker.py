import ssl
import socket
import json
import os
import telebot
from telebot import types
from datetime import datetime, timezone
from dotenv import load_dotenv

# ================= НАСТРОЙКА ОКРУЖЕНИЯ (.env) =================
ENV_FILE = '.env'

if not os.path.exists(ENV_FILE):
    with open(ENV_FILE, 'w', encoding='utf-8') as f:
        f.write("BOT_TOKEN=твой_токен_здесь\n")
        f.write("ADMIN_ID=твой_id_здесь\n")
    print(f"[!] Файл {ENV_FILE} не найден. Я создал шаблон.")
    exit(0)

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = os.getenv('ADMIN_ID')

if not BOT_TOKEN or BOT_TOKEN == 'твой_токен_здесь':
    print("[!] Ошибка: Укажи реальный BOT_TOKEN в файле .env!")
    exit(1)
if not ADMIN_ID or ADMIN_ID == 'твой_id_здесь':
    print("[!] Ошибка: Укажи свой ADMIN_ID в файле .env! (только цифры)")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# ================= БАЗА ДАННЫХ =================
DB_FILE = 'domains.json'

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {"operators": [], "domains": []}

def save_db(db_data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db_data, f, indent=4)

# ================= РОЛИ И ПРАВА =================
def is_admin(chat_id):
    return str(chat_id) == str(ADMIN_ID)

def is_operator(chat_id):
    db = load_db()
    return str(chat_id) in db.get("operators", [])

def has_access(chat_id):
    return is_admin(chat_id) or is_operator(chat_id)

def get_menu(chat_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_check = types.KeyboardButton('🔍 Проверить всё')
    btn_list = types.KeyboardButton('📋 Список доменов')
    
    if is_admin(chat_id):
        btn_add = types.KeyboardButton('➕ Добавить домен')
        btn_del = types.KeyboardButton('❌ Удалить домен')
        btn_add_op = types.KeyboardButton('➕ Добавить оператора')
        btn_del_op = types.KeyboardButton('❌ Удалить оператора')
        markup.add(btn_check, btn_list, btn_add, btn_del, btn_add_op, btn_del_op)
    else:
        markup.add(btn_check, btn_list)
        
    return markup

def get_cancel_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton('❌ Отмена'))
    return markup

# ================= ЛОГИКА ПРОВЕРКИ =================
def check_ssl_expiry(domain: str):
    context = ssl.create_default_context()
    try:
        with socket.create_connection((domain, 443), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                expire_date_str = cert['notAfter']
                expire_date = datetime.strptime(expire_date_str, '%b %d %H:%M:%S %Y %Z')
                current_time = datetime.now(timezone.utc).replace(tzinfo=None)
                days_left = (expire_date - current_time).days
                return days_left, expire_date
    except ssl.SSLCertVerificationError as e:
        return None, f"Ошибка верификации: {e}"
    except socket.timeout:
        return None, "Таймаут соединения"
    except Exception as e:
        return None, f"Ошибка: {e}"

# ================= ХЕНДЛЕРЫ TELEGRAM =================

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    chat_id = message.chat.id
    if not has_access(chat_id):
        bot.send_message(chat_id, f"⛔️ Доступ запрещен.\nВаш ID: `{chat_id}`", parse_mode="Markdown")
        return
        
    role = "Администратор" if is_admin(chat_id) else "Оператор"
    text = f"Привет! Я бот для мониторинга SSL-сертификатов.\nВаша роль: **{role}**\nВыберите действие в меню:"
    bot.send_message(chat_id, text, reply_markup=get_menu(chat_id), parse_mode="Markdown")

# --- БЛОК ОПЕРАТОРА И АДМИНА ---

@bot.message_handler(func=lambda message: message.text == '🔍 Проверить всё' or message.text == '/check')
def check_all(message):
    if not has_access(message.chat.id): return
    db = load_db()
    domains = db.get("domains", [])
    
    if not domains:
        bot.reply_to(message, "Список доменов пуст.")
        return
        
    bot.send_message(message.chat.id, "⏳ Запускаю проверку...")
    response = "Результаты:\n\n"
    for domain in domains:
        days, result = check_ssl_expiry(domain)
        if days is not None:
            if days < 14:
                response += f"⚠️ {domain}: осталось {days} дн. (до {result.strftime('%Y-%m-%d')})\n"
            else:
                response += f"✅ {domain}: осталось {days} дн. (до {result.strftime('%Y-%m-%d')})\n"
        else:
            response += f"❌ {domain}: {result}\n"
            
    bot.send_message(message.chat.id, response)

@bot.message_handler(func=lambda message: message.text == '📋 Список доменов' or message.text == '/list')
def list_domains(message):
    if not has_access(message.chat.id): return
    db = load_db()
    domains = db.get("domains", [])
    if not domains:
        bot.reply_to(message, "Список доменов пуст.")
        return
    response = "Мониторинг доменов:\n\n"
    for idx, d in enumerate(domains, 1):
        response += f"{idx}. {d}\n"
    bot.send_message(message.chat.id, response)

# --- БЛОК АДМИНА (Управление доменами) ---

@bot.message_handler(func=lambda message: message.text == '➕ Добавить домен')
def add_domain_start(message):
    if not is_admin(message.chat.id): return
    msg = bot.send_message(message.chat.id, "Напишите домен (например: google.com):", reply_markup=get_cancel_markup())
    bot.register_next_step_handler(msg, process_add_domain)

def process_add_domain(message):
    if message.text == '❌ Отмена' or (message.text and message.text.startswith('/')):
        bot.send_message(message.chat.id, "Отмена.", reply_markup=get_menu(message.chat.id))
        return

    new_domain = message.text.strip().lower()
    db = load_db()
    
    if new_domain in db["domains"]:
        bot.send_message(message.chat.id, f"Домен {new_domain} уже есть в базе.", reply_markup=get_menu(message.chat.id))
    else:
        db["domains"].append(new_domain)
        save_db(db)
        bot.send_message(message.chat.id, f"✅ Домен {new_domain} добавлен!", reply_markup=get_menu(message.chat.id))

@bot.message_handler(func=lambda message: message.text == '❌ Удалить домен')
def del_domain_start(message):
    if not is_admin(message.chat.id): return
    db = load_db()
    domains = db.get("domains", [])
    
    if not domains:
        bot.send_message(message.chat.id, "База доменов пуста, удалять нечего.")
        return

    text = "Выберите номер домена для удаления:\n\n"
    for i, d in enumerate(domains, 1):
        text += f"{i}. {d}\n"
        
    msg = bot.send_message(message.chat.id, text, reply_markup=get_cancel_markup())
    bot.register_next_step_handler(msg, process_del_domain)

def process_del_domain(message):
    if message.text == '❌ Отмена' or (message.text and message.text.startswith('/')):
        bot.send_message(message.chat.id, "Отмена.", reply_markup=get_menu(message.chat.id))
        return

    if not message.text.isdigit():
        bot.send_message(message.chat.id, "Нужно было ввести цифру. Отмена.", reply_markup=get_menu(message.chat.id))
        return

    idx = int(message.text) - 1
    db = load_db()
    domains = db.get("domains", [])

    if 0 <= idx < len(domains):
        domain_to_del = domains.pop(idx)
        save_db(db)
        bot.send_message(message.chat.id, f"🗑 Домен {domain_to_del} удален.", reply_markup=get_menu(message.chat.id))
    else:
        bot.send_message(message.chat.id, "Нет домена с таким номером. Отмена.", reply_markup=get_menu(message.chat.id))

# --- БЛОК АДМИНА (Управление операторами) ---

@bot.message_handler(func=lambda message: message.text == '➕ Добавить оператора')
def add_op_start(message):
    if not is_admin(message.chat.id): return
    db = load_db()
    
    # Убрали специальную кнопку контакта, оставляем только кнопку Отмена
    markup = get_cancel_markup()

    text = (f"Текущие операторы: {', '.join(db['operators']) if db['operators'] else 'нет'}\n\n"
            "Чтобы добавить оператора, используйте один из способов:\n"
            "1. Нажмите на **скрепку 📎** внизу экрана, выберите **Контакт 👤** и отправьте нужного человека из записной книжки.\n"
            "2. Или просто введите его Telegram ID цифрами.")
            
    msg = bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_add_op)

def process_add_op(message):
    if (message.text and message.text == '❌ Отмена') or (message.text and message.text.startswith('/')):
        bot.send_message(message.chat.id, "Отмена.", reply_markup=get_menu(message.chat.id))
        return

    op_id = None

    # Если пользователь прикрепил контакт через скрепку
    if message.content_type == 'contact':
        if message.contact.user_id:
            op_id = str(message.contact.user_id)
        else:
            # Важный нюанс Telegram: если у человека скрыт ID настройками приватности, 
            # Telegram не передаст его боту даже при отправке контакта.
            bot.send_message(message.chat.id, "У этого контакта скрыт Telegram ID настройками приватности, либо его нет в Telegram. Попробуйте ввести ID вручную.", reply_markup=get_menu(message.chat.id))
            return
    # Если пользователь ввел ID вручную
    elif message.content_type == 'text' and message.text.isdigit():
        op_id = message.text.strip()
    else:
        bot.send_message(message.chat.id, "Ожидался ID (цифры) или контакт. Отмена.", reply_markup=get_menu(message.chat.id))
        return

    db = load_db()
    if op_id in db["operators"]:
        bot.send_message(message.chat.id, f"Пользователь {op_id} уже является оператором.", reply_markup=get_menu(message.chat.id))
    else:
        db["operators"].append(op_id)
        save_db(db)
        bot.send_message(message.chat.id, f"✅ Оператор {op_id} добавлен!", reply_markup=get_menu(message.chat.id))

@bot.message_handler(func=lambda message: message.text == '❌ Удалить оператора')
def del_op_start(message):
    if not is_admin(message.chat.id): return
    db = load_db()
    ops = db.get("operators", [])
    
    if not ops:
        bot.send_message(message.chat.id, "Список операторов пуст.")
        return

    text = "Выберите номер оператора для удаления:\n\n"
    for i, op in enumerate(ops, 1):
        text += f"{i}. {op}\n"
        
    msg = bot.send_message(message.chat.id, text, reply_markup=get_cancel_markup())
    bot.register_next_step_handler(msg, process_del_op)

def process_del_op(message):
    if message.text == '❌ Отмена' or (message.text and message.text.startswith('/')):
        bot.send_message(message.chat.id, "Отмена.", reply_markup=get_menu(message.chat.id))
        return

    if not message.text.isdigit():
        bot.send_message(message.chat.id, "Нужно было ввести цифру. Отмена.", reply_markup=get_menu(message.chat.id))
        return

    idx = int(message.text) - 1
    db = load_db()
    ops = db.get("operators", [])

    if 0 <= idx < len(ops):
        op_to_del = ops.pop(idx)
        save_db(db)
        bot.send_message(message.chat.id, f"🗑 Оператор {op_to_del} удален.", reply_markup=get_menu(message.chat.id))
    else:
        bot.send_message(message.chat.id, "Нет оператора с таким номером. Отмена.", reply_markup=get_menu(message.chat.id))

# ================= ЗАПУСК БОТА =================
if __name__ == '__main__':
    if not os.path.exists(DB_FILE):
        save_db({"operators": [], "domains": []})
        
    print("Бот запущен. Нажми Ctrl+C для остановки.")
    bot.infinity_polling()
