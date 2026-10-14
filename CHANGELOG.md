# Changelog

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
