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
1. Create a directory and go into it:
```bash
mkdir ssl-checker-tgbot
```
```bash
cd ./ssl-checker-tgbot
```
2. Download 'setup.sh':
```bash
sudo wget -O setup.sh "https://raw.githubusercontent.com/kraget37/ssl-checker-tgbot/refs/heads/main/setup.sh"
```   
3. Make the script executable and run it:
```bash
sudo chmod +x setup.sh && ./setup.sh
```

The script will prompt you for the necessary configuration variables (for example, your Telegram bot's token) and offer a command to launch the Docker container.
```bash
sudo docker compose up -d
```
---

### File Structure & Description

Here is an overview of the key components created and used in this project:

* **`setup.sh`** — Automated bash script for initial configuration, environment setup, and deployment.
* **`docker-compose.yml`** — Docker Compose configuration file defining the bot services and container layout.
* **`.env`** — Configuration file storing secret environment variables (e.g., API keys, tokens).
