#include "logging.h"
#include <pthread.h>
#include <string.h>
#include <unistd.h>

/* Thread-local thread name */
__thread char tl_thread_name[64] = "main";

/* Global log level */
volatile log_level_t g_log_level = LOG_INFO;

/* Mutex for serialized log output */
static pthread_mutex_t log_mutex = PTHREAD_MUTEX_INITIALIZER;

/* Log level names */
static const char *level_names[] = {"DBG", "INF", "WRN", "ERR"};

/* Initialize logging subsystem */
void logging_init(void) {
    g_log_level = LOG_INFO;
    setvbuf(stdout, NULL, _IOLBF, 0);
}

/* Set global log level */
void logging_set_level(log_level_t level) {
    g_log_level = level;
}

/* Core variadic logging function */
void log_message(log_level_t level, const char *fmt, ...) {
    if (level < g_log_level) return;

    va_list args;
    va_start(args, fmt);

    pthread_mutex_lock(&log_mutex);

    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    double elapsed = (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;

    fprintf(stdout, "[%8.3f] %s: ", elapsed, level_names[level]);
    vfprintf(stdout, fmt, args);
    fprintf(stdout, "\n");

    pthread_mutex_unlock(&log_mutex);

    va_end(args);
}

/* Periodic logging helper */
void log_periodic(int *counter, int interval, log_level_t level, const char *fmt, ...) {
    if (!counter) return;
    (*counter)++;
    if (*counter < interval) return;
    *counter = 0;

    if (level < g_log_level) return;

    va_list args;
    va_start(args, fmt);

    pthread_mutex_lock(&log_mutex);

    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    double elapsed = (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;

    fprintf(stdout, "[%8.3f] %s: ", elapsed, level_names[level]);
    vfprintf(stdout, fmt, args);
    fprintf(stdout, "\n");

    pthread_mutex_unlock(&log_mutex);

    va_end(args);
}

/* Get timestamp string (thread-local buffer) */
const char *logging_timestamp(void) {
    static __thread char buf[32];
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    snprintf(buf, sizeof(buf), "%.3f", (double)ts.tv_sec + (double)ts.tv_nsec / 1e9);
    return buf;
}

/* Flush all log output */
void logging_flush(void) {
    fflush(stdout);
    fflush(stderr);
}
