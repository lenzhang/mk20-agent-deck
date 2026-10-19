"""Shared installed/source provider presets; no discovery or network access."""
import json
from pathlib import Path

CATALOGUE = json.loads((Path(__file__).with_name("providers.json")).read_text(encoding="utf-8"))["providers"]
