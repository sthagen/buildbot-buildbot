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

import calendar
import datetime
from unittest import mock

import jwt
from twisted.cred import strcred
from twisted.cred.checkers import InMemoryUsernamePasswordDatabaseDontUse
from twisted.internet import defer
from twisted.trial import unittest
from twisted.web._auth.wrapper import HTTPAuthSessionWrapper
from twisted.web.server import Request

from buildbot.test.reactor import TestReactorMixin
from buildbot.test.unit.www import test_hooks_base
from buildbot.test.util import www
from buildbot.www import auth
from buildbot.www import change_hook
from buildbot.www import resource
from buildbot.www import rest
from buildbot.www import service


class FakeChannel:
    transport = None

    def __init__(self, site: service.BuildbotSite) -> None:
        self.site = site

    def isSecure(self):
        return False

    def getPeer(self):
        return None

    def getHost(self):
        return None


class NeedsReconfigResource(resource.Resource):
    needsReconfig = True
    reconfigs = 0

    def reconfigResource(self, new_config):
        NeedsReconfigResource.reconfigs += 1


class Test(TestReactorMixin, www.WwwTestMixin, unittest.TestCase):
    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        self.master = yield self.make_master(url='h:/a/b/')
        self.svc = self.master.www = service.WWWService()
        yield self.svc.setServiceParent(self.master)

    def makeConfig(self, **kwargs):
        w = {"port": None, "auth": auth.NoAuth(), "logfileName": 'l'}
        w.update(kwargs)
        new_config = mock.Mock()
        new_config.www = w
        new_config.buildbotURL = 'h:/'
        self.master.config = new_config
        return new_config

    @defer.inlineCallbacks
    def test_reconfigService_no_port(self):
        new_config = self.makeConfig()
        yield self.svc.reconfigServiceWithBuildbotConfig(new_config)

        self.assertEqual(self.svc.site, None)

    @defer.inlineCallbacks
    def test_reconfigService_reconfigResources(self):
        new_config = self.makeConfig(port=8080)
        self.patch(rest, 'RestRootResource', NeedsReconfigResource)
        NeedsReconfigResource.reconfigs = 0

        # first time, reconfigResource gets called along with setupSite
        yield self.svc.reconfigServiceWithBuildbotConfig(new_config)
        self.assertEqual(NeedsReconfigResource.reconfigs, 1)

        # and the next time, setupSite isn't called, but reconfigResource is
        yield self.svc.reconfigServiceWithBuildbotConfig(new_config)
        self.assertEqual(NeedsReconfigResource.reconfigs, 2)

    @defer.inlineCallbacks
    def test_reconfigService_port(self):
        new_config = self.makeConfig(port=20)
        yield self.svc.reconfigServiceWithBuildbotConfig(new_config)

        self.assertNotEqual(self.svc.site, None)
        self.assertNotEqual(self.svc.port_service, None)
        self.assertEqual(self.svc.port, 20)

    @defer.inlineCallbacks
    def test_reconfigService_expiration_time(self):
        new_config = self.makeConfig(port=80, cookie_expiration_time=datetime.timedelta(minutes=1))
        yield self.svc.reconfigServiceWithBuildbotConfig(new_config)

        self.assertNotEqual(self.svc.site, None)
        self.assertNotEqual(self.svc.port_service, None)
        self.assertEqual(service.BuildbotSession.expDelay, datetime.timedelta(minutes=1))

    @defer.inlineCallbacks
    def test_reconfigService_port_changes(self):
        new_config = self.makeConfig(port=20)
        yield self.svc.reconfigServiceWithBuildbotConfig(new_config)

        newer_config = self.makeConfig(port=999)
        yield self.svc.reconfigServiceWithBuildbotConfig(newer_config)

        self.assertNotEqual(self.svc.site, None)
        self.assertNotEqual(self.svc.port_service, None)
        self.assertEqual(self.svc.port, 999)

    @defer.inlineCallbacks
    def test_reconfigService_port_changes_to_none(self):
        new_config = self.makeConfig(port=20)
        yield self.svc.reconfigServiceWithBuildbotConfig(new_config)

        newer_config = self.makeConfig()
        yield self.svc.reconfigServiceWithBuildbotConfig(newer_config)

        # (note the site sticks around)
        self.assertEqual(self.svc.port_service, None)
        self.assertEqual(self.svc.port, None)

    def test_setupSite(self):
        self.svc.setupSite(self.makeConfig())
        site = self.svc.site

        # check that it has the right kind of resources attached to its
        # root
        root = site.resource
        req = mock.Mock()
        self.assertIsInstance(root.getChildWithDefault(b'api', req), rest.RestRootResource)

    def test_setupSiteWithProtectedHook(self):
        checker = InMemoryUsernamePasswordDatabaseDontUse()
        checker.addUser("guest", "password")

        self.svc.setupSite(
            self.makeConfig(change_hook_dialects={'base': True}, change_hook_auth=[checker])
        )
        site = self.svc.site

        # check that it has the right kind of resources attached to its
        # root
        root = site.resource
        req = mock.Mock()
        self.assertIsInstance(root.getChildWithDefault(b'change_hook', req), HTTPAuthSessionWrapper)

    @defer.inlineCallbacks
    def test_setupSiteWithHook(self):
        new_config = self.makeConfig(change_hook_dialects={'base': True})
        self.svc.setupSite(new_config)
        site = self.svc.site

        # check that it has the right kind of resources attached to its
        # root
        root = site.resource
        req = mock.Mock()
        ep = root.getChildWithDefault(b'change_hook', req)
        self.assertIsInstance(ep, change_hook.ChangeHookResource)

        # not yet configured
        self.assertEqual(ep.dialects, {})

        yield self.svc.reconfigServiceWithBuildbotConfig(new_config)

        # now configured
        self.assertEqual(ep.dialects, {'base': True})

        rsrc = self.svc.site.resource.getChildWithDefault(b'change_hook', mock.Mock())
        path = b'/change_hook/base'
        request = test_hooks_base._prepare_request({})
        self.master.data.updates.addChange = mock.Mock()
        yield self.render_resource(rsrc, path, request=request)
        self.master.data.updates.addChange.assert_called()

    @defer.inlineCallbacks
    def test_setupSiteWithHookAndAuth(self):
        fn = self.mktemp()
        with open(fn, 'w', encoding='utf-8') as f:
            f.write("user:pass")
        new_config = self.makeConfig(
            port=8080,
            plugins={},
            change_hook_dialects={'base': True},
            change_hook_auth=[strcred.makeChecker("file:" + fn)],
        )
        self.svc.setupSite(new_config)

        yield self.svc.reconfigServiceWithBuildbotConfig(new_config)
        rsrc = self.svc.site.resource.getChildWithDefault(b'', mock.Mock())

        res = yield self.render_resource(rsrc, b'')
        self.assertIn(b'{"type": "file"}', res)

        rsrc = self.svc.site.resource.getChildWithDefault(b'change_hook', mock.Mock())
        res = yield self.render_resource(rsrc, b'/change_hook/base')
        # as UnauthorizedResource is in private namespace, we cannot use
        # assertIsInstance :-(
        self.assertIn('UnauthorizedResource', repr(res))


