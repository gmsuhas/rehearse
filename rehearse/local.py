"""Explicit, loopback-only boto3 client construction, with no ambient credentials."""

import configparser
import os
from pathlib import Path
import ssl
import urllib.request

ACCOUNT = "000000000000"


def endpoint(port):
    if not 1 <= port <= 65535:
        raise ValueError("invalid emulator port")
    return f"https://127.0.0.1:{port}"


def read_credentials(port, home=None):
    folder = (Path(home) if home else Path.home()) / ".aws" / "emulator" / str(port)
    parser = configparser.ConfigParser()
    if not parser.read(folder / "emulator_credentials") or "default" not in parser:
        raise RuntimeError("emulator credentials missing")
    key = parser["default"].get("aws_access_key_id", "")
    secret = parser["default"].get("aws_secret_access_key", "")
    ca = folder / "ca.pem"
    if not key.startswith("EMULATOR") or not secret or not ca.is_file():
        raise RuntimeError("refusing non-emulator credentials or missing CA")
    return key, secret, str(ca)


def connect(port=9297, home=None):
    url = endpoint(port)
    key, secret, ca = read_credentials(port, home)
    # Do not allow proxies to forward loopback traffic; use the generated CA.
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)),
                                         urllib.request.ProxyHandler({}))
    with opener.open(url + "/__admin/health", timeout=5) as response:
        if response.status != 200 or response.read().strip() != b"ok":
            raise RuntimeError("emulator health response is unexpected")
    import boto3
    from botocore.config import Config
    # Explicit endpoint, credentials, region and CA. Environment variables do
    # not supply a profile, service endpoint or alternate credentials here.
    import botocore.session
    isolated = botocore.session.Session()
    isolated.get_component("config_store").set_config_variable("profile", None)
    isolated.set_config_variable("config_file", os.devnull)
    isolated.set_config_variable("credentials_file", os.devnull)
    isolated.set_config_variable("region", "us-east-1")
    isolated.set_credentials(key, secret)
    session = boto3.session.Session(botocore_session=isolated)
    common = dict(endpoint_url=url, verify=ca,
                  config=Config(retries={"mode": "standard", "total_max_attempts": 1},
                                inject_host_prefix=False, proxies={}, connect_timeout=5, read_timeout=5,
                                use_fips_endpoint=False, use_dualstack_endpoint=False,
                                request_checksum_calculation="when_required",
                                response_checksum_validation="when_required"))
    sts = session.client("sts", **common)
    if sts.get_caller_identity().get("Account") != ACCOUNT:
        raise RuntimeError("endpoint did not return the emulator account")
    # An explicit endpoint wins over AWS_ENDPOINT_URL and its service variants.
    s3 = session.client("s3", **{**common, "config": Config(
        retries={"mode": "standard", "total_max_attempts": 1},
        inject_host_prefix=False, proxies={}, connect_timeout=5, read_timeout=5,
                                use_fips_endpoint=False, use_dualstack_endpoint=False,
                                request_checksum_calculation="when_required",
                                response_checksum_validation="when_required", s3={"addressing_style": "path"})})
    ddb = session.client("dynamodb", **common)
    return s3, ddb, boto3.__version__
