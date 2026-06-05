import ssl
import socket
import json
import os
import re
import logging
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import telebot
from telebot import types
from dotenv import load_dotenv

# ============================================================
#  НАСТРОЙКА ОКРУЖЕНИЯ (Docker Environment / .env)
# ============================================================
from dotenv import load_dotenv

# load_dotenv безопасно подгрузит .env, если он есть (для локальных тестов).
# Если его нет (в Docker), он просто пойдет дальше и возьмет системные переменные.
load_dotenv() 

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = os.getenv('ADMIN_ID')

def _env_int(name, default):
    """Безопасно читает целое число из окружения."""
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default

CHECK_INTERVAL_HOURS = _env_int('CHECK_INTERVAL_HOURS', 12)
WARNING_DAYS = _env_int('WARNING_DAYS', 30)
CRITICAL_DAYS = _env_int('CRITICAL_DAYS', 14)
MAX_WORKERS = _env_int('MAX_WORKERS', 10)
CONNECT_TIMEOUT = _env_int('CONNECT_TIMEOUT', 7)

if not BOT_TOKEN or BOT_TOKEN == 'твой_токен_здесь':
    print("[!] Ошибка: Переменная BOT_TOKEN не задана в окружении!")
    raise SystemExit(1)
if not ADMIN_ID or ADMIN_ID == 'твой_id_здесь':
    print("[!] Ошибка: Переменная ADMIN_ID не задана в окружении!")
    raise SystemExit(1)

# ============================================================
#  ЛОГИРОВАНИЕ
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('ssl_bot.log', encoding='utf-8'),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger('ssl_bot')

bot = telebot.TeleBot(BOT_TOKEN)

# Время последней авто-проверки (для экрана статуса)
LAST_CHECK = None

# ============================================================
#  БАЗА ДАННЫХ (потокобезопасная, с миграцией формата)
# ============================================================
DB_FILE = 'domains.json'
_DB_LOCK = threading.RLock()


def _make_domain_entry(name, port=443):
    return {
        "name": name,
        "port": port,
        "last_status": None,
        "last_checked": None,
        "days_left": None,
        "added_at": datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }


def _migrate(db):
    """Старый формат хранил домены как список строк — переводим в словари."""
    changed = False
    domains = db.get("domains", [])
    new_domains = []
    for d in domains:
        if isinstance(d, str):
            new_domains.append(_make_domain_entry(d))
            changed = True
        elif isinstance(d, dict) and "name" in d:
            d.setdefault("port", 443)
            d.setdefault("last_status", None)
            d.setdefault("last_checked", None)
            d.setdefault("days_left", None)
            new_domains.append(d)
    db["domains"] = new_domains
    db.setdefault("operators", [])
    # операторы храним как строки
    db["operators"] = [str(o) for o in db["operators"]]
    return db, changed


def load_db():
    with _DB_LOCK:
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                logger.warning("Не удалось прочитать %s, создаю новую базу.", DB_FILE)
                data = {"operators": [], "domains": []}
        else:
            data = {"operators": [], "domains": []}
        data, changed = _migrate(data)
        if changed:
            _save_unlocked(data)
        return data


def _save_unlocked(db_data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db_data, f, indent=4, ensure_ascii=False)


def save_db(db_data):
    with _DB_LOCK:
        _save_unlocked(db_data)

# ============================================================
#  РОЛИ И ПРАВА
# ============================================================
def is_admin(chat_id):
    return str(chat_id) == str(ADMIN_ID)


def is_operator(chat_id):
    return str(chat_id) in load_db().get("operators", [])


def has_access(chat_id):
    return is_admin(chat_id) or is_operator(chat_id)

