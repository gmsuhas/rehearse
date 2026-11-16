# rehearse

A timed-out write may already have succeeded. Find out what your retry leaves behind.

**TLDR.** This small Python demo writes an artifact to S3 and its index to
DynamoDB. It deliberately hides a successful write response and replays the
request. Then it checks the real state left in a local [AWS Emulator](https://github.com/aws/aws-emulator):
one logical artifact, a matching index entry, and no missing objects. It
includes a deliberately broken publisher and a corrected one, so a useful run
should show the broken path fail and the corrected path pass. No AWS account or
customer data is needed.

**Read this if** you own an ingest or AI artifact pipeline that retries after a
timeout. A successful retry does not prove the first attempt had no effect.
This repo is a test of *application recovery*, not a replacement for the
emulator's [fidelity tests](https://github.com/aws/aws-emulator-evaluation-suite)
or a test of IAM and production resilience.

## The one failure

A request uploads a blob and records its location. The local service accepts
`PutObject`, but the caller never receives its success response. A retry with
a fresh object key creates two objects for one logical request. A retry keyed
by the stable request ID converges on one object. The runner inspects both S3
and DynamoDB after recovery rather than trusting the return value.

```text
rehearse run --publisher broken --case lost-s3
# Expected verdict: FAIL, because there are two objects for one request.
rehearse run --publisher fixed --case lost-s3
# Expected verdict: PASS, if the emulator supports the required operations.
```

These lines describe the expected behavior, not a run captured on this machine.
The injected timeout is a Python wrapper that forwards the SDK call, sees its
success, then raises. It models uncertainty at the application boundary; it
is not a dropped TCP packet and does not test the SDK's internal retry logic.
SDK retries are disabled. The first version covers sequential replays only,
not concurrent writers, crashes or conflicting payloads under the same ID.

## Run locally

Python 3.10+ and `boto3` are required. Unlike my earlier dependency-free
experiments, this one uses the real AWS SDK. Create a virtual environment and
install the local package:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Start an official AWS Emulator separately. On macOS and Windows, follow the
[Docker instructions](https://docs.aws.amazon.com/emulator/latest/userguide/getting-started-docker.html)
to **build your own image**: AWS does not distribute a prebuilt image. On Apple
silicon use the aarch64 RPM. Publish port 9297 to `127.0.0.1` only, share the
generated `~/.aws/emulator` directory with the container, leave authentication
on, and turn telemetry off if desired. On Linux you can use the official package.
This repo does not launch or modify your emulator locally. A GitHub-hosted
workflow starts a separate emulator inside a disposable Docker container.

The CLI reads `~/.aws/emulator/<port>/` (9297 by default) for generated
credentials and CA certificate. It explicitly targets `https://127.0.0.1:<port>`,
requires an `EMULATOR` access key, checks `/__admin/health`, and verifies the emulator's
account `000000000000` before it writes anything. It uses an isolated SDK
session with empty profile configuration and does not read your regular AWS
credentials. Still, do not run arbitrary plugins or external code under the
impression that this is a network sandbox.

Run each case, then inspect its output. Only `PASS` exits zero:

```sh
.venv/bin/python -m rehearse run --publisher broken --case lost-s3 --report broken.json
.venv/bin/python -m rehearse run --publisher fixed --case lost-s3 --report fixed.json
.venv/bin/python -m rehearse run --publisher fixed --case lost-index --report index.json
.venv/bin/python -m rehearse run --publisher fixed --case duplicate --report duplicate.json
.venv/bin/python -m rehearse run --publisher fixed --case before-s3 --report before.json
.venv/bin/python -m rehearse run --publisher fixed --case happy --report happy.json
```

A `FAIL` is an observed invariant violation or an unapplied requested fault.
`UNSUPPORTED` is an HTTP 501 from the emulator. `ERROR` means setup,
execution, inspection or cleanup did not finish. Missing evidence never
becomes a `PASS`.
Use only generated test data; reports do not contain credentials or payloads.
Each run creates unique local bucket and table names and cleans up only those
resources, then reports any cleanup failure as `ERROR`. No unattended
real-AWS execution is part of this project.

## What the checks actually say

| Case | Injected event | Expected result for the fixed publisher |
|---|---|---|
| `happy` | none | one object and one matching index row |
| `duplicate` | same logical request delivered twice | still one object and row |
| `lost-s3` | `PutObject` succeeds; response hidden once | replay converges |
| `lost-index` | `PutItem` succeeds; response hidden once | replay converges |
| `before-s3` | first `PutObject` call refused before forwarding | bounded retry converges |

For every case the observer checks object count, bytes, recorded key and
checksum, index count and linkage, as well as whether the requested fault fired
and whether the application stayed inside its retry budget. The report carries
SDK version, case and wrapper-level call events (not SDK-internal attempts). If
an operation returns 501, the result is `UNSUPPORTED`, not a passing
demonstration. Check the emulator's
[service fidelity scores](https://github.com/aws/aws-emulator/releases) and
[operation coverage](https://docs.aws.amazon.com/emulator/latest/userguide/check-project.html)
for the release you run. Passing locally does **not** prove AWS production
behavior, exactly-once execution, authorization, multi-account behavior,
concurrent recovery or cross-service atomicity. Test those separately on AWS.

## Upstream and validation status

The emulator source is AWS's work. [My fork](https://github.com/gmsuhas/aws-emulator)
is a reference copy, not my implementation; inherited workflows are disabled.
The evaluation suite is linked, not copied or rebranded.

**Prototype:** offline tests pass; actual emulator compatibility has not yet
been validated. The `checks` GitHub Actions workflow runs unit tests on
Python 3.10 and 3.13 and a Docker-based integration on Ubuntu. It uses a pinned
official emulator package, no real AWS credentials, telemetry disabled and
loopback-only ports. Integration asserts the broken publisher fails for the
expected duplicate-state invariant, not an unrelated setup failure.

## Offline tests

```sh
python3 -m unittest discover -s tests -v
```

Offline tests need no boto3, Docker or credentials. They cover the injected
fault, broken/fixed behavior and evidence aggregation with in-memory fakes.
They cannot establish AWS Emulator compatibility. Until the integration runs
on a pinned emulator release, do not publish a green screenshot.

## Why this sits with otl, ramp and veto

[otl](https://github.com/gmsuhas/otl) leaves an unclosed span visible.
[ramp](https://github.com/gmsuhas/ramp) puts the variant on the request that
failed. [veto](https://github.com/gmsuhas/veto) refuses to pass a rule with
missing input. Here, a timeout is unresolved evidence: replay the operation,
inspect the state, and report an unknown result as unknown. The common theme is
not hiding uncertainty behind a tidy dashboard.

## Sources and boundaries

- [AWS Emulator announcement](https://aws.amazon.com/blogs/aws/test-your-applications-against-aws-locally-with-aws-emulator/)
- [Point boto3 at the emulator](https://docs.aws.amazon.com/emulator/latest/userguide/point-sdk-to-emulator.html)
- [Emulator security limits](https://docs.aws.amazon.com/emulator/latest/userguide/security.html)
- [AWS emulator fidelity suite](https://github.com/aws/aws-emulator-evaluation-suite) tests AWS behavior; this tests an example application's recovery
- [LocalStack's chaos engineering](https://docs.localstack.cloud/aws/capabilities/chaos-engineering/) already offers fault injection; this repo is deliberately a tiny, auditable application-specific rehearsal

This is an independent example, not an AWS product or a claim that fault
injection is new.
