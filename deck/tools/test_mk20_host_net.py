"""Tests for mk20_host_net (pure parsing + apply/restore with mocked commands)."""
import ipaddress
import pytest
import mk20_host_net as m

PORTS = """
Hardware Port: Wi-Fi
Device: en0
Ethernet Address: aa:bb:cc:dd:ee:ff

Hardware Port: Serial
Device: en13
Ethernet Address: 02:11:22:33:44:56
"""
ORDER = """An asterisk (*) denotes that a network service is disabled.
(1) Wi-Fi
(Hardware Port: Wi-Fi, Device: en0)

(*) My (old) LAN
(Hardware Port: USB 10/100/1000 LAN, Device: en10)

(4) MK20 (deck) link
(Hardware Port: Serial, Device: en13)
"""
NETSTAT = """Routing tables

Internet:
Destination        Gateway            Flags               Netif Expire
default            192.168.88.1        UGScg                 en0
1                  198.18.0.1         UGSc                utun1
10.20/24           link#27            UCS                  en13      !
10.20.0.2          2:11:22:33:44:55   UHLWI                en13   1149
127                127.0.0.1          UCS                   lo0
172.16/12          192.168.88.1        UGSc                  en0
"""
T = ipaddress.IPv4Network("172.30.250.0/24")


def test_find_device_and_service():
    assert m.find_device(PORTS) == "en13"
    assert m.find_device(PORTS, "00:00:00:00:00:01") is None
    assert m.find_service(ORDER, "en13") == ("MK20 (deck) link", True)
    assert m.find_service(ORDER, "en10") == ("My (old) LAN", False)
    assert m.find_service(ORDER, "en9") is None


def test_norm_dest():
    assert str(m.norm_dest("10.20/24")) == "10.20.0.0/24"
    assert str(m.norm_dest("172.30.250")) == "172.30.250.0/24"
    assert str(m.norm_dest("169.254")) == "169.254.0.0/16"
    assert str(m.norm_dest("10.20.0.2")) == "10.20.0.2/32"
    assert str(m.norm_dest("default")) == "0.0.0.0/0"
    assert m.norm_dest("fe80::%lo0") is None and m.norm_dest("link#27") is None


def test_conflicts():
    r = m.parse_routes(NETSTAT)
    assert m.conflicts(r, T, "en13") == []          # broad TUN / 172.16/12 routes don't block
    r2 = m.parse_routes(NETSTAT + "172.30.250         link#30            UCS                 bridge100      !\n"
                                  "172.30.250.7       1:2:3:4:5:6        UHLWI               en13   10\n")
    assert m.conflicts(r2, T, "en13") == ["172.30.250"]
    r3 = m.parse_routes(NETSTAT + "172.30.250.128/25  link#31            UCS                 utun4\n")
    assert m.conflicts(r3, T, "en13") == ["172.30.250.128/25"]


def test_netstat_fail_closed():
    with pytest.raises(RuntimeError):
        m.parse_routes("garbage\n")


def test_subnet_validation():
    assert str(m.subnet24("172.30.250")) == "172.30.250.0/24"
    for bad in ("999.1.1", "172.30.250.0", "01.2.3", "a.b.c"):
        with pytest.raises(Exception):
            m.subnet24(bad)


def test_parse_info():
    i = m.parse_info("Manual Configuration\nIP address: 10.20.0.1\nSubnet mask: 255.255.255.0\nRouter: (null)\n")
    assert i == {"mode": "manual", "ip": "10.20.0.1", "mask": "255.255.255.0", "router": None}
    assert m.parse_info("DHCP Configuration\nIP address: 192.168.1.5\n")["mode"] == "dhcp"


class Fake:
    def __init__(self, route_ok=True, ping_ok=True, dev_ip="172.30.250.2", info="Manual Configuration\nIP address: 10.20.0.1\nSubnet mask: 255.255.255.0\nRouter: (null)\n", netstat=NETSTAT):
        self.calls, self.route_ok, self.ping_ok, self.dev_ip, self.info_out, self.netstat = [], route_ok, ping_ok, dev_ip, info, netstat

    def install(self, mp):
        mp.setattr(m, "run", self.run)
        mp.setattr(m, "route_if", lambda ip: "en13" if self.route_ok else "en0")
        mp.setattr(m, "ping", lambda ip, tries=3: self.ping_ok)
        mp.setattr(m, "device_ecm_ip", lambda: self.dev_ip)
        mp.setattr(m.time, "sleep", lambda s: None)

    def run(self, cmd, check=True, timeout=15):
        self.calls.append(cmd)
        if cmd[:2] == ["networksetup", "-listallhardwareports"]:
            return PORTS
        if cmd[:2] == ["networksetup", "-listnetworkserviceorder"]:
            return ORDER
        if cmd[:2] == ["networksetup", "-getinfo"]:
            return self.info_out
        if cmd[0] == "netstat":
            return self.netstat
        if cmd[:2] == ["networksetup", "-listallnetworkservices"]:
            return "An asterisk (*) denotes that a network service is disabled.\nWi-Fi\nMK20 (deck) link\n"
        if cmd[0] == "ifconfig":
            return "en13: flags\n\tinet 169.254.7.1 netmask 0xffff0000\n"
        return ""

    def sets(self):
        return [c for c in self.calls if c[0] == "networksetup" and c[1].startswith("-set")]


