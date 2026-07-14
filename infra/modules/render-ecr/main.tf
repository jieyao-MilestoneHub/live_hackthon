# render-ecr: ECR repo for the FFmpeg render container (separate from backend).
variable "name" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_ecr_repository" "render" {
  name                 = var.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
  tags = merge(var.tags, { Purpose = "render-ffmpeg-image" })
}

output "repository_url" {
  value = aws_ecr_repository.render.repository_url
}
output "repository_arn" {
  value = aws_ecr_repository.render.arn
}
