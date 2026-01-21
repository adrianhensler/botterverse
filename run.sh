#!/bin/bash
# Simple docker run (bypasses docker-compose issues)

set -e

echo "🤖 Starting Botterverse (simple mode)..."
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found"
    exit 1
fi

# Load port from .env
PORT=$(grep BOTTERVERSE_PORT .env 2>/dev/null | cut -d'=' -f2 | tr -d '"' | tr -d ' ')
PORT=${PORT:-8000}

# Create data directory
mkdir -p data

# Remove old container if exists
if docker ps -aq --filter "name=botterverse" | grep -q .; then
    echo "Removing old container..."
    docker rm -f $(docker ps -aq --filter "name=botterverse") 2>/dev/null || true
fi

# Build image
echo "Building image..."
docker build -t botterverse:latest .

echo ""
echo "Starting container..."

# Run container
docker run -d \
  --name botterverse \
  --restart unless-stopped \
  -p ${PORT}:8000 \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/app:/app/app" \
  --env-file .env \
  -e BOTTERVERSE_STORE=sqlite \
  -e BOTTERVERSE_SQLITE_PATH=data/botterverse.db \
  --memory=512m \
  --cpus=1.0 \
  botterverse:latest \
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

echo ""
echo "✅ Botterverse started!"
echo ""

# Get server IPs
SERVER_IP=$(hostname -I | awk '{print $1}')
PUBLIC_IP=$(curl -s -4 ifconfig.me 2>/dev/null || echo "N/A")

echo "🌐 Access URLs:"
echo "  Local:  http://localhost:${PORT}"
[ -n "$SERVER_IP" ] && echo "  LAN:    http://${SERVER_IP}:${PORT}"
[ "$PUBLIC_IP" != "N/A" ] && echo "  Public: http://${PUBLIC_IP}:${PORT} (if firewall allows)"
echo ""
echo "📊 View logs: docker logs -f botterverse"
echo "🛑 Stop: docker stop botterverse"
echo ""

# Wait and test
echo "Waiting for service to start..."
sleep 5

if curl -s http://localhost:${PORT}/authors > /dev/null 2>&1; then
    echo "✅ Service is running!"
    BOT_COUNT=$(curl -s http://localhost:${PORT}/authors | jq '[.[] | select(.type=="bot")] | length' 2>/dev/null || echo "?")
    echo "🤖 Loaded $BOT_COUNT bots"
else
    echo "⚠️  Service may still be starting. Check logs:"
    echo "  docker logs botterverse"
fi
