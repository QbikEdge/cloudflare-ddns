#!/bin/sh
# Manual Deployment Script for Alpine Linux
# Run this directly on your Alpine LXC container

set -e

APP_NAME="cloudflare-ddns"
DEPLOY_PATH="/opt/cloudflare-ddns"
SERVICE_USER="cloudflare-ddns"

echo "🚀 Starting CloudFlare DDNS Manual Deployment on Alpine Linux"
echo "=============================================================="

# Stop existing service if running
echo "📝 Stopping existing service..."
rc-service $APP_NAME stop 2>/dev/null || true

# Update package manager and install dependencies
echo "📦 Installing dependencies..."
apk update
apk add --no-cache python3 py3-pip python3-dev

# Create service user if not exists
echo "👤 Creating service user..."
if ! id -u $SERVICE_USER >/dev/null 2>&1; then
    adduser -D -s /bin/sh $SERVICE_USER
fi

# Create deployment directory
echo "📁 Creating deployment directory..."
mkdir -p $DEPLOY_PATH
chown $SERVICE_USER:$SERVICE_USER $DEPLOY_PATH

# Install Python dependencies - Alpine way with system packages first
echo "🐍 Installing Python dependencies..."
# Try to install via apk first (preferred for Alpine)
apk add --no-cache py3-requests 2>/dev/null || \
# If not available via apk, use pip with override
pip3 install --break-system-packages requests==2.31.0

# Create log directory
echo "📊 Creating log directory..."
mkdir -p /var/log/$APP_NAME
chown $SERVICE_USER:$SERVICE_USER /var/log/$APP_NAME

# Create OpenRC service
echo "⚙️ Creating OpenRC service..."
cat > /etc/init.d/$APP_NAME << 'EOF'
#!/sbin/openrc-run

# Enable OpenRC supervision
supervise=YES
# Allow up to 5 restarts within a 60-second period, with a 10-second delay between attempts
respawn_max=5
respawn_period=60
respawn_delay=10

description="Cloudflare Dynamic DNS Service"
name="cloudflare-ddns"

# User and group for the service to run as, improving security
user="cloudflare-ddns"
group="cloudflare-ddns"
directory="/opt/cloudflare-ddns"

command="/usr/bin/python3"
command_args="/opt/cloudflare-ddns/cloudflare-ddns.py --repeat"
command_background="yes"

# PID file for process management
pidfile="/run/${name}.pid"

# Unified log file for stdout and stderr, simplifying log management
output_log="/var/log/${name}/${name}.log"
error_log="/var/log/${name}/${name}.log"

depend() {
  # Ensure network is available before starting
  need net
  # Start after firewall has been set up
  after firewall
}

start_pre() {
  # Create necessary directories and log files with appropriate permissions and ownership
  checkpath --directory --mode 0755 --owner "${user}:${group}" "${directory}"
  checkpath --directory --mode 0755 --owner "${user}:${group}" "/var/log/${name}"
  checkpath --file --mode 0644 --owner "${user}:${group}" "${output_log}"
}
EOF

# Make service executable
chmod +x /etc/init.d/$APP_NAME

# Set proper permissions for application files
echo "🔒 Setting permissions..."
chown -R $SERVICE_USER:$SERVICE_USER $DEPLOY_PATH
chmod 755 $DEPLOY_PATH/cloudflare-ddns.py
chmod 644 $DEPLOY_PATH/config.json

# Enable and start service
echo "🎯 Enabling and starting service..."
rc-update add $APP_NAME default
rc-service $APP_NAME start

echo ""
echo "✅ Deployment completed successfully!"
echo ""
echo "📋 Service Management Commands:"
echo "  Status:  rc-service $APP_NAME status"
echo "  Start:   rc-service $APP_NAME start"
echo "  Stop:    rc-service $APP_NAME stop"
echo "  Restart: rc-service $APP_NAME restart"
echo "  Logs:    tail -f /var/log/$APP_NAME/$APP_NAME.log"
echo ""
echo "🔍 Checking service status..."
rc-service $APP_NAME status 
