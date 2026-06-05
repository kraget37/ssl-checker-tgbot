#!/bin/bash

echo "=== Настройка окружения для SSL Checker Bot ==="

# 1. Интерактивный опрос пользователя
read -p "Введите BOT_TOKEN от BotFather: " BOT_TOKEN
read -p "Введите ваш Telegram ADMIN_ID (только цифры): " ADMIN_ID

# 2. Создание файла domains.json
echo "[1/2] Создаю файл domains.json..."
cat <<EOF > domains.json
{
    "operators": [],
    "domains": [
        "ya.ru",
        "google.com"
    ]
}
EOF

# 3. Создание файла docker-compose.yml
echo "[2/2] Создаю файл docker-compose.yml..."
cat <<EOF > docker-compose.yml
services:
  ssl-bot:
    image: kraget37/ssl-checker-tgbot:latest
    container_name: ssl-checker-tgbot
    restart: unless-stopped
    environment:
      # Обязательные параметры
      - BOT_TOKEN=твой_токен_от_бота
      - ADMIN_ID=твой_telegram_id
      # Необязательные параметры
      - CHECK_INTERVAL_HOURS=12
      - WARNING_DAYS=30
      - CRITICAL_DAYS=14
      - MAX_WORKERS=10
    volumes:
      - ./domains.json:/app/domains.json
EOF
sudo docker compose up -d
echo "=== Подготовка успешно завершена! ==="
echo "Файлы domains.json и docker-compose.yml созданы в текущей директории."
echo "Бот успешно запущен."
echo ""
echo "⚙️ Изменить токен бота или ID админа можно в файле docker-compose.yml"
echo "📂 Список операторов и детальная информация о доменах находится в файле domains.json"
