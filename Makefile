.PHONY: build run clean help

IMAGE ?= personal-agent-worker
TASK  ?= "Build a REST API for managing books"

help:
	@echo "Usage:"
	@echo "  make build          Build the Docker image"
	@echo "  make run TASK='...' Run the full multi-agent pipeline"
	@echo "  make clean          Remove workspace output files"

build:
	docker build -t $(IMAGE) .

run: build
	python3 orchestrator.py $(TASK)

# Run a single agent role interactively
agent:
	docker run --rm -it \
	  -v $(PWD)/workspace:/workspace \
	  -e OPENROUTER_API_KEY=$(shell grep OPENROUTER_API_KEY .env | cut -d= -f2) \
	  -e AGENT_ROLE=$(ROLE) \
	  $(IMAGE) $(TASK)

clean:
	rm -rf workspace/*
