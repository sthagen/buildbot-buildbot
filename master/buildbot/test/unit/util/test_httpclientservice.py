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

import datetime
import json
import os
from unittest import mock

from twisted.internet import defer
from twisted.internet import reactor
from twisted.python import components
from twisted.trial import unittest
from twisted.web import resource

from buildbot import interfaces
from buildbot.test.fake import httpclientservice as fakehttpclientservice
from buildbot.test.util.site import SiteWithClose
from buildbot.test.util.warnings import assertProducesWarning
from buildbot.util import bytes2unicode
from buildbot.util import httpclientservice
from buildbot.util import service
from buildbot.util import unicode2bytes

try:
    from requests.auth import HTTPDigestAuth
except ImportError:
    pass

# There is no way to unregister an adapter, so we have no other option
# than registering it as a module side effect :-(
components.registerAdapter(lambda m: m, mock.Mock, interfaces.IHttpResponse)


class HTTPClientServiceTestBase(unittest.TestCase):
    @defer.inlineCallbacks
    def setUp(self):
        if httpclientservice.txrequests is None or httpclientservice.treq is None:
            raise unittest.SkipTest('this test requires txrequests and treq')
        self.patch(httpclientservice, 'txrequests', mock.Mock())
        self.patch(httpclientservice, 'treq', mock.Mock())
        self.parent = service.MasterService()
        self.parent.reactor = reactor
        self.base_headers = {}
        yield self.parent.startService()


