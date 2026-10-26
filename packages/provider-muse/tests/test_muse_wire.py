"""The tunnel's wire: the Noise_XX handshake against a real responder, the frames both ways, and the tunnel's calls
(chunked bodies, split answers, resets, a broken socket, the subscription's events)."""

import asyncio
import json

import pytest
from cryptography.exceptions import InvalidTag
from musekit import VM, Responder, response_payload, vm_routes

from relay_core.testing.mutation import mutation
from relay_muse.engine import noise
from relay_muse.engine.tunnel import Tunnel, TunnelError

NOISE_FILE = "packages/provider-muse/src/relay_muse/engine/noise.py"
TUNNEL_FILE = "packages/provider-muse/src/relay_muse/engine/tunnel.py"


def run(coro):
    return asyncio.run(coro)


@mutation(NOISE_FILE, "        self.state.mix_key(dh(self.s, self.re))",
          "        self.state.mix_key(dh(self.e, self.re))", expect="InvalidTag")
def test_the_handshake_agrees_on_the_transport_keys_with_a_responder():
    client, server = noise.Initiator(), Responder()
    assert server.read1(client.message1(b"\x0a\x20" + b"n" * 32)) == b"\x0a\x20" + b"n" * 32
    assert client.read2(server.message2()) == b"attestation"
    last, send, receive = client.message3(b"")
    to_client, from_client = server.read3(last)
    for i in range(3):                      # the counters move together
        assert from_client.decrypt(b"", send.encrypt(b"", b"ping %d" % i)) == b"ping %d" % i
        assert receive.decrypt(b"", to_client.encrypt(b"", b"pong %d" % i)) == b"pong %d" % i


@mutation(NOISE_FILE, '        nonce = b"\\0\\0\\0\\0" + struct.pack(">Q", self.n)',
          '        nonce = b"\\0\\0\\0\\0" + struct.pack("<Q", self.n)', expect="big-endian counter")
def test_the_nonce_is_four_zero_bytes_and_a_big_endian_counter():
    cipher = noise.Cipher(b"k" * 32)
    assert [cipher._nonce() for _ in range(2)] == [b"\0" * 12, b"\0" * 11 + b"\x01"], "big-endian counter"


def test_a_forged_second_message_is_refused():
    client, server = noise.Initiator(), Responder()
    server.read1(client.message1(b""))
    m2 = bytearray(server.message2())
    m2[40] ^= 1
    with pytest.raises(InvalidTag):
        client.read2(bytes(m2))
    with pytest.raises(ValueError):
        noise.Initiator().read2(b"short")


@mutation(NOISE_FILE, "    total = max(1, -(-len(payload) // size))", "    total = max(1, len(payload) // size)",
          expect="the whole payload")
def test_a_payload_over_48_kb_is_cut_into_chunks_and_put_back_together():
    payload = noise.request_payload(7, "POST", "/chat/stream", [("content-type", "application/json")],
                                    b"x" * 120_000, noise.SENTINEL)
    frames = noise.chunks(3, payload)
    joined = b"".join(noise.parse(f)[4][0] for f in frames)
    assert joined == payload, "the whole payload"
    assert len(frames) == 3 and all(len(noise.parse(f)[4][0]) <= noise.CHUNK_BYTES for f in frames)
    assert {noise.parse(f)[3][0] for f in frames} == {3} and [noise.parse(f)[2][0] for f in frames] == [0, 1, 2]
    sr = noise.parse(joined)
    assert sr[1] == [noise.SENTINEL]
    frame = noise.parse(sr[2][0])
    request = noise.parse(frame[2][0])
    assert frame[1] == [7] and request[1] == [b"POST"] and request[2] == [b"/chat/stream"] and request[5] == [1]
    assert len(request[4][0]) == 120_000
    assert 1 not in noise.parse(noise.request_payload(1, "GET", "/x", [], b"")), "DAEMON is the default, omitted"


def test_server_frames_are_read_back_whole():
    parts = noise.Reassembler()
    big = b"y" * 100_000
    frames = noise.chunks(9, response_payload(4, 200, big))
    assert [parts.feed(f) for f in frames[:-1]] == [None] * (len(frames) - 1)
    part = parts.feed(frames[-1])
    assert (part.stream_id, part.kind, part.status, part.end, len(part.data)) == (4, "response", 200, True, 100_000)
    with pytest.raises(ValueError):
        noise.parse(b"\x0a\x05ab")


