import asyncio
from pathlib import Path

from app.config import Settings
from app.engine import Engine
from app.models import BatchInput


def run(coro):
    return asyncio.run(coro)


def farm(tmp_path):
    engine = Engine(Settings(data_dir=tmp_path, start_scheduler=False))
    engine.state['scheduler_enabled'] = True
    return engine


def batch(engine, **changes):
    data = dict(name='Widget', file_id=engine.state['files'][0]['id'], quantity=1,
                profile='demo-cr10', volume=[80, 60, 40], nozzle=.4,
                material='PLA', color='', grams=25, duration_minutes=30,
                priority=50, not_before='')
    data.update(changes)
    return run(engine.create_batch(BatchInput(**data)))['jobs']


def test_clear_bed_is_required_and_job_is_not_dispatched_twice(tmp_path):
    engine = farm(tmp_path)
    job = batch(engine)[0]
    run(engine.tick())
    assert job['state'] == 'queued'
    printer = engine.state['printers'][0]
    run(engine.clear_bed(printer['id']))
    run(engine.tick())
    assert job['state'] == 'running'
    assert printer['bed_clear'] is False
    run(engine.tick())
    assert engine.gateway.start_count == 1


def test_incompatible_profile_and_future_time_are_not_assigned(tmp_path):
    engine = farm(tmp_path)
    first = batch(engine, profile='wrong-profile')[0]
    future = batch(engine, not_before='2099-01-01T00:00:00Z')[0]
    run(engine.tick())
    run(engine.clear_bed(engine.state['printers'][0]['id']))
    run(engine.tick())
    assert first['state'] == future['state'] == 'queued'


def test_insufficient_filament_blocks_assignment(tmp_path):
    engine = farm(tmp_path)
    job = batch(engine, grams=10000)[0]
    run(engine.tick())
    run(engine.clear_bed(engine.state['printers'][0]['id']))
    run(engine.tick())
    assert job['state'] == 'queued'


def test_uncertain_start_is_held_and_never_retried(tmp_path):
    engine = farm(tmp_path)
    job = batch(engine)[0]
    run(engine.tick())
    run(engine.clear_bed(engine.state['printers'][0]['id']))
    async def timeout(*args):
        raise TimeoutError('response lost')
    engine.gateway.start = timeout
    run(engine.tick())
    assert job['state'] == 'needs_attention'
    assert job['printer_id']
    run(engine.tick())
    assert job['state'] == 'needs_attention'
    assert engine.snapshot()['spools'][0]['reserved_g'] == 25


def test_restart_resets_clearance_and_pauses_dispatch(tmp_path):
    engine = farm(tmp_path)
    run(engine.tick())
    run(engine.clear_bed(engine.state['printers'][0]['id']))
    second = Engine(Settings(data_dir=tmp_path, start_scheduler=False))
    assert not second.state['scheduler_enabled']
    assert not any(p['bed_clear'] for p in second.state['printers'])


def test_batched_jobs_are_durable_individual_copies(tmp_path):
    engine = farm(tmp_path)
    jobs = batch(engine, quantity=3)
    assert len({j['id'] for j in jobs}) == 3
    assert len({j['batch_id'] for j in jobs}) == 1
    second = Engine(Settings(data_dir=tmp_path, start_scheduler=False))
    assert len(second.state['jobs']) == 3


def test_completion_is_matched_by_unique_filename(tmp_path):
    engine = farm(tmp_path)
    job = batch(engine)[0]
    run(engine.tick())
    run(engine.clear_bed(engine.state['printers'][0]['id']))
    run(engine.tick())
    device = engine.gateway.devices[job['printer_id']]
    device['state'] = 'complete'
    device['filename'] = 'unrelated.gcode'
    run(engine.tick())
    assert job['state'] == 'needs_attention'


def test_completion_does_not_clear_bed(tmp_path):
    engine = farm(tmp_path)
    job = batch(engine)[0]
    run(engine.tick())
    printer = engine.state['printers'][0]
    run(engine.clear_bed(printer['id']))
    run(engine.tick())
    engine.gateway.devices[printer['id']]['state'] = 'complete'
    run(engine.tick())
    assert job['state'] == 'completed'
    assert not printer['bed_clear']


