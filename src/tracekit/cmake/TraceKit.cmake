# TraceKit.cmake — compile-time function tracing for CMake targets.
#
# Usage in CMakeLists.txt, after the target is defined:
#
#     list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/tracekit")
#     include(TraceKit)
#     tracekit_instrument(<target>)
#
# Instrumented build (normal builds are untouched):
#
#     cmake -DTRACEKIT_INSTRUMENT=ON -B build-instr
#     cmake --build build-instr
#
# Cache variables:
#   TRACEKIT_INSTRUMENT  ON/OFF — apply hooks to tracekit_instrument() targets
#   TRACEKIT_CONFIG      selection config (default: <source dir>/trace.config)
#   TRACEKIT_COMMAND     command that prints the flags (default: tracekit;
#                        may be a ;-list, e.g. "python3;/path/to/flags.py")
#
# The compile options and -no-pie are only applied when TRACEKIT_INSTRUMENT=ON.
# -no-pie keeps runtime addresses equal to link addresses so `tracekit
# analyze` can resolve symbols with addr2line directly.

option(TRACEKIT_INSTRUMENT "Build with tracekit compile-time tracing" OFF)
set(TRACEKIT_CONFIG "${CMAKE_CURRENT_SOURCE_DIR}/trace.config"
    CACHE FILEPATH "tracekit selection config")
set(TRACEKIT_COMMAND "tracekit"
    CACHE STRING "command used to generate instrumentation flags")

function(tracekit_instrument target)
    if(NOT TRACEKIT_INSTRUMENT)
        return()
    endif()
    if(NOT EXISTS "${TRACEKIT_CONFIG}")
        message(FATAL_ERROR "tracekit: config not found: ${TRACEKIT_CONFIG}")
    endif()

    # Sources as the compiler sees them (relative to the target's source dir).
    get_target_property(_srcs ${target} SOURCES)
    execute_process(
        COMMAND ${TRACEKIT_COMMAND} flags --format raw
                --config "${TRACEKIT_CONFIG}" -- ${_srcs}
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
        OUTPUT_VARIABLE _flags
        ERROR_VARIABLE _flags_err
        RESULT_VARIABLE _flags_rc
        OUTPUT_STRIP_TRAILING_WHITESPACE)
    if(NOT _flags_rc EQUAL 0)
        message(FATAL_ERROR "tracekit: flag generation failed (rc=${_flags_rc})\n"
                "command: ${TRACEKIT_COMMAND} flags --format raw "
                "--config ${TRACEKIT_CONFIG} -- ${_srcs}\n${_flags_err}")
    endif()
    separate_arguments(_flags)
    target_compile_options(${target} PRIVATE ${_flags})

    # Hook runtime, compiled WITHOUT instrumentation so the hooks cannot
    # recurse. Lives next to this file (tracekit init layout) or in the
    # sibling runtime/ dir (tracekit source tree layout).
    if(EXISTS "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/trace.c")
        set(_runtime "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/trace.c")
    else()
        get_filename_component(_runtime
            "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../runtime/trace.c" ABSOLUTE)
    endif()
    target_sources(${target} PRIVATE "${_runtime}")
    set_source_files_properties("${_runtime}"
        PROPERTIES COMPILE_OPTIONS "-fno-instrument-functions")

    target_link_options(${target} PRIVATE -no-pie)
    message(STATUS "tracekit: instrumenting target ${target}")
endfunction()