# ============================================================
#  КЛАВИАТУРЫ
# ============================================================
def get_menu(chat_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_check = types.KeyboardButton('🔍 Проверить всё')
    btn_one = types.KeyboardButton('🔎 Проверить один')
    btn_list = types.KeyboardButton('📋 Список доменов')
    btn_status = types.KeyboardButton('📊 Статус')

    if is_admin(chat_id):
        markup.add(btn_check, btn_one)
        markup.add(btn_list, btn_status)
        markup.add(types.KeyboardButton('➕ Добавить домен'),
                   types.KeyboardButton('❌ Удалить домен'))
        markup.add(types.KeyboardButton('➕ Добавить оператора'),
                   types.KeyboardButton('❌ Удалить оператора'))
    else:
        markup.add(btn_check, btn_one)
        markup.add(btn_list, btn_status)
    return markup


def get_cancel_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton('❌ Отмена'))
    return markup


def _is_cancel(message):
    """Любая команда или 'Отмена' прерывают пошаговый ввод."""
    t = message.text
    return (t == '❌ Отмена') or (t is not None and t.startswith('/'))

# ============================================================
#  РАЗБОР И ВАЛИДАЦИЯ ДОМЕНА
# ============================================================
DOMAIN_RE = re.compile(
    r'^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)'
    r'(\.(?!-)[a-z0-9-]{1,63}(?<!-))*\.[a-z]{2,}$'
)


def normalize_domain(raw):
    """
    Приводит ввод к (хост, порт). Понимает протокол, путь и порт:
      'https://example.com:8443/path' -> ('example.com', 8443)
    Возвращает (host, port) или (None, None) при невалидном вводе.
    """
    if not raw:
        return None, None
    raw = raw.strip().lower()
    if '://' in raw:
        raw = raw.split('://', 1)[1]
    raw = raw.split('/', 1)[0]   # убираем путь
    raw = raw.split('?', 1)[0]
    port = 443
    if ':' in raw:
        host, _, p = raw.rpartition(':')
        if p.isdigit() and 1 <= int(p) <= 65535:
            raw, port = host, int(p)
    if not DOMAIN_RE.match(raw):
        return None, None
    return raw, port

# ============================================================
#  ПРОВЕРКА SSL
# ============================================================
CATEGORY_ICON = {
    "ok": "✅",
    "warning": "⚠️",
    "critical": "🟠",
    "expired": "⛔️",
    "error": "❌",
}
CATEGORY_SEVERITY = {"ok": 0, "warning": 1, "critical": 2, "expired": 3, "error": 3}


def _categorize(days_left, error):
    if error or days_left is None:
        return "error"
    if days_left < 0:
        return "expired"
    if days_left <= CRITICAL_DAYS:
        return "critical"
    if days_left <= WARNING_DAYS:
        return "warning"
    return "ok"


def _plural_days(n):
    """Русские окончания: 1 день, 2 дня, 5 дней."""
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return "дней"
    last = n % 10
    if last == 1:
        return "день"
    if 2 <= last <= 4:
        return "дня"
    return "дней"


def check_domain_raw(host, port=443, timeout=None):
    """Подключается к хосту, читает сертификат и возвращает структурированный результат."""
    if timeout is None:
        timeout = CONNECT_TIMEOUT
    result = {
        "host": host, "port": port,
        "days_left": None, "expire_date": None,
        "not_before": None, "issuer": "—", "error": None,
    }
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()

        not_after = datetime.strptime(
            cert['notAfter'], '%b %d %H:%M:%S %Y %Z'
        ).replace(tzinfo=timezone.utc)
        not_before = datetime.strptime(
            cert['notBefore'], '%b %d %H:%M:%S %Y %Z'
        ).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        result["days_left"] = (not_after - now).days
        result["expire_date"] = not_after
        result["not_before"] = not_before

        issuer = dict(x[0] for x in cert.get('issuer', ()))
        result["issuer"] = (issuer.get('organizationName')
                            or issuer.get('commonName') or "—")
    except ssl.SSLCertVerificationError as e:
        msg = getattr(e, 'verify_message', None) or str(e)
        result["error"] = f"Ошибка верификации: {msg}"
    except socket.timeout:
        result["error"] = "Таймаут соединения"
    except socket.gaierror:
        result["error"] = "Домен не найден (DNS)"
    except ConnectionRefusedError:
        result["error"] = "Соединение отклонено"
    except OSError as e:
        result["error"] = f"Сетевая ошибка: {e}"
    except Exception as e:  # noqa: BLE001 — на всякий случай ловим всё
        result["error"] = f"Ошибка: {e}"

    result["category"] = _categorize(result["days_left"], result["error"])
    return result


