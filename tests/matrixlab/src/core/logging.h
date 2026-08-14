#ifndef MATRIXLAB_LOGGING_H
#define MATRIXLAB_LOGGING_H

#include <stdio.h>
#include <stdarg.h>
#include <time.h>

/* Log levels */
typedef enum {
    LOG_DEBUG = 0,
    LOG_INFO  = 1,
    LOG_WARN  = 2,
    LOG_ERROR = 3
} log_level_t;

/* Thread-local thread name storage */
extern __thread char tl_thread_name[64];

/* Global log level */
extern volatile log_level_t g_log_level;

/* Initialize logging subsystem */
void logging_init(void);

/* Set global log level */
void logging_set_level(log_level_t level);

/* Core variadic logging function */
void log_message(log_level_t level, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

/* Convenience macros with thread name */
#define LOG_DEBUG(fmt, ...) log_message(LOG_DEBUG, "[%s] " fmt, tl_thread_name, ##__VA_ARGS__)
#define LOG_INFO(fmt, ...)  log_message(LOG_INFO,  "[%s] " fmt, tl_thread_name, ##__VA_ARGS__)
#define LOG_WARN(fmt, ...)  log_message(LOG_WARN,  "[%s] " fmt, tl_thread_name, ##__VA_ARGS__)
#define LOG_ERROR(fmt, ...) log_message(LOG_ERROR, "[%s] " fmt, tl_thread_name, ##__VA_ARGS__)

/* System-level log (no thread name) */
#define LOG_SYSTEM(fmt, ...) log_message(LOG_INFO, "[system] " fmt, ##__VA_ARGS__)

/* Periodic logging helper - log every N calls */
void log_periodic(int *counter, int interval, log_level_t level, const char *fmt, ...);

/* Get timestamp string */
const char *logging_timestamp(void);

/* Flush all log output */
void logging_flush(void);

#endif
