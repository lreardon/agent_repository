"""Sandbox for executing arbitrary acceptance criteria scripts.

Supports two backends:
- **GKE Autopilot** (staging/production): Runs scripts as Kubernetes Jobs in an
  isolated namespace with NetworkPolicy blocking all egress. This is the only
  backend acceptable for non-development environments.
- **Local Docker** (development only): Runs scripts via `docker run` for fast
  iteration. Only used when SANDBOX_GKE_CLUSTER is not configured.

Scripts run with:
- No network access (NetworkPolicy deny-all / --network=none)
- Read-only root filesystem
- Memory and CPU limits
- Time limit (activeDeadlineSeconds / Docker timeout)
- Non-root user (UID 65534)
- Dropped capabilities, no privilege escalation

The deliverable is mounted at /input/result.json (read-only).
The script is mounted at /input/verify (read-only, executable).
Exit code 0 = pass, non-zero = fail.
stdout/stderr captured for audit.
"""

import asyncio
import base64
import hashlib
import json
import logging
import tempfile
import time as _time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Allowed runtimes — maps to Docker images
ALLOWED_RUNTIMES: dict[str, str] = {
    "python:3.13": "python:3.13-slim",
    "python:3.12": "python:3.12-slim",
    "node:20": "node:20-slim",
    "node:22": "node:22-slim",
    "bash": "bash:5",
    "ruby:3.3": "ruby:3.3-slim",
}

# Defaults
DEFAULT_RUNTIME = "python:3.13"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MEMORY_LIMIT_MB = 256
MAX_TIMEOUT_SECONDS = 300
MAX_MEMORY_LIMIT_MB = 512
MAX_SCRIPT_SIZE_BYTES = 1_048_576  # 1MB
MAX_OUTPUT_CAPTURE_BYTES = 65_536  # 64KB stdout/stderr capture


@dataclass
class SandboxResult:
    """Result of running a script in a sandbox container."""
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    error: str | None = None
    elapsed_seconds: float = 0.0

    def to_dict(self, max_output_len: int = 2000) -> dict:
        """Serialize to dict, truncating stdout/stderr for API responses."""
        return {
            "passed": self.passed,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:max_output_len],
            "stderr": self.stderr[:max_output_len],
            "timed_out": self.timed_out,
            "error": self.error,
            "elapsed_seconds": self.elapsed_seconds,
        }


def validate_sandbox_inputs(
    script_b64: str,
    runtime: str,
    timeout_seconds: int,
    memory_limit_mb: int,
) -> SandboxResult | None:
    """Validate inputs, returning a SandboxResult on error or None if valid."""
    if runtime not in ALLOWED_RUNTIMES:
        return SandboxResult(
            passed=False, exit_code=-1, stdout="", stderr="",
            timed_out=False, error=f"Unsupported runtime: {runtime}",
        )

    if timeout_seconds <= 0 or timeout_seconds > MAX_TIMEOUT_SECONDS:
        return SandboxResult(
            passed=False, exit_code=-1, stdout="", stderr="",
            timed_out=False,
            error=f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}",
        )

    if memory_limit_mb <= 0 or memory_limit_mb > MAX_MEMORY_LIMIT_MB:
        return SandboxResult(
            passed=False, exit_code=-1, stdout="", stderr="",
            timed_out=False,
            error=f"memory_limit_mb must be between 1 and {MAX_MEMORY_LIMIT_MB}",
        )

    try:
        script_bytes = base64.b64decode(script_b64)
    except Exception as e:
        return SandboxResult(
            passed=False, exit_code=-1, stdout="", stderr="",
            timed_out=False, error=f"Invalid base64 script: {e}",
        )

    if len(script_bytes) > MAX_SCRIPT_SIZE_BYTES:
        return SandboxResult(
            passed=False, exit_code=-1, stdout="", stderr="",
            timed_out=False,
            error=f"Script too large: {len(script_bytes)} bytes",
        )

    return None  # Valid


def validate_script_criteria(criteria: dict) -> None:
    """Validate acceptance criteria dict. Raises ValueError on invalid input.

    Backward-compatible interface used by tests and the test_runner.
    """
    if "script" not in criteria:
        raise ValueError("Acceptance criteria must include 'script'")

    script_b64 = criteria["script"]
    runtime = criteria.get("runtime", DEFAULT_RUNTIME)
    timeout = criteria.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    memory = criteria.get("memory_limit_mb", DEFAULT_MEMORY_LIMIT_MB)

    if runtime not in ALLOWED_RUNTIMES:
        raise ValueError(f"Unsupported runtime: {runtime}")

    if timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    if timeout > MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds cannot exceed {MAX_TIMEOUT_SECONDS}")

    if memory <= 0:
        raise ValueError("memory_limit_mb must be positive")
    if memory > MAX_MEMORY_LIMIT_MB:
        raise ValueError(f"memory_limit_mb cannot exceed {MAX_MEMORY_LIMIT_MB}")

    try:
        script_bytes = base64.b64decode(script_b64)
    except Exception as e:
        raise ValueError(f"Script must be valid base64: {e}")

    if len(script_bytes) > MAX_SCRIPT_SIZE_BYTES:
        raise ValueError(f"Script too large: {len(script_bytes)} bytes")


