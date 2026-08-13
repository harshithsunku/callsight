#ifndef MATRIXLAB_DFS_H
#define MATRIXLAB_DFS_H

#include "graph.h"

/* DFS traversal (recursive, deep call stacks) */
__attribute__((noinline))
int dfs_traverse(const graph_t *g, int start, int *visited_order);

/* DFS iterative (stack-based) */
__attribute__((noinline))
int dfs_iterative(const graph_t *g, int start, int *visited_order);

/* Topological sort (DFS-based) */
__attribute__((noinline))
int dfs_topological_sort(const graph_t *g, int *order);

/* Detect cycle using DFS */
__attribute__((noinline))
int dfs_has_cycle(const graph_t *g);

/* Find connected components */
__attribute__((noinline))
int dfs_connected_components(const graph_t *g, int *component);

#endif
