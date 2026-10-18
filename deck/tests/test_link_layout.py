import io

from PIL import Image

from mk20deck import protocol as P
from mk20deck.layout import DeviceLayout
from mk20deck.link import DeviceLink
from mk20deck.transport import MockDevice


def _jpeg(w, h):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(b, "JPEG")
    return b.getvalue()


def test_default_layout_guess():
    lay = DeviceLayout.default()
    assert (lay.width, lay.height) == (640, 656)
    assert len(lay.keys) == 20 and lay.subscreen.w == 428 and lay.subscreen.h == 142
    r = lay.key_rect(5, 4)
    assert (r.x, r.y) == (512, 528)
    assert lay.fw_to_doc(0, 0) == (1, 1)


def test_get_info_roundtrip_and_base():
    info = DeviceLayout.default().to_get_info()
    lay = DeviceLayout.from_get_info(info, fw_base=0)
    assert not lay.guessed and len(lay.keys) == 20
    lay1 = DeviceLayout.from_get_info(info, fw_base=1)
    assert lay1.fw_to_doc(1, 1) == (1, 1)
    bad = DeviceLayout.from_get_info({"deviceWidth": 0})
    assert bad.guessed and bad.extra.get("getInfo_incomplete")


def test_handshake_show_and_ack_latency():
    dev = MockDevice()
    keys = []
    link = DeviceLink(dev, on_key=keys.append, ack_timeout=1.0, preamble_flush=4096)
    link.start()
    lay = link.handshake()
    assert lay.width == 640 and not lay.guessed
    assert link.show_jpeg(_jpeg(640, 656))
    assert link.frame_ack.summary()["n"] == 1
    assert len(dev.frames) == 1
    dev.press(2, 3, True)
    import time
    for _ in range(100):
        if keys:
            break
        time.sleep(0.01)
    assert keys == [P.KeyEvent(2, 3, True)]
    assert all(r.get("method") != "keyboardInput" for r in dev.json_requests)
    link.close()


def test_wrong_resolution_rejected_by_device():
    dev = MockDevice()
    link = DeviceLink(dev, ack_timeout=0.2, preamble_flush=0)
    link.start()
    link.handshake()
    assert not link.show_jpeg(_jpeg(128, 128))
    assert dev.rejected and link.device_errors
    link.close()
