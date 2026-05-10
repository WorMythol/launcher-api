#!/usr/bin/env bash
# deploy.sh — одна команда для установки и обновления Launcher API
# Использование:
#   curl -fsSL https://raw.githubusercontent.com/WorMythol/launcher-api/master/deploy.sh | bash
#   или: bash deploy.sh
set -euo pipefail

REPO="https://github.com/WorMythol/launcher-api.git"
INSTALL_DIR="/opt/launcher-api"
SERVICE="launcher-api"
VENV="$INSTALL_DIR/venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

GREEN="\033[0;32m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; exit 1; }

# ── Права ────────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    warn "Запускаю через sudo..."
    exec sudo bash "$0" "$@"
fi

# ── Зависимости ОС ───────────────────────────────────────────────────────────
info "Проверяю системные зависимости..."
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip curl 2>/dev/null || true

# ── Клонирование / обновление ────────────────────────────────────────────────
if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Обновляю репозиторий..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    info "Клонирую репозиторий в $INSTALL_DIR..."
    git clone "$REPO" "$INSTALL_DIR"
fi

# ── brand.json ───────────────────────────────────────────────────────────────
BRAND="$INSTALL_DIR/brand.json"
if [[ ! -f "$BRAND" ]]; then
    warn "Файл brand.json не найден — нужно настроить."
    echo ""

    read -rp "  DB URL (postgresql://user:pass@host:port/db): " DB_URL
    read -rp "  IP сервера (например 91.144.171.180):          " SERVER_IP
    read -rp "  Название сервера [My Server]:                  " SERVER_NAME
    SERVER_NAME="${SERVER_NAME:-My Server}"

    cat > "$BRAND" <<EOF
{
  "server_name":         "$SERVER_NAME",
  "launcher_title":      "$SERVER_NAME Launcher",
  "launcher_version":    "1.0.1",
  "api_url":             "http://$SERVER_IP:8000",
  "db_url":              "$DB_URL",
  "fixed_version":       "1.20.1-wm2",
  "client_download_url": "http://$SERVER_IP:8000/api/downloads/client/latest",
  "color_dark":          "#1a1a2e",
  "color_panel":         "#16213e",
  "color_accent":        "#00d4aa",
  "color_red":           "#e94560",
  "color_input":         "#0f3460",
  "color_text":          "#e0e0e0",
  "color_muted":         "#888888",
  "default_memory":      "2048",
  "authlib_jar":         "",
  "authlib_server_url":  ""
}
EOF
    info "brand.json создан."
else
    info "brand.json уже существует — пропускаю."
fi

# ── Виртуальное окружение ────────────────────────────────────────────────────
if [[ ! -d "$VENV" ]]; then
    info "Создаю виртуальное окружение..."
    python3 -m venv "$VENV"
fi

info "Устанавливаю/обновляю Python-зависимости..."
"$PIP" install --quiet --upgrade pip
"$PIP" install --quiet -r "$INSTALL_DIR/requirements_server.txt"

# ── systemd-сервис ────────────────────────────────────────────────────────────
SERVICE_FILE="/etc/systemd/system/$SERVICE.service"
info "Настраиваю systemd-сервис..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Minecraft Launcher API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$PYTHON server.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE"

# ── Запуск / перезапуск ───────────────────────────────────────────────────────
if systemctl is-active --quiet "$SERVICE"; then
    info "Перезапускаю сервис..."
    systemctl restart "$SERVICE"
else
    info "Запускаю сервис..."
    systemctl start "$SERVICE"
fi

# ── Проверка ──────────────────────────────────────────────────────────────────
sleep 2
if systemctl is-active --quiet "$SERVICE"; then
    info "Сервис запущен успешно!"
    echo ""
    echo -e "  Статус:  ${GREEN}$(systemctl is-active $SERVICE)${NC}"
    echo -e "  Логи:    journalctl -u $SERVICE -f"
    # Быстрая проверка API
    if curl -sf http://localhost:8000/api/news > /dev/null 2>&1; then
        echo -e "  API:     ${GREEN}http://localhost:8000 — отвечает ✓${NC}"
    else
        warn "API пока не отвечает — подождите несколько секунд или проверьте логи."
    fi
else
    error "Сервис не запустился! Смотрите логи: journalctl -u $SERVICE -n 50"
fi
