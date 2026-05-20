output "instance_id" {
  description = "EC2 instance ID."
  value       = aws_instance.lobbyops.id
}

output "instance_public_ip" {
  description = "Instance public IP. Changes on stop/start; use elastic_ip for stable addressing."
  value       = aws_instance.lobbyops.public_ip
}

output "elastic_ip" {
  description = "Elastic IP bound to the instance."
  value       = aws_eip.lobbyops.public_ip
}
