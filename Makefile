#!/usr/bin/make -f
# -*- makefile -*-

SHELL          := /bin/bash
.SHELLFLAGS   := -eu -o pipefail -c
.DEFAULT_GOAL := help
.LOGGING      := 0

.ONESHELL:             ;    # Recipes execute in same shell
.NOTPARALLEL:          ;    # Wait for this target to finish
.SILENT:               ;    # No need for @
.EXPORT_ALL_VARIABLES: ;    # Export variables to child processes.
.DELETE_ON_ERROR:      ;    # Delete target if recipe fails.

# Modify the block character to be `-\t` instead of `\t`
ifeq ($(origin .RECIPEPREFIX), undefined)
    $(error This version of Make does not support .RECIPEPREFIX.)
endif
.RECIPEPREFIX = -


PROJECT_DIR := $(shell git rev-parse --show-toplevel)
SRC_DIR     := $(PROJECT_DIR)/src
BUILD_DIR   := $(PROJECT_DIR)/dist

default: $(.DEFAULT_GOAL)
all: help


.PHONY: help
help: ## List commands <default>
-    echo -e "USAGE: make \033[36m[COMMAND]\033[0m\n"
-    echo "Available commands:"
-    awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "\t\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)


.PHONY: lint
lint: ## Lint the code
-    black $(PROJECT_DIR)
-    ruff check $(PROJECT_DIR) --fix


.PHONY: sync
sync: ## Sync package-monitor from remote agent using .rsync-exclude
-    if [ ! -f .rsync-exclude ]; then echo "Error: .rsync-exclude file not found!"; exit 1; fi
-    rsync -avzP --exclude-from='.rsync-exclude' agent@192.168.0.18:/Users/agent/package-monitor/ .