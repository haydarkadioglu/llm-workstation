from fastapi import APIRouter, Request

from ..telemetry import get_system_telemetry
from ..tunnel import get_cloudflare_url

router = APIRouter()


@router.get("/api/telemetry")
def telemetry(request: Request):
    return {
        **request.app.state.manager.snapshot(),
        "transfer": request.app.state.transfers.job,
        "cloudflare_url": get_cloudflare_url(),
        "system": get_system_telemetry(),
    }
