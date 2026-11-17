"""Install your own software: a .deb uploaded to the workspace goes into the machine.

Machines run unprivileged on a read-only system, so nothing can be installed from inside
them. Instead, CyberLab builds a new image (the machine's image plus the package, installed
as root only during that build), then recreates the machine from it. /workspace is kept.

For software you supply yourself, e.g. Cisco Packet Tracer (which you download with your
own Cisco account). You are responsible for its licence. Installs last until the machine is
destroyed; creating the environments again from the manifest starts without them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cyberlab.api.deps import current_user, get_db, get_registry, request_id
from cyberlab.api.environments import (
    _env_out,
    _env_owned,
    _require_provider,
    _rewrite,
    _workspace_key,
    get_provider,
)
from cyberlab.api.files import _fail, _path
from cyberlab.api.routes import _owned
from cyberlab.config import Settings, get_settings
from cyberlab.db import User, record_audit
from cyberlab.execution import EnvState, ProviderAdapter, ProviderError, ProvisionRequest
from cyberlab.execution.docker import FileOpError
from cyberlab.manifest.models import Environment, NetworkMode
from cyberlab.registry import Registry

router = APIRouter(tags=["environments"])


class InstallIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


@router.post("/v1/workspaces/{ws_id}/environments/{env_id}/install")
def install_package(
    ws_id: str,
    env_id: str,
    body: InstallIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    reg: Registry = Depends(get_registry),
    settings: Settings = Depends(get_settings),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> dict:
    ws = _owned(db, user, ws_id)
    rec = _env_owned(db, ws, env_id)
    adapter = _require_provider(provider)
    if not hasattr(adapter, "install_package"):
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED, "this provider cannot install packages"
        )
    if rec.provider_ref is None or rec.state not in (EnvState.running, EnvState.stopped):
        raise HTTPException(status.HTTP_409_CONFLICT, "the machine must be running or stopped")
    full = _path(body.path)
    if not full.lower().endswith(".deb"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "only .deb packages can be installed"
        )
    env = Environment.model_validate(rec.spec)
    base = reg.base_images.get(env.base_image)
    if base is None or base.family != "debian":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            ".deb packages install on Ubuntu, Debian, Kali and Parrot OS machines only",
        )
    if env.network.mode == NetworkMode.restricted_egress:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "installing on a machine with allow-listed websites is not supported yet; "
            "use a machine with the lab or isolated network",
        )

    try:
        built = adapter.install_package(rec.provider_ref, full, settings.max_upload_bytes)
    except FileOpError as exc:
        raise _fail(exc) from exc
    except ProviderError as exc:
        rec.last_error = str(exc)
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    # Recreate the machine from the new image; its /workspace volume is kept.
    old_ref = rec.provider_ref
    try:
        adapter.destroy(old_ref, remove_workspace_volume=False)
        result = adapter.provision(
            ProvisionRequest(
                workspace_key=_workspace_key(ws),
                environment=env,
                image=base.image,
                family=base.family,
                source_rewrite=_rewrite(base),
                tool_packs=tuple(reg.tool_packs[p] for p in env.tool_packs),
                internet_approved=env.network.mode == NetworkMode.internet,
                image_override=built["image"],
            )
        )
    except ProviderError as exc:
        rec.state = EnvState.failed.value
        rec.last_error = f"recreating the machine after installing failed: {exc}"
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, rec.last_error) from exc
    installers = [*(rec.resources or {}).get("installers", [])]
    installers.append({"path": full, "sha256": built["sha256"], "bytes": built["bytes"]})
    rec.provider_ref = result.provider_ref
    rec.state = result.state.value
    rec.resources = {**result.resource_names, "image": built["image"], "installers": installers}
    rec.last_error = None
    record_audit(
        db,
        actor=user.subject,
        action="environment.install",
        target_type="environment",
        target_id=rec.id,
        detail={"name": rec.name, "path": full, "sha256": built["sha256"], "bytes": built["bytes"]},
        request_id=request_id(request),
    )
    db.commit()
    return _env_out(rec)
