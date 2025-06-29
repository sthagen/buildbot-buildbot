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

from collections.abc import Iterable
from copy import deepcopy
from functools import reduce
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import cast

from twisted.internet import defer
from twisted.internet import error
from twisted.python import failure
from twisted.python import log
from twisted.python.failure import Failure

from buildbot import interfaces
from buildbot.process import buildstep
from buildbot.process import metrics
from buildbot.process import properties
from buildbot.process.buildstep import BuildStep
from buildbot.process.locks import get_real_locks_from_accesses
from buildbot.process.results import CANCELLED
from buildbot.process.results import EXCEPTION
from buildbot.process.results import FAILURE
from buildbot.process.results import RETRY
from buildbot.process.results import SKIPPED
from buildbot.process.results import SUCCESS
from buildbot.process.results import WARNINGS
from buildbot.process.results import computeResultAndTermination
from buildbot.process.results import statusToString
from buildbot.process.results import worst_status
from buildbot.reporters.utils import getURLForBuild
from buildbot.util import Notifier
from buildbot.util import bytes2unicode
from buildbot.util.eventual import eventually

if TYPE_CHECKING:
    from collections.abc import Iterator
    from collections.abc import Sequence
    from pathlib import PurePath

    from twisted.internet.defer import Deferred

    from buildbot.interfaces import IBuildStep
    from buildbot.interfaces import IBuildStepFactory
    from buildbot.interfaces import IMaybeRenderableType
    from buildbot.interfaces import IProperties
    from buildbot.locks import BaseLock
    from buildbot.locks import BaseLockId
    from buildbot.process.builder import Builder
    from buildbot.process.buildrequest import BuildRequest
    from buildbot.process.buildrequest import TempChange
    from buildbot.process.buildrequest import TempSourceStamp
    from buildbot.process.properties import Properties
    from buildbot.process.workerforbuilder import AbstractWorkerForBuilder
    from buildbot.process.workerforbuilder import WorkerForBuilder
    from buildbot.util.subscription import Subscription
    from buildbot.util.twisted import InlineCallbacksType
    from buildbot.worker.protocols.base import Connection