async def run_script_in_sandbox(
    script_b64: str,
    deliverable: Any,
    runtime: str = DEFAULT_RUNTIME,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
) -> SandboxResult:
    """Execute a verification script against a deliverable in a sandbox.

    Routes to GKE (K8s Job) when SANDBOX_GKE_CLUSTER is configured,
    otherwise falls back to local Docker for development.
    """
    # Validate inputs
    error = validate_sandbox_inputs(script_b64, runtime, timeout_seconds, memory_limit_mb)
    if error:
        return error

    if settings.sandbox_gke_cluster:
        return await _run_in_gke(
            script_b64, deliverable, runtime, timeout_seconds, memory_limit_mb,
        )
    else:
        return await _run_in_docker(
            script_b64, deliverable, runtime, timeout_seconds, memory_limit_mb,
        )


# ==========================================================================
# GKE Backend — Kubernetes Jobs
# ==========================================================================

def _get_k8s_clients():
    """Get authenticated Kubernetes API clients for the sandbox cluster.

    Uses google-auth for GKE authentication:
    - On Cloud Run: uses the service account's default credentials
    - Locally: uses Application Default Credentials (gcloud auth)
    """
    import google.auth
    import google.auth.transport.requests
    from kubernetes import client as k8s_client

    project = settings.sandbox_gke_project or settings.gcp_project_id
    cluster = settings.sandbox_gke_cluster
    location = settings.sandbox_gke_location

    # Get GKE cluster info
    from google.cloud import container_v1
    gke_client = container_v1.ClusterManagerClient()
    cluster_path = f"projects/{project}/locations/{location}/clusters/{cluster}"
    gke_cluster = gke_client.get_cluster(name=cluster_path)

    # Get credentials
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())

    # Configure K8s client
    configuration = k8s_client.Configuration()
    configuration.host = f"https://{gke_cluster.endpoint}"
    configuration.api_key = {"authorization": f"Bearer {credentials.token}"}
    configuration.ssl_ca_cert = _write_ca_cert(gke_cluster.master_auth.cluster_ca_certificate)
    configuration.verify_ssl = True

    api_client = k8s_client.ApiClient(configuration)
    return (
        k8s_client.BatchV1Api(api_client),
        k8s_client.CoreV1Api(api_client),
    )


def _write_ca_cert(ca_cert_b64: str) -> str:
    """Write the cluster CA cert to a temp file and return its path."""
    import tempfile
    ca_cert_bytes = base64.b64decode(ca_cert_b64)
    f = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
    f.write(ca_cert_bytes)
    f.close()
    return f.name


