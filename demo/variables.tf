variable "region" {
  type        = string
  default     = "eu-central-1"
  description = "Region of every resource but CloudFront: the API, the searches, the mail and the sign-in."
}

# The CI reads this default: a preview of a pull request is <name>-pr<N>.
variable "name" {
  type        = string
  default     = "search-serverless"
  description = "Prefix of every resource name; bucket names add the account id. The deploy role of the demo is limited to it."
}

variable "access_logs_bucket" {
  type        = string
  default     = ""
  description = "Bucket that the access logs of the demo go to (the repository variable ACCESS_LOGS_BUCKET in CI). Empty: no access logs."
}

variable "tags" {
  type = map(string)
  default = {
    Managed = "IAC"
    Project = "search-serverless-aws"
  }
  description = "Tags applied to supported resources."
}
