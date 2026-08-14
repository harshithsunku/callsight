cmake_minimum_required(VERSION 3.17)
project(cmake_demo C)

# Tiny fixture proving the CallScope.cmake integration: configures an
# instrumented build with -DCALLSCOPE_INSTRUMENT=ON, normal builds untouched.

set(CMAKE_C_STANDARD 11)

add_executable(demo main.c helper.c)
target_compile_options(demo PRIVATE -g -Wall -Wextra)

# In-repo wiring: use the toolkit straight from the source tree. A project
# adopted via `callscope init` would instead point CMAKE_MODULE_PATH at its
# local callscope/ copy. CALLSCOPE_COMMAND is a cache variable — pass it on
# the command line: -D"CALLSCOPE_COMMAND=python3;<repo>/src/callscope/cli.py"
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../../src/callscope/cmake")
include(CallScope)
callscope_instrument(demo)
