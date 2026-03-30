#!/bin/bash
# ============================================================
# Swing Trading Bot - VPS Deployment Script (Ubuntu)
# ============================================================
# Usage: bash install.sh
# Prerequisites: Ubuntu 20.04+, Python 3.10+
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_DIR/.venv"
SERVICE_NAME="swing-trading-bot"

echo "============================================"
echo "  Swing Trading Bot - VPS Installer"
echo "============================================"

# 1. System dependencies
echo "[1/6] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv

# 2. Python virtual environment
echo "[2/6] Setting up Python virtual environment..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r "$PROJECT_DIR/requirements.txt" -q
pip install python-dotenv -q

# 3. Create .env from template if not exists
echo "[3/6] Setting up environment..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "  !! IMPORTANT: Edit $PROJECT_DIR/.env with your Capital.com credentials"
fi

# 4. Create log and data directories
echo "[4/6] Creating directories..."
mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data"

# 5. Create systemd service
echo "[5/6] Installing systemd service..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Swing Trading Bot - Capital.com
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$VENV_DIR/bin/python main.py
Restart=on-failure
RestartSec=30
StartLimitBurst=5
StartLimitIntervalSec=300

# Logging
StandardOutput=append:$PROJECT_DIR/logs/service.log
StandardError=append:$PROJECT_DIR/logs/service_error.log

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$PROJECT_DIR

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}

# 6. Create management script
echo "[6/6] Creating management commands..."
cat > "$PROJECT_DIR/bot" <<'SCRIPT'
#!/bin/bash
# Quick management commands for the swing trading bot
SERVICE="swing-trading-bot"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-help}" in
    start)
        sudo systemctl start $SERVICE
        echo "Bot started. Check logs: journalctl -u $SERVICE -f"
        ;;
    stop)
        sudo systemctl stop $SERVICE
        echo "Bot stopped."
        ;;
    restart)
        sudo systemctl restart $SERVICE
        echo "Bot restarted."
        ;;
    status)
        sudo systemctl status $SERVICE
        ;;
    logs)
        journalctl -u $SERVICE -f --no-pager
        ;;
    scan)
        source "$PROJECT_DIR/.venv/bin/activate"
        cd "$PROJECT_DIR" && python main.py --scan
        ;;
    check)
        source "$PROJECT_DIR/.venv/bin/activate"
        cd "$PROJECT_DIR" && python main.py --status
        ;;
    demo)
        source "$PROJECT_DIR/.venv/bin/activate"
        cd "$PROJECT_DIR" && USE_DEMO=true python main.py --demo --scan
        ;;
    help|*)
        echo "Usage: ./bot {start|stop|restart|status|logs|scan|check|demo}"
        echo ""
        echo "  start   - Start the bot as a background service"
        echo "  stop    - Stop the bot"
        echo "  restart - Restart the bot"
        echo "  status  - Show service status"
        echo "  logs    - Tail live logs"
        echo "  scan    - Run one-time market scan"
        echo "  check   - Show current positions and risk status"
        echo "  demo    - Run a demo scan (no real trades)"
        ;;
esac
SCRIPT
chmod +x "$PROJECT_DIR/bot"

echo ""
echo "============================================"
echo "  Installation complete!"
echo "============================================"
echo ""
echo "  Next steps:"
echo "  1. Edit .env with your Capital.com credentials:"
echo "     nano $PROJECT_DIR/.env"
echo ""
echo "  2. Test with a demo scan first:"
echo "     ./bot demo"
echo ""
echo "  3. Start the bot:"
echo "     ./bot start"
echo ""
echo "  4. View live logs:"
echo "     ./bot logs"
echo ""
echo "  NOTE: Bot starts in DEMO mode by default."
echo "  Set USE_DEMO=false in .env for live trading."
echo "============================================"