def test_inventory_failure_blocks_dispatch_with_old_inventory(tmp_path):
    engine = farm(tmp_path)
    job = batch(engine)[0]
    run(engine.tick())
    run(engine.clear_bed(engine.state['printers'][0]['id']))
    async def offline():
        raise ConnectionError('Spoolman is offline')
    engine.gateway.spools = offline
    run(engine.tick())
    assert job['state'] == 'queued'
    assert engine.state['spoolman_error']


def test_pending_consumption_keeps_reservation(tmp_path):
    engine = farm(tmp_path)
    job = batch(engine)[0]
    run(engine.tick())
    run(engine.clear_bed(engine.state['printers'][0]['id']))
    run(engine.tick())
    engine.gateway.devices[job['printer_id']].update(state='complete', pending_reports=True)
    run(engine.tick())
    assert job['state'] == 'running'
    assert engine.snapshot()['spools'][0]['reserved_g'] == 25
    engine.gateway.devices[job['printer_id']]['pending_reports'] = False
    run(engine.tick())
    assert job['state'] == 'completed'


def test_disconnect_invalidates_clearance(tmp_path):
    engine = farm(tmp_path)
    run(engine.tick())
    printer = engine.state['printers'][0]
    run(engine.clear_bed(printer['id']))
    async def offline(_printer):
        raise ConnectionError('Disconnected')
    engine.gateway.status = offline
    run(engine.tick())
    assert not printer['bed_clear']


def test_concurrent_ticks_dispatch_only_once(tmp_path):
    engine = farm(tmp_path)
    batch(engine, quantity=2)
    async def exercise():
        await engine.tick()
        await engine.clear_bed(engine.state['printers'][0]['id'])
        await asyncio.gather(engine.tick(), engine.tick(), engine.tick())
    run(exercise())
    assert engine.gateway.start_count == 1
    assert sorted(j['state'] for j in engine.state['jobs']) == ['queued', 'running']


def test_intervening_print_during_upload_invalidates_clearance(tmp_path):
    engine = farm(tmp_path)
    job = batch(engine)[0]
    run(engine.tick())
    printer = engine.state['printers'][0]
    run(engine.clear_bed(printer['id']))
    async def changed_during_upload(printer, path, filename):
        engine.gateway.devices[printer['id']].update(state='complete', filename='manual-job.gcode')
        return filename
    engine.gateway.upload = changed_during_upload
    run(engine.tick())
    assert job['state'] == 'needs_attention'
    assert engine.gateway.start_count == 0


def test_omitted_scheduled_time_defaults_to_now(tmp_path):
    engine = farm(tmp_path)
    data = dict(name='Widget', file_id=engine.state['files'][0]['id'], quantity=1,
                profile='demo-cr10', volume=[80,60,40], nozzle=.4, material='PLA',
                grams=25, duration_minutes=30)
    job = run(engine.create_batch(BatchInput(**data)))['jobs'][0]
    assert job['not_before']
    run(engine.tick())


def test_uncommitted_consumption_cannot_fund_another_job(tmp_path):
    engine = farm(tmp_path)
    job = batch(engine, grams=700)[0]
    run(engine.tick())
    printer = engine.state['printers'][0]
    run(engine.clear_bed(printer['id']))
    run(engine.tick())
    engine.gateway.devices[printer['id']].update(state='complete', pending_reports=False)
    run(engine.tick())
    assert job['state'] == 'completed'
    second = batch(engine, grams=100)[0]
    run(engine.clear_bed(printer['id']))
    run(engine.tick())
    assert second['state'] == 'queued'
    assert engine.snapshot()['spools'][0]['available_g'] == 40


def test_budget_survives_restart_and_resolution_until_explicit_reconcile(tmp_path):
    import pytest
    from fastapi import HTTPException
    engine = farm(tmp_path)
    job = batch(engine, grams=700)[0]
    run(engine.tick())
    run(engine.clear_bed(engine.state['printers'][0]['id']))
    async def timeout(*args):
        raise TimeoutError('uncertain')
    engine.gateway.start = timeout
    run(engine.tick())
    with pytest.raises(HTTPException) as caught:
        run(engine.reconcile_spool(1))
    assert caught.value.status_code == 409
    second = Engine(Settings(data_dir=tmp_path, start_scheduler=False))
    run(second.sync())
    assert second.snapshot()['spools'][0]['available_g'] == 40
    run(second.resolve_job(job['id'], 'failed'))
    assert second.snapshot()['spools'][0]['available_g'] == 40
    run(second.reconcile_spool(1))
    assert second.snapshot()['spools'][0]['available_g'] == 740
