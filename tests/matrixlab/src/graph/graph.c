#include "graph.h"
#include "../utils/rng.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

/* Create a graph with given vertices */
graph_t *graph_create(int num_vertices) {
    graph_t *g = (graph_t *)malloc(sizeof(graph_t));
    if (!g) return NULL;
    g->num_vertices = num_vertices;
    g->num_edges = 0;
    g->adj = (graph_edge_t **)calloc((size_t)num_vertices, sizeof(graph_edge_t *));
    if (!g->adj) { free(g); return NULL; }
    return g;
}

/* Destroy a graph */
void graph_destroy(graph_t *g) {
    if (!g) return;
    for (int i = 0; i < g->num_vertices; i++) {
        graph_edge_t *e = g->adj[i];
        while (e) {
            graph_edge_t *next = e->next;
            free(e);
            e = next;
        }
    }
    free(g->adj);
    free(g);
}

/* Add directed edge */
void graph_add_edge(graph_t *g, int src, int dest, double weight) {
    if (src < 0 || src >= g->num_vertices || dest < 0 || dest >= g->num_vertices) return;

    graph_edge_t *e = (graph_edge_t *)malloc(sizeof(graph_edge_t));
    if (!e) return;
    e->dest = dest;
    e->weight = weight;
    e->next = g->adj[src];
    g->adj[src] = e;
    g->num_edges++;
}

/* Add undirected edge */
void graph_add_edge_undirected(graph_t *g, int src, int dest, double weight) {
    graph_add_edge(g, src, dest, weight);
    graph_add_edge(g, dest, src, weight);
}

/* Generate random graph */
graph_t *graph_generate_random(int vertices, int edges, double max_weight) {
    graph_t *g = graph_create(vertices);
    if (!g) return NULL;

    for (int i = 0; i < edges; i++) {
        int src = rng_next_int(0, vertices);
        int dest = rng_next_int(0, vertices);
        if (src == dest) continue;
        double w = rng_next_range(0.1, max_weight);
        graph_add_edge(g, src, dest, w);
    }
    return g;
}

/* Generate grid graph */
graph_t *graph_generate_grid(int rows, int cols) {
    graph_t *g = graph_create(rows * cols);
    if (!g) return NULL;

    for (int r = 0; r < rows; r++) {
        for (int c = 0; c < cols; c++) {
            int v = r * cols + c;
            if (c + 1 < cols)
                graph_add_edge_undirected(g, v, v + 1, rng_next_range(1.0, 10.0));
            if (r + 1 < rows)
                graph_add_edge_undirected(g, v, v + cols, rng_next_range(1.0, 10.0));
        }
    }
    return g;
}

/* Get degree of a vertex */
int graph_degree(const graph_t *g, int vertex) {
    int count = 0;
    graph_edge_t *e = g->adj[vertex];
    while (e) { count++; e = e->next; }
    return count;
}

/* BFS-based connectivity check */
int graph_is_connected(const graph_t *g) {
    if (g->num_vertices == 0) return 1;

    int *visited = (int *)calloc((size_t)g->num_vertices, sizeof(int));
    int *queue = (int *)malloc((size_t)g->num_vertices * sizeof(int));
    if (!visited || !queue) { free(visited); free(queue); return 0; }

    int front = 0, back = 0;
    queue[back++] = 0;
    visited[0] = 1;
    int count = 1;

    while (front < back) {
        int v = queue[front++];
        graph_edge_t *e = g->adj[v];
        while (e) {
            if (!visited[e->dest]) {
                visited[e->dest] = 1;
                queue[back++] = e->dest;
                count++;
            }
            e = e->next;
        }
    }

    free(visited);
    free(queue);
    return count == g->num_vertices;
}

/* Print graph adjacency lists */
void graph_print(const graph_t *g) {
    int limit = g->num_vertices < 20 ? g->num_vertices : 20;
    for (int i = 0; i < limit; i++) {
        printf("%d: ", i);
        graph_edge_t *e = g->adj[i];
        while (e) {
            printf("->%d(%.1f) ", e->dest, e->weight);
            e = e->next;
        }
        printf("\n");
    }
    if (limit < g->num_vertices) printf("...\n");
}
