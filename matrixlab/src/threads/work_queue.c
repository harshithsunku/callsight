#include "work_queue.h"
#include <stdlib.h>
#include <string.h>

/* Create work queue */
work_queue_t *workqueue_create(int max_size) {
    work_queue_t *wq = (work_queue_t *)malloc(sizeof(work_queue_t));
    if (!wq) return NULL;

    wq->head = NULL;
    wq->tail = NULL;
    wq->count = 0;
    wq->max_size = max_size;
    wq->shutdown = 0;
    pthread_mutex_init(&wq->mutex, NULL);
    pthread_cond_init(&wq->not_empty, NULL);
    pthread_cond_init(&wq->not_full, NULL);
    return wq;
}

/* Destroy work queue */
void workqueue_destroy(work_queue_t *wq) {
    if (!wq) return;

    pthread_mutex_lock(&wq->mutex);
    wq_item_t *item = wq->head;
    while (item) {
        wq_item_t *next = item->next;
        free(item);
        item = next;
    }
    pthread_mutex_unlock(&wq->mutex);

    pthread_mutex_destroy(&wq->mutex);
    pthread_cond_destroy(&wq->not_empty);
    pthread_cond_destroy(&wq->not_full);
    free(wq);
}

/* Push work item (blocks if full) */
int workqueue_push(work_queue_t *wq, void (*fn)(void *), void *arg) {
    if (!wq || !fn) return -1;

    wq_item_t *item = (wq_item_t *)malloc(sizeof(wq_item_t));
    if (!item) return -1;
    item->fn = fn;
    item->arg = arg;
    item->next = NULL;

    pthread_mutex_lock(&wq->mutex);

    while (wq->count >= wq->max_size && !wq->shutdown) {
        pthread_cond_wait(&wq->not_full, &wq->mutex);
    }

    if (wq->shutdown) {
        pthread_mutex_unlock(&wq->mutex);
        free(item);
        return -1;
    }

    if (wq->tail) {
        wq->tail->next = item;
    } else {
        wq->head = item;
    }
    wq->tail = item;
    wq->count++;

    pthread_cond_signal(&wq->not_empty);
    pthread_mutex_unlock(&wq->mutex);
    return 0;
}

/* Pop work item (blocks if empty) */
wq_item_t *workqueue_pop(work_queue_t *wq) {
    if (!wq) return NULL;

    pthread_mutex_lock(&wq->mutex);

    while (wq->count == 0 && !wq->shutdown) {
        pthread_cond_wait(&wq->not_empty, &wq->mutex);
    }

    if (wq->shutdown && wq->count == 0) {
        pthread_mutex_unlock(&wq->mutex);
        return NULL;
    }

    wq_item_t *item = wq->head;
    if (item) {
        wq->head = item->next;
        if (!wq->head) wq->tail = NULL;
        wq->count--;
        pthread_cond_signal(&wq->not_full);
    }

    pthread_mutex_unlock(&wq->mutex);
    return item;
}

/* Try pop (non-blocking) */
wq_item_t *workqueue_try_pop(work_queue_t *wq) {
    if (!wq) return NULL;

    pthread_mutex_lock(&wq->mutex);
    if (wq->count == 0) {
        pthread_mutex_unlock(&wq->mutex);
        return NULL;
    }

    wq_item_t *item = wq->head;
    if (item) {
        wq->head = item->next;
        if (!wq->head) wq->tail = NULL;
        wq->count--;
        pthread_cond_signal(&wq->not_full);
    }
    pthread_mutex_unlock(&wq->mutex);
    return item;
}

/* Get queue size */
int workqueue_size(work_queue_t *wq) {
    pthread_mutex_lock(&wq->mutex);
    int sz = wq->count;
    pthread_mutex_unlock(&wq->mutex);
    return sz;
}

/* Signal shutdown */
void workqueue_shutdown(work_queue_t *wq) {
    pthread_mutex_lock(&wq->mutex);
    wq->shutdown = 1;
    pthread_cond_broadcast(&wq->not_empty);
    pthread_cond_broadcast(&wq->not_full);
    pthread_mutex_unlock(&wq->mutex);
}