async def _run_in_gke(
    script_b64: str,
    deliverable: Any,
    runtime: str,
    timeout_seconds: int,
    memory_limit_mb: int,
) -> SandboxResult:
    """Run verification script as a Kubernetes Job on GKE Autopilot."""
    from kubernetes import client as k8s_client
    from kubernetes.client.rest import ApiException

    namespace = settings.sandbox_namespace
    job_id = uuid.uuid4().hex[:12]
    job_name = f"verify-{job_id}"
    configmap_name = f"verify-input-{job_id}"
    docker_image = ALLOWED_RUNTIMES[runtime]
    script_bytes = base64.b64decode(script_b64)

    # Determine entrypoint based on runtime
    if runtime.startswith("python"):
        command = ["python", "/input/verify"]
    elif runtime.startswith("node"):
        command = ["node", "/input/verify"]
    elif runtime == "bash":
        command = ["bash", "/input/verify"]
    elif runtime.startswith("ruby"):
        command = ["ruby", "/input/verify"]
    else:
        command = ["/input/verify"]

    t0 = _time.monotonic()

    try:
        batch_api, core_api = await asyncio.get_event_loop().run_in_executor(
            None, _get_k8s_clients,
        )
    except Exception as e:
        logger.exception("Failed to connect to GKE cluster")
        return SandboxResult(
            passed=False, exit_code=-1, stdout="", stderr="",
            timed_out=False,
            error=f"GKE connection failed: {e}",
        )

    try:
        # 1. Create ConfigMap with deliverable and script
        deliverable_json = json.dumps(deliverable, default=str)
        script_text = script_bytes.decode("utf-8", errors="replace")

        configmap = k8s_client.V1ConfigMap(
            metadata=k8s_client.V1ObjectMeta(
                name=configmap_name,
                namespace=namespace,
                labels={"app": "sandbox-verify", "job-id": job_id},
            ),
            data={
                "result.json": deliverable_json,
            },
            binary_data={
                "verify": base64.b64encode(script_bytes).decode(),
            },
        )

        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: core_api.create_namespaced_config_map(namespace, configmap),
        )

        # 2. Create the Job
        job = k8s_client.V1Job(
            metadata=k8s_client.V1ObjectMeta(
                name=job_name,
                namespace=namespace,
                labels={"app": "sandbox-verify", "job-id": job_id},
            ),
            spec=k8s_client.V1JobSpec(
                backoff_limit=0,  # No retries
                active_deadline_seconds=timeout_seconds,
                ttl_seconds_after_finished=300,  # Auto-cleanup after 5 min
                template=k8s_client.V1PodTemplateSpec(
                    metadata=k8s_client.V1ObjectMeta(
                        labels={"app": "sandbox-verify", "job-id": job_id},
                    ),
                    spec=k8s_client.V1PodSpec(
                        restart_policy="Never",
                        automount_service_account_token=False,
                        enable_service_links=False,
                        containers=[
                            k8s_client.V1Container(
                                name="verify",
                                image=docker_image,
                                command=command,
                                resources=k8s_client.V1ResourceRequirements(
                                    limits={
                                        "memory": f"{memory_limit_mb}Mi",
                                        "cpu": "1",
                                    },
                                    requests={
                                        "memory": f"{memory_limit_mb}Mi",
                                        "cpu": "250m",
                                        "ephemeral-storage": "100Mi",
                                    },
                                ),
                                security_context=k8s_client.V1SecurityContext(
                                    run_as_non_root=True,
                                    run_as_user=65534,
                                    run_as_group=65534,
                                    read_only_root_filesystem=True,
                                    allow_privilege_escalation=False,
                                    capabilities=k8s_client.V1Capabilities(
                                        drop=["ALL"],
                                    ),
                                ),
                                volume_mounts=[
                                    k8s_client.V1VolumeMount(
                                        name="input",
                                        mount_path="/input",
                                        read_only=True,
                                    ),
                                    k8s_client.V1VolumeMount(
                                        name="tmp",
                                        mount_path="/tmp",
                                    ),
                                ],
                            ),
                        ],
                        volumes=[
                            k8s_client.V1Volume(
                                name="input",
                                config_map=k8s_client.V1ConfigMapVolumeSource(
                                    name=configmap_name,
                                    default_mode=0o555,  # Read + execute
                                    items=[
                                        k8s_client.V1KeyToPath(
                                            key="result.json",
                                            path="result.json",
                                            mode=0o444,
                                        ),
                                        k8s_client.V1KeyToPath(
                                            key="verify",
                                            path="verify",
                                            mode=0o555,
                                        ),
                                    ],
                                ),
                            ),
                            k8s_client.V1Volume(
                                name="tmp",
                                empty_dir=k8s_client.V1EmptyDirVolumeSource(
                                    medium="Memory",
                                    size_limit="32Mi",
                                ),
                            ),
                        ],
                    ),
                ),
            ),
        )

        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: batch_api.create_namespaced_job(namespace, job),
        )

        # 3. Poll for Job completion
        deadline = _time.monotonic() + timeout_seconds + 15  # Grace period
        completed = False
        timed_out = False
        exit_code = -1

        while _time.monotonic() < deadline:
            await asyncio.sleep(1)

            job_status = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: batch_api.read_namespaced_job_status(job_name, namespace),
            )

            if job_status.status.succeeded is not None and job_status.status.succeeded > 0:
                completed = True
                exit_code = 0
                break
            elif job_status.status.failed is not None and job_status.status.failed > 0:
                completed = True
                exit_code = 1
                # Check if it was a deadline exceeded
                if job_status.status.conditions:
                    for cond in job_status.status.conditions:
                        if cond.reason == "DeadlineExceeded":
                            timed_out = True
                break

        if not completed:
            timed_out = True

        # 4. Collect pod logs
        stdout = ""
        stderr = ""
        try:
            pods = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: core_api.list_namespaced_pod(
                    namespace,
                    label_selector=f"job-name={job_name}",
                ),
            )

            if pods.items:
                pod = pods.items[0]
                pod_name = pod.metadata.name

                # Get exit code from container status
                if pod.status and pod.status.container_statuses:
                    cs = pod.status.container_statuses[0]
                    if cs.state and cs.state.terminated:
                        exit_code = cs.state.terminated.exit_code

                try:
                    log_output = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: core_api.read_namespaced_pod_log(
                            pod_name, namespace,
                            container="verify",
                            limit_bytes=MAX_OUTPUT_CAPTURE_BYTES,
                        ),
                    )
                    # K8s logs API doesn't separate stdout/stderr — everything goes to stdout
                    stdout = log_output or ""
                except ApiException as e:
                    if e.status != 404:
                        logger.warning("Failed to read pod logs: %s", e)
        except Exception as e:
            logger.warning("Failed to collect pod logs: %s", e)

        elapsed = _time.monotonic() - t0

        return SandboxResult(
            passed=(exit_code == 0),
            exit_code=exit_code,
            stdout=stdout[:MAX_OUTPUT_CAPTURE_BYTES],
            stderr=stderr[:MAX_OUTPUT_CAPTURE_BYTES],
            timed_out=timed_out,
            elapsed_seconds=elapsed,
        )

    except ApiException as e:
        elapsed = _time.monotonic() - t0
        logger.exception("K8s API error during sandbox execution")
        return SandboxResult(
            passed=False, exit_code=-1, stdout="", stderr="",
            timed_out=False,
            error=f"K8s API error: {e.reason} ({e.status})",
            elapsed_seconds=elapsed,
        )
    except Exception as e:
        elapsed = _time.monotonic() - t0
        logger.exception("Sandbox execution failed")
        return SandboxResult(
            passed=False, exit_code=-1, stdout="", stderr="",
            timed_out=False, error=f"Sandbox error: {e}",
            elapsed_seconds=elapsed,
        )
    finally:
        # Cleanup: delete ConfigMap (Job auto-cleans via ttlSecondsAfterFinished)
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: core_api.delete_namespaced_config_map(
                    configmap_name, namespace,
                    propagation_policy="Background",
                ),
            )
        except Exception:
            pass  # Best-effort cleanup


