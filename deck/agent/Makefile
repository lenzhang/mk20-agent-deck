CC ?= cc
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -O1 -g
CPPFLAGS += -Ivendor/cjson -Isrc -DCJSON_NESTING_LIMIT=32
SAN = -fsanitize=address,undefined -fno-sanitize-recover=undefined
BUILD ?= build
ifeq ($(shell uname -s),Darwin)
VENDOR_FLAGS = -Wno-deprecated-declarations
endif
RFB_SRC = src/rfb.c src/scale.c src/mem.c src/des.c
RFB_HDR = src/rfb.h src/scale.h src/mem.h src/des.h
ZLIB_SRC = $(addprefix vendor/zlib/,inflate.c inftrees.c inffast.c zutil.c adler32.c crc32.c)
ZLIB_OBJ = $(patsubst vendor/zlib/%.c,$(BUILD)/z-%.o,$(ZLIB_SRC))
ZLIB_NS = $(patsubst vendor/zlib/%.c,$(BUILD)/ns-z-%.o,$(ZLIB_SRC))
CPPFLAGS += -Ivendor/zlib -DZ_SOLO
all: $(BUILD)/oai_cli $(BUILD)/mk20-agent
$(BUILD):
	mkdir -p $@
$(BUILD)/cJSON.o: vendor/cjson/cJSON.c vendor/cjson/cJSON.h | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(VENDOR_FLAGS) $(SAN) -c -o $@ $<
$(BUILD)/oai_cli: src/oai_cli.c src/oai.c src/oai.h $(BUILD)/cJSON.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) -o $@ src/oai_cli.c src/oai.c $(BUILD)/cJSON.o
$(BUILD)/test_oai: tests/test_oai.c src/oai.c src/oai.h $(BUILD)/cJSON.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) -o $@ tests/test_oai.c src/oai.c $(BUILD)/cJSON.o
$(BUILD)/z-%.o: vendor/zlib/%.c | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) -c -o $@ $<
$(BUILD)/ns-z-%.o: vendor/zlib/%.c | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<
$(BUILD)/stb_jpeg.o: src/stb_jpeg.c vendor/stb/stb_image_write.h | $(BUILD)
# stb 1.16's JPEG bit buffer deliberately wraps signed shifts. Keep sanitizers;
# define wraparound semantics in this vendor-only TU, without editing the header.
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) -fwrapv -w -c -o $@ $<
$(BUILD)/ns-stb_jpeg.o: src/stb_jpeg.c vendor/stb/stb_image_write.h | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) -fwrapv -w -c -o $@ $<
$(BUILD)/mk20-agent: src/agent_cli.c src/img.c src/img.h src/oai.h $(RFB_SRC) $(RFB_HDR) $(ZLIB_OBJ) $(BUILD)/stb_jpeg.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) $(LDFLAGS) -o $@ src/agent_cli.c src/img.c $(RFB_SRC) $(ZLIB_OBJ) $(BUILD)/stb_jpeg.o -lm
$(BUILD)/mk20-agent-nosan: src/agent_cli.c src/img.c src/img.h src/oai.h $(RFB_SRC) $(RFB_HDR) $(ZLIB_NS) $(BUILD)/ns-stb_jpeg.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ src/agent_cli.c src/img.c $(RFB_SRC) $(ZLIB_NS) $(BUILD)/ns-stb_jpeg.o -lm
$(BUILD)/test_rfb: tests/test_rfb.c $(RFB_SRC) $(RFB_HDR) src/img.c src/img.h $(ZLIB_OBJ) $(BUILD)/stb_jpeg.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) $(LDFLAGS) -o $@ tests/test_rfb.c src/img.c $(RFB_SRC) $(ZLIB_OBJ) $(BUILD)/stb_jpeg.o -lm
test: $(BUILD)/test_oai $(BUILD)/oai_cli $(BUILD)/mk20-agent $(BUILD)/test_rfb
	$(BUILD)/test_oai
	$(BUILD)/test_rfb
clean:
	rm -rf $(BUILD)
.PHONY: all test clean
