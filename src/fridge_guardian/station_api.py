"""Loopback FastAPI bridge for the PN54 camera station."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Callable, Literal, Sequence
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from fridge_guardian.adapters.camera import OpenCVCamera
from fridge_guardian.adapters.face import FaceRecognitionSettings, SFaceIdentityProvider
from fridge_guardian.adapters.feedback import OpenCVFeedback
from fridge_guardian.adapters.item import SpatialHistogramItemRecognizer
from fridge_guardian.adapters.knowledge import FoodQuestions, LocalKnowledge
from fridge_guardian.adapters.sqlite_repository import SQLiteRepository
from fridge_guardian.application import SessionCoordinator
from fridge_guardian.application.fridge_service import FridgeService, PutOptions
from fridge_guardian.domain import Action, DecisionCode, FrameSample, utc_now


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger(__name__)
DEFAULT_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class OperateRequest(BaseModel):
    action: Action
    label: str | None = Field(default=None, max_length=200)
    shared: bool = False
    expires_on: date | None = None


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    category: Literal["recipes", "storage"] = "storage"


class CameraSessionCapture:
    """Capture short in-memory sessions from the one backend-owned camera."""

    def __init__(self, camera: OpenCVCamera, settings: FaceRecognitionSettings) -> None:
        self.camera = camera
        self.settings = settings

    def __call__(self) -> list[FrameSample]:
        if self.settings.prepare_seconds > 0:
            time.sleep(self.settings.prepare_seconds)
        session_id = str(uuid4())
        count = self.settings.recognition_candidate_frames
        duration = self.settings.recognition_window_seconds
        interval = duration / max(count, 1)
        frames: list[FrameSample] = []
        started = time.monotonic()
        for index in range(count):
            target = started + index * interval
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            frame = self.camera.read()
            frames.append(FrameSample(session_id, utc_now(), frame.copy()))
        return frames


class StationRuntime:
    """Own the service and serialize every station request on one event loop."""

    def __init__(
        self,
        service: FridgeService,
        capture_frames: Callable[[], Sequence[FrameSample]],
        *,
        questions: FoodQuestions | None = None,
        close: Callable[[], None] | None = None,
    ) -> None:
        self.service = service
        self.capture_frames = capture_frames
        self.questions = questions
        self.close_callback = close
        self.lock = asyncio.Lock()

    def close(self) -> None:
        if self.close_callback is not None:
            self.close_callback()


def _env_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, str(default))).expanduser().resolve()


def build_runtime() -> StationRuntime:
    """Create the real PN54/desktop adapters once during application startup."""
    settings = FaceRecognitionSettings.load(
        _env_path("FRIDGE_FACE_CONFIG", PROJECT_ROOT / "config" / "face.json")
    )
    repository = SQLiteRepository(
        _env_path("FRIDGE_DB_PATH", PROJECT_ROOT / "data" / "fridge_guardian.db")
    )
    camera: OpenCVCamera | None = None
    try:
        identity = SFaceIdentityProvider(
            _env_path(
                "FRIDGE_YUNET_PATH",
                PROJECT_ROOT / "models" / "face_detection_yunet_2023mar.onnx",
            ),
            _env_path(
                "FRIDGE_SFACE_PATH",
                PROJECT_ROOT / "models" / "face_recognition_sface_2021dec.onnx",
            ),
            settings,
        )
        items = SpatialHistogramItemRecognizer(
            threshold=float(os.environ.get("FRIDGE_ITEM_THRESHOLD", "0.70"))
        )
        feedback = OpenCVFeedback()
        coordinator = SessionCoordinator(repository, identity, items, feedback)
        service = FridgeService(coordinator)
        questions = FoodQuestions(
            service, LocalKnowledge(PROJECT_ROOT / "data" / "knowledge")
        )
        camera = OpenCVCamera(int(os.environ.get("FRIDGE_CAMERA_INDEX", "0")))

        def close() -> None:
            assert camera is not None
            camera.close()
            repository.close()

        return StationRuntime(
            service, CameraSessionCapture(camera, settings), questions=questions, close=close
        )
    except Exception:
        if camera is not None:
            camera.close()
        repository.close()
        raise


def _origins_from_environment() -> list[str]:
    configured = os.environ.get("FRIDGE_FRONTEND_ORIGINS")
    if configured is None:
        return list(DEFAULT_ORIGINS)
    origins = [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    if not origins or "*" in origins:
        raise ValueError("FRIDGE_FRONTEND_ORIGINS must contain explicit local origins")
    for origin in origins:
        parsed = urlparse(origin)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("FRIDGE_FRONTEND_ORIGINS may contain only explicit loopback HTTP origins")
    return origins


def _success(data: object) -> dict[str, object]:
    return {"success": True, "data": data}


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": {"code": code, "message": message}},
    )


def _token(authorization: str | None) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise ApiError(401, "UNAUTHORIZED", "A bearer token is required.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise ApiError(401, "UNAUTHORIZED", "A bearer token is required.")
    return token


def _outcome(code: DecisionCode) -> Literal["ALLOW", "WARNING", "UNKNOWN"]:
    if code is DecisionCode.WARN_NOT_OWNER or code is DecisionCode.ITEM_ALREADY_REGISTERED:
        return "WARNING"
    if code in {
        DecisionCode.UNKNOWN_ITEM,
        DecisionCode.NO_FACE,
        DecisionCode.UNKNOWN_USER,
        DecisionCode.AMBIGUOUS_USER,
    }:
        return "UNKNOWN"
    return "ALLOW"


def _operation_data(result) -> dict[str, object]:
    decision = result.decision
    return {
        "outcome": _outcome(decision.code),
        "decision": decision.code.value,
        "message": decision.message,
        "session_id": decision.session_id,
        "user_id": decision.user_id,
        "item_id": decision.item_id,
        "identity_confidence": decision.identity_confidence,
        "item_confidence": decision.item_confidence,
        "warnings": list(result.warnings),
        "decided_at": decision.decided_at.isoformat(),
    }


def create_app(
    runtime: StationRuntime | None = None,
    *,
    allowed_origins: Sequence[str] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned_runtime = runtime or build_runtime()
        app.state.station = owned_runtime
        try:
            yield
        finally:
            if runtime is None:
                owned_runtime.close()

    api = FastAPI(title="Fridge Guardian Station API", version="1.1.0", lifespan=lifespan)
    api.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins or _origins_from_environment()),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @api.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError):
        return _error(exc.status_code, exc.code, exc.message)

    @api.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        message = str(first.get("msg", "Invalid request."))
        return _error(422, "VALIDATION_ERROR", message)

    @api.exception_handler(Exception)
    async def internal_error_handler(_request: Request, exc: Exception):
        LOGGER.exception("Unhandled station API error", exc_info=exc)
        return _error(500, "INTERNAL_ERROR", "The local station could not complete the request.")

    @api.get("/api/v1/health")
    async def health(request: Request):
        station: StationRuntime = request.app.state.station
        async with station.lock:
            return _success({"status": "ok", "mode": "local", "camera_owner": "python"})

    @api.post("/api/v1/station/identify")
    async def identify(request: Request):
        station: StationRuntime = request.app.state.station
        async with station.lock:
            try:
                login = station.service.identify(station.capture_frames())
            except PermissionError as exc:
                raise ApiError(401, "UNKNOWN_USER", str(exc)) from exc
            return _success(
                {
                    "access_token": login.token,
                    "token_type": "bearer",
                    "user_id": login.user_id,
                    "display_name": login.display_name,
                    "expires_at": login.expires_at.isoformat(),
                }
            )

    @api.post("/api/v1/station/operate")
    async def operate(
        payload: OperateRequest,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ):
        station: StationRuntime = request.app.state.station
        token = _token(authorization)
        options = None
        if payload.action is Action.PUT_IN:
            label = (payload.label or "").strip()
            if not label:
                raise ApiError(422, "VALIDATION_ERROR", "PUT_IN requires a confirmed item label.")
            options = PutOptions(label, payload.shared, payload.expires_on)
        async with station.lock:
            try:
                # Reject expired/invalid tokens before opening a fresh camera session.
                station.service._login(token)
                result = station.service.process(
                    token, payload.action, station.capture_frames(), options
                )
            except PermissionError as exc:
                message = str(exc)
                changed_person = "person changed" in message.lower()
                code = "PERSON_CHANGED" if changed_person else "UNAUTHORIZED"
                status = 403 if changed_person else 401
                raise ApiError(status, code, message) from exc
            except ValueError as exc:
                raise ApiError(422, "VALIDATION_ERROR", str(exc)) from exc
            return _success(_operation_data(result))

    @api.get("/api/v1/inventory")
    async def inventory(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ):
        station: StationRuntime = request.app.state.station
        token = _token(authorization)
        async with station.lock:
            try:
                items = station.service.inventory(token)
            except PermissionError as exc:
                raise ApiError(401, "UNAUTHORIZED", str(exc)) from exc
            return _success({"items": items})

    @api.post("/api/v1/questions")
    async def ask_question(
        payload: QuestionRequest,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ):
        station: StationRuntime = request.app.state.station
        token = _token(authorization)
        if station.questions is None:
            raise ApiError(503, "RAG_NOT_CONFIGURED", "Food guidance is not configured.")
        async with station.lock:
            try:
                answer = station.questions.ask(token, payload.question.strip(), payload.category)
            except PermissionError as exc:
                raise ApiError(401, "UNAUTHORIZED", str(exc)) from exc
            except ValueError as exc:
                raise ApiError(422, "VALIDATION_ERROR", str(exc)) from exc
            return _success(
                {
                    "status": answer.status,
                    "answer": answer.text,
                    "sources": [
                        {"source": passage.source, "text": passage.text}
                        for passage in answer.passages
                    ],
                }
            )

    return api


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "fridge_guardian.station_api:app",
        host=os.environ.get("FRIDGE_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("FRIDGE_API_PORT", "8000")),
        reload=False,
        workers=1,
    )
