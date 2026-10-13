.DEFAULT_GOAL := help
.PHONY: help install run app demo cli benchmark test check
PORT ?= 8765
ARGS ?=

help:
	@echo "make run       Launch the local dashboard (PORT=$(PORT))"
	@echo "make demo      Launch the dashboard in offline demo mode"
	@echo "make cli       Launch the interactive terminal benchmark"
	@echo "make test      Run the automated test suite"
	@echo "make benchmark Run the terminal benchmark (optional ARGS='...')"
	@echo "make check     Run tests, lint, and formatting checks"
	@echo "make install   Install application and development dependencies"

install:
	uv sync --group dev

run app:
	uv run decision-web --port $(PORT)

demo:
	uv run decision-web --port $(PORT) --demo

cli:
	uv run decision-tests

benchmark:
	uv run decision-tests $(ARGS)

test:
	uv run pytest -q

check: test
	uv run ruff check .
	uv run ruff format --check .
