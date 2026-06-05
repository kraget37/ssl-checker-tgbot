#!/bin/bash

echo "=== Настройка окружения для SSL Checker Bot ==="

# 1. Создание структуры данных
echo "[1/3] Проверяю папку data и файл domains.json..."
mkdir -p data
if [ -f "data/domains.json" ]; then
    echo "Файл data/domains.json уже существует, создание пропущено."
else
    cat <<EOF > data/domains.json
{
    "operators": [],
    "domains": [
        "ya.ru",
        "google.com"
    ]
}
EOF
    echo "Базовый файл domains.json успешно создан."
fi

# Настраиваем права доступа на папку и файл
echo "[2/3] Настраиваю права доступа для базы данных..."
chmod 777 data
chmod 666 data/domains.json

# 2. Создание файла docker-compose.yml
echo "[3/3] Проверяю конфигурацию docker-compose..."
if [ -f "docker-compose.yml" ]; then
    echo "Файл docker-compose.yml уже существует."
    echo "Если вы хотели изменить настройки (токен/ID), отредактируйте файл вручную."
else
    echo "Файл docker-compose.yml не найден. Давайте его создадим."
    # Спрашиваем токены ТОЛЬКО если нужно создать новый файл
    read -p "Введите BOT_TOKEN от BotFather: " BOT_TOKEN
    read -p "Введите ваш Telegram ADMIN_ID (только цифры): " ADMIN_ID

    cat <<EOF > docker-compose.yml
services:
  ssl-bot:
    image: kraget37/ssl-checker-tgbot:latest
    container_name: ssl-checker-tgbot
    restart: unless-stopped
    environment:
      # Обязательные параметры
      - BOT_TOKEN=${BOT_TOKEN}
      - ADMIN_ID=${ADMIN_ID}
      # Необязательные параметры
      - CHECK_INTERVAL_HOURS=12
      - WARNING_DAYS=30
      - CRITICAL_DAYS=14
      - MAX_WORKERS=10
    volumes:
      - ./data:/app/data
EOF
    echo "Файл docker-compose.yml успешно создан."
fi

echo "=== Подготовка успешно завершена! ==="

# 3. Запуск контейнера
echo "Запускаю движок Docker Compose..."
# Docker Compose сам проверит, запущен ли контейнер, и обновит его при необходимости
sudo docker compose up -d

echo ""
echo "✅ Бот готов к работе!"
echo "⚙️ Изменить токен бота или ID админа можно в файле docker-compose.yml"
echo "📂 Список операторов и детальная информация о доменах находится в файле data/domains.json"
