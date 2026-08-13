#include "bfs.h"
#include <stdlib.h>
#include <string.h>

/* BFS traversal returning count of visited nodes */
__attribute__((noinline))
int bfs_traverse(const graph_t *g, int start, int *visited_order) {
    int *visited = (int *)calloc((size_t)g->num_vertices, sizeof(int));
    int *queue = (int *)malloc((size_t)g->num_vertices * sizeof(int));
    if (!visited || !queue) { free(visited); free(queue); return 0; }

    int front = 0, back = 0, count = 0;
    queue[back++] = start;
    visited[start] = 1;

    while (front < back) {
        int v = queue[front++];
        if (visited_order) visited_order[count] = v;
        count++;

        graph_edge_t *e = g->adj[v];
        while (e) {
            if (!visited[e->dest]) {
                visited[e->dest] = 1;
                queue[back++] = e->dest;
            }
            e = e->next;
        }
    }

    free(visited);
    free(queue);
    return count;
}

/* BFS shortest path (unweighted) */
__attribute__((noinline))
int bfs_shortest_path(const graph_t *g, int start, int end, int *path) {
    int *visited = (int *)calloc((size_t)g->num_vertices, sizeof(int));
    int *parent = (int *)malloc((size_t)g->num_vertices * sizeof(int));
    int *queue = (int *)malloc((size_t)g->num_vertices * sizeof(int));
    if (!visited || !parent || !queue) {
        free(visited); free(parent); free(queue); return -1;
    }

    memset(parent, -1, (size_t)g->num_vertices * sizeof(int));
    int front = 0, back = 0;
    queue[back++] = start;
    visited[start] = 1;
    int found = 0;

    while (front < back && !found) {
        int v = queue[front++];
        graph_edge_t *e = g->adj[v];
        while (e) {
            if (!visited[e->dest]) {
                visited[e->dest] = 1;
                parent[e->dest] = v;
                queue[back++] = e->dest;
                if (e->dest == end) { found = 1; break; }
            }
            e = e->next;
        }
    }

    int path_len = -1;
    if (found && path) {
        /* Reconstruct path */
        int len = 0;
        int v = end;
        while (v != -1 && len < g->num_vertices) {
            path[len++] = v;
            v = parent[v];
        }
        /* Reverse path */
        for (int i = 0; i < len / 2; i++) {
            int tmp = path[i];
            path[i] = path[len - 1 - i];
            path[len - 1 - i] = tmp;
        }
        path_len = len;
    }

    free(visited); free(parent); free(queue);
    return path_len;
}

/* BFS distances from start */
__attribute__((noinline))
void bfs_distances(const graph_t *g, int start, int *distances) {
    int *queue = (int *)malloc((size_t)g->num_vertices * sizeof(int));
    if (!queue) return;

    memset(distances, -1, (size_t)g->num_vertices * sizeof(int));
    distances[start] = 0;
    int front = 0, back = 0;
    queue[back++] = start;

    while (front < back) {
        int v = queue[front++];
        graph_edge_t *e = g->adj[v];
        while (e) {
            if (distances[e->dest] == -1) {
                distances[e->dest] = distances[v] + 1;
                queue[back++] = e->dest;
            }
            e = e->next;
        }
    }
    free(queue);
}

/* Multi-source BFS */
__attribute__((noinline))
void bfs_multi_source(const graph_t *g, const int *sources, int nsources, int *distances) {
    int *queue = (int *)malloc((size_t)g->num_vertices * sizeof(int));
    if (!queue) return;

    memset(distances, -1, (size_t)g->num_vertices * sizeof(int));
    int front = 0, back = 0;

    for (int i = 0; i < nsources; i++) {
        if (sources[i] >= 0 && sources[i] < g->num_vertices) {
            distances[sources[i]] = 0;
            queue[back++] = sources[i];
        }
    }

    while (front < back) {
        int v = queue[front++];
        graph_edge_t *e = g->adj[v];
        while (e) {
            if (distances[e->dest] == -1) {
                distances[e->dest] = distances[v] + 1;
                queue[back++] = e->dest;
            }
            e = e->next;
        }
    }
    free(queue);
}
