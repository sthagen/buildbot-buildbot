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

import json
import os
import shutil
from io import StringIO
from unittest import mock

from twisted.internet import defer
from twisted.protocols import basic
from twisted.trial import unittest

from buildbot.schedulers import trysched
from buildbot.test.reactor import TestReactorMixin
from buildbot.test.util import dirs
from buildbot.test.util import scheduler


class TryBase(scheduler.SchedulerMixin, TestReactorMixin, unittest.TestCase):
    OBJECTID = 26
    SCHEDULERID = 6

    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        yield self.setUpScheduler()

    def makeScheduler(self, **kwargs):
        return self.attachScheduler(
            trysched.Try_Userpass(**kwargs), self.OBJECTID, self.SCHEDULERID
        )

    def test_filterBuilderList_ok(self):
        sched = trysched.TryBase(name='tsched', builderNames=['a', 'b', 'c'], properties={})
        self.assertEqual(sched.filterBuilderList(['b', 'c']), ['b', 'c'])

    def test_filterBuilderList_bad(self):
        sched = trysched.TryBase(name='tsched', builderNames=['a', 'b'], properties={})
        self.assertEqual(sched.filterBuilderList(['b', 'c']), [])

    def test_filterBuilderList_empty(self):
        sched = trysched.TryBase(name='tsched', builderNames=['a', 'b'], properties={})
        self.assertEqual(sched.filterBuilderList([]), ['a', 'b'])

    @defer.inlineCallbacks
    def test_enabled_callback(self):
        sched = yield self.makeScheduler(
            name='tsched', builderNames=['a'], port='tcp:9999', userpass=[('fred', 'derf')]
        )
        yield sched.configureService()
        expectedValue = not sched.enabled
        yield sched._enabledCallback(None, {'enabled': not sched.enabled})
        self.assertEqual(sched.enabled, expectedValue)
        expectedValue = not sched.enabled
        yield sched._enabledCallback(None, {'enabled': not sched.enabled})
        self.assertEqual(sched.enabled, expectedValue)

    @defer.inlineCallbacks
    def test_disabled_activate(self):
        sched = yield self.makeScheduler(
            name='tsched', builderNames=['a'], port='tcp:9999', userpass=[('fred', 'derf')]
        )
        yield sched._enabledCallback(None, {'enabled': not sched.enabled})
        self.assertEqual(sched.enabled, False)
        r = yield sched.activate()
        self.assertEqual(r, None)

    @defer.inlineCallbacks
    def test_disabled_deactivate(self):
        sched = yield self.makeScheduler(
            name='tsched', builderNames=['a'], port='tcp:9999', userpass=[('fred', 'derf')]
        )
        yield sched._enabledCallback(None, {'enabled': not sched.enabled})
        self.assertEqual(sched.enabled, False)
        r = yield sched.deactivate()
        self.assertEqual(r, None)


class JobdirService(dirs.DirsMixin, unittest.TestCase):
    def setUp(self):
        self.jobdir = 'jobdir'
        self.newdir = os.path.join(self.jobdir, 'new')
        self.curdir = os.path.join(self.jobdir, 'cur')
        self.tmpdir = os.path.join(self.jobdir, 'tmp')
        self.setUpDirs(self.jobdir, self.newdir, self.curdir, self.tmpdir)

    def test_messageReceived(self):
        # stub out svc.scheduler.handleJobFile and .jobdir
        scheduler = mock.Mock()

        def handleJobFile(filename, f):
            self.assertEqual(filename, 'jobdata')
            self.assertEqual(f.read(), 'JOBDATA')

        scheduler.handleJobFile = handleJobFile
        scheduler.jobdir = self.jobdir

        svc = trysched.JobdirService(scheduler=scheduler, basedir=self.jobdir)

        # create some new data to process
        jobdata = os.path.join(self.newdir, 'jobdata')
        with open(jobdata, "w", encoding='utf-8') as f:
            f.write('JOBDATA')

        # run it
        svc.messageReceived('jobdata')


