#include "httpd.h"
#include <stdio.h>
#include <unistd.h>
int main(void)
{
    int fd = httpd_listen("172.30.250.2:8780",0);
    if (fd < 0) { perror("usb0 web bind"); return 1; }
    puts("ready"); fflush(stdout); pause(); close(fd); return 0;
}
