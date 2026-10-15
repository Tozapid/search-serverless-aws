import json
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import engines
import search as app

OWNER = "me@example.org"


# Fakes ----------------------------------------------------------------------

def matches(condition, item):
    """Evaluates the boto3 condition objects the function builds."""
    expression = condition.get_expression()
    operator = expression["operator"]
    values = expression["values"]
    if operator == "AND":
        return matches(values[0], item) and matches(values[1], item)
    name = values[0].name
    if name not in item:
        return False
    if operator == "=":
        return item[name] == values[1]
    if operator == "<=":
        return item[name] <= values[1]
    if operator == "begins_with":
        return str(item[name]).startswith(values[1])
    raise NotImplementedError(operator)


def stored(value):
    """What DynamoDB gives back: every number as a Decimal."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: stored(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [stored(item) for item in value]
    return value


class ConditionFailed(Exception):
    def __init__(self):
        super().__init__("condition")
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": stored(item)} if item else {}

    def check(self, key, condition, values):
        item = self.items.get(key)
        if condition is None:
            return
        if condition == "attribute_exists(pk)":
            ok = item is not None
        elif condition == "attribute_not_exists(pk)":
            ok = item is None
        elif condition == "next_run = :old":
            ok = item is not None and item.get("next_run") == values[":old"]
        elif condition == "attribute_exists(pk) AND (attribute_not_exists(collecting) OR collecting < :stale)":
            ok = item is not None and item.get("collecting", "") < values[":stale"]
        else:
            raise NotImplementedError(condition)
        if not ok:
            raise ConditionFailed()

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["pk"], Item["sk"])
        self.check(key, ConditionExpression, {})
        self.items[key] = stored(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None):
        key = (Key["pk"], Key["sk"])
        self.check(key, ConditionExpression, ExpressionAttributeValues)
        item = self.items.setdefault(key, dict(Key))
        for action, rest in re.findall(r"(SET|ADD|REMOVE) (.+?)(?= SET | ADD | REMOVE |$)", UpdateExpression):
            for part in rest.split(","):
                if action == "SET":
                    name, value = [side.strip() for side in part.split("=")]
                    item[name] = stored(ExpressionAttributeValues[value])
                elif action == "REMOVE":
                    item.pop(part.strip(), None)
                else:
                    name, value = part.split()
                    item[name] = stored(item.get(name, 0) + ExpressionAttributeValues[value])

    def delete_item(self, Key):
        self.items.pop((Key["pk"], Key["sk"]), None)

    def query(self, KeyConditionExpression, IndexName=None, ScanIndexForward=True, Limit=None,
              ProjectionExpression=None, ExclusiveStartKey=None):
        found = [stored(item) for item in self.items.values() if matches(KeyConditionExpression, item)]
        sort = "next_run" if IndexName == "due" else "sk"
        found.sort(key=lambda item: item[sort], reverse=not ScanIndexForward)
        return {"Items": found[:Limit] if Limit else found}

    def scan(self, FilterExpression=None, ExclusiveStartKey=None):
        found = [stored(item) for item in self.items.values() if FilterExpression is None or matches(FilterExpression, item)]
        return {"Items": found}

    def batch_writer(self):
        table = self

        class Writer:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def delete_item(self, Key):
                table.delete_item(Key)

        return Writer()


class FakeSes:
    def __init__(self, verified=(OWNER,), production=False):
        self.sent = []
        self.verified = set(verified)
        self.production = production

    def send_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"MessageId": "1"}

    def get_email_identity(self, EmailIdentity):
        if EmailIdentity in self.verified:
            return {"VerifiedForSendingStatus": True}
        error = Exception("missing")
        error.response = {"Error": {"Code": "NotFoundException"}}
        raise error

    def get_account(self):
        return {"ProductionAccessEnabled": self.production}


class FakeCognito:
    """A user pool: users by email, and the members of the group admins."""

    def __init__(self, users=(OWNER,), admins=(OWNER,)):
        self.users = {email: {"enabled": True, "mfa": False, "status": "CONFIRMED", "password": None} for email in users}
        self.admins = set(admins)

    def user(self, email):
        if email not in self.users:
            error = Exception("missing")
            error.response = {"Error": {"Code": "UserNotFoundException"}}
            raise error
        return self.users[email]

    def get_paginator(self, name):
        cognito = self

        class Paginator:
            def paginate(self, UserPoolId, GroupName=None):
                emails = sorted(cognito.admins if name == "list_users_in_group" else cognito.users)
                yield {"Users": [{
                    "Username": email,
                    "Attributes": [{"Name": "email", "Value": email}],
                    "UserStatus": cognito.users.get(email, {}).get("status", ""),
                    "Enabled": cognito.users.get(email, {}).get("enabled", True),
                    "UserCreateDate": datetime(2026, 10, 14, tzinfo=timezone.utc),
                } for email in emails]}

        return Paginator()

    def admin_get_user(self, UserPoolId, Username):
        return {"UserMFASettingList": ["SOFTWARE_TOKEN_MFA"] if self.user(Username)["mfa"] else []}

    def admin_create_user(self, UserPoolId, Username, UserAttributes, TemporaryPassword, DesiredDeliveryMediums):
        if Username in self.users:
            error = Exception("exists")
            error.response = {"Error": {"Code": "UsernameExistsException"}}
            raise error
        self.users[Username] = {"enabled": True, "mfa": False, "status": "FORCE_CHANGE_PASSWORD", "password": TemporaryPassword}

    def admin_add_user_to_group(self, UserPoolId, Username, GroupName):
        self.user(Username)
        self.admins.add(Username)

    def admin_remove_user_from_group(self, UserPoolId, Username, GroupName):
        self.admins.discard(Username)

    def admin_enable_user(self, UserPoolId, Username):
        self.user(Username)["enabled"] = True

    def admin_disable_user(self, UserPoolId, Username):
        self.user(Username)["enabled"] = False

    def admin_set_user_mfa_preference(self, UserPoolId, Username, SoftwareTokenMfaSettings):
        self.user(Username)["mfa"] = SoftwareTokenMfaSettings["Enabled"]

    def admin_set_user_password(self, UserPoolId, Username, Password, Permanent):
        self.user(Username)["password"] = Password

    def admin_delete_user(self, UserPoolId, Username):
        self.user(Username)
        del self.users[Username]
        self.admins.discard(Username)


class FakeLambda:
    def __init__(self):
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(json.loads(kwargs["Payload"]))


class FakeBedrock:
    """The Converse API of bedrock-runtime."""

    def __init__(self, text="Коротко о главном [1, 2].", stop="end_turn", error=None):
        self.requests = []
        self.text = text
        self.stop = stop
        self.error = error

    def converse(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": self.text}]}},
            "stopReason": self.stop,
            "usage": {"inputTokens": 2000, "outputTokens": 300, "totalTokens": 2300},
        }


def client_error(code, message):
    error = Exception(message)
    error.response = {"Error": {"Code": code, "Message": message}}
    return error


RESULTS = {
    "google_news": [
        {"title": "First", "url": "https://news.example/1?utm_source=x", "snippet": "", "published": "2026-10-14T06:00:00Z", "source": "News"},
        {"title": "Second", "url": "https://news.example/2", "snippet": "", "published": "", "source": "News"},
    ],
    "yahoo": [
        {"title": "First again", "url": "https://www.news.example/1/", "snippet": "about the first", "published": "", "source": ""},
        {"title": "Third", "url": "https://other.example/3", "snippet": "third", "published": "", "source": ""},
    ],
}


def fake_search(engine, query, limit, market, period, retries=0, channels=(None,), tries=3):
    if engine == "brave":
        raise engines.EngineError("Brave Search asked for a captcha")
    return [dict(item) for item in RESULTS.get(engine, [])][:limit]


def event(method, path, body=None, email=OWNER, admin=False):
    path, _, query = path.partition("?")
    claims = {"email": email, "email_verified": "true"}
    if admin:
        # API Gateway hands the list claim over as a string.
        claims["cognito:groups"] = "[admins]"
    return {
        "rawPath": path,
        "queryStringParameters": dict(part.split("=", 1) for part in query.split("&")) if query else None,
        "requestContext": {
            "http": {"method": method},
            "authorizer": {"jwt": {"claims": claims}},
        },
        "body": json.dumps(body) if body is not None else None,
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.table = FakeTable()
        self.ses = FakeSes()
        self.lambda_ = FakeLambda()
        self.env = mock.patch.dict("os.environ", {
            "TABLE_NAME": "t",
            "SENDER": "search@example.com",
            "SITE_URL": "https://search.example.com",
            "AI_ENABLED": "true",
            "AI_MODEL": "eu.amazon.nova-2-lite-v1:0",
            "AI_REGION": "eu-central-1",
            "AI_PRICE_INPUT": "0.33",
            "AI_PRICE_OUTPUT": "2.75",
            "AI_MONTHLY_LIMIT": "0.5",
            "MAX_SEARCHES": "3",
            "USER_POOL_ID": "pool",
            "CONFIGURATION_SET": "search-demo",
            "VERSION": "v1.0.0",
            "RELEASES_REPOSITORY": "Tozapid/search-serverless-aws",
            "ADMIN_GROUP": "admins",
            "AWS_LAMBDA_FUNCTION_NAME": "search-demo",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cognito = FakeCognito(users=(OWNER, "admin@example.org", "user@example.org"), admins=("admin@example.org",))
        for name, value in (("_table", self.table), ("_ses", self.ses), ("_ses_v1", mock.Mock()), ("_lambda", self.lambda_), ("_cognito", self.cognito)):
            patcher = mock.patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        app._proxy = None
        patcher = mock.patch.object(engines, "search", side_effect=fake_search)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, method, path, body=None, email=OWNER, admin=False):
        result = app.handler(event(method, path, body, email=email, admin=admin), None)
        return result["statusCode"], json.loads(result["body"])

    def create(self, **fields):
        body = {"query": "serverless", "engines": ["google_news", "yahoo", "brave"], **fields}
        status, data = self.call("POST", "/api/searches", body)
        self.assertEqual(status, 201, data)
        return data


# Schedule -------------------------------------------------------------------

class ScheduleTest(unittest.TestCase):
    def test_every_hours_on_divisible_local_hours(self):
        after = datetime(2026, 10, 14, 7, 30, tzinfo=timezone.utc)  # 10:30 in Moscow
        found = app.next_run({"type": "hours", "every": 6}, "Europe/Moscow", after)
        self.assertEqual(app.iso(found), "2026-10-14T09:00:00Z")  # 12:00 in Moscow

    def test_every_hour(self):
        after = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
        found = app.next_run({"type": "hours", "every": 1}, "UTC", after)
        self.assertEqual(app.iso(found), "2026-10-14T08:00:00Z")

    def test_weekdays_at_hour(self):
        # Wednesday 14 October 2026, 9:00 in Berlin; Mondays and Fridays at 8.
        after = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
        found = app.next_run({"type": "days", "days": [0, 4], "hour": 8}, "Europe/Berlin", after)
        self.assertEqual(app.iso(found), "2026-10-16T06:00:00Z")

    def test_daily_later_today(self):
        after = datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc)
        found = app.next_run({"type": "days", "days": list(range(7)), "hour": 8}, "Europe/Berlin", after)
        self.assertEqual(app.iso(found), "2026-10-14T06:00:00Z")

    def test_schedule_from_dynamodb(self):
        # DynamoDB gives numbers back as Decimal.
        after = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
        days = {"type": "days", "days": [Decimal(0), Decimal(4)], "hour": Decimal(8)}
        self.assertEqual(app.iso(app.next_run(days, "Europe/Berlin", after)), "2026-10-16T06:00:00Z")
        hours = {"type": "hours", "every": Decimal(6)}
        self.assertEqual(app.iso(app.next_run(hours, "UTC", after)), "2026-10-14T12:00:00Z")

    def test_daylight_saving_change(self):
        # Berlin moves from CEST to CET on 25 October 2026.
        after = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)
        found = app.next_run({"type": "days", "days": list(range(7)), "hour": 8}, "Europe/Berlin", after)
        self.assertEqual(app.iso(found), "2026-10-25T07:00:00Z")


# Engines --------------------------------------------------------------------

class EnginesTest(unittest.TestCase):
    def setUp(self):
        # Queues and retries sleep; the tests count the pauses instead.
        self.sleep = mock.patch.object(engines.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)
        engines._gates.clear()

    def test_reset_connection_is_tried_again(self):
        page = """<div class="snippet" data-type="web"><a href="https://example.net/"><div class="title">Title</div></a></div>"""
        answers = [engines.EngineError("no answer ([Errno 104] Connection reset by peer)", transient=True), (200, page)]
        with mock.patch.object(engines, "fetch", side_effect=answers) as fetch:
            found = engines.search("brave", "q", 10, "en-US")
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(found[0]["url"], "https://example.net/")
        self.assertIn(mock.call(engines.BACKOFF[0]), self.sleep.call_args_list)

    def test_retries_end(self):
        error = engines.EngineError("no answer (timed out)", transient=True)
        with mock.patch.object(engines, "fetch", side_effect=error) as fetch:
            with self.assertRaises(engines.EngineError):
                engines.search("bing_news", "q", 10, "en-US", retries=2)
        self.assertEqual(fetch.call_count, 3)
        with mock.patch.object(engines, "fetch", side_effect=error) as fetch:
            with self.assertRaises(engines.EngineError):
                engines.search("bing_news", "q", 10, "en-US", retries=0)
        self.assertEqual(fetch.call_count, 1)

    def test_captcha_is_not_tried_again(self):
        with mock.patch.object(engines, "fetch", return_value=(200, "<title>Captcha</title>")) as fetch:
            with self.assertRaises(engines.EngineError):
                engines.search("brave", "q", 10, "en-US")
        self.assertEqual(fetch.call_count, 1)

    def test_one_engine_waits_between_requests(self):
        feed = "<rss><channel></channel></rss>"
        with mock.patch.object(engines, "fetch", return_value=(200, feed)):
            engines.search("bing_news", "a", 10, "en-US")
            engines.search("bing_news", "b", 10, "en-US")
        waits = [call.args[0] for call in self.sleep.call_args_list]
        self.assertEqual(len(waits), 1)
        self.assertGreater(waits[0], 0)
        self.assertLessEqual(waits[0], engines.DEFAULT_GAP)

    def test_http_errors(self):
        for code, transient in ((429, True), (503, True), (403, False)):
            error = engines.urllib.error.HTTPError("https://x", code, "x", {}, None)
            opener = mock.Mock()
            opener.open.side_effect = error
            with mock.patch.object(engines.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(engines.EngineError) as caught:
                    engines.fetch("https://x")
            self.assertEqual(caught.exception.transient, transient, code)

    def test_normal_url_ignores_small_differences(self):
        self.assertEqual(
            engines.normal_url("https://www.Example.com/a/?utm_source=x&b=2#top"),
            engines.normal_url("http://example.com/a?b=2"),
        )

    def test_merge_keeps_rank_order_and_all_engines(self):
        merged = engines.merge(RESULTS)
        self.assertEqual([item["title"] for item in merged], ["First", "Second", "Third"])
        self.assertEqual(merged[0]["engines"], ["google_news", "yahoo"])
        self.assertEqual(merged[0]["snippet"], "about the first")

    def test_yahoo_page(self):
        page = """<html><body><div class="dd algo algo-sr"><div class="compTitle"><h3 class="title">
        <a href="https://r.search.yahoo.com/_ylt=x/RV=2/RE=1/RO=10/RU=https%3a%2f%2fexample.org%2fpage/RK=2/RS=y"
        aria-label="Example page">example.org › page Example page</a></h3></div>
        <div class="compText"><p>Some <b>words</b> about it</p></div></div>
        <div class="dd algo"><h3><a href="https://plain.example/"><span>plain.example</span>Plain title</a></h3></div>
        </body></html>""" + " " * 20_000
        with mock.patch.object(engines, "fetch", return_value=(200, page)):
            found = engines.search("yahoo", "q", 10, "en-US")
        self.assertEqual(found[0], {"title": "Example page", "url": "https://example.org/page",
                                    "snippet": "Some words about it", "published": "", "source": ""})
        self.assertEqual(found[1]["title"], "Plain title")

    def test_brave_page(self):
        page = """<div class="snippet svelte-x" data-pos="0" data-type="ad"><a href="https://ad.example">Ad</a></div>
        <div class="snippet svelte-jmfu5f" data-pos="1" data-type="web"><div class="result-content">
        <a href="https://example.net/x" class="l1"><div class="site-name-wrapper"><img src="https://imgs.example/i"/>
        <div class="site-name-content"><div class="desktop-small-semibold t-secondary">Example Site</div>
        <cite class="snippet-url">example.net <span>› x</span></cite></div></div>
        <div class="title search-snippet-title" title="Example &amp; co">Example &amp; co</div></a>
        <div class="generic-snippet"><div class="content">Snippet <strong>text</strong></div></div></div></div>"""
        with mock.patch.object(engines, "fetch", return_value=(200, page)) as fetch:
            found = engines.search("brave", "q", 10, "ru-RU", "week")
        self.assertIn("tf=pw", fetch.call_args[0][0])
        self.assertEqual(found, [{"title": "Example & co", "url": "https://example.net/x", "snippet": "Snippet text",
                                  "published": "", "source": "Example Site"}])

    def test_brave_captcha(self):
        with mock.patch.object(engines, "fetch", return_value=(200, "<html><title>Captcha</title></html>")):
            with self.assertRaises(engines.EngineError):
                engines.search("brave", "q", 10, "en-US")

    def test_google_news_rss(self):
        feed = """<?xml version="1.0"?><rss><channel><item><title>Headline - The Paper</title>
        <link>https://news.google.com/rss/articles/abc</link><pubDate>Wed, 14 Oct 2026 06:00:00 GMT</pubDate>
        <description>&lt;a href="x"&gt;Headline&lt;/a&gt;</description><source url="https://paper.example">The Paper</source>
        </item></channel></rss>"""
        with mock.patch.object(engines, "fetch", return_value=(200, feed)) as fetch:
            found = engines.search("google_news", "q", 10, "ru-RU", "day")
        self.assertIn("when%3A1d", fetch.call_args[0][0])
        self.assertIn("ceid=RU%3Aru", fetch.call_args[0][0])
        self.assertEqual(found, [{"title": "Headline", "url": "https://news.google.com/rss/articles/abc", "snippet": "",
                                  "published": "2026-10-14T06:00:00Z", "source": "The Paper"}])

    def test_bing_news_unwraps_click_links(self):
        feed = """<rss><channel><item><title>T</title>
        <link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fpaper.example%2fstory&amp;c=1</link>
        <description>D</description></item></channel></rss>"""
        with mock.patch.object(engines, "fetch", return_value=(200, feed)):
            found = engines.search("bing_news", "q", 10, "en-US")
        self.assertEqual(found[0]["url"], "https://paper.example/story")

    def test_not_rss(self):
        with mock.patch.object(engines, "fetch", return_value=(200, "<html>blocked</html>")):
            with self.assertRaises(engines.EngineError):
                engines.search("bing_news", "q", 10, "en-US")


# API ------------------------------------------------------------------------

class ApiTest(Base):
    def test_me(self):
        status, data = self.call("GET", "/api/me")
        self.assertEqual(status, 200)
        self.assertEqual(data["email"], OWNER)
        self.assertEqual([engine["id"] for engine in data["engines"]], list(engines.ENGINES))
        self.assertTrue(data["ai"]["enabled"])

    def test_create_with_defaults_and_list(self):
        created = self.create()
        self.assertRegex(created["id"], r"^[a-f0-9]{12}$")
        self.assertEqual(created["recipient"], OWNER)
        self.assertTrue(created["active"])
        self.assertTrue(created["next_run"])
        status, data = self.call("GET", "/api/searches")
        self.assertEqual([item["id"] for item in data["items"]], [created["id"]])
        self.assertEqual(data["destinations"], {OWNER: "verified"})
        self.assertFalse(data["production"])

    def test_searches_are_private(self):
        created = self.create()
        result = app.handler(event("GET", f"/api/searches/{created['id']}", email="other@example.org"), None)
        self.assertEqual(result["statusCode"], 404)

    def test_validation(self):
        cases = [
            ({"query": ""}, "bad_query"),
            ({"query": "a\nb"}, "bad_query"),
            ({"query": "q", "engines": []}, "bad_engines"),
            ({"query": "q", "engines": ["altavista"]}, "bad_engines"),
            ({"query": "q", "market": "xx-XX"}, "bad_market"),
            ({"query": "q", "max_results": 100}, "bad_max_results"),
            ({"query": "q", "schedule": {"type": "hours", "every": 5}}, "bad_schedule"),
            ({"query": "q", "schedule": {"type": "days", "days": [], "hour": 8}}, "bad_schedule"),
            ({"query": "q", "schedule": {"type": "days", "days": [1], "hour": 24}}, "bad_schedule"),
            ({"query": "q", "timezone": "Mars/Base"}, "bad_timezone"),
            ({"query": "q", "recipient": "nope"}, "bad_recipient"),
            ({"query": "q", "ai": "yes"}, "bad_flag"),
            ({"query": "q", "language": "de"}, "bad_language"),
            ({"query": "q", "language": "ES"}, "bad_language"),
        ]
        for body, code in cases:
            with self.subTest(body=body):
                status, data = self.call("POST", "/api/searches", body)
                self.assertEqual((status, data["code"]), (400, code))

    def test_limit_of_searches(self):
        for _ in range(3):
            self.create()
        status, data = self.call("POST", "/api/searches", {"query": "q"})
        self.assertEqual((status, data["code"]), (409, "too_many_searches"))

    def test_turning_off_leaves_the_due_index(self):
        created = self.create()
        status, data = self.call("PATCH", f"/api/searches/{created['id']}", {"active": False})
        self.assertEqual(status, 200)
        self.assertIsNone(data["next_run"])
        stored = self.table.items[("USER#" + OWNER, "SEARCH#" + created["id"])]
        self.assertNotIn("due", stored)
        status, data = self.call("PATCH", f"/api/searches/{created['id']}", {"active": True, "schedule": {"type": "hours", "every": 2}})
        self.assertEqual(data["schedule"], {"type": "hours", "every": 2})
        self.assertEqual(self.table.items[("USER#" + OWNER, "SEARCH#" + created["id"])]["due"], "1")

    def test_run_now_starts_an_invocation(self):
        created = self.create()
        status, _ = self.call("POST", f"/api/searches/{created['id']}/run")
        self.assertEqual(status, 202)
        self.assertEqual(self.lambda_.calls, [{"action": "run", "owner": OWNER, "id": created["id"], "trigger": "manual"}])

    def test_preview_sends_nothing(self):
        created = self.create()
        status, data = self.call("POST", f"/api/searches/{created['id']}/preview")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["results"]), 3)
        self.assertIn("brave", data["errors"])
        self.assertEqual(self.ses.sent, [])

    def test_delete_removes_runs_and_seen(self):
        created = self.create()
        app.run_search(OWNER, created["id"], "manual")
        self.assertTrue(any(sk.startswith("RUN#") for _, sk in self.table.items))
        status, _ = self.call("DELETE", f"/api/searches/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(self.table.items, {})


# Runs -----------------------------------------------------------------------

class RunTest(Base):
    def test_first_run_sends_everything_then_only_new(self):
        created = self.create(language="en")
        result = app.run_search(OWNER, created["id"], "schedule")
        self.assertEqual(result, {"status": "ok", "new": 3, "sent": True})
        mail = self.ses.sent[0]
        self.assertEqual(mail["Destination"], {"ToAddresses": [OWNER]})
        # The set without tracking keeps the links direct.
        self.assertEqual(mail["ConfigurationSetName"], "search-demo")
        self.assertEqual(mail["Content"]["Simple"]["Subject"]["Data"], "serverless: 3 new")
        text = mail["Content"]["Simple"]["Body"]["Text"]["Data"]
        self.assertIn("[1] First", text)
        self.assertIn("Did not answer: Brave Search", text)

        # Nothing new the second time: skip_empty keeps the mail back.
        result = app.run_search(OWNER, created["id"], "schedule")
        self.assertEqual(result, {"status": "empty", "new": 0, "sent": False})
        self.assertEqual(len(self.ses.sent), 1)

        status, data = self.call("GET", f"/api/searches/{created['id']}/runs")
        self.assertEqual([run["status"] for run in data["items"]], ["empty", "ok"])
        self.assertEqual(data["items"][1]["results"][0]["engines"], ["google_news", "yahoo"])

        # Forgetting what was sent brings everything back.
        self.call("DELETE", f"/api/searches/{created['id']}/seen")
        self.assertEqual(app.run_search(OWNER, created["id"], "manual")["new"], 3)

    def test_mail_in_french_and_italian(self):
        for language, subject in (("fr", "serverless : 3 nouveaux"), ("it", "serverless: 3 nuovi")):
            with self.subTest(language=language):
                created = self.create(language=language)
                app.run_search(OWNER, created["id"], "manual")
                mail = self.ses.sent[-1]
                self.assertEqual(mail["Content"]["Simple"]["Subject"]["Data"], subject)

    def test_inactive_search_does_not_run_on_schedule(self):
        created = self.create(active=False)
        self.assertEqual(app.run_search(OWNER, created["id"], "schedule"), {"skipped": "inactive"})
        self.assertEqual(app.run_search(OWNER, created["id"], "manual")["sent"], True)

    def bedrock(self, **kwargs):
        fake = FakeBedrock(**kwargs)
        patcher = mock.patch.object(app, "_bedrock", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def test_ai_digest_and_its_cost(self):
        created = self.create(ai=True, ai_instructions="only prices", language="ru")
        fake = self.bedrock()
        app.run_search(OWNER, created["id"], "schedule")
        request = fake.requests[0]
        self.assertEqual(request["modelId"], "eu.amazon.nova-2-lite-v1:0")
        self.assertIn("Russian", request["system"][0]["text"])
        prompt = request["messages"][0]["content"][0]["text"]
        self.assertIn("only prices", prompt)
        self.assertIn("[3] Third", prompt)
        markup = self.ses.sent[0]["Content"]["Simple"]["Body"]["Html"]["Data"]
        self.assertIn('[<a href="https://news.example/1?utm_source=x"', markup)
        usage = self.table.items[("USAGE", app.now_utc().strftime("%Y-%m"))]
        self.assertEqual(usage["cost"], round(2000 * 0.33 + 300 * 2.75))
        self.assertEqual(usage["digests"], 1)
        runs = self.call("GET", f"/api/searches/{created['id']}/runs")[1]["items"]
        self.assertEqual(runs[0]["ai"], "ok")
        self.assertEqual(runs[0]["summary"], "Коротко о главном [1, 2].")

    def test_ai_stops_at_the_monthly_limit(self):
        created = self.create(ai=True)
        self.table.items[("USAGE", app.now_utc().strftime("%Y-%m"))] = {"pk": "USAGE", "sk": app.now_utc().strftime("%Y-%m"), "cost": 600_000}
        fake = self.bedrock()
        app.run_search(OWNER, created["id"], "schedule")
        self.assertEqual(fake.requests, [])
        self.assertEqual(len(self.ses.sent), 1)
        runs = self.call("GET", f"/api/searches/{created['id']}/runs")[1]["items"]
        self.assertEqual(runs[0]["ai"], "limit")

    def test_bedrock_error_is_kept_in_the_history(self):
        created = self.create(ai=True)
        self.bedrock(error=client_error("AccessDeniedException", "You don't have access to the model with the specified model ID."))
        app.run_search(OWNER, created["id"], "schedule")
        self.assertEqual(len(self.ses.sent), 1)
        run = self.call("GET", f"/api/searches/{created['id']}/runs")[1]["items"][0]
        self.assertEqual(run["ai"], "error")
        self.assertEqual(run["ai_error"], "AccessDeniedException: You don't have access to the model with the specified model ID.")

    def test_unexpected_ai_failure_still_sends(self):
        created = self.create(ai=True)
        self.bedrock(error=RuntimeError("no region"))
        app.run_search(OWNER, created["id"], "schedule")
        self.assertEqual(len(self.ses.sent), 1)
        run = self.call("GET", f"/api/searches/{created['id']}/runs")[1]["items"][0]
        self.assertEqual(run["ai_error"], "RuntimeError: no region")

    def test_filtered_answer_sends_without_digest(self):
        created = self.create(ai=True)
        self.bedrock(stop="content_filtered")
        app.run_search(OWNER, created["id"], "schedule")
        self.assertNotIn("Коротко", self.ses.sent[0]["Content"]["Simple"]["Body"]["Text"]["Data"])
        run = self.call("GET", f"/api/searches/{created['id']}/runs")[1]["items"][0]
        self.assertEqual(run["ai"], "refused")

    def test_rejected_mail_keeps_results_new(self):
        created = self.create(recipient="stranger@example.com")
        self.ses.send_email = mock.Mock(side_effect=Exception("Email address is not verified"))
        result = app.run_search(OWNER, created["id"], "schedule")
        self.assertEqual(result["sent"], False)
        self.assertNotIn(("USER#" + OWNER, "SEEN#" + created["id"]), self.table.items)
        stored = self.table.items[("USER#" + OWNER, "SEARCH#" + created["id"])]
        self.assertEqual(stored["last_status"], "not_sent")
        self.assertIn("not verified", stored["last_error"])


class RetiredEngineTest(Base):
    def test_a_search_with_an_unknown_engine_skips_it(self):
        created = self.create(engines=["google_news"])
        key = ("USER#" + OWNER, "SEARCH#" + created["id"])
        self.table.items[key]["engines"] = ["altavista", "google_news"]
        status, data = self.call("GET", f"/api/searches/{created['id']}")
        self.assertEqual(data["engines"], ["google_news"])
        with mock.patch.object(engines, "search", side_effect=fake_search) as search:
            app.run_search(OWNER, created["id"], "manual")
        self.assertEqual([call.args[0] for call in search.call_args_list], ["google_news"])


class ProxyTest(Base):
    def admin(self, method, path, body=None):
        return self.call(method, path, body, email="admin@example.org", admin=True)

    def test_only_administrators(self):
        for method, path in (("GET", "/api/admin/proxy"), ("PUT", "/api/admin/proxy"), ("POST", "/api/admin/proxy/test")):
            status, data = self.call(method, path, {})
            self.assertEqual((status, data["code"]), (403, "admin_only"))

    def test_list_keeps_the_passwords_hidden(self):
        status, data = self.admin("GET", "/api/admin/proxy")
        self.assertEqual((data["enabled"], data["direct_first"], data["engines"], data["proxies"]),
                         (False, True, ["duckduckgo", "brave"], []))
        body = {"enabled": True, "engines": ["duckduckgo"], "scheme": "http", "proxies": [
            {"url": "socks5h://me:s3cret%3A%2F%40@one.example.net:1080"},
            {"url": "two.example.net:3128:user:pa:ss"},
            {"url": "three.example.net:8080"},
            {"url": "socks5h://me:s3cret%3A%2F%40@one.example.net:1080"},
        ]}
        status, data = self.admin("PUT", "/api/admin/proxy", body)
        self.assertEqual(status, 200)
        self.assertNotIn("s3cret", json.dumps(data))
        self.assertEqual([proxy["url"] for proxy in data["proxies"]], [
            "socks5h://me:****@one.example.net:1080", "http://user:****@two.example.net:3128", "http://three.example.net:8080"])
        self.assertEqual([proxy["has_password"] for proxy in data["proxies"]], [True, True, False])
        self.assertEqual(data["updated_by"], "admin@example.org")
        stored = app.plain(self.table.items[("SETTINGS", "proxy")])
        self.assertEqual(engines.proxy_url(stored["proxies"][0]), "socks5h://me:s3cret%3A%2F%40@one.example.net:1080")
        self.assertEqual(stored["proxies"][1]["password"], "pa:ss")
        # A new order of the stored ones keeps their passwords; one left out is gone.
        ids = [proxy["id"] for proxy in data["proxies"]]
        status, data = self.admin("PUT", "/api/admin/proxy", {"proxies": [{"id": ids[1]}, {"id": ids[0]}]})
        self.assertEqual([proxy["id"] for proxy in data["proxies"]], [ids[1], ids[0]])
        stored = app.plain(self.table.items[("SETTINGS", "proxy")])
        self.assertEqual([proxy["password"] for proxy in stored["proxies"]], ["pa:ss", "s3cret:/@"])

    def test_first_version_becomes_a_list(self):
        self.table.items[("SETTINGS", "proxy")] = {
            "pk": "SETTINGS", "sk": "proxy", "enabled": True, "scheme": "socks5", "host": "old.example.net",
            "port": Decimal(1080), "username": "me", "password": "pw", "engines": ["duckduckgo"]}
        data = self.admin("GET", "/api/admin/proxy")[1]
        self.assertEqual([proxy["url"] for proxy in data["proxies"]], ["socks5://me:****@old.example.net:1080"])
        self.assertEqual(app.channels_for("duckduckgo", app.stored_proxy())[1]["password"], "pw")

    def test_validation(self):
        for body in ({"proxies": [{"url": "ftp://h:1"}]}, {"proxies": [{"url": "h:0"}]}, {"proxies": [{"url": "h"}]},
                     {"proxies": [{"url": "bad host:1080"}]}, {"proxies": [{"id": "nope"}]}, {"proxies": "h:1"},
                     {"scheme": "ftp", "proxies": []}, {"engines": ["altavista"]}, {"direct_first": "yes"},
                     {"proxies": [{"url": f"h{number}.example.net:1080"} for number in range(51)]}):
            with self.subTest(body=body):
                status, data = self.admin("PUT", "/api/admin/proxy", body)
                self.assertEqual(status, 400)
        status, data = self.admin("PUT", "/api/admin/proxy", {"proxies": [{"url": "ok.example.net:1"}, {"url": "bad host:1:me:secret"}]})
        self.assertEqual((data["code"], data["detail"]), ("bad_proxy", "bad host:1:me:****"))

    def test_runs_go_direct_then_through_the_list(self):
        created = self.create(engines=["google_news", "duckduckgo"])
        self.admin("PUT", "/api/admin/proxy", {"enabled": True, "engines": ["duckduckgo"],
                                               "proxies": [{"url": "one.example.net:1080"}, {"url": "two.example.net:1080"}]})
        with mock.patch.object(engines, "search", side_effect=fake_search) as search:
            app.run_search(OWNER, created["id"], "manual")
        channels = {call.args[0]: call.kwargs["channels"] for call in search.call_args_list}
        self.assertEqual(channels["google_news"], [None])
        self.assertEqual([proxy and proxy["host"] for proxy in channels["duckduckgo"]], [None, "one.example.net", "two.example.net"])
        # The page offers the proxied engines for a new search.
        me = self.call("GET", "/api/me")[1]
        self.assertEqual(me["default_engines"], ["google_news", "bing_news", "yahoo", "duckduckgo"])
        self.assertEqual(me["proxied"], ["duckduckgo"])
        # Without the direct channel only the proxies are asked.
        self.admin("PUT", "/api/admin/proxy", {"direct_first": False})
        with mock.patch.object(engines, "search", side_effect=fake_search) as search:
            app.run_search(OWNER, created["id"], "manual")
        channels = {call.args[0]: call.kwargs["channels"] for call in search.call_args_list}
        self.assertEqual([proxy["host"] for proxy in channels["duckduckgo"]], ["one.example.net", "two.example.net"])

    def test_turned_off_goes_direct(self):
        created = self.create(engines=["duckduckgo"])
        self.admin("PUT", "/api/admin/proxy", {"enabled": False, "proxies": [{"url": "one.example.net:1080"}]})
        with mock.patch.object(engines, "search", side_effect=fake_search) as search:
            app.run_search(OWNER, created["id"], "manual")
        self.assertEqual(search.call_args.kwargs["channels"], [None])
        self.assertEqual(self.call("GET", "/api/me")[1]["default_engines"], ["google_news", "bing_news", "yahoo"])

    def test_check(self):
        data = self.admin("PUT", "/api/admin/proxy", {"proxies": [{"url": "one.example.net:1080:me:pw"}]})[1]
        with mock.patch.object(engines, "check_ip", side_effect=lambda proxy: "203.0.113.9" if proxy else "3.120.0.1"), \
                mock.patch.object(engines, "search", return_value=[{}, {}]) as search:
            status, found = self.admin("POST", "/api/admin/proxy/test", {"id": data["proxies"][0]["id"]})
            self.assertEqual(status, 200)
            self.assertEqual((found["direct_ip"], found["proxy_ip"], found["found"]), ("3.120.0.1", "203.0.113.9", 2))
            self.assertEqual(search.call_args.kwargs["channels"][0]["password"], "pw")
            # A line not saved yet; the check does not save it.
            status, found = self.admin("POST", "/api/admin/proxy/test", {"url": "two.example.net:1090", "scheme": "http"})
            self.assertEqual(search.call_args.kwargs["channels"][0]["port"], 1090)
        self.assertEqual(len(self.table.items[("SETTINGS", "proxy")]["proxies"]), 1)


class NoMailTest(Base):
    """The demo: no mail, and the administration only to look at."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict("os.environ", {"MAIL_ENABLED": "false", "DEMO": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_run_sends_nothing_but_keeps_its_history(self):
        created = self.create(only_new=True)
        app.run_search(OWNER, created["id"], "manual")
        self.assertEqual(self.ses.sent, [])
        runs = self.call("GET", f"/api/searches/{created['id']}/runs")[1]["items"]
        self.assertEqual((runs[0]["status"], runs[0]["sent"]), ("no_mail", False))
        self.assertTrue(runs[0]["results"])
        # What it found counts as seen: the next run has nothing new.
        app.run_search(OWNER, created["id"], "manual")
        runs = self.call("GET", f"/api/searches/{created['id']}/runs")[1]["items"]
        self.assertEqual(runs[0]["new"], 0)

    def test_combined_digest_sends_nothing(self):
        self.create()
        self.call("PUT", "/api/digest", {"combined": True})
        result = app.run_digest(OWNER, "manual")
        self.assertEqual((result["status"], result["sent"]), ("no_mail", False))
        self.assertEqual(self.ses.sent, [])

    def test_no_addresses_to_verify(self):
        self.create()
        data = self.call("GET", "/api/searches")[1]
        self.assertEqual((data["destinations"], data["production"]), ({}, True))
        status, data = self.call("POST", "/api/destinations", {"address": "a@example.org"})
        self.assertEqual((status, data["code"]), (400, "mail_disabled"))
        me = self.call("GET", "/api/me")[1]
        self.assertEqual((me["mail"], me["demo"]), (False, True))

    def test_administration_is_read_only(self):
        status, _ = self.call("GET", "/api/admin/users", email="admin@example.org", admin=True)
        self.assertEqual(status, 200)
        for method, path, body in (("POST", "/api/admin/users", {"email": "x@example.org"}),
                                   ("DELETE", "/api/admin/users?email=user@example.org", None),
                                   ("PUT", "/api/admin/proxy", {"enabled": True}),
                                   ("POST", "/api/admin/proxy/test", {"url": "h.example.net:1080"})):
            with self.subTest(path=path, method=method):
                status, data = self.call(method, path, body, email="admin@example.org", admin=True)
                self.assertEqual((status, data["code"]), (403, "demo_readonly"))


