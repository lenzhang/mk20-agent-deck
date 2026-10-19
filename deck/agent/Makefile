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
LLM_HDR = src/llm.h src/oai.h
LLM_OBJ = $(BUILD)/llm.o $(BUILD)/oai.o
LLM_NS = $(BUILD)/ns-llm.o $(BUILD)/ns-oai.o
NET_SRC = src/net.c src/netcfg.c src/clock.c src/json_util.c
NET_HDR = src/net.h src/netcfg.h src/clock.h src/json_util.h src/mk20_mbedtls_config.h src/psa_config.h
CPPFLAGS += -Ivendor/mbedtls/include -DMBEDTLS_CONFIG_FILE='"mk20_mbedtls_config.h"'
$(LLM_OBJ): $(BUILD)/%.o: src/%.c $(LLM_HDR) $(NET_HDR) | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) -c -o $@ $<
$(LLM_NS): $(BUILD)/ns-%.o: src/%.c $(LLM_HDR) $(NET_HDR) | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<
# Only translation units used by the client/selected crypto configuration.
TLS_NAMES = aes asn1parse asn1write base64 bignum bignum_core \
 chacha20 chachapoly cipher cipher_wrap constant_time ctr_drbg ecdh ecdsa \
 ecp ecp_curves entropy entropy_poll gcm md oid pem pk pk_ecc pk_wrap pkparse \
 platform_util poly1305 psa_crypto psa_crypto_aead psa_crypto_cipher psa_crypto_client \
 psa_crypto_driver_wrappers_no_static psa_crypto_ecp psa_crypto_hash psa_crypto_mac \
 psa_crypto_rsa psa_crypto_slot_management psa_crypto_random psa_util rsa rsa_alt_helpers \
 sha256 sha512 ssl_ciphersuites ssl_client ssl_msg ssl_tls ssl_tls12_client \
 ssl_tls13_client ssl_tls13_generic ssl_tls13_keys x509 x509_crt
TLS_OBJ = $(addprefix $(BUILD)/tls-,$(addsuffix .o,$(TLS_NAMES)))
TLS_NS = $(addprefix $(BUILD)/ns-tls-,$(addsuffix .o,$(TLS_NAMES)))
$(BUILD)/tls-%.o: vendor/mbedtls/library/%.c $(NET_HDR) | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(VENDOR_FLAGS) $(SAN) -c -o $@ $<
$(BUILD)/ns-tls-%.o: vendor/mbedtls/library/%.c $(NET_HDR) | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(VENDOR_FLAGS) -c -o $@ $<
$(BUILD)/ns-cJSON.o: vendor/cjson/cJSON.c vendor/cjson/cJSON.h | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(VENDOR_FLAGS) -c -o $@ $<
.DEFAULT_GOAL := all
all: $(BUILD)/oai_cli $(BUILD)/mk20-agent
$(BUILD):
	mkdir -p $@
$(BUILD)/cJSON.o: vendor/cjson/cJSON.c vendor/cjson/cJSON.h | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(VENDOR_FLAGS) $(SAN) -c -o $@ $<
$(BUILD)/oai_cli: src/oai_cli.c $(BUILD)/oai.o src/oai.h src/json_util.c src/json_util.h $(BUILD)/cJSON.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) -o $@ src/oai_cli.c $(BUILD)/oai.o src/json_util.c $(BUILD)/cJSON.o
$(BUILD)/test_oai: tests/test_oai.c $(BUILD)/oai.o src/oai.h src/json_util.c src/json_util.h $(BUILD)/cJSON.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) -o $@ tests/test_oai.c $(BUILD)/oai.o src/json_util.c $(BUILD)/cJSON.o
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
$(BUILD)/mk20-agent: src/agent_cli.c src/img.c src/img.h src/oai.h $(RFB_SRC) $(RFB_HDR) $(ZLIB_OBJ) $(BUILD)/stb_jpeg.o $(NET_SRC) $(NET_HDR) $(TLS_OBJ) $(BUILD)/cJSON.o $(LLM_OBJ) $(LLM_HDR)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) $(LDFLAGS) -o $@ src/agent_cli.c src/img.c $(RFB_SRC) $(ZLIB_OBJ) $(BUILD)/stb_jpeg.o $(NET_SRC) $(LLM_OBJ) $(TLS_OBJ) $(BUILD)/cJSON.o -lm
$(BUILD)/mk20-agent-nosan: src/agent_cli.c src/img.c src/img.h src/oai.h $(RFB_SRC) $(RFB_HDR) $(ZLIB_NS) $(BUILD)/ns-stb_jpeg.o $(NET_SRC) $(NET_HDR) $(TLS_NS) $(BUILD)/ns-cJSON.o $(LLM_NS) $(LLM_HDR)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ src/agent_cli.c src/img.c $(RFB_SRC) $(ZLIB_NS) $(BUILD)/ns-stb_jpeg.o $(NET_SRC) $(LLM_NS) $(TLS_NS) $(BUILD)/ns-cJSON.o -lm
$(BUILD)/test_rfb: tests/test_rfb.c $(RFB_SRC) $(RFB_HDR) src/img.c src/img.h $(ZLIB_OBJ) $(BUILD)/stb_jpeg.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) $(LDFLAGS) -o $@ tests/test_rfb.c src/img.c $(RFB_SRC) $(ZLIB_OBJ) $(BUILD)/stb_jpeg.o -lm
$(BUILD)/test_net: tests/test_net.c $(NET_SRC) $(NET_HDR) src/mem.c $(TLS_OBJ) $(BUILD)/cJSON.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) $(LDFLAGS) -o $@ tests/test_net.c $(NET_SRC) src/mem.c $(TLS_OBJ) $(BUILD)/cJSON.o -lm
$(BUILD)/test_cli: tests/test_cli.c src/agent_cli.c $(NET_SRC) $(NET_HDR) src/mem.c $(TLS_OBJ) $(BUILD)/cJSON.o $(LLM_OBJ) $(LLM_HDR)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) $(LDFLAGS) -o $@ tests/test_cli.c $(NET_SRC) $(LLM_OBJ) src/mem.c $(TLS_OBJ) $(BUILD)/cJSON.o -lm
$(BUILD)/test_llm: tests/test_llm.c $(LLM_OBJ) $(LLM_HDR) $(NET_SRC) $(NET_HDR) src/mem.c $(TLS_OBJ) $(BUILD)/cJSON.o
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SAN) $(LDFLAGS) -o $@ tests/test_llm.c $(LLM_OBJ) $(NET_SRC) src/mem.c $(TLS_OBJ) $(BUILD)/cJSON.o -lm
test: $(BUILD)/test_llm $(BUILD)/test_oai $(BUILD)/oai_cli $(BUILD)/mk20-agent $(BUILD)/test_rfb $(BUILD)/test_net $(BUILD)/test_cli
	$(BUILD)/test_llm
	$(BUILD)/test_oai
	$(BUILD)/test_rfb
	$(BUILD)/test_net
	$(BUILD)/test_cli
clean:
	rm -rf $(BUILD)
.PHONY: all test clean

armhf:
	@if command -v arm-linux-gnueabihf-gcc >/dev/null 2>&1; then \
	  $(MAKE) BUILD=build-armhf CC=arm-linux-gnueabihf-gcc SAN= LDFLAGS=-static build-armhf/mk20-agent-nosan; \
	else echo "armhf toolchain unavailable; static cross-build not run"; fi
.PHONY: armhf
