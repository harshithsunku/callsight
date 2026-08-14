#include "helper.h"

/* Deliberately naive recursion: generates a deep, busy call tree. */
int fib(int n)
{
    if (n < 2)
        return n;
    return fib(n - 1) + fib(n - 2);
}

/* Chatty leaf helper — excluded in trace.config to prove selection works. */
int mix(int x)
{
    return (x * 1103515245 + 12345) & 0x7fffffff;
}
