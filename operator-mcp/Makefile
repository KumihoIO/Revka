# Revka Operator MCP — build and install targets
#
# Build:   make build
# Install: make install
# Dev:     make dev-install (editable Python + linked TS)

SHELL := /bin/bash
INSTALL_DIR := $(HOME)/.revka/operator_mcp
SKILLS_DIR := $(HOME)/.revka/skills
VENV_DIR := $(INSTALL_DIR)/venv
PYTHON := python3

.PHONY: build build-python build-ts install install-python install-ts install-skills \
        dev-install clean typecheck

# -- Build --------------------------------------------------------------------

build: build-ts build-python

build-python:
	@echo "==> Checking Python dependencies..."
	$(PYTHON) -m py_compile operator_mcp/operator_mcp.py
	$(PYTHON) -m py_compile operator_mcp/subagent_mcp.py
	@echo "    Python syntax OK"

build-ts:
	@echo "==> Building session-manager..."
	cd session-manager && npm install && npm run build
	@echo "    TypeScript build OK"

typecheck:
	cd session-manager && npx tsc --noEmit

# -- Install ------------------------------------------------------------------

install: install-python install-ts install-skills install-bootstrap
	@echo ""
	@echo "==> Revka Operator installed to $(INSTALL_DIR)"
	@echo "    Python:  $(VENV_DIR)/bin/python3 -m operator_mcp"
	@echo "    Sidecar: node $(INSTALL_DIR)/session-manager/dist/index.js"
	@echo "    Skills:  $(SKILLS_DIR)/"

install-python:
	@echo "==> Installing Python operator..."
	@mkdir -p $(INSTALL_DIR)
	# Create/update venv
	@test -d $(VENV_DIR) || $(PYTHON) -m venv $(VENV_DIR)
	# Install package into venv
	$(VENV_DIR)/bin/pip install --quiet --upgrade pip
	$(VENV_DIR)/bin/pip install --quiet '.[all]'
	# Copy full operator package (all subpackages) into install dir; rsync
	# mirrors structure while excluding caches and byte-compiled artifacts.
	rsync -a --delete-excluded \
	  --exclude='__pycache__' --exclude='*.pyc' \
	  --exclude='venv' --exclude='session-manager' \
	  operator_mcp/ $(INSTALL_DIR)/
	@echo "    Python operator installed"

install-ts:
	@echo "==> Installing session-manager sidecar..."
	@mkdir -p $(INSTALL_DIR)/session-manager
	# Copy built dist + package.json + node_modules
	cp -r session-manager/dist $(INSTALL_DIR)/session-manager/
	cp session-manager/package.json $(INSTALL_DIR)/session-manager/
	cd $(INSTALL_DIR)/session-manager && npm install --production --quiet 2>/dev/null || true
	@echo "    Session manager installed"

install-skills:
	@echo "==> Installing orchestration skills..."
	@mkdir -p $(SKILLS_DIR)
	cp skills/*.md $(SKILLS_DIR)/ 2>/dev/null || true
	@echo "    Skills installed"

install-bootstrap:
	@echo "==> Installing bootstrap script..."
	@mkdir -p $(INSTALL_DIR)
	cp operator_mcp/run_operator_mcp.py $(INSTALL_DIR)/run_operator_mcp.py
	@echo "    Bootstrap script installed"

# -- Dev install (editable) ---------------------------------------------------

dev-install:
	@echo "==> Dev-installing (editable mode)..."
	@mkdir -p $(INSTALL_DIR) $(SKILLS_DIR)
	# Symlink operator package
	@test -d $(VENV_DIR) || $(PYTHON) -m venv $(VENV_DIR)
	$(VENV_DIR)/bin/pip install --quiet --upgrade pip
	$(VENV_DIR)/bin/pip install --quiet -e '.[all]'
	# Symlink session-manager
	@rm -f $(INSTALL_DIR)/session-manager
	ln -sf $(CURDIR)/session-manager $(INSTALL_DIR)/session-manager
	# Symlink skills
	@for f in skills/*.md; do \
		ln -sf $(CURDIR)/$$f $(SKILLS_DIR)/$$(basename $$f); \
	done
	@echo "    Dev-install done (editable, symlinked)"

# -- Clean --------------------------------------------------------------------

clean:
	rm -rf session-manager/dist session-manager/node_modules
	rm -rf build dist *.egg-info
	find operator_mcp -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "    Cleaned"
