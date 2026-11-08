import asyncio
import contextlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from filelock import FileLock, Timeout

from .config import Settings
from .engine import Engine
from .models import BatchInput, ControlInput, DiscoveryInput, PrinterInput, ResolveInput, SchedulerInput


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    settings.validate()

    @asynccontextmanager
    async def lifespan(app):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        process_lock = FileLock(str(settings.data_dir / 'service.lock'))
        try:
            process_lock.acquire(timeout=0)
        except Timeout:
            raise RuntimeError('PrintForge already runs against this data directory. Use exactly one process.')
        task = None
        try:
            app.state.engine = Engine(settings)
            async def poll_loop():
                while True:
                    try:
                        await app.state.engine.tick()
                    except Exception:
                        logging.exception('Scheduler stopped dispatching after an unexpected error')
                        app.state.engine.state['scheduler_enabled'] = False
                        app.state.engine.save()
                    await asyncio.sleep(settings.poll_seconds)
            if settings.start_scheduler:
                task = asyncio.create_task(poll_loop())
            else:
                await app.state.engine.sync()
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            process_lock.release()

    app = FastAPI(title='PrintForge', version='0.2.0', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    security = HTTPBearer(auto_error=False)

    async def auth(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
        if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), settings.token.encode()):
            raise HTTPException(401, 'A valid PrintForge administrator token is required', headers={'WWW-Authenticate': 'Bearer'})

    def engine():
        return app.state.engine

    @app.get('/api/health')
    async def health():
        return {'status': 'ok', 'mode': settings.mode, 'version': '0.2.0'}

    protected = [Depends(auth)]

    @app.get('/api/state', dependencies=protected)
    async def state():
        async with engine().lock:
            return engine().snapshot()

    @app.post('/api/printers', dependencies=protected)
    async def add_printer(data: PrinterInput):
        return await engine().configure_printer(data)

    @app.post('/api/printers/discover', dependencies=protected)
    async def discover_printer(data: DiscoveryInput):
        return await engine().discover_printer(data)

    @app.put('/api/printers/{printer_id}', dependencies=protected)
    async def edit_printer(printer_id: str, data: PrinterInput):
        return await engine().configure_printer(data, printer_id)

    @app.post('/api/printers/{printer_id}/clear-bed', dependencies=protected)
    async def clear(printer_id: str):
        await engine().clear_bed(printer_id)
        return {'ok': True}

    @app.post('/api/printers/{printer_id}/control', dependencies=protected)
    async def control(printer_id: str, data: ControlInput):
        await engine().control(printer_id, data.action)
        return {'ok': True}

    @app.post('/api/files', dependencies=protected)
    async def upload(file: UploadFile = File(...)):
        filename = (file.filename or '').replace('\\', '/').rsplit('/', 1)[-1]
        if not filename.lower().endswith('.gcode') or len(filename) > 200:
            raise HTTPException(400, 'Upload a .gcode file with a filename under 200 characters')
        content = await file.read(50 * 1024 * 1024 + 1)
        await file.close()
        if not content or len(content) > 50 * 1024 * 1024:
            raise HTTPException(400, 'G-code must be nonempty and no larger than 50 MiB')
        return await engine().add_file(filename, content)

    @app.post('/api/jobs/batch', dependencies=protected)
    async def batch(data: BatchInput):
        return await engine().create_batch(data)

    @app.post('/api/jobs/{job_id}/cancel', dependencies=protected)
    async def cancel(job_id: str):
        await engine().cancel_job(job_id)
        return {'ok': True}

    @app.post('/api/jobs/{job_id}/resolve', dependencies=protected)
    async def resolve(job_id: str, data: ResolveInput):
        await engine().resolve_job(job_id, data.outcome)
        return {'ok': True}

    @app.post('/api/scheduler', dependencies=protected)
    async def scheduler(data: SchedulerInput):
        await engine().set_scheduler(data.enabled)
        return {'ok': True}

    @app.post('/api/sync', dependencies=protected)
    async def sync():
        await engine().sync()
        return {'ok': True}

    @app.post('/api/spools/{spool_id}/reconcile', dependencies=protected)
    async def reconcile_spool(spool_id: int):
        await engine().reconcile_spool(spool_id)
        return {'ok': True}

    @app.get('/api/{unknown:path}', dependencies=protected)
    async def unknown_api(unknown: str):
        raise HTTPException(404, 'API route not found')

    frontend = Path(__file__).resolve().parent.parent / 'frontend' / 'dist'
    if frontend.exists():
        app.mount('/', StaticFiles(directory=frontend, html=True), name='frontend')
    return app
