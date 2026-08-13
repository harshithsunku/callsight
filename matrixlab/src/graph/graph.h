#ifndef MATRIXLAB_GRAPH_H
#define MATRIXLAB_GRAPH_H

#include <stddef.h>
#include <stdint.h>

/* Adjacency list graph */
typedef struct graph_edge {
    int dest;
    double weight;
    struct graph_edge *next;
} graph_edge_t;

typedef struct {
    int num_vertices;
    int num_edges;
    graph_edge_t **adj;  /* Array of adjacency lists */
} graph_t;

/* Create/destroy graph */
graph_t *graph_create(int num_vertices);
void graph_destroy(graph_t *g);

/* Add edges */
void graph_add_edge(graph_t *g, int src, int dest, double weight);
void graph_add_edge_undirected(graph_t *g, int src, int dest, double weight);

/* Generate random graph */
graph_t *graph_generate_random(int vertices, int edges, double max_weight);

/* Generate grid graph (for regular structure) */
graph_t *graph_generate_grid(int rows, int cols);

/* Graph properties */
int graph_degree(const graph_t *g, int vertex);
int graph_is_connected(const graph_t *g);

/* Print graph (small) */
void graph_print(const graph_t *g);

#endif
