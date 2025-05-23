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

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from twisted.internet import defer
from twisted.internet import reactor
from twisted.python import log
from zope.interface import implementer

from buildbot_worker import util
from buildbot_worker.exceptions import AbandonChain
from buildbot_worker.interfaces import IWorkerCommand

if TYPE_CHECKING:
    from typing import TypeVar

    from twisted.internet.defer import Deferred
    from twisted.internet.interfaces import IReactorTime
    from twisted.python.failure import Failure

    from buildbot_worker.base import ProtocolCommandBase

    _T = TypeVar("_T")

# The following identifier should be updated each time this file is changed
command_version = "3.3"

# version history:
#  >=1.17: commands are interruptable
#  >=1.28: Arch understands 'revision', added Bazaar
#  >=1.33: Source classes understand 'retry'
#  >=1.39: Source classes correctly handle changes in branch (except Git)
#          Darcs accepts 'revision' (now all do but Git) (well, and P4Sync)
#          Arch/Baz should accept 'build-config'
#  >=1.51: (release 0.7.3)
#  >= 2.1: SlaveShellCommand now accepts 'initial_stdin', 'keep_stdin_open',
#          and 'logfiles'. It now sends 'log' messages in addition to
#          stdout/stdin/header/rc. It acquired writeStdin/closeStdin methods,
#          but these are not remotely callable yet.
#          (not externally visible: ShellCommandPP has writeStdin/closeStdin.
#          ShellCommand accepts new arguments (logfiles=, initialStdin=,
#          keepStdinOpen=) and no longer accepts stdin=)
#          (release 0.7.4)
#  >= 2.2: added monotone, uploadFile, and downloadFile (release 0.7.5)
#  >= 2.3: added bzr (release 0.7.6)
#  >= 2.4: Git understands 'revision' and branches
#  >= 2.5: workaround added for remote 'hg clone --rev REV' when hg<0.9.2
#  >= 2.6: added uploadDirectory
#  >= 2.7: added usePTY option to SlaveShellCommand
#  >= 2.8: added username and password args to SVN class
#  >= 2.9: add depth arg to SVN class
#  >= 2.10: CVS can handle 'extra_options' and 'export_options'
#  >= 2.11: Arch, Bazaar, and Monotone removed
#  >= 2.12: SlaveShellCommand no longer accepts 'keep_stdin_open'
#  >= 2.13: SlaveFileUploadCommand supports option 'keepstamp'
#  >= 2.14: RemoveDirectory can delete multiple directories
#  >= 2.15: 'interruptSignal' option is added to SlaveShellCommand
#  >= 2.16: 'sigtermTime' option is added to SlaveShellCommand
#  >= 2.16: runprocess supports obfuscation via tuples (#1748)
#  >= 2.16: listdir command added to read a directory
#  >= 3.0: new buildbot-worker package:
#    * worker-side usePTY configuration (usePTY='slave-config') support
#      dropped,
#    * remote method getSlaveInfo() renamed to getWorkerInfo().
#    * "slavesrc" command argument renamed to "workersrc" in uploadFile and
#      uploadDirectory commands.
#    * "slavedest" command argument renamed to "workerdest" in downloadFile
#      command.
#  >= 3.1: rmfile command added to remove a file
#  >= 3.2: shell command now reports failure reason in case the command timed out.
#  >= 3.3: shell command now supports max_lines parameter.


