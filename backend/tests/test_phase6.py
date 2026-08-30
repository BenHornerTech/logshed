"""
Phase 6 Test Suite: Single-Container Packaging & Unraid Distribution.
Tests Dockerfile, entrypoint.sh, unraid-template.xml, GitHub Actions workflow,
and live Docker build/container runtime verification.
"""

import os
import pathlib
import subprocess
import time
import xml.etree.ElementTree as ET
import pytest
import yaml
import httpx

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
ENTRYPOINT_PATH = REPO_ROOT / "entrypoint.sh"
UNRAID_TEMPLATE_PATH = REPO_ROOT / "unraid-template.xml"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "build-publish.yml"


def is_docker_daemon_running() -> bool:
    """Check if docker CLI is available and docker daemon is responding."""
    try:
        res = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


class TestUnraidTemplate:
    """Tests for Unraid Community Applications XML configuration template."""

    def test_unraid_template_valid_xml(self):
        assert UNRAID_TEMPLATE_PATH.exists(), "unraid-template.xml must exist"
        tree = ET.parse(UNRAID_TEMPLATE_PATH)
        root = tree.getroot()
        assert root.tag == "Container"
        assert root.attrib.get("version") == "2"

    def test_unraid_container_identity_and_network(self):
        tree = ET.parse(UNRAID_TEMPLATE_PATH)
        root = tree.getroot()

        name = root.find("Name")
        assert name is not None and name.text == "homelab-log-hub"

        network = root.find("Network")
        assert network is not None and network.text == "br0", "Network must be set to br0 for dedicated IP support"

        webui = root.find("WebUI")
        assert webui is not None and "8080" in webui.text

    def test_unraid_port_mappings(self):
        tree = ET.parse(UNRAID_TEMPLATE_PATH)
        root = tree.getroot()

        configs = root.findall("Config")
        ports = [c for c in configs if c.attrib.get("Type") == "Port"]

        port_8080 = next((p for p in ports if p.attrib.get("Target") == "8080"), None)
        assert port_8080 is not None, "Port 8080 mapping missing"
        assert port_8080.attrib.get("Mode") == "tcp"

        port_1514_udp = next((p for p in ports if p.attrib.get("Target") == "1514" and p.attrib.get("Mode") == "udp"), None)
        assert port_1514_udp is not None, "Port 1514 UDP mapping missing"

        port_1514_tcp = next((p for p in ports if p.attrib.get("Target") == "1514" and p.attrib.get("Mode") == "tcp"), None)
        assert port_1514_tcp is not None, "Port 1514 TCP mapping missing"

    def test_unraid_path_mappings(self):
        tree = ET.parse(UNRAID_TEMPLATE_PATH)
        root = tree.getroot()

        configs = root.findall("Config")
        paths = [c for c in configs if c.attrib.get("Type") == "Path"]

        data_path = next((p for p in paths if p.attrib.get("Target") == "/data"), None)
        assert data_path is not None, "Path /data mapping missing"
        assert data_path.attrib.get("Default") == "/mnt/user/appdata/homelab-log-hub"
        assert data_path.attrib.get("Mode") == "rw"

        sock_path = next((p for p in paths if p.attrib.get("Target") == "/var/run/docker.sock"), None)
        assert sock_path is not None, "Path /var/run/docker.sock mapping missing"
        assert sock_path.attrib.get("Default") == "/var/run/docker.sock"
        assert sock_path.attrib.get("Mode") == "ro", "Docker socket must be read-only"

    def test_unraid_variables_and_no_docker_host(self):
        tree = ET.parse(UNRAID_TEMPLATE_PATH)
        root = tree.getroot()

        configs = root.findall("Config")
        variables = [c for c in configs if c.attrib.get("Type") == "Variable"]
        var_targets = {v.attrib.get("Target"): v.attrib.get("Default") for v in variables}

        assert "PUID" in var_targets and var_targets["PUID"] == "1000"
        assert "PGID" in var_targets and var_targets["PGID"] == "1000"
        assert "TZ" in var_targets

        # SPEC.md §1.1 & §8.2: DOCKER_HOST must NOT be exposed as an Unraid template Config entry
        assert "DOCKER_HOST" not in var_targets


class TestDockerfileStatic:
    """Static validation tests for Dockerfile directives."""

    def test_dockerfile_exists_and_multistage(self):
        assert DOCKERFILE_PATH.exists(), "Dockerfile must exist"
        content = DOCKERFILE_PATH.read_text()

        # Multi-stage frontend builder
        assert "FROM node:20-alpine AS frontend-builder" in content or "FROM node:20-alpine" in content
        assert "npm run build" in content

        # Runtime stage
        assert "FROM python:3.12-slim" in content
        assert "tini" in content
        assert "curl" in content
        assert "gosu" in content

    def test_dockerfile_ports_volumes_and_entrypoint(self):
        content = DOCKERFILE_PATH.read_text()

        assert "EXPOSE 8080/tcp 1514/tcp 1514/udp" in content or ("8080" in content and "1514" in content)
        assert 'VOLUME ["/data"]' in content or "VOLUME /data" in content
        assert "HEALTHCHECK" in content
        assert "/api/health" in content
        assert 'ENTRYPOINT ["/entrypoint.sh"]' in content


