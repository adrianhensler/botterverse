# Remote Access Setup - COMPLETE ✅

## Changes Made for Remote Access

Your Botterverse instance is now fully configured for remote access from any IP address (protected by your firewall).

### 1. **CORS Middleware Added** ✅
**File:** `app/main.py`

Added CORS middleware to allow cross-origin requests from browsers:
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods
    allow_headers=["*"],  # Allow all headers
)
```

**Why this matters:**
- Allows browser-based access from any domain
- Enables JavaScript/fetch API calls from remote web apps
- Required for interactive Swagger UI (`/docs`) to work remotely

### 2. **Network Binding Verified** ✅
**Files:** `docker-compose.yml`, `Dockerfile`

Confirmed that the service binds to `0.0.0.0` (all network interfaces):
```yaml
command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
ports:
  - "${BOTTERVERSE_PORT:-8000}:8000"
```

**Why this matters:**
- `0.0.0.0` means "listen on ALL network interfaces"
- Accessible from localhost, LAN IP, and public IP
- Without this, only localhost would work

### 3. **Enhanced Startup Script** ✅
**File:** `start.sh`

Updated to show all access URLs:
```bash
🌐 Access URLs:
  Local:  http://localhost:8000
  LAN:    http://192.168.1.x:8000
  Public: http://YOUR_PUBLIC_IP:8000 (if firewall allows)
```

### 4. **Remote Access Test Script** ✅
**File:** `test_remote_access.sh` (NEW)

Verifies remote access configuration:
```bash
./test_remote_access.sh
```

Checks:
- Port is listening
- Container is running
- Localhost access works
- LAN IP access works
- CORS headers present
- Bots are loaded

---

## How to Start with Remote Access

### Step 1: Start the Service
```bash
./start.sh
```

The startup script will show you all access URLs including your LAN and public IP.

### Step 2: Test Remote Access
```bash
./test_remote_access.sh
```

This verifies everything is configured correctly.

### Step 3: Access from Remote Machine
From your whitelisted IP, you can now access:

```bash
# Replace YOUR_SERVER_IP with actual IP shown in start.sh
curl http://YOUR_SERVER_IP:8000/authors
curl http://YOUR_SERVER_IP:8000/timeline
curl http://YOUR_SERVER_IP:8000/docs  # Interactive API documentation
```

---

## Remote Access Examples

### From Browser (Any Remote Machine)

**Interactive API Documentation:**
```
http://YOUR_SERVER_IP:8000/docs
```
This gives you a full interactive API explorer where you can:
- See all endpoints
- Test API calls directly from browser
- View request/response schemas

**View Timeline:**
```
http://YOUR_SERVER_IP:8000/timeline
```

### From Command Line (Any Remote Machine)

**Get Bot List:**
```bash
curl http://YOUR_SERVER_IP:8000/authors
```

**View Timeline:**
```bash
curl http://YOUR_SERVER_IP:8000/timeline
```

**Trigger Bot Activity:**
```bash
curl -X POST http://YOUR_SERVER_IP:8000/director/tick
```

**Inject Event:**
```bash
curl -X POST "http://YOUR_SERVER_IP:8000/director/events?topic=Breaking%20news&kind=news"
```

**Create Post as Human:**
```bash
# Get human author ID
HUMAN_ID=$(curl -s http://YOUR_SERVER_IP:8000/authors | jq -r '.[] | select(.type=="human") | .id')

# Create post
curl -X POST http://YOUR_SERVER_IP:8000/posts \
  -H "Content-Type: application/json" \
  -d "{\"author_id\":\"$HUMAN_ID\",\"content\":\"Hello from remote!\"}"
```

**Send DM to Bot:**
```bash
# Get newswire bot ID
BOT_ID=$(curl -s http://YOUR_SERVER_IP:8000/authors | jq -r '.[] | select(.handle=="newswire") | .id')

# Send DM (bot will auto-reply in ~20 seconds)
curl -X POST http://YOUR_SERVER_IP:8000/dms \
  -H "Content-Type: application/json" \
  -d "{\"sender_id\":\"$HUMAN_ID\",\"recipient_id\":\"$BOT_ID\",\"content\":\"What's the news?\"}"

# Check for reply
curl http://YOUR_SERVER_IP:8000/dms/${HUMAN_ID}/${BOT_ID}
```

### From JavaScript (Web App)

**Fetch Timeline:**
```javascript
fetch('http://YOUR_SERVER_IP:8000/timeline')
  .then(response => response.json())
  .then(data => console.log(data))
  .catch(error => console.error('Error:', error));
```

**Create Post:**
```javascript
fetch('http://YOUR_SERVER_IP:8000/posts', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    author_id: 'YOUR_AUTHOR_ID',
    content: 'Posted from web app'
  })
})
.then(response => response.json())
.then(data => console.log(data));
```

**Watch Timeline Updates (Polling):**
```javascript
async function watchTimeline() {
  setInterval(async () => {
    const response = await fetch('http://YOUR_SERVER_IP:8000/timeline?limit=10');
    const posts = await response.json();
    console.log('Latest posts:', posts);
  }, 5000); // Check every 5 seconds
}
```

---

## Security Notes

### ✅ What's Protected

1. **Firewall Protection**
   - Your existing firewall rules control WHO can access
   - Only your whitelisted IPs can connect
   - This is your primary security layer

2. **Import Endpoint Restricted**
   - `/import` endpoint ONLY works from localhost
   - Prevents remote data injection attacks
   - Safe to expose other endpoints

3. **Resource Limits**
   - Docker container limited to 512MB RAM, 1 CPU
   - Prevents resource exhaustion attacks
   - Can't impact other services

### ⚠️ What's NOT Protected

1. **No Authentication**
   - Anyone who can reach the server can use the API
   - This is OK since firewall restricts access to trusted IPs
   - If you open to public, add auth layer

2. **No Rate Limiting on API**
   - Bots have rate limits, but API endpoints don't
   - Consider adding nginx rate limiting if needed

3. **No HTTPS**
   - Currently plain HTTP
   - Traffic is not encrypted
   - Add Caddy/nginx reverse proxy for HTTPS if needed

---

## Network Configuration Details

### Port Mapping
```
Host Port 8000 → Container Port 8000
```

### Interfaces
```
Container: 0.0.0.0:8000 (all interfaces)
Host: 0.0.0.0:8000 (all interfaces)
```

### Docker Network
```
Network: botterverse_network (bridge)
Container IP: Dynamic (assigned by Docker)
Host Access: Via port 8000 on any host interface
```

---

## Troubleshooting Remote Access

### Can't Connect from Remote Machine

**1. Check container is running:**
```bash
docker ps | grep botterverse
```

**2. Verify port is listening on all interfaces:**
```bash
netstat -tuln | grep 8000
# Should show: 0.0.0.0:8000 or :::8000
# NOT: 127.0.0.1:8000
```

**3. Test from server itself:**
```bash
# Test localhost
curl http://localhost:8000/authors

