# CloudFlare DDNS Deployment Script for Proxmox LXC Container (Alpine Linux)
# This script securely deploys the CloudFlare DDNS application to a Proxmox LXC container

param(
    [Parameter(Mandatory=$true)]
    [string]$LXC_HOST,
    
    [Parameter(Mandatory=$false)]
    [string]$LXC_USER = "root",
    
    [Parameter(Mandatory=$false)]
    [int]$LXC_PORT = 22,
    
    [Parameter(Mandatory=$false)]
    [string]$DEPLOY_PATH = "/opt/cloudflare-ddns",
    
    [Parameter(Mandatory=$false)]
    [string]$SERVICE_USER = "cloudflare-ddns"
)

# Configuration
$ErrorActionPreference = "Stop"
$APP_NAME = "cloudflare-ddns"
$SCRIPT_DIR = $PSScriptRoot
$REQUIRED_FILES = @(
    "cloudflare-ddns.py",
    "requirements.txt",
    "config.json"
)

# Colors for output
function Write-ColorOutput($Color, $Message) {
    Write-Host $Message -ForegroundColor $Color
}

function Write-Success($Message) {
    Write-ColorOutput Green "✅ $Message"
}

function Write-Info($Message) {
    Write-ColorOutput Blue "ℹ️  $Message"
}

function Write-Warning($Message) {
    Write-ColorOutput Yellow "⚠️  $Message"
}

function Write-Error($Message) {
    Write-ColorOutput Red "❌ $Message"
}

function Test-Prerequisites {
    Write-Info "Checking prerequisites..."
    
    # Check if required files exist
    foreach ($file in $REQUIRED_FILES) {
        if (-not (Test-Path "$SCRIPT_DIR\$file")) {
            Write-Error "Required file missing: $file"
            Write-Info "Please ensure config.json exists (copy from config-example.json and configure)"
            exit 1
        }
    }
    
    # Check if SSH client is available
    try {
        ssh -V 2>$null
    } catch {
        Write-Error "SSH client not found. Please install OpenSSH client."
        Write-Info "Install via: Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0"
        exit 1
    }
    
    Write-Success "Prerequisites check passed"
}

function Test-SSHConnection {
    Write-Info "Testing SSH connection to ${LXC_USER}@${LXC_HOST}:${LXC_PORT}..."
    
    $testCommand = "echo 'Connection test successful'"
    try {
        $result = ssh -o ConnectTimeout=10 -o BatchMode=yes -p $LXC_PORT "${LXC_USER}@${LXC_HOST}" $testCommand 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Success "SSH connection successful"
        } else {
            Write-Error "SSH connection failed: $result"
            Write-Info "Please ensure SSH key authentication is set up correctly"
            exit 1
        }
    } catch {
        Write-Error "SSH connection test failed: $_"
        exit 1
    }
}

