#include "dijkstra.h"
#include <stdlib.h>
#include <string.h>
#include <float.h>

/* Min-heap entry */
typedef struct {
    int vertex;
    double dist;
} heap_entry_t;

/* Min-heap for Dijkstra */
typedef struct {
    heap_entry_t *data;
    int size;
    int capacity;
} min_heap_t;

/* Heap operations */
static min_heap_t *heap_create(int cap) {
    min_heap_t *h = (min_heap_t *)malloc(sizeof(min_heap_t));
    if (!h) return NULL;
    h->data = (heap_entry_t *)malloc((size_t)cap * sizeof(heap_entry_t));
    h->size = 0;
    h->capacity = cap;
    return h;
}

/* Destroy heap */
static void heap_destroy(min_heap_t *h) {
    if (!h) return;
    free(h->data);
    free(h);
}

/* Swap heap entries */
static inline void he_swap(heap_entry_t *a, heap_entry_t *b) {
    heap_entry_t t = *a; *a = *b; *b = t;
}

/* Push to min-heap */
static void heap_push(min_heap_t *h, int vertex, double dist) {
    if (h->size >= h->capacity) return;
    h->data[h->size] = (heap_entry_t){vertex, dist};
    int i = h->size++;
    while (i > 0) {
        int parent = (i - 1) / 2;
        if (h->data[i].dist < h->data[parent].dist) {
            he_swap(&h->data[i], &h->data[parent]);
            i = parent;
        } else break;
    }
}

/* Pop from min-heap */
static heap_entry_t heap_pop(min_heap_t *h) {
    heap_entry_t top = h->data[0];
    h->size--;
    h->data[0] = h->data[h->size];
    int i = 0;
    while (1) {
        int smallest = i;
        int left = 2 * i + 1, right = 2 * i + 2;
        if (left < h->size && h->data[left].dist < h->data[smallest].dist) smallest = left;
        if (right < h->size && h->data[right].dist < h->data[smallest].dist) smallest = right;
        if (smallest == i) break;
        he_swap(&h->data[i], &h->data[smallest]);
        i = smallest;
    }
    return top;
}

/* Dijkstra's shortest path using min-heap */
__attribute__((noinline))
void dijkstra_shortest_path(const graph_t *g, int start, double *distances, int *parent) {
    int n = g->num_vertices;
    for (int i = 0; i < n; i++) {
        distances[i] = DBL_MAX;
        if (parent) parent[i] = -1;
    }
    distances[start] = 0.0;

    min_heap_t *heap = heap_create(n * 2);
    if (!heap) return;
    heap_push(heap, start, 0.0);

    int *visited = (int *)calloc((size_t)n, sizeof(int));
    if (!visited) { heap_destroy(heap); return; }

    while (heap->size > 0) {
        heap_entry_t cur = heap_pop(heap);
        if (visited[cur.vertex]) continue;
        visited[cur.vertex] = 1;

        graph_edge_t *e = g->adj[cur.vertex];
        while (e) {
            double new_dist = distances[cur.vertex] + e->weight;
            if (new_dist < distances[e->dest]) {
                distances[e->dest] = new_dist;
                if (parent) parent[e->dest] = cur.vertex;
                heap_push(heap, e->dest, new_dist);
            }
            e = e->next;
        }
    }

    free(visited);
    heap_destroy(heap);
}

/* Simple O(V^2) Dijkstra */
__attribute__((noinline))
void dijkstra_simple(const graph_t *g, int start, double *distances) {
    int n = g->num_vertices;
    int *visited = (int *)calloc((size_t)n, sizeof(int));
    if (!visited) return;

    for (int i = 0; i < n; i++) distances[i] = DBL_MAX;
    distances[start] = 0.0;

    for (int iter = 0; iter < n; iter++) {
        /* Find unvisited vertex with minimum distance */
        int u = -1;
        double min_dist = DBL_MAX;
        for (int v = 0; v < n; v++) {
            if (!visited[v] && distances[v] < min_dist) {
                min_dist = distances[v];
                u = v;
            }
        }
        if (u == -1) break;
        visited[u] = 1;

        /* Relax neighbors */
        graph_edge_t *e = g->adj[u];
        while (e) {
            double new_dist = distances[u] + e->weight;
            if (new_dist < distances[e->dest]) {
                distances[e->dest] = new_dist;
            }
            e = e->next;
        }
    }
    free(visited);
}

/* All-pairs shortest path */
__attribute__((noinline))
void dijkstra_all_pairs(const graph_t *g, double *dist_matrix) {
    int n = g->num_vertices;
    double *dists = (double *)malloc((size_t)n * sizeof(double));
    if (!dists) return;

    for (int src = 0; src < n; src++) {
        dijkstra_shortest_path(g, src, dists, NULL);
        memcpy(dist_matrix + (size_t)src * (size_t)n, dists, (size_t)n * sizeof(double));
    }
    free(dists);
}

/* Reconstruct path from parent array */
int dijkstra_reconstruct_path(const int *parent, int start, int end, int *path) {
    int len = 0;
    int v = end;
    while (v != -1 && v != start && len < 10000) {
        path[len++] = v;
        v = parent[v];
    }
    if (v == start) path[len++] = start;

    /* Reverse */
    for (int i = 0; i < len / 2; i++) {
        int tmp = path[i];
        path[i] = path[len - 1 - i];
        path[len - 1 - i] = tmp;
    }
    return len;
}

/* Bellman-Ford algorithm */
__attribute__((noinline))
int bellman_ford(const graph_t *g, int start, double *distances) {
    int n = g->num_vertices;
    for (int i = 0; i < n; i++) distances[i] = DBL_MAX;
    distances[start] = 0.0;

    /* Relax all edges V-1 times */
    for (int iter = 0; iter < n - 1; iter++) {
        int changed = 0;
        for (int u = 0; u < n; u++) {
            if (distances[u] == DBL_MAX) continue;
            graph_edge_t *e = g->adj[u];
            while (e) {
                double new_dist = distances[u] + e->weight;
                if (new_dist < distances[e->dest]) {
                    distances[e->dest] = new_dist;
                    changed = 1;
                }
                e = e->next;
            }
        }
        if (!changed) break; /* Early exit */
    }

    /* Check for negative cycle */
    for (int u = 0; u < n; u++) {
        if (distances[u] == DBL_MAX) continue;
        graph_edge_t *e = g->adj[u];
        while (e) {
            if (distances[u] + e->weight < distances[e->dest]) {
                return -1; /* Negative cycle */
            }
            e = e->next;
        }
    }
    return 0;
}