def format_result_line(res):
    """Одна строка отчёта по домену."""
    icon = CATEGORY_ICON.get(res["category"], "❓")
    host = res["host"] + (f":{res['port']}" if res["port"] != 443 else "")
    if res["error"]:
        return f"{icon} {host}\n     └ {res['error']}"
    d = res["days_left"]
    exp = res["expire_date"].strftime('%Y-%m-%d')
    if d < 0:
        return f"{icon} {host}\n     └ ИСТЁК {_plural_days(d)} назад ({exp})"
    return (f"{icon} {host}\n"
            f"     └ осталось {d} {_plural_days(d)} (до {exp}, {res['issuer']})")


def run_all_checks():
    """Параллельно проверяет все домены из базы. Возвращает отсортированный список результатов."""
    db = load_db()
    domains = db.get("domains", [])
    results = []
    if not domains:
        return results
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(domains))) as ex:
        futures = {
            ex.submit(check_domain_raw, d["name"], d.get("port", 443)): d
            for d in domains
        }
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:  # noqa: BLE001
                d = futures[fut]
                logger.exception("Сбой проверки %s: %s", d.get("name"), e)
    # сначала самые проблемные, затем по имени
    results.sort(key=lambda r: (-CATEGORY_SEVERITY.get(r["category"], 0), r["host"]))
    return results


def update_statuses(results):
    """Сохраняет последние статусы проверки в базу."""
    with _DB_LOCK:
        db = load_db()
        by_key = {(r["host"], r["port"]): r for r in results}
        now_iso = datetime.now(timezone.utc).isoformat(timespec='seconds')
        for d in db["domains"]:
            r = by_key.get((d["name"], d.get("port", 443)))
            if r:
                d["last_status"] = r["category"]
                d["last_checked"] = now_iso
                d["days_left"] = r["days_left"]
        save_db(db)

# ============================================================
#  ОПОВЕЩЕНИЯ И ФОНОВЫЙ МОНИТОРИНГ
# ============================================================
def broadcast(text):
    """Отправляет сообщение администратору и всем операторам."""
    recipients = {str(ADMIN_ID)}
    recipients.update(load_db().get("operators", []))
    for rid in recipients:
        try:
            bot.send_message(int(rid), text)
        except Exception as e:  # noqa: BLE001
            logger.warning("Не удалось отправить оповещение %s: %s", rid, e)


def monitor_loop():
    """Фоновый цикл: периодическая проверка + оповещения при ухудшении/восстановлении."""
    global LAST_CHECK
    time.sleep(20)  # даём боту стартовать
    logger.info("Фоновый мониторинг запущен (интервал %d ч).", CHECK_INTERVAL_HOURS)
    while True:
        try:
            db = load_db()
            old = {
                (d["name"], d.get("port", 443)): d.get("last_status")
                for d in db.get("domains", [])
            }
            results = run_all_checks()
            update_statuses(results)
            LAST_CHECK = datetime.now(timezone.utc)

            alerts, recoveries = [], []
            for r in results:
                prev = old.get((r["host"], r["port"]))
                cat = r["category"]
                if cat != "ok":
                    # алерт при первой проверке проблемы или при ухудшении
                    if (prev is None or prev == "ok"
                            or CATEGORY_SEVERITY.get(cat, 0) > CATEGORY_SEVERITY.get(prev, 0)):
                        alerts.append(r)
                else:
                    if prev not in (None, "ok"):
                        recoveries.append(r)

            if alerts:
                broadcast("🚨 ВНИМАНИЕ! Проблемы с сертификатами:\n\n"
                          + "\n".join(format_result_line(r) for r in alerts))
            if recoveries:
                broadcast("✅ Восстановлено:\n\n"
                          + "\n".join(format_result_line(r) for r in recoveries))

            logger.info("Авто-проверка завершена: %d доменов, %d алертов.",
                        len(results), len(alerts))
        except Exception as e:  # noqa: BLE001
            logger.exception("Ошибка в цикле мониторинга: %s", e)

        time.sleep(max(1, CHECK_INTERVAL_HOURS) * 3600)

