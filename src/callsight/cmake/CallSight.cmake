# CallSight.cmake — compile-time function tracing for CMake targets.
#
# Usage in CMakeLists.txt, after the target is defined:
#
#     list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/callsight")
#     include(CallSight)
#     callsight_instrument(<target>)
#
# Instrumented build (normal builds are untouched):
#
#     cmake -DCALLSIGHT_INSTRUMENT=ON -B build-instr
#     cmake --build build-instr
#
# Cache variables:
#   CALLSIGHT_INSTRUMENT  ON/OFF — apply hooks to callsight_instrument() targets
#   CALLSIGHT_CONFIG      selection config (default: <source dir>/trace.config)
#   CALLSIGHT_COMMAND     command that prints the flags (default: callsight;
#                        may be a ;-list, e.g. "python3;/path/to/flags.py")
#
# The compile options and -no-pie are only applied when CALLSIGHT_INSTRUMENT=ON.
# -no-pie keeps runtime addresses equal to link addresses so `callsight
# analyze` can resolve symbols with addr2line directly.

option(CALLSIGHT_INSTRUMENT "Build with callsight compile-time tracing" OFF)
set(CALLSIGHT_CONFIG "${CMAKE_CURRENT_SOURCE_DIR}/trace.config"
    CACHE FILEPATH "callsight selection config")
set(CALLSIGHT_COMMAND "callsight"
    CACHE STRING "command used to generate instrumentation flags")

function(callsight_instrument target)
    if(NOT CALLSIGHT_INSTRUMENT)
        return()
    endif()
    if(NOT EXISTS "${CALLSIGHT_CONFIG}")
        message(FATAL_ERROR "callsight: config not found: ${CALLSIGHT_CONFIG}")
    endif()

    # Sources as the compiler sees them (relative to the target's source dir).
    get_target_property(_srcs ${target} SOURCES)
    execute_process(
        COMMAND ${CALLSIGHT_COMMAND} flags --format raw
                --config "${CALLSIGHT_CONFIG}" -- ${_srcs}
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
        OUTPUT_VARIABLE _flags
        ERROR_VARIABLE _flags_err
        RESULT_VARIABLE _flags_rc
        OUTPUT_STRIP_TRAILING_WHITESPACE)
    if(NOT _flags_rc EQUAL 0)
        message(FATAL_ERROR "callsight: flag generation failed (rc=${_flags_rc})\n"
                "command: ${CALLSIGHT_COMMAND} flags --format raw "
                "--config ${CALLSIGHT_CONFIG} -- ${_srcs}\n${_flags_err}")
    endif()
    separate_arguments(_flags)
    target_compile_options(${target} PRIVATE ${_flags})

    # Hook runtime, compiled WITHOUT instrumentation so the hooks cannot
    # recurse. Lives next to this file (callsight init layout) or in the
    # sibling runtime/ dir (callsight source tree layout).
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
    message(STATUS "callsight: instrumenting target ${target}")
endfunction()
