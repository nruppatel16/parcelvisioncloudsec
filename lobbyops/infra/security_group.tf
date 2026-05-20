resource "aws_security_group" "lobbyops" {
  name        = "lobbyops"
  description = "LobbyOps inbound and outbound traffic rules."

  tags = {
    Name        = "lobbyops"
    Project     = "LobbyOps"
    Environment = "production"
    ManagedBy   = "terraform"
  }
}

# HTTPS inbound -- required for all clients: 1Valet portal, mobile app uploads.
resource "aws_security_group_rule" "https_in" {
  type              = "ingress"
  security_group_id = aws_security_group.lobbyops.id
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "HTTPS from all clients"
}

# SSH inbound -- restricted to admin CIDR. 0.0.0.0/0 is never acceptable here.
resource "aws_security_group_rule" "ssh_in" {
  type              = "ingress"
  security_group_id = aws_security_group.lobbyops.id
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = [var.admin_cidr]
  description       = "SSH from admin CIDR only"
}

# HTTPS outbound -- covers Gemini API, Google Sheets API, and any 1Valet callbacks.
# All external dependencies run on port 443; no other egress protocol is needed.
resource "aws_security_group_rule" "https_out" {
  type              = "egress"
  security_group_id = aws_security_group.lobbyops.id
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "HTTPS to Gemini API, Google Sheets, and 1Valet"
}
