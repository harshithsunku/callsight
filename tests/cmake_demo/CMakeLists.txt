cmake_minimum_required(VERSION 3.17)
project(cmake_demo C)

# Tiny fixture proving the CallSight.cmake integration: configures an
# instrumented build with -DCALLSIGHT_INSTRUMENT=ON, normal builds untouched.

set(CMAKE_C_STANDARD 11)

add_executable(demo main.c helper.c)
target_compile_options(demo PRIVATE -g -Wall -Wextra)

# In-repo wiring: use the toolkit straight from the source tree. A project
# adopted via `callsight init` would instead point CMAKE_MODULE_PATH at its
# local callsight/ copy. CALLSIGHT_COMMAND is a cache variable — pass it on
# the command line: -D"CALLSIGHT_COMMAND=python3;<repo>/src/callsight/cli.py"
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../../src/callsight/cmake")
include(CallSight)
callsight_instrument(demo)
