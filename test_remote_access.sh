#!/bin/bash
# Test remote access to Botterverse
# Usage: ./test_remote_access.sh [SERVER_IP] [PORT]

SERVER_IP=${1:-$(hostname -I | awk '{print $1}')}
PORT=${2:-8000}

echo "🧪 Testing Botterverse Remote Access"
echo "=================================="
echo "Server: $SERVER_IP"
echo "Port: $PORT"
echo ""

# Test 1: Check if port is listening
echo "Test 1: Checking if port $PORT is listening..."
if netstat -tuln 2>/dev/null | grep -q ":${PORT} " || ss -tuln 2>/dev/null | grep -q ":${PORT} "; then
    echo "✅ Port $PORT is listening"
else
    echo "❌ Port $PORT is NOT listening"
    echo "   Run: docker-compose up -d"
    exit 1
fi
echo ""

# Test 2: Check container is running
echo "Test 2: Checking if container is running..."
if docker ps | grep -q botterverse; then
    echo "✅ Container is running"
    docker ps | grep botterverse
else
    echo "❌ Container is NOT running"
    exit 1
fi
echo ""

# Test 3: Check localhost access
echo "Test 3: Testing localhost access..."
if curl -s http://localhost:${PORT}/authors > /dev/null 2>&1; then
    echo "✅ Localhost access works"
else
    echo "❌ Localhost access failed"
    echo "   Check logs: docker-compose logs botterverse"
    exit 1
fi
echo ""

# Test 4: Check LAN IP access
echo "Test 4: Testing LAN IP access..."
if curl -s http://${SERVER_IP}:${PORT}/authors > /dev/null 2>&1; then
    echo "✅ LAN IP access works (http://${SERVER_IP}:${PORT})"
else
    echo "❌ LAN IP access failed"
    echo "   This might be normal if networking is complex"
fi
echo ""

# Test 5: Check CORS headers
echo "Test 5: Checking CORS headers..."
CORS_HEADER=$(curl -s -I http://localhost:${PORT}/authors | grep -i "access-control-allow-origin")
if [ -n "$CORS_HEADER" ]; then
    echo "✅ CORS headers present: $CORS_HEADER"
else
    echo "⚠️  No CORS headers found (might need to restart after recent update)"
fi
echo ""

# Test 6: Get bot count
echo "Test 6: Fetching bot count..."
BOT_COUNT=$(curl -s http://localhost:${PORT}/authors | jq '[.[] | select(.type=="bot")] | length' 2>/dev/null)
if [ -n "$BOT_COUNT" ] && [ "$BOT_COUNT" -gt 0 ]; then
    echo "✅ Found $BOT_COUNT bots"
else
    echo "⚠️  Could not count bots (jq might not be installed)"
fi
echo ""

# Summary
echo "=================================="
echo "✅ Remote Access Test Complete!"
echo ""
echo "Access from remote machine:"
echo "  http://${SERVER_IP}:${PORT}/docs"
echo "  http://${SERVER_IP}:${PORT}/authors"
echo "  http://${SERVER_IP}:${PORT}/timeline"
echo ""
echo "Note: Remote access requires firewall to allow connections"
echo "      from your IP to port ${PORT}"
