import subprocess, time, os, signal
import pytest

@pytest.fixture(scope="session", autouse=True)
def docker_compose():
    # Spin up prod compose in background
    subprocess.run(["docker", "compose", "-f", "docker-compose.prod.yml", "up", "-d"], check=True)
    # Wait for services to be ready (backend and frontend)
    time.sleep(10)
    yield
    # Tear down containers and volumes after tests
    subprocess.run(["docker", "compose", "-f", "docker-compose.prod.yml", "down", "-v"], check=True)