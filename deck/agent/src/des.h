/* Minimal DES-ECB encrypt (public-domain style, written for MK20 VNC auth). */
#ifndef MK20_DES_H
#define MK20_DES_H
#include <stdint.h>
void des_encrypt_block(const uint8_t key[8], const uint8_t in[8], uint8_t out[8]);
#endif
