"""mk20_accept helpers: bridge client detection fails closed; getInfo refuses unbound."""
import mk20_accept as acc

NS = ("Active Internet connections (w/o servers)\nProto Recv-Q Send-Q Local Address Foreign Address State\n"
      "tcp 0 0 172.30.250.2:17890 172.30.250.1:50000 ESTABLISHED\n"
      "tcp 0 0 172.30.250.2:5555 172.30.250.1:50001 ESTABLISHED\nNETSTAT_RC=0\n")


def test_clients_counted(monkeypatch):
    monkeypatch.setattr(acc, "sh", lambda c, timeout=20: (0, NS))
    assert acc.bridge_clients() == 1


def test_clients_zero(monkeypatch):
    monkeypatch.setattr(acc, "sh", lambda c, timeout=20: (0, NS.replace("17890", "17891")))
    assert acc.bridge_clients() == 0


def test_clients_unknown_fails_closed(monkeypatch):
    for out in ((124, ""), (0, "netstat: not found\n"), (1, NS)):
        monkeypatch.setattr(acc, "sh", lambda c, timeout=20, o=out: o)
        assert acc.bridge_clients() is None


def test_getinfo_refuses_unbound():
    ok, d = acc.get_info(None)
    assert not ok and "unbound" in d


def test_module_doc_does_not_claim_hid_writes():
    import mk20_accept
    doc = mk20_accept.__doc__
    assert "enumeration only" in doc and "reports written" not in doc and "consumed by the host" not in doc
