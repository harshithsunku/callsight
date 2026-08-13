#ifndef MATRIXLAB_TIMER_H
#define MATRIXLAB_TIMER_H

#include <stdint.h>
#include <time.h>

/* High-resolution timer */
typedef struct {
    struct timespec start;
    struct timespec end;
} timer_t_ml;

/* Start a timer */
void timer_start(timer_t_ml *t);

/* Stop a timer */
void timer_stop(timer_t_ml *t);

/* Get elapsed time in milliseconds */
double timer_elapsed_ms(const timer_t_ml *t);

/* Get elapsed time in microseconds */
double timer_elapsed_us(const timer_t_ml *t);

/* Get current monotonic time in milliseconds */
double timer_now_ms(void);

/* Sleep for given microseconds with slight randomization */
void timer_sleep_us(int base_us, int jitter_us);

/* Sleep for given milliseconds */
void timer_sleep_ms(int ms);

/* Busy-spin for approximate microseconds (cache-unfriendly) */
void timer_busy_spin_us(int us);

/* Yield to scheduler */
void timer_yield(void);

/* Adaptive sleep based on load phase */
void timer_adaptive_sleep(int phase, int base_us);

#endif
