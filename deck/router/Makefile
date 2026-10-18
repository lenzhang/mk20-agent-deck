CFLAGS ?= -Wall -Wextra -Werror -O2
host: mk20-router
mk20-router: mk20_router.c
	$(CC) $(CFLAGS) -o $@ $<
armhf: mk20_router.c
	arm-linux-gnueabihf-gcc $(CFLAGS) -static -o mk20-router.armhf $<
.PHONY: host armhf
