"""Agent runtime config written by the settings page, read by the future on-board agent.

Files (atomic replace, dir 0700, files 0600, owner = the daemon user, root on the T113)::

    <root>/net.json        network profile + proxy route
    <root>/provider.json   selected provider/model/base_url/route + *reference* to the key file

API keys are never written here: ``api_key_file`` only names ``<secrets>/<provider>.key``, which
is provisioned from the Mac/web page (out of band).  root defaults: /data/mk20/agent and
/data/mk20/secrets on the device; tests/sim pass temp dirs.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .catalogue import CATALOGUE

DEVICE_ROOT = "/data/mk20/agent"
DEVICE_SECRETS = "/data/mk20/secrets"
# Mac end of the USB link, M4 addressing order (fw>=1.1.0 ECM → 1.0.x → link-local)
MAC_HOSTS = ("172.30.250.1", "10.20.0.1", "169.254.7.1")

PROVIDERS: dict[str, dict] = {
    p["id"]: {"base_url": p["base_url"], "domestic": p["domestic"]} for p in CATALOGUE
}


def net_config(full: dict) -> dict:
    if full["profile"] == "mac_proxy":
        port = int(full["proxy_port"])
        host = full.get("proxy_host", MAC_HOSTS[0])
        urls = [f"http://{h}:{port}" for h in (MAC_HOSTS if host == MAC_HOSTS[0] else (host,))]
        return {"version": 1, "profile": "mac_proxy", "proxy": {"url": urls[0], "fallbacks": urls[1:]},
                "routes": {"domestic": "proxy", "foreign": "proxy"},
                "note": "Mac mihomo rules split domestic/foreign; never fall back to direct for foreign"}
    wp = full.get("wifi_proxy") or None
    return {"version": 1, "profile": "board_wifi", "proxy": {"url": wp, "fallbacks": []},
            "routes": {"domestic": "direct", "foreign": "proxy" if wp else "blocked"}}


def provider_config(full: dict, secrets_dir: str) -> dict:
    p = full["provider"]
    meta = PROVIDERS[p]
    return {"version": 1, "provider": p, "model": full["model"], "base_url": meta["base_url"],
            "domestic": meta["domestic"], "route": "domestic" if meta["domestic"] else "foreign",
            "api_key_file": os.path.join(secrets_dir, f"{p}.key")}


def route_for(net: dict, prov: dict) -> str | None:
    """Proxy URL to use, "" for direct, None if the route is blocked (foreign without proxy)."""
    r = net["routes"][prov["route"]]
    if r == "blocked":
        return None
    return net["proxy"]["url"] if r == "proxy" else ""


def _atomic_write(path: str, data: dict) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    tmp = f"{path}.tmp.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


class ConnectionTester(Protocol):
    sync: bool

    def test(self, prov: dict, proxy: str | None) -> tuple[bool, str]: ...


@dataclass
class HttpReachability:
    """Real check: any HTTP answer from base_url (401/404 included) through the chosen route = reachable."""
    timeout: float = 4.0
    sync: bool = False

    def test(self, prov: dict, proxy: str | None) -> tuple[bool, str]:
        if proxy is None:
            return False, "需代理"
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
        opener = urllib.request.build_opener(handler)
        import time
        t0 = time.monotonic()
        try:
            opener.open(urllib.request.Request(prov["base_url"], method="GET"), timeout=self.timeout)
        except urllib.error.HTTPError:
            pass  # server answered → reachable
        except Exception as e:  # noqa: BLE001
            return False, f"不可达（{type(e).__name__}）"
        return True, f"成功 · {int((time.monotonic() - t0) * 1000)} ms"


@dataclass
class MockTester:
    """Sim/tests: no network.  Reachable unless the route is blocked; records calls."""
    sync: bool = True
    calls: list = field(default_factory=list)
    fail: set = field(default_factory=set)

    def test(self, prov: dict, proxy: str | None) -> tuple[bool, str]:
        self.calls.append((prov["provider"], proxy))
        if proxy is None:
            return False, "需代理"
        if prov["provider"] in self.fail:
            return False, "不可达（模拟）"
        return True, "成功 · 模拟 " + ("180" if prov["domestic"] else "420") + " ms"


@dataclass
class AgentConfigApplier:
    root: str = DEVICE_ROOT
    secrets_dir: str = DEVICE_SECRETS
    tester: ConnectionTester = field(default_factory=HttpReachability)
    calls: list = field(default_factory=list)

    def apply(self, changes: dict, full: dict) -> tuple[bool, str]:
        try:
            _atomic_write(os.path.join(self.root, "net.json"), net_config(full))
            _atomic_write(os.path.join(self.root, "provider.json"), provider_config(full, self.secrets_dir))
        except OSError as e:
            return False, f"写配置失败：{e.strerror or e}"
        self.calls.append(dict(changes))
        return True, "已应用 · 智能体配置已更新"

    def has_key(self, provider: str) -> bool:
        try:
            return os.path.getsize(os.path.join(self.secrets_dir, f"{provider}.key")) > 0
        except OSError:
            return False

    def run_test(self, full: dict, done: Callable[[bool, str], None]) -> None:
        net, prov = net_config(full), provider_config(full, self.secrets_dir)
        proxy = route_for(net, prov)
        if self.tester.sync:
            done(*self.tester.test(prov, proxy))
        else:
            threading.Thread(target=lambda: done(*self.tester.test(prov, proxy)), daemon=True).start()
