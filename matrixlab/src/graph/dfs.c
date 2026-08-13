#include "dfs.h"
#include <stdlib.h>
#include <string.h>

/* Recursive DFS helper (deep call stacks for profiling) */
__attribute__((noinline))
static void dfs_visit(const graph_t *g, int v, int *visited, int *order, int *count) {
    visited[v] = 1;
    order[*count] = v;
    (*count)++;

    graph_edge_t *e = g->adj[v];
    while (e) {
        if (!visited[e->dest]) {
            dfs_visit(g, e->dest, visited, order, count);
        }
        e = e->next;
    }
}

/* DFS traversal (recursive) */
__attribute__((noinline))
int dfs_traverse(const graph_t *g, int start, int *visited_order) {
    int *visited = (int *)calloc((size_t)g->num_vertices, sizeof(int));
    if (!visited) return 0;

    int count = 0;
    dfs_visit(g, start, visited, visited_order, &count);

    free(visited);
    return count;
}

/* DFS iterative using explicit stack */
__attribute__((noinline))
int dfs_iterative(const graph_t *g, int start, int *visited_order) {
    int *visited = (int *)calloc((size_t)g->num_vertices, sizeof(int));
    int *stack = (int *)malloc((size_t)g->num_vertices * sizeof(int));
    if (!visited || !stack) { free(visited); free(stack); return 0; }

    int top = 0, count = 0;
    stack[top++] = start;

    while (top > 0) {
        int v = stack[--top];
        if (visited[v]) continue;
        visited[v] = 1;
        visited_order[count++] = v;

        graph_edge_t *e = g->adj[v];
        while (e) {
            if (!visited[e->dest]) {
                stack[top++] = e->dest;
            }
            e = e->next;
        }
    }

    free(visited);
    free(stack);
    return count;
}

/* Topological sort DFS helper */
__attribute__((noinline))
static void topo_dfs(const graph_t *g, int v, int *visited, int *stack, int *top) {
    visited[v] = 1;

    graph_edge_t *e = g->adj[v];
    while (e) {
        if (!visited[e->dest]) {
            topo_dfs(g, e->dest, visited, stack, top);
        }
        e = e->next;
    }

    stack[(*top)++] = v;
}

/* Topological sort */
__attribute__((noinline))
int dfs_topological_sort(const graph_t *g, int *order) {
    int *visited = (int *)calloc((size_t)g->num_vertices, sizeof(int));
    int *stack = (int *)malloc((size_t)g->num_vertices * sizeof(int));
    if (!visited || !stack) { free(visited); free(stack); return -1; }

    int top = 0;
    for (int i = 0; i < g->num_vertices; i++) {
        if (!visited[i]) {
            topo_dfs(g, i, visited, stack, &top);
        }
    }

    /* Reverse order */
    for (int i = 0; i < top; i++) {
        order[i] = stack[top - 1 - i];
    }

    free(visited);
    free(stack);
    return top;
}

/* Cycle detection DFS helper */
__attribute__((noinline))
static int cycle_dfs(const graph_t *g, int v, int *color) {
    color[v] = 1; /* Gray (in progress) */

    graph_edge_t *e = g->adj[v];
    while (e) {
        if (color[e->dest] == 1) return 1; /* Back edge = cycle */
        if (color[e->dest] == 0 && cycle_dfs(g, e->dest, color)) return 1;
        e = e->next;
    }

    color[v] = 2; /* Black (done) */
    return 0;
}

/* Detect cycle */
__attribute__((noinline))
int dfs_has_cycle(const graph_t *g) {
    int *color = (int *)calloc((size_t)g->num_vertices, sizeof(int));
    if (!color) return 0;

    for (int i = 0; i < g->num_vertices; i++) {
        if (color[i] == 0 && cycle_dfs(g, i, color)) {
            free(color);
            return 1;
        }
    }

    free(color);
    return 0;
}

/* Find connected components */
__attribute__((noinline))
int dfs_connected_components(const graph_t *g, int *component) {
    memset(component, -1, (size_t)g->num_vertices * sizeof(int));
    int num_components = 0;

    int *order = (int *)malloc((size_t)g->num_vertices * sizeof(int));
    int *visited = (int *)calloc((size_t)g->num_vertices, sizeof(int));
    if (!order || !visited) { free(order); free(visited); return 0; }

    for (int i = 0; i < g->num_vertices; i++) {
        if (!visited[i]) {
            int count = 0;
            /* DFS from this vertex */
            int *stack = (int *)malloc((size_t)g->num_vertices * sizeof(int));
            if (!stack) break;
            int top = 0;
            stack[top++] = i;

            while (top > 0) {
                int v = stack[--top];
                if (visited[v]) continue;
                visited[v] = 1;
                component[v] = num_components;
                count++;

                graph_edge_t *e = g->adj[v];
                while (e) {
                    if (!visited[e->dest]) stack[top++] = e->dest;
                    e = e->next;
                }
            }
            free(stack);
            num_components++;
        }
    }

    free(order);
    free(visited);
    return num_components;
}
