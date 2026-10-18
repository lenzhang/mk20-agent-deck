import json
import os
import stat

from mk20deck.agentcfg import (AgentConfigApplier, HttpReachability, MockTester, net_config, provider_config,
                               route_for)
from mk20deck.settings import DEFAULTS, SettingsPage, SettingsStore


def mk(tmp_path, keys=("qwen",)):
    sec = tmp_path / "secrets"
    sec.mkdir(mode=0o700)
    for k in keys:
        (sec / f"{k}.key").write_text("x")
    return AgentConfigApplier(root=str(tmp_path / "agent"), secrets_dir=str(sec), tester=MockTester())


def test_mac_proxy_net_has_m4_fallbacks():
    n = net_config({**DEFAULTS, "proxy_port": 7891})
    assert n["proxy"]["url"] == "http://172.30.250.1:7891"
    assert n["proxy"]["fallbacks"] == ["http://10.20.0.1:7891", "http://169.254.7.1:7891"]


def test_board_wifi_routes():
    n = net_config({**DEFAULTS, "profile": "board_wifi"})
    assert n["routes"] == {"domestic": "direct", "foreign": "blocked"}
    n2 = net_config({**DEFAULTS, "profile": "board_wifi", "wifi_proxy": "http://127.0.0.1:7890"})
    assert n2["routes"]["foreign"] == "proxy"
    po = provider_config({**DEFAULTS, "provider": "openai", "model": "gpt-5"}, "/s")
    assert route_for(n, po) is None and route_for(n2, po) == "http://127.0.0.1:7890"
    pq = provider_config(DEFAULTS, "/s")
    assert route_for(n, pq) == ""


def test_apply_writes_atomic_private_files_without_keys(tmp_path):
    ap = mk(tmp_path)
    ok, _ = ap.apply({"provider": "qwen"}, dict(DEFAULTS))
    assert ok
    agent = tmp_path / "agent"
    assert stat.S_IMODE(agent.stat().st_mode) == 0o700
    for f in ("net.json", "provider.json"):
        assert stat.S_IMODE((agent / f).stat().st_mode) == 0o600
    prov = json.loads((agent / "provider.json").read_text())
    assert prov["api_key_file"].endswith("secrets/qwen.key") and "api_key" not in prov
    assert prov["base_url"].startswith("https://dashscope")
    assert "x" not in (agent / "provider.json").read_text().split('"api_key_file"')[0].split()  # no key material
    assert not [p for p in os.listdir(agent) if ".tmp" in p]


def test_apply_failure_reported(tmp_path):
    (tmp_path / "agent").write_text("not a dir")
    ap = AgentConfigApplier(root=str(tmp_path / "agent"), secrets_dir=str(tmp_path), tester=MockTester())
    ok, msg = ap.apply({}, dict(DEFAULTS))
    assert not ok and "失败" in msg


def test_has_key(tmp_path):
    ap = mk(tmp_path, keys=("qwen",))
    assert ap.has_key("qwen") and not ap.has_key("kimi")


def test_settings_page_end_to_end_test_routes(tmp_path):
    ap = mk(tmp_path, keys=("qwen", "openai"))
    s = SettingsPage(store=SettingsStore(str(tmp_path / "s.json")), applier=ap)
    s.open()
    s.key(4, 1); s.key(1, 2)                       # test with defaults (qwen via Mac proxy)
    assert s.test_result.startswith("成功") and ap.tester.calls[-1] == ("qwen", "http://172.30.250.1:7890")
    s.key(1, 1); s.key(2, 2); s.key(2, 4); s.key(2, 4)   # board Wi-Fi, confirmed
    s.key(2, 1); s.key(5, 2); s.key(2, 4)                # openai
    s.key(4, 1); s.key(1, 2)
    assert s.test_result == "失败：需代理"
    assert json.loads((tmp_path / "agent" / "net.json").read_text())["profile"] == "board_wifi"


def test_test_requires_apply_first(tmp_path):
    s = SettingsPage(store=SettingsStore(str(tmp_path / "s.json")), applier=mk(tmp_path))
    s.open()
    s.key(2, 1); s.key(2, 2)        # stage GLM
    s.key(4, 1); s.key(1, 2)
    assert "先「应用」" in s.toast


def test_http_reachability_blocked_route_makes_no_request():
    ok, msg = HttpReachability(timeout=0.1).test({"base_url": "https://example.invalid"}, None)
    assert not ok and msg == "需代理"