async def _tunnel(routes) -> tuple[Tunnel, VM]:
    vm = VM(routes)
    return await Tunnel.open(vm, 2.0), vm


@mutation(TUNNEL_FILE, "        if part.end:\n            self._done[sid]",
          "        if True:\n            self._done[sid]", expect="split body")
def test_calls_answer_whole_bodies_and_the_subscription_keeps_its_events():
    async def go():
        routes = vm_routes(overrides={"/split": ("split", 200, b"0123456789abcdefghij"),
                                      "/big": (200, b"z" * 200_000)})
        tunnel, vm = await _tunnel(routes)
        assert (await tunnel.call("POST", "/client/register-capabilities", {"a": 1})).status == 200
        assert vm.requests[0].json() == {"a": 1}
        assert vm.requests[0].headers["content-type"] == "application/json"
        assert await tunnel.subscribe({"after_stream_seq": 0}) == 200
        vm.push({"event": "approvals.snapshot", "payload": {"approvals": []}})
        vm.push({"event": "agent.status", "payload": {"activity_code": "working"}})
        reply = await tunnel.call("GET", "/split")
        assert reply == (200, b"0123456789abcdefghij"), "split body"
        assert len((await tunnel.call("GET", "/big")).body) == 200_000
        assert [e["event"] for e in tunnel.events] == ["approvals.snapshot", "agent.status"]
        await tunnel.close()
        assert vm.closed
    run(go())


def test_a_reset_a_silence_and_a_broken_socket_are_tunnel_errors():
    async def go():
        routes = vm_routes(overrides={"/reset": ("reset", "no route"), "/silent": "silent"})
        tunnel, vm = await _tunnel(routes)
        with pytest.raises(TunnelError, match="reset: 5 no route"):
            await tunnel.call("GET", "/reset")
        with pytest.raises(TunnelError, match="no whole answer"):
            await tunnel.call("GET", "/silent", timeout=0.2)
        vm.broken()
        with pytest.raises(TunnelError, match="closed"):
            await tunnel.call("GET", "/chat/history?x", timeout=2)
    run(go())


CLOSE_ON_FAIL = "            await _close(ws, timeout)      # never left open behind a failed open\n"
BOUNDED_SEND = "                await asyncio.wait_for(self.ws.send(self._tx.encrypt(b\"\", frame)), self.io_timeout)"


@mutation(TUNNEL_FILE, CLOSE_ON_FAIL, "", expect="left open")
def test_a_handshake_with_no_answer_or_a_bad_answer_fails_and_closes_the_socket():
    class Mute:
        closed = False

        async def send(self, data):
            pass

        async def recv(self):
            await asyncio.sleep(5)

        async def close(self):
            self.closed = True

    class Liar(Mute):
        async def recv(self):
            return b"\0" * 96

    class Stuck(Mute):
        async def send(self, data):          # a send that never ends is cut by the handshake's bound
            await asyncio.sleep(5)

    for sock, why in ((Mute(), "no handshake within"), (Liar(), "handshake failed"), (Stuck(), "no handshake within")):
        with pytest.raises(TunnelError, match=why):
            run(Tunnel.open(sock, 0.1))
        assert sock.closed, "the socket was left open after a failed open"


@mutation(TUNNEL_FILE, BOUNDED_SEND, "                await self.ws.send(self._tx.encrypt(b\"\", frame))",
          expect="TimeoutError")
def test_a_send_that_hangs_is_cut_and_a_close_that_hangs_too():
    async def go():
        tunnel, vm = await _tunnel(vm_routes())
        tunnel.io_timeout = 0.1

        async def hang(data=None):
            await asyncio.sleep(5)
        vm.send = hang
        with pytest.raises(TunnelError, match="could not be sent within"):
            await asyncio.wait_for(tunnel.call("GET", "/x", timeout=10), 2)
        vm.close = hang
        await asyncio.wait_for(tunnel.close(), 1)
    run(go())


def test_a_reply_that_is_not_json_reads_as_none():
    from relay_muse.engine.tunnel import Reply
    assert Reply(200, b"<html>").json() is None and Reply(200, b"").json() is None
    assert Reply(200, json.dumps({"a": 1}).encode()).json() == {"a": 1}
