# Development checks for the Sony Headphones plugin.
#
# Every target is dependency-free and non-destructive. Optional tools (ruff,
# qmllint, qmltestrunner, omarchy) are used when present and skipped with a
# clear message when not; a missing optional tool is never a failure. No target installs anything
# and no target touches real hardware: `make demo` is demo mode in a throwaway
# XDG_RUNTIME_DIR, so the safe run is the easy one.

.DEFAULT_GOAL := check

.PHONY: test lint qmltest check demo verify doctor install-hooks

# The Python suite (unittest discovery) and the Model.js harness.
test:
	python3 tests/run.py
	@if command -v node >/dev/null 2>&1; then \
		echo "JS harness: node tests/test_model.mjs"; \
		node tests/test_model.mjs; \
	elif command -v deno >/dev/null 2>&1; then \
		echo "JS harness: deno run --allow-read tests/test_model.mjs"; \
		deno run --allow-read tests/test_model.mjs; \
	else \
		echo "error: neither node nor deno is installed; the JS harness needs one" >&2; \
		exit 1; \
	fi

# Byte-compile the helper and lint what we can. ruff is a development tool
# only: the helper never imports it and the plugin never needs it to run.
lint:
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check . bin/sony-headphones; \
	else \
		echo "WARNING: ruff not found -- Python lint SKIPPED (install: omarchy pkg add ruff)"; \
	fi
	@cache="$$(mktemp -d)"; \
	PYTHONPYCACHEPREFIX="$$cache" python3 -m py_compile bin/sony-headphones; \
	status=$$?; \
	rm -rf "$$cache"; \
	exit $$status
	@if command -v qmllint >/dev/null 2>&1; then \
		qmllint Panel.qml Service.qml Model.js; \
	else \
		echo "WARNING: qmllint not found -- QML/JS lint SKIPPED (it ships in qt6-declarative at /usr/lib/qt6/bin)"; \
	fi

# QtTest cover for the pure Model.js helpers in the real QML engine. Optional
# like the other engine tools: without qmltestrunner this skips, so CI stays
# green; with it, a failing case fails the target.
qmltest:
	@if command -v qmltestrunner >/dev/null 2>&1; then \
		echo "QML tests: QT_QPA_PLATFORM=offscreen qmltestrunner -input tests/tst_model.qml"; \
		QT_QPA_PLATFORM=offscreen qmltestrunner -input tests/tst_model.qml; \
	else \
		echo "WARNING: qmltestrunner not found -- QML tests SKIPPED (it ships in qt6-declarative at /usr/lib/qt6/bin)"; \
	fi

# The single source of truth for "green": tests, lint, and plugin validation
# when the Omarchy tooling is installed (it is absent on CI runners). The
# validator refuses any symlink in the plugin folder, so validation runs
# against a throwaway copy of the tracked files only.
check: test lint qmltest
	@if command -v omarchy >/dev/null 2>&1; then \
		d=$$(mktemp -d); \
		git ls-files -z | tar --null -T - -cf - | tar -xf - -C "$$d"; \
		omarchy plugin validate "$$d"; rc=$$?; rm -rf "$$d"; exit $$rc; \
	else \
		echo "WARNING: omarchy not found -- plugin validation SKIPPED (a local-only check)"; \
	fi

# Report the development tools the gate uses. None is a runtime dependency: the
# helper is standard library only. A missing tool makes its check skip, so run
# this when `make check` prints a WARNING.
doctor:
	@echo "Development tools (the plugin needs none of these to run):"
	@if command -v python3 >/dev/null 2>&1; then echo "  ok       python3  $$(python3 --version 2>&1)"; else echo "  MISSING  python3"; fi
	@if command -v node >/dev/null 2>&1; then echo "  ok       node     $$(node --version)"; elif command -v deno >/dev/null 2>&1; then echo "  ok       deno     $$(deno --version 2>/dev/null | head -1)"; else echo "  MISSING  node/deno (the Model.js harness needs one)"; fi
	@if command -v ruff >/dev/null 2>&1; then echo "  ok       ruff     $$(ruff --version)"; else echo "  MISSING  ruff      -> omarchy pkg add ruff"; fi
	@if command -v qmllint >/dev/null 2>&1; then echo "  ok       qmllint  $$(qmllint --version)"; else echo "  MISSING  qmllint   -> symlink /usr/lib/qt6/bin/qmllint into ~/.local/bin"; fi
	@if command -v qmltestrunner >/dev/null 2>&1; then echo "  ok       qmltestrunner"; else echo "  MISSING  qmltestrunner -> symlink /usr/lib/qt6/bin/qmltestrunner into ~/.local/bin"; fi
	@if command -v omarchy >/dev/null 2>&1; then echo "  ok       omarchy  (plugin validate)"; else echo "  MISSING  omarchy   (plugin validate is skipped)"; fi

# Install the versioned pre-commit hook into this clone's .git/hooks. The hook
# runs `make check`, so a commit cannot land unlinted or untested.
install-hooks:
	@chmod +x hooks/pre-commit
	@ln -sf ../../hooks/pre-commit .git/hooks/pre-commit
	@echo "Installed .git/hooks/pre-commit -> hooks/pre-commit (runs make check)"

# Run the helper against a stand-in link in a fresh private runtime directory.
# SONY_HEADPHONES_DEMO defaults to 1; pass DEMO=v2 for the v2 command set.
DEMO ?= 1
ARGS ?=

demo:
	@dir="$$(mktemp -d)"; \
	trap 'rm -rf "$$dir"' EXIT; \
	XDG_RUNTIME_DIR="$$dir" SONY_HEADPHONES_DEMO="$(DEMO)" \
		/usr/bin/python3 -I bin/sony-headphones $(ARGS)

# Verify that the headphones honour each control: read, write a different
# value, release/reclaim, read back, restore. This one wants the radio, so it
# is never part of the gate; the daemon has to be running (the widget starts
# it). CONTROLS=... restricts the run to named controls.
verify:
	@if [ -z "$(MAC)" ]; then \
		echo "usage: make verify MAC=<address> [CONTROLS='<control> ...']" >&2; \
		exit 2; \
	fi
	python3 tools/verify.py $(MAC) $(CONTROLS)
