# MatrixLab — Multi-threaded numerical computation engine
#
# Usage:
#   make                                # debug build (default)
#   make debug                          # debug build with full symbols
#   make release                        # optimized release build
#   make symbols                        # release with debug symbols
#   make strip                          # stripped binary
#   make all-variants                   # build all variants
#   make CROSS=aarch64-linux-gnu-       # ARM64 cross-compile
#   make CROSS=arm-linux-gnueabihf-    # ARMv7 cross-compile
#   make all-cross                      # all cross targets
#   make clean

# --- Toolchain ---
CC       = $(CROSS)gcc
OBJCOPY  = $(CROSS)objcopy
OBJDUMP  = $(CROSS)objdump
STRIP    = $(CROSS)strip

# --- Directories ---
SRCDIR   = src
BUILDDIR = build
BINDIR   = bin

# --- Output names ---
TARGET   = matrixlab

# --- Sources ---
SRCS = \
    $(SRCDIR)/main.c \
    $(SRCDIR)/workloads.c \
    $(SRCDIR)/core/memory_pool.c \
    $(SRCDIR)/core/arena.c \
    $(SRCDIR)/core/logging.c \
    $(SRCDIR)/core/errors.c \
    $(SRCDIR)/matrix/matrix_ops.c \
    $(SRCDIR)/matrix/matrix_multiply.c \
    $(SRCDIR)/matrix/matrix_decomp.c \
    $(SRCDIR)/stats/statistics.c \
    $(SRCDIR)/stats/monte_carlo.c \
    $(SRCDIR)/stats/regression.c \
    $(SRCDIR)/signal/fft.c \
    $(SRCDIR)/signal/filters.c \
    $(SRCDIR)/signal/convolution.c \
    $(SRCDIR)/crypto/sha256.c \
    $(SRCDIR)/crypto/md5.c \
    $(SRCDIR)/crypto/crc32.c \
    $(SRCDIR)/crypto/hmac.c \
    $(SRCDIR)/sort/quicksort.c \
    $(SRCDIR)/sort/mergesort.c \
    $(SRCDIR)/sort/heapsort.c \
    $(SRCDIR)/sort/radixsort.c \
    $(SRCDIR)/graph/graph.c \
    $(SRCDIR)/graph/bfs.c \
    $(SRCDIR)/graph/dfs.c \
    $(SRCDIR)/graph/dijkstra.c \
    $(SRCDIR)/threads/thread_pool.c \
    $(SRCDIR)/threads/work_queue.c \
    $(SRCDIR)/threads/sync.c \
    $(SRCDIR)/compress/rle.c \
    $(SRCDIR)/compress/huffman.c \
    $(SRCDIR)/utils/rng.c \
    $(SRCDIR)/utils/timer.c \
    $(SRCDIR)/utils/helpers.c

OBJS = $(patsubst $(SRCDIR)/%.c,$(BUILDDIR)/%.o,$(SRCS))

# --- Common flags ---
INCLUDES = -I$(SRCDIR)
WARNINGS = -Wall -Wextra -Wno-unused-parameter -Wno-unused-variable -pedantic
DEFINES  = -D_POSIX_C_SOURCE=200809L -D_GNU_SOURCE
LDFLAGS  = -lpthread -lm -rdynamic

# --- Build profiles ---
# Debug (default): full symbols, no optimization
CFLAGS_DEBUG   = -std=c11 -g3 -O0 -fno-omit-frame-pointer $(WARNINGS) $(DEFINES) $(INCLUDES)

# Release: optimized
CFLAGS_RELEASE = -std=c11 -O2 -fno-omit-frame-pointer $(WARNINGS) $(DEFINES) $(INCLUDES)

# Symbols: optimized with debug info (best for perf)
CFLAGS_SYMBOLS = -std=c11 -O2 -g3 -fno-omit-frame-pointer $(WARNINGS) $(DEFINES) $(INCLUDES)

