variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "ca-central-1"
}

variable "key_pair_name" {
  description = "Name of an existing EC2 key pair for SSH access."
  type        = string
}

variable "admin_cidr" {
  description = "CIDR block allowed SSH access. Restrict to a static IP or VPN egress."
  type        = string
}
