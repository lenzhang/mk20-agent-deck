/* Read-only actual agent loaders; never invokes chat, key loading or transport. */
#include "llm.h"
#include "netcfg.h"
int main(int argc,char **argv)
{
    llm_provider_t p; netcfg_t n;
    if (argc!=3 || llm_provider_load(argv[1],&p) || netcfg_load(argv[2],&n)) return 1;
    netcfg_free(&n); return 0;
}
