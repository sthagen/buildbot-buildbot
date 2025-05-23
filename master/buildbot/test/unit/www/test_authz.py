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

from twisted.internet import defer
from twisted.trial import unittest

from buildbot.test import fakedb
from buildbot.test.reactor import TestReactorMixin
from buildbot.test.util import www
from buildbot.www import authz
from buildbot.www.authz.endpointmatchers import AnyControlEndpointMatcher
from buildbot.www.authz.endpointmatchers import AnyEndpointMatcher
from buildbot.www.authz.endpointmatchers import BranchEndpointMatcher
from buildbot.www.authz.endpointmatchers import EndpointMatcherBase
from buildbot.www.authz.endpointmatchers import ForceBuildEndpointMatcher
from buildbot.www.authz.endpointmatchers import RebuildBuildEndpointMatcher
from buildbot.www.authz.endpointmatchers import StopBuildEndpointMatcher
from buildbot.www.authz.endpointmatchers import ViewBuildsEndpointMatcher
from buildbot.www.authz.roles import RolesFromDomain
from buildbot.www.authz.roles import RolesFromEmails
from buildbot.www.authz.roles import RolesFromGroups
from buildbot.www.authz.roles import RolesFromOwner

if TYPE_CHECKING:
    from buildbot.util.twisted import InlineCallbacksType