function Deploy-Application {
    Write-Info "Starting deployment to $LXC_HOST..."
    
    # Create deployment directory
    Write-Info "Creating deployment directory..."
    ssh -p $LXC_PORT "${LXC_USER}@${LXC_HOST}" "mkdir -p $DEPLOY_PATH && chown ${LXC_USER}:${LXC_USER} $DEPLOY_PATH"
    
    # Copy application files
    Write-Info "Copying application files..."
    scp -P $LXC_PORT -r "$SCRIPT_DIR\cloudflare-ddns.py" "$SCRIPT_DIR\requirements.txt" "$SCRIPT_DIR\config.json" "${LXC_USER}@${LXC_HOST}:${DEPLOY_PATH}/"
    
    # Install dependencies and setup application
    Write-Info "Setting up application on remote host..."
    
    $setupScript = @"
#!/bin/sh
set -e

# Update package manager
apk update

# Install Python and pip
apk add --no-cache python3 py3-pip python3-dev

# Create service user if not exists
if ! id -u $SERVICE_USER >/dev/null 2>&1; then
    adduser -D -s /bin/sh $SERVICE_USER
fi

# Set proper permissions
chown -R ${SERVICE_USER}:${SERVICE_USER} $DEPLOY_PATH
chmod 755 $DEPLOY_PATH
chmod 644 $DEPLOY_PATH/*.json
chmod 755 $DEPLOY_PATH/cloudflare-ddns.py

# Install Python dependencies globally (Alpine way)
cd $DEPLOY_PATH
pip3 install -r requirements.txt

# Create log directory
mkdir -p /var/log/$APP_NAME
chown ${SERVICE_USER}:${SERVICE_USER} /var/log/$APP_NAME

# Create OpenRC service file content
SERVICE_CONTENT="#!/sbin/openrc-run

name=\"Cloudflare DDNS\"
description=\"CloudFlare Dynamic DNS Service\"

user=\"$SERVICE_USER\"
group=\"$SERVICE_USER\"
pidfile=\"/var/run/$APP_NAME.pid\"
command=\"/usr/bin/python3\"
command_args=\"$DEPLOY_PATH/cloudflare-ddns.py\"
command_background=\"yes\"
command_user=\"`$user:`$group\"
directory=\"$DEPLOY_PATH\"

output_log=\"/var/log/$APP_NAME/$APP_NAME.log\"
error_log=\"/var/log/$APP_NAME/$APP_NAME.log\"

depend() {
    need net
    after firewall
}

start_pre() {
    checkpath --directory --owner `$user:`$group --mode 0755 `$(dirname `$pidfile)
    checkpath --directory --owner `$user:`$group --mode 0755 /var/log/$APP_NAME
    checkpath --file --owner `$user:`$group --mode 0644 /var/log/$APP_NAME/$APP_NAME.log
}"

# Write the service file
echo "`$SERVICE_CONTENT" | tee /etc/init.d/$APP_NAME > /dev/null

# Make service executable
chmod +x /etc/init.d/$APP_NAME

# Enable and start service
rc-update add $APP_NAME default
rc-service $APP_NAME stop 2>/dev/null || true
rc-service $APP_NAME start

echo "Deployment completed successfully!"
"@
    
    # Execute setup script on remote host
    Write-Output $setupScript | ssh -p $LXC_PORT "${LXC_USER}@${LXC_HOST}" 'cat > /tmp/setup.sh && chmod +x /tmp/setup.sh && /tmp/setup.sh && rm /tmp/setup.sh'
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Application deployed successfully!"
    } else {
        Write-Error "Deployment failed"
        exit 1
    }
}

function Show-Status {
    Write-Info "Checking service status (OpenRC)..."
    ssh -p $LXC_PORT "${LXC_USER}@${LXC_HOST}" "rc-service $APP_NAME status || (echo 'Listing services via rc-status:' && rc-status | grep $APP_NAME || true)"
}

function Show-Logs {
    Write-Info "Showing recent logs (tail file)..."
    ssh -p $LXC_PORT "${LXC_USER}@${LXC_HOST}" "tail -n 200 /var/log/$APP_NAME/$APP_NAME.log || echo 'Log file not found'"
}

# Main execution
try {
    Write-Info "CloudFlare DDNS Deployment Script"
    Write-Info "=================================="
    Write-Info "Target: ${LXC_USER}@${LXC_HOST}:${LXC_PORT}"
    Write-Info "Deploy Path: $DEPLOY_PATH"
    Write-Info "Service User: $SERVICE_USER"
    Write-Info ""
    
    Test-Prerequisites
    Test-SSHConnection
    Deploy-Application
    Show-Status
    Show-Logs
    
    Write-Success "Deployment completed successfully!"
    Write-Info "You can manage the service with:"
    Write-Info "  Start:   rc-service $APP_NAME start"
    Write-Info "  Stop:    rc-service $APP_NAME stop"
    Write-Info "  Status:  rc-service $APP_NAME status"
    Write-Info "  Logs:    tail -f /var/log/$APP_NAME/$APP_NAME.log"
    
} catch {
    Write-Error "Deployment failed: $_"
    exit 1
} 