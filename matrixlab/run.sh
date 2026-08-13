#!/bin/bash
# MatrixLab launcher script
#
# Environment variables:
#   MATRIXLAB_THREADS     Number of threads (default: 25)
#   MATRIXLAB_THROTTLE_US Sleep between iterations in microseconds (default: 1000)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BINARY="$SCRIPT_DIR/bin/matrixlab.debug"

# Fall back to other variants
if [ ! -f "$BINARY" ]; then
    BINARY="$SCRIPT_DIR/bin/matrixlab.sym"
fi
if [ ! -f "$BINARY" ]; then
    BINARY="$SCRIPT_DIR/bin/matrixlab.release"
fi
if [ ! -f "$BINARY" ]; then
    echo "[MatrixLab] Binary not found. Run 'make' first."
    exit 1
fi

THREADS=${MATRIXLAB_THREADS:-25}
THROTTLE=${MATRIXLAB_THROTTLE_US:-1000}

echo "[MatrixLab] Binary=$BINARY"
echo "[MatrixLab] Threads=$THREADS Throttle=${THROTTLE}us"
echo "[MatrixLab] PID will be printed at startup"
echo ""

export MATRIXLAB_THREADS=$THREADS
export MATRIXLAB_THROTTLE_US=$THROTTLE

exec "$BINARY"
