# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members

# This module is left for backward compatibility of old-named worker API.
# It should never be imported by Buildbot.
from __future__ import annotations

from typing import Any

from buildbot_worker.scripts import base
from buildbot_worker.scripts import start
from buildbot_worker.scripts import stop


def restart(config: dict[str, Any]) -> int:
    quiet = config['quiet']
    basedir = config['basedir']

    if not base.isWorkerDir(basedir):
        return 1

    try:
        stop.stopWorker(basedir, quiet)
    except stop.WorkerNotRunning:
        if not quiet:
            print("no old worker process found to stop")
    if not quiet:
        print("now restarting worker process..")

    return start.startWorker(basedir, quiet, config['nodaemon'])
