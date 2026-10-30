# SPDX-License-Identifier: GPL-3.0-or-later
"""Whether the acquisition worker can run: shared by the API and the scheduler job.

The scheduler job runs on APScheduler's thread and may fire while the main
thread is still importing route modules at startup. Nothing reachable from
here may import a module that defines a Flask blueprint; doing so once left
``api_v1`` registered without the acquisition routes (#2648).
"""
import os
from pathlib import Path

from ... import config, constants, ub
from . import admission, runtime


def run_private_blocking(operation):
    # gevent's threadpool reports uncaught worker exceptions to stderr before
    # a waiting request can sanitize them. Keep errors as private return values
    # inside the worker; route error handling remains on the request thread.
    from ..parallel import run_blocking
    def invoke():
        try:
            return True,operation()
        except Exception as error:
            return False,error
    successful,value=run_blocking(invoke)
    if not successful:
        raise value from None
    return value


def worker_available():
    """Runtime preconditions only: a catalog probe does not prove import success.

    First slice requires the existing s6 ingest service. Unknown/non-s6 runtimes
    are unavailable rather than accepting jobs into an unverified queue.
    """
    from ...schedule import acquisition_scheduler_available
    from ..s6_status import check_s6_service_status
    reasons=[]
    if not acquisition_scheduler_available(): reasons.append('scheduler_unavailable')
    if admission.instance_state(ub.app_DB_path)['migration_status'] != 'ready': reasons.append('migration_unavailable')
    if not admission.configured_media_types(ub.app_DB_path): reasons.append('formats_disabled')
    try:
        key=Path(ub.app_DB_path).parent/'acquisition.key'
        key_present=key.is_file() and not key.is_symlink()
    except (TypeError,OSError):
        key_present=False
    if not key_present:
        reasons.append('key_unavailable')
    else:
        database=ub.app_DB_path
        def repository_ready():
            try:
                with runtime.open_repository(database):
                    return True
            except Exception:
                return False
        if not run_private_blocking(repository_ready): reasons.append('repository_unavailable')
    try:
        ingest=Path(constants.ingest_folder())
        if not ingest.is_dir() or not os.access(ingest,os.W_OK|os.X_OK): reasons.append('ingest_unwritable')
    except (OSError,TypeError): reasons.append('ingest_unavailable')
    try:
        if not config.config_calibre_dir or not Path(config.config_calibre_dir).is_dir(): reasons.append('library_unavailable')
    except (OSError,TypeError): reasons.append('library_unavailable')
    try:
        running=run_private_blocking(check_s6_service_status).get('cwa-ingest-service') == 'up'
    except Exception:
        running=False
    if not running: reasons.append('ingest_service_unavailable')
    return {'available':not reasons,'reasons':reasons}
