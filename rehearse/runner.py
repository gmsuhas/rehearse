"""One isolated run: local setup, replay, observation and owned-resource cleanup."""

import uuid

from .core import CASES, check, clients_with_fault, replay
from .local import connect


def classify(exc):
    response = getattr(exc, "response", None) or {}
    if response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 501 or response.get("Error", {}).get("Code") in ("NotImplemented", "NotImplementedException"):
        return "UNSUPPORTED"
    return "ERROR"


def run(case, publisher, port=9297, connector=connect):
    if case not in CASES or publisher not in ("broken", "fixed"):
        raise ValueError("unknown case or publisher")
    report = {"case": case, "publisher": publisher, "verdict": "ERROR", "checks": {}, "events": []}
    try:
        s3, ddb, report["boto3_version"] = connector(port)
    except Exception as exc:
        report["verdict"] = classify(exc)
        report["error"] = f"setup: {type(exc).__name__}: {exc}"
        return report
    suffix = uuid.uuid4().hex[:16]
    bucket, table = "rehearse-" + suffix, "rehearse-" + suffix
    report["resource_suffix"] = suffix
    bucket_created = table_created = False
    try:
        s3.create_bucket(Bucket=bucket)
        bucket_created = True
        ddb.create_table(TableName=table,
                         AttributeDefinitions=[{"AttributeName": "key", "AttributeType": "S"}],
                         KeySchema=[{"AttributeName": "key", "KeyType": "HASH"}],
                         BillingMode="PAY_PER_REQUEST")
        table_created = True
        ddb.get_waiter("table_exists").wait(
            TableName=table, WaiterConfig={"Delay": 1, "MaxAttempts": 10})
        fault_s3, fault_ddb = clients_with_fault(case, s3, ddb, report["events"])
        attempts, outcomes = replay(fault_s3, fault_ddb, bucket, table, publisher,
                                    2 if case == "duplicate" else 1, report["events"])
        fired = getattr(fault_s3, "fired", False) or getattr(fault_ddb, "fired", False)
        report["checks"], report["observation"] = check(
            s3, ddb, bucket, table, attempts, outcomes, case, fired)
        report["verdict"] = "PASS" if all(report["checks"].values()) else "FAIL"
    except Exception as exc:
        report["verdict"] = classify(exc)
        report["error"] = f"execution/observation: {type(exc).__name__}: {exc}"
    finally:
        errors = []
        if bucket_created:
            try:
                for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                    for obj in page.get("Contents", []):
                        s3.delete_object(Bucket=bucket, Key=obj["Key"])
                s3.delete_bucket(Bucket=bucket)
            except Exception as exc:
                errors.append(f"bucket cleanup: {type(exc).__name__}: {exc}")
        if table_created:
            try:
                ddb.delete_table(TableName=table)
            except Exception as exc:
                errors.append(f"table cleanup: {type(exc).__name__}: {exc}")
        if errors:
            report["cleanup_errors"] = errors
            report["verdict"] = "ERROR"
    return report
