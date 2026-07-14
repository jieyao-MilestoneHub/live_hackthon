terraform {
  # Pinned to the locally-installed toolchain: Terraform 1.10.5 + AWS provider 5.x.
  #
  # NOTE: docs/aws-infra.md §12 recommends TF ~> 1.15 / aws ~> 6.54 (the latest
  # as of 2026-07-14). We intentionally pin LOWER to match the installed CLI
  # (terraform 1.10.5) and to avoid AWS provider 6.x behavioral/schema changes
  # that could break local `plan`/`apply`. Bump both together in a dedicated PR
  # once the team upgrades their local Terraform.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      Env       = var.env
      ManagedBy = "terraform"
    }
  }
}
