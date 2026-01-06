.DEFAULT_GOAL := help

help:
	@echo "Eve (WIP)"
	@echo ""
	@echo "Targets:"
	@echo "  make tree        Show repo tree (high-level)"
	@echo "  make py-check    Sanity-check Python backend import graph"
	@echo "  make ts-check    Sanity-check TypeScript workspace (tsc)"

# --- utils ---

tree:
	@echo "./"
	@find . -maxdepth 2 -type d \( -name .git -o -name node_modules -o -name __pycache__ \) -prune -o -type d -print | sed 's|^\./||' | sort

# --- Python ---

py-check:
	@python3 -c "import sys; sys.path.insert(0, 'python'); import backend; print('OK: imported backend as namespace package')"

# --- TypeScript ---

ts-check:
	@cd ts && (command -v bun >/dev/null 2>&1 && bunx tsc -p tsconfig.json || npx -y tsc -p tsconfig.json)