# ============================================================
#  ХЕНДЛЕРЫ TELEGRAM
# ============================================================
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    chat_id = message.chat.id
    if not has_access(chat_id):
        bot.send_message(chat_id, f"⛔️ Доступ запрещён.\nВаш ID: `{chat_id}`",
                         parse_mode="Markdown")
        logger.info("Отказано в доступе для %s", chat_id)
        return
    role = "Администратор" if is_admin(chat_id) else "Оператор"
    text = (f"Привет! Я бот для мониторинга SSL-сертификатов.\n"
            f"Ваша роль: *{role}*\n\nВыберите действие в меню:")
    bot.send_message(chat_id, text, reply_markup=get_menu(chat_id), parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text in ('🔍 Проверить всё', '/check'))
def check_all(message):
    if not has_access(message.chat.id):
        return
    if not load_db().get("domains"):
        bot.reply_to(message, "Список доменов пуст.")
        return
    bot.send_message(message.chat.id, "⏳ Запускаю проверку...")
    results = run_all_checks()
    update_statuses(results)
    response = "📄 Результаты проверки:\n\n" + "\n".join(
        format_result_line(r) for r in results
    )
    bot.send_message(message.chat.id, response)


@bot.message_handler(func=lambda m: m.text in ('🔎 Проверить один', '/check_one'))
def check_one_start(message):
    if not has_access(message.chat.id):
        return
    msg = bot.send_message(message.chat.id,
                           "Введите домен для разовой проверки "
                           "(например: google.com или example.com:8443):",
                           reply_markup=get_cancel_markup())
    bot.register_next_step_handler(msg, process_check_one)


def process_check_one(message):
    if _is_cancel(message):
        bot.send_message(message.chat.id, "Отмена.", reply_markup=get_menu(message.chat.id))
        return
    host, port = normalize_domain(message.text)
    if not host:
        bot.send_message(message.chat.id, "Некорректный домен. Отмена.",
                         reply_markup=get_menu(message.chat.id))
        return
    bot.send_message(message.chat.id, f"⏳ Проверяю {host}...")
    res = check_domain_raw(host, port)
    bot.send_message(message.chat.id, format_result_line(res),
                     reply_markup=get_menu(message.chat.id))


@bot.message_handler(func=lambda m: m.text in ('📋 Список доменов', '/list'))
def list_domains(message):
    if not has_access(message.chat.id):
        return
    domains = load_db().get("domains", [])
    if not domains:
        bot.reply_to(message, "Список доменов пуст.")
        return
    lines = ["📋 Мониторинг доменов:\n"]
    for idx, d in enumerate(domains, 1):
        icon = CATEGORY_ICON.get(d.get("last_status"), "▫️")
        host = d["name"] + (f":{d['port']}" if d.get("port", 443) != 443 else "")
        left = d.get("days_left")
        tail = f" — {left} {_plural_days(left)}" if isinstance(left, int) else ""
        lines.append(f"{idx}. {icon} {host}{tail}")
    bot.send_message(message.chat.id, "\n".join(lines))


@bot.message_handler(func=lambda m: m.text in ('📊 Статус', '/status'))
def show_status(message):
    if not has_access(message.chat.id):
        return
    db = load_db()
    last = (LAST_CHECK.strftime('%Y-%m-%d %H:%M UTC')
            if LAST_CHECK else "ещё не выполнялась")
    text = (
        "📊 Конфигурация мониторинга:\n\n"
        f"• Интервал авто-проверки: каждые {CHECK_INTERVAL_HOURS} ч\n"
        f"• Порог «предупреждение»: ≤ {WARNING_DAYS} дн.\n"
        f"• Порог «критично»: ≤ {CRITICAL_DAYS} дн.\n"
        f"• Доменов в мониторинге: {len(db.get('domains', []))}\n"
        f"• Операторов: {len(db.get('operators', []))}\n"
        f"• Последняя авто-проверка: {last}"
    )
    bot.send_message(message.chat.id, text)

