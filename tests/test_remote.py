import json

import pytest

from helpers import FakeHttp
from manifests import entry, manifest
from minecraft_setup.engine.remote import RAW, RemoteError, config_url, fetch_index, fetch_manifest


def test_fetch_index():
    http = FakeHttp({f"{RAW}/packs/index.json": json.dumps({"packs": ["fabric-26.3", "solo"]}).encode()})
    assert fetch_index(http) == ["fabric-26.3", "solo"]


def test_fetch_index_rejects_bad_shape():
    http = FakeHttp({f"{RAW}/packs/index.json": b'{"packs": "solo"}'})
    with pytest.raises(RemoteError, match="index"):
        fetch_index(http)


def test_fetch_manifest():
    m = manifest(entry("10 perf - sodium.jar"))
    http = FakeHttp({f"{RAW}/packs/fabric-26.3/manifest.json": m.dumps().encode()})
    assert fetch_manifest(http, "fabric-26.3") == m


def test_fetch_manifest_bad_json_raises_remote_error():
    http = FakeHttp({f"{RAW}/packs/solo/manifest.json": b"{nope"})
    with pytest.raises(RemoteError, match="solo"):
        fetch_manifest(http, "solo")


def test_fetch_manifest_for_another_pack_id_refused():
    http = FakeHttp({f"{RAW}/packs/solo/manifest.json": manifest().dumps().encode()})
    with pytest.raises(RemoteError, match="fabric-26.3"):
        fetch_manifest(http, "solo")


def test_missing_pack_raises_remote_error():
    with pytest.raises(RemoteError, match="solo"):
        fetch_manifest(FakeHttp({}), "solo")


def test_config_url_quotes_spaces_and_brackets():
    assert config_url("solo", "config/[cfg] a/b c.json") == f"{RAW}/packs/solo/files/config/%5Bcfg%5D%20a/b%20c.json"
