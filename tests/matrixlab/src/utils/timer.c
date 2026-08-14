#include "timer.h"
#include "rng.h"
#include <sched.h>
#include <unistd.h>

/* Start a timer */
void timer_start(timer_t_ml *t) {
    clock_gettime(CLOCK_MONOTONIC, &t->start);
}

/* Stop a timer */
void timer_stop(timer_t_ml *t) {
    clock_gettime(CLOCK_MONOTONIC, &t->end);
}

/* Get elapsed time in milliseconds */
double timer_elapsed_ms(const timer_t_ml *t) {
    double s = (double)(t->end.tv_sec - t->start.tv_sec) * 1000.0;
    double ns = (double)(t->end.tv_nsec - t->start.tv_nsec) / 1e6;
    return s + ns;
}

/* Get elapsed time in microseconds */
double timer_elapsed_us(const timer_t_ml *t) {
    double s = (double)(t->end.tv_sec - t->start.tv_sec) * 1e6;
    double ns = (double)(t->end.tv_nsec - t->start.tv_nsec) / 1e3;
    return s + ns;
}

/* Get current monotonic time in milliseconds */
double timer_now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1e6;
}

/* Sleep for given microseconds with slight randomization */
void timer_sleep_us(int base_us, int jitter_us) {
    int actual = base_us;
    if (jitter_us > 0) {
        actual += rng_next_int(-jitter_us, jitter_us + 1);
    }
    if (actual <= 0) actual = 1;

    struct timespec ts;
    ts.tv_sec = actual / 1000000;
    ts.tv_nsec = (long)(actual % 1000000) * 1000L;
    nanosleep(&ts, NULL);
}

/* Sleep for given milliseconds */
void timer_sleep_ms(int ms) {
    if (ms <= 0) return;
    struct timespec ts;
    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (long)(ms % 1000) * 1000000L;
    nanosleep(&ts, NULL);
}

/* Busy-spin for approximate microseconds */
__attribute__((noinline))
void timer_busy_spin_us(int us) {
    struct timespec start, now;
    clock_gettime(CLOCK_MONOTONIC, &start);
    volatile int dummy = 0;
    for (;;) {
        /* Some busywork to prevent optimization */
        for (int i = 0; i < 100; i++) {
            dummy += i;
        }
        clock_gettime(CLOCK_MONOTONIC, &now);
        long elapsed = (now.tv_sec - start.tv_sec) * 1000000L +
                       (now.tv_nsec - start.tv_nsec) / 1000L;
        if (elapsed >= us) break;
    }
}

/* Yield to scheduler */
void timer_yield(void) {
    sched_yield();
}

/* Adaptive sleep based on load phase */
void timer_adaptive_sleep(int phase, int base_us) {
    int sleep_us;
    switch (phase) {
        case 0: /* High load - minimal sleep */
            sleep_us = base_us / 10;
            break;
        case 1: /* Normal load */
            sleep_us = base_us;
            break;
        case 2: /* Low load - extra sleep */
            sleep_us = base_us * 5;
            break;
        case 3: /* Idle - long sleep */
            sleep_us = base_us * 20;
            break;
        default:
            sleep_us = base_us;
            break;
    }
    timer_sleep_us(sleep_us, sleep_us / 4);
}