class ChannelTest(unittest.TestCase):
    """The ways to an engine: direct and the proxies, in turn."""

    def setUp(self):
        self.sleep = mock.patch.object(engines.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)
        engines._gates.clear()
        engines.forget_channels()
        self.one = {"scheme": "socks5h", "host": "one.example.net", "port": 1080, "username": "", "password": ""}
        self.two = {"scheme": "http", "host": "two.example.net", "port": 3128, "username": "", "password": ""}
        self.page = """<div class="snippet" data-type="web"><a href="https://example.net/"><div class="title">Title</div></a></div>"""

    def answers(self, by_channel):
        """fetch() as each channel answers: a page or an error."""
        used = []

        def fetch(url, data=None, language="en"):
            proxy = engines._context.proxy
            name = engines.channel_name(proxy)
            used.append(name)
            answer = by_channel[name]
            if isinstance(answer, Exception):
                raise answer
            return 200, answer
        return used, fetch

    def test_next_channel_after_an_error(self):
        used, fetch = self.answers({"direct": engines.EngineError("HTTP 429", transient=True),
                                    "one.example.net:1080": "<title>Captcha</title>", "two.example.net:3128": self.page})
        with mock.patch.object(engines, "fetch", side_effect=fetch):
            found = engines.search("brave", "q", 10, "en-US", channels=[None, self.one, self.two])
            self.assertEqual(found[0]["url"], "https://example.net/")
            # A transient error on a channel with others left is not retried.
            self.assertEqual(used, ["direct", "one.example.net:1080", "two.example.net:3128"])
            # The next search starts with the channel that worked.
            used.clear()
            engines.search("brave", "q", 10, "en-US", channels=[None, self.one, self.two])
            self.assertEqual(used, ["two.example.net:3128"])

    def test_failed_channels_rest(self):
        used, fetch = self.answers({"direct": engines.EngineError("HTTP 429"), "one.example.net:1080": engines.EngineError("no answer")})
        with mock.patch.object(engines, "fetch", side_effect=fetch):
            with self.assertRaises(engines.EngineError) as caught:
                engines.search("brave", "q", 10, "en-US", channels=[None, self.one])
            self.assertEqual(str(caught.exception), "direct: HTTP 429; one.example.net:1080: no answer")
            used.clear()
            with self.assertRaises(engines.EngineError) as caught:
                engines.search("brave", "q", 10, "en-US", channels=[None, self.one])
            self.assertEqual(used, [])
            self.assertIn("all 2 channels failed", str(caught.exception))
        # After FAILED_SECONDS they are asked again.
        with mock.patch.object(engines.time, "monotonic", return_value=engines.time.monotonic() + engines.FAILED_SECONDS + 1), \
                mock.patch.object(engines, "fetch", side_effect=fetch):
            with self.assertRaises(engines.EngineError):
                engines.search("brave", "q", 10, "en-US", channels=[None, self.one])
        self.assertEqual(used, ["direct", "one.example.net:1080"])

    def test_tries_limit_the_channels_of_one_search(self):
        used, fetch = self.answers({"direct": engines.EngineError("HTTP 429"), "one.example.net:1080": engines.EngineError("no answer"),
                                    "two.example.net:3128": self.page})
        with mock.patch.object(engines, "fetch", side_effect=fetch):
            with self.assertRaises(engines.EngineError):
                engines.search("brave", "q", 10, "en-US", channels=[None, self.one, self.two], tries=2)
            # The next one skips the two that failed and reaches the third.
            found = engines.search("brave", "q", 10, "en-US", channels=[None, self.one, self.two], tries=2)
        self.assertEqual(used, ["direct", "one.example.net:1080", "two.example.net:3128"])
        self.assertEqual(len(found), 1)

    def test_one_channel_is_always_asked(self):
        used, fetch = self.answers({"direct": engines.EngineError("HTTP 429")})
        with mock.patch.object(engines, "fetch", side_effect=fetch):
            for _ in range(2):
                with self.assertRaises(engines.EngineError):
                    engines.search("brave", "q", 10, "en-US", retries=0)
        self.assertEqual(used, ["direct", "direct"])

    def test_lines_of_a_list(self):
        cases = {
            "socks5h://me:p%40ss@h.example.net:1080": ("socks5h", "h.example.net", 1080, "me", "p@ss"),
            "me:pw@h.example.net:8080": ("http", "h.example.net", 8080, "me", "pw"),
            "h.example.net:3128:me:pa:ss": ("http", "h.example.net", 3128, "me", "pa:ss"),
            " HTTPS://h.example.net:443/ ": ("https", "h.example.net", 443, "", ""),
            "10.0.0.1:1080": ("http", "10.0.0.1", 1080, "", ""),
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                proxy = engines.parse_proxy(line, "http")
                self.assertEqual(tuple(proxy[key] for key in ("scheme", "host", "port", "username", "password")), expected)
        for line in ("h.example.net", "ftp://h:21", "h:port", ""):
            with self.subTest(line=line):
                self.assertIsNone(engines.parse_proxy(line, "http"))


try:
    import socks  # noqa: F401  (PySocks, as in the layer)
except ImportError:
    socks = None


@unittest.skipUnless(socks, "PySocks is not installed")
class SocksTest(unittest.TestCase):
    """A request really goes through a small SOCKS5 server with a password."""

    def setUp(self):
        import http.server
        import socket
        import socketserver
        import struct
        import threading

        class Page(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = f"hello {self.headers['Host']}".encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        web_port = self.web.server_address[1]
        seen = self.seen = []

        class Socks(socketserver.BaseRequestHandler):
            def handle(self):
                conn = self.request
                version, count = conn.recv(2)
                methods = conn.recv(count)
                conn.sendall(b"\x05\x02" if 2 in methods else b"\x05\xff")
                _, length = conn.recv(2)
                user = conn.recv(length).decode()
                (length,) = conn.recv(1)
                password = conn.recv(length).decode()
                ok = (user, password) == ("me", "pa:ss")
                conn.sendall(b"\x01\x00" if ok else b"\x01\x01")
                if not ok:
                    return
                _, command, _, kind = conn.recv(4)
                if kind == 3:
                    (length,) = conn.recv(1)
                    host = conn.recv(length).decode()
                else:
                    host = socket.inet_ntoa(conn.recv(4))
                (port,) = struct.unpack("!H", conn.recv(2))
                seen.append((host, port))
                upstream = socket.create_connection(("127.0.0.1", web_port))
                conn.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack("!H", port))
                request = conn.recv(65536)
                upstream.sendall(request)
                upstream.shutdown(socket.SHUT_WR)
                while True:
                    chunk = upstream.recv(65536)
                    if not chunk:
                        break
                    conn.sendall(chunk)
                upstream.close()

        self.socks = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Socks)
        threading.Thread(target=self.socks.serve_forever, daemon=True).start()
        self.web_port = web_port
        self.addCleanup(self.web.shutdown)
        self.addCleanup(self.socks.shutdown)

    def test_fetch_through_socks5h(self):
        proxy = {"scheme": "socks5h", "host": "127.0.0.1", "port": self.socks.server_address[1], "username": "me", "password": "pa:ss"}
        engines._context.proxy = proxy
        try:
            status, text = engines.fetch(f"http://search.test:{self.web_port}/")
        finally:
            engines._context.proxy = None
        self.assertEqual((status, text), (200, f"hello search.test:{self.web_port}"))
        # socks5h: the proxy got the name, not an address resolved here.
        self.assertEqual(self.seen, [("search.test", self.web_port)])

    def test_wrong_password(self):
        proxy = {"scheme": "socks5", "host": "127.0.0.1", "port": self.socks.server_address[1], "username": "me", "password": "no"}
        engines._context.proxy = proxy
        try:
            with self.assertRaises(engines.EngineError) as caught:
                engines.fetch(f"http://127.0.0.1:{self.web_port}/")
        finally:
            engines._context.proxy = None
        self.assertIn("via the proxy", str(caught.exception))