# ==========================================================================
# Local Docker Backend — Development Only
# ==========================================================================

async def _run_in_docker(
    script_b64: str,
    deliverable: Any,
    runtime: str,
    timeout_seconds: int,
    memory_limit_mb: int,
) -> SandboxResult:
    """Run verification script in a local Docker container. Development only."""
    script_bytes = base64.b64decode(script_b64)
    docker_image = ALLOWED_RUNTIMES[runtime]
    container_name = f"verify-{uuid.uuid4().hex[:12]}"

    with tempfile.TemporaryDirectory(prefix="sandbox-") as tmpdir:
        tmppath = Path(tmpdir)

        # Write deliverable as JSON
        result_path = tmppath / "result.json"
        result_path.write_text(json.dumps(deliverable, default=str))

        # Write script
        script_path = tmppath / "verify"
        script_path.write_bytes(script_bytes)
        script_path.chmod(0o555)

        # Determine entrypoint
        if runtime.startswith("python"):
            cmd = ["python", "/input/verify"]
        elif runtime.startswith("node"):
            cmd = ["node", "/input/verify"]
        elif runtime == "bash":
            cmd = ["bash", "/input/verify"]
        elif runtime.startswith("ruby"):
            cmd = ["ruby", "/input/verify"]
        else:
            cmd = ["/input/verify"]

        # Build docker command
        docker_cmd = [
            "docker", "run",
            "--rm",
            "--name", container_name,
            "--network=none",
            f"--memory={memory_limit_mb}m",
            "--memory-swap", f"{memory_limit_mb}m",
            "--cpus=1",
            "--pids-limit=256",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=32m",
            "-v", f"{tmppath}:/input:ro",
            "--user=65534:65534",
            docker_image,
            *cmd,
        ]

        try:
            t0 = _time.monotonic()

            process = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_seconds + 5,
                )
            except asyncio.TimeoutError:
                kill_proc = await asyncio.create_subprocess_exec(
                    "docker", "kill", container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await kill_proc.wait()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()

                elapsed = _time.monotonic() - t0
                return SandboxResult(
                    passed=False, exit_code=-1,
                    stdout="", stderr="Execution timed out",
                    timed_out=True, elapsed_seconds=elapsed,
                )

            elapsed = _time.monotonic() - t0
            stdout = stdout_bytes[:MAX_OUTPUT_CAPTURE_BYTES].decode("utf-8", errors="replace")
            stderr = stderr_bytes[:MAX_OUTPUT_CAPTURE_BYTES].decode("utf-8", errors="replace")
            exit_code = process.returncode or 0

            return SandboxResult(
                passed=(exit_code == 0),
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                timed_out=False,
                elapsed_seconds=elapsed,
            )

        except FileNotFoundError:
            return SandboxResult(
                passed=False, exit_code=-1, stdout="", stderr="",
                timed_out=False, error="Docker not available",
            )
        except Exception as e:
            logger.exception("Sandbox execution failed")
            return SandboxResult(
                passed=False, exit_code=-1, stdout="", stderr="",
                timed_out=False, error=f"Sandbox error: {e}",
            )