class Try_Jobdir(scheduler.SchedulerMixin, TestReactorMixin, unittest.TestCase):
    OBJECTID = 23
    SCHEDULERID = 3

    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        yield self.setUpScheduler()
        self.jobdir = None

    def tearDown(self):
        if self.jobdir:
            shutil.rmtree(self.jobdir)

    # tests

    @defer.inlineCallbacks
    def setup_test_startService(self, jobdir, exp_jobdir):
        # set up jobdir
        self.jobdir = os.path.abspath('jobdir')
        if os.path.exists(self.jobdir):
            shutil.rmtree(self.jobdir)
        os.mkdir(self.jobdir)

        # build scheduler
        kwargs = {"name": 'tsched', "builderNames": ['a'], "jobdir": self.jobdir}
        sched = yield self.attachScheduler(
            trysched.Try_Jobdir(**kwargs),
            self.OBJECTID,
            self.SCHEDULERID,
            overrideBuildsetMethods=True,
        )

        # watch interaction with the watcher service
        sched.watcher.startService = mock.Mock()
        sched.watcher.stopService = mock.Mock()

    @defer.inlineCallbacks
    def do_test_startService(self):
        # start it
        yield self.sched.startService()

        # check that it has set the basedir correctly
        self.assertEqual(self.sched.watcher.basedir, self.jobdir)
        self.assertEqual(1, self.sched.watcher.startService.call_count)
        self.assertEqual(0, self.sched.watcher.stopService.call_count)

        yield self.sched.stopService()

        self.assertEqual(1, self.sched.watcher.startService.call_count)
        self.assertEqual(1, self.sched.watcher.stopService.call_count)

    @defer.inlineCallbacks
    def test_startService_reldir(self):
        yield self.setup_test_startService('jobdir', os.path.abspath('basedir/jobdir'))
        yield self.do_test_startService()

    @defer.inlineCallbacks
    def test_startService_reldir_subdir(self):
        yield self.setup_test_startService('jobdir', os.path.abspath('basedir/jobdir/cur'))
        yield self.do_test_startService()

    @defer.inlineCallbacks
    def test_startService_absdir(self):
        yield self.setup_test_startService(os.path.abspath('jobdir'), os.path.abspath('jobdir'))
        yield self.do_test_startService()

    @defer.inlineCallbacks
    def do_test_startService_but_not_active(self, jobdir, exp_jobdir):
        """Same as do_test_startService, but the master wont activate this service"""
        yield self.setup_test_startService('jobdir', os.path.abspath('basedir/jobdir'))

        self.setSchedulerToMaster(self.OTHER_MASTER_ID)

        # start it
        self.sched.startService()

        # check that it has set the basedir correctly, even if it doesn't start
        self.assertEqual(self.sched.watcher.basedir, self.jobdir)

        yield self.sched.stopService()

        self.assertEqual(0, self.sched.watcher.startService.call_count)
        self.assertEqual(0, self.sched.watcher.stopService.call_count)

    # parseJob

    def test_parseJob_empty(self):
        sched = trysched.Try_Jobdir(name='tsched', builderNames=['a'], jobdir='foo')
        with self.assertRaises(trysched.BadJobfile):
            sched.parseJob(StringIO(''))

    def test_parseJob_longer_than_netstring_MAXLENGTH(self):
        self.patch(basic.NetstringReceiver, 'MAX_LENGTH', 100)
        sched = trysched.Try_Jobdir(name='tsched', builderNames=['a'], jobdir='foo')
        jobstr = self.makeNetstring(
            '1',
            'extid',
            'trunk',
            '1234',
            '1',
            'this is my diff, -- ++, etc.',
            'buildera',
            'builderc',
        )
        jobstr += 'x' * 200

        test_temp_file = StringIO(jobstr)

        with self.assertRaises(trysched.BadJobfile):
            sched.parseJob(test_temp_file)

    def test_parseJob_invalid(self):
        sched = trysched.Try_Jobdir(name='tsched', builderNames=['a'], jobdir='foo')
        with self.assertRaises(trysched.BadJobfile):
            sched.parseJob(StringIO('this is not a netstring'))

    def test_parseJob_invalid_version(self):
        sched = trysched.Try_Jobdir(name='tsched', builderNames=['a'], jobdir='foo')
        with self.assertRaises(trysched.BadJobfile):
            sched.parseJob(StringIO('1:9,'))

    def makeNetstring(self, *strings):
        return ''.join([f'{len(s)}:{s},' for s in strings])

    def test_parseJob_v1(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            '1',
            'extid',
            'trunk',
            '1234',
            '1',
            'this is my diff, -- ++, etc.',
            'buildera',
            'builderc',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(
            parsedjob,
            {
                'baserev': '1234',
                'branch': 'trunk',
                'builderNames': ['buildera', 'builderc'],
                'jobid': 'extid',
                'patch_body': b'this is my diff, -- ++, etc.',
                'patch_level': 1,
                'project': '',
                'who': '',
                'comment': '',
                'repository': '',
                'properties': {},
            },
        )

    def test_parseJob_v1_empty_branch_rev(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            # blank branch, rev are turned to None
            '1',
            'extid',
            '',
            '',
            '1',
            'this is my diff, -- ++, etc.',
            'buildera',
            'builderc',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['branch'], None)
        self.assertEqual(parsedjob['baserev'], None)

    def test_parseJob_v1_no_builders(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring('1', 'extid', '', '', '1', 'this is my diff, -- ++, etc.')
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['builderNames'], [])

    def test_parseJob_v1_no_properties(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring('1', 'extid', '', '', '1', 'this is my diff, -- ++, etc.')
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['properties'], {})

    def test_parseJob_v2(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            '2',
            'extid',
            'trunk',
            '1234',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
            'buildera',
            'builderc',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(
            parsedjob,
            {
                'baserev': '1234',
                'branch': 'trunk',
                'builderNames': ['buildera', 'builderc'],
                'jobid': 'extid',
                'patch_body': b'this is my diff, -- ++, etc.',
                'patch_level': 1,
                'project': 'proj',
                'who': '',
                'comment': '',
                'repository': 'repo',
                'properties': {},
            },
        )

    def test_parseJob_v2_empty_branch_rev(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            # blank branch, rev are turned to None
            '2',
            'extid',
            '',
            '',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
            'buildera',
            'builderc',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['branch'], None)
        self.assertEqual(parsedjob['baserev'], None)

    def test_parseJob_v2_no_builders(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            '2',
            'extid',
            'trunk',
            '1234',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['builderNames'], [])

    def test_parseJob_v2_no_properties(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            '2',
            'extid',
            'trunk',
            '1234',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['properties'], {})

    def test_parseJob_v3(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            '3',
            'extid',
            'trunk',
            '1234',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
            'who',
            'buildera',
            'builderc',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(
            parsedjob,
            {
                'baserev': '1234',
                'branch': 'trunk',
                'builderNames': ['buildera', 'builderc'],
                'jobid': 'extid',
                'patch_body': b'this is my diff, -- ++, etc.',
                'patch_level': 1,
                'project': 'proj',
                'who': 'who',
                'comment': '',
                'repository': 'repo',
                'properties': {},
            },
        )

    def test_parseJob_v3_empty_branch_rev(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            # blank branch, rev are turned to None
            '3',
            'extid',
            '',
            '',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
            'who',
            'buildera',
            'builderc',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['branch'], None)
        self.assertEqual(parsedjob['baserev'], None)

    @defer.inlineCallbacks
    def test_parseJob_v3_no_builders(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        yield self.attachScheduler(sched, self.OBJECTID, self.SCHEDULERID)
        yield self.master.startService()
        jobstr = self.makeNetstring(
            '3',
            'extid',
            'trunk',
            '1234',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
            'who',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['builderNames'], [])

    @defer.inlineCallbacks
    def test_parseJob_v3_no_properties(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        yield self.attachScheduler(sched, self.OBJECTID, self.SCHEDULERID)
        yield self.master.startService()
        jobstr = self.makeNetstring(
            '3',
            'extid',
            'trunk',
            '1234',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
            'who',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['properties'], {})

    def test_parseJob_v4(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            '4',
            'extid',
            'trunk',
            '1234',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
            'who',
            'comment',
            'buildera',
            'builderc',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(
            parsedjob,
            {
                'baserev': '1234',
                'branch': 'trunk',
                'builderNames': ['buildera', 'builderc'],
                'jobid': 'extid',
                'patch_body': b'this is my diff, -- ++, etc.',
                'patch_level': 1,
                'project': 'proj',
                'who': 'who',
                'comment': 'comment',
                'repository': 'repo',
                'properties': {},
            },
        )

    def test_parseJob_v4_empty_branch_rev(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            # blank branch, rev are turned to None
            '4',
            'extid',
            '',
            '',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
            'who',
            'comment',
            'buildera',
            'builderc',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['branch'], None)
        self.assertEqual(parsedjob['baserev'], None)

    def test_parseJob_v4_no_builders(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            '4',
            'extid',
            'trunk',
            '1234',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
            'who',
            'comment',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['builderNames'], [])

    def test_parseJob_v4_no_properties(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            '4',
            'extid',
            'trunk',
            '1234',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
            'who',
            'comment',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['properties'], {})

    def test_parseJob_v5(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            '5',
            json.dumps({
                'jobid': 'extid',
                'branch': 'trunk',
                'baserev': '1234',
                'patch_level': 1,
                'patch_body': 'this is my diff, -- ++, etc.',
                'repository': 'repo',
                'project': 'proj',
                'who': 'who',
                'comment': 'comment',
                'builderNames': ['buildera', 'builderc'],
                'properties': {'foo': 'bar'},
            }),
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(
            parsedjob,
            {
                'baserev': '1234',
                'branch': 'trunk',
                'builderNames': ['buildera', 'builderc'],
                'jobid': 'extid',
                'patch_body': b'this is my diff, -- ++, etc.',
                'patch_level': 1,
                'project': 'proj',
                'who': 'who',
                'comment': 'comment',
                'repository': 'repo',
                'properties': {'foo': 'bar'},
            },
        )

    def test_parseJob_v5_empty_branch_rev(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            # blank branch, rev are turned to None
            '4',
            'extid',
            '',
            '',
            '1',
            'this is my diff, -- ++, etc.',
            'repo',
            'proj',
            'who',
            'comment',
            'buildera',
            'builderc',
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['branch'], None)
        self.assertEqual(parsedjob['baserev'], None)

    def test_parseJob_v5_no_builders(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            '5',
            json.dumps({
                'jobid': 'extid',
                'branch': 'trunk',
                'baserev': '1234',
                'patch_level': '1',
                'patch_body': 'this is my diff, -- ++, etc.',
                'repository': 'repo',
                'project': 'proj',
                'who': 'who',
                'comment': 'comment',
                'builderNames': [],
                'properties': {'foo': 'bar'},
            }),
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['builderNames'], [])

    def test_parseJob_v5_no_properties(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring(
            '5',
            json.dumps({
                'jobid': 'extid',
                'branch': 'trunk',
                'baserev': '1234',
                'patch_level': '1',
                'patch_body': 'this is my diff, -- ++, etc.',
                'repository': 'repo',
                'project': 'proj',
                'who': 'who',
                'comment': 'comment',
                'builderNames': ['buildera', 'builderb'],
                'properties': {},
            }),
        )
        parsedjob = sched.parseJob(StringIO(jobstr))
        self.assertEqual(parsedjob['properties'], {})

    def test_parseJob_v5_invalid_json(self):
        sched = trysched.Try_Jobdir(
            name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'
        )
        jobstr = self.makeNetstring('5', '{"comment": "com}')
        with self.assertRaises(trysched.BadJobfile):
            sched.parseJob(StringIO(jobstr))

    # handleJobFile

    @defer.inlineCallbacks
    def call_handleJobFile(self, parseJob):
        sched = yield self.attachScheduler(
            trysched.Try_Jobdir(name='tsched', builderNames=['buildera', 'builderb'], jobdir='foo'),
            self.OBJECTID,
            self.SCHEDULERID,
            overrideBuildsetMethods=True,
            createBuilderDB=True,
        )
        yield self.master.startService()
        fakefile = mock.Mock()

        def parseJob_(f):
            assert f is fakefile
            return parseJob(f)

        sched.parseJob = parseJob_
        yield sched.handleJobFile('fakefile', fakefile)

    def makeSampleParsedJob(self, **overrides):
        pj = {
            "baserev": '1234',
            "branch": 'trunk',
            "builderNames": ['buildera', 'builderb'],
            "jobid": 'extid',
            "patch_body": b'this is my diff, -- ++, etc.',
            "patch_level": 1,
            "project": 'proj',
            "repository": 'repo',
            "who": 'who',
            "comment": 'comment',
            "properties": {},
        }
        pj.update(overrides)
        return pj

    @defer.inlineCallbacks
    def test_handleJobFile(self):
        yield self.call_handleJobFile(lambda f: self.makeSampleParsedJob())

        self.assertEqual(
            self.addBuildsetCalls,
            [
                (
                    'addBuildsetForSourceStamps',
                    {
                        "builderNames": ['buildera', 'builderb'],
                        "external_idstring": 'extid',
                        "properties": {},
                        "reason": "'try' job by user who",
                        "sourcestamps": [
                            {
                                "branch": 'trunk',
                                "codebase": '',
                                "patch_author": 'who',
                                "patch_body": b'this is my diff, -- ++, etc.',
                                "patch_comment": 'comment',
                                "patch_level": 1,
                                "patch_subdir": '',
                                "project": 'proj',
                                "repository": 'repo',
                                "revision": '1234',
                            },
                        ],
                    },
                ),
            ],
        )

    @defer.inlineCallbacks
    def test_handleJobFile_exception(self):
        def parseJob(f):
            raise trysched.BadJobfile

        yield self.call_handleJobFile(parseJob)

        self.assertEqual(self.addBuildsetCalls, [])
        self.assertEqual(1, len(self.flushLoggedErrors(trysched.BadJobfile)))

    @defer.inlineCallbacks
    def test_handleJobFile_bad_builders(self):
        yield self.call_handleJobFile(lambda f: self.makeSampleParsedJob(builderNames=['xxx']))

        self.assertEqual(self.addBuildsetCalls, [])

    @defer.inlineCallbacks
    def test_handleJobFile_subset_builders(self):
        yield self.call_handleJobFile(lambda f: self.makeSampleParsedJob(builderNames=['buildera']))

        self.assertEqual(
            self.addBuildsetCalls,
            [
                (
                    'addBuildsetForSourceStamps',
                    {
                        "builderNames": ['buildera'],
                        "external_idstring": 'extid',
                        "properties": {},
                        "reason": "'try' job by user who",
                        "sourcestamps": [
                            {
                                "branch": 'trunk',
                                "codebase": '',
                                "patch_author": 'who',
                                "patch_body": b'this is my diff, -- ++, etc.',
                                "patch_comment": 'comment',
                                "patch_level": 1,
                                "patch_subdir": '',
                                "project": 'proj',
                                "repository": 'repo',
                                "revision": '1234',
                            },
                        ],
                    },
                ),
            ],
        )

    @defer.inlineCallbacks
    def test_handleJobFile_with_try_properties(self):
        yield self.call_handleJobFile(lambda f: self.makeSampleParsedJob(properties={'foo': 'bar'}))

        self.assertEqual(
            self.addBuildsetCalls,
            [
                (
                    'addBuildsetForSourceStamps',
                    {
                        "builderNames": ['buildera', 'builderb'],
                        "external_idstring": 'extid',
                        "properties": {'foo': ('bar', 'try build')},
                        "reason": "'try' job by user who",
                        "sourcestamps": [
                            {
                                "branch": 'trunk',
                                "codebase": '',
                                "patch_author": 'who',
                                "patch_body": b'this is my diff, -- ++, etc.',
                                "patch_comment": 'comment',
                                "patch_level": 1,
                                "patch_subdir": '',
                                "project": 'proj',
                                "repository": 'repo',
                                "revision": '1234',
                            },
                        ],
                    },
                ),
            ],
        )

    @defer.inlineCallbacks
    def test_handleJobFile_with_invalid_try_properties(self):
        with self.assertRaises(AttributeError):
            yield self.call_handleJobFile(
                lambda f: self.makeSampleParsedJob(properties=['foo', 'bar'])
            )


class Try_Userpass_Perspective(scheduler.SchedulerMixin, TestReactorMixin, unittest.TestCase):
    OBJECTID = 26
    SCHEDULERID = 6

    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        yield self.setUpScheduler()

    def makeScheduler(self, **kwargs):
        return self.attachScheduler(
            trysched.Try_Userpass(**kwargs),
            self.OBJECTID,
            self.SCHEDULERID,
            overrideBuildsetMethods=True,
            createBuilderDB=True,
        )

    @defer.inlineCallbacks
    def call_perspective_try(self, *args, **kwargs):
        sched = yield self.makeScheduler(
            name='tsched',
            builderNames=['a', 'b'],
            port='xxx',
            userpass=[('a', 'b')],
            properties={"frm": 'schd'},
        )
        persp = trysched.Try_Userpass_Perspective(sched, 'a')

        # patch out all of the handling after addBuildsetForSourceStamp
        def getBuildset(bsid):
            return {"bsid": bsid}

        self.master.db.buildsets.getBuildset = getBuildset

        rbss = yield persp.perspective_try(*args, **kwargs)

        if rbss is None:
            return
        self.assertIsInstance(rbss, trysched.RemoteBuildSetStatus)

    @defer.inlineCallbacks
    def test_perspective_try(self):
        yield self.call_perspective_try(
            'default', 'abcdef', (1, '-- ++'), 'repo', 'proj', ['a'], properties={'pr': 'op'}
        )

        self.maxDiff = None
        self.assertEqual(
            self.addBuildsetCalls,
            [
                (
                    'addBuildsetForSourceStamps',
                    {
                        "builderNames": ['a'],
                        "external_idstring": None,
                        "properties": {'pr': ('op', 'try build')},
                        "reason": "'try' job",
                        "sourcestamps": [
                            {
                                "branch": 'default',
                                "codebase": '',
                                "patch_author": '',
                                "patch_body": b'-- ++',
                                "patch_comment": '',
                                "patch_level": 1,
                                "patch_subdir": '',
                                "project": 'proj',
                                "repository": 'repo',
                                "revision": 'abcdef',
                            },
                        ],
                    },
                ),
            ],
        )

    @defer.inlineCallbacks
    def test_perspective_try_bytes(self):
        yield self.call_perspective_try(
            'default', 'abcdef', (1, b'-- ++\xf8'), 'repo', 'proj', ['a'], properties={'pr': 'op'}
        )

        self.assertEqual(
            self.addBuildsetCalls,
            [
                (
                    'addBuildsetForSourceStamps',
                    {
                        'builderNames': ['a'],
                        'external_idstring': None,
                        'properties': {'pr': ('op', 'try build')},
                        'reason': "'try' job",
                        'sourcestamps': [
                            {
                                'branch': 'default',
                                'codebase': '',
                                'patch_author': '',
                                'patch_body': b'-- ++\xf8',
                                'patch_comment': '',
                                'patch_level': 1,
                                'patch_subdir': '',
                                'project': 'proj',
                                'repository': 'repo',
                                'revision': 'abcdef',
                            }
                        ],
                    },
                ),
            ],
        )

    @defer.inlineCallbacks
    def test_perspective_try_who(self):
        yield self.call_perspective_try(
            'default',
            'abcdef',
            (1, '-- ++'),
            'repo',
            'proj',
            ['a'],
            who='who',
            comment='comment',
            properties={'pr': 'op'},
        )

        self.assertEqual(
            self.addBuildsetCalls,
            [
                (
                    'addBuildsetForSourceStamps',
                    {
                        "builderNames": ['a'],
                        "external_idstring": None,
                        "properties": {'pr': ('op', 'try build')},
                        "reason": "'try' job by user who (comment)",
                        "sourcestamps": [
                            {
                                "branch": 'default',
                                "codebase": '',
                                "patch_author": 'who',
                                "patch_body": b'-- ++',
                                "patch_comment": 'comment',
                                "patch_level": 1,
                                "patch_subdir": '',
                                "project": 'proj',
                                "repository": 'repo',
                                "revision": 'abcdef',
                            },
                        ],
                    },
                ),
            ],
        )

    @defer.inlineCallbacks
    def test_perspective_try_bad_builders(self):
        yield self.call_perspective_try(
            'default', 'abcdef', (1, '-- ++'), 'repo', 'proj', ['xxx'], properties={'pr': 'op'}
        )

        self.assertEqual(self.addBuildsetCalls, [])

    @defer.inlineCallbacks
    def test_getAvailableBuilderNames(self):
        sched = yield self.makeScheduler(
            name='tsched', builderNames=['a', 'b'], port='xxx', userpass=[('a', 'b')]
        )
        persp = trysched.Try_Userpass_Perspective(sched, 'a')
        buildernames = yield persp.perspective_getAvailableBuilderNames()

        self.assertEqual(buildernames, ['a', 'b'])


class Try_Userpass(scheduler.SchedulerMixin, TestReactorMixin, unittest.TestCase):
    OBJECTID = 25
    SCHEDULERID = 5

    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        yield self.setUpScheduler()

    @defer.inlineCallbacks
    def makeScheduler(self, **kwargs):
        sched = yield self.attachScheduler(
            trysched.Try_Userpass(**kwargs), self.OBJECTID, self.SCHEDULERID
        )
        return sched

    @defer.inlineCallbacks
    def test_service(self):
        sched = yield self.makeScheduler(
            name='tsched', builderNames=['a'], port='tcp:9999', userpass=[('fred', 'derf')]
        )
        # patch out the pbmanager's 'register' command both to be sure
        # the registration is correct and to get a copy of the factory
        registration = mock.Mock()
        registration.unregister = lambda: defer.succeed(None)
        sched.master.pbmanager = mock.Mock()

        def register(portstr, user, passwd, factory):
            self.assertEqual([portstr, user, passwd], ['tcp:9999', 'fred', 'derf'])
            self.got_factory = factory
            return defer.succeed(registration)

        sched.master.pbmanager.register = register
        # start it
        yield sched.startService()
        # make a fake connection by invoking the factory, and check that we
        # get the correct perspective
        persp = self.got_factory(mock.Mock(), 'fred')
        self.assertTrue(isinstance(persp, trysched.Try_Userpass_Perspective))
        yield sched.stopService()

    @defer.inlineCallbacks
    def test_service_but_not_active(self):
        sched = yield self.makeScheduler(
            name='tsched', builderNames=['a'], port='tcp:9999', userpass=[('fred', 'derf')]
        )

        self.setSchedulerToMaster(self.OTHER_MASTER_ID)

        sched.master.pbmanager = mock.Mock()

        sched.startService()
        yield sched.stopService()

        self.assertFalse(sched.master.pbmanager.register.called)
