#ifndef MATRIXLAB_WORK_QUEUE_H
#define MATRIXLAB_WORK_QUEUE_H

#include <pthread.h>
#include <stddef.h>
#include <stdint.h>

/* Work queue item */
typedef struct wq_item {
    void (*fn)(void *arg);
    void *arg;
    struct wq_item *next;
} wq_item_t;

/* Thread-safe work queue */
typedef struct {
    wq_item_t *head;
    wq_item_t *tail;
    int count;
    int max_size;
    pthread_mutex_t mutex;
    pthread_cond_t not_empty;
    pthread_cond_t not_full;
    volatile int shutdown;
} work_queue_t;

/* Create/destroy work queue */
work_queue_t *workqueue_create(int max_size);
void workqueue_destroy(work_queue_t *wq);

/* Push work item (blocks if full) */
int workqueue_push(work_queue_t *wq, void (*fn)(void *), void *arg);

/* Pop work item (blocks if empty) */
wq_item_t *workqueue_pop(work_queue_t *wq);

/* Try pop (non-blocking) */
wq_item_t *workqueue_try_pop(work_queue_t *wq);

/* Get queue size */
int workqueue_size(work_queue_t *wq);

/* Signal shutdown */
void workqueue_shutdown(work_queue_t *wq);

#endif
