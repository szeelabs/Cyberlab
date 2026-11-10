from cyberlab.execution.base import (
    CostClass,
    EnvState,
    ProviderAdapter,
    ProviderError,
    ProviderStatus,
    ProvisionRequest,
    ProvisionResult,
)
from cyberlab.execution.shell import ShellSession

__all__ = [
    "CostClass",
    "EnvState",
    "ProviderAdapter",
    "ProviderError",
    "ProviderStatus",
    "ProvisionRequest",
    "ProvisionResult",
    "ShellSession",
    "build_provider",
]


def build_provider(settings) -> ProviderAdapter | None:
    """Return the configured provider, or ``None`` when execution is not configured."""
    if settings.execution_provider == "docker":
        from cyberlab.execution.docker import (
            DockerAdapter,
            docker_env,
            subprocess_runner,
            subprocess_streamer,
        )

        env = docker_env(
            settings.docker_host,
            tls_verify=settings.docker_tls_verify,
            cert_path=settings.docker_cert_path,
        )
        return DockerAdapter(
            subprocess_runner(env=env),
            binary=settings.docker_binary,
            image_mirror=settings.image_mirror,
            docker_host=settings.docker_host,
            egress_image=settings.egress_proxy_image,
            streamer=subprocess_streamer(env=env),
            env=env,
            cost_class=CostClass(settings.execution_cost_class),
            allow_paid=settings.allow_paid_providers,
        )
    return None
