import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rehearse.core import FaultClient, InjectedTimeout
from rehearse.local import endpoint, read_credentials
from rehearse.runner import run, classify


class Pager:
    def __init__(self, rows, field):
        self.rows = rows
        self.field = field

    def paginate(self, **kwargs):
        yield {self.field: list(self.rows())}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.buckets = set()

    def create_bucket(self, Bucket):
        self.buckets.add(Bucket)

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def list_objects_v2(self, Bucket):
        return {"Contents": [{"Key": key} for bucket, key in self.objects if bucket == Bucket]}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return Pager(lambda: [{"Key": k} for _, k in self.objects], "Contents")

    def delete_object(self, Bucket, Key):
        del self.objects[(Bucket, Key)]

    def delete_bucket(self, Bucket):
        assert not any(b == Bucket for b, _ in self.objects)
        self.buckets.remove(Bucket)


class FakeDDB:
    def __init__(self):
        self.items = {}
        self.tables = set()

    def create_table(self, TableName, **kwargs):
        self.tables.add(TableName)

    def get_waiter(self, name):
        class Waiter:
            def wait(self, **kwargs):
                pass
        return Waiter()

    def put_item(self, TableName, Item):
        self.items[(TableName, Item["key"]["S"])] = Item
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_paginator(self, name):
        assert name == "scan"
        return Pager(lambda: self.items.values(), "Items")

    def delete_table(self, TableName):
        self.tables.remove(TableName)


class TestRehearsal(unittest.TestCase):
    def make_run(self, case, publisher):
        s3, ddb = FakeS3(), FakeDDB()
        result = run(case, publisher, connector=lambda _: (s3, ddb, "fake"))
        self.assertEqual(s3.buckets, set())
        self.assertEqual(ddb.tables, set())
        return result

    def test_fixed_publisher_survives_all_cases(self):
        for case in ("happy", "duplicate", "lost-s3", "lost-index", "before-s3"):
            with self.subTest(case=case):
                result = self.make_run(case, "fixed")
                self.assertEqual(result["verdict"], "PASS", result)
                self.assertEqual(result["observation"]["object_count"], 1)

    def test_broken_publisher_fails_for_duplicates_not_setup(self):
        for case in ("duplicate", "lost-s3", "lost-index"):
            with self.subTest(case=case):
                result = self.make_run(case, "broken")
                self.assertEqual(result["verdict"], "FAIL", result)
                self.assertFalse(result["checks"]["one_object"])
                self.assertEqual(result["observation"]["object_count"], 2)

    def test_commit_then_fail_did_write_before_raising(self):
        s3 = FakeS3()
        events = []
        client = FaultClient(s3, "put_object", "after", events)
        with self.assertRaises(InjectedTimeout):
            client.put_object(Bucket="b", Key="k", Body=b"v")
        self.assertEqual(s3.objects[("b", "k")], b"v")
        self.assertEqual(events[0]["event"], "committed-response-hidden")

    def test_unapplied_fault_cannot_pass(self):
        with patch("rehearse.runner.clients_with_fault", side_effect=lambda case, s3, ddb, events: (s3, ddb)):
            result = self.make_run("lost-s3", "fixed")
        self.assertEqual(result["verdict"], "FAIL")
        self.assertFalse(result["checks"]["fault_applied"])

    def test_observer_error_cannot_pass(self):
        s3, ddb = FakeS3(), FakeDDB()
        s3.get_paginator = lambda _: (_ for _ in ()).throw(RuntimeError("observation failed"))
        result = run("happy", "fixed", connector=lambda _: (s3, ddb, "fake"))
        self.assertEqual(result["verdict"], "ERROR")

    def test_cleanup_failure_cannot_pass(self):
        s3, ddb = FakeS3(), FakeDDB()
        s3.delete_bucket = lambda **_: (_ for _ in ()).throw(RuntimeError("cleanup failed"))
        result = run("happy", "fixed", connector=lambda _: (s3, ddb, "fake"))
        self.assertEqual(result["verdict"], "ERROR")
        self.assertIn("cleanup_errors", result)

    def test_unsupported_and_setup_errors_do_not_pass(self):
        class Unsupported(Exception):
            response = {"ResponseMetadata": {"HTTPStatusCode": 501}}

        self.assertEqual(classify(Unsupported()), "UNSUPPORTED")
        self.assertEqual(classify(RuntimeError("unknown")), "ERROR")
        error = RuntimeError("none response")
        error.response = None
        self.assertEqual(classify(error), "ERROR")
        result = run("happy", "fixed", connector=lambda _: (_ for _ in ()).throw(RuntimeError("no emulator")))
        self.assertEqual(result["verdict"], "ERROR")
        self.assertEqual(result["checks"], {})

    def test_non_emulator_credentials_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / ".aws/emulator/9297"
            folder.mkdir(parents=True)
            (folder / "emulator_credentials").write_text(
                "[default]\naws_access_key_id=AKIA123\naws_secret_access_key=secret\n")
            (folder / "ca.pem").write_text("certificate")
            with self.assertRaisesRegex(RuntimeError, "refusing non-emulator"):
                read_credentials(9297, root)
            (folder / "emulator_credentials").write_text(
                "[default]\naws_access_key_id=EMULATOR123\naws_secret_access_key=secret\n")
            self.assertEqual(read_credentials(9297, root)[0], "EMULATOR123")
        self.assertEqual(endpoint(9297), "https://127.0.0.1:9297")
        with self.assertRaises(ValueError):
            endpoint(0)


if __name__ == "__main__":
    unittest.main()