def test_apply_only_touches_mk20_service(monkeypatch):
    f = Fake(); f.install(monkeypatch)
    assert m.Host().apply(T) == 0
    assert f.sets() == [["networksetup", "-setmanual", "MK20 (deck) link", "172.30.250.1", "255.255.255.0"]]


def test_apply_refuses_unconfirmed_device(monkeypatch):
    f = Fake(dev_ip="10.20.0.2"); f.install(monkeypatch)
    with pytest.raises(m.Refused):
        m.Host().apply(T)
    f2 = Fake(dev_ip=None); f2.install(monkeypatch)
    with pytest.raises(m.Refused):
        m.Host().apply(T)
    assert f.sets() == [] and f2.sets() == []


def test_apply_refuses_overlap(monkeypatch):
    f = Fake(netstat=NETSTAT + "172.30.250/24      link#30            UCS                 bridge100      !\n"); f.install(monkeypatch)
    with pytest.raises(m.Refused):
        m.Host().apply(T)
    assert f.sets() == []


def test_route_failure_restores_manual(monkeypatch):
    f = Fake(route_ok=False); f.install(monkeypatch)
    assert m.Host().apply(T) == 4
    assert f.sets()[-1] == ["networksetup", "-setmanual", "MK20 (deck) link", "10.20.0.1", "255.255.255.0"]


def test_ping_failure_restores_dhcp(monkeypatch):
    f = Fake(ping_ok=False, info="DHCP Configuration\nIP address: 169.254.3.3\n"); f.install(monkeypatch)
    assert m.Host().apply(T) == 5
    assert f.sets()[-1] == ["networksetup", "-setdhcp", "MK20 (deck) link"]


def test_forced_unreachable_keeps_and_warns(monkeypatch):
    f = Fake(ping_ok=False, dev_ip=None); f.install(monkeypatch)
    assert m.Host().apply(T, force=True) == 6
    assert len(f.sets()) == 1


def test_interrupt_restores(monkeypatch):
    f = Fake(); f.install(monkeypatch)
    def boom(ip):
        raise KeyboardInterrupt
    monkeypatch.setattr(m, "route_if", boom)
    with pytest.raises(KeyboardInterrupt):
        m.Host().apply(T)
    assert f.sets()[-1][3] == "10.20.0.1"


def test_disabled_service_refused(monkeypatch):
    f = Fake(); f.install(monkeypatch)
    monkeypatch.setattr(m, "find_service", lambda o, d: ("x", False))
    with pytest.raises(m.Refused):
        m.Host().apply(T)


def test_linklocal_creates_service_on_mk20_port(monkeypatch):
    f = Fake(); f.install(monkeypatch)
    monkeypatch.setattr(m, "route_if", lambda ip: "en0")
    assert m.Host().linklocal() == 0
    assert f.sets() == [["networksetup", "-setmanual", "MK20 link-local", "169.254.7.1", "255.255.0.0"]]
    assert ["networksetup", "-createnetworkservice", "MK20 link-local", "Serial"] in f.calls


def test_linklocal_removed_if_it_steals_link_local_default(monkeypatch):
    f = Fake(); f.install(monkeypatch)   # route_if -> en13 for everything
    assert m.Host().linklocal() == 4
    assert ["networksetup", "-removenetworkservice", "MK20 link-local"] in f.calls


def test_primary_service_ignores_linklocal_helper():
    order = ORDER + "\n(5) MK20 link-local\n(Hardware Port: Serial, Device: en13)\n"
    assert m.find_service(order, "en13") == ("MK20 (deck) link", True)
    assert [n for n, _, _ in m.services_on(order, "en13")] == ["MK20 (deck) link", "MK20 link-local"]
    with pytest.raises(m.Refused):
        m.find_service(order + "\n(6) Dup\n(Hardware Port: Serial, Device: en13)\n", "en13")


def test_two_mk20s_refused(monkeypatch):
    f = Fake(); f.install(monkeypatch)
    monkeypatch.setattr(m, "run", lambda cmd, check=True, timeout=15: PORTS + "\nHardware Port: Serial 2\nDevice: en14\nEthernet Address: 02:11:22:33:44:56\n" if cmd[1] == "-listallhardwareports" else f.run(cmd))
    with pytest.raises(m.Refused):
        m.Host()


def test_local_lo0_entry_not_overlap():
    r = m.parse_routes(NETSTAT + "172.30.250.1       127.0.0.1          UHS                   lo0\n")
    assert m.conflicts(r, T, "en13") == []
