variable "region" {
  type        = string
  default     = "eu-central-1"
  description = "Region of every resource but CloudFront: the API, the searches, the mail and the sign-in."
}

variable "name" {
  type        = string
  default     = "search-serverless"
  description = "Prefix of every resource name; bucket names add the account id. The deploy role of the demo is limited to it."
}

variable "demo" {
  type        = bool
  default     = true
  description = "Public demo: the sign-in screen shows and fills in the demo sign-in, the settings hide the password and two-factor authentication, and administrators may look at the users and the proxies but not change them."
}

variable "domain_name" {
  type        = string
  default     = ""
  description = "Name of the page, such as search.example.com, with a certificate of its own. Empty: the page answers at its CloudFront address (the demo)."
}

variable "route53_zone_name" {
  type        = string
  default     = ""
  description = "Public Route 53 zone of domain_name; the stack writes the certificate's validation and the alias records there. Empty with a domain_name: validate the certificate and point the name at CloudFront by hand (outputs certificate_validation_records and cloudfront_url)."
}

variable "sender_address" {
  type        = string
  default     = ""
  description = "From address of the digests, on a domain verified in SES in this region. Empty: no mail; runs keep their results and AI digests in the history only (the demo)."

  validation {
    condition     = var.sender_address == "" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.sender_address))
    error_message = "sender_address is an email address or empty."
  }
}

variable "admin_email" {
  type        = string
  default     = "demo@example.com"
  description = "Sign-in of the first user, an administrator. Self sign-up is off; administrators invite the others on the page."
}

variable "admin_password" {
  type        = string
  default     = "Demo-Password-1"
  sensitive   = true
  description = "Permanent password of the first user, published in the demo and put back by the reset. Empty: Cognito mails admin_email a temporary password instead."
}

variable "reset_schedule" {
  type        = string
  default     = "rate(1 hour)"
  description = "When the demo goes back to its initial state: searches, their history and the settings are wiped and the examples of seed/seed.json written again, the first user gets admin_password back and other users go. An empty string turns the reset off."
}

variable "tick_minutes" {
  type        = number
  default     = 60
  description = "How often the scheduler looks for searches that are due. The hours of the schedules are as exact as this."

  validation {
    condition     = contains([15, 30, 60], var.tick_minutes)
    error_message = "tick_minutes is 15, 30 or 60."
  }
}

variable "max_searches_per_user" {
  type        = number
  default     = 10
  description = "How many searches one user may keep. Keeps the bill of a single account, and of a public demo, in check."
}

variable "ai_enabled" {
  type        = bool
  default     = true
  description = "Whether searches may shorten their digest with a model of Amazon Bedrock. Each search turns it on for itself."
}

variable "ai_model" {
  type        = string
  default     = "eu.amazon.nova-2-lite-v1:0"
  description = "Model of the digest on Amazon Bedrock (bedrock-runtime, Converse API): an inference profile such as eu.amazon.nova-2-lite-v1:0 (EU Regions only) or global.amazon.nova-2-lite-v1:0. The function works with any text model of Bedrock."

  validation {
    condition     = can(regex("^(eu|us|global|jp|au|apac)\\.[a-z0-9-]+\\.[a-z0-9.:-]+$", var.ai_model))
    error_message = "ai_model is an inference profile ID such as eu.amazon.nova-2-lite-v1:0."
  }
}

variable "ai_marketplace_product_id" {
  type        = string
  default     = ""
  description = "AWS Marketplace product of ai_model when it is a third-party model (on its model card in the Bedrock documentation): the function may then subscribe the account to it on the first call, and to nothing else. Empty for Amazon's own models such as Nova."
}

variable "ai_region" {
  type        = string
  default     = "eu-central-1"
  description = "Region of the bedrock-runtime endpoint the function calls; the inference profile of ai_model routes from there."
}

variable "ai_price_per_mtok" {
  type = object({
    input  = number
    output = number
  })
  default = {
    input  = 0.33
    output = 2.75
  }
  description = "USD per million input and output tokens of ai_model through its inference profile. The function counts the month's spend with it and stops the AI digest at ai_monthly_limit_usd."
}

variable "ai_monthly_limit_usd" {
  type        = number
  default     = 0.2
  description = "Spend on the AI digest per calendar month (UTC) after which digests go out without it until the next month. The demo reset keeps the count."
}

variable "releases_repository" {
  type        = string
  default     = "Tozapid/search-serverless-aws"
  description = "GitHub repository (owner/name) whose releases the settings compare the version of the site with. Empty turns the check off."
}

variable "tags" {
  type = map(string)
  default = {
    Managed = "IAC"
    Project = "search-serverless-aws"
  }
  description = "Tags applied to supported resources."
}