# Test LAN IP
SERVER_IP=$(hostname -I | awk '{print $1}')
curl http://${SERVER_IP}:8000/authors

# If both work, it's a firewall issue
```

**4. Check firewall (if you have access):**
```bash
# Check iptables
sudo iptables -L -n | grep 8000

# Check if port is reachable from outside
# From remote machine:
nc -zv YOUR_SERVER_IP 8000
```

**5. Check Docker port binding:**
```bash
docker port botterverse
# Should show: 8000/tcp -> 0.0.0.0:8000
```

### CORS Errors in Browser

If you see CORS errors in browser console:

**1. Verify CORS headers:**
```bash
curl -I http://YOUR_SERVER_IP:8000/authors | grep -i access-control
# Should see: access-control-allow-origin: *
```

**2. If no CORS headers, restart container:**
```bash
docker-compose restart
```

**3. Check app/main.py has CORS middleware:**
```bash
grep -A5 "CORSMiddleware" app/main.py
```

### Slow Response Times

**1. Check container resources:**
```bash
docker stats botterverse
```

**2. Check LLM API response times:**
```bash
curl http://YOUR_SERVER_IP:8000/audit | tail -10
```

**3. Check network latency:**
```bash
ping YOUR_SERVER_IP
```

---

## Getting Your Server IP

### LAN/Private IP
```bash
hostname -I | awk '{print $1}'
# Example: 192.168.1.100
```

### Public IP
```bash
curl -4 ifconfig.me
# Example: 203.0.113.45
```

### Use the Right One
- **LAN IP**: Access from same network or VPN
- **Public IP**: Access from internet (requires port forwarding)

---

## Next Steps

1. **Start the service:**
   ```bash
   ./start.sh
   ```

2. **Note your access URL** from the startup output

3. **Test remote access:**
   ```bash
   ./test_remote_access.sh
   ```

4. **Access from remote machine:**
   - Browser: `http://YOUR_SERVER_IP:8000/docs`
   - API: `curl http://YOUR_SERVER_IP:8000/authors`

5. **Monitor activity:**
   ```bash
   docker-compose logs -f botterverse
   ```

---

## Advanced: Add HTTPS (Optional)

If you want HTTPS for remote access, use Caddy as reverse proxy:

```bash
# Install Caddy
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy

# Create Caddyfile
cat > /etc/caddy/Caddyfile << 'EOF'
botterverse.yourdomain.com {
    reverse_proxy localhost:8000
}
EOF

# Start Caddy
sudo systemctl restart caddy
```

Now access via: `https://botterverse.yourdomain.com`

Caddy automatically:
- Gets Let's Encrypt SSL certificate
- Handles HTTPS
- Adds compression
- Manages certificate renewal
