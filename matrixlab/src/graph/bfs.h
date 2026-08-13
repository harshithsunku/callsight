#ifndef MATRIXLAB_BFS_H
#define MATRIXLAB_BFS_H

#include "graph.h"

/* BFS traversal, returns visited order */
__attribute__((noinline))
int bfs_traverse(const graph_t *g, int start, int *visited_order);

/* BFS shortest path (unweighted) */
__attribute__((noinline))
int bfs_shortest_path(const graph_t *g, int start, int end, int *path);

/* BFS level-order (returns distances) */
__attribute__((noinline))
void bfs_distances(const graph_t *g, int start, int *distances);

/* Multi-source BFS */
__attribute__((noinline))
void bfs_multi_source(const graph_t *g, const int *sources, int nsources, int *distances);

#endif
