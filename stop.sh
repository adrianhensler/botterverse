#!/bin/bash
# Stop Botterverse

echo "🛑 Stopping Botterverse..."

# Check if container exists
if docker ps -a --format '{{.Names}}' | grep -q '^botterverse$'; then
    echo "Found Botterverse container, stopping..."
    docker-compose down
    echo "✅ Botterverse stopped"
else
    echo "ℹ️  Botterverse container not found (already stopped)"
fi

echo ""
echo "To remove data as well, run: docker-compose down -v"
echo "To start again, run: ./start.sh"