class HTTPClientServiceTestTxRequest(HTTPClientServiceTestBase):
    @defer.inlineCallbacks
    def setUp(self):
        yield super().setUp()
        self._http = yield httpclientservice.HTTPClientService.getService(
            self.parent, 'http://foo', headers=self.base_headers
        )

    @defer.inlineCallbacks
    def test_get(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.get('/bar')
        self._http._txrequests_sessions[0].request.assert_called_once_with(
            'get', 'http://foo/bar', headers={}, background_callback=mock.ANY
        )

    @defer.inlineCallbacks
    def test_get_full_url(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.get('http://other/bar')
        self._http._txrequests_sessions[0].request.assert_called_once_with(
            'get', 'http://other/bar', headers={}, background_callback=mock.ANY
        )

    @defer.inlineCallbacks
    def test_put(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.put('/bar', json={'foo': 'bar'})
        jsonStr = json.dumps({"foo": 'bar'})
        jsonBytes = unicode2bytes(jsonStr)
        headers = {'Content-Type': 'application/json'}
        self._http._txrequests_sessions[0].request.assert_called_once_with(
            'put', 'http://foo/bar', background_callback=mock.ANY, data=jsonBytes, headers=headers
        )

    @defer.inlineCallbacks
    def test_post(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.post('/bar', json={'foo': 'bar'})
        jsonStr = json.dumps({"foo": 'bar'})
        jsonBytes = unicode2bytes(jsonStr)
        headers = {'Content-Type': 'application/json'}
        self._http._txrequests_sessions[0].request.assert_called_once_with(
            'post', 'http://foo/bar', background_callback=mock.ANY, data=jsonBytes, headers=headers
        )

    @defer.inlineCallbacks
    def test_delete(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.delete('/bar')
        self._http._txrequests_sessions[0].request.assert_called_once_with(
            'delete', 'http://foo/bar', background_callback=mock.ANY, headers={}
        )

    @defer.inlineCallbacks
    def test_post_headers(self):
        self.base_headers.update({'X-TOKEN': 'XXXYYY'})
        with assertProducesWarning(DeprecationWarning):
            yield self._http.post('/bar', json={'foo': 'bar'})
        jsonStr = json.dumps({"foo": 'bar'})
        jsonBytes = unicode2bytes(jsonStr)
        self._http._txrequests_sessions[0].request.assert_called_once_with(
            'post',
            'http://foo/bar',
            background_callback=mock.ANY,
            data=jsonBytes,
            headers={'X-TOKEN': 'XXXYYY', 'Content-Type': 'application/json'},
        )

    @defer.inlineCallbacks
    def test_post_auth(self):
        self._http = yield httpclientservice.HTTPClientService.getService(
            self.parent, 'http://foo', auth=('user', 'pa$$')
        )
        with assertProducesWarning(DeprecationWarning):
            yield self._http.post('/bar', json={'foo': 'bar'})
        jsonStr = json.dumps({"foo": 'bar'})
        jsonBytes = unicode2bytes(jsonStr)
        self._http._txrequests_sessions[0].request.assert_called_once_with(
            'post',
            'http://foo/bar',
            background_callback=mock.ANY,
            data=jsonBytes,
            auth=('user', 'pa$$'),
            headers={'Content-Type': 'application/json'},
        )

    @defer.inlineCallbacks
    def test_post_mtls(self):
        self._http = yield httpclientservice.HTTPClientService.getService(
            self.parent,
            'http://foo',
            verify='/etc/pki/certbundle.pem',
            cert=('/etc/pki/cert.pem', '/etc/pki/key.pem'),
        )
        with assertProducesWarning(DeprecationWarning):
            yield self._http.post('/bar', json={'foo': 'bar'})
        jsonStr = json.dumps({"foo": 'bar'})
        jsonBytes = unicode2bytes(jsonStr)
        self._http._txrequests_sessions[0].request.assert_called_once_with(
            'post',
            'http://foo/bar',
            background_callback=mock.ANY,
            data=jsonBytes,
            verify='/etc/pki/certbundle.pem',
            cert=('/etc/pki/cert.pem', '/etc/pki/key.pem'),
            headers={'Content-Type': 'application/json'},
        )


class HTTPClientServiceTestTxRequestNoEncoding(HTTPClientServiceTestBase):
    @defer.inlineCallbacks
    def setUp(self):
        yield super().setUp()
        self._http = self.successResultOf(
            httpclientservice.HTTPClientService.getService(
                self.parent, 'http://foo', headers=self.base_headers, skipEncoding=True
            )
        )

    @defer.inlineCallbacks
    def test_post_raw(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.post('/bar', json={'foo': 'bar'})
        jsonStr = json.dumps({"foo": 'bar'})
        headers = {'Content-Type': 'application/json'}
        self._http._txrequests_sessions[0].request.assert_called_once_with(
            'post', 'http://foo/bar', background_callback=mock.ANY, data=jsonStr, headers=headers
        )

    @defer.inlineCallbacks
    def test_post_rawlist(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.post('/bar', json=[{'foo': 'bar'}])
        jsonStr = json.dumps([{"foo": 'bar'}])
        headers = {'Content-Type': 'application/json'}
        self._http._txrequests_sessions[0].request.assert_called_once_with(
            'post', 'http://foo/bar', background_callback=mock.ANY, data=jsonStr, headers=headers
        )


class HTTPClientServiceTestTReq(HTTPClientServiceTestBase):
    @defer.inlineCallbacks
    def setUp(self):
        yield super().setUp()
        self.patch(httpclientservice.HTTPClientService, 'PREFER_TREQ', True)
        self._http = yield httpclientservice.HTTPClientService.getService(
            self.parent, 'http://foo', headers=self.base_headers
        )

    @defer.inlineCallbacks
    def test_get(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.get('/bar')
        httpclientservice.treq.get.assert_called_once_with(
            'http://foo/bar', agent=mock.ANY, headers={}
        )

    @defer.inlineCallbacks
    def test_put(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.put('/bar', json={'foo': 'bar'})
        headers = {'Content-Type': ['application/json']}
        httpclientservice.treq.put.assert_called_once_with(
            'http://foo/bar', agent=mock.ANY, data=b'{"foo": "bar"}', headers=headers
        )

    @defer.inlineCallbacks
    def test_post(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.post('/bar', json={'foo': 'bar'})
        headers = {'Content-Type': ['application/json']}
        httpclientservice.treq.post.assert_called_once_with(
            'http://foo/bar', agent=mock.ANY, data=b'{"foo": "bar"}', headers=headers
        )

    @defer.inlineCallbacks
    def test_delete(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.delete('/bar')
        httpclientservice.treq.delete.assert_called_once_with(
            'http://foo/bar', agent=mock.ANY, headers={}
        )

    @defer.inlineCallbacks
    def test_post_headers(self):
        self.base_headers.update({'X-TOKEN': 'XXXYYY'})
        with assertProducesWarning(DeprecationWarning):
            yield self._http.post('/bar', json={'foo': 'bar'})
        headers = {'Content-Type': ['application/json'], 'X-TOKEN': ['XXXYYY']}
        httpclientservice.treq.post.assert_called_once_with(
            'http://foo/bar', agent=mock.ANY, data=b'{"foo": "bar"}', headers=headers
        )

    @defer.inlineCallbacks
    def test_post_auth(self):
        self._http = yield httpclientservice.HTTPClientService.getService(
            self.parent, 'http://foo', auth=('user', 'pa$$')
        )
        with assertProducesWarning(DeprecationWarning):
            yield self._http.post('/bar', json={'foo': 'bar'})
        headers = {
            'Content-Type': ['application/json'],
        }
        httpclientservice.treq.post.assert_called_once_with(
            'http://foo/bar',
            agent=mock.ANY,
            data=b'{"foo": "bar"}',
            auth=('user', 'pa$$'),
            headers=headers,
        )

    @defer.inlineCallbacks
    def test_post_auth_digest(self):
        auth = HTTPDigestAuth('user', 'pa$$')
        self._http = yield httpclientservice.HTTPClientService.getService(
            self.parent, 'http://foo', auth=auth
        )
        with assertProducesWarning(DeprecationWarning):
            yield self._http.post('/bar', data={'foo': 'bar'})
        # if digest auth, we don't use treq! we use txrequests
        self._http._txrequests_sessions[0].request.assert_called_once_with(
            'post',
            'http://foo/bar',
            background_callback=mock.ANY,
            data={"foo": 'bar'},
            auth=auth,
            headers={},
        )


class HTTPClientServiceTestTReqNoEncoding(HTTPClientServiceTestBase):
    @defer.inlineCallbacks
    def setUp(self):
        yield super().setUp()
        self.patch(httpclientservice.HTTPClientService, 'PREFER_TREQ', True)
        self._http = self.successResultOf(
            httpclientservice.HTTPClientService.getService(
                self.parent, 'http://foo', headers=self.base_headers, skipEncoding=True
            )
        )

    @defer.inlineCallbacks
    def test_post_raw(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.post('/bar', json={'foo': 'bar'})
        json_str = json.dumps({"foo": 'bar'})
        headers = {'Content-Type': ['application/json']}
        httpclientservice.treq.post.assert_called_once_with(
            'http://foo/bar', agent=mock.ANY, data=json_str, headers=headers
        )

    @defer.inlineCallbacks
    def test_post_rawlist(self):
        with assertProducesWarning(DeprecationWarning):
            yield self._http.post('/bar', json=[{'foo': 'bar'}])
        json_str = json.dumps([{"foo": 'bar'}])
        headers = {'Content-Type': ['application/json']}
        httpclientservice.treq.post.assert_called_once_with(
            'http://foo/bar', agent=mock.ANY, data=json_str, headers=headers
        )


class MyResource(resource.Resource):
    isLeaf = True

    def render_GET(self, request):
        def decode(x):
            if isinstance(x, bytes):
                return bytes2unicode(x)
            elif isinstance(x, (list, tuple)):
                return [bytes2unicode(y) for y in x]
            elif isinstance(x, dict):
                newArgs = {}
                for a, b in x.items():
                    newArgs[decode(a)] = decode(b)
                return newArgs
            return x

        args = decode(request.args)
        content_type = request.getHeader(b'content-type')
        if content_type == b"application/json":
            jsonBytes = request.content.read()
            jsonStr = bytes2unicode(jsonBytes)
            args['json_received'] = json.loads(jsonStr)

        data = json.dumps(args)
        data = unicode2bytes(data)
        request.setHeader(b'content-type', b'application/json')
        request.setHeader(b'content-length', b"%d" % len(data))
        if request.method == b'HEAD':
            return b''
        return data

    render_HEAD = render_GET
    render_POST = render_GET


class HTTPClientServiceTestTxRequestE2E(unittest.TestCase):
    """The e2e tests must be the same for txrequests and treq

    We just force treq in the other TestCase
    """

    def httpFactory(self, parent):
        return httpclientservice.HTTPClientService.getService(
            parent, f'http://127.0.0.1:{self.port}'
        )

    def expect(self, *arg, **kwargs):
        pass

    @defer.inlineCallbacks
    def setUp(self):
        # On slower machines with high CPU oversubscription this test may take longer to run than
        # the default timeout.
        self.timeout = 10

        if httpclientservice.txrequests is None or httpclientservice.treq is None:
            raise unittest.SkipTest('this test requires txrequests and treq')
        self.site = SiteWithClose(MyResource())
        self.addCleanup(self.site.close_connections)
        self.addCleanup(self.site.stopFactory)

        self.listenport = reactor.listenTCP(0, self.site)
        self.addCleanup(self.listenport.stopListening)

        self.port = self.listenport.getHost().port
        self.parent = parent = service.MasterService()
        self.parent.reactor = reactor
        yield parent.startService()
        self.addCleanup(self.parent.stopService)
        self._http = yield self.httpFactory(parent)

    @defer.inlineCallbacks
    def test_content(self):
        self.expect('get', '/', content_json={})
        with assertProducesWarning(DeprecationWarning):
            res = yield self._http.get('/')
        content = yield res.content()
        self.assertEqual(content, b'{}')

    @defer.inlineCallbacks
    def test_content_with_params(self):
        self.expect('get', '/', params={"a": 'b'}, content_json={"a": ['b']})
        with assertProducesWarning(DeprecationWarning):
            res = yield self._http.get('/', params={"a": 'b'})
        content = yield res.content()
        self.assertEqual(content, b'{"a": ["b"]}')

    @defer.inlineCallbacks
    def test_post_content_with_params(self):
        self.expect('post', '/', params={"a": 'b'}, content_json={"a": ['b']})
        with assertProducesWarning(DeprecationWarning):
            res = yield self._http.post('/', params={"a": 'b'})
        content = yield res.content()
        self.assertEqual(content, b'{"a": ["b"]}')

    @defer.inlineCallbacks
    def test_put_content_with_data(self):
        self.expect('post', '/', data={"a": 'b'}, content_json={"a": ['b']})
        with assertProducesWarning(DeprecationWarning):
            res = yield self._http.post('/', data={"a": 'b'})
        content = yield res.content()
        self.assertEqual(content, b'{"a": ["b"]}')

    @defer.inlineCallbacks
    def test_put_content_with_json(self):
        exp_content_json = {"json_received": {"a": 'b'}}
        self.expect('post', '/', json={"a": 'b'}, content_json=exp_content_json)
        with assertProducesWarning(DeprecationWarning):
            res = yield self._http.post('/', json={"a": 'b'})
        content = yield res.content()
        content = bytes2unicode(content)
        content = json.loads(content)
        self.assertEqual(content, exp_content_json)

    @defer.inlineCallbacks
    def test_put_content_with_json_datetime(self):
        exp_content_json = {"json_received": {"a": 'b', "ts": 12}}
        dt = datetime.datetime.fromtimestamp(12, datetime.timezone.utc)
        self.expect('post', '/', json={"a": 'b', "ts": dt}, content_json=exp_content_json)
        with assertProducesWarning(DeprecationWarning):
            res = yield self._http.post('/', json={"a": 'b', "ts": dt})
        content = yield res.content()
        content = bytes2unicode(content)
        content = json.loads(content)
        self.assertEqual(content, exp_content_json)

    @defer.inlineCallbacks
    def test_json(self):
        self.expect('get', '/', content_json={})
        with assertProducesWarning(DeprecationWarning):
            res = yield self._http.get('/')
        content = yield res.json()
        self.assertEqual(content, {})
        self.assertEqual(res.code, 200)

    # note that freebsd workers will not like when there are too many parallel connections
    # we can change this test via environment variable
    NUM_PARALLEL = int(os.environ.get("BBTEST_NUM_PARALLEL", "5"))

    @defer.inlineCallbacks
    def test_lots(self):
        for _ in range(self.NUM_PARALLEL):
            self.expect('get', '/', params={"a": 'b'}, content_json={"a": ['b']})
        # use for benchmarking (txrequests: 3ms per request treq: 1ms per
        # request)
        for _ in range(self.NUM_PARALLEL):
            with assertProducesWarning(DeprecationWarning):
                res = yield self._http.get('/', params={"a": 'b'})
            content = yield res.content()
            self.assertEqual(content, b'{"a": ["b"]}')

    @defer.inlineCallbacks
    def test_lots_parallel(self):
        for _ in range(self.NUM_PARALLEL):
            self.expect('get', '/', params={"a": 'b'}, content_json={"a": ['b']})

        # use for benchmarking (txrequests: 3ms per request treq: 11ms per
        # request (!?))
        def oneReq():
            with assertProducesWarning(DeprecationWarning):
                d = self._http.get('/', params={"a": 'b'})

            @d.addCallback
            def content(res):
                return res.content()

            return d

        dl = [oneReq() for i in range(self.NUM_PARALLEL)]
        yield defer.gatherResults(dl, consumeErrors=True)


class HTTPClientServiceTestTReqE2E(HTTPClientServiceTestTxRequestE2E):
    @defer.inlineCallbacks
    def setUp(self):
        self.patch(httpclientservice.HTTPClientService, 'PREFER_TREQ', True)
        yield super().setUp()


class HTTPClientServiceTestFakeE2E(HTTPClientServiceTestTxRequestE2E):
    @defer.inlineCallbacks
    def httpFactory(self, parent):
        service = yield fakehttpclientservice.HTTPClientService.getService(
            parent, self, f'http://127.0.0.1:{self.port}'
        )
        return service

    def expect(self, *arg, **kwargs):
        self._http.expect(*arg, **kwargs)
