# Makefile
.PHONY: build up down test load

# Build all containers
build:
	docker compose -f docker-compose.prod.yml build

# Start containers in detached mode
up:
	docker compose -f docker-compose.prod.yml up -d

# Stop containers and clean up volumes
down:
	docker compose -f docker-compose.prod.yml down -v

# Run the end‑to‑end integration test (spins up services, executes pytest)
test:
	docker compose -f docker-compose.prod.yml up -d
	pytest -x -v tests/e2e_integration.py

# Run Locust load test (50 users, 5 spawn/sec, 2 min)
load:
	docker compose -f docker-compose.prod.yml up -d
	locust -f loadtest/locustfile.py --headless -u 50 -r 5 --run-time 2m