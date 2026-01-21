#!/bin/bash
# Botterverse startup script

set -e

echo "🤖 Starting Botterverse..."
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed"
    echo "Install Docker from: https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if docker-compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Error: Docker Compose is not installed"
    echo "Install Docker Compose from: https://docs.docker.com/compose/install/"
    exit 1
fi

# Check if .env exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found"
    echo "Copy .env.example to .env and add your OpenRouter API key"
    exit 1
fi

# Check if OPENROUTER_API_KEY is set
if ! grep -q "OPENROUTER_API_KEY=" .env; then
    echo "⚠️  Warning: OPENROUTER_API_KEY not found in .env"
    echo "The bot will use local stub generation (no real AI)"
fi

# Create data directory if it doesn't exist
mkdir -p data

# Stop and remove any existing botterverse containers (prevents docker-compose errors)
if docker ps -aq --filter "name=botterverse" | grep -q .; then
    echo "ℹ️  Found existing Botterverse container(s), removing..."
    docker rm -f $(docker ps -aq --filter "name=botterverse") 2>/dev/null || true
    echo "✅ Old containers removed"
    echo ""
fi

# Clean up docker-compose state
docker-compose down 2>/dev/null || true

# Check for port conflicts
PORT=$(grep BOTTERVERSE_PORT .env | cut -d'=' -f2 | tr -d '"' | tr -d ' ')
PORT=${PORT:-8000}

if netstat -tuln 2>/dev/null | grep -q ":${PORT} " || ss -tuln 2>/dev/null | grep -q ":${PORT} "; then
    echo "⚠️  Warning: Port ${PORT} is already in use!"
    echo "Change BOTTERVERSE_PORT in .env to use a different port"
    echo "Example: BOTTERVERSE_PORT=8002"
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "✅ Prerequisites checked"
echo ""
echo "Building and starting Botterverse..."
echo ""

# Build and start
docker-compose up --build -d

echo ""
echo "✅ Botterverse is starting!"
echo ""

# Get server IP addresses
SERVER_IP=$(hostname -I | awk '{print $1}')
PUBLIC_IP=$(curl -s -4 ifconfig.me 2>/dev/null || echo "N/A")

echo "🌐 Access URLs:"
echo "  Local:  http://localhost:${PORT}"
if [ "$SERVER_IP" != "" ]; then
    echo "  LAN:    http://${SERVER_IP}:${PORT}"
fi
if [ "$PUBLIC_IP" != "N/A" ]; then
    echo "  Public: http://${PUBLIC_IP}:${PORT} (if firewall allows)"
fi
echo ""
echo "📊 View logs: docker-compose logs -f"
echo "📖 API docs: http://localhost:${PORT}/docs"
echo "🛑 Stop: docker-compose down"
echo ""
echo "Waiting for service to be ready..."
sleep 5

# Check if service is up
if curl -s http://localhost:${PORT}/authors > /dev/null 2>&1; then
    echo ""
    echo "✅ Botterverse is running and accessible!"
    echo ""
    echo "Try these commands (locally):"
    echo "  curl http://localhost:${PORT}/authors"
    echo "  curl -X POST http://localhost:${PORT}/director/tick"
    echo "  curl http://localhost:${PORT}/timeline"
    echo ""
    if [ "$SERVER_IP" != "" ]; then
        echo "From remote machine (if firewall allows):"
        echo "  curl http://${SERVER_IP}:${PORT}/authors"
        echo "  curl http://${SERVER_IP}:${PORT}/docs"
    fi
    echo ""
else
    echo ""
    echo "⚠️  Service may still be starting. Check logs with:"
    echo "  docker-compose logs -f"
    echo ""
fi
