#ifndef MATRIXLAB_ERRORS_H
#define MATRIXLAB_ERRORS_H

#include <stdint.h>

/* Error codes */
typedef enum {
    ERR_OK = 0,
    ERR_NOMEM,
    ERR_INVALID_ARG,
    ERR_OVERFLOW,
    ERR_SINGULAR,
    ERR_TIMEOUT,
    ERR_IO,
    ERR_INTERNAL,
    ERR_NOT_FOUND,
    ERR_FULL,
    ERR_EMPTY
} error_code_t;

/* Error context with union payload */
typedef union {
    int ival;
    double dval;
    const char *sval;
    void *pval;
} error_payload_t;

typedef struct {
    error_code_t code;
    const char *file;
    int line;
    const char *func;
    const char *message;
    error_payload_t payload;
} error_context_t;

/* Thread-local last error */
extern __thread error_context_t tl_last_error;

/* Set error with source location */
#define SET_ERROR(code, msg) \
    error_set((code), __FILE__, __LINE__, __func__, (msg))

#define SET_ERROR_VAL(code, msg, val) \
    error_set_with_payload((code), __FILE__, __LINE__, __func__, (msg), (error_payload_t){.ival = (val)})

/* Error handling functions */
void error_set(error_code_t code, const char *file, int line, const char *func, const char *msg);
void error_set_with_payload(error_code_t code, const char *file, int line,
                            const char *func, const char *msg, error_payload_t payload);
void error_clear(void);
const char *error_code_str(error_code_t code);
void error_print_last(void);

/* Error statistics */
extern volatile uint64_t g_error_counts[16];
uint64_t error_get_count(error_code_t code);
void error_reset_counts(void);

#endif
