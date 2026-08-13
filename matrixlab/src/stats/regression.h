#ifndef MATRIXLAB_REGRESSION_H
#define MATRIXLAB_REGRESSION_H

#include <stddef.h>

/* Linear regression result */
typedef struct {
    double slope;
    double intercept;
    double r_squared;
    double mse;
} regression_result_t;

/* Simple linear regression */
__attribute__((noinline))
regression_result_t regression_linear(const double *x, const double *y, size_t n);

/* Polynomial regression of degree k */
__attribute__((noinline))
void regression_polynomial(const double *x, const double *y, size_t n, int degree,
                            double *coefficients);

/* Logistic regression (gradient descent, 1D) */
__attribute__((noinline))
void regression_logistic(const double *x, const int *y, size_t n,
                          double *w, double *b, int iterations, double lr);

/* Predict using linear model */
static inline double regression_predict_linear(const regression_result_t *r, double x) {
    return r->slope * x + r->intercept;
}

/* Predict using polynomial coefficients */
__attribute__((noinline))
double regression_predict_poly(const double *coefficients, int degree, double x);

/* Ridge regression (L2 regularized) */
__attribute__((noinline))
regression_result_t regression_ridge(const double *x, const double *y, size_t n, double lambda);

/* Compute residuals */
void regression_residuals(const double *x, const double *y, size_t n,
                           const regression_result_t *model, double *residuals);

/* Compute R-squared */
double regression_r_squared(const double *y, const double *predicted, size_t n);

#endif