class Build(properties.PropertiesMixin):
    """I represent a single build by a single worker. Specialized Builders can
    use subclasses of Build to hold status information unique to those build
    processes.

    I control B{how} the build proceeds. The actual build is broken up into a
    series of steps, saved in the .buildSteps[] array as a list of
    L{buildbot.process.step.BuildStep} objects. Each step is a single remote
    command, possibly a shell command.

    After the build, I go away.

    I can be used by a factory by setting buildClass on
    L{buildbot.process.factory.BuildFactory}

    @ivar requests: the list of L{BuildRequest}s that triggered me
    """

    VIRTUAL_BUILDERNAME_PROP = "virtual_builder_name"
    VIRTUAL_BUILDERDESCRIPTION_PROP = "virtual_builder_description"
    VIRTUAL_BUILDER_PROJECT_PROP = "virtual_builder_project"
    VIRTUAL_BUILDERTAGS_PROP = "virtual_builder_tags"
    workdir = "build"
    workername: str | None
    reason = "changes"
    finished = False
    results: int | None = None
    stopped = False
    set_runtime_properties = True

    class Sentinel:
        pass

    _sentinel = Sentinel()  # used as a sentinel to indicate unspecified initial_value

    def __init__(self, requests: list[BuildRequest], builder: Builder) -> None:
        self.requests = requests
        self.builder = builder
        assert builder.master is not None
        self.master = builder.master
        self.workerforbuilder: AbstractWorkerForBuilder | None = None

        self.locks: IMaybeRenderableType[Iterable[BaseLockId]] = []  # list of lock accesses

        # list of (real_lock, access) tuples
        self._locks_to_acquire: list[tuple[BaseLock, str]] = []
        # build a source stamp
        self.sources: list[TempSourceStamp] = requests[0].mergeSourceStampsWith(requests[1:])
        self.reason = requests[0].mergeReasons(requests[1:])

        self._preparation_step: buildstep.BuildStep | None = None
        self._locks_acquire_step: buildstep.BuildStep | None = None
        self.currentStep: IBuildStep | None = None

        self.workerEnvironment: dict[str, str] = {}
        self.buildid: int | None = None
        self._buildid_notifier: Notifier[int] = Notifier()
        self.number: int | None = None
        self.executedSteps: list[IBuildStep] = []
        self.stepnames: dict[str, int] = {}

        self.subs: Subscription | None = None
        self.conn: Connection | None = None

        self.terminate = False

        self._acquiringLock: tuple[BaseLock, str, Deferred[None]] | None = None
        self._builderid: int | None = None
        # overall results, may downgrade after each step
        self.results = SUCCESS
        self.properties = properties.Properties()
        self.stopped_reason: str | None = None

        # tracks execution during the build finish phase
        self._locks_released = False
        self._build_finished = False

        # tracks execution during substantiation
        self._is_substantiating = False

        # tracks the config version for locks
        self.config_version = builder.config_version

        self.env: dict[str, Any] = {}
        if builder.config is not None:
            self.env.update(deepcopy(builder.config.env))

    def getProperties(self) -> IProperties:
        return self.properties

    def setLocks(self, locks: IMaybeRenderableType[Iterable[BaseLockId]]) -> None:
        if isinstance(locks, Iterable):
            # copy the passed list so that changes by the caller do not modify stored instance
            self.locks = list(locks)
        else:
            self.locks = locks

    @defer.inlineCallbacks
    def _setup_locks(self) -> InlineCallbacksType[None]:
        self._locks_to_acquire = yield get_real_locks_from_accesses(self.locks, self)

    def setWorkerEnvironment(self, env: dict[str, str]) -> None:
        # TODO: remove once we don't have anything depending on this method or attribute
        # e.g., old-style steps (ShellMixin pulls the environment out of the
        # builder directly)
        self.workerEnvironment = env

    def getSourceStamp(self, codebase: str = '') -> TempSourceStamp | None:
        for source in self.sources:
            if source.codebase == codebase:
                return source
        return None

    def getAllSourceStamps(self) -> list[TempSourceStamp]:
        return list(self.sources)

    @staticmethod
    def allChangesFromSources(
        sources: Iterable[TempSourceStamp],
    ) -> Iterator[TempChange]:
        for s in sources:
            yield from s.changes

    def allChanges(self) -> Iterator[TempChange]:
        return Build.allChangesFromSources(self.sources)

    def allFiles(self) -> list[str]:
        # return a list of all source files that were changed
        files: set[str] = set()
        for c in self.allChanges():
            files.update(c.files)
        return list(files)

    def __repr__(self) -> str:
        return (
            f"<Build {self.builder.name} number:{self.number!r} "
            f"results:{statusToString(self.results)}>"
        )

    def blamelist(self) -> list[str]:
        # Note that this algorithm is also implemented in
        # buildbot.reporters.utils.getResponsibleUsersForBuild, but using the data api.
        # it is important for the UI to have the blamelist easily available.
        # The best way is to make sure the owners property is set to full blamelist
        blamelist: list[str] = []
        for c in self.allChanges():
            if c.who not in blamelist:
                blamelist.append(c.who)
        for source in self.sources:
            if source.patch:  # Add patch author to blamelist
                blamelist.append(source.patch_info[0])
        blamelist.sort()
        return blamelist

    def changesText(self) -> str:
        changetext = ""
        for c in self.allChanges():
            changetext += "-" * 60 + "\n\n" + c.asText() + "\n"
        # consider sorting these by number
        return changetext

    def setStepFactories(self, step_factories: Iterable[IBuildStepFactory]) -> None:
        """Set a list of 'step factories', which are tuples of (class,
        kwargs), where 'class' is generally a subclass of step.BuildStep .
        These are used to create the Steps themselves when the Build starts
        (as opposed to when it is first created). By creating the steps
        later, their __init__ method will have access to things like
        build.allFiles() ."""
        self.stepFactories = list(step_factories)

    useProgress = True

    def getWorkerCommandVersion(self, command: str, oldversion: str | None = None) -> str:
        assert self.workerforbuilder is not None
        return self.workerforbuilder.getWorkerCommandVersion(command, oldversion)

    def getWorkerName(self) -> str | None:
        return self.workername

    @staticmethod
    @defer.inlineCallbacks
    def setup_properties_known_before_build_starts(
        props: Properties,
        requests: Sequence[BuildRequest],
        builder: Builder,
        workerforbuilder: WorkerForBuilder | None = None,
    ) -> InlineCallbacksType[None]:
        # Note that this function does not setup the 'builddir' worker property
        # It's not possible to know it until before the actual worker has
        # attached.

        # start with global properties from the configuration
        assert builder.master is not None
        props.updateFromProperties(builder.master.config.properties)

        # from the SourceStamps, which have properties via Change
        sources = requests[0].mergeSourceStampsWith(requests[1:])
        for change in Build.allChangesFromSources(sources):
            props.updateFromProperties(change.properties)

        # get any properties from requests (this is the path through which
        # schedulers will send us properties)
        for rq in requests:
            props.updateFromProperties(rq.properties)

        # get builder properties
        yield builder.setup_properties(props)

        # get worker properties
        # navigate our way back to the L{buildbot.worker.Worker}
        # object that came from the config, and get its properties
        if workerforbuilder is not None:
            assert workerforbuilder.worker is not None
            workerforbuilder.worker.setupProperties(props)

    @staticmethod
    def setupBuildProperties(
        props: IProperties,
        requests: Sequence[BuildRequest],
        sources: Sequence[TempSourceStamp] | None = None,
        number: int | None = None,
    ) -> None:
        # now set some properties of our own, corresponding to the
        # build itself
        props.setProperty("buildnumber", number, "Build")

        if sources is None:
            sources = requests[0].mergeSourceStampsWith(requests[1:])

        if sources and len(sources) == 1:
            # old interface for backwards compatibility
            source = sources[0]
            props.setProperty("branch", source.branch, "Build")
            props.setProperty("revision", source.revision, "Build")
            props.setProperty("repository", source.repository, "Build")
            props.setProperty("codebase", source.codebase, "Build")
            props.setProperty("project", source.project, "Build")

    def setupWorkerProperties(self, workerforbuilder: AbstractWorkerForBuilder) -> None:
        assert workerforbuilder.worker is not None
        path_module = workerforbuilder.worker.path_module

        # navigate our way back to the L{buildbot.worker.Worker}
        # object that came from the config, and get its properties
        worker_basedir = workerforbuilder.worker.worker_basedir
        if worker_basedir:
            assert self.builder.config is not None
            builddir = path_module.join(
                bytes2unicode(worker_basedir),
                bytes2unicode(self.builder.config.workerbuilddir),
            )
            self.setProperty("basedir", worker_basedir, "Worker")
            self.setProperty("builddir", builddir, "Worker")

    def setupWorkerForBuilder(self, workerforbuilder: AbstractWorkerForBuilder) -> None:
        assert workerforbuilder.worker is not None
        self.path_module = workerforbuilder.worker.path_module
        self.path_cls: type[PurePath] | None = workerforbuilder.worker.path_cls
        self.workername = workerforbuilder.worker.workername
        self.worker_info = workerforbuilder.worker.info

    @defer.inlineCallbacks
    def getBuilderId(self) -> InlineCallbacksType[int]:
        if self._builderid is None:
            if self.hasProperty(self.VIRTUAL_BUILDERNAME_PROP):
                self._builderid = yield self.builder.getBuilderIdForName(
                    self.getProperty(self.VIRTUAL_BUILDERNAME_PROP)
                )

                assert self.builder.config is not None
                description = self.getProperty(
                    self.VIRTUAL_BUILDERDESCRIPTION_PROP, self.builder.config.description
                )
                project = self.getProperty(
                    self.VIRTUAL_BUILDER_PROJECT_PROP, self.builder.config.project
                )
                tags = self.getProperty(self.VIRTUAL_BUILDERTAGS_PROP, self.builder.config.tags)
                if isinstance(tags, list) and '_virtual_' not in tags:
                    tags.append('_virtual_')

                projectid = yield self.builder.find_project_id(project)
                # Note: not waiting for updateBuilderInfo to complete
                self.master.data.updates.updateBuilderInfo(
                    self._builderid, description, None, None, projectid, tags
                )

            else:
                self._builderid = yield self.builder.getBuilderId()

        assert self._builderid is not None
        return self._builderid

    @defer.inlineCallbacks
    def startBuild(self, workerforbuilder: AbstractWorkerForBuilder) -> InlineCallbacksType[None]:
        """This method sets up the build, then starts it by invoking the
        first Step. It returns a Deferred which will fire when the build
        finishes. This Deferred is guaranteed to never errback."""
        self.workerforbuilder = workerforbuilder
        self.conn = None
        if self.builder.config is not None:
            self.do_build = self.builder.config.do_build_if(self)
        else:
            self.do_build = True

        worker = workerforbuilder.worker
        assert worker is not None

        # Cache the worker information as variables instead of accessing via worker, as the worker
        # will disappear during disconnection and some of these properties may still be needed.
        self.workername = worker.workername
        self.worker_info = worker.info

        log.msg(f"{self}.startBuild{' skipped' if not self.do_build else ''}")

        # TODO: this will go away when build collapsing is implemented; until
        # then we just assign the build to the first buildrequest
        brid = self.requests[0].id
        builderid = yield self.getBuilderId()
        assert self.master.data is not None
        self.buildid, self.number = yield self.master.data.updates.addBuild(
            builderid=builderid, buildrequestid=brid, workerid=worker.workerid
        )
        assert self.buildid is not None
        self._buildid_notifier.notify(self.buildid)

        assert self.master.mq is not None
        self.stopBuildConsumer = yield self.master.mq.startConsuming(
            self.controlStopBuild, ("control", "builds", str(self.buildid), "stop")
        )

        # Check if buildrequest has been cancelled in the mean time. Must be done after subscription
        # to stop control endpoint is established to avoid race condition.
        for r in self.requests:
            reason = self.master.botmaster.remove_in_progress_buildrequest(r.id)
            if isinstance(reason, str):
                yield self.stopBuild(reason=reason)
                return

        if not self.do_build:
            Build.setupBuildProperties(
                self.getProperties(), self.requests, self.sources, self.number
            )
            metrics.MetricCountEvent.log('active_builds', 1)

            # flush properties in the beginning of the build
            yield self.master.data.updates.setBuildProperties(self.buildid, self)

            self.results = SKIPPED
            self.text: list[str] = []
            yield self.allStepsDone()
            return

        # the preparation step counts the time needed for preparing the worker and getting the
        # locks.
        # we cannot use a real step as we don't have a worker yet.
        self._preparation_step = cast(
            buildstep.BuildStep,
            buildstep.create_step_from_step_or_factory(
                buildstep.BuildStep(name="worker_preparation")
            ),
        )
        assert self._preparation_step is not None
        self._preparation_step.setBuild(self)
        yield self._preparation_step.addStep()
        assert self.master.data.updates is not None
        yield self.master.data.updates.startStep(self._preparation_step.stepid, locks_acquired=True)

        Build.setupBuildProperties(self.getProperties(), self.requests, self.sources, self.number)

        yield self._setup_locks()
        metrics.MetricCountEvent.log('active_builds', 1)

        if self._locks_to_acquire:
            # Note that most of the time locks will already free because build distributor does
            # not start builds that cannot acquire locks immediately. However on a loaded master
            # it may happen that more builds are cleared to start than there are free locks. In
            # such case some of the builds will be blocked and wait for the locks.
            self._locks_acquire_step = cast(
                buildstep.BuildStep,
                buildstep.create_step_from_step_or_factory(
                    buildstep.BuildStep(name="locks_acquire")
                ),
            )
            self._locks_acquire_step.setBuild(self)
            yield self._locks_acquire_step.addStep()

        # make sure properties are available to people listening on 'new'
        # events
        yield self.master.data.updates.setBuildProperties(self.buildid, self)
        yield self.master.data.updates.setBuildStateString(self.buildid, 'starting')
        yield self.master.data.updates.generateNewBuildEvent(self.buildid)

        try:
            self.setupBuild()  # create .steps
        except Exception:
            yield self.buildPreparationFailure(Failure(), "setupBuild")
            yield self.buildFinished(['Build.setupBuild', 'failed'], EXCEPTION)
            return

        # flush properties in the beginning of the build
        yield self.master.data.updates.setBuildProperties(self.buildid, self)
        yield self.master.data.updates.setBuildStateString(self.buildid, 'preparing worker')
        try:
            ready_or_failure: bool | Failure = False
            if workerforbuilder.worker and workerforbuilder.worker.acquireLocks():
                self._is_substantiating = True
                ready_or_failure = yield workerforbuilder.substantiate_if_needed(self)
        except Exception:
            ready_or_failure = Failure()
        finally:
            self._is_substantiating = False

        # If prepare returns True then it is ready and we start a build
        # If it returns failure then we don't start a new build.
        if ready_or_failure is not True:
            yield self.buildPreparationFailure(ready_or_failure, "worker_prepare")
            if self.stopped:
                yield self.buildFinished(["worker", "cancelled"], self.results)
            elif isinstance(ready_or_failure, Failure) and ready_or_failure.check(
                interfaces.LatentWorkerCannotSubstantiate
            ):
                yield self.buildFinished(["worker", "cannot", "substantiate"], EXCEPTION)
            else:
                yield self.buildFinished(["worker", "not", "available"], RETRY)
            return

        # ping the worker to make sure they're still there. If they've
        # fallen off the map (due to a NAT timeout or something), this
        # will fail in a couple of minutes, depending upon the TCP
        # timeout.
        #
        # TODO: This can unnecessarily suspend the starting of a build, in
        # situations where the worker is live but is pushing lots of data to
        # us in a build.
        yield self.master.data.updates.setBuildStateString(self.buildid, 'pinging worker')
        log.msg(f"starting build {self}.. pinging the worker {workerforbuilder}")
        try:
            ping_success_or_failure = yield workerforbuilder.ping()
        except Exception:
            ping_success_or_failure = Failure()

        if ping_success_or_failure is not True:
            yield self.buildPreparationFailure(ping_success_or_failure, "worker_ping")
            yield self.buildFinished(["worker", "not", "pinged"], RETRY)
            return

        yield self.master.data.updates.setStepStateString(
            self._preparation_step.stepid, f"worker {self.getWorkerName()} ready"
        )
        yield self.master.data.updates.finishStep(self._preparation_step.stepid, SUCCESS, False)

        assert workerforbuilder.worker is not None
        self.conn = workerforbuilder.worker.conn

        # To retrieve the worker properties, the worker must be attached as we depend on its
        # path_module for at least the builddir property. Latent workers become attached only after
        # preparing them, so we can't setup the builddir property earlier like the rest of
        # properties
        self.setupWorkerProperties(workerforbuilder)
        self.setupWorkerForBuilder(workerforbuilder)
        self.subs = self.conn.notifyOnDisconnect(self.lostRemote)

        # tell the remote that it's starting a build, too
        try:
            assert self.builder.name is not None
            yield self.conn.remoteStartBuild(self.builder.name)
        except Exception:
            yield self.buildPreparationFailure(Failure(), "start_build")
            yield self.buildFinished(["worker", "not", "building"], RETRY)
            return

        if self._locks_to_acquire:
            yield self.master.data.updates.setBuildStateString(self.buildid, "acquiring locks")
            locks_acquire_start_at = int(self.master.reactor.seconds())
            assert self._locks_acquire_step is not None
            yield self.master.data.updates.startStep(
                self._locks_acquire_step.stepid, started_at=locks_acquire_start_at
            )
            yield self.acquireLocks()
            locks_acquired_at = int(self.master.reactor.seconds())
            yield self.master.data.updates.set_step_locks_acquired_at(
                self._locks_acquire_step.stepid, locks_acquired_at=locks_acquired_at
            )
            yield self.master.data.updates.add_build_locks_duration(
                self.buildid, duration_s=locks_acquired_at - locks_acquire_start_at
            )
            yield self.master.data.updates.setStepStateString(
                self._locks_acquire_step.stepid, "locks acquired"
            )
            yield self.master.data.updates.finishStep(
                self._locks_acquire_step.stepid, SUCCESS, False
            )

        yield self.master.data.updates.setBuildStateString(self.buildid, 'building')

        # start the sequence of steps
        self.startNextStep()

    @defer.inlineCallbacks
    def buildPreparationFailure(
        self,
        why: bool | Failure | Exception,
        state_string: str,
    ) -> InlineCallbacksType[None]:
        assert self._preparation_step is not None
        if self.stopped:
            # if self.stopped, then this failure is a LatentWorker's failure to substantiate
            # which we triggered on purpose in stopBuild()
            log.msg("worker stopped while " + state_string, why)
            yield self.master.data.updates.finishStep(
                self._preparation_step.stepid, CANCELLED, False
            )
        else:
            log.err(why, "while " + state_string)
            assert self.workerforbuilder is not None
            assert self.workerforbuilder.worker is not None
            self.workerforbuilder.worker.putInQuarantine()
            if isinstance(why, failure.Failure):
                yield self._preparation_step.addLogWithFailure(why)
            elif isinstance(why, Exception):
                yield self._preparation_step.addLogWithException(why)
            yield self.master.data.updates.setStepStateString(
                self._preparation_step.stepid, "error while " + state_string
            )
            yield self.master.data.updates.finishStep(
                self._preparation_step.stepid, EXCEPTION, False
            )

    def acquireLocks(self, res: BaseLock | None = None) -> Deferred[None]:
        self._acquiringLock = None
        if not self._locks_to_acquire:
            return defer.succeed(None)
        if self.stopped:
            return defer.succeed(None)
        log.msg(f"acquireLocks(build {self}, locks {self._locks_to_acquire})")
        for lock, access in self._locks_to_acquire:
            if not lock.isAvailable(self, access):
                log.msg(f"Build {self} waiting for lock {lock}")
                d = lock.waitUntilMaybeAvailable(self, access)
                d.addCallback(self.acquireLocks)
                self._acquiringLock = (lock, access, d)
                return d
        # all locks are available, claim them all
        for lock, access in self._locks_to_acquire:
            lock.claim(self, access)
        return defer.succeed(None)

    def setUniqueStepName(self, name: str) -> str:
        # If there are any name collisions, we add a count to the loser
        # until it is unique.
        if name in self.stepnames:
            count = self.stepnames[name]
            count += 1
            self.stepnames[name] = count
            name = f"{name}_{count}"
        else:
            self.stepnames[name] = 0
        return name

    def setupBuildSteps(
        self,
        step_factories: list[IBuildStepFactory],
    ) -> list[IBuildStep]:
        steps: list[IBuildStep] = []
        for factory in step_factories:
            step = buildstep.create_step_from_step_or_factory(factory)
            # FIXME: this is not great, assumption of BuildStep instead of correct IBuildStep
            assert isinstance(step, BuildStep)
            step.setBuild(self)
            assert self.workerforbuilder is not None
            assert self.workerforbuilder.worker is not None
            step.setWorker(self.workerforbuilder.worker)
            steps.append(step)

            if self.useProgress:
                step.setupProgress()
        return steps

    def setupBuild(self) -> None:
        # create the actual BuildSteps.

        self.steps = self.setupBuildSteps(self.stepFactories)

        owners = set(self.blamelist())
        # gather owners from build requests
        owners.update({r.properties['owner'] for r in self.requests if "owner" in r.properties})
        if owners:
            self.setProperty('owners', sorted(owners), 'Build')
        self.text = []  # list of text string lists (text2)

    def addStepsAfterCurrentStep(self, step_factories: list[IBuildStepFactory]) -> None:
        # Add the new steps after the step that is running.
        # The running step has already been popped from self.steps
        self.steps[0:0] = self.setupBuildSteps(step_factories)

    def addStepsAfterLastStep(self, step_factories: list[IBuildStepFactory]) -> None:
        # Add the new steps to the end.
        self.steps.extend(self.setupBuildSteps(step_factories))

    def getNextStep(self) -> IBuildStep | None:
        """This method is called to obtain the next BuildStep for this build.
        When it returns None, the build is complete."""
        while self.steps and self.conn:
            s = self.steps.pop(0)
            assert isinstance(s, BuildStep)
            if not (self.terminate or self.stopped) or s.alwaysRun:
                return s

        return None

    def startNextStep(self) -> Deferred[None]:
        next_step = self.getNextStep()
        if next_step is None:
            return self.allStepsDone()
        self.executedSteps.append(next_step)
        self.currentStep = next_step

        # the following function returns a deferred, but we don't wait for it
        self._start_next_step_impl(next_step)
        return defer.succeed(None)

    @defer.inlineCallbacks
    def _start_next_step_impl(self, step: IBuildStep) -> InlineCallbacksType[None]:
        # FIXME: bad assumption of BuildStep instead of IBuildStep
        assert isinstance(step, BuildStep)
        assert self.conn is not None
        try:
            results = yield step.startStep(self.conn)
            yield self.master.data.updates.setBuildProperties(self.buildid, self)

            self.currentStep = None
            if self.finished:
                return  # build was interrupted, don't keep building

            terminate = yield self.stepDone(results, step)  # interpret/merge results
            if terminate:
                self.terminate = True
            yield self.startNextStep()

        except Exception as e:
            log.msg(f"{self} build got exception when running step {step}")
            log.err(e)

            yield self.master.data.updates.setBuildProperties(self.buildid, self)

            # Note that buildFinished can't throw exception
            yield self.buildFinished(["build", "exception"], EXCEPTION)

    @defer.inlineCallbacks
    def stepDone(
        self,
        results: int | tuple[int, Iterable[str]],
        step: BuildStep,
    ) -> InlineCallbacksType[bool]:
        """This method is called when the BuildStep completes. It is passed a
        status object from the BuildStep and is responsible for merging the
        Step's results into those of the overall Build."""

        terminate = False
        text: Iterable[str] | None = None
        if isinstance(results, tuple):
            results, text = results
        assert isinstance(results, type(SUCCESS)), f"got {results!r}"
        summary = yield step.getBuildResultSummary()
        # mypy doesn't identify 'summary' as a dict[str, str] for some reason
        assert isinstance(summary, dict)
        if 'build' in summary:
            text = [summary['build']]
        log.msg(f" step '{step.name}' complete: {statusToString(results)} ({text})")
        if text:
            self.text.extend(text)
            self.master.data.updates.setBuildStateString(
                self.buildid, bytes2unicode(" ".join(self.text))
            )
        self.results, terminate = computeResultAndTermination(step, results, self.results)
        if not self.conn:
            # force the results to retry if the connection was lost
            self.results = RETRY
            terminate = True
        return terminate

    def lostRemote(self, conn: None = None) -> None:
        # the worker went away. There are several possible reasons for this,
        # and they aren't necessarily fatal. For now, kill the build, but
        # TODO: see if we can resume the build when it reconnects.
        log.msg(f"{self}.lostRemote")
        self.conn = None
        self.text = ["lost", "connection"]
        self.results = RETRY
        # FIXME: bad assumption of BuildStep instead of IBuildStep
        assert self.currentStep is None or isinstance(self.currentStep, BuildStep)
        if self.currentStep and self.currentStep.results is None:
            # this should cause the step to finish.
            log.msg(" stopping currentStep", self.currentStep)
            self.currentStep.interrupt(Failure(error.ConnectionLost()))
        else:
            self.text = ["lost", "connection"]
            self.stopped = True
            if self._acquiringLock:
                lock, access, d = self._acquiringLock
                lock.stopWaitingUntilAvailable(self, access, d)

    def controlStopBuild(self, key: tuple[str, ...], params: dict[str, Any]) -> Deferred:
        return self.stopBuild(**params)

    @defer.inlineCallbacks
    def stopBuild(
        self,
        reason: str = "<no reason given>",
        results: int = CANCELLED,
    ) -> InlineCallbacksType[None]:
        # the idea here is to let the user cancel a build because, e.g.,
        # they realized they committed a bug and they don't want to waste
        # the time building something that they know will fail. Another
        # reason might be to abandon a stuck build. We want to mark the
        # build as failed quickly rather than waiting for the worker's
        # timeout to kill it on its own.

        log.msg(f" {self}: stopping build: {reason} {results}")
        if self.finished:
            return
        self.stopped_reason = reason
        self.stopped = True
        assert self.currentStep is None or isinstance(self.currentStep, BuildStep)
        if self.currentStep and self.currentStep.results is None:
            yield self.currentStep.interrupt(reason)

        self.results = results

        if self._acquiringLock:
            lock, access, d = self._acquiringLock
            lock.stopWaitingUntilAvailable(self, access, d)
        elif self._is_substantiating:
            # We're having a latent worker that hasn't been substantiated yet. We need to abort
            # that to not have a latent worker without an associated build
            assert self.workerforbuilder is not None
            self.workerforbuilder.insubstantiate_if_needed()

    def allStepsDone(self) -> Deferred[None]:
        if self.results == FAILURE:
            text = ["failed"]
        elif self.results == WARNINGS:
            text = ["warnings"]
        elif self.results == EXCEPTION:
            text = ["exception"]
        elif self.results == RETRY:
            text = ["retry"]
        elif self.results == CANCELLED:
            text = ["cancelled"]
        else:
            text = ["build", "successful"]
        if self.stopped_reason is not None:
            text.extend([f'({self.stopped_reason})'])
        text.extend(self.text)
        return self.buildFinished(text, self.results)

    @defer.inlineCallbacks
    def buildFinished(
        self,
        # NOTE: should be Iterable[str], but should NOT be a simple str
        text: list[str] | set[str] | tuple[str, ...],
        results: int | None,
    ) -> InlineCallbacksType[None]:
        """This method must be called when the last Step has completed. It
        marks the Build as complete and returns the Builder to the 'idle'
        state.

        It takes two arguments which describe the overall build status:
        text, results. 'results' is one of the possible results (see buildbot.process.results).

        If 'results' is SUCCESS or WARNINGS, we will permit any dependent
        builds to start. If it is 'FAILURE', those builds will be
        abandoned.

        This method never throws."""
        try:
            self.stopBuildConsumer.stopConsuming()
            self.finished = True
            if self.conn:
                assert self.subs is not None
                self.subs.unsubscribe()
                self.subs = None
                self.conn = None
            log.msg(f" {self}: build finished")
            self.results = worst_status(self.results, results)
            eventually(self.releaseLocks)
            metrics.MetricCountEvent.log('active_builds', -1)

            yield self.master.data.updates.setBuildStateString(
                self.buildid, bytes2unicode(" ".join(text))
            )
            yield self.master.data.updates.finishBuild(self.buildid, self.results)

            assert self.workerforbuilder is not None
            if self.results == EXCEPTION:
                # When a build has an exception, put the worker in quarantine for a few seconds
                # to make sure we try next build with another worker
                assert self.workerforbuilder.worker is not None
                self.workerforbuilder.worker.putInQuarantine()
            elif self.results != RETRY:
                # This worker looks sane if status is neither retry or exception

                # Avoid a race in case the build step reboot the worker
                if self.workerforbuilder.worker is not None:
                    self.workerforbuilder.worker.resetQuarantine()

            # mark the build as finished
            self.workerforbuilder.buildFinished()
            self.builder.buildFinished(self, self.workerforbuilder)

            self._tryScheduleBuildsAfterLockUnlock(build_finished=True)
        except Exception:
            log.err(
                None,
                'from finishing a build; this is a '
                'serious error - please file a bug at http://buildbot.net',
            )

    def releaseLocks(self) -> None:
        if self._locks_to_acquire:
            log.msg(f"releaseLocks({self}): {self._locks_to_acquire}")

        for lock, access in self._locks_to_acquire:
            if lock.isOwner(self, access):
                lock.release(self, access)

        self._tryScheduleBuildsAfterLockUnlock(locks_released=True)

    def _tryScheduleBuildsAfterLockUnlock(
        self,
        locks_released: bool = False,
        build_finished: bool = False,
    ) -> None:
        # we need to inform the botmaster to attempt to schedule any pending
        # build request if we released any locks. This is because buildrequest
        # may be started for a completely unrelated builder and yet depend on
        # a lock released by this build.
        #
        # TODO: the current approach is dumb as we just attempt to schedule
        # all buildrequests. A much better idea would be to record the reason
        # of why a buildrequest was not scheduled in the BuildRequestDistributor
        # and then attempt to schedule only these buildrequests which may have
        # had that reason resolved.

        # this function is complicated by the fact that the botmaster must be
        # informed only when all locks have been released and the actions in
        # buildFinished have concluded. Since releaseLocks is called using
        # eventually this may happen in any order.
        self._locks_released = self._locks_released or locks_released
        self._build_finished = self._build_finished or build_finished

        if not self._locks_to_acquire:
            return

        if self._locks_released and self._build_finished:
            self.master.botmaster.maybeStartBuildsForAllBuilders()

    def getSummaryStatistic(
        self,
        name: str,
        summary_fn: Callable[[int, int], int],
        initial_value: Sentinel | int = _sentinel,
    ) -> int:
        assert all(isinstance(s, BuildStep) for s in self.executedSteps)
        step_stats_list: list[int] = [
            st.getStatistic(name)  # type: ignore[attr-defined]
            for st in self.executedSteps
            if st.hasStatistic(name)  # type: ignore[attr-defined]
        ]
        if initial_value is self._sentinel:
            return reduce(summary_fn, step_stats_list)
        assert isinstance(initial_value, int)
        return reduce(summary_fn, step_stats_list, initial_value)

    @defer.inlineCallbacks
    def getUrl(self) -> InlineCallbacksType[str]:
        builder_id = yield self.getBuilderId()
        return getURLForBuild(self.master, builder_id, self.number)

    @defer.inlineCallbacks
    def get_buildid(self) -> InlineCallbacksType[int]:
        if self.buildid is not None:
            return self.buildid
        buildid = yield self._buildid_notifier.wait()
        return buildid

    @defer.inlineCallbacks
    def waitUntilFinished(self) -> InlineCallbacksType[None]:
        buildid = yield self.get_buildid()
        yield self.master.mq.waitUntilEvent(
            ('builds', str(buildid), 'finished'), lambda: self.finished
        )

    def getWorkerInfo(self) -> Properties:
        return self.worker_info
