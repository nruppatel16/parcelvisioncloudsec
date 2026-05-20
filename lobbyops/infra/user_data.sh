#!/bin/bash
set -euo pipefail

# EC2 bootstrap for LobbyOps on Amazon Linux 2023.
# Runs once at instance launch. All secrets come from SSM Parameter Store.

dnf update -y
dnf install -y python3.11 python3.11-pip nginx git firewalld

# Clone application repository
git clone https://github.com/nruppatel16/parcelvisioncloudsec.git /opt/lobbyops
cd /opt/lobbyops/lobbyops
python3.11 -m pip install -r requirements.txt

# Create application user
id -u lobbyops &>/dev/null || useradd -r -s /sbin/nologin lobbyops

# Format and mount data EBS volume
if ! blkid /dev/xvdf; then
    mkfs.ext4 /dev/xvdf
fi
mkdir -p /data
echo "/dev/xvdf /data ext4 defaults,nofail 0 2" >> /etc/fstab
mount -a
chown lobbyops:lobbyops /data

chown -R lobbyops:lobbyops /opt/lobbyops

# Pull secrets from SSM Parameter Store into .env
REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)
aws ssm get-parameters-by-path \
    --path /lobbyops \
    --with-decryption \
    --region "$REGION" \
    --query 'Parameters[*].[Name,Value]' \
    --output text | \
    awk '{split($1, a, "/"); print a[length(a)] "=" $2}' \
    > /opt/lobbyops/lobbyops/.env

chown lobbyops:lobbyops /opt/lobbyops/lobbyops/.env
chmod 600 /opt/lobbyops/lobbyops/.env

# systemd service
cp /opt/lobbyops/systemd/lobbyops.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable lobbyops
systemctl start lobbyops

# nginx
cp /opt/lobbyops/nginx/lobbyops.conf /etc/nginx/conf.d/lobbyops.conf
nginx -t
systemctl enable nginx
systemctl start nginx

# Firewall: drop all by default, allow only HTTPS and SSH
systemctl enable --now firewalld
firewall-cmd --permanent --set-default-zone=drop
firewall-cmd --permanent --add-service=https
firewall-cmd --permanent --add-service=ssh
firewall-cmd --reload
