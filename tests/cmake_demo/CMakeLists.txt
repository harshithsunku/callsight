cmake_minimum_required(VERSION 3.17)
project(cmake_demo C)

# Tiny fixture proving the TraceKit.cmake integration: configures an
# instrumented build with -DTRACEKIT_INSTRUMENT=ON, normal builds untouched.

set(CMAKE_C_STANDARD 11)

add_executable(demo main.c helper.c)
target_compile_options(demo PRIVATE -g -Wall -Wextra)

# In-repo wiring: use the toolkit straight from the source tree. A project
# adopted via `tracekit init` would instead point CMAKE_MODULE_PATH at its
# local tracekit/ copy. TRACEKIT_COMMAND is a cache variable — pass it on
# the command line: -D"TRACEKIT_COMMAND=python3;<repo>/src/tracekit/cli.py"
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../../src/tracekit/cmake")
include(TraceKit)
tracekit_instrument(demo)
