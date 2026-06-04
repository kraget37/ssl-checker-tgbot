#!/bin/bash

echo "=== Настройка окружения для SSL Checker Bot ==="

# 1. Интерактивный опрос пользователя
read -p "Введите BOT_TOKEN от BotFather: " BOT_TOKEN
read -p "Введите ваш Telegram ADMIN_ID (только цифры): " ADMIN_ID

# 2. Создание файла .env
echo "[1/3] Создаю файл .env..."
cat <<EOF > .env
BOT_TOKEN=$BOT_TOKEN
ADMIN_ID=$ADMIN_ID
EOF

# 3. Создание файла domains.json
echo "[2/3] Создаю файл domains.json..."
cat <<EOF > domains.json
{
    "operators": [],
    "domains": [
        "ya.ru",
        "google.com"
    ]
}
EOF

# 4. Создание файла docker-compose.yml
echo "[3/3] Создаю файл docker-compose.yml..."
cat <<EOF > docker-compose.yml
services:
  ssl-bot:
    image: kraget37/ssl-checker-tgbot
    container_name: ssl-checker-tgbot
    restart: unless-stopped
    volumes:
      - ./.env:/app/.env
      - ./domains.json:/app/domains.json
EOF

echo "=== Подготовка успешно завершена! ==="
echo "Файлы .env, domains.json и docker-compose.yml созданы в текущей директории."
echo "Теперь вы можете запустить бота командой:"
echo "sudo docker compose up -d"
echo "Изменить токен бота можно изменив файл .env"
echo "Список пользователей-операторов и доменов находится в файле domains.json"
