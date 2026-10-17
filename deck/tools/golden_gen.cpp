// Golden-frame generator: replicates packet_cmd_ack() from the vendor
// mainwindow.cpp byte-for-byte, using the vendor CRC32.cpp unchanged.
// Build: g++ -I<ref> golden_gen.cpp <ref>/CRC32.cpp -o golden_gen
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>
#include "CRC32.h"

static unsigned long CalcDataCRC(const char* d, int n) {
    unsigned long crc = 0; unsigned char res = 0;
    Start_CheckCRC(&crc, &res); Calculate_CRC(&crc, d, n); CheckCRC_Result(&crc, &res);
    return crc;
}
static void put32(std::vector<unsigned char>& v, uint32_t x) { unsigned char b[4]; memcpy(b, &x, 4); v.insert(v.end(), b, b + 4); }
static std::vector<unsigned char> packet(uint32_t id, uint32_t cmd, const std::string& data) {
    std::vector<unsigned char> r = {0xA1, 0xA5, 0x5A, 0x5E};
    put32(r, id); put32(r, cmd); uint32_t size = data.size(); put32(r, size);
    uint32_t sz = size; put32(r, (uint32_t)CalcDataCRC((const char*)&sz, 4));
    r.insert(r.end(), data.begin(), data.end());
    put32(r, (uint32_t)CalcDataCRC(data.data(), data.size()));
    return r;
}
static void emit(const char* name, uint32_t id, uint32_t cmd, const std::string& data) {
    auto p = packet(id, cmd, data);
    printf("%s ", name);
    for (auto c : p) printf("%02x", c);
    printf("\n");
}
int main() {
    // QJsonDocument::Compact output of the demo's commands.
    emit("getInfo", 1, 101, "{\"method\":\"getInfo\"}");
    emit("setBacklight50", 2, 101, "{\"method\":\"setBacklight\",\"parameters\":{\"level\":50}}");
    emit("keyStateChanged", 7, 101, "{\"method\":\"keyStateChanged\",\"parameters\":{\"col\":0,\"pressed\":true,\"row\":0}}");
    emit("showJpgEmpty", 3, 100, "");
    std::string jpg = "\xff\xd8\xff\xe0\x00\x10JFIF\x00"; jpg += std::string("\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9", 11);
    emit("showJpgStub", 4, 100, jpg);
    return 0;
}
