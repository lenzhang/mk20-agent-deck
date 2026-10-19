CFLAGS ?= -Wall -Wextra -Werror -O2 -std=c99 -D_DEFAULT_SOURCE
SRC = gd32.c gd32io.c host.c panels.c screen.c jpeg.c kbd.c hid.c ctl.c ctlscr.c k2estop.c onboard.c ui.c ui_font.c ui_font_render.c uicfg.c
host: mk20-kbd
mk20-kbd: mk20_kbd.c $(SRC) *.h vendor/stb_image.h
	$(CC) $(CFLAGS) -o $@ mk20_kbd.c $(SRC) -pthread
armhf: mk20_kbd.c $(SRC) *.h vendor/stb_image.h
	arm-linux-gnueabihf-gcc $(CFLAGS) -static -o mk20-kbd.armhf mk20_kbd.c $(SRC) -pthread
tests/test_kbd: tests/test_kbd.c tests/tests_k3.inc tests/tests_ctl.inc $(SRC) *.h vendor/stb_image.h
	$(CC) $(CFLAGS) -fsanitize=address,undefined -fno-sanitize-recover=undefined -g -I. -o $@ tests/test_kbd.c $(SRC) -pthread
tests/test_kbd_tsan: tests/test_kbd.c tests/tests_k3.inc tests/tests_ctl.inc $(SRC) *.h vendor/stb_image.h
	$(CC) $(CFLAGS) -fsanitize=thread -g -I. -o $@ tests/test_kbd.c $(SRC) -pthread
tsan: tests/test_kbd_tsan
	./tests/test_kbd_tsan
test: tests/test_kbd tests/test_kbd_tsan mk20-kbd
	./tests/test_kbd
	./tests/test_kbd_tsan
	python3 tests/e2e_run.py
clean:
	rm -f mk20-kbd mk20-kbd.armhf tests/test_kbd tests/test_kbd_tsan tests/ui_test tests/ui_headless tests/uicfg_cli tests/ui_load tests/onboard_headless tests/onboard_test tests/onboard_test_tsan
.PHONY: host armhf test tsan clean

# U1 is deliberately independent of hardware/transport sources and libraries.
UI_FLAGS = -std=c99 -Wall -Wextra -Werror -O1 -g -I.
UI_SRC = ui.c ui_font.c ui_font_render.c uicfg.c panels.c
UI_HDR = ui.h ui_font.h uicfg.h ui_labels.inc panels.h providers.inc
UI_SAN = -fsanitize=address,undefined -fno-sanitize-recover=undefined
PYTHON ?= python3
tests/ui_test: tests/ui_test.c $(UI_SRC) $(UI_HDR)
	$(CC) $(UI_FLAGS) $(UI_SAN) -o $@ tests/ui_test.c $(UI_SRC)
tests/ui_headless: tests/ui_headless.c $(UI_SRC) $(UI_HDR)
	$(CC) $(UI_FLAGS) $(UI_SAN) -o $@ tests/ui_headless.c $(UI_SRC)
tests/uicfg_cli: tests/uicfg_cli.c uicfg.c uicfg.h providers.inc
	$(CC) $(UI_FLAGS) $(UI_SAN) -o $@ tests/uicfg_cli.c uicfg.c
ui-test: tests/ui_test tests/ui_headless tests/uicfg_cli
	$(PYTHON) ../tools/gen-providers.py --check
	./tests/ui_test
	$(PYTHON) tests/ui_conformance.py
	$(PYTHON) tests/ui_golden.py
ui-golden: tests/ui_headless
	$(PYTHON) tests/ui_golden.py --regenerate
.PHONY: ui-test ui-golden
# Drop unused transport/chat functions; the resulting test executes loaders only.
ifeq ($(shell uname -s),Darwin)
UI_GC = -Wl,-dead_strip
else
UI_GC = -Wl,--gc-sections
endif
UI_AGENT = ../agent
UI_AGENT_SRC = $(addprefix $(UI_AGENT)/src/,llm.c netcfg.c net.c json_util.c)
tests/ui_load: tests/ui_load.c $(UI_AGENT_SRC) $(UI_AGENT)/vendor/cjson/cJSON.c
	$(CC) $(UI_FLAGS) $(UI_SAN) -Wno-deprecated-declarations -ffunction-sections -fdata-sections $(UI_GC) -I$(UI_AGENT)/src -I$(UI_AGENT)/vendor/cjson -I$(UI_AGENT)/vendor/mbedtls/include -DMBEDTLS_CONFIG_FILE='"mk20_mbedtls_config.h"' -o $@ tests/ui_load.c $(UI_AGENT_SRC) $(UI_AGENT)/vendor/cjson/cJSON.c -lm
ui-test: tests/ui_load

# U2 uses the real compositor with fake clock/fds and buffers only.
tests/onboard_headless: tests/onboard_headless.c $(SRC) *.h
	$(CC) $(CFLAGS) $(UI_SAN) -g -I. -o $@ tests/onboard_headless.c $(SRC) -pthread
tests/onboard_test: tests/onboard_test.c tests/tests_onboard_r1.inc $(SRC) *.h
	$(CC) $(CFLAGS) $(UI_SAN) -g -I. -o $@ tests/onboard_test.c $(SRC) -pthread
onboard-test: tests/onboard_test tests/onboard_headless
	./tests/onboard_test
ui-test: onboard-test
test: onboard-test
.PHONY: onboard-test

tests/onboard_test_tsan: tests/onboard_test.c tests/tests_onboard_r1.inc $(SRC) *.h
	$(CC) $(CFLAGS) -fsanitize=thread -g -I. -o $@ tests/onboard_test.c $(SRC) -pthread