# --- БЛОК АДМИНА: УПРАВЛЕНИЕ ДОМЕНАМИ ---

@bot.message_handler(func=lambda m: m.text == '➕ Добавить домен')
def add_domain_start(message):
    if not is_admin(message.chat.id):
        return
    msg = bot.send_message(message.chat.id,
                           "Напишите домен (например: google.com или example.com:8443):",
                           reply_markup=get_cancel_markup())
    bot.register_next_step_handler(msg, process_add_domain)


def process_add_domain(message):
    if _is_cancel(message):
        bot.send_message(message.chat.id, "Отмена.", reply_markup=get_menu(message.chat.id))
        return
    host, port = normalize_domain(message.text)
    if not host:
        bot.send_message(message.chat.id, "Некорректный домен. Отмена.",
                         reply_markup=get_menu(message.chat.id))
        return
    db = load_db()
    if any(d["name"] == host and d.get("port", 443) == port for d in db["domains"]):
        bot.send_message(message.chat.id, f"Домен {host} уже есть в базе.",
                         reply_markup=get_menu(message.chat.id))
        return
    db["domains"].append(_make_domain_entry(host, port))
    save_db(db)
    label = host + (f":{port}" if port != 443 else "")
    bot.send_message(message.chat.id, f"✅ Домен {label} добавлен!",
                     reply_markup=get_menu(message.chat.id))
    logger.info("Админ %s добавил домен %s:%s", message.chat.id, host, port)


@bot.message_handler(func=lambda m: m.text == '❌ Удалить домен')
def del_domain_start(message):
    if not is_admin(message.chat.id):
        return
    domains = load_db().get("domains", [])
    if not domains:
        bot.send_message(message.chat.id, "База доменов пуста, удалять нечего.")
        return
    text = "Выберите номер домена для удаления:\n\n"
    for i, d in enumerate(domains, 1):
        host = d["name"] + (f":{d['port']}" if d.get("port", 443) != 443 else "")
        text += f"{i}. {host}\n"
    msg = bot.send_message(message.chat.id, text, reply_markup=get_cancel_markup())
    bot.register_next_step_handler(msg, process_del_domain)


def process_del_domain(message):
    if _is_cancel(message):
        bot.send_message(message.chat.id, "Отмена.", reply_markup=get_menu(message.chat.id))
        return
    if not (message.text and message.text.isdigit()):
        bot.send_message(message.chat.id, "Нужно было ввести номер. Отмена.",
                         reply_markup=get_menu(message.chat.id))
        return
    idx = int(message.text) - 1
    db = load_db()
    domains = db.get("domains", [])
    if 0 <= idx < len(domains):
        removed = domains.pop(idx)
        save_db(db)
        bot.send_message(message.chat.id, f"🗑 Домен {removed['name']} удалён.",
                         reply_markup=get_menu(message.chat.id))
        logger.info("Админ %s удалил домен %s", message.chat.id, removed['name'])
    else:
        bot.send_message(message.chat.id, "Нет домена с таким номером. Отмена.",
                         reply_markup=get_menu(message.chat.id))

# --- БЛОК АДМИНА: УПРАВЛЕНИЕ ОПЕРАТОРАМИ ---

@bot.message_handler(func=lambda m: m.text == '➕ Добавить оператора')
def add_op_start(message):
    if not is_admin(message.chat.id):
        return
    db = load_db()
    ops = ', '.join(db['operators']) if db['operators'] else 'нет'
    text = (f"Текущие операторы: {ops}\n\n"
            "Чтобы добавить оператора:\n"
            "1. Нажмите *скрепку 📎* → *Контакт 👤* и отправьте человека из контактов, или\n"
            "2. Просто введите его Telegram ID цифрами.")
    msg = bot.send_message(message.chat.id, text,
                           reply_markup=get_cancel_markup(), parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_add_op)


