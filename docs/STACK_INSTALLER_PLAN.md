# All-in-one native installer, v0.2.0

The installer runs once on the farm's Debian 13 Klipper host. PrintForge, Spoolman and Moonraker remain independent services. PrintForge connects to Moonraker by its existing HTTP API key; Moonraker reports usage to Spoolman; PrintForge reads that inventory. No Klipper firmware or authentication policy is changed.

Implementation milestones:

1. Install or reuse a verified Spoolman service. Pin its official compiled ZIP and SHA-256, use a dedicated account/final-path Python environment, preserve persistent data, and health-check activation with rollback on failure.
2. Discover local Moonraker data paths and sockets from active services and conventional directories. Read the existing API key over the local socket and verify HTTP access. Inspect effective and on-disk configuration, including includes, before selecting an inventory server.
3. Connect the same Spoolman inventory everywhere. Back up changed files. Guard Moonraker configuration/restart with fail-closed idle checks and verify its Spoolman connection afterward. Report partial/pending setup explicitly; no background restart loop.
4. Add authenticated PrintForge discovery registration. Import advisory metadata and absolute cameras, preserve existing printer settings on repeat, redact credentials, and require operator review before enabling new printers.
5. Verify focused unit tests, full backend regression suite, frontend build, release contents and an actual Debian 13 systemd install/reinstall with real Spoolman and a Moonraker protocol fixture. Publish a new immutable prerelease and verify anonymous downloads.

Scope boundaries: remote hosts still need their connection details/configuration; physical spool selection and printer/slicer compatibility require review. Real printer hardware remains a separate pilot. Belt/resin automatic printing and optional local AI remain later milestones.