class TestBuildbotSite(unittest.SynchronousTestCase):
    SECRET = 'secret'

    def setUp(self):
        self.site = service.BuildbotSite(None, "logs", 0, 0)
        self.site.setSessionSecret(self.SECRET)

    def test_getSession_from_bad_jwt(self):
        """if the cookie is bad (maybe from previous version of buildbot),
        then we should raise KeyError for consumption by caller,
        and log the JWT error
        """
        with self.assertRaises(KeyError):
            self.site.getSession("xxx")
        self.flushLoggedErrors(jwt.exceptions.DecodeError)

    def test_getSession_from_correct_jwt(self):
        payload = {'user_info': {'some': 'payload'}}
        uid = jwt.encode(payload, self.SECRET, algorithm=auth.SESSION_SECRET_ALGORITHM)
        session = self.site.getSession(uid)
        self.assertEqual(session.user_info, {'some': 'payload'})

    def test_getSession_from_expired_jwt(self):
        # expired one week ago
        exp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(weeks=1)
        exp = calendar.timegm(datetime.datetime.timetuple(exp))
        payload = {'user_info': {'some': 'payload'}, 'exp': exp}
        uid = jwt.encode(payload, self.SECRET, algorithm=auth.SESSION_SECRET_ALGORITHM)
        with self.assertRaises(KeyError):
            self.site.getSession(uid)

    def test_getSession_with_no_user_info(self):
        payload = {'foo': 'bar'}
        uid = jwt.encode(payload, self.SECRET, algorithm=auth.SESSION_SECRET_ALGORITHM)
        with self.assertRaises(KeyError):
            self.site.getSession(uid)

    def test_makeSession(self):
        session = self.site.makeSession()
        self.assertEqual(session.user_info, {'anonymous': True})

    def test_updateSession(self):
        session = self.site.makeSession()
        request = Request(FakeChannel(self.site), False)
        request.sitepath = [b"bb"]
        session.updateSession(request)
        self.assertEqual(len(request.cookies), 1)
        _, value = request.cookies[0].split(b";")[0].split(b"=")
        decoded = jwt.decode(value, self.SECRET, algorithms=[auth.SESSION_SECRET_ALGORITHM])
        self.assertEqual(decoded['user_info'], {'anonymous': True})
        self.assertIn('exp', decoded)

    def test_absentServerHeader(self):
        request = Request(FakeChannel(self.site), False)
        self.assertEqual(request.responseHeaders.hasHeader('Server'), False)
