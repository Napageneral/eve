.DEFAULT_GOAL := help

.PHONY: help build test fmt clean

help:
	@echo "Eve - iMessage Analysis & Embeddings CLI"
	@echo ""
	@echo "Targets:"
	@echo "  make build    Build eve binary"
	@echo "  make test     Run all tests"
	@echo "  make fmt      Format Go code"
	@echo "  make clean    Remove build artifacts"

build:
	@command -v go >/dev/null 2>&1 || (echo "go is required to build" && exit 1)
	@go build -o bin/eve ./cmd/eve
	@echo "Built: bin/eve"

test:
	@command -v go >/dev/null 2>&1 || (echo "go is required to run tests" && exit 1)
	@go test ./...

fmt:
	@gofmt -w $$(find . -name '*.go')

clean:
	@rm -rf bin/eve
	@echo "Cleaned build artifacts"