# Default to debug
CFLAGS ?= $(CFLAGS_DEBUG)

# Map file
MAPFLAG = -Wl,-Map=$(BINDIR)/$(TARGET).map

# --- Targets ---
.PHONY: all debug release symbols strip all-variants clean all-cross

all: debug

debug: CFLAGS = $(CFLAGS_DEBUG)
debug: $(BINDIR)/$(TARGET).debug
	@echo "Built: $< (debug)"

release: CFLAGS = $(CFLAGS_RELEASE)
release: $(BINDIR)/$(TARGET).release
	@echo "Built: $< (release)"

symbols: CFLAGS = $(CFLAGS_SYMBOLS)
symbols: $(BINDIR)/$(TARGET).sym
	@echo "Built: $< (symbols)"

strip: symbols
	$(STRIP) -o $(BINDIR)/$(TARGET).stripped $(BINDIR)/$(TARGET).sym
	@echo "Built: $(BINDIR)/$(TARGET).stripped"

all-variants: debug release symbols strip disasm
	@echo "=== All variants built ==="
	@ls -la $(BINDIR)/$(TARGET).*

# --- Convenience: plain 'matrixlab' symlink to debug build ---
$(BINDIR)/$(TARGET): $(BINDIR)/$(TARGET).debug
	@ln -sf $(TARGET).debug $@

# --- Link rules ---
$(BINDIR)/$(TARGET).debug: $(OBJS) | $(BINDIR)
	$(CC) $(CFLAGS_DEBUG) -o $@ $(OBJS) $(LDFLAGS) $(MAPFLAG)

$(BINDIR)/$(TARGET).release: $(OBJS) | $(BINDIR)
	$(CC) $(CFLAGS_RELEASE) -o $@ $(OBJS) $(LDFLAGS) $(MAPFLAG)

$(BINDIR)/$(TARGET).sym: $(OBJS) | $(BINDIR)
	$(CC) $(CFLAGS_SYMBOLS) -o $@ $(OBJS) $(LDFLAGS) $(MAPFLAG)

# --- Compile rule ---
$(BUILDDIR)/%.o: $(SRCDIR)/%.c | $(BUILDDIR)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<

# --- Directory creation ---
$(BUILDDIR):
	@mkdir -p $(BUILDDIR)

$(BINDIR):
	@mkdir -p $(BINDIR)

# --- Post-build: disassembly ---
disasm: symbols
	$(OBJDUMP) -d -S $(BINDIR)/$(TARGET).sym > $(BINDIR)/$(TARGET).disasm 2>/dev/null || true
	@echo "Built: $(BINDIR)/$(TARGET).disasm"

# --- Cross-compilation targets ---
CROSS_TARGETS = aarch64-linux-gnu- arm-linux-gnueabihf- x86_64-linux-gnu-

all-cross:
	@for cross in $(CROSS_TARGETS); do \
		arch=$$(echo $$cross | cut -d- -f1); \
		echo "=== Building for $$arch (CROSS=$$cross) ==="; \
		$(MAKE) clean 2>/dev/null; \
		$(MAKE) symbols CROSS=$$cross BINDIR=bin/$$arch && \
			echo "  OK: bin/$$arch/$(TARGET).sym" || \
			echo "  SKIP: $$cross toolchain not found"; \
	done
	@echo "=== Cross builds complete ==="

# --- Clean ---
clean:
	rm -rf $(BUILDDIR) $(BINDIR)

# --- Dependencies (simple) ---
$(BUILDDIR)/main.o: $(SRCDIR)/main.c $(SRCDIR)/workloads.h
$(BUILDDIR)/workloads.o: $(SRCDIR)/workloads.c $(SRCDIR)/workloads.h

# --- Info ---
info:
	@echo "Sources: $(words $(SRCS)) files"
	@echo "Objects: $(words $(OBJS)) files"
	@echo "CC:      $(CC)"
	@echo "CFLAGS:  $(CFLAGS)"
