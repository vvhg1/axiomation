.PHONY: help sync test test-core coverage lint fmt check

help:
	@echo "sync   install the dev environment"
	@echo "test   run the offline suite (live tests are deselected)"
	@echo "test-core  the suite without the library extra: its tests skip"
	@echo "coverage   the suite, with each module's line coverage; a report, no threshold"
	@echo "lint   ruff check and a format check"
	@echo "fmt    ruff format and fix what ruff can fix"
	@echo "check  lint and test"

sync:
	uv sync

test:
	uv run pytest

# The core without the `library` extra: a throwaway environment holding the
# package alone and pytest; a test that needs the extra skips there.
test-core:
	uv run --isolated --no-dev --with pytest pytest -p no:cacheprovider

coverage:
	uv run pytest --cov --cov-report=term-missing:skip-covered

lint:
	uv run ruff check
	uv run ruff format --check

fmt:
	uv run ruff format
	uv run ruff check --fix

check: lint test
