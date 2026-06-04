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

# Если файла нет, создаем шаблон и останавливаем скрипт
if not os.path.exists(ENV_FILE):
    with open(ENV_FILE, 'w', encoding='utf-8') as f:
        f.write("BOT_TOKEN=твой_токен_здесь\n")
        f.write("ADMIN_ID=твой_id_здесь\n")
    print(f"[!] Файл {ENV_FILE} не найден. Я создал шаблон.")
    print(f"[!] Заполни BOT_TOKEN и ADMIN_ID в файле {ENV_FILE} и запусти скрипт заново.")
    exit(0)

# Загружаем переменные из .env
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

# Подключаем прокси (если нужно, раскомментируй)
# from telebot import apihelper
# apihelper.proxy = {'http': 'http://127.0.0.1:10808', 'https': 'http://127.0.0.1:10808'}

# ================= БАЗА ДАННЫХ =================
DB_FILE = 'domains.json'

def load_db():
    """Загружает базу. Формат: {'operators': ['id1'], 'domains': ['google.com']}"""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {"operators": [], "domains": []}

def save_db(db_data):
    """Сохраняет базу в JSON."""
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

# Создаем клавиатуру в зависимости от роли
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
        # Оператор видит только это
        markup.add(btn_check, btn_list)
        
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
        bot.send_message(chat_id, f"⛔️ Доступ запрещен.\nВаш ID: `{chat_id}`\nПередайте его администратору для получения прав.", parse_mode="Markdown")
        return
        
    role = "Администратор" if is_admin(chat_id) else "Оператор"
    text = f"Привет! Я бот для мониторинга SSL-сертификатов.\nВаша роль: **{role}**\nВыберите действие в меню:"
    bot.send_message(chat_id, text, reply_markup=get_menu(chat_id), parse_mode="Markdown")

# --- БЛОК ОПЕРАТОРА И АДМИНА (Доступно обоим) ---

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
    msg = bot.send_message(message.chat.id, "Напишите домен (например: google.com):", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, process_add_domain)

def process_add_domain(message):
    if message.text.startswith('/') or ' ' in message.text or message.text.startswith('🔍'):
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
    msg = bot.send_message(message.chat.id, "Напишите домен для удаления:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, process_del_domain)

def process_del_domain(message):
    domain_to_del = message.text.strip().lower()
    db = load_db()
    
    if domain_to_del in db["domains"]:
        db["domains"].remove(domain_to_del)
        save_db(db)
        bot.send_message(message.chat.id, f"🗑 Домен {domain_to_del} удален.", reply_markup=get_menu(message.chat.id))
    else:
        bot.send_message(message.chat.id, f"Домен {domain_to_del} не найден.", reply_markup=get_menu(message.chat.id))

# --- БЛОК АДМИНА (Управление операторами) ---

@bot.message_handler(func=lambda message: message.text == '➕ Добавить оператора')
def add_op_start(message):
    if not is_admin(message.chat.id): return
    db = load_db()
    text = f"Текущие операторы: {', '.join(db['operators']) if db['operators'] else 'нет'}\n\nВведите Telegram ID нового оператора (только цифры):"
    msg = bot.send_message(message.chat.id, text, reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, process_add_op)

def process_add_op(message):
    if not message.text.isdigit():
        bot.send_message(message.chat.id, "ID должен состоять только из цифр. Отмена.", reply_markup=get_menu(message.chat.id))
        return
        
    op_id = message.text.strip()
    db = load_db()
    
    if op_id in db["operators"]:
        bot.send_message(message.chat.id, f"Пользователь {op_id} уже является оператором.", reply_markup=get_menu(message.chat.id))
    else:
        db["operators"].append(op_id)
        save_db(db)
        bot.send_message(message.chat.id, f"✅ Оператор {op_id} добавлен! Теперь он может проверять домены.", reply_markup=get_menu(message.chat.id))

@bot.message_handler(func=lambda message: message.text == '❌ Удалить оператора')
def del_op_start(message):
    if not is_admin(message.chat.id): return
    msg = bot.send_message(message.chat.id, "Введите Telegram ID оператора для удаления:", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, process_del_op)

def process_del_op(message):
    op_id = message.text.strip()
    db = load_db()
    
    if op_id in db["operators"]:
        db["operators"].remove(op_id)
        save_db(db)
        bot.send_message(message.chat.id, f"🗑 Оператор {op_id} удален. У него больше нет доступа.", reply_markup=get_menu(message.chat.id))
    else:
        bot.send_message(message.chat.id, f"ID {op_id} не найден в списке операторов.", reply_markup=get_menu(message.chat.id))

# ================= ЗАПУСК БОТА =================
if __name__ == '__main__':
    if not os.path.exists(DB_FILE):
        save_db({"operators": [], "domains": []})
        
    print("Бот запущен. Нажми Ctrl+C для остановки.")
    bot.infinity_polling()