class TestEntrypointStatic:
    """Static validation tests for entrypoint.sh script."""

    def test_entrypoint_syntax(self):
        assert ENTRYPOINT_PATH.exists(), "entrypoint.sh must exist"
        res = subprocess.run(["sh", "-n", str(ENTRYPOINT_PATH)], capture_output=True, text=True)
        assert res.returncode == 0, f"entrypoint.sh syntax error: {res.stderr}"

    def test_entrypoint_privilege_dropping_logic(self):
        content = ENTRYPOINT_PATH.read_text()

        # Check PUID and PGID handling
        assert "PUID" in content and "PGID" in content
        assert "1000" in content

        # Check docker.sock GID detection
        assert "docker.sock" in content
        assert "stat" in content

        # Check /data chown
        assert "chown appuser:appuser /data" in content or ("chown" in content and "/data" in content)

        # Check single worker uvicorn execution with gosu and tini
        assert "--workers 1" in content
        assert "gosu" in content
        assert "tini" in content
        assert "uvicorn" in content


class TestGitHubWorkflow:
    """Validation tests for GitHub Actions build and publish workflow."""

    def test_workflow_valid_yaml(self):
        assert WORKFLOW_PATH.exists(), ".github/workflows/build-publish.yml must exist"
        content = WORKFLOW_PATH.read_text()
        parsed = yaml.safe_load(content)
        assert parsed is not None
        assert "name" in parsed
        assert "jobs" in parsed

    def test_workflow_triggers_and_permissions(self):
        content = WORKFLOW_PATH.read_text()
        parsed = yaml.safe_load(content)

        triggers = parsed.get("on") or parsed.get(True) or {}
        if isinstance(triggers, dict):
            push = triggers.get("push", {})
            assert "main" in push.get("branches", [])
            assert any("v*" in tag for tag in push.get("tags", []))

        permissions = parsed.get("permissions", {})
        assert permissions.get("packages") == "write"

    def test_workflow_multiarch_and_ghcr(self):
        content = WORKFLOW_PATH.read_text()
        assert "ghcr.io" in content
        assert "linux/amd64" in content
        assert "linux/arm64" in content


@pytest.fixture(scope="module")
def docker_available():
    if not is_docker_daemon_running():
        pytest.skip("Docker daemon is not running or accessible in this test environment.")
    return True


@pytest.fixture(scope="module")
def built_image(docker_available):
    image_name = "homelab-log-hub:phase6-test"
    build_cmd = ["docker", "build", "-t", image_name, str(REPO_ROOT)]
    res = subprocess.run(build_cmd, capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, f"Docker build failed: {res.stderr}\n{res.stdout}"
    yield image_name
    # Cleanup image
    subprocess.run(["docker", "rmi", "-f", image_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class TestDockerLiveContainer:
    """Live Docker image build and container startup tests."""

    def test_container_default_port_and_healthcheck(self, built_image):
        container_name = "test-hub-default-port"
        host_port = "18080"

        # Run container
        run_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-p", f"{host_port}:8080",
            built_image,
        ]
        res = subprocess.run(run_cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"Failed to start container: {res.stderr}"

        try:
            # Poll health endpoint
            health_url = f"http://localhost:{host_port}/api/health"
            healthy = False
            for _ in range(30):
                time.sleep(1)
                try:
                    r = httpx.get(health_url, timeout=2.0)
                    if r.status_code == 200 and r.json().get("status") == "ok":
                        healthy = True
                        break
                except Exception:
                    continue

            assert healthy, "Container failed to pass GET /api/health on default port 8080 within 30 seconds."

            # Verify single uvicorn worker process inside container
            ps_res = subprocess.run(
                ["docker", "exec", container_name, "ps", "aux"],
                capture_output=True,
                text=True,
            )
            assert ps_res.returncode == 0
            # Ensure only 1 Python uvicorn process is running under tini
            python_uvicorn_lines = [
                line for line in ps_res.stdout.splitlines()
                if "python" in line and "uvicorn" in line
            ]
            assert len(python_uvicorn_lines) == 1, f"Expected 1 uvicorn worker process, found: {python_uvicorn_lines}"
            assert "--workers 1" in python_uvicorn_lines[0]

        finally:
            subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_container_custom_port_override(self, built_image):
        container_name = "test-hub-custom-port"
        host_port = "19090"
        target_port = "9090"

        run_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-e", f"PORT={target_port}",
            "-p", f"{host_port}:{target_port}",
            built_image,
        ]
        res = subprocess.run(run_cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"Failed to start container: {res.stderr}"

        try:
            health_url = f"http://localhost:{host_port}/api/health"
            healthy = False
            for _ in range(30):
                time.sleep(1)
                try:
                    r = httpx.get(health_url, timeout=2.0)
                    if r.status_code == 200 and r.json().get("status") == "ok":
                        healthy = True
                        break
                except Exception:
                    continue

            assert healthy, f"Container failed to pass GET /api/health on overridden port {target_port} within 30 seconds."

        finally:
            subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_container_privilege_drop_and_permissions(self, built_image):
        container_name = "test-hub-privilege-test"

        run_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-e", "PUID=1001",
            "-e", "PGID=1001",
            built_image,
        ]
        res = subprocess.run(run_cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"Failed to start container: {res.stderr}"

        try:
            time.sleep(3)
            # Check user ID under which uvicorn runs
            id_res = subprocess.run(
                ["docker", "exec", container_name, "id", "-u", "appuser"],
                capture_output=True,
                text=True,
            )
            assert id_res.returncode == 0
            assert id_res.stdout.strip() == "1001"

            # Check /data ownership
            stat_res = subprocess.run(
                ["docker", "exec", container_name, "stat", "-c", "%u:%g", "/data"],
                capture_output=True,
                text=True,
            )
            assert stat_res.returncode == 0
            assert stat_res.stdout.strip() == "1001:1001"

        finally:
            subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
