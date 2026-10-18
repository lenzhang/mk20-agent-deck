// Build: g++ -o golden golden_frame.cpp /workspace/mk20-research/oss/OpenSourceLicenseDemo_V1.0/CRC32.cpp -I/workspace/mk20-research/oss/OpenSourceLicenseDemo_V1.0
// Byte-for-byte reimplementation of the vendor demo's packet_cmd_ack (mainwindow.cpp)
// and the device's packet_cmd_new, using the vendor CRC32.cpp verbatim.
#include "CRC32.h"
#include <cstdio>
#include <cstring>
#include <string>
#include <cstdint>
static uint32_t CalcDataCRC(const std::string& d){unsigned long crc=0;unsigned char res=0;Start_CheckCRC(&crc,&res);Calculate_CRC(&crc,d.data(),d.size());CheckCRC_Result(&crc,&res);return (uint32_t)crc;}
static std::string u32(uint32_t v){return std::string((const char*)&v,4);}
int main(int argc,char**argv){
  uint32_t id=atoi(argv[1]),cmd=atoi(argv[2]); std::string data=argv[3];
  std::string r="\xA1\xA5\x5A\x5E"; r+=u32(id); r+=u32(cmd); r+=u32(data.size()); r+=u32(CalcDataCRC(u32(data.size()))); r+=data; r+=u32(CalcDataCRC(data));
  for(unsigned char c: r) printf("%02x",c); printf("\n");
}
