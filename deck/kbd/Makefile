CFLAGS ?= -Wall -Wextra -Werror -O2 -std=c11 -D_DEFAULT_SOURCE
SRC = gd32.c gd32io.c host.c panels.c screen.c jpeg.c kbd.c
host: mk20-kbd
mk20-kbd: mk20_kbd.c $(SRC) *.h vendor/stb_image.h
	$(CC) $(CFLAGS) -o $@ mk20_kbd.c $(SRC) -pthread
armhf: mk20_kbd.c $(SRC) *.h vendor/stb_image.h
	arm-linux-gnueabihf-gcc $(CFLAGS) -static -o mk20-kbd.armhf mk20_kbd.c $(SRC) -pthread
tests/test_kbd: tests/test_kbd.c tests/tests_k3.inc $(SRC) *.h vendor/stb_image.h
	$(CC) $(CFLAGS) -fsanitize=address,undefined -fno-sanitize-recover=undefined -g -I. -o $@ tests/test_kbd.c $(SRC) -pthread
tests/test_kbd_tsan: tests/test_kbd.c tests/tests_k3.inc $(SRC) *.h vendor/stb_image.h
	$(CC) $(CFLAGS) -fsanitize=thread -g -I. -o $@ tests/test_kbd.c $(SRC) -pthread
tsan: tests/test_kbd_tsan
	./tests/test_kbd_tsan
test: tests/test_kbd tests/test_kbd_tsan mk20-kbd
	./tests/test_kbd
	./tests/test_kbd_tsan
	python3 tests/e2e_run.py
clean:
	rm -f mk20-kbd mk20-kbd.armhf tests/test_kbd tests/test_kbd_tsan
.PHONY: host armhf test tsan clean
