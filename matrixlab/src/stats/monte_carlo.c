#include "monte_carlo.h"
#include "../utils/rng.h"
#include "statistics.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

/* Estimate pi using Monte Carlo circle method */
__attribute__((noinline))
mc_result_t monte_carlo_pi(size_t samples) {
    mc_result_t result = {0};
    size_t inside = 0;

    for (size_t i = 0; i < samples; i++) {
        double x = rng_next_double() * 2.0 - 1.0;
        double y = rng_next_double() * 2.0 - 1.0;
        if (x * x + y * y <= 1.0) inside++;
    }

    result.estimate = 4.0 * (double)inside / (double)samples;
    result.error = fabs(result.estimate - 3.14159265358979323846);
    result.samples = samples;
    return result;
}

/* 1D Monte Carlo integration */
__attribute__((noinline))
mc_result_t monte_carlo_integrate_1d(mc_function_t f, double a, double b, size_t samples) {
    mc_result_t result = {0};
    double sum = 0.0;
    double sum2 = 0.0;

    for (size_t i = 0; i < samples; i++) {
        double x = rng_next_range(a, b);
        double fx = f(x);
        sum += fx;
        sum2 += fx * fx;
    }

    double width = b - a;
    result.estimate = width * sum / (double)samples;
    double mean = sum / (double)samples;
    double var = sum2 / (double)samples - mean * mean;
    result.error = width * sqrt(var / (double)samples);
    result.samples = samples;
    return result;
}

/* 2D Monte Carlo integration */
__attribute__((noinline))
mc_result_t monte_carlo_integrate_2d(mc_function_2d_t f, double x0, double x1,
                                       double y0, double y1, size_t samples) {
    mc_result_t result = {0};
    double sum = 0.0;
    double sum2 = 0.0;

    for (size_t i = 0; i < samples; i++) {
        double x = rng_next_range(x0, x1);
        double y = rng_next_range(y0, y1);
        double fxy = f(x, y);
        sum += fxy;
        sum2 += fxy * fxy;
    }

    double area = (x1 - x0) * (y1 - y0);
    result.estimate = area * sum / (double)samples;
    double mean = sum / (double)samples;
    double var = sum2 / (double)samples - mean * mean;
    result.error = area * sqrt(var / (double)samples);
    result.samples = samples;
    return result;
}

/* Random walk: returns average displacement */
__attribute__((noinline))
double monte_carlo_random_walk(int steps, int trials) {
    double total_displacement = 0.0;

    for (int t = 0; t < trials; t++) {
        double x = 0.0, y = 0.0;
        for (int s = 0; s < steps; s++) {
            double angle = rng_next_double() * 2.0 * 3.14159265358979323846;
            x += cos(angle);
            y += sin(angle);
        }
        total_displacement += sqrt(x * x + y * y);
    }

    return total_displacement / (double)trials;
}

/* Simplified option pricing via MC */
__attribute__((noinline))
mc_result_t monte_carlo_option_price(double S0, double K, double r, double sigma,
                                       double T, size_t paths) {
    mc_result_t result = {0};
    double sum = 0.0;
    double sum2 = 0.0;
    double dt = T;

    for (size_t i = 0; i < paths; i++) {
        double z = rng_next_gaussian(0.0, 1.0);
        double ST = S0 * exp((r - 0.5 * sigma * sigma) * dt + sigma * sqrt(dt) * z);
        double payoff = ST > K ? ST - K : 0.0;
        sum += payoff;
        sum2 += payoff * payoff;
    }

    double discount = exp(-r * T);
    result.estimate = discount * sum / (double)paths;
    double mean = sum / (double)paths;
    double var = sum2 / (double)paths - mean * mean;
    result.error = discount * sqrt(var / (double)paths);
    result.samples = paths;
    return result;
}

/* Buffon's needle - estimate pi */
__attribute__((noinline))
mc_result_t monte_carlo_buffon(size_t tosses) {
    mc_result_t result = {0};
    size_t crossings = 0;
    double L = 0.8; /* Needle length */
    double D = 1.0; /* Line spacing */

    for (size_t i = 0; i < tosses; i++) {
        double center = rng_next_double() * D / 2.0;
        double angle = rng_next_double() * 3.14159265358979323846;
        double half_projection = L * sin(angle) / 2.0;
        if (center <= half_projection) crossings++;
    }

    if (crossings > 0) {
        result.estimate = (2.0 * L * (double)tosses) / (D * (double)crossings);
    }
    result.error = fabs(result.estimate - 3.14159265358979323846);
    result.samples = tosses;
    return result;
}

/* Portfolio VaR (Value at Risk) simulation */
__attribute__((noinline))
mc_result_t monte_carlo_var(const double *returns, size_t n, size_t simulations, double confidence) {
    mc_result_t result = {0};
    if (n == 0 || simulations == 0) return result;

    double mu = stats_mean(returns, n);
    double sigma = stats_stddev(returns, n);

    double *sim_returns = (double *)malloc(simulations * sizeof(double));
    if (!sim_returns) return result;

    for (size_t i = 0; i < simulations; i++) {
        sim_returns[i] = rng_next_gaussian(mu, sigma);
    }

    /* Sort ascending to find VaR */
    for (size_t i = 1; i < simulations; i++) {
        double key = sim_returns[i];
        size_t j = i;
        while (j > 0 && sim_returns[j - 1] > key) {
            sim_returns[j] = sim_returns[j - 1];
            j--;
        }
        sim_returns[j] = key;
    }

    size_t var_index = (size_t)((1.0 - confidence) * (double)simulations);
    result.estimate = sim_returns[var_index];
    result.samples = simulations;
    result.error = sigma / sqrt((double)simulations);

    free(sim_returns);
    return result;
}
