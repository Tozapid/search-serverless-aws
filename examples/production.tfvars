# Production: your own domain, digests by mail, no public sign-in and no
# hourly reset. Copy to terraform.tfvars and fill in.

name = "search"
demo = false

domain_name       = "search.example.com"
route53_zone_name = "example.com" # "" if the DNS is elsewhere, see README

# On a domain verified in SES in this region.
sender_address = "search@example.com"

admin_email    = "you@example.com"
admin_password = "" # Cognito mails a temporary password instead

reset_schedule        = ""
max_searches_per_user = 20
ai_monthly_limit_usd  = 0.5
