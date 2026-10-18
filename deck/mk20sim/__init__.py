"""MK20 key/menu interaction simulator (phase 1).

Drives the *real* control stack (``mk20deck.daemon.Daemon`` → ``Controller`` →
``Renderer``) with a virtual input adapter (raw firmware ``KeyEvent``s, including
knob codes 100..105) and a virtual screen sink.  Nothing here touches hardware.
"""
from .harness import Sim, KEY_NAMES  # noqa: F401
