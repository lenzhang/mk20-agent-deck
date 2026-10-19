"""The adapter must expose C pixels, and reject unsupported DSL fields."""
import gzip
import io
from pathlib import Path

from PIL import Image

from mk20sim.cases import Case, run_case
from mk20sim.harness import CSim


def test_c_backend_home_pixels_and_browser_frame():
    sim = CSim()
    try:
        reference = Path(__file__).resolve().parents[1] / "kbd/tests/golden/ui/home-offline/fb01.ppm.gz"
        with gzip.open(reference, "rb") as f:
            expected = Image.open(io.BytesIO(f.read())).convert("RGB")
        assert sim.key_image("A1").tobytes() == expected.tobytes()
        assert Image.open(io.BytesIO(sim.frame)).size == (640, 656)
    finally:
        sim.close()


def test_c_backend_unknown_expectation_is_failure():
    r = run_case(Case("unknown", "onboard", "", [{"expect": {"voice_generation": 9}}], backend="c"))
    assert not r.ok and r.failure.key == "voice_generation"
    assert r.screen_png_b64
