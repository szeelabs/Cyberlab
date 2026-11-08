from cyberlab.execution.base import (
    CostClass,
    EnvState,
    ProviderAdapter,
    ProviderError,
    ProviderStatus,
    ProvisionRequest,
    ProvisionResult,
)

__all__ = [
    "CostClass",
    "EnvState",
    "ProviderAdapter",
    "ProviderError",
    "ProviderStatus",
    "ProvisionRequest",
    "ProvisionResult",
    "build_provider",
]


def build_provider(settings) -> ProviderAdapter | None:
    """Return the configured provider, or ``None`` when execution is not configured."""
    if settings.execution_provider == "docker":
        from cyberlab.execution.docker import DockerAdapter, subprocess_runner

        return DockerAdapter(
            subprocess_runner(settings.docker_host),
            binary=settings.docker_binary,
            image_mirror=settings.image_mirror,
        )
    return None
