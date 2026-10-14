"""Hourly reset of the public demo: back to a fresh deployment.

- Every item of the table goes: searches, what they have sent, their history,
  the combined digests and the proxy settings. The month's spend on the AI
  digest (pk = USAGE) stays, so that the limit holds across resets.
- The example searches of seed.json are written again with their history,
  times counted back from now, so the demo always looks recent; their next
  runs come on their schedules like any other search.
- The demo user gets the published password back, two-factor authentication
  off, the default language, the administrator role, and is signed out
  everywhere; any other user is deleted.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import search as app

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Next to the code in the function's zip; in the repository, in seed/.
SEED = next(path for path in (Path(__file__).with_name("seed.json"), Path(__file__).parent.parent / "seed" / "seed.json")
            if path.exists())
KEPT = ("USAGE",)

_ssm = None


def handler(event, context):
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    removed = wipe_table()
    owner = os.environ["DEMO_EMAIL"].lower()
    searches, runs = write_searches(owner, seed.get("searches", []), now)
    reset_users(owner)
    logger.info("reset: %s items removed, %s searches and %s runs written", removed, searches, runs)
    return {"removed": removed, "searches": searches, "runs": runs}


def wipe_table():
    keys = []
    kwargs = {"ProjectionExpression": "pk, sk"}
    while True:
        page = app.table().scan(**kwargs)
        keys.extend({"pk": item["pk"], "sk": item["sk"]} for item in page.get("Items", []) if item["pk"] not in KEPT)
        if not page.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    with app.table().batch_writer() as batch:
        for key in keys:
            batch.delete_item(Key=key)
    return len(keys)


# A seed search is what the page would have made, and its runs what the
# tick would have left behind; the results of the last run count as seen.
def write_searches(owner, searches, now):
    runs = 0
    for entry in searches:
        created = now - timedelta(minutes=entry.get("created_minutes_ago", 0))
        fields = {key: value for key, value in entry.items() if key in app.DEFAULTS or key in ("query", "recipient")}
        item = {
            "pk": "USER#" + owner,
            "sk": "SEARCH#" + entry["id"],
            **app.DEFAULTS,
            **fields,
            "created_at": app.iso(created),
            "updated_at": app.iso(created),
        }
        item.setdefault("recipient", owner)
        app.schedule_fields(item, now)
        app.table().put_item(Item=app.clean_item(item))
        seen = []
        for run in sorted(entry.get("runs", []), key=lambda run: -run.get("minutes_ago", 0)):
            moment = now - timedelta(minutes=run.get("minutes_ago", 0))
            results = run.get("results", [])
            record = {
                "at": app.iso(moment),
                "trigger": run.get("trigger", "schedule"),
                "found": run.get("found", len(results)),
                "new": len(results),
                "errors": run.get("errors", {}),
                "sent": False,
                "ai": "ok" if run.get("summary") else "",
                "summary": run.get("summary", ""),
                "status": run.get("status", "no_mail" if results else "empty"),
                "results": [{
                    **{key: result.get(key, "") for key in ("title", "url", "snippet", "source", "engines")},
                    "published": app.iso(moment - timedelta(hours=result.get("published_hours_ago", 0))),
                } for result in results],
            }
            app.save_run(item, record, moment)
            seen.extend(app.engines.url_key(result["url"]) for result in results)
            runs += 1
        if seen:
            app.save_seen(item, seen)
    return len(searches), runs


def reset_users(owner):
    pool = os.environ["USER_POOL_ID"]
    cognito = app.cognito()
    username = None
    for page in cognito.get_paginator("list_users").paginate(UserPoolId=pool):
        for user in page.get("Users", []):
            address = next((attribute["Value"] for attribute in user.get("Attributes", []) if attribute["Name"] == "email"), "")
            if address.lower() == owner and username is None:
                username = user["Username"]
            else:
                cognito.admin_delete_user(UserPoolId=pool, Username=user["Username"])
    if username is None:
        username = owner
        cognito.admin_create_user(
            UserPoolId=pool,
            Username=owner,
            MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": owner}, {"Name": "email_verified", "Value": "true"}],
        )
    password = ssm().get_parameter(Name=os.environ["DEMO_PASSWORD_PARAMETER"], WithDecryption=True)["Parameter"]["Value"]
    cognito.admin_set_user_password(UserPoolId=pool, Username=username, Password=password, Permanent=True)
    cognito.admin_enable_user(UserPoolId=pool, Username=username)
    cognito.admin_set_user_mfa_preference(
        UserPoolId=pool,
        Username=username,
        SoftwareTokenMfaSettings={"Enabled": False, "PreferredMfa": False},
    )
    cognito.admin_add_user_to_group(UserPoolId=pool, Username=username, GroupName=app.admin_group())
    try:
        cognito.admin_delete_user_attributes(UserPoolId=pool, Username=username, UserAttributeNames=["locale"])
    except Exception:
        logger.info("no language to clear")
    cognito.admin_user_global_sign_out(UserPoolId=pool, Username=username)


def ssm():
    global _ssm
    if _ssm is None:
        import boto3
        _ssm = boto3.client("ssm")
    return _ssm
