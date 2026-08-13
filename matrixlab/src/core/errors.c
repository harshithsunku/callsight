#include "errors.h"
#include "logging.h"
#include <string.h>
#include <stdio.h>

/* Thread-local last error */
__thread error_context_t tl_last_error = {0};

/* Global error counts */
volatile uint64_t g_error_counts[16] = {0};

/* Error code names */
static const char *error_names[] = {
    "OK", "NOMEM", "INVALID_ARG", "OVERFLOW", "SINGULAR",
    "TIMEOUT", "IO", "INTERNAL", "NOT_FOUND", "FULL", "EMPTY"
};

/* Set error with source location */
void error_set(error_code_t code, const char *file, int line, const char *func, const char *msg) {
    tl_last_error.code = code;
    tl_last_error.file = file;
    tl_last_error.line = line;
    tl_last_error.func = func;
    tl_last_error.message = msg;
    tl_last_error.payload.ival = 0;

    if (code < 16) {
        __sync_fetch_and_add(&g_error_counts[code], 1);
    }
}

/* Set error with payload */
void error_set_with_payload(error_code_t code, const char *file, int line,
                            const char *func, const char *msg, error_payload_t payload) {
    tl_last_error.code = code;
    tl_last_error.file = file;
    tl_last_error.line = line;
    tl_last_error.func = func;
    tl_last_error.message = msg;
    tl_last_error.payload = payload;

    if (code < 16) {
        __sync_fetch_and_add(&g_error_counts[code], 1);
    }
}

/* Clear the last error */
void error_clear(void) {
    memset(&tl_last_error, 0, sizeof(tl_last_error));
}

/* Get error code string */
const char *error_code_str(error_code_t code) {
    if (code <= ERR_EMPTY) return error_names[code];
    return "UNKNOWN";
}

/* Print the last error */
void error_print_last(void) {
    if (tl_last_error.code == ERR_OK) return;
    fprintf(stderr, "Error %s at %s:%d (%s): %s\n",
            error_code_str(tl_last_error.code),
            tl_last_error.file ? tl_last_error.file : "?",
            tl_last_error.line,
            tl_last_error.func ? tl_last_error.func : "?",
            tl_last_error.message ? tl_last_error.message : "");
}

/* Get error count for a specific code */
uint64_t error_get_count(error_code_t code) {
    if (code < 16) return g_error_counts[code];
    return 0;
}

/* Reset all error counts */
void error_reset_counts(void) {
    memset((void *)g_error_counts, 0, sizeof(g_error_counts));
}
