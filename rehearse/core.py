"""Small, sequential application replay and state checks."""

import hashlib
import uuid

PAYLOAD = b"synthetic artifact for local rehearsal\n"
REQUEST_ID = "request-1"
CASES = ("happy", "duplicate", "lost-s3", "lost-index", "before-s3")


class InjectedTimeout(Exception):
    """A deliberately hidden successful response, or a pre-call failure."""


class FaultClient:
    def __init__(self, client, method, timing, events):
        self.client = client
        self.method = method
        self.timing = timing
        self.events = events
        self.fired = False

    def __getattr__(self, name):
        target = getattr(self.client, name)
        if name != self.method:
            return target

        def call(**kwargs):
            if not self.fired:
                self.fired = True
                if self.timing == "before":
                    self.events.append({"operation": name, "event": "refused-before-call"})
                    raise InjectedTimeout(f"synthetic timeout before {name}")
                target(**kwargs)
                self.events.append({"operation": name, "event": "committed-response-hidden"})
                raise InjectedTimeout(f"synthetic timeout after {name}")
            return target(**kwargs)

        return call


def clients_with_fault(case, s3, ddb, events):
    if case == "lost-s3":
        return FaultClient(s3, "put_object", "after", events), ddb
    if case == "lost-index":
        return s3, FaultClient(ddb, "put_item", "after", events)
    if case == "before-s3":
        return FaultClient(s3, "put_object", "before", events), ddb
    return s3, ddb


def publish(s3, ddb, bucket, table, publisher, request_id=REQUEST_ID):
    # The broken example gives each retry a new identity, even for the same request.
    key = f"artifacts/{request_id if publisher == 'fixed' else uuid.uuid4().hex}"
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    s3.put_object(Bucket=bucket, Key=key, Body=PAYLOAD)
    ddb.put_item(
        TableName=table,
        Item={
            "key": {"S": key},
            "request_id": {"S": request_id},
            "sha256": {"S": digest},
        },
    )
    return key


def replay(s3, ddb, bucket, table, publisher, deliveries, events, limit=2):
    attempts = 0
    outcomes = []
    for delivery in range(deliveries):
        for _ in range(limit):
            attempts += 1
            try:
                key = publish(s3, ddb, bucket, table, publisher)
                events.append({"delivery": delivery + 1, "event": "completed", "key": key})
                outcomes.append("completed")
                break
            except InjectedTimeout as exc:
                events.append({"delivery": delivery + 1, "event": "application-retry", "reason": str(exc)})
        else:
            outcomes.append("attempt-budget-exhausted")
    return attempts, outcomes


def inspect(s3, ddb, bucket, table):
    # These are fault-free clients. Failure to observe is an error, never a pass.
    objects = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        objects.extend(page.get("Contents", []))
    rows = []
    for page in ddb.get_paginator("scan").paginate(TableName=table, ConsistentRead=True):
        rows.extend(page.get("Items", []))
    return objects, rows


def check(s3, ddb, bucket, table, attempts, outcomes, case, fired):
    objects, rows = inspect(s3, ddb, bucket, table)
    keys = [obj["Key"] for obj in objects]
    expected = "artifacts/" + REQUEST_ID
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    checks = {
        "all_deliveries_completed": bool(outcomes) and all(x == "completed" for x in outcomes),
        "attempt_budget": attempts <= (4 if case == "duplicate" else 2),
        "fault_applied": fired if case in ("lost-s3", "lost-index", "before-s3") else not fired,
        "one_object": len(keys) == 1,
        "one_index_row": len(rows) == 1,
    }
    # A broken publisher may use a different stable-looking key. Detect duplicate
    # artifacts without requiring the fixed publisher's naming scheme.
    checks["object_bytes_match"] = all(
        s3.get_object(Bucket=bucket, Key=k)["Body"].read() == PAYLOAD for k in keys
    ) and len(keys) > 0
    checks["index_matches_object"] = len(rows) == 1 and len(keys) == 1 and all(
        row.get("request_id", {}).get("S") == REQUEST_ID
        and row.get("sha256", {}).get("S") == digest
        and row.get("key", {}).get("S") == keys[0]
        for row in rows
    )
    return checks, {"object_count": len(keys), "index_count": len(rows), "attempts": attempts,
                    "outcomes": outcomes, "fixed_key_observed": keys == [expected]}