@implementer(IWorkerCommand)
class Command:
    """This class defines one command that can be invoked by the build master.
    The command is executed on the worker side, and always sends back a
    completion message when it finishes. It may also send intermediate status
    as it runs (by calling builder.sendStatus). Some commands can be
    interrupted (either by the build master or a local timeout), in which
    case the step is expected to complete normally with a status message that
    indicates an error occurred.

    These commands are used by BuildSteps on the master side. Each kind of
    BuildStep uses a single Command. The worker must implement all the
    Commands required by the set of BuildSteps used for any given build:
    this is checked at startup time.

    All Commands are constructed with the same signature:
     c = CommandClass(builder, stepid, args)
    where 'builder' is the parent WorkerForBuilder object, and 'args' is a
    dict that is interpreted per-command.

    The setup(args) method is available for setup, and is run from __init__.
    Mandatory args can be declared by listing them in the requiredArgs property.
    They will be checked before calling the setup(args) method.

    The Command is started with start(). This method must be implemented in a
    subclass, and it should return a Deferred. When your step is done, you
    should fire the Deferred (the results are not used). If the command is
    interrupted, it should fire the Deferred anyway.

    While the command runs. it may send status messages back to the
    buildmaster by calling self.sendStatus(statusdict). The statusdict is
    interpreted by the master-side BuildStep however it likes.

    A separate completion message is sent when the deferred fires, which
    indicates that the Command has finished, but does not carry any status
    data. If the Command needs to return an exit code of some sort, that
    should be sent as a regular status message before the deferred is fired .
    Once builder.commandComplete has been run, no more status messages may be
    sent.

    If interrupt() is called, the Command should attempt to shut down as
    quickly as possible. Child processes should be killed, new ones should
    not be started. The Command should send some kind of error status update,
    then complete as usual by firing the Deferred.

    .interrupted should be set by interrupt(), and can be tested to avoid
    sending multiple error status messages.

    If .running is False, the bot is shutting down (or has otherwise lost the
    connection to the master), and should not send any status messages. This
    is checked in Command.sendStatus .

    """

    # builder methods:
    #  sendStatus(list of tuples) (zero or more)
    #  commandComplete() or commandInterrupted() (one, at end)

    requiredArgs: list[str] = []
    debug = False
    interrupted = False
    # set by Builder, cleared on shutdown or when the Deferred fires
    running = False

    _reactor: IReactorTime = cast("IReactorTime", reactor)

    def __init__(
        self,
        protocol_command: ProtocolCommandBase,
        command_id: str,
        args: Any,
    ) -> None:
        self.protocol_command = protocol_command
        self.command_id = command_id  # just for logging
        self.args = args
        self.startTime: float | None = None

        missingArgs = [arg for arg in self.requiredArgs if arg not in args]
        if missingArgs:
            raise ValueError(
                "{} is missing args: {}".format(self.__class__.__name__, ", ".join(missingArgs))
            )
        self.setup(args)

    def log_msg(self, msg: str, *args: Any) -> None:
        log.msg(f"(command {self.command_id}): {msg}", *args)

    def setup(self, args: dict[str, Any]) -> None:
        """Override this in a subclass to extract items from the args dict."""

    def doStart(self) -> Deferred:
        self.running = True
        self.startTime = util.now(self._reactor)
        d = defer.maybeDeferred(self.start)

        def commandComplete(res: _T) -> _T:
            assert self.startTime is not None
            self.sendStatus([("elapsed", util.now(self._reactor) - self.startTime)])
            self.running = False
            return res

        d.addBoth(commandComplete)
        return d

    def start(self) -> Deferred[None] | None:
        """Start the command. This method should return a Deferred that will
        fire when the command has completed. The Deferred's argument will be
        ignored.

        This method should be overridden by subclasses."""
        raise NotImplementedError("You must implement this in a subclass")

    def sendStatus(self, status: list[Any]) -> None:
        """Send a status update to the master."""
        if self.debug:
            self.log_msg(f"sendStatus: {status}")
        if not self.running:
            self.log_msg("would sendStatus but not .running")
            return
        self.protocol_command.send_update(status)

    def doInterrupt(self) -> None:
        self.running = False
        self.interrupt()

    def interrupt(self) -> Deferred[None] | None:
        """Override this in a subclass to allow commands to be interrupted.
        May be called multiple times, test and set self.interrupted=True if
        this matters."""

    # utility methods, mostly used by WorkerShellCommand and the like

    def _abandonOnFailure(self, rc: int) -> int:
        if not isinstance(rc, int):
            self.log_msg(f"weird, _abandonOnFailure was given rc={rc} ({type(rc)})")
        assert isinstance(rc, int)
        if rc != 0:
            raise AbandonChain(rc)
        return rc

    def _sendRC(self, res: int) -> None:
        self.sendStatus([('rc', 0)])

    def _checkAbandoned(self, why: Failure) -> None:
        self.log_msg("_checkAbandoned", why)
        why.trap(AbandonChain)
        self.log_msg(" abandoning chain", why.value)
        self.sendStatus([('rc', why.value.args[0])])
        return None
