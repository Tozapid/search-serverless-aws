# Scheduled search

Searches that repeat themselves in several search engines and mail you what they find, running serverless on AWS. Each search has a query, its engines, the region and period of the results, a schedule, the address of the digest and, if you like, a short AI digest by a model of Amazon Bedrock. By default a digest carries only what earlier digests did not; with nothing new there is no mail.

Everything is Terraform and AWS managed services: no third-party API, no API keys, no servers. Nothing runs between the scheduled searches and page views, so an installation costs cents a month (see [Cost](#cost)).

## Demo

**<https://dez6kwn58upj9.cloudfront.net>**

Sign in with `demo@example.com` / `Demo-Password-1` (the fields are filled in for you).

The demo has **no mail**: it never sends a single message. Everything else works, on real search engines: create, edit, switch off and delete searches, preview what the engines find now, run a search now and read its results and AI digest in its history, turn on the combined digest, switch the language. The demo user is an administrator, so the users and the proxies are there to look at; changing them is not. The demo is public and goes back to its initial state **every hour**: searches, their history and the settings are wiped, the examples of [`seed/seed.json`](seed/seed.json) are written again, the demo password is put back and any other user is deleted. Changing the password and two-factor authentication are hidden in the demo, so no visitor can lock the others out. The AI digest of the demo has a small monthly limit; once it is spent, runs go on without it until the next month.

## Features

- **Searches**: a query, the engines, the region and period of the results, up to 30 results per engine; only the results not sent before, or all of them; no mail when there is nothing new, if you like.
- **Schedules**: chosen weekdays at an hour, or every 1, 2, 3, 4, 6, 8 or 12 hours, in the time zone of the search.
- **AI digest**: a model of Amazon Bedrock (Amazon Nova 2 Lite by default) writes a few sentences and a list of the findings above the results, with your wishes ("only prices", "no reviews") and a monthly spending limit.
- **Combined digest**: all searches of a user in one message on one schedule, a section per search with its own AI digest.
- **Run now, preview, history**: a run with mail outside the schedule; what the engines find now, without mail; the runs of the last 90 days with their results and digests; forgetting what was sent.
- **Administrators**: the searches of every user, and the accounts — invite, new temporary password, reset of two-factor sign-in, disable, administrator role, delete.
- **Proxies** for engines that turn programs away (DuckDuckGo, Brave Search): a list of SOCKS and HTTP proxies tried in turn when the direct request fails.
- **Version**: Settings → Version shows the version of the site and the releases of this repository that came after it.
- **Account**: two-factor authentication with an authenticator app (TOTP), password change; interface, mail and AI digest in English, Russian, French and Italian.

## How it works

```mermaid
flowchart LR
  B[Browser] --> CF[CloudFront]
  CF -->|page| WEB[(S3: page)]
  CF -->|/api/*| API[API Gateway] --> L[Lambda]
  B -->|sign in| COG[Cognito]
  EB[EventBridge, every tick_minutes] -->|tick| L
  L -->|one invocation per due search| L
  L <--> DB[(DynamoDB)]
  L -->|RSS, HTML| SE[Search engines]
  L -->|Converse| BR[Amazon Bedrock]
  L -->|SendEmail| SES[SES]
  EB2[EventBridge, demo only] --> R[Lambda: hourly reset]
```

| Part | Service |
| --- | --- |
| Page | S3 + CloudFront, on its own domain or on the `*.cloudfront.net` address |
| Sign-in | Cognito user pool, no self sign-up, optional TOTP; group `admins` |
| API | API Gateway HTTP API with a Cognito JWT authorizer, Lambda `lambda/search.py` (Python, arm64) |
| Schedule | EventBridge `rate(tick_minutes)` wakes the same Lambda; the sparse index `due` of the table lists the searches whose time has come |
| Engines | `lambda/engines.py`: RSS and HTML pages, read as a browser without JavaScript reads them |
| Data | DynamoDB on demand, one table: searches, what they sent, their history, combined digests, settings, the month's AI spend |
| Mail | SES, with a configuration set that counts opens and leaves links alone (mail only) |
| AI digest | Amazon Bedrock, Converse API |
| Demo reset | Lambda `lambda/reset.py`, EventBridge `rate(1 hour)` |

### Search engines

None of the big engines has a free search API any more, so each one is read the way a browser without JavaScript reads it:

| Engine | How it is read |
| --- | --- |
| Google News | RSS `news.google.com/rss/search`; the period as `when:1d/7d/30d` in the query |
| Bing News | RSS `bing.com/news/search?format=rss`; links freed of Bing's click counter |
| Yahoo | HTML results of `search.yahoo.com` (the Bing index) |
| DuckDuckGo | HTML version `html.duckduckgo.com`; without a proxy it usually answers with a captcha |
| Brave Search | HTML results of `search.brave.com`, an index of its own; without a proxy it usually answers 429 |

Google News, Bing News and Yahoo are the defaults of a new search; DuckDuckGo and Brave join them once a proxy serves them. Google's web results are left out on purpose (google.com serves none without JavaScript and turns cloud addresses away, and its Programmable Search API takes no new customers), and so are Bing's (unrelated to the query when a program asks; Yahoo shows the Bing index).

Engines do not like several requests at once from one address, so the requests to one engine wait in a queue with a pause after each other, while different engines are asked at the same time. A failed connection, a timeout, HTTP 429 or 5xx is tried again twice. An engine may change its page or refuse an AWS address at any time; the digest and the history then name the engine that failed, and the results of the others still go out. Results found by several engines come once, with all of them; addresses that differ only by `www.`, the scheme, a `#fragment`, `utm_*` parameters or a closing `/` count as one.

### Proxies

DuckDuckGo and Brave Search answer programs with a captcha or HTTP 429, and not only from AWS. An administrator lists proxies in Settings → Proxy, one per line: `socks5h://user:password@host:port`, `user:password@host:port`, `host:port:user:password` or `host:port` (`socks5h` resolves site names on the proxy, `socks5` here; `socks4`, `http` and `https` work too). The engines checked there are asked directly first (unless turned off), then through the proxies in the order of the list, each when the one before fails, whatever the error. A warm function remembers the channel that worked and starts with it; one that failed rests for 10 minutes. A search tries at most 3 channels, a preview 2. Passwords never leave the function. A proxy is an outside service; an address in an AWS data centre is no better than Lambda's own, so it takes a residential or mobile one.

### AI digest

A search with the AI digest on sends its new results (up to 60, with titles, links and snippets) to a model of Amazon Bedrock through the Converse API. The model is one variable, `ai_model` — the inference profile `eu.amazon.nova-2-lite-v1:0` by default, which keeps the requests in EU Regions; Converse is the same for every model of Bedrock. The results are passed as untrusted data, not as instructions. The month's spend is counted in DynamoDB from the tokens of every answer, at `ai_price_per_mtok`, and the digest stops at `ai_monthly_limit_usd` until the next month; Settings → AI shows it. A third-party model from AWS Marketplace also needs `ai_marketplace_product_id`.

## Test environment and production

The defaults deploy the **test environment**, the public demo above. It is the same code with mail turned off:

- no domain: the page answers at its `*.cloudfront.net` address with CloudFront's certificate, and no DNS is touched;
- no sender address: nothing is made in SES, and a run does everything but send — its status is "collected, no mail" and its results and AI digest are in the history;
- the sign-in is published and filled in (`demo = true`), password change and two-factor authentication are hidden, and the administration is read-only;
- the hourly reset (`reset_schedule`) rebuilds everything from `seed/seed.json`, but keeps the month's AI spend, so the limit holds;
- point-in-time recovery is off, and the page bucket is deleted with its content on `terraform destroy`.

**Production** turns mail on with a sender address. Start from [`examples/production.tfvars`](examples/production.tfvars):

| Variable | Test environment (default) | Production |
| --- | --- | --- |
| `name` | `search-serverless` | `search` — the prefix of every resource |
| `demo` | `true` | `false` |
| `domain_name`, `route53_zone_name` | `""` — the CloudFront address | `search.example.com`, `example.com` |
| `sender_address` | `""` — no mail | `search@example.com`, on a domain verified in SES |
| `admin_email` | `demo@example.com` | your address |
| `admin_password` | `Demo-Password-1` | `""` — Cognito mails a temporary password |
| `reset_schedule` | `rate(1 hour)` | `""` — no reset, no examples |
| `max_searches_per_user` | `10` | `20` |
| `ai_monthly_limit_usd` | `0.2` | as you like |

Steps to production:

1. Pick a fresh `name` (and state key): production is a separate deployment, not a change of the demo.
2. Verify the domain of `sender_address` in SES in the same region (DKIM; a custom MAIL FROM helps); the plan stops while it is not verified. While the SES account is in the sandbox, digests reach only verified addresses: the page shows it and verifies an address on request. Request production access in the SES console to lift it.
3. With `route53_zone_name` the stack validates the certificate and writes the alias records. Without it, the first apply waits for the certificate: add the records of the output `certificate_validation_records` at your DNS host while it waits, then a CNAME from `domain_name` to the output `cloudfront_url`.
4. Sign in with the temporary password Cognito mails to `admin_email`. Invite the others in Settings → Users.

## Deploy

### What you need

1. An S3 bucket for Terraform state (the S3 backend with the native lockfile, Terraform 1.10+).
2. The GitHub OIDC provider in the AWS account and a role for this repository that trusts only jobs of the GitHub environment `demo`. Its permissions: everything named with the `name` prefix (`s3:*` on `arn:aws:s3:::search-serverless-*`, the table, functions and Lambda layers, roles with `iam:PassRole`, log groups, SSM parameters, EventBridge rules), API Gateway, Cognito and CloudFront (their ids are generated), log delivery for API access logs, and the state objects under the state key prefix. The demo needs no SES, ACM or Route 53; production adds them.
3. In the GitHub repository: the environment `demo`, and the variables `AWS_ROLE_ARN` (the role) and `TF_STATE_BUCKET` (the state bucket). There are no secrets.
4. Python 3 with pip (or the venv module) and access to PyPI where Terraform plans: `lambda/build_layer.py` fetches the wheels of `lambda/requirements.txt` (`tzdata` for the time zones, `PySocks` for SOCKS proxies) for Linux arm64 into `.build/layer`.
5. Amazon Bedrock in the account: Amazon's own models such as Nova need no subscription.

### GitHub Actions

[`.github/workflows/terraform.yml`](.github/workflows/terraform.yml) only calls the shared workflow of [Tozapid/demo-ci-cd-skeleton](https://github.com/Tozapid/demo-ci-cd-skeleton):

- every push and pull request: `terraform fmt` and `terraform validate`; the Lambda tests; the page's syntax check;
- pull requests from this repository: `terraform plan` in the environment `demo`;
- a push to `main` or a manual run: plan and apply in the environment `demo`. The job stops if the plan would destroy or replace a bucket, the table or the user pool. A manual run from another branch deploys that branch over the same site until `main` is deployed again;
- after `main` is deployed: the GitHub release of the newest `CHANGELOG.md` entry, once.

The jobs get AWS credentials from GitHub's OIDC token: no AWS key is stored anywhere.

### Versions and releases

Every entry of [`CHANGELOG.md`](CHANGELOG.md) starts with its version, optionally followed by a date or a title (`## v1.1.0`, `## v1.1.0 — 2026-10-20`), with the changes under `### Added`, `### Changed`, `### Fixed` and `### Security`, newest first. The site takes its version from the newest entry when Terraform deploys it, and the release job makes it a tag and a GitHub release. Settings → Version reads the releases of `releases_repository` from the Atom feed of the GitHub releases and keeps them an hour.

### From a workstation

```bash
terraform init \
  -backend-config="bucket=<state bucket>" \
  -backend-config="key=search-serverless-aws/terraform.tfstate" \
  -backend-config="region=eu-central-1"
terraform apply                                   # the test environment
terraform apply -var-file=terraform.tfvars        # production, see above
```

## Access and security

- `/api/*` takes only a Cognito token (JWT authorizer); the Lambda also wants a verified email in it, and users cannot change their email (the client writes `locale` only).
- Each user sees and changes only their own searches. Administrators are the members of the Cognito group `admins`, read from the `cognito:groups` claim of the token.
- The page bucket is private, read only by the distribution; responses of `/api/*` are never cached.
- The page builds its DOM itself and its content security policy allows scripts only from itself and two pinned jsDelivr packages with subresource integrity.
- The Lambda may send mail only from `sender_address`, and call only the inference profile of `ai_model` and its model.
- Proxy passwords stay in the table and never go back to the page.

## Tests

```bash
cd lambda && python3 -m unittest -v search_test reset_test
```

The tests use no network: the engines, DynamoDB, SES, Lambda, Cognito and Bedrock are faked. A request through SOCKS5 with a password is checked against a local server when PySocks is installed (`pip install -r lambda/requirements.txt`). The reset is tested end to end through the API, and every example of `seed/seed.json` must pass the checks of the page.

## Cost

| Service | Test environment (demo) | Production, light use* |
| --- | --- | --- |
| Lambda, EventBridge, API Gateway, Cognito, CloudFront | free allowances | free allowances |
| DynamoDB on demand, S3, CloudWatch Logs | a few cents | a few cents |
| SES | — | $0.10 per 1,000 messages |
| Bedrock (Nova 2 Lite), a digest ≈ 4,000 input and 500 output tokens ≈ $0.003 | at most `ai_monthly_limit_usd` ($0.20) | ≈ $0.08 per search with a daily digest |
| **Total** | **≈ $0.25 a month** | **under $1 a month** |

\* A handful of users, a few daily searches with the AI digest. A WAF web ACL is left out on purpose: it alone would cost about $5–6 a month. Prices change; check the AWS pricing pages for your region.

## Repository

| Path | What it is |
| --- | --- |
| `lambda.tf`, `iam.tf`, `lambda/search.py` | The function of the API, the tick and the runs, its permissions and code |
| `lambda/engines.py` | Reading the search engines, the queue and the proxies |
| `lambda/build_layer.py`, `lambda/requirements.txt` | The layer with `tzdata` and `PySocks`, built while planning |
| `apigateway.tf`, `cognito.tf` | The API and the users |
| `dynamodb.tf`, `s3.tf` | The table and the page bucket |
| `cloudfront.tf`, `dns.tf`, `web.tf`, `web/` | The distribution, the domain, the page and its translations (`i18n.js`) |
| `ses.tf` | The SES configuration set of the digests (mail only) |
| `reset.tf`, `lambda/reset.py`, `seed/seed.json` | The hourly reset of the demo and its examples |
| `examples/production.tfvars` | A starting point for production |

## Limitations

- Engines can change their pages or refuse cloud addresses at any time; there is no official API behind any of them.
- Times a stopped function missed are not caught up, and a failed run is not retried, so that no digest goes out twice.
- Two-factor authentication is TOTP only; a lost authenticator is reset by an administrator or in the Cognito console.

## License

[MIT](LICENSE)
