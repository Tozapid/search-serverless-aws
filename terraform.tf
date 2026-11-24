terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 6.69.0 cannot publish a Lambda layer ("decomposing request:
      # bufio.Scanner: token too long", hashicorp/terraform-provider-aws#50474).
      version               = "~> 6.0, != 6.69.0"
      configuration_aliases = [aws.global]
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6"
    }
    # Runs lambda/build_layer.py while planning, for the layer of search.py.
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
  }
}
