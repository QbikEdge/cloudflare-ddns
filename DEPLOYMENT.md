# CloudFlare DDNS Deployment to Proxmox LXC Container

This PowerShell script automates the secure deployment of the CloudFlare DDNS application to a Proxmox LXC container running Alpine Linux.

## Prerequisites

### On Windows (Your Local Machine)

1. **PowerShell** (Windows PowerShell 5.1+ or PowerShell 7+)
2. **OpenSSH Client** - Install if not available:
   ```powershell
   Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
   ```
3. **SSH Key Authentication** - Set up passwordless SSH access to your LXC container

### On LXC Container (Alpine Linux)

1. **SSH Server** running and accessible
2. **Sudo privileges** for the deployment user
3. **systemd** (most Alpine installations include this)

## Setup SSH Key Authentication

### 1. Generate SSH Key (if you don't have one)

```powershell
ssh-keygen -t ed25519 -C "your-email@example.com"
```

### 2. Copy SSH Key to LXC Container

```powershell
# Replace with your actual values
$LXC_HOST = "192.168.1.100"
$LXC_USER = "root"

# Copy the key
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh $LXC_USER@$LXC_HOST "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

### 3. Test SSH Connection

```powershell
ssh $LXC_USER@$LXC_HOST "echo 'SSH connection successful'"
```

## Configuration

### 1. Create Configuration File

Copy `config-example.json` to `config.json` and configure your CloudFlare settings:

```json
{
  "cloudflare": [
    {
      "authentication": {
        "api_token": "your_cloudflare_api_token_here"
      },
      "zone_id": "your_zone_id_here",
      "subdomains": [
        {
          "name": "home",
          "proxied": false
        }
      ]
    }
  ],
  "a": true,
  "aaaa": true,
  "purgeUnknownRecords": false,
  "ttl": 300,
  "cyclic_config_read": true,
  "sleep_time": 5
}
```

**Important**: Keep your `config.json` file secure and never commit it to version control.

## Deployment

### Basic Usage

```powershell
.\deploy-to-proxmox.ps1 -LXC_HOST "192.168.1.100"
```

### Advanced Usage with Custom Settings

```powershell
.\deploy-to-proxmox.ps1 `
    -LXC_HOST "192.168.1.100" `
    -LXC_USER "root" `
    -LXC_PORT 2222 `
    -DEPLOY_PATH "/opt/my-ddns" `
    -SERVICE_USER "ddns-service"
```

### Parameters

| Parameter      | Required | Default              | Description                             |
| -------------- | -------- | -------------------- | --------------------------------------- |
| `LXC_HOST`     | Yes      | -                    | IP address or hostname of LXC container |
| `LXC_USER`     | No       | root                 | SSH username for LXC container          |
| `LXC_PORT`     | No       | 22                   | SSH port                                |
| `DEPLOY_PATH`  | No       | /opt/cloudflare-ddns | Installation directory on LXC           |
| `SERVICE_USER` | No       | cloudflare-ddns      | System user to run the service          |

## What the Script Does

1. **Prerequisites Check**: Verifies required files and SSH client
2. **SSH Connection Test**: Ensures secure connection to LXC container
3. **File Transfer**: Copies application files using SCP
4. **Remote Setup**:
   - Updates Alpine package manager
   - Installs Python 3 and pip
   - Creates dedicated service user
   - Sets proper file permissions
   - Installs Python dependencies
   - Creates systemd service
   - Enables and starts the service
5. **Status Check**: Shows service status and recent logs

## Security Features

- **SSH Key Authentication**: No passwords transmitted
- **Dedicated Service User**: Application runs with minimal privileges
- **Secure File Permissions**: Restrictive file access
- **Connection Timeout**: Prevents hanging connections
- **Error Handling**: Stops deployment on any failure

## Service Management

After successful deployment, manage the service on your LXC container:

```bash
# Check status
sudo systemctl status cloudflare-ddns

# View logs
sudo journalctl -u cloudflare-ddns -f

# Start/Stop/Restart
sudo systemctl start cloudflare-ddns
sudo systemctl stop cloudflare-ddns
sudo systemctl restart cloudflare-ddns

# Disable service
sudo systemctl disable cloudflare-ddns
```

## Troubleshooting

### Common Issues

1. **SSH Connection Failed**

   - Verify SSH key authentication is set up
   - Check if SSH service is running on LXC container
   - Confirm firewall allows SSH connections

2. **Permission Denied**

   - Ensure deployment user has sudo privileges
   - Check SSH key permissions (should be 600)

3. **File Not Found**

   - Verify `config.json` exists in script directory
   - Ensure all required files are present

4. **Service Start Failed**
   - Check configuration file syntax
   - Verify CloudFlare API credentials
   - Review service logs for details

### Log Analysis

```bash
# View detailed logs
sudo journalctl -u cloudflare-ddns --no-pager -l

# Follow logs in real-time
sudo journalctl -u cloudflare-ddns -f
```

## Updating the Application

To update the application, simply run the deployment script again. It will:

- Stop the existing service
- Update files
- Restart the service

## Uninstallation

To remove the application from your LXC container:

```bash
# Stop and disable service
sudo systemctl stop cloudflare-ddns
sudo systemctl disable cloudflare-ddns

# Remove service file
sudo rm /etc/systemd/system/cloudflare-ddns.service
sudo systemctl daemon-reload

# Remove application directory
sudo rm -rf /opt/cloudflare-ddns

# Remove service user (optional)
sudo deluser cloudflare-ddns
```

## Support

If you encounter issues:

1. Check the troubleshooting section above
2. Review application logs
3. Verify your CloudFlare configuration
4. Ensure network connectivity between LXC and CloudFlare APIs
