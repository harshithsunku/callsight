#include "regression.h"
#include "statistics.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

/* Simple linear regression using least squares */
__attribute__((noinline))
regression_result_t regression_linear(const double *x, const double *y, size_t n) {
    regression_result_t result = {0};
    if (n < 2) return result;

    double sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (size_t i = 0; i < n; i++) {
        sx += x[i];
        sy += y[i];
        sxx += x[i] * x[i];
        sxy += x[i] * y[i];
    }

    double dn = (double)n;
    double denom = dn * sxx - sx * sx;
    if (fabs(denom) < 1e-12) return result;

    result.slope = (dn * sxy - sx * sy) / denom;
    result.intercept = (sy - result.slope * sx) / dn;

    /* Compute R-squared */
    double my = sy / dn;
    double ss_tot = 0, ss_res = 0;
    for (size_t i = 0; i < n; i++) {
        double pred = result.slope * x[i] + result.intercept;
        ss_res += (y[i] - pred) * (y[i] - pred);
        ss_tot += (y[i] - my) * (y[i] - my);
    }
    result.r_squared = ss_tot > 0 ? 1.0 - ss_res / ss_tot : 0.0;
    result.mse = ss_res / dn;

    return result;
}

/* Polynomial regression using normal equations */
__attribute__((noinline))
void regression_polynomial(const double *x, const double *y, size_t n, int degree,
                            double *coefficients) {
    int m = degree + 1;
    /* Build Vandermonde-style normal equations */
    double *A = (double *)calloc((size_t)(m * m), sizeof(double));
    double *b = (double *)calloc((size_t)m, sizeof(double));
    if (!A || !b) { free(A); free(b); return; }

    for (size_t i = 0; i < n; i++) {
        double xi = 1.0;
        for (int j = 0; j < m; j++) {
            double xij = xi;
            for (int k = 0; k < m; k++) {
                A[j * m + k] += xij;
                xij *= x[i];
            }
            b[j] += xi * y[i];
            xi *= x[i];
        }
    }

    /* Gaussian elimination */
    for (int col = 0; col < m; col++) {
        /* Find pivot */
        int max_row = col;
        double max_val = fabs(A[col * m + col]);
        for (int row = col + 1; row < m; row++) {
            double v = fabs(A[row * m + col]);
            if (v > max_val) { max_val = v; max_row = row; }
        }

        /* Swap rows */
        if (max_row != col) {
            for (int k = 0; k < m; k++) {
                double tmp = A[col * m + k];
                A[col * m + k] = A[max_row * m + k];
                A[max_row * m + k] = tmp;
            }
            double tmp = b[col]; b[col] = b[max_row]; b[max_row] = tmp;
        }

        /* Eliminate */
        if (fabs(A[col * m + col]) < 1e-12) continue;
        for (int row = col + 1; row < m; row++) {
            double factor = A[row * m + col] / A[col * m + col];
            for (int k = col; k < m; k++) {
                A[row * m + k] -= factor * A[col * m + k];
            }
            b[row] -= factor * b[col];
        }
    }

    /* Back substitution */
    for (int i = m - 1; i >= 0; i--) {
        coefficients[i] = b[i];
        for (int j = i + 1; j < m; j++) {
            coefficients[i] -= A[i * m + j] * coefficients[j];
        }
        if (fabs(A[i * m + i]) > 1e-12) {
            coefficients[i] /= A[i * m + i];
        }
    }

    free(A);
    free(b);
}

/* Sigmoid function */
static inline double sigmoid(double z) {
    if (z > 500.0) return 1.0;
    if (z < -500.0) return 0.0;
    return 1.0 / (1.0 + exp(-z));
}

/* Logistic regression via gradient descent */
__attribute__((noinline))
void regression_logistic(const double *x, const int *y, size_t n,
                          double *w, double *b, int iterations, double lr) {
    *w = 0.0;
    *b = 0.0;

    for (int iter = 0; iter < iterations; iter++) {
        double dw = 0.0, db = 0.0;
        for (size_t i = 0; i < n; i++) {
            double pred = sigmoid((*w) * x[i] + (*b));
            double err = pred - (double)y[i];
            dw += err * x[i];
            db += err;
        }
        *w -= lr * dw / (double)n;
        *b -= lr * db / (double)n;
    }
}

/* Evaluate polynomial at point x */
__attribute__((noinline))
double regression_predict_poly(const double *coefficients, int degree, double x) {
    double result = 0.0;
    double xi = 1.0;
    for (int i = 0; i <= degree; i++) {
        result += coefficients[i] * xi;
        xi *= x;
    }
    return result;
}

/* Ridge regression (L2 regularized linear regression) */
__attribute__((noinline))
regression_result_t regression_ridge(const double *x, const double *y, size_t n, double lambda) {
    regression_result_t result = {0};
    if (n < 2) return result;

    double sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (size_t i = 0; i < n; i++) {
        sx += x[i]; sy += y[i];
        sxx += x[i] * x[i]; sxy += x[i] * y[i];
    }

    double dn = (double)n;
    double denom = (sxx - sx * sx / dn) + lambda;
    if (fabs(denom) < 1e-12) return result;

    result.slope = (sxy - sx * sy / dn) / denom;
    result.intercept = (sy - result.slope * sx) / dn;

    double my = sy / dn;
    double ss_tot = 0, ss_res = 0;
    for (size_t i = 0; i < n; i++) {
        double pred = result.slope * x[i] + result.intercept;
        ss_res += (y[i] - pred) * (y[i] - pred);
        ss_tot += (y[i] - my) * (y[i] - my);
    }
    result.r_squared = ss_tot > 0 ? 1.0 - ss_res / ss_tot : 0.0;
    result.mse = ss_res / dn;

    return result;
}

/* Compute residuals */
void regression_residuals(const double *x, const double *y, size_t n,
                           const regression_result_t *model, double *residuals) {
    for (size_t i = 0; i < n; i++) {
        double pred = model->slope * x[i] + model->intercept;
        residuals[i] = y[i] - pred;
    }
}

/* Compute R-squared from actual and predicted */
double regression_r_squared(const double *y, const double *predicted, size_t n) {
    double my = stats_mean(y, n);
    double ss_tot = 0, ss_res = 0;
    for (size_t i = 0; i < n; i++) {
        ss_tot += (y[i] - my) * (y[i] - my);
        ss_res += (y[i] - predicted[i]) * (y[i] - predicted[i]);
    }
    return ss_tot > 0 ? 1.0 - ss_res / ss_tot : 0.0;
}
