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

import base64
import textwrap
from typing import TYPE_CHECKING
from unittest import mock

import sqlalchemy as sa
from twisted.internet import defer
from twisted.trial import unittest

from buildbot.db import compression
from buildbot.db import logs
from buildbot.test import fakedb
from buildbot.test.fake import fakemaster
from buildbot.test.reactor import TestReactorMixin
from buildbot.util import bytes2unicode
from buildbot.util import unicode2bytes
from buildbot.util.twisted import async_to_deferred

if TYPE_CHECKING:
    from typing import Callable


class FakeUnavailableCompressor(compression.CompressorInterface):
    name = "fake"
    available = False

    HEADER = b"[FakeHeader]"

    @staticmethod
    def dumps(data: bytes) -> bytes:
        return FakeUnavailableCompressor.HEADER + data

    @staticmethod
    def read(data: bytes) -> bytes:
        assert data.startswith(FakeUnavailableCompressor.HEADER)
        return data[len(FakeUnavailableCompressor.HEADER) :]


class Tests(TestReactorMixin, unittest.TestCase):
    TIMESTAMP_STEP101 = 100000
    TIMESTAMP_STEP102 = 200000
    backgroundData = [
        fakedb.Worker(id=47, name='linux'),
        fakedb.Buildset(id=20),
        fakedb.Builder(id=88, name='b1'),
        fakedb.BuildRequest(id=41, buildsetid=20, builderid=88),
        fakedb.Master(id=88),
        fakedb.Build(id=30, buildrequestid=41, number=7, masterid=88, builderid=88, workerid=47),
        fakedb.Step(id=101, buildid=30, number=1, name='one', started_at=TIMESTAMP_STEP101),
        fakedb.Step(id=102, buildid=30, number=2, name='two', started_at=TIMESTAMP_STEP102),
    ]
    insert_test_data: Callable[[list], defer.Deferred]

    testLogLines = [
        fakedb.Log(
            id=201, stepid=101, name='stdio', slug='stdio', complete=0, num_lines=7, type='s'
        ),
        fakedb.LogChunk(
            logid=201,
            first_line=0,
            last_line=1,
            compressed=0,
            content=textwrap.dedent(
                """\
                    line zero
                    line 1"""
                + "x" * 200
            ),
        ),
        fakedb.LogChunk(
            logid=201,
            first_line=2,
            last_line=4,
            compressed=0,
            content=textwrap.dedent("""\
                    line TWO

                    line 2**2"""),
        ),
        fakedb.LogChunk(logid=201, first_line=5, last_line=5, compressed=0, content="another line"),
        fakedb.LogChunk(
            logid=201, first_line=6, last_line=6, compressed=0, content="yet another line"
        ),
    ]
    bug3101Content = base64.b64decode("""
        PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0
        9PT09PT09PT09PT09PT09PT09PT09PT09PT09PQpbU0tJUFBFRF0Kbm90IGEgd2luMz
        IgcGxhdGZvcm0KCmJ1aWxkc2xhdmUudGVzdC51bml0LnRlc3RfcnVucHJvY2Vzcy5UZ
        XN0UnVuUHJvY2Vzcy50ZXN0UGlwZVN0cmluZwotLS0tLS0tLS0tLS0tLS0tLS0tLS0t
        LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0
        tLS0tLS0tClJhbiAyNjcgdGVzdHMgaW4gNS4zNzhzCgpQQVNTRUQgKHNraXBzPTEsIH
        N1Y2Nlc3Nlcz0yNjYpCnByb2dyYW0gZmluaXNoZWQgd2l0aCBleGl0IGNvZGUgMAplb
        GFwc2VkVGltZT04LjI0NTcwMg==""")

    bug3101Rows = [
        fakedb.Log(
            id=1470,
            stepid=101,
            name='problems',
            slug='problems',
            complete=1,
            num_lines=11,
            type='t',
        ),
        fakedb.LogChunk(
            logid=1470, first_line=0, last_line=10, compressed=0, content=bug3101Content
        ),
    ]

    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        self.master = yield fakemaster.make_master(self, wantDb=True)
        self.db = self.master.db

    @defer.inlineCallbacks
    def checkTestLogLines(self):
        expLines = [
            'line zero',
            'line 1' + "x" * 200,
            'line TWO',
            '',
            'line 2**2',
            'another line',
            'yet another line',
        ]

        def _join_lines(lines: list[str]):
            return ''.join(e + '\n' for e in lines)

        for first_line in range(0, 7):
            for last_line in range(first_line, 7):
                got_lines = yield self.db.logs.getLogLines(201, first_line, last_line)
                self.assertEqual(got_lines, _join_lines(expLines[first_line : last_line + 1]))
        # check overflow
        self.assertEqual((yield self.db.logs.getLogLines(201, 5, 20)), _join_lines(expLines[5:7]))

    @defer.inlineCallbacks
    def test_getLog(self):
        yield self.db.insert_test_data([
            *self.backgroundData,
            fakedb.Log(
                id=201, stepid=101, name="stdio", slug="stdio", complete=0, num_lines=200, type="s"
            ),
        ])
        logdict = yield self.db.logs.getLog(201)
        self.assertIsInstance(logdict, logs.LogModel)
        self.assertEqual(
            logdict,
            logs.LogModel(
                id=201,
                stepid=101,
                name='stdio',
                slug='stdio',
                complete=False,
                num_lines=200,
                type='s',
            ),
        )

    @defer.inlineCallbacks
    def test_getLog_missing(self):
        logdict = yield self.db.logs.getLog(201)
        self.assertEqual(logdict, None)

    @defer.inlineCallbacks
    def test_getLogBySlug(self):
        yield self.db.insert_test_data([
            *self.backgroundData,
            fakedb.Log(
                id=201, stepid=101, name="stdio", slug="stdio", complete=0, num_lines=200, type="s"
            ),
            fakedb.Log(
                id=202,
                stepid=101,
                name="dbg.log",
                slug="dbg_log",
                complete=1,
                num_lines=200,
                type="s",
            ),
        ])
        logdict = yield self.db.logs.getLogBySlug(101, 'dbg_log')
        self.assertIsInstance(logdict, logs.LogModel)
        self.assertEqual(logdict.id, 202)

    @defer.inlineCallbacks
    def test_getLogBySlug_missing(self):
        yield self.db.insert_test_data([
            *self.backgroundData,
            fakedb.Log(
                id=201, stepid=101, name="stdio", slug="stdio", complete=0, num_lines=200, type="s"
            ),
        ])
        logdict = yield self.db.logs.getLogBySlug(102, 'stdio')
        self.assertEqual(logdict, None)

    @defer.inlineCallbacks
    def test_getLogs(self):
        yield self.db.insert_test_data([
            *self.backgroundData,
            fakedb.Log(
                id=201, stepid=101, name="stdio", slug="stdio", complete=0, num_lines=200, type="s"
            ),
            fakedb.Log(
                id=202,
                stepid=101,
                name="dbg.log",
                slug="dbg_log",
                complete=1,
                num_lines=300,
                type="t",
            ),
            fakedb.Log(
                id=203, stepid=102, name="stdio", slug="stdio", complete=0, num_lines=200, type="s"
            ),
        ])
        logdicts = yield self.db.logs.getLogs(101)
        for logdict in logdicts:
            self.assertIsInstance(logdict, logs.LogModel)
        self.assertEqual(sorted([ld.id for ld in logdicts]), [201, 202])

    @defer.inlineCallbacks
    def test_getLogLines(self):
        yield self.db.insert_test_data(self.backgroundData + self.testLogLines)
        yield self.checkTestLogLines()

        # check line number reversal
        self.assertEqual((yield self.db.logs.getLogLines(201, 6, 3)), '')

    @defer.inlineCallbacks
    def test_getLogLines_empty(self):
        yield self.db.insert_test_data([
            *self.backgroundData,
            fakedb.Log(
                id=201, stepid=101, name="stdio", slug="stdio", complete=0, num_lines=200, type="s"
            ),
        ])
        self.assertEqual((yield self.db.logs.getLogLines(201, 9, 99)), '')
        self.assertEqual((yield self.db.logs.getLogLines(999, 9, 99)), '')

    @defer.inlineCallbacks
    def test_getLogLines_bug3101(self):
        # regression test for #3101
        content = self.bug3101Content
        yield self.db.insert_test_data(self.backgroundData + self.bug3101Rows)
        # overall content is the same, with '\n' padding at the end
        expected = bytes2unicode(self.bug3101Content + b'\n')
        self.assertEqual((yield self.db.logs.getLogLines(1470, 0, 99)), expected)
        # try to fetch just one line
        expected = bytes2unicode(content.split(b'\n')[0] + b'\n')
        self.assertEqual((yield self.db.logs.getLogLines(1470, 0, 0)), expected)

    @defer.inlineCallbacks
    def test_addLog_getLog(self):
        yield self.db.insert_test_data(self.backgroundData)
        logid = yield self.db.logs.addLog(
            stepid=101, name='config.log', slug='config_log', type='t'
        )
        logdict = yield self.db.logs.getLog(logid)
        self.assertIsInstance(logdict, logs.LogModel)
        self.assertEqual(
            logdict,
            logs.LogModel(
                id=logid,
                stepid=101,
                name='config.log',
                slug='config_log',
                complete=False,
                num_lines=0,
                type='t',
            ),
        )

    @defer.inlineCallbacks
    def test_appendLog_getLogLines(self):
        yield self.db.insert_test_data(self.backgroundData + self.testLogLines)
        logid = yield self.db.logs.addLog(stepid=102, name='another', slug='another', type='s')
        self.assertEqual((yield self.db.logs.appendLog(logid, 'xyz\n')), (0, 0))
        self.assertEqual((yield self.db.logs.appendLog(201, 'abc\ndef\n')), (7, 8))
        self.assertEqual((yield self.db.logs.appendLog(logid, 'XYZ\n')), (1, 1))
        self.assertEqual((yield self.db.logs.getLogLines(201, 6, 7)), "yet another line\nabc\n")
        self.assertEqual((yield self.db.logs.getLogLines(201, 7, 8)), "abc\ndef\n")
        self.assertEqual((yield self.db.logs.getLogLines(201, 8, 8)), "def\n")
        self.assertEqual((yield self.db.logs.getLogLines(logid, 0, 1)), "xyz\nXYZ\n")
        self.assertEqual(
            (yield self.db.logs.getLog(logid)),
            logs.LogModel(
                complete=False,
                id=logid,
                name='another',
                slug='another',
                num_lines=2,
                stepid=102,
                type='s',
            ),
        )

    @defer.inlineCallbacks
    def test_compressLog(self):
        yield self.db.insert_test_data(self.backgroundData + self.testLogLines)
        yield self.db.logs.compressLog(201)
        # test log lines should still be readable just the same
        yield self.checkTestLogLines()

    @defer.inlineCallbacks
    def test_addLogLines_big_chunk(self):
        yield self.db.insert_test_data(self.backgroundData + self.testLogLines)
        self.assertEqual(
            (yield self.db.logs.appendLog(201, 'abc\n' * 20000)),  # 80k
            (7, 20006),
        )
        lines = yield self.db.logs.getLogLines(201, 7, 50000)
        self.assertEqual(len(lines), 80000)
        self.assertEqual(lines, ('abc\n' * 20000))

    @defer.inlineCallbacks
    def test_addLogLines_big_chunk_big_lines(self):
        yield self.db.insert_test_data(self.backgroundData + self.testLogLines)
        line = 'x' * 33000 + '\n'
        self.assertEqual(
            (yield self.db.logs.appendLog(201, line * 3)), (7, 9)
        )  # three long lines, all truncated
        lines = yield self.db.logs.getLogLines(201, 7, 100)
        self.assertEqual(len(lines), 99003)
        self.assertEqual(lines, (line * 3))

    @defer.inlineCallbacks
    def test_addLogLines_db(self):
        yield self.db.insert_test_data(self.backgroundData + self.testLogLines)
        self.assertEqual((yield self.db.logs.appendLog(201, 'abc\ndef\nghi\njkl\n')), (7, 10))

        def thd(conn):
            res = conn.execute(
                self.db.model.logchunks.select().where(self.db.model.logchunks.c.first_line > 6)
            ).mappings()
            row = res.fetchone()
            res.close()
            return dict(row)

        newRow = yield self.db.pool.do(thd)
        self.assertEqual(
            newRow,
            {
                'logid': 201,
                'first_line': 7,
                'last_line': 10,
                'content': b'abc\ndef\nghi\njkl',
                'compressed': 0,
            },
        )

    async def _base_appendLog_truncate(self, content: str):
        LOG_ID = 201
        await self.db.insert_test_data([
            *self.backgroundData,
            fakedb.Log(
                id=LOG_ID,
                stepid=101,
                name='stdio',
                slug='stdio',
                complete=0,
                num_lines=0,
                type='s',
            ),
        ])
        await self.db.logs.appendLog(LOG_ID, content)

        def _thd(conn: sa.engine.Connection) -> list[dict]:
            tbl = self.db.model.logchunks
            res = conn.execute(
                tbl.select().where(tbl.c.logid == LOG_ID).order_by(tbl.c.first_line)
            ).mappings()
            rows = [dict(row) for row in res]
            res.close()
            return rows

        return await self.db.pool.do(_thd)

    @async_to_deferred
    async def test_appendLog_no_truncate_compressable_chunks(self):
        content = 'a ' + '\N{SNOWMAN}' * 100000 + '\n'
        assert len(content) > self.db.logs.MAX_CHUNK_SIZE
        self.db.master.config.logCompressionMethod = "gz"
        rows = await self._base_appendLog_truncate(content)
        self.assertEqual(
            [
                {
                    'compressed': 1,
                    'content': self.db.logs._get_compressor(1).dumps(content[:-1].encode('utf-8')),
                    'first_line': 0,
                    'last_line': 0,
                    'logid': 201,
                }
            ],
            rows,
        )

    @async_to_deferred
    async def test_appendLog_truncate_chunk(self):
        self.maxDiff = None
        content = 'a ' + '\N{SNOWMAN}' * 100000 + '\n'
        assert len(content) > self.db.logs.MAX_CHUNK_SIZE
        self.db.master.config.logCompressionMethod = "raw"
        rows = await self._base_appendLog_truncate(content)
        self.assertTrue(len(rows[0].pop('content')) <= self.db.logs.MAX_CHUNK_SIZE)
        self.assertEqual(
            [
                {
                    'compressed': 0,
                    'first_line': 0,
                    'last_line': 0,
                    'logid': 201,
                }
            ],
            rows,
        )

    @defer.inlineCallbacks
    def test_no_compress_small_chunk(self):
        yield self.db.insert_test_data(self.backgroundData + self.testLogLines)
        self.db.master.config.logCompressionMethod = "gz"
        self.assertEqual((yield self.db.logs.appendLog(201, 'abc\n')), (7, 7))

        def thd(conn):
            res = conn.execute(
                self.db.model.logchunks.select().where(self.db.model.logchunks.c.first_line > 6)
            ).mappings()
            row = res.fetchone()
            res.close()
            return dict(row)

        newRow = yield self.db.pool.do(thd)
        self.assertEqual(
            newRow,
            {'logid': 201, 'first_line': 7, 'last_line': 7, 'content': b'abc', 'compressed': 0},
        )

    async def _test_compress_big_chunk(
        self,
        compressor: compression.CompressorInterface,
        compressed_id: int,
    ) -> None:
        await self.db.insert_test_data(self.backgroundData + self.testLogLines)
        line = 'xy' * 10000
        self.assertEqual((await self.db.logs.appendLog(201, line + '\n')), (7, 7))

        def thd(conn):
            res = conn.execute(
                self.db.model.logchunks.select().where(self.db.model.logchunks.c.first_line > 6)
            ).mappings()
            row = res.fetchone()
            res.close()
            return dict(row)

        newRow = await self.db.pool.do(thd)
        self.assertEqual(compressor.read(newRow.pop('content')), unicode2bytes(line))
        self.assertEqual(
            newRow,
            {
                'logid': 201,
                'first_line': 7,
                'last_line': 7,
                'compressed': compressed_id,
            },
        )

    @async_to_deferred
    async def test_raw_compress_big_chunk(self):
        fake_raw_compressor = mock.Mock(spec=compression.CompressorInterface)
        fake_raw_compressor.read = lambda d: d
        self.db.master.config.logCompressionMethod = "raw"
        await self._test_compress_big_chunk(fake_raw_compressor, 0)

    @async_to_deferred
    async def test_gz_compress_big_chunk(self):
        self.db.master.config.logCompressionMethod = "gz"
        await self._test_compress_big_chunk(compression.GZipCompressor, 1)

    @async_to_deferred
    async def test_bz2_compress_big_chunk(self):
        self.db.master.config.logCompressionMethod = "bz2"
        await self._test_compress_big_chunk(compression.BZipCompressor, 2)

    @async_to_deferred
    async def test_lz4_compress_big_chunk(self):
        try:
            import lz4  # noqa: F401
        except ImportError as e:
            raise unittest.SkipTest("lz4 not installed, skip the test") from e

        self.db.master.config.logCompressionMethod = "lz4"
        await self._test_compress_big_chunk(compression.LZ4Compressor, 3)

    @async_to_deferred
    async def test_zstd_compress_big_chunk(self):
        try:
            import zstandard  # noqa: F401
        except ImportError as e:
            raise unittest.SkipTest("zstandard not installed, skip the test") from e

        self.db.master.config.logCompressionMethod = "zstd"
        await self._test_compress_big_chunk(compression.ZStdCompressor, 4)

    @async_to_deferred
    async def test_br_compress_big_chunk(self):
        try:
            import brotli  # noqa: F401
        except ImportError as e:
            raise unittest.SkipTest("brotli not installed, skip the test") from e

        self.db.master.config.logCompressionMethod = "br"
        await self._test_compress_big_chunk(compression.BrotliCompressor, 5)

    @defer.inlineCallbacks
    def do_addLogLines_huge_log(self, NUM_CHUNKS=3000, chunk=('xy' * 70 + '\n') * 3):
        if chunk.endswith("\n"):
            chunk = chunk[:-1]
        linesperchunk = chunk.count("\n") + 1
        test_data = [
            fakedb.LogChunk(
                logid=201,
                first_line=i * linesperchunk,
                last_line=i * linesperchunk + linesperchunk - 1,
                compressed=0,
                content=chunk,
            )
            for i in range(NUM_CHUNKS)
        ]
        yield self.db.insert_test_data([
            *self.backgroundData,
            fakedb.Log(
                id=201,
                stepid=101,
                name="stdio",
                slug="stdio",
                complete=0,
                num_lines=NUM_CHUNKS * 3,
                type="s",
            ),
            *test_data,
        ])
        wholeLog = yield self.db.logs.getLogLines(201, 0, NUM_CHUNKS * 3)
        for i in range(10):
            yield self.db.logs.compressLog(201)
            wholeLog2 = yield self.db.logs.getLogLines(201, 0, NUM_CHUNKS * 3)
            self.assertEqual(wholeLog, wholeLog2)
        self.assertEqual(wholeLog, wholeLog2)

        def countChunk(conn):
            tbl = self.db.model.logchunks
            q = sa.select(sa.func.count(tbl.c.content))
            q = q.where(tbl.c.logid == 201)
            return conn.execute(q).fetchone()[0]

        chunks = yield self.db.pool.do(countChunk)
        # make sure MAX_CHUNK_LINES is taken in account
        self.assertGreaterEqual(
            chunks, NUM_CHUNKS * linesperchunk / logs.LogsConnectorComponent.MAX_CHUNK_LINES
        )

    def test_addLogLines_huge_log(self):
        return self.do_addLogLines_huge_log()

    def test_addLogLines_huge_log_lots_line(self):
        return self.do_addLogLines_huge_log(NUM_CHUNKS=3000, chunk='x\n' * 50)

    def test_addLogLines_huge_log_lots_snowmans(self):
        return self.do_addLogLines_huge_log(NUM_CHUNKS=3000, chunk='\N{SNOWMAN}\n' * 50)

    @defer.inlineCallbacks
    def test_compressLog_non_existing_log(self):
        yield self.db.logs.compressLog(201)
        logdict = yield self.db.logs.getLog(201)
        self.assertEqual(logdict, None)

    @defer.inlineCallbacks
    def test_compressLog_empty_log(self):
        yield self.db.insert_test_data([
            *self.backgroundData,
            fakedb.Log(
                id=201, stepid=101, name="stdio", slug="stdio", complete=1, num_lines=0, type="s"
            ),
        ])
        yield self.db.logs.compressLog(201)
        logdict = yield self.db.logs.getLog(201)
        self.assertEqual(
            logdict,
            logs.LogModel(
                stepid=101,
                num_lines=0,
                name='stdio',
                id=201,
                type='s',
                slug='stdio',
                complete=True,
            ),
        )

    @defer.inlineCallbacks
    def test_deleteOldLogChunks_basic(self):
        yield self.db.insert_test_data(self.backgroundData)
        logids = []
        for stepid in (101, 102):
            for i in range(stepid):
                logid = yield self.db.logs.addLog(
                    stepid=stepid, name='another' + str(i), slug='another' + str(i), type='s'
                )
                yield self.db.logs.appendLog(logid, 'xyz\n')
                logids.append(logid)

        deleted_chunks = yield self.db.logs.deleteOldLogChunks(
            (self.TIMESTAMP_STEP102 + self.TIMESTAMP_STEP101) / 2
        )
        self.assertEqual(deleted_chunks, 101)
        deleted_chunks = yield self.db.logs.deleteOldLogChunks(
            self.TIMESTAMP_STEP102 + self.TIMESTAMP_STEP101
        )
        self.assertEqual(deleted_chunks, 102)
        deleted_chunks = yield self.db.logs.deleteOldLogChunks(
            self.TIMESTAMP_STEP102 + self.TIMESTAMP_STEP101
        )
        self.assertEqual(deleted_chunks, 0)
        deleted_chunks = yield self.db.logs.deleteOldLogChunks(0)
        self.assertEqual(deleted_chunks, 0)
        for logid in logids:
            logdict = yield self.db.logs.getLog(logid)
            self.assertEqual(logdict.type, 'd')

            # we make sure we can still getLogLines, it will just return empty value
            lines = yield self.db.logs.getLogLines(logid, 0, logdict.num_lines)
            self.assertEqual(lines, '')

    @async_to_deferred
    async def test_insert_logs_non_existing_compression_method(self):
        LOG_ID = 201
        await self.db.insert_test_data([
            *self.backgroundData,
            fakedb.Log(
                id=LOG_ID, stepid=101, name="stdio", slug="stdio", complete=0, num_lines=1, type="s"
            ),
            fakedb.LogChunk(
                logid=LOG_ID, first_line=0, last_line=0, compressed=0, content=b"fake_log_chunk\n"
            ),
        ])

        def _thd_get_log_chunks(conn):
            res = conn.execute(
                self.db.model.logchunks.select().where(self.db.model.logchunks.c.logid == LOG_ID)
            ).mappings()
            return [dict(row) for row in res]

        self.db.master.config.logCompressionMethod = "non_existing"
        await self.db.logs.compressLog(LOG_ID)

        self.assertEqual(
            await self.db.pool.do(_thd_get_log_chunks),
            [
                {
                    'compressed': 0,
                    'content': b'fake_log_chunk\n',
                    'first_line': 0,
                    'last_line': 0,
                    'logid': LOG_ID,
                }
            ],
        )

        await self.db.logs.appendLog(LOG_ID, 'other_chunk\n')

        self.assertEqual(
            await self.db.pool.do(_thd_get_log_chunks),
            [
                {
                    'compressed': 0,
                    'content': b'fake_log_chunk\n',
                    'first_line': 0,
                    'last_line': 0,
                    'logid': LOG_ID,
                },
                {
                    'compressed': 0,
                    'content': b'other_chunk',
                    'first_line': 1,
                    'last_line': 1,
                    'logid': LOG_ID,
                },
            ],
        )

    @async_to_deferred
    async def test_get_logs_non_existing_compression_method(self):
        LOG_ID = 201

        # register fake compressor
        FAKE_COMPRESSOR_ID = max(self.db.logs.COMPRESSION_BYID.keys()) + 1
        self.db.logs.COMPRESSION_BYID[FAKE_COMPRESSOR_ID] = FakeUnavailableCompressor
        NON_EXISTING_COMPRESSOR_ID = max(self.db.logs.COMPRESSION_BYID.keys()) + 1

        await self.db.insert_test_data([
            *self.backgroundData,
            fakedb.Log(
                id=LOG_ID, stepid=101, name="stdio", slug="stdio", complete=0, num_lines=1, type="s"
            ),
            fakedb.LogChunk(
                logid=LOG_ID,
                first_line=0,
                last_line=0,
                compressed=FAKE_COMPRESSOR_ID,
                content=b"fake_log_chunk\n",
            ),
            fakedb.LogChunk(
                logid=LOG_ID,
                first_line=1,
                last_line=1,
                compressed=NON_EXISTING_COMPRESSOR_ID,
                content=b"fake_log_chunk\n",
            ),
        ])

        with self.assertRaises(logs.LogCompressionFormatUnavailableError):
            await self.db.logs.getLogLines(logid=LOG_ID, first_line=0, last_line=0)

        with self.assertRaises(logs.LogCompressionFormatUnavailableError):
            await self.db.logs.getLogLines(logid=LOG_ID, first_line=1, last_line=1)
        self.flushLoggedErrors(logs.LogCompressionFormatUnavailableError)
