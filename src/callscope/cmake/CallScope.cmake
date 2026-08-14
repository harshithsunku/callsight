# CallScope.cmake — compile-time function tracing for CMake targets.
#
# Usage in CMakeLists.txt, after the target is defined:
#
#     list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/callscope")
#     include(CallScope)
#     callscope_instrument(<target>)
#
# Instrumented build (normal builds are untouched):
#
#     cmake -DCALLSCOPE_INSTRUMENT=ON -B build-instr
#     cmake --build build-instr
#
# Cache variables:
#   CALLSCOPE_INSTRUMENT  ON/OFF — apply hooks to callscope_instrument() targets
#   CALLSCOPE_CONFIG      selection config (default: <source dir>/trace.config)
#   CALLSCOPE_COMMAND     command that prints the flags (default: callscope;
#                        may be a ;-list, e.g. "python3;/path/to/flags.py")
#
# The compile options and -no-pie are only applied when CALLSCOPE_INSTRUMENT=ON.
# -no-pie keeps runtime addresses equal to link addresses so `callscope
# analyze` can resolve symbols with addr2line directly.

option(CALLSCOPE_INSTRUMENT "Build with callscope compile-time tracing" OFF)
set(CALLSCOPE_CONFIG "${CMAKE_CURRENT_SOURCE_DIR}/trace.config"
    CACHE FILEPATH "callscope selection config")
set(CALLSCOPE_COMMAND "callscope"
    CACHE STRING "command used to generate instrumentation flags")

function(callscope_instrument target)
    if(NOT CALLSCOPE_INSTRUMENT)
        return()
    endif()
    if(NOT EXISTS "${CALLSCOPE_CONFIG}")
        message(FATAL_ERROR "callscope: config not found: ${CALLSCOPE_CONFIG}")
    endif()

    # Sources as the compiler sees them (relative to the target's source dir).
    get_target_property(_srcs ${target} SOURCES)
    execute_process(
        COMMAND ${CALLSCOPE_COMMAND} flags --format raw
                --config "${CALLSCOPE_CONFIG}" -- ${_srcs}
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
        OUTPUT_VARIABLE _flags
        ERROR_VARIABLE _flags_err
        RESULT_VARIABLE _flags_rc
        OUTPUT_STRIP_TRAILING_WHITESPACE)
    if(NOT _flags_rc EQUAL 0)
        message(FATAL_ERROR "callscope: flag generation failed (rc=${_flags_rc})\n"
                "command: ${CALLSCOPE_COMMAND} flags --format raw "
                "--config ${CALLSCOPE_CONFIG} -- ${_srcs}\n${_flags_err}")
    endif()
    separate_arguments(_flags)
    target_compile_options(${target} PRIVATE ${_flags})

    # Hook runtime, compiled WITHOUT instrumentation so the hooks cannot
    # recurse. Lives next to this file (callscope init layout) or in the
    # sibling runtime/ dir (callscope source tree layout).
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
    message(STATUS "callscope: instrumenting target ${target}")
endfunction()
