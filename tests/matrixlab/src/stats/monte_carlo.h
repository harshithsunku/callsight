#ifndef MATRIXLAB_MONTE_CARLO_H
#define MATRIXLAB_MONTE_CARLO_H

#include <stddef.h>

/* Monte Carlo result */
typedef struct {
    double estimate;
    double error;
    size_t samples;
} mc_result_t;

/* Estimate pi using Monte Carlo circle method */
__attribute__((noinline))
mc_result_t monte_carlo_pi(size_t samples);

/* Monte Carlo integration of f(x) over [a, b] */
typedef double (*mc_function_t)(double x);
__attribute__((noinline))
mc_result_t monte_carlo_integrate_1d(mc_function_t f, double a, double b, size_t samples);

/* 2D Monte Carlo integration */
typedef double (*mc_function_2d_t)(double x, double y);
__attribute__((noinline))
mc_result_t monte_carlo_integrate_2d(mc_function_2d_t f, double x0, double x1,
                                       double y0, double y1, size_t samples);

/* Random walk simulation */
__attribute__((noinline))
double monte_carlo_random_walk(int steps, int trials);

/* Option pricing (simplified Black-Scholes MC) */
__attribute__((noinline))
mc_result_t monte_carlo_option_price(double S0, double K, double r, double sigma,
                                       double T, size_t paths);

/* Buffon's needle simulation */
__attribute__((noinline))
mc_result_t monte_carlo_buffon(size_t tosses);

/* Portfolio risk simulation */
__attribute__((noinline))
mc_result_t monte_carlo_var(const double *returns, size_t n, size_t simulations, double confidence);

#endif