def process_add_op(message):
    if _is_cancel(message):
        bot.send_message(message.chat.id, "Отмена.", reply_markup=get_menu(message.chat.id))
        return

    op_id = None
    if message.content_type == 'contact':
        if message.contact.user_id:
            op_id = str(message.contact.user_id)
        else:
            bot.send_message(message.chat.id,
                             "У этого контакта скрыт Telegram ID настройками приватности "
                             "(или его нет в Telegram). Введите ID вручную.",
                             reply_markup=get_menu(message.chat.id))
            return
    elif message.content_type == 'text' and message.text and message.text.isdigit():
        op_id = message.text.strip()
    else:
        bot.send_message(message.chat.id, "Ожидался ID (цифры) или контакт. Отмена.",
                         reply_markup=get_menu(message.chat.id))
        return

    if op_id == str(ADMIN_ID):
        bot.send_message(message.chat.id, "Это ID администратора — он и так имеет полный доступ.",
                         reply_markup=get_menu(message.chat.id))
        return

    db = load_db()
    if op_id in db["operators"]:
        bot.send_message(message.chat.id, f"Пользователь {op_id} уже оператор.",
                         reply_markup=get_menu(message.chat.id))
    else:
        db["operators"].append(op_id)
        save_db(db)
        bot.send_message(message.chat.id, f"✅ Оператор {op_id} добавлен!",
                         reply_markup=get_menu(message.chat.id))
        logger.info("Админ %s добавил оператора %s", message.chat.id, op_id)


@bot.message_handler(func=lambda m: m.text == '❌ Удалить оператора')
def del_op_start(message):
    if not is_admin(message.chat.id):
        return
    ops = load_db().get("operators", [])
    if not ops:
        bot.send_message(message.chat.id, "Список операторов пуст.")
        return
    text = "Выберите номер оператора для удаления:\n\n"
    for i, op in enumerate(ops, 1):
        text += f"{i}. {op}\n"
    msg = bot.send_message(message.chat.id, text, reply_markup=get_cancel_markup())
    bot.register_next_step_handler(msg, process_del_op)


def process_del_op(message):
    if _is_cancel(message):
        bot.send_message(message.chat.id, "Отмена.", reply_markup=get_menu(message.chat.id))
        return
    if not (message.text and message.text.isdigit()):
        bot.send_message(message.chat.id, "Нужно было ввести номер. Отмена.",
                         reply_markup=get_menu(message.chat.id))
        return
    idx = int(message.text) - 1
    db = load_db()
    ops = db.get("operators", [])
    if 0 <= idx < len(ops):
        removed = ops.pop(idx)
        save_db(db)
        bot.send_message(message.chat.id, f"🗑 Оператор {removed} удалён.",
                         reply_markup=get_menu(message.chat.id))
        logger.info("Админ %s удалил оператора %s", message.chat.id, removed)
    else:
        bot.send_message(message.chat.id, "Нет оператора с таким номером. Отмена.",
                         reply_markup=get_menu(message.chat.id))


@bot.message_handler(func=lambda m: True, content_types=['text'])
def fallback(message):
    """Любой нераспознанный текст у пользователя с доступом — показываем меню."""
    if has_access(message.chat.id):
        bot.send_message(message.chat.id, "Не понял команду. Выберите действие в меню:",
                         reply_markup=get_menu(message.chat.id))

# ============================================================
#  ЗАПУСК
# ============================================================
if __name__ == '__main__':
    if not os.path.exists(DB_FILE):
        save_db({"operators": [], "domains": []})

    threading.Thread(target=monitor_loop, daemon=True, name="ssl-monitor").start()

    logger.info("Бот запущен. Нажми Ctrl+C для остановки.")
    try:
        bot.infinity_polling(skip_pending=True, timeout=30)
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем.")
