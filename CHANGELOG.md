# Changelog

## v1.2.0

### Added

- CloudFront access logs, optional: with `access_logs_bucket` set, the standard logs (v2) of the distribution go to that bucket under `cloudfront/<name>/` as JSON lines, for an overview of the account such as [account-serverless-aws](https://github.com/Tozapid/account-serverless-aws) to read. The CI passes the repository variable `ACCESS_LOGS_BUCKET`; unset, there are none.

## v1.1.0

### Changed

- Results are collected ahead of the mail, and at the time of the schedule the mail only goes out. A search joins a queue `collect_lead_minutes` (three hours by default) before its mail; the tick, now every 5 minutes instead of 60 (`tick_minutes`), starts at most one collection and none while another runs, so the engines never hear from several searches at once. The collected results and their AI digest wait for their time in `REPORT#<id>`. When every engine fails, the search is collected again in 30 minutes while there is time before the mail. The combined digest collects each of its searches in the same queue. The page shows when the results of the next mail were collected, and the history when each run was.

## v1.0.0

### Added

- Scheduled searches: a query, the engines (Google News, Bing News, Yahoo, DuckDuckGo, Brave Search), the region and period of the results, a schedule of weekdays at an hour or every few hours in the search's time zone, the address and language of the digest. Only results the search has not sent before count; with nothing new there is no mail.
- AI digest above the results from a model of Amazon Bedrock (Amazon Nova 2 Lite by default), with the reader's wishes and a monthly spending limit (`ai_monthly_limit_usd`).
- Run now, a preview without mail, forgetting what was sent, and the history of runs for 90 days.
- A combined digest per user: every active search in one message on one schedule, a section per search with its own AI digest.
- Administrators (Cognito group `admins`): the searches of every user, and the accounts in Settings → Users — invite, new temporary password, reset of two-factor sign-in, disable, administrator role, delete.
- Proxies for engines that turn programs away (Settings → Proxy): a list of SOCKS and HTTP proxies, tried in turn when the direct request fails; the channel that worked is remembered and one that failed rests for 10 minutes.
- Settings → Version: the version of the site and the releases that came after it.
- Interface, mail and AI digest in English, Russian, French and Italian.
- The public demo: no mail (runs keep their results and AI digests in the history), the sign-in published and filled in, the administration read-only, and an hourly reset to the examples of `seed/seed.json`.
