"""The Turnstile dx solver ported from aurora: register keys, the XOR, the opcodes, the recorded program."""

import base64
import json

import pytest
from gptkit import fixture

from relay_core.testing.mutation import mutation
from relay_gpt.engine.turnstile import MAX_STEPS, Profile, TurnstileError, _Solver, reg_key, solve_dx, xor_string

TS_FILE = "packages/provider-gpt/src/relay_gpt/engine/turnstile.py"
REG_FLOAT_LINE = '        return "n:" + repr(value)'
REG_FLOAT_TRUNC = '        return "n:%d" % value'
XOR_LINE = '    return "".join(chr((ord(c) & 0xFF) ^ k[i % len(k)]) for i, c in enumerate(data))'
XOR_NO_KEY = '    return "".join(chr(ord(c) & 0xFF) for i, c in enumerate(data))'
KINDS = {"bad requirements token", "bad program", "rejected", "unresolved", "step overflow"}
NOW = 1_793_809_812.0


def requirements(fields: list | None = None) -> str:
    fields = fields or [3000, "", "", "", "UA", "", "", "en-US", "en-US,en", 0, "", "", "", 1000.5, "sid", "", 8, 1.0]
    return "gAAAAAC" + base64.b64encode(json.dumps(fields).encode()).decode() + "~S"


def program(queue: list, token: str) -> str:
    return base64.b64encode(xor_string(json.dumps(queue), token).encode("latin-1")).decode()


def test_max_steps_is_aurora_s():
    assert MAX_STEPS == 50_000


@mutation(TS_FILE, REG_FLOAT_LINE, REG_FLOAT_TRUNC, expect="float registers must stay distinct")
def test_reg_key_keeps_float_registers_distinct():
    assert reg_key(6.21) != reg_key(6) and reg_key(6.0) == reg_key(6), "float registers must stay distinct"
    assert reg_key("6") != reg_key(6) and reg_key(None) == "nil"


@mutation(TS_FILE, XOR_LINE, XOR_NO_KEY, expect="xor round trip")
def test_xor_string_round_trips_with_a_cycled_key():
    data = "héllo \x00\xff world"
    ok = xor_string(xor_string(data, "key"), "key") == data and xor_string("abab", "ab") == "\x00" * 4
    assert ok, "xor round trip"
    assert xor_string(data, "") == data


CALL = 2.0  # opcode 2 (set a register) is the function the guarded calls invoke: [.., 2, 70, "hit"]


@pytest.mark.parametrize("op, regs, args, expect", [
    (5, {40: 2.0, 41: 3.0}, [40, 41], {40: 5.0}),
    (5, {40: "a", 41: 1.0}, [40, 41], {40: "a1"}),
    (5, {40: [1.0], 41: "x"}, [40, 41], {40: [1.0, "x"]}),
    (6, {41: {"k": "v"}, 42: "k"}, [40, 41, 42], {40: "v"}),
    (6, {41: [7.0, 8.0], 42: 1.0}, [40, 41, 42], {40: 8.0}),
    (6, {41: "abc", 42: "length"}, [40, 41, 42], {40: 3.0}),
    (8, {41: "x"}, [40, 41], {40: "x"}),
    (8, {6.21: "float", 6: "int"}, [40, 6.21], {40: "float"}),
    (34, {41: "y"}, [40, 41], {40: "y"}),
    (27, {40: [1.0, 2.0, 1.0], 41: 1.0}, [40, 41], {40: [2.0]}),
    (27, {40: 5.0, 41: 2.0}, [40, 41], {40: 3.0}),
    (29, {41: 1.0, 42: 2.0}, [40, 41, 42], {40: True}),
    (33, {41: 2.0, 42: 3.0}, [40, 41, 42], {40: 6.0}),
    (35, {41: 1.0, 42: 0.0}, [40, 41, 42], {40: 0.0}),
    (35, {41: 6.0, 42: 4.0}, [40, 41, 42], {40: 1.5}),
    (18, {40: "aGk="}, [40], {40: "hi"}),
    (19, {40: "hi"}, [40], {40: "aGk="}),
    (20, {40: "a", 41: "a"}, [40, 41, CALL, 70, "hit"], {70: "hit"}),
    (20, {40: "a", 41: 1.0}, [40, 41, CALL, 70, "hit"], {70: None}),
    (21, {40: 1.0, 41: 5.0, 42: 2.0}, [40, 41, 42, CALL, 70, "hit"], {70: "hit"}),
    (21, {40: 1.0, 41: 2.0, 42: 2.0}, [40, 41, 42, CALL, 70, "hit"], {70: None}),
    (23, {40: None}, [40, CALL, 70, "hit"], {70: None}),
    (23, {40: 1.0}, [40, CALL, 70, "hit"], {70: "hit"}),
])
def test_opcode_semantics_match_aurora(op, regs, args, expect):
    solver = _Solver(Profile(), clock=lambda: NOW, rand=lambda: 0.5)
    for reg, value in regs.items():
        solver._set(reg, value)
    solver._get(op)(*args)
    assert {reg: solver._get(reg) for reg in expect} == expect


def test_a_simple_program_resolves_and_rejects():
    token = requirements()
    ok = solve_dx(token, program([[2, 40, "answer"], [7, 3, 40]], token), clock=lambda: NOW, rand=lambda: 0.5)
    assert base64.b64decode(ok) == b"answer"
    with pytest.raises(TurnstileError) as e:
        solve_dx(token, program([[2, 40, "no"], [7, 4, 40]], token))
    assert str(e.value) == "rejected"
    with pytest.raises(TurnstileError) as e:
        solve_dx(token, program([[2, 40, "nothing"]], token))
    assert str(e.value) == "unresolved"


def test_the_recorded_program_resolves():
    rec = fixture("turnstile_dx")
    token = solve_dx(rec["p"], rec["dx"], clock=lambda: NOW, rand=lambda: 0.5)
    assert base64.b64decode(token) and abs(len(token) - rec["go_token_len"]) <= rec["go_token_len"] * 0.15


def test_frozen_clock_and_rand_give_the_same_token():
    rec = fixture("turnstile_dx")
    one = solve_dx(rec["p"], rec["dx"], clock=lambda: NOW, rand=lambda: 0.25)
    assert one == solve_dx(rec["p"], rec["dx"], clock=lambda: NOW, rand=lambda: 0.25)


def test_a_dx_for_another_token_raises_a_fixed_kind():
    rec = fixture("turnstile_dx")
    with pytest.raises(TurnstileError) as e:
        solve_dx(requirements(), rec["dx"])
    assert str(e.value) in KINDS and str(e.value) == "bad program"
    for bad in ("", "gAAAAAC~S", "gAAAAACnot-base64~S", requirements([1, 2, 3])):
        with pytest.raises(TurnstileError) as e:
            solve_dx(bad, rec["dx"])
        assert str(e.value) == "bad requirements token"


def test_a_looping_program_stops_at_max_steps():
    token = requirements()
    loop = program([[2, 50, [[8, 9, 50]]], [8, 9, 50]], token)  # register 9 (the queue) re-loads itself
    with pytest.raises(TurnstileError) as e:
        solve_dx(token, loop, max_steps=1000)
    assert str(e.value) == "step overflow"
