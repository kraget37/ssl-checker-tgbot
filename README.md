## SSL Checker Telegram Bot

A lightweight and efficient Telegram bot designed to monitor SSL/TLS certificates expiration dates and send timely notifications directly to your Telegram channel or chat.

### System Requirements

Before running the installation script, ensure your system meets the following requirements:
* **Environment:** VPS / Dedicated Server
* **OS:** Ubuntu (20.04 LTS or newer recommended)
* **Dependencies:** Docker and Docker Compose installed

---

### Installation via Script

The easiest way to deploy the bot is by using the automated `setup.sh` script. It handles configuration and environment setup in one go.

One-line installation:
```bash
mkdir -p ssl-checker-tgbot && cd ./ssl-checker-tgbot && sudo wget -O setup.sh "https://raw.githubusercontent.com/kraget37/ssl-checker-tgbot/refs/heads/main/setup.sh" && sudo chmod +x setup.sh && ./setup.sh
```
The script will ask you for the necessary configuration variables (your Telegram bot's token and your Telegram ID) and automatically launch the bot in Docker.

---

### File Structure & Description

Here is an overview of the key components created and used in this project:

* **`setup.sh`** — Automated bash script for initial configuration, environment setup, and deployment.
* **`docker-compose.yml`** — The definitive service definitions and runtime specifications for the containerized application.
* **`domains.json`** — The self-contained database of the application. It maintains an inventory of tracked domains, sub-operators, and the cached dynamic statuses from ongoing SSL/TLS sweeps.

Since the application relies strictly on cloud-native environment ingestion, you do not need to rebuild the image or maintain .env files to update your setup.
To rotate tokens, hand over administration rights, or adjust check intervals, simply modify the environment block inside your active docker-compose.yml file:
```bash
environment:
  - BOT_TOKEN=your_new_telegram_bot_token
  - ADMIN_ID=your_new_telegram_admin_id
```
After modifying the variables, apply the changes instantly without downtime by running:
```bash
sudo docker compose up -d
```
⚠️If you don't want to use the installation script, but use docker-compose, after trying to build "sudo docker compose up -d" you will get the error "permission denied", grant rights to the data folder:
```bash
sudo chmod -R 777 data
```
