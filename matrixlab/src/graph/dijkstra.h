#ifndef MATRIXLAB_DIJKSTRA_H
#define MATRIXLAB_DIJKSTRA_H

#include "graph.h"

/* Dijkstra's shortest path (min-heap based) */
__attribute__((noinline))
void dijkstra_shortest_path(const graph_t *g, int start, double *distances, int *parent);

/* Dijkstra's - simple array-based (O(V^2), cache-friendly for dense graphs) */
__attribute__((noinline))
void dijkstra_simple(const graph_t *g, int start, double *distances);

/* All-pairs shortest path (repeated Dijkstra) */
__attribute__((noinline))
void dijkstra_all_pairs(const graph_t *g, double *dist_matrix);

/* Reconstruct path from Dijkstra result */
int dijkstra_reconstruct_path(const int *parent, int start, int end, int *path);

/* Bellman-Ford (handles negative weights) */
__attribute__((noinline))
int bellman_ford(const graph_t *g, int start, double *distances);

#endif
