CC ?= cc
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -O1 -g
CPPFLAGS += -Ivendor/cjson -Isrc -DCJSON_NESTING_LIMIT=32
SAN = -fsanitize=address,undefined -fno-sanitize-recover=undefined
BUILD ?= build
ifeq ($(shell uname -s),Darwin)
VENDOR_FLAGS = -Wno-deprecated-declarations
endif
all: $(BUILD)/oai_cli
$(BUILD):
	mkdir -p $@
$(BUILD)/cJSON.o: vendor/cjson/cJSON.c vendor/cjson/cJSON.h | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(VENDOR_FLAGS) $(SAN) -c -o $@ $<
$(BUILD)/oai_cli: src/oai_cli.c src/oai.c src/oai.h $(BUILD)/cJSON.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) -o $@ src/oai_cli.c src/oai.c $(BUILD)/cJSON.o
$(BUILD)/test_oai: tests/test_oai.c src/oai.c src/oai.h $(BUILD)/cJSON.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) -o $@ tests/test_oai.c src/oai.c $(BUILD)/cJSON.o
test: $(BUILD)/test_oai $(BUILD)/oai_cli
	$(BUILD)/test_oai
clean:
	rm -rf $(BUILD)
.PHONY: all test clean
