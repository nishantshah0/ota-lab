# One-command entry points. Every target works natively and inside the
# Docker image (docker run --rm ota-lab make test).

PYTHON ?= python
BUILD  ?= build
PYTEST ?= $(PYTHON) -m pytest -q -p no:cacheprovider

.PHONY: all build test test-core test-faults test-fleet clean

all: build

build:
	cmake -S . -B $(BUILD) -G Ninja
	cmake --build $(BUILD)

# The whole suite: core (phases 1 to 3), fault injection, fleet.
test: build
	$(PYTEST) tests

test-core: build
	$(PYTEST) tests/test_boot.py tests/test_can.py tests/test_ab_boot.py tests/test_ota_transfer.py

test-faults: build
	$(PYTEST) tests/test_faults.py

test-fleet: build
	$(PYTEST) tests/test_fleet.py

clean:
	cmake -E rm -rf $(BUILD) test-logs
