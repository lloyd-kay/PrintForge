# PrintForge MVP implementation plan

**Goal:** a runnable local simulator and hardware-ready first implementation of the architecture in ARCHITECTURE.md.

**Architecture:** one FastAPI application, SQLite and integrated serialized scheduler, React/TypeScript frontend, Moonraker and Spoolman HTTP adapters. One process only, real-mode token required, demo isolated.

## Work units

- [x] Backend: persistence and domain validation, authenticated API, demo telemetry, scheduler reservations and conservative recovery. Tests for incompatibility, duplicate dispatch, unclear bed, shared spool inventory, timing, ambiguous start and restart.
- [x] Integrations: verified Moonraker upload/query/start/spool routes and Spoolman inventory reads, bounded timeouts and no hidden retries. Tests with HTTP fixture transports.
- [x] Frontend: overview, printers/cameras, queue and batch form, library, filament and history, login and visible failures. Build and browser smoke tests.
- [x] Delivery: Dockerfile, Compose, optional Spoolman, Portainer image stack, sample configuration, AGPL license, operator guide and local repository handoff.
- [x] Review: independent code review, fix material findings, test suite, build, package clean source archive.

See VERIFICATION.md for evidence and pending hardware/container acceptance. Deploying to a real farm is a separate acceptance step.

## Review focus

An uncertain physical side effect must never trigger blind retry. A repeated complete telemetry sample must not decrement or finish twice. Stale inventory and offline telemetry must prevent dispatch. Same-profile matching must be explicit because geometry alone cannot certify G-code compatibility. A browser failure or token rejection must be shown rather than silently swallowed.

The user requested architecture plus a working MVP. Local implementation proceeds under that authorization; repository access and real printer credentials are not needed until integration/deployment to their farm.
