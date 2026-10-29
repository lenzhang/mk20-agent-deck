# reference/

- `spi_lcd_map.txt` — reverse-engineered SPI LCD panel map of the MK20.
- `OpenSourceLicenseDemo_V1.0/` — **not in git.** Vendor (Waveshare) Qt demo for the MK20 serial protocol
  ("Protocol A"). `deck/mk20deck/protocol.py` and `deck/tools/golden*/` were cross-checked against its
  `mainwindow.cpp` / `CRC32.cpp`. Its licence is unclear, so it is not redistributed: download it from the
  Waveshare MK20 wiki (https://www.waveshare.com/wiki/MK20) and unpack it here if you need to regenerate
  `deck/tests/fixtures/golden_frames.txt`.
