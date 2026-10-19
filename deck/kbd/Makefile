CFLAGS ?= -Wall -Wextra -Werror -O2 -std=c11 -D_DEFAULT_SOURCE
SRC = gd32.c gd32io.c host.c panels.c screen.c jpeg.c kbd.c
host: mk20-kbd
mk20-kbd: mk20_kbd.c $(SRC) *.h vendor/stb_image.h
	$(CC) $(CFLAGS) -o $@ mk20_kbd.c $(SRC)
armhf: mk20_kbd.c $(SRC) *.h vendor/stb_image.h
	arm-linux-gnueabihf-gcc $(CFLAGS) -static -o mk20-kbd.armhf mk20_kbd.c $(SRC)
tests/test_kbd: tests/test_kbd.c tests/tests_k3.inc $(SRC) *.h vendor/stb_image.h
	$(CC) $(CFLAGS) -fsanitize=address,undefined -fno-sanitize-recover=undefined -g -I. -o $@ tests/test_kbd.c $(SRC)
test: tests/test_kbd
	./tests/test_kbd
clean:
	rm -f mk20-kbd mk20-kbd.armhf tests/test_kbd
.PHONY: host armhf test clean