class DigestTest(Base):
    def due_keys(self):
        return sorted(sk for (pk, sk), item in self.table.items.items() if item.get("due") == "1")

    def test_turning_the_digest_on_and_off_moves_the_schedule(self):
        first = self.create(name="First")
        second = self.create(name="Second", active=False)
        self.assertEqual(self.due_keys(), ["SEARCH#" + first["id"]])

        status, data = self.call("PUT", "/api/digest", {"combined": True, "schedule": {"type": "days", "days": [0], "hour": 9}, "timezone": "UTC"})
        self.assertEqual(status, 200)
        self.assertTrue(data["combined"])
        self.assertTrue(data["next_run"])
        self.assertEqual(data["recipient"], OWNER)
        self.assertEqual(self.due_keys(), ["DIGEST"])
        # A search created or changed meanwhile stays out of the schedule.
        third = self.create(name="Third")
        self.call("PATCH", f"/api/searches/{second['id']}", {"active": True})
        self.assertEqual(self.due_keys(), ["DIGEST"])

        status, data = self.call("PUT", "/api/digest", {"combined": False})
        self.assertFalse(data["combined"])
        self.assertIsNone(data["next_run"])
        self.assertEqual(self.due_keys(), sorted("SEARCH#" + item["id"] for item in (first, second, third)))

    def test_the_list_carries_the_digest(self):
        self.create()
        status, data = self.call("GET", "/api/searches")
        self.assertFalse(data["digest"]["combined"])
        self.assertEqual(data["digest"]["schedule"]["hour"], 8)

    def test_the_digest_waits_for_all_its_searches(self):
        first = self.create(name="First")
        second = self.create(name="Second", engines=["yahoo"])
        self.call("PUT", "/api/digest", {"combined": True})
        start = app.now_utc()
        when = app.iso(start + timedelta(hours=1))
        self.table.items[("USER#" + OWNER, "DIGEST")].update(next_run=when, collect_after=app.iso(start))
        for _ in range(2):
            self.assertEqual(app.handler({"action": "tick"}, None)["started"], 1)
            self.assertEqual(app.handler(self.lambda_.calls[-1], None)["status"], "prepared")
        self.assertEqual({call["id"] for call in self.lambda_.calls}, {first["id"], second["id"]})
        self.assertEqual({call["for"] for call in self.lambda_.calls}, {when})
        # Nothing is sent before its time.
        self.assertEqual(self.ses.sent, [])
        with mock.patch.object(app, "now_utc", return_value=start + timedelta(minutes=61)):
            self.assertEqual(app.handler({"action": "tick"}, None)["sent"], 1)
        self.assertEqual(len(self.ses.sent), 1)
        text = self.ses.sent[0]["Content"]["Simple"]["Body"]["Text"]["Data"]
        self.assertIn("First — new: 3", text)
        self.assertIn("Second — new: 2", text)
        runs = self.call("GET", f"/api/searches/{second['id']}/runs")[1]["items"]
        self.assertEqual((runs[0]["trigger"], runs[0]["sent"]), ("digest", True))
        self.assertIn("collected", runs[0])

    def test_one_message_for_all_searches(self):
        first = self.create(name="First", ai=True)
        second = self.create(name="Second", engines=["yahoo"])
        self.create(name="Off", active=False)
        self.call("PUT", "/api/digest", {"combined": True, "recipient": "box@example.org", "language": "en"})
        self.ses.verified.add("box@example.org")
        fake = FakeBedrock()
        with mock.patch.object(app, "_bedrock", fake):
            result = app.handler({"action": "digest", "owner": OWNER, "trigger": "schedule"}, None)
        self.assertEqual(result, {"status": "ok", "new": 5, "sent": True, "searches": 2})
        self.assertEqual(len(self.ses.sent), 1)
        mail = self.ses.sent[0]
        self.assertEqual(mail["Destination"], {"ToAddresses": ["box@example.org"]})
        self.assertEqual(mail["Content"]["Simple"]["Subject"]["Data"], "Search digest: 5 new")
        text = mail["Content"]["Simple"]["Body"]["Text"]["Data"]
        self.assertIn("First — new: 3", text)
        self.assertIn("Second — new: 2", text)
        self.assertNotIn("Off", text)
        self.assertIn("Коротко о главном", text)
        # Only the search that wants it gets an AI digest.
        self.assertEqual(len(fake.requests), 1)

        runs = self.call("GET", f"/api/searches/{first['id']}/runs")[1]["items"]
        self.assertEqual((runs[0]["trigger"], runs[0]["sent"], runs[0]["ai"]), ("digest", True, "ok"))
        digest = self.call("GET", "/api/digest")[1]
        self.assertEqual((digest["last_status"], digest["last_count"]), ("ok", 5))

        # Nothing new the next time: no mail, both searches say so.
        result = app.run_digest(OWNER, "schedule")
        self.assertEqual(result["status"], "empty")
        self.assertEqual(len(self.ses.sent), 1)
        runs = self.call("GET", f"/api/searches/{second['id']}/runs")[1]["items"]
        self.assertEqual(runs[0]["status"], "empty")

    def test_digest_off_does_not_run_on_schedule(self):
        self.create()
        self.assertEqual(app.run_digest(OWNER, "schedule"), {"skipped": "inactive"})
        # By hand it goes out anyway.
        status, _ = self.call("POST", "/api/digest/run")
        self.assertEqual(status, 202)
        self.assertEqual(self.lambda_.calls[-1], {"action": "digest", "owner": OWNER, "trigger": "manual"})
        self.assertTrue(app.run_digest(OWNER, "manual")["sent"])

    def test_single_run_uses_the_digest_address(self):
        created = self.create(recipient="old@example.org")
        self.call("PUT", "/api/digest", {"combined": True})
        self.assertEqual(app.run_search(OWNER, created["id"], "schedule"), {"skipped": "inactive"})
        app.run_search(OWNER, created["id"], "manual")
        self.assertEqual(self.ses.sent[0]["Destination"], {"ToAddresses": [OWNER]})

    def test_digest_validation_and_admin(self):
        status, data = self.call("PUT", "/api/digest", {"schedule": {"type": "hours", "every": 5}})
        self.assertEqual((status, data["code"]), (400, "bad_schedule"))
        status, data = self.call("PUT", "/api/digest", {})
        self.assertEqual((status, data["code"]), (400, "nothing_to_change"))
        status, data = self.call("PUT", f"/api/digest?owner={OWNER}", {"combined": True}, email="user@example.org")
        self.assertEqual((status, data["code"]), (403, "admin_only"))
        status, data = self.call("PUT", f"/api/digest?owner={OWNER}", {"combined": True}, email="admin@example.org", admin=True)
        self.assertEqual((status, data["owner"], data["combined"]), (200, OWNER, True))
        status, data = self.call("GET", f"/api/admin/searches?owner={OWNER}", email="admin@example.org", admin=True)
        self.assertTrue(data["digest"]["combined"])


FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><updated>2026-11-02T10:00:00Z</updated>
    <link rel="alternate" type="text/html" href="https://github.com/Tozapid/search-serverless-aws/releases/tag/v1.1.0"/>
    <title>v1.1.0</title><content type="html">&lt;ul&gt;&lt;li&gt;Monthly schedule&lt;/li&gt;&lt;/ul&gt;</content></entry>
  <entry><updated>2026-10-14T10:00:00Z</updated>
    <link rel="alternate" type="text/html" href="https://github.com/Tozapid/search-serverless-aws/releases/tag/v1.0.0"/>
    <title>v1.0.0</title><content type="html">First</content></entry>
</feed>"""


class VersionTest(Base):
    def setUp(self):
        super().setUp()
        app._releases = None

    def test_newer_releases(self):
        answer = mock.MagicMock()
        answer.__enter__.return_value.read.return_value = FEED
        with mock.patch.object(app.urllib.request, "urlopen", return_value=answer) as urlopen:
            status, data = self.call("GET", "/api/version")
        self.assertEqual(status, 200)
        self.assertEqual(urlopen.call_args[0][0].full_url, "https://github.com/Tozapid/search-serverless-aws/releases.atom")
        self.assertEqual(data["version"], "v1.0.0")
        self.assertEqual(data["latest"], "v1.1.0")
        self.assertEqual([release["version"] for release in data["newer"]], ["v1.1.0"])
        self.assertEqual(data["newer"][0]["notes"], "• Monthly schedule")

    def test_github_down(self):
        with mock.patch.object(app.urllib.request, "urlopen", side_effect=OSError("down")):
            status, data = self.call("GET", "/api/version")
        self.assertEqual((status, data["error"]), (200, "unavailable"))


ADMIN = "admin@example.org"


class AdminTest(Base):
    def admin(self, method, path, body=None):
        return self.call(method, path, body, email=ADMIN, admin=True)

    def test_me_tells_who_is_admin(self):
        self.assertFalse(self.call("GET", "/api/me")[1]["admin"])
        self.assertTrue(self.admin("GET", "/api/me")[1]["admin"])

    def test_users_cannot_reach_admin_routes_or_others_searches(self):
        created = self.create()
        for method, path in (("GET", "/api/admin/searches"), ("GET", "/api/admin/users")):
            status, data = self.call(method, path, email="user@example.org")
            self.assertEqual((status, data["code"]), (403, "admin_only"))
        status, data = self.call("GET", f"/api/searches/{created['id']}?owner={OWNER}", email="user@example.org")
        self.assertEqual((status, data["code"]), (403, "admin_only"))

    def test_admin_sees_and_manages_every_search(self):
        created = self.create()
        self.call("POST", "/api/searches", {"query": "other"}, email="user@example.org")
        status, data = self.admin("GET", "/api/admin/searches")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(item["owner"] for item in data["items"]), [OWNER, "user@example.org"])
        status, data = self.admin("GET", f"/api/admin/searches?owner={OWNER}")
        self.assertEqual([item["id"] for item in data["items"]], [created["id"]])

        path = f"/api/searches/{created['id']}?owner={OWNER}"
        status, data = self.admin("PATCH", path, {"active": False, "recipient": ""})
        self.assertEqual(status, 200)
        self.assertFalse(data["active"])
        # An empty address falls back to the owner, not to the administrator.
        self.assertEqual(data["recipient"], OWNER)
        status, data = self.admin("GET", f"/api/searches/{created['id']}/runs?owner={OWNER}")
        self.assertEqual((status, data["items"]), (200, []))
        status, _ = self.admin("POST", f"/api/searches/{created['id']}/run?owner={OWNER}")
        self.assertEqual(status, 202)
        self.assertEqual(self.lambda_.calls[-1]["owner"], OWNER)
        # Without ?owner= the administrator's own searches are meant.
        status, _ = self.admin("GET", f"/api/searches/{created['id']}")
        self.assertEqual(status, 404)

    def test_list_users(self):
        self.create()
        status, data = self.admin("GET", "/api/admin/users")
        self.assertEqual(status, 200)
        users = {user["email"]: user for user in data["items"]}
        self.assertEqual(users[OWNER]["searches"], 1)
        self.assertTrue(users[ADMIN]["admin"])
        self.assertTrue(users[ADMIN]["self"])
        self.assertFalse(users[OWNER]["admin"])

    def test_invite_change_and_delete_a_user(self):
        status, data = self.admin("POST", "/api/admin/users", {"email": "New@Example.org", "admin": True})
        self.assertEqual(status, 201)
        self.assertEqual(data["email"], "new@example.org")
        self.assertRegex(data["temporary_password"], r"^\S{16}$")
        self.assertIn("new@example.org", self.cognito.admins)
        status, data = self.admin("POST", "/api/admin/users", {"email": "new@example.org"})
        self.assertEqual((status, data["code"]), (409, "user_exists"))

        status, data = self.admin("PUT", "/api/admin/users", {"email": "new@example.org", "admin": False, "enabled": False, "reset_password": True})
        self.assertEqual(status, 200)
        self.assertNotIn("new@example.org", self.cognito.admins)
        self.assertFalse(self.cognito.users["new@example.org"]["enabled"])
        self.assertEqual(self.cognito.users["new@example.org"]["password"], data["temporary_password"])

        self.cognito.users[OWNER]["mfa"] = True
        self.admin("PUT", "/api/admin/users", {"email": OWNER, "reset_mfa": True})
        self.assertFalse(self.cognito.users[OWNER]["mfa"])

    def test_admin_cannot_lock_themselves_out(self):
        for body in ({"email": ADMIN, "enabled": False}, {"email": ADMIN, "admin": False}):
            status, data = self.admin("PUT", "/api/admin/users", body)
            self.assertEqual((status, data["code"]), (400, "not_yourself"))
        status, data = self.admin("DELETE", f"/api/admin/users?email={ADMIN}")
        self.assertEqual((status, data["code"]), (400, "not_yourself"))

    def test_deleting_a_user_deletes_their_searches(self):
        created = self.create()
        app.run_search(OWNER, created["id"], "manual")
        other = self.call("POST", "/api/searches", {"query": "other"}, email="user@example.org")[1]
        status, data = self.admin("DELETE", f"/api/admin/users?email={OWNER}")
        self.assertEqual(status, 200)
        self.assertNotIn(OWNER, self.cognito.users)
        self.assertFalse([key for key in self.table.items if key[0] == "USER#" + OWNER])
        self.assertIn(("USER#user@example.org", "SEARCH#" + other["id"]), self.table.items)

    def test_unknown_user(self):
        status, data = self.admin("PUT", "/api/admin/users", {"email": "ghost@example.org", "enabled": True})
        self.assertEqual((status, data["code"]), (404, "user_not_found"))


class TickTest(Base):
    def due(self, search, when, opens=None):
        """The search's mail at `when`, its collection open from `opens`."""
        self.table.items[("USER#" + OWNER, "SEARCH#" + search["id"])].update(
            next_run=app.iso(when), collect_after=app.iso(opens or when - timedelta(hours=3)))

    def item(self, search):
        return self.table.items[("USER#" + OWNER, "SEARCH#" + search["id"])]

    def tick(self, moment=None):
        if moment is None:
            return app.handler({"action": "tick"}, None)
        with mock.patch.object(app, "now_utc", return_value=moment):
            return app.handler({"action": "tick"}, None)

    def test_one_collection_at_a_time_soonest_mail_first(self):
        start = app.now_utc()
        later = self.create(name="Later")
        sooner = self.create(name="Sooner")
        self.due(later, start + timedelta(hours=2))
        self.due(sooner, start + timedelta(hours=1))
        self.assertEqual(self.tick(), {"sent": 0, "started": 1, "queued": 1})
        self.assertEqual(self.lambda_.calls, [{"action": "collect", "owner": OWNER, "id": sooner["id"], "for": app.iso(start + timedelta(hours=1))}])
        # Nothing else starts while it runs.
        self.assertEqual(self.tick(), {"sent": 0, "started": 0, "queued": 1})
        self.assertEqual(app.handler(self.lambda_.calls[-1], None), {"status": "prepared", "new": 3})
        self.assertNotIn("collecting", self.item(sooner))
        # Collected, it waits for its time without a mail.
        self.assertEqual(self.ses.sent, [])
        self.assertEqual(self.tick(), {"sent": 0, "started": 1, "queued": 0})
        self.assertEqual(self.lambda_.calls[-1]["id"], later["id"])
        # A collection that never came back is taken for lost.
        self.assertEqual(self.tick(start + timedelta(minutes=16))["started"], 1)

    def test_the_queue_opens_ahead_of_the_mail(self):
        start = app.now_utc()
        search = self.create()
        self.due(search, start + timedelta(hours=4))
        self.assertEqual(self.tick(), {"sent": 0, "started": 0, "queued": 0})
        self.assertEqual(self.tick(start + timedelta(hours=1, minutes=1))["started"], 1)

    def test_the_mail_goes_at_its_time_from_what_was_collected(self):
        start = app.now_utc()
        search = self.create(ai=True)
        self.due(search, start + timedelta(hours=1))
        self.tick()
        fake = FakeBedrock()
        with mock.patch.object(app, "_bedrock", fake):
            app.handler(self.lambda_.calls[-1], None)
            self.assertEqual(len(fake.requests), 1)
            status, data = self.call("GET", f"/api/searches/{search['id']}")
            self.assertEqual(data["prepared_at"], app.iso(start))
            # At its time: the mail, without asking the engines or the model again.
            with mock.patch.object(engines, "search", side_effect=AssertionError("asked again")):
                self.assertEqual(self.tick(start + timedelta(hours=1, minutes=2)), {"sent": 1, "started": 0, "queued": 0})
            self.assertEqual(len(fake.requests), 1)
        self.assertEqual(len(self.ses.sent), 1)
        self.assertIn("Коротко о главном", self.ses.sent[0]["Content"]["Simple"]["Body"]["Text"]["Data"])
        runs = self.call("GET", f"/api/searches/{search['id']}/runs")[1]["items"]
        self.assertEqual((runs[0]["trigger"], runs[0]["sent"], runs[0]["new"], runs[0]["collected"]), ("schedule", True, 3, app.iso(start)))
        item = self.item(search)
        self.assertGreater(item["next_run"], app.iso(start + timedelta(hours=1)))
        self.assertNotIn("prepared", item)
        self.assertNotIn(("USER#" + OWNER, "REPORT#" + search["id"]), self.table.items)
        # The next collection opens no earlier than now.
        self.assertGreaterEqual(item["collect_after"], app.iso(start + timedelta(hours=1, minutes=2)))
        self.assertEqual(len(self.table.items[("USER#" + OWNER, "SEEN#" + search["id"])]["keys"]), 3)
        # Once only.
        self.assertEqual(self.tick(start + timedelta(hours=1, minutes=3))["sent"], 0)

    def test_what_went_out_since_is_left_out(self):
        start = app.now_utc()
        search = self.create(ai=True)
        self.due(search, start + timedelta(hours=1))
        self.tick()
        with mock.patch.object(app, "_bedrock", FakeBedrock()):
            app.handler(self.lambda_.calls[-1], None)
        self.table.items[("USER#" + OWNER, "SEEN#" + search["id"])] = {
            "pk": "USER#" + OWNER, "sk": "SEEN#" + search["id"], "keys": [engines.url_key("https://other.example/3")]}
        self.tick(start + timedelta(hours=1, minutes=1))
        text = self.ses.sent[0]["Content"]["Simple"]["Body"]["Text"]["Data"]
        self.assertNotIn("Third", text)
        # The AI digest numbered the results it was written for.
        self.assertNotIn("Коротко о главном", text)

    def test_run_now_sends_the_collected_back_to_the_queue(self):
        start = app.now_utc()
        search = self.create()
        self.due(search, start + timedelta(hours=1))
        self.tick()
        app.handler(self.lambda_.calls[-1], None)
        app.run_search(OWNER, search["id"], "manual")
        self.assertNotIn("prepared", self.item(search))
        self.assertNotIn(("USER#" + OWNER, "REPORT#" + search["id"]), self.table.items)
        self.assertEqual(self.tick()["started"], 1)

    def test_a_change_of_the_query_collects_again(self):
        start = app.now_utc()
        search = self.create()
        self.due(search, start + timedelta(hours=1))
        self.tick()
        app.handler(self.lambda_.calls[-1], None)
        self.call("PATCH", f"/api/searches/{search['id']}", {"name": "Renamed"})
        self.assertIn("prepared", self.item(search))
        self.call("PATCH", f"/api/searches/{search['id']}", {"query": "other"})
        self.assertNotIn("prepared", self.item(search))
        self.assertEqual(self.tick()["started"], 1)

    def test_engines_that_all_fail_are_asked_again_later(self):
        start = app.now_utc()
        search = self.create(engines=["brave"])
        self.due(search, start + timedelta(hours=2))
        self.tick()
        self.assertEqual(app.handler(self.lambda_.calls[-1], None), {"status": "retry"})
        item = self.item(search)
        self.assertNotIn("prepared", item)
        self.assertNotIn("collecting", item)
        self.assertEqual(self.tick()["started"], 0)
        self.assertEqual(self.tick(start + timedelta(minutes=31))["started"], 1)
        # Too close to the mail: it goes with the errors.
        with mock.patch.object(app, "now_utc", return_value=start + timedelta(hours=1, minutes=45)):
            self.assertEqual(app.handler(self.lambda_.calls[-1], None)["status"], "prepared")

    def test_a_mail_not_collected_in_time_is_missed(self):
        start = app.now_utc()
        search = self.create()
        self.due(search, start - timedelta(hours=3))
        self.assertEqual(self.tick()["sent"], 1)
        self.assertEqual(self.ses.sent, [])
        runs = self.call("GET", f"/api/searches/{search['id']}/runs")[1]["items"]
        self.assertEqual(runs[0]["status"], "missed")
        self.assertGreater(self.item(search)["next_run"], app.iso(start))

    def test_an_overdue_mail_waits_for_its_collection(self):
        start = app.now_utc()
        search = self.create()
        self.due(search, start - timedelta(minutes=10))
        self.assertEqual(self.tick(), {"sent": 0, "started": 1, "queued": 0})
        app.handler(self.lambda_.calls[-1], None)
        self.assertEqual(self.tick()["sent"], 1)
        self.assertEqual(len(self.ses.sent), 1)

    def test_a_moved_mail_drops_its_collection(self):
        start = app.now_utc()
        search = self.create()
        self.due(search, start + timedelta(hours=1))
        self.tick()
        job = self.lambda_.calls[-1]
        self.due(search, start + timedelta(hours=2))
        self.assertEqual(app.handler(job, None), {"skipped": "moved"})
        self.assertNotIn("collecting", self.item(search))


if __name__ == "__main__":
    unittest.main()