class Authz(TestReactorMixin, www.WwwTestMixin, unittest.TestCase):
    @defer.inlineCallbacks
    def setUp(self) -> InlineCallbacksType[None]:  # type: ignore[override]
        self.setup_test_reactor()
        authzcfg = authz.Authz(
            # simple matcher with '*' glob character
            stringsMatcher=authz.fnmatchStrMatcher,
            # stringsMatcher = authz.Authz.reStrMatcher,  # if you prefer
            # regular expressions
            allowRules=[
                # admins can do anything,
                # defaultDeny=False: if user does not have the admin role, we
                # continue parsing rules
                AnyEndpointMatcher(role="admins", defaultDeny=False),
                # rules for viewing builds, builders, step logs
                # depending on the sourcestamp or buildername
                ViewBuildsEndpointMatcher(branch="secretbranch", role="agents"),
                ViewBuildsEndpointMatcher(project="secretproject", role="agents"),
                ViewBuildsEndpointMatcher(branch="*", role="*"),
                ViewBuildsEndpointMatcher(project="*", role="*"),
                StopBuildEndpointMatcher(role="owner"),
                RebuildBuildEndpointMatcher(role="owner"),
                # nine-* groups can do stuff on the nine branch
                BranchEndpointMatcher(branch="nine", role="nine-*"),
                # eight-* groups can do stuff on the eight branch
                BranchEndpointMatcher(branch="eight", role="eight-*"),
                # *-try groups can start "try" builds
                ForceBuildEndpointMatcher(builder="try", role="*-developers"),
                # *-mergers groups can start "merge" builds
                ForceBuildEndpointMatcher(builder="merge", role="*-mergers"),
                # *-releasers groups can start "release" builds
                ForceBuildEndpointMatcher(builder="release", role="*-releasers"),
                # finally deny any control endpoint for non-admin users
                AnyControlEndpointMatcher(role="admins"),
            ],
            roleMatchers=[
                RolesFromGroups(groupPrefix="buildbot-"),
                RolesFromEmails(admins=["homer@springfieldplant.com"], agents=["007@mi6.uk"]),
                RolesFromOwner(role="owner"),
                RolesFromDomain(admins=["mi7.uk"]),
            ],
        )
        self.users = {
            "homer": {"email": "homer@springfieldplant.com"},
            "bond": {"email": "007@mi6.uk"},
            "moneypenny": {"email": "moneypenny@mi7.uk"},
            "nineuser": {
                "email": "user@nine.com",
                "groups": ["buildbot-nine-mergers", "buildbot-nine-developers"],
            },
            "eightuser": {"email": "user@eight.com", "groups": ["buildbot-eight-developers"]},
        }
        self.master = yield self.make_master(url='h:/a/b/', authz=authzcfg)
        self.authz = self.master.authz
        yield self.master.db.insert_test_data([
            fakedb.Builder(id=77, name="mybuilder"),
            fakedb.Master(id=88),
            fakedb.Worker(id=13, name='wrk'),
            fakedb.Buildset(id=8822),
            fakedb.BuildsetProperty(
                buildsetid=8822, property_name='owner', property_value='["user@nine.com", "force"]'
            ),
            fakedb.BuildRequest(id=82, buildsetid=8822, builderid=77),
            fakedb.Build(
                id=13, builderid=77, masterid=88, workerid=13, buildrequestid=82, number=3
            ),
            fakedb.Build(
                id=14, builderid=77, masterid=88, workerid=13, buildrequestid=82, number=4
            ),
            fakedb.Build(
                id=15, builderid=77, masterid=88, workerid=13, buildrequestid=82, number=5
            ),
        ])

    def setAllowRules(self, allow_rules: list[EndpointMatcherBase]) -> None:
        # we should add links to authz and master instances in each new rule
        for r in allow_rules:
            r.setAuthz(self.authz)

        self.authz.allowRules = allow_rules

    def assertUserAllowed(self, ep: str, action: str, options: dict, user: str) -> defer.Deferred:
        return self.authz.assertUserAllowed(tuple(ep.split("/")), action, options, self.users[user])

    @defer.inlineCallbacks
    def assertUserForbidden(
        self, ep: str, action: str, options: dict, user: str
    ) -> InlineCallbacksType[None]:
        try:
            yield self.authz.assertUserAllowed(
                tuple(ep.split("/")), action, options, self.users[user]
            )
        except authz.Forbidden as err:
            self.assertIn('need to have role', repr(err))
        else:
            self.fail('authz.Forbidden with error "need to have role" was expected!')

    @defer.inlineCallbacks
    def test_anyEndpoint(self) -> InlineCallbacksType[None]:
        # admin users can do anything
        yield self.assertUserAllowed("foo/bar", "get", {}, "homer")
        yield self.assertUserAllowed("foo/bar", "stop", {}, "moneypenny")
        # non-admin user can only do "get" action
        yield self.assertUserAllowed("foo/bar", "get", {}, "bond")
        # non-admin user cannot do control actions
        yield self.assertUserForbidden("foo/bar", "stop", {}, "bond")

        # non-admin user cannot do any actions
        allow_rules: list[EndpointMatcherBase] = [
            AnyEndpointMatcher(role="admins"),
        ]
        self.setAllowRules(allow_rules)
        yield self.assertUserForbidden("foo/bar", "get", {}, "bond")
        yield self.assertUserForbidden("foo/bar", "stop", {}, "bond")

    @defer.inlineCallbacks
    def test_stopBuild(self) -> InlineCallbacksType[None]:
        # admin can always stop
        yield self.assertUserAllowed("builds/13", "stop", {}, "homer")
        # owner can always stop
        yield self.assertUserAllowed("builds/13", "stop", {}, "nineuser")
        yield self.assertUserAllowed("buildrequests/82", "stop", {}, "nineuser")
        # not owner cannot stop
        yield self.assertUserForbidden("builds/13", "stop", {}, "eightuser")
        yield self.assertUserForbidden("buildrequests/82", "stop", {}, "eightuser")

        # can stop build/buildrequest with matching builder
        allow_rules = [
            StopBuildEndpointMatcher(role="eight-*", builder="mybuilder"),
            AnyEndpointMatcher(role="admins"),
        ]
        self.setAllowRules(allow_rules)
        yield self.assertUserAllowed("builds/13", "stop", {}, "eightuser")
        yield self.assertUserAllowed("buildrequests/82", "stop", {}, "eightuser")
        yield self.assertUserForbidden("builds/999", "stop", {}, "eightuser")
        yield self.assertUserForbidden("buildrequests/999", "stop", {}, "eightuser")

        # cannot stop build/buildrequest with non-matching builder
        allow_rules = [
            StopBuildEndpointMatcher(role="eight-*", builder="foo"),
            AnyEndpointMatcher(role="admins"),
        ]
        self.setAllowRules(allow_rules)
        yield self.assertUserForbidden("builds/13", "stop", {}, "eightuser")
        yield self.assertUserForbidden("buildrequests/82", "stop", {}, "eightuser")

    @defer.inlineCallbacks
    def test_rebuildBuild(self) -> InlineCallbacksType[None]:
        # admin can rebuild
        yield self.assertUserAllowed("builds/13", "rebuild", {}, "homer")
        # owner can always rebuild
        yield self.assertUserAllowed("builds/13", "rebuild", {}, "nineuser")
        # not owner cannot rebuild
        yield self.assertUserForbidden("builds/13", "rebuild", {}, "eightuser")

        # can rebuild build with matching builder
        allow_rules = [
            RebuildBuildEndpointMatcher(role="eight-*", builder="mybuilder"),
            AnyEndpointMatcher(role="admins"),
        ]
        self.setAllowRules(allow_rules)
        yield self.assertUserAllowed("builds/13", "rebuild", {}, "eightuser")
        yield self.assertUserForbidden("builds/999", "rebuild", {}, "eightuser")

        # cannot rebuild build with non-matching builder
        allow_rules = [
            RebuildBuildEndpointMatcher(role="eight-*", builder="foo"),
            AnyEndpointMatcher(role="admins"),
        ]
        self.setAllowRules(allow_rules)
        yield self.assertUserForbidden("builds/13", "rebuild", {}, "eightuser")

    @defer.inlineCallbacks
    def test_fnmatchPatternRoleCheck(self) -> InlineCallbacksType[None]:
        # defaultDeny is True by default so action is denied if no match
        allow_rules: list[EndpointMatcherBase] = [AnyEndpointMatcher(role="[a,b]dmin?")]

        self.setAllowRules(allow_rules)

        yield self.assertUserAllowed("builds/13", "rebuild", {}, "homer")

        # check if action is denied
        with self.assertRaisesRegex(authz.Forbidden, '403 you need to have role .+'):
            yield self.assertUserAllowed("builds/13", "rebuild", {}, "nineuser")

        with self.assertRaisesRegex(authz.Forbidden, '403 you need to have role .+'):
            yield self.assertUserAllowed("builds/13", "rebuild", {}, "eightuser")

    @defer.inlineCallbacks
    def test_regexPatternRoleCheck(self) -> InlineCallbacksType[None]:
        # change matcher
        self.authz.match = authz.reStrMatcher

        # defaultDeny is True by default so action is denied if no match
        allow_rules: list[EndpointMatcherBase] = [
            AnyEndpointMatcher(role="(admin|agent)s"),
        ]

        self.setAllowRules(allow_rules)

        yield self.assertUserAllowed("builds/13", "rebuild", {}, "homer")
        yield self.assertUserAllowed("builds/13", "rebuild", {}, "bond")

        # check if action is denied
        with self.assertRaisesRegex(authz.Forbidden, '403 you need to have role .+'):
            yield self.assertUserAllowed("builds/13", "rebuild", {}, "nineuser")

        with self.assertRaisesRegex(authz.Forbidden, '403 you need to have role .+'):
            yield self.assertUserAllowed("builds/13", "rebuild", {}, "eightuser")

    @defer.inlineCallbacks
    def test_DefaultDenyFalseContinuesCheck(self) -> InlineCallbacksType[None]:
        # defaultDeny is True in the last rule so action is denied in the last check
        allow_rules: list[EndpointMatcherBase] = [
            AnyEndpointMatcher(role="not-exists1", defaultDeny=False),
            AnyEndpointMatcher(role="not-exists2", defaultDeny=False),
            AnyEndpointMatcher(role="not-exists3", defaultDeny=True),
        ]

        self.setAllowRules(allow_rules)
        # check if action is denied and last check was exact against not-exist3
        with self.assertRaisesRegex(authz.Forbidden, '.+not-exists3.+'):
            yield self.assertUserAllowed("builds/13", "rebuild", {}, "nineuser")

    @defer.inlineCallbacks
    def test_DefaultDenyTrueStopsCheckIfFailed(self) -> InlineCallbacksType[None]:
        # defaultDeny is True in the first rule so action is denied in the first check
        allow_rules: list[EndpointMatcherBase] = [
            AnyEndpointMatcher(role="not-exists1", defaultDeny=True),
            AnyEndpointMatcher(role="not-exists2", defaultDeny=False),
            AnyEndpointMatcher(role="not-exists3", defaultDeny=False),
        ]

        self.setAllowRules(allow_rules)

        # check if action is denied and last check was exact against not-exist1
        with self.assertRaisesRegex(authz.Forbidden, '.+not-exists1.+'):
            yield self.assertUserAllowed("builds/13", "rebuild", {}, "nineuser")
