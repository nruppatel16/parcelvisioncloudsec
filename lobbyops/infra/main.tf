terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

resource "aws_instance" "lobbyops" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = "t3.micro"
  key_name               = var.key_pair_name
  iam_instance_profile   = aws_iam_instance_profile.lobbyops.name
  vpc_security_group_ids = [aws_security_group.lobbyops.id]
  user_data              = file("${path.module}/user_data.sh")

  root_block_device {
    volume_size = 8
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name        = "lobbyops"
    Project     = "LobbyOps"
    Environment = "production"
    ManagedBy   = "terraform"
  }
}

resource "aws_ebs_volume" "data" {
  availability_zone = aws_instance.lobbyops.availability_zone
  size              = 20
  type              = "gp3"
  encrypted         = true

  tags = {
    Name        = "lobbyops-data"
    Project     = "LobbyOps"
    Environment = "production"
    ManagedBy   = "terraform"
  }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/xvdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.lobbyops.id
}

resource "aws_eip" "lobbyops" {
  instance = aws_instance.lobbyops.id
  domain   = "vpc"

  tags = {
    Name        = "lobbyops-eip"
    Project     = "LobbyOps"
    Environment = "production"
    ManagedBy   = "terraform"
  }
}

resource "aws_iam_role" "lobbyops" {
  name = "lobbyops-instance-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Project   = "LobbyOps"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_role_policy" "ssm_read" {
  name = "lobbyops-ssm-read"
  role = aws_iam_role.lobbyops.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ssm:GetParameter",
        "ssm:GetParameters",
        "ssm:GetParametersByPath",
      ]
      Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/lobbyops/*"
    }]
  })
}

resource "aws_iam_instance_profile" "lobbyops" {
  name = "lobbyops-instance-profile"
  role = aws_iam_role.lobbyops.name
}
