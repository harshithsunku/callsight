#include <stdio.h>
#include "helper.h"

int main(void)
{
    long acc = 0;
    for (int i = 0; i < 100; i++)
        acc += fib(18) + mix(i);
    printf("acc=%ld\n", acc);
    return 0;
}
