# Deployment Guide

Deploy Swarm Traders to any VPS/cloud instance with Docker.

---

## Prerequisites

- A Linux server (Ubuntu 22.04+ recommended) with a **static public IP**
- Docker and Docker Compose installed
- Git access to this repo (SSH key or HTTPS token)

---

## 1. Server Setup

### Install Docker

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in for group change to take effect
```

### Clone the Repo

```bash
git clone git@github.com:Sumit-07/swarm-traders.git
cd swarm-traders
```

### Configure Environment

```bash
cp .env.example .env
nano .env
```

Fill in all API keys. **Important:** Leave `REDIS_HOST=localhost` in `.env` — Docker Compose overrides it to `redis` automatically.

| Variable | Notes |
|---|---|
| `OPENAI_API_KEY` | Required for GPT-4o agents |
| `GOOGLE_API_KEY` | Required for Gemini Flash agents |
| `FYERS_CLIENT_ID` | From https://myapi.fyers.in |
| `FYERS_SECRET_KEY` | From Fyers dashboard |
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `TELEGRAM_CHAT_ID` | Your chat ID |
| `TRADING_MODE` | `PAPER` (default) or `LIVE` |

### Whitelist Server IP in Fyers

```bash
# Get your server's public IP
curl ifconfig.me
```

1. Go to https://myapi.fyers.in
2. Select your app → Edit
3. Add the server IP to the whitelist
4. Save

---

## 2. First Deploy

```bash
# Build and start all services (Redis, trading app, dashboard)
docker compose up -d --build

# Watch the app startup logs
docker compose logs -f app

# Verify all 3 services are running
docker compose ps
```

Expected output from `docker compose ps`:
```
NAME                    STATUS
swarm-traders-redis-1      Up (healthy)
swarm-traders-app-1        Up
swarm-traders-dashboard-1  Up
```

The dashboard is accessible at `http://<server-ip>:8501`.

---

## 3. Updating Configuration

### Method A: Edit on Server (no rebuild)

For **environment variable** changes (API keys, trading mode, log level):

```bash
nano .env
docker compose restart app
```

For **config.py** changes (risk limits, capital, strategies):

```bash
nano config.py
docker compose restart app
```

For **strategy parameter** changes (backtesting/runner.py):

```bash
nano backtesting/runner.py
docker compose restart app
```

No rebuild needed — the container restarts with the updated files.

### Method B: Push Code Changes (rebuild)

When you've made changes locally and want to deploy:

**Option 1 — Git pull on server:**

```bash
# On your local machine
git add -A && git commit -m "update" && git push

# On the server
cd swarm-traders
git pull
docker compose up -d --build
```

**Option 2 — Docker Hub (for registry-based deploys):**

```bash
# On your local machine
docker build -t yourusername/swarm-traders:latest .
docker push yourusername/swarm-traders:latest

# On the server
docker compose pull
docker compose up -d
```

For Option 2, update `docker-compose.yml` to use `image: yourusername/swarm-traders:latest` instead of `build: .` for the `app` and `dashboard` services.

---

## 4. Common Operations

### Logs

```bash
# Trading system logs (live tail)
docker compose logs -f app

# Dashboard logs
docker compose logs -f dashboard

# Last 100 lines
docker compose logs --tail=100 app

# All services
docker compose logs -f
```

### Run Backtest

```bash
docker compose exec app python -m backtesting.runner \
    --strategy all --start 2026-03-05 --end 2026-04-04 --report
```

Reports are saved to `backtesting/reports/` (mounted volume, accessible from server).

### Run Tests

```bash
docker compose exec app pytest tests/ -v
```

### Stop / Start

```bash
# Stop all services (data preserved)
docker compose down

# Start again
docker compose up -d

# Restart just the trading app
docker compose restart app

# Full rebuild (after code changes)
docker compose up -d --build
```

### Shell Access

```bash
# Open a shell inside the app container
docker compose exec app bash

# Check Redis directly
docker compose exec redis redis-cli
docker compose exec redis redis-cli GET state:system_mode
```

---

## 5. Fyers Authentication

Fyers uses OAuth2 which normally requires a browser redirect. On a headless server:

1. Set `FYERS_REDIRECT_URI` in `.env` to `http://<server-ip>:8080` (or any accessible URL)
2. On first run, the app logs the Fyers auth URL — copy it and open in your local browser
3. After authorizing, copy the auth code from the redirect URL
4. The access token is saved and refreshed automatically

**Note:** Fyers access tokens expire daily. You may need to re-authenticate periodically. Check `logs/error_logs/` if you see auth failures.

---

## 6. Data Persistence

All persistent data is stored in mounted volumes:

| Host Path | Container Path | Contents |
|---|---|---|
| `./data/` | `/app/data/` | SQLite DB (`trading_swarm.db`), backtest cache |
| `./logs/` | `/app/logs/` | Agent logs, trade logs, error logs |
| `./backtesting/reports/` | `/app/backtesting/reports/` | HTML backtest reports |
| Docker volume `redis_data` | `/data` | Redis persistence (RDB snapshots) |

To back up:
```bash
# SQLite database
cp data/trading_swarm.db data/trading_swarm.db.backup

# Full data backup
tar czf swarm-backup-$(date +%Y%m%d).tar.gz data/ logs/
```

---

## 7. Security Notes

- **Never commit `.env`** — it's in `.gitignore` and `.dockerignore`
- The dashboard (port 8501) has no authentication — restrict access via firewall rules or a reverse proxy
- Redis is internal-only (no exposed port) — only accessible between containers
- Consider using `ufw` to restrict access:
  ```bash
  sudo ufw allow 22        # SSH
  sudo ufw allow 8501      # Dashboard (restrict to your IP if possible)
  sudo ufw enable
  ```

---

## 8. Troubleshooting

**Container won't start:**
```bash
docker compose logs app    # check for errors
docker compose ps          # check status
```

**Redis connection refused:**
```bash
docker compose ps          # is redis healthy?
docker compose restart redis
```

**Out of disk space:**
```bash
docker system prune -f     # clean unused images/containers
```

**Fyers auth failed:**
- Access tokens expire daily
- Check `FYERS_CLIENT_ID` and `FYERS_SECRET_KEY` in `.env`
- Re-authenticate via the auth URL in logs
