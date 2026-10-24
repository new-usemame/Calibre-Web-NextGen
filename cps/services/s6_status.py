# SPDX-License-Identifier: GPL-3.0-or-later
"""Liveness of the container's critical s6 longruns.

This lives outside ``cps.web`` so background jobs (the acquisition scheduler
job among them) can ask about the ingest service without importing a route
module from their own thread (#2648).
"""
import shutil
import subprocess

# The longruns whose liveness directly determines whether the container is
# functionally useful. ``cwa-ingest-service`` watches /cwa-book-ingest and
# moves new files into the library; ``metadata-change-detector`` propagates
# metadata edits back to the underlying book files. If either has died and
# isn't being restarted, the container can still serve /health and read
# metadata.db but the user-visible features dependent on those services are
# broken — exactly the failure mode reported in fork #193 (droM4X) and the
# ~200k-book production trace from @FRaccie.
CRITICAL_LONGRUNS = ("cwa-ingest-service", "metadata-change-detector")


def check_s6_service_status():
    """Probe live state of each :data:`CRITICAL_LONGRUNS` longrun via
    ``s6-rc -a list``.

    ``s6-rc -a list`` enumerates the services currently active under
    s6-rc and returns their names on stdout. Stopping a service via
    ``s6-rc -d change <name>`` removes it from this output, so membership
    is a reliable "is the service currently up" signal. We use this
    primitive rather than ``s6-svstat`` because the latter requires read
    access to ``/run/service/<svc>/supervise/`` (root-only in the lsio
    base image) while the Flask app runs as ``abc``. ``s6-rc -a list``
    reads world-readable state under ``/run/s6/db`` and works fine for
    unprivileged callers — that's the same primitive the existing
    ``scripts/check-cwa-services.sh`` (consumed by the admin UI's "Check
    NextGen Status" action) already relies on.

    Returns a dict ``{service_name: "up" | "down" | "unknown"}``. The
    ``unknown`` value covers any environment where ``s6-rc`` isn't on
    PATH (the unit-test harness, dev compose without s6, k8s sidecars) —
    not a known-bad state, so the caller treats ``unknown`` as a no-op
    for the 503 decision.
    """
    s6_rc = shutil.which("s6-rc")
    if not s6_rc:
        return {service: "unknown" for service in CRITICAL_LONGRUNS}

    try:
        completed = subprocess.run(
            [s6_rc, "-a", "list"],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {service: "unknown" for service in CRITICAL_LONGRUNS}

    if completed.returncode != 0:
        return {service: "unknown" for service in CRITICAL_LONGRUNS}

    active = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
    return {service: ("up" if service in active else "down") for service in CRITICAL_LONGRUNS}
