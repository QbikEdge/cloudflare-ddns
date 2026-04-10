#!/bin/sh
# Minimal Deployment Script for Alpine Linux (Low Disk Space)
# Run this directly on your Alpine LXC container

set -e

APP_NAME="cloudflare-ddns"
DEPLOY_PATH="/opt/cloudflare-ddns"
SERVICE_USER="cloudflare-ddns"

echo "🚀 Starting CloudFlare DDNS Minimal Deployment"
echo "=============================================="

# Stop existing service if running
echo "📝 Stopping existing service..."
rc-service $APP_NAME stop 2>/dev/null || true

# Clean up space first
echo "🧹 Cleaning up space..."
apk cache clean
rm -rf /tmp/* 2>/dev/null || true
rm -rf /var/tmp/* 2>/dev/null || true

# Update package manager and install MINIMAL dependencies
echo "📦 Installing minimal dependencies..."
apk update
apk add --no-cache python3 py3-pip

# Create service user if not exists
echo "👤 Creating service user..."
if ! id -u $SERVICE_USER >/dev/null 2>&1; then
    adduser -D -s /bin/sh $SERVICE_USER
fi

# Ensure deployment directory exists
echo "📁 Setting up deployment directory..."
mkdir -p $DEPLOY_PATH
chown $SERVICE_USER:$SERVICE_USER $DEPLOY_PATH

# Install Python dependencies using pip (without dev packages)
echo "🐍 Installing Python dependencies..."
# Try Alpine package first, then pip with override
apk add --no-cache py3-requests 2>/dev/null || \
pip3 install --break-system-packages --no-cache-dir requests==2.31.0

# Create log directory
echo "📊 Creating log directory..."
mkdir -p /var/log/$APP_NAME
chown $SERVICE_USER:$SERVICE_USER /var/log/$APP_NAME

# Remove any existing service file and create new one
echo "⚙️ Creating OpenRC service..."
rm -f /etc/init.d/$APP_NAME

cat > /etc/init.d/$APP_NAME << 'EOF'
#!/sbin/openrc-run

name="Cloudflare DDNS"
description="CloudFlare Dynamic DNS Service"

user="cloudflare-ddns"
group="cloudflare-ddns"
pidfile="/var/run/cloudflare-ddns.pid"
command="/usr/bin/python3"
command_args="/opt/cloudflare-ddns/cloudflare-ddns.py"
command_background="yes"
command_user="$user:$group"
directory="/opt/cloudflare-ddns"

output_log="/var/log/cloudflare-ddns/cloudflare-ddns.log"
error_log="/var/log/cloudflare-ddns/cloudflare-ddns.log"

depend() {
    need net
    after firewall
}

start_pre() {
    checkpath --directory --owner $user:$group --mode 0755 $(dirname $pidfile)
    checkpath --directory --owner $user:$group --mode 0755 /var/log/cloudflare-ddns
    checkpath --file --owner $user:$group --mode 0644 /var/log/cloudflare-ddns/cloudflare-ddns.log
}
EOF

# Make service executable
chmod +x /etc/init.d/$APP_NAME

# Set proper permissions for application files
echo "🔒 Setting permissions..."
chown -R $SERVICE_USER:$SERVICE_USER $DEPLOY_PATH
chmod 755 $DEPLOY_PATH/cloudflare-ddns.py 2>/dev/null || true
chmod 644 $DEPLOY_PATH/config.json 2>/dev/null || true

# Remove from any existing runlevels and add to default
echo "🎯 Configuring service..."
rc-update del $APP_NAME default 2>/dev/null || true
rc-update add $APP_NAME default

# Start service
echo "▶️ Starting service..."
rc-service $APP_NAME start

echo ""
echo "✅ Minimal deployment completed!"
echo ""
echo "📋 Service Management:"
echo "  Status:  rc-service $APP_NAME status"
echo "  Logs:    tail -f /var/log/$APP_NAME/$APP_NAME.log"
echo ""
echo "💾 Disk usage after deployment:"
df -h /

echo ""
echo "🔍 Final service status:"
rc-service $APP_NAME status 