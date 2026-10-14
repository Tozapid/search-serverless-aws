import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import reset
import search as app
from search_test import OWNER, Base, FakeCognito, FakeTable

SEED = Path(__file__).resolve().parent.parent / "seed" / "seed.json"


class ScanTable(FakeTable):
    # The reset scans the whole table, two items a page to exercise paging.
    def scan(self, ProjectionExpression=None, ExclusiveStartKey=None, FilterExpression=None):
        keys = sorted(self.items)
        if ExclusiveStartKey:
            keys = [key for key in keys if key > (ExclusiveStartKey["pk"], ExclusiveStartKey["sk"])]
        page = [{"pk": pk, "sk": sk} for pk, sk in keys[:2]]
        return {"Items": page, **({"LastEvaluatedKey": page[-1]} if len(keys) > 2 else {})}


class ResetCognito(FakeCognito):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.signed_out = []
        self.locale_cleared = []

    def admin_create_user(self, UserPoolId, Username, UserAttributes, MessageAction):
        self.users[Username] = {"enabled": True, "mfa": False, "status": "CONFIRMED", "password": None}

    def admin_delete_user_attributes(self, UserPoolId, Username, UserAttributeNames):
        self.locale_cleared.append(Username)

    def admin_user_global_sign_out(self, UserPoolId, Username):
        self.signed_out.append(Username)


class FakeSsm:
    def get_parameter(self, Name, WithDecryption):
        return {"Parameter": {"Value": "Demo-Password-1"}}


class ResetTest(Base):
    def setUp(self):
        super().setUp()
        self.table = ScanTable()
        self.cognito = ResetCognito(users=(OWNER, "visitor@example.org"), admins=())
        self.cognito.users[OWNER].update({"mfa": True, "enabled": False, "password": "changed"})
        for name, value in (("_table", self.table), ("_cognito", self.cognito)):
            patcher = mock.patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(reset, "_ssm", FakeSsm())
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.dict("os.environ", {"DEMO_EMAIL": OWNER, "DEMO_PASSWORD_PARAMETER": "/p", "MAIL_ENABLED": "false", "DEMO": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_seed_is_valid(self):
        # Every example search passes the checks of the page.
        for entry in json.loads(SEED.read_text(encoding="utf-8"))["searches"]:
            with self.subTest(search=entry["name"]):
                fields = {key: value for key, value in entry.items() if key in app.DEFAULTS or key == "query"}
                app.clean_search({**fields, "recipient": OWNER}, OWNER, new=True)

    def test_back_to_the_examples(self):
        self.create(query="left by a visitor")
        self.call("PUT", "/api/digest", {"combined": True})
        self.table.put_item(Item={"pk": "USAGE", "sk": "2026-10", "cost": 1000})
        result = reset.handler({}, None)
        seed = json.loads(SEED.read_text(encoding="utf-8"))["searches"]
        self.assertEqual(result["searches"], len(seed))
        items = self.call("GET", "/api/searches")[1]["items"]
        self.assertEqual(sorted(item["name"] for item in items), sorted(entry["name"] for entry in seed))
        self.assertFalse(self.call("GET", "/api/digest")[1]["combined"])
        # The month's AI spend stays.
        self.assertIn(("USAGE", "2026-10"), self.table.items)
        # The history of an example, newest first, with times counted from now.
        first = next(item for item in items if item["id"] == "a1b2c3d4e5f6")
        runs = self.call("GET", "/api/searches/a1b2c3d4e5f6/runs")[1]["items"]
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["status"], "no_mail")
        self.assertTrue(runs[0]["summary"])
        at = datetime.strptime(runs[0]["at"], app.TIME_FORMAT).replace(tzinfo=timezone.utc)
        self.assertLess(abs(datetime.now(timezone.utc) - timedelta(minutes=60) - at), timedelta(minutes=1))
        self.assertEqual(first["last_status"], "no_mail")
        # What the examples found is seen: a run now reports only what is new.
        self.assertEqual(len(app.load_seen({"pk": "USER#" + OWNER, "sk": "SEARCH#a1b2c3d4e5f6"})), 6)
        # Active examples are due on their schedules; the inactive one is not.
        due = {key[1] for key, item in self.table.items.items() if item.get("due")}
        self.assertNotIn("SEARCH#d4e5f6a1b2c3", due)
        self.assertIn("SEARCH#a1b2c3d4e5f6", due)

    def test_users(self):
        reset.handler({}, None)
        self.assertEqual(set(self.cognito.users), {OWNER})
        user = self.cognito.users[OWNER]
        self.assertEqual((user["password"], user["mfa"], user["enabled"]), ("Demo-Password-1", False, True))
        self.assertIn(OWNER, self.cognito.admins)
        self.assertEqual((self.cognito.signed_out, self.cognito.locale_cleared), ([OWNER], [OWNER]))

    def test_demo_user_made_again(self):
        del self.cognito.users[OWNER]
        reset.handler({}, None)
        self.assertEqual(set(self.cognito.users), {OWNER})


if __name__ == "__main__":
    unittest.main()
