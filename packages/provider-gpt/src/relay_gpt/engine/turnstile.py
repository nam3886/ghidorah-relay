"""The sentinel's Turnstile `dx` solver: a register VM that runs the program chatgpt.com sends in
`chat-requirements/prepare` and returns the answer finalize takes as `turnstile_token`. Pure and CPU-bound
(the submit calls it through `asyncio.to_thread`); no network, no `eval`, stdlib only.

Ported line by line from aurora-develop/aurora, commit cbfbabc9c14c7278e4dd91cf8807b2bf500860b1,
`internal/turnstile/turnstile.go` (the browser fingerprint it reads from `internal/browserfp` is fixed here to
one of that package's Win32 profiles). Values keep the Go shapes: numbers are floats, JS objects are dicts that
carry their key order under `__ordered_keys__`, a Go `[]string` is a tuple. Changes from the Go code: the clock
and `Math.random` are injected (`clock`, `rand`), a step overflow and every other failure raise `TurnstileError`
with a fixed kind (never program text), and a function turns into a fixed text where Go prints its address.

aurora's licence:

MIT License

Copyright (c) 2024 aurora-develop

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

MAX_STEPS = 50_000

QUEUE_REG = 9
WINDOW_REG = 10
KEY_REG = 16
SUCCESS_REG = 3
ERROR_REG = 4
CALLBACK_REG = 30
ORDER = "__ordered_keys__"
PROTO = "__prototype__"
STORAGE_DATA = "__storage_data__"
STORAGE_KEYS = "__storage_keys__"
_STORAGE_OWN = frozenset({STORAGE_DATA, STORAGE_KEYS, "length", "key", "getItem", "setItem", "removeItem", "clear"})
_META = frozenset({ORDER, PROTO, STORAGE_DATA, STORAGE_KEYS})
_OPCODES = (0, 1, 2, 5, 6, 7, 8, 11, 12, 13, 14, 15, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 33, 34, 35)
_FUNCTION_TEXT = "function () { [native code] }"  # Go prints a func's address here

# browserfp's Win32 profile, fixed (aurora picks one at random per process)
WEBGL_VENDOR = "Google Inc. (AMD)"
WEBGL_RENDERER = "ANGLE (AMD, AMD Radeon RX 6600 (0x000073FF) Direct3D11 vs_5_0 ps_5_0, D3D11)"
DEVICE_PIXEL_RATIO = 1.0

WINDOW_KEY_ORDER = (
    "0", "window", "self", "document", "name", "location", "customElements", "history", "navigation", "locationbar",
    "menubar", "personalbar", "scrollbars", "statusbar", "toolbar", "status", "closed", "frames", "length", "top",
    "opener", "parent", "frameElement", "navigator", "origin", "external", "screen", "innerWidth", "innerHeight",
    "scrollX", "pageXOffset", "scrollY", "pageYOffset", "visualViewport", "screenX", "screenY", "outerWidth",
    "outerHeight", "devicePixelRatio", "event", "clientInformation", "screenLeft", "screenTop", "styleMedia",
    "onsearch", "trustedTypes", "performance", "onappinstalled", "onbeforeinstallprompt", "crypto", "indexedDB",
    "sessionStorage", "localStorage", "onbeforexrselect", "onabort", "onbeforeinput", "onbeforematch",
    "onbeforetoggle", "onblur", "oncancel", "oncanplay", "oncanplaythrough", "onchange", "onclick", "onclose",
    "oncommand", "oncontentvisibilityautostatechange", "oncontextlost", "oncontextmenu", "oncontextrestored",
    "oncuechange", "ondblclick", "ondrag", "ondragend", "ondragenter", "ondragleave", "ondragover", "ondragstart",
    "ondrop", "ondurationchange", "onemptied", "onended", "onerror", "onfocus", "onformdata", "oninput", "oninvalid",
    "onkeydown", "onkeypress", "onkeyup", "onload", "onloadeddata", "onloadedmetadata", "onloadstart", "onmousedown",
    "onmouseenter", "onmouseleave", "onmousemove", "onmouseout", "onmouseover", "onmouseup", "onmousewheel",
    "onpause", "onplay", "onplaying", "onprogress", "onratechange", "onreset", "onresize", "onscroll", "onscrollend",
    "onsecuritypolicyviolation", "onseeked", "onseeking", "onselect", "onslotchange", "onstalled", "onsubmit",
    "onsuspend", "ontimeupdate", "ontoggle", "onvolumechange", "onwaiting", "onwebkitanimationend",
    "onwebkitanimationiteration", "onwebkitanimationstart", "onwebkittransitionend", "onwheel", "onauxclick",
    "ongotpointercapture", "onlostpointercapture", "onpointerdown", "onpointermove", "onpointerup", "onpointercancel",
    "onpointerover", "onpointerout", "onpointerenter", "onpointerleave", "onselectstart", "onselectionchange",
    "onanimationend", "onanimationiteration", "onanimationstart", "ontransitionrun", "ontransitionstart",
    "ontransitionend", "ontransitioncancel", "onafterprint", "onbeforeprint", "onbeforeunload", "onhashchange",
    "onlanguagechange", "onmessage", "onmessageerror", "onoffline", "ononline", "onpagehide", "onpageshow",
    "onpopstate", "onrejectionhandled", "onstorage", "onunhandledrejection", "onunload", "isSecureContext",
    "crossOriginIsolated", "scheduler", "alert", "atob", "blur", "btoa", "cancelAnimationFrame", "cancelIdleCallback",
    "captureEvents", "clearInterval", "clearTimeout", "close", "confirm", "createImageBitmap", "fetch", "find",
    "focus", "getComputedStyle", "getSelection", "matchMedia", "moveBy", "moveTo", "open", "postMessage", "print",
    "prompt", "queueMicrotask", "releaseEvents", "reportError", "requestAnimationFrame", "requestIdleCallback",
    "resizeBy", "resizeTo", "scroll", "scrollBy", "scrollTo", "setInterval", "setTimeout", "stop", "structuredClone",
    "webkitCancelAnimationFrame", "webkitRequestAnimationFrame", "chrome", "caches", "cookieStore", "ondevicemotion",
    "ondeviceorientation", "ondeviceorientationabsolute", "onpointerrawupdate", "documentPictureInPicture",
    "sharedStorage", "fetchLater", "getScreenDetails", "queryLocalFonts", "showDirectoryPicker", "showOpenFilePicker",
    "showSaveFilePicker", "originAgentCluster", "viewport", "onpageswap", "onpagereveal", "credentialless", "fence",
    "launchQueue", "speechSynthesis", "onscrollsnapchange", "onscrollsnapchanging", "webkitRequestFileSystem",
    "webkitResolveLocalFileSystemURL", "__reactRouterContext", "$RB", "$RV", "$RC", "$RT", "__reactRouterManifest",
    "__STATSIG__", "__reactRouterVersion", "__REACT_INTL_CONTEXT__", "DD_RUM", "__SEGMENT_INSPECTOR__",
    "__reactRouterRouteModules", "__reactRouterDataRouter", "__sentinel_token_pending", "__sentinel_init_pending",
    "SentinelSDK", "rwha4gh7no",
)

NAVIGATOR_PROTOTYPE_KEYS = (
    "vendorSub", "productSub", "vendor", "maxTouchPoints", "scheduling", "userActivation", "geolocation", "doNotTrack",
    "connection", "plugins", "mimeTypes", "pdfViewerEnabled", "webkitTemporaryStorage", "webkitPersistentStorage",
    "hardwareConcurrency", "cookieEnabled", "appCodeName", "appName", "appVersion", "platform", "product", "userAgent",
    "language", "languages", "onLine", "webdriver", "getGamepads", "javaEnabled", "sendBeacon", "vibrate",
    "windowControlsOverlay", "deprecatedRunAdAuctionEnforcesKAnonymity", "protectedAudience", "bluetooth",
    "storageBuckets", "clipboard", "credentials", "keyboard", "managed", "mediaDevices", "storage", "serviceWorker",
    "virtualKeyboard", "wakeLock", "deviceMemory", "userAgentData", "login", "ink", "mediaCapabilities",
    "devicePosture", "hid", "locks", "gpu", "mediaSession", "permissions", "presentation", "serial", "usb", "xr",
    "adAuctionComponents", "runAdAuction", "canLoadAdAuctionFencedFrame", "canShare", "share", "clearAppBadge",
    "getBattery", "getUserMedia", "requestMIDIAccess", "requestMediaKeySystemAccess", "setAppBadge",
    "webkitGetUserMedia",
    "clearOriginJoinedAdInterestGroups", "createAuctionNonce", "joinAdInterestGroup", "leaveAdInterestGroup",
    "updateAdInterestGroups", "deprecatedReplaceInURN", "deprecatedURNToURL", "getInstalledRelatedApps",
    "getInterestGroupAdAuctionData", "registerProtocolHandler", "unregisterProtocolHandler",
)


class TurnstileError(Exception):
    """The solver gave no answer. The text is one fixed kind: "bad requirements token", "bad program", "rejected",
    "unresolved" or "step overflow"; never program text."""


class _VmError(Exception):
    """An error inside the VM (Go's `error`); its text may become program data, as in aurora."""


@dataclass(frozen=True)
class Profile:
    """The fields of the requirements token the window is built from (aurora's turnstileRequirementsProfile)."""

    screen_sum: int = 0
    user_agent: str = ""
    language: str = ""
    languages_join: str = ""
    navigator_probe: str = ""
    document_probe: str = ""
    window_probe: str = ""
    performance_now: float = 0.0
    session_id: str = ""
    hardware_concurrency: int = 0
    time_origin: float = 0.0


def solve_dx(requirements_token: str, dx: str, *, clock: Callable[[], float] = time.time,
             rand: Callable[[], float] = random.random, max_steps: int = MAX_STEPS) -> str:
    """The Turnstile answer for `dx` under `requirements_token` (the `gAAAAAC...~S` sent to prepare)."""
    solver = _Solver(parse_profile(requirements_token), clock=clock, rand=rand, max_steps=max_steps)
    try:
        return solver.solve(requirements_token, dx)
    except TurnstileError:
        raise
    except Exception:  # a program that breaks the port (deep recursion, odd types): no text of it leaves
        raise TurnstileError("bad program") from None


def parse_profile(token: str) -> Profile:
    body = token.strip().removeprefix("gAAAAAC").removesuffix("~S")
    try:
        if not body:
            raise ValueError
        fields = _json_loads(base64.b64decode(body, validate=True))
        if not isinstance(fields, list) or len(fields) < 18:
            raise ValueError
        return Profile(
            screen_sum=int(_json_float(fields[0])), user_agent=_json_str_field(fields[4]),
            language=_json_str_field(fields[7]), languages_join=_json_str_field(fields[8]),
            navigator_probe=_json_str_field(fields[10]), document_probe=_json_str_field(fields[11]),
            window_probe=_json_str_field(fields[12]), performance_now=_json_float(fields[13]),
            session_id=_json_str_field(fields[14]), hardware_concurrency=int(_json_float(fields[16])),
            time_origin=_json_float(fields[17]))
    except (ValueError, TypeError, OverflowError, binascii.Error):
        raise TurnstileError("bad requirements token") from None


class _RegMap:
    """aurora's regMapRef: an object whose properties are the solver's registers."""

    def __init__(self, solver: _Solver):
        self.solver = solver


class _Solver:
    def __init__(self, profile: Profile, *, clock: Callable[[], float] = time.time,
                 rand: Callable[[], float] = random.random, max_steps: int = MAX_STEPS):
        self.profile, self.clock, self.rand, self.max_steps = profile, clock, rand, max_steps
        self.regs: dict[str, Any] = {}
        self.done = False
        self.resolved = ""
        self.rejected = ""
        self.steps = 0
        self.window = self._build_window()
        self._init_runtime()

    # ---- the program --------------------------------------------------------------------------------------------
    def solve(self, requirements_token: str, dx: str) -> str:
        self._set(SUCCESS_REG, self._on_success)
        self._set(ERROR_REG, self._on_error)
        self._set(CALLBACK_REG, self._make_callback)
        self._set(KEY_REG, requirements_token)
        try:
            queue = _json_loads(xor_string(_b64_latin1_decode(dx), requirements_token))
        except (ValueError, _VmError):
            raise TurnstileError("bad program") from None
        if not isinstance(queue, list):
            raise TurnstileError("bad program")
        self._set(QUEUE_REG, queue)
        try:
            self._run_queue()
        except _VmError as e:
            if self.steps > self.max_steps:
                raise TurnstileError("step overflow") from None
            if not self.done:
                success = self._get(SUCCESS_REG)
                if callable(success):
                    success(f"{self.steps}: {e}")
        if self.rejected:
            raise TurnstileError("rejected")
        if not self.resolved:
            raise TurnstileError("unresolved")
        return self.resolved

    def _on_success(self, *args):
        if not self.done:
            self.done = True
            self.resolved = _b64_latin1_encode(_js_str(args[0] if args else None))

    def _on_error(self, *args):
        if not self.done:
            self.done = True
            self.rejected = _b64_latin1_encode(_js_str(args[0] if args else None))

    def _make_callback(self, *args):
        if len(args) < 3:
            return None
        target, ret = args[0], args[1]
        inner = args[2] if isinstance(args[2], list) else None
        mapped: list = []
        if len(args) >= 4:
            if isinstance(args[2], list):
                mapped = args[2]
            if isinstance(args[3], list):
                inner = args[3]

        def call(*call_args):
            if self.done:
                return None
            previous = self._copy_queue()
            for i, reg in enumerate(mapped):
                self._set(reg, call_args[i] if i < len(call_args) else None)
            self._set(QUEUE_REG, list(inner or ()))
            try:
                self._run_queue()
            except _VmError as e:
                self._set(QUEUE_REG, previous)
                return str(e)
            self._set(QUEUE_REG, previous)
            return self._get(ret)

        self._set(target, call)
        return None

    def _run_queue(self) -> None:
        while not self.done:
            queue = self._get(QUEUE_REG)
            if not isinstance(queue, list) or not queue:
                return
            ins = queue[0]
            self._set(QUEUE_REG, queue[1:])
            if not isinstance(ins, list) or not ins:
                continue
            fn = self._get(ins[0])
            if not callable(fn):
                raise _VmError("vm opcode not callable: " + _js_str(ins[0]))
            fn(*ins[1:])
            self.steps += 1
            if self.steps > self.max_steps:
                raise _VmError("turnstile vm step overflow")

    def _call(self, value, *args):
        return value(*args) if callable(value) else None

    def _deref(self, args) -> list:
        return [self._get(a) for a in args]

    def _get(self, key):
        return self.regs.get(reg_key(key))

    def _set(self, key, value) -> None:
        self.regs[reg_key(key)] = value

    def _copy_queue(self) -> list:
        queue = self._get(QUEUE_REG)
        return list(queue) if isinstance(queue, list) else []

    # ---- opcodes ------------------------------------------------------------------------------------------------
    def _init_runtime(self) -> None:
        for n in _OPCODES:
            self._set(float(n), getattr(self, f"_op_{n}"))
        self._set(WINDOW_REG, self.window)

    def _op_0(self, *a):
        if not a:
            return None
        try:
            return solve_dx(_js_str(self._get(KEY_REG)), _js_str(a[0]), clock=self.clock, rand=self.rand,
                            max_steps=self.max_steps)
        except TurnstileError as e:
            raise _VmError(str(e)) from None

    def _op_1(self, *a):
        if len(a) >= 2:
            self._set(a[0], xor_string(_js_str(self._get(a[0])), _js_str(self._get(a[1]))))

    def _op_2(self, *a):
        if len(a) >= 2:
            self._set(a[0], a[1])

    def _op_5(self, *a):
        if len(a) < 2:
            return
        left, right = self._get(a[0]), self._get(a[1])
        if isinstance(left, list):
            self._set(a[0], [*left, right])
            return
        ln, lok = _as_number(left)
        rn, rok = _as_number(right)
        self._set(a[0], ln + rn if lok and rok else _js_str(left) + _js_str(right))

    def _op_6(self, *a):
        if len(a) >= 3:
            self._set(a[0], _get_prop(self._get(a[1]), self._get(a[2])))

    def _op_7(self, *a):
        if a:
            self._call(self._get(a[0]), *self._deref(a[1:]))

    def _op_8(self, *a):
        if len(a) >= 2:
            self._set(a[0], self._get(a[1]))

    def _op_11(self, *a):
        if len(a) < 2:
            return
        try:
            rx = re.compile(_js_str(self._get(a[1])))
        except (re.error, RecursionError, OverflowError):
            self._set(a[0], None)
            return
        scripts = _get_prop(_get_prop(self.window, "document"), "scripts")
        for item in scripts if isinstance(scripts, list) else ():
            src = _js_str(_get_prop(item, "src"))
            if not src:
                continue
            hit = rx.search(src)
            if hit and hit.group(0):
                self._set(a[0], hit.group(0))
                return
        self._set(a[0], None)

    def _op_12(self, *a):
        if a:
            self._set(a[0], _RegMap(self))

    def _op_13(self, *a):
        if len(a) < 2:
            return
        try:
            self._call(self._get(a[1]), *a[2:])
        except _VmError as e:
            self._set(a[0], str(e))

    def _op_14(self, *a):
        if len(a) >= 2:
            try:
                self._set(a[0], _json_loads(_js_str(self._get(a[1]))))
            except ValueError as e:
                raise _VmError("invalid JSON") from e

    def _op_15(self, *a):
        if len(a) >= 2:
            self._set(a[0], _js_json(self._get(a[1])))

    def _op_17(self, *a):
        if len(a) < 2:
            return
        try:
            self._set(a[0], self._call(self._get(a[1]), *self._deref(a[2:])))
        except _VmError as e:
            self._set(a[0], str(e))

    def _op_18(self, *a):
        if a:
            self._set(a[0], _b64_latin1_decode(_js_str(self._get(a[0]))))

    def _op_19(self, *a):
        if a:
            self._set(a[0], _b64_latin1_encode(_js_str(self._get(a[0]))))

    def _op_20(self, *a):
        if len(a) >= 3 and _equal(self._get(a[0]), self._get(a[1])):
            self._call(self._get(a[2]), *a[3:])

    def _op_21(self, *a):
        if len(a) < 4:
            return
        left, right, threshold = (_as_number(self._get(r))[0] for r in a[:3])
        if abs(left - right) > threshold:
            self._call(self._get(a[3]), *a[4:])

    def _op_22(self, *a):
        if len(a) < 2:
            return
        previous = self._copy_queue()
        self._set(QUEUE_REG, list(a[1]) if isinstance(a[1], list) else [])
        try:
            self._run_queue()
        except _VmError as e:
            self._set(QUEUE_REG, previous)
            self._set(a[0], str(e))
            return
        self._set(QUEUE_REG, previous)

    def _op_23(self, *a):
        if len(a) >= 2 and self._get(a[0]) is not None:
            self._call(self._get(a[1]), *a[2:])

    def _op_24(self, *a):
        if len(a) >= 3:
            method = _get_prop(self._get(a[1]), self._get(a[2]))
            self._set(a[0], method if callable(method) else None)

    def _op_25(self, *a):
        return None

    def _op_26(self, *a):
        return None

    def _op_27(self, *a):
        if len(a) < 2:
            return
        left, right = self._get(a[0]), self._get(a[1])
        if isinstance(left, list):
            self._set(a[0], [item for item in left if not _equal(item, right)])
            return
        ln, lok = _as_number(left)
        rn, rok = _as_number(right)
        if lok and rok:
            self._set(a[0], ln - rn)

    def _op_28(self, *a):
        return None

    def _op_29(self, *a):
        if len(a) >= 3:
            self._set(a[0], _as_number(self._get(a[1]))[0] < _as_number(self._get(a[2]))[0])

    def _op_33(self, *a):
        if len(a) >= 3:
            self._set(a[0], _as_number(self._get(a[1]))[0] * _as_number(self._get(a[2]))[0])

    def _op_34(self, *a):
        if len(a) >= 2:
            self._set(a[0], self._get(a[1]))

    def _op_35(self, *a):
        if len(a) < 3:
            return
        left, right = _as_number(self._get(a[1]))[0], _as_number(self._get(a[2]))[0]
        self._set(a[0], 0.0 if right == 0 else left / right)

    # ---- the browser window -------------------------------------------------------------------------------------
    def _build_window(self) -> dict:
        p = self.profile
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 "
              "Safari/537.36")
        lang, languages_join = "en-US", "en-US,en"
        width, height = 1920, 1080
        inner_width, inner_height = width, height - 86
        outer_width, outer_height = 1280, 720
        screen_x = screen_y = 0
        hardware_concurrency = 16
        heap_limit = 4294967296
        device_id = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
        time_origin = float(int((self.clock() - 10) * 1000))
        performance_now = 9270.4
        vendor, platform = "Google Inc.", "Win32"
        document_probe, window_probe = "__reactContainer$b63yiita51i", "__oai_cached_session"
        if p.screen_sum > 0:
            width, height = _split_screen(p.screen_sum)
            inner_width, inner_height = width, max(height - 86, 640)
        if p.user_agent.strip():
            ua = p.user_agent
        if p.language.strip():
            lang = p.language
        if p.languages_join.strip():
            languages_join = p.languages_join
        if p.hardware_concurrency > 0:
            hardware_concurrency = p.hardware_concurrency
        if p.time_origin > 0:
            time_origin = p.time_origin
        if p.performance_now > 0:
            performance_now = p.performance_now
        if p.document_probe.strip():
            document_probe = p.document_probe.strip()
        if p.window_probe.strip():
            window_probe = p.window_probe.strip()
        hints = _browser_hints(ua, platform, vendor)
        session_id = p.session_id.strip() or "session-" + device_id.replace("-", "")

        location = _obj({"href": "https://auth.openai.com/create-account/password", "search": ""})
        scripts = [_obj({"src": "https://chatgpt.com/backend-api/sentinel/sdk.js"}),
                   _obj({"src": "https://chatgpt.com/sentinel/20260423af3c/sdk.js"})]
        storage_order = ("statsig.stable_id.444584300", "statsig.session_id.444584300",
                         "statsig.network_fallback.2742193661")
        storage_data = _obj({
            "statsig.stable_id.444584300": '"' + device_id + '"',
            "statsig.session_id.444584300": '{"sessionID":%s,"startTime":%.0f,"lastUpdate":%.0f}' % (
                json.dumps(session_id), time_origin, time_origin + performance_now),
            "statsig.network_fallback.2742193661": '{"initialize":{"urlConfigChecksum":"3392903","url":'
            '"https://assetsconfigcdn.org/v1/initialize","expiryTime":1775799927542,"previous":[]}}',
        }, storage_order)
        storage_keys = [storage_order]  # a cell: the closures below see the reassigned value
        local_storage = _obj({STORAGE_DATA: storage_data, STORAGE_KEYS: storage_order,
                              "length": float(len(storage_order))})

        def refresh_storage():
            storage_keys[0] = tuple(_keys_of(storage_data))
            local_storage[STORAGE_KEYS] = storage_keys[0]
            local_storage["length"] = float(len(storage_keys[0]))

        def storage_key(*a):
            if not a:
                return None
            i = _index(a[0])
            return storage_keys[0][i] if 0 <= i < len(storage_keys[0]) else None

        def get_item(*a):
            return storage_data.get(_js_str(a[0])) if a else None

        def set_item(*a):
            if len(a) >= 2:
                storage_data[_js_str(a[0])] = _js_str(a[1])
                refresh_storage()

        def remove_item(*a):
            if a:
                storage_data.pop(_js_str(a[0]), None)
                refresh_storage()

        def clear(*a):
            for key in list(storage_data):
                del storage_data[key]
            refresh_storage()

        local_storage.update(key=storage_key, getItem=get_item, setItem=set_item, removeItem=remove_item, clear=clear)
        screen = _obj({"availWidth": float(width), "availHeight": float(height), "availLeft": 0.0, "availTop": 0.0,
                       "colorDepth": 24.0, "pixelDepth": 24.0, "width": float(width), "height": float(height)})
        document = _obj({"scripts": scripts, "location": location,
                         "documentElement": _obj({"getAttribute": lambda *a: None})},
                        ("location", document_probe, "_reactListeningj3rmi50kcy", "closure_lm_184788"))
        document["body"] = _obj({"getBoundingClientRect": lambda *a: _obj({
            "x": 0.0, "y": 0.0, "width": 800.0, "height": 346.0, "top": 0.0, "left": 0.0, "right": 800.0,
            "bottom": 346.0})})
        document["getElementById"] = lambda *a: document["body"]
        document["querySelector"] = lambda *a: document["body"]

        def get_extension(*a):
            if a and _js_str(a[0]) == "WEBGL_debug_renderer_info":
                return _obj({"UNMASKED_VENDOR_WEBGL": 37445.0, "UNMASKED_RENDERER_WEBGL": 37446.0})
            return None

        def get_parameter(*a):
            if not a:
                return None
            param = _index(a[0])
            if param in (37445, 7936):
                return hints["webgl_vendor"]
            if param in (37446, 7937):
                return hints["webgl_renderer"]
            return None

        def create_element(*a):
            tag = _js_str(a[0]).lower() if a else ""
            element = _obj({"tagName": tag.upper(), "style": _obj({}),
                            "appendChild": lambda *x: x[0] if x else None,
                            "removeChild": lambda *x: x[0] if x else None,
                            "remove": lambda *x: None})
            if tag == "canvas":
                element["getContext"] = lambda *x: _obj({"getExtension": get_extension,
                                                         "getParameter": get_parameter})
            return element

        document["createElement"] = create_element
        navigator = _obj({
            "userAgent": ua, "vendor": hints["vendor"], "platform": platform,
            "hardwareConcurrency": float(hardware_concurrency), "deviceMemory": 8.0, "maxTouchPoints": 0.0,
            "language": lang,
            "languages": [x.strip() for x in languages_join.replace(";q=0.9", "").split(",") if x.strip()],
            "webdriver": False})
        navigator["clipboard"] = _obj({})
        navigator["xr"] = _obj({})
        navigator["storage"] = _obj({"estimate": lambda *a: _obj({
            "quota": 306461727129.0, "usage": 0.0, "usageDetails": _obj({})})})
        if hints["send_client_hints"]:
            major = hints["version_major"]
            navigator["userAgentData"] = _obj({
                "brands": [_obj({"brand": "Chromium", "version": major}),
                           _obj({"brand": "Google Chrome", "version": major}),
                           _obj({"brand": "Not_A Brand", "version": "24"})],
                "mobile": False, "platform": hints["platform_name"],
                "getHighEntropyValues": lambda *a: _obj({
                    "platform": hints["platform_name"], "platformVersion": hints["platform_version"],
                    "architecture": hints["architecture"], "model": "", "uaFullVersion": hints["version_full"]})})
        else:
            navigator["userAgentData"] = None
        start = self.clock()
        proto = _obj({}, NAVIGATOR_PROTOTYPE_KEYS)
        for key in NAVIGATOR_PROTOTYPE_KEYS:
            proto.setdefault(key, None)
        navigator[PROTO] = proto

        window = _obj({}, WINDOW_KEY_ORDER)
        window["Reflect"] = _obj({"set": lambda *a: _set_prop(a[0], a[1], a[2]) if len(a) >= 3 else True})
        window["Object"] = _obj({
            "keys": lambda *a: _object_keys(a[0]) if a else [],
            "getPrototypeOf": lambda *a: a[0].get(PROTO) if a and isinstance(a[0], dict) else None,
            "create": lambda *a: _obj({})})
        window["Math"] = _obj({"random": lambda *a: self.rand(),
                               "abs": lambda *a: abs(_as_number(a[0])[0]) if a else 0.0})

        def json_parse(*a):
            if not a:
                return None
            try:
                return _json_loads(_js_str(a[0]))
            except ValueError as e:
                raise _VmError("invalid JSON") from e

        window["JSON"] = _obj({"parse": json_parse, "stringify": lambda *a: _js_json(a[0]) if a else "null"})
        window["atob"] = lambda *a: _b64_latin1_decode(_js_str(a[0])) if a else ""
        window["btoa"] = lambda *a: _b64_latin1_encode(_js_str(a[0])) if a else ""
        window["localStorage"] = local_storage
        window["document"] = document
        window["navigator"] = navigator
        window["screen"] = screen
        window["location"] = location
        window["history"] = _obj({"length": 3.0})
        window["performance"] = _obj({
            "now": lambda *a: performance_now + (self.clock() - start) * 1000,
            "timeOrigin": time_origin,
            "memory": _obj({"jsHeapSizeLimit": float(heap_limit)})})
        window["Array"] = _obj({"from": lambda *a: list(a[0]) if a and isinstance(a[0], (list, tuple)) else []})
        window["0"] = window
        window.update({
            "innerWidth": float(inner_width), "innerHeight": float(inner_height), "outerWidth": float(outer_width),
            "outerHeight": float(outer_height), "screenX": float(screen_x), "screenY": float(screen_y),
            "scrollX": 0.0, "pageXOffset": 0.0, "scrollY": 0.0, "pageYOffset": 0.0,
            "devicePixelRatio": DEVICE_PIXEL_RATIO, "hardwareConcurrency": float(hardware_concurrency),
            "isSecureContext": True, "crossOriginIsolated": False, "event": None, "clientInformation": navigator,
            "screenLeft": float(screen_x), "screenTop": float(screen_y)})
        window["chrome"] = _obj({"runtime": _obj({})}) if hints["send_client_hints"] else None
        window["__reactRouterContext"] = _obj({
            "future": _obj({}), "routeDiscovery": _obj({}), "ssr": True, "isSpaMode": True,
            "loaderData": _obj({"routes/layouts/client-auth-session-layout/layout": _obj({"session": _obj({
                "session_id": session_id, "auth_session_logging_id": "3691f3d7-3e89-440c-99c1-0788585b7688"})})})})
        window["$RB"] = []
        window["$RV"] = lambda *a: None
        window["$RC"] = lambda *a: None
        window["$RT"] = performance_now
        for key in ("__reactRouterManifest", "__STATSIG__"):
            window[key] = _obj({})
        window["__reactRouterVersion"] = "7.9.3"
        for key in ("__REACT_INTL_CONTEXT__", "DD_RUM", "__SEGMENT_INSPECTOR__", "__reactRouterRouteModules",
                    "__reactRouterDataRouter", "__sentinel_token_pending", "__sentinel_init_pending", "SentinelSDK"):
            window[key] = _obj({})
        window["rwha4gh7no"] = lambda *a: None
        window[window_probe] = None
        _append_key(window, window_probe)
        document[document_probe] = None
        document["_reactListeningj3rmi50kcy"] = True
        document["closure_lm_184788"] = None
        window["window"] = window
        window["self"] = window
        window["globalThis"] = window
        for key in WINDOW_KEY_ORDER:
            window.setdefault(key, None)
        return window


# ---- browser hints --------------------------------------------------------------------------------------------------
def _browser_hints(ua: str, platform: str, vendor: str) -> dict:
    hints = {"send_client_hints": True, "version_major": "150", "version_full": "150.0.0.0",
             "platform_name": "Windows", "platform_version": "10.0.0", "architecture": "x86_64",
             "vendor": vendor.strip() and vendor or "Google Inc.",
             "webgl_vendor": WEBGL_VENDOR, "webgl_renderer": WEBGL_RENDERER}
    if version := _ua_version(ua, r"Chrome/([0-9]+(?:\.[0-9]+)*)"):
        hints["version_full"], hints["version_major"] = version, _major(version)
    if "Version/" in ua and "Safari/" in ua and "Chrome/" not in ua:
        hints.update(send_client_hints=False, vendor="Apple Computer, Inc.", webgl_vendor="Apple Inc.",
                     webgl_renderer="Apple GPU")
        if version := _ua_version(ua, r"Version/([0-9]+(?:\.[0-9]+)*)"):
            hints["version_full"], hints["version_major"] = version, _major(version)
    return hints


def _ua_version(ua: str, pattern: str) -> str:
    m = re.search(pattern, ua)
    return m.group(1).strip() if m else ""


def _major(version: str) -> str:
    return version.strip().split(".", 1)[0]


def _split_screen(total: int) -> tuple[int, int]:
    for w, h in ((2048, 1152), (1920, 1080), (1536, 864), (1440, 900), (1600, 900), (1366, 768)):
        if w + h == total:
            return w, h
    if total > 2000:
        width = math.floor(total * 0.64 + 0.5)
        return width, total - width
    return total, 0


# ---- JS values ------------------------------------------------------------------------------------------------------
def reg_key(value) -> str:
    """The register a value names: 6 and 6.0 are one register, 6.21 another, "6" a third."""
    if value is None:
        return "nil"
    if isinstance(value, str):
        return "s:" + value
    if isinstance(value, bool):
        return "x:" + ("true" if value else "false")
    if isinstance(value, int) or (isinstance(value, float) and math.isfinite(value) and value == math.trunc(value)):
        return "n:%d" % value
    if isinstance(value, float):
        return "n:" + repr(value)
    return "x:" + repr(value)


def _as_number(value) -> tuple[float, bool]:
    if isinstance(value, bool):
        return float(value), True
    if isinstance(value, (int, float)):
        return float(value), True
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0, True
        try:
            return float(text), True
        except ValueError:
            return math.nan, False
    return math.nan, False


def _equal(left, right) -> bool:
    if isinstance(left, bool):
        return isinstance(right, bool) and left == right
    if isinstance(left, (int, float)):
        return isinstance(right, (int, float)) and not isinstance(right, bool) and left == right
    if isinstance(left, str):
        return isinstance(right, str) and left == right
    if left is None:
        return right is None
    return left is right or (type(left) is type(right) and left == right)


def _js_str(value) -> str:
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        if value == math.trunc(value):
            return str(int(value))
        return format(Decimal(repr(value)), "f")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ",".join(_js_str_item(item) for item in value)
    if isinstance(value, tuple):
        return "[" + " ".join(value) + "]"
    if isinstance(value, dict):
        if isinstance(value.get("href"), str) and value.get("search") is not None:
            return value["href"]
        return "[object Object]"
    return _FUNCTION_TEXT


def _js_str_item(value) -> str:
    return "" if value is None else _js_str(value)


def _get_prop(obj, prop):
    if obj is None:
        return None
    if isinstance(obj, _RegMap):
        return obj.solver._get(prop)
    if isinstance(obj, dict):
        key = _js_str(prop)
        storage = obj.get(STORAGE_DATA)
        if isinstance(storage, dict) and key not in _STORAGE_OWN and key not in obj:
            return storage.get(key)
        return obj.get(key)
    if isinstance(obj, str):  # Go indexes the UTF-8 bytes
        obj = tuple(chr(b) for b in obj.encode("utf-8", "surrogatepass"))
    if isinstance(obj, (list, tuple)):
        if _js_str(prop) == "length":
            return float(len(obj))
        i = _index(prop)
        return obj[i] if 0 <= i < len(obj) else None
    return None


def _set_prop(obj, prop, value) -> bool:
    if isinstance(obj, _RegMap):
        obj.solver._set(prop, value)
        return True
    if not isinstance(obj, dict):
        return False
    key = _js_str(prop)
    storage = obj.get(STORAGE_DATA)
    if isinstance(storage, dict):
        obj[key] = value
        if key not in _STORAGE_OWN:
            storage[key] = value
            obj[STORAGE_KEYS] = tuple(_keys_of(storage))
            obj["length"] = float(len(obj[STORAGE_KEYS]))
        return True
    obj[key] = value
    _append_key(obj, key)
    return True


def _object_keys(value) -> list:
    if isinstance(value, dict):
        storage = value.get(STORAGE_DATA)
        if isinstance(storage, dict):
            return list(_keys_of(storage))
        return [k for k in _keys_of(value) if not _is_meta(k)]
    if isinstance(value, list):
        return [float(i) for i in range(len(value))]
    return []


def _keys_of(value: dict) -> list[str]:
    order = value.get(ORDER)
    if isinstance(order, tuple):
        return list(order)
    return sorted(k for k in value if not _is_meta(k))


def _obj(value: dict, keys=()) -> dict:
    """aurora's withOrderedKeys: `keys` first, then the keys already there, sorted."""
    ordered = list(dict.fromkeys(k for k in keys if not _is_meta(k)))
    ordered += sorted(k for k in value if not _is_meta(k) and k not in ordered)
    value[ORDER] = tuple(ordered)
    return value


def _append_key(value: dict, key: str) -> None:
    if _is_meta(key):
        return
    order = value.get(ORDER)
    if not isinstance(order, tuple):
        value[ORDER] = (key,)
    elif key not in order:
        value[ORDER] = (*order, key)


def _is_meta(key: str) -> bool:
    return key in _META


def _js_json(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _json_str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _json_number(value) if math.isfinite(value) else "null"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_js_json(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(_json_str(k) + ":" + _js_json(value.get(k))
                              for k in _keys_of(value) if not _is_meta(k)) + "}"
    if isinstance(value, _RegMap):
        return "{}"
    raise _VmError("json: unsupported type: turnstile.vmFunc")


def _json_str(text: str) -> str:
    """Go's json.Marshal of a string: HTML characters and the JS line separators escaped."""
    out = json.dumps(text, ensure_ascii=False)
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), (" ", "\\u2028"),
                    (" ", "\\u2029")):
        out = out.replace(ch, esc)
    return out


def _json_number(value: float) -> str:
    """Go's json.Marshal of a float64: plain digits, an exponent below 1e-6 and from 1e21 on."""
    magnitude = abs(value)
    if magnitude != 0 and (magnitude < 1e-6 or magnitude >= 1e21):
        mantissa, exp = repr(value).split("e")
        return mantissa + ("e-%d" % -int(exp) if int(exp) < 0 else "e+%02d" % int(exp))
    if value == math.trunc(value):
        return str(int(value))
    return format(Decimal(repr(value)), "f")


def _json_loads(text):
    def no_constant(name):
        raise ValueError(name)

    return json.loads(text, parse_int=float, parse_constant=no_constant)


def _json_str_field(value) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else _js_str(value)


def _json_float(value) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return 0.0
    return 0.0


def _index(value) -> int:
    if isinstance(value, bool):
        return -1
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value == math.trunc(value) else -1
    if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        return int(value.strip())
    return -1


# ---- bytes as latin-1 text ------------------------------------------------------------------------------------------
def _b64_latin1_decode(text: str) -> str:
    try:
        return base64.b64decode(text.replace("\r", "").replace("\n", ""), validate=True).decode("latin-1")
    except ValueError:
        raise _VmError("illegal base64 data") from None


def _b64_latin1_encode(text: str) -> str:
    return base64.b64encode(bytes(ord(c) & 0xFF for c in text)).decode("ascii")


def xor_string(data: str, key: str) -> str:
    """Each latin-1 byte of `data` XOR the key's bytes, the key cycled; an empty key leaves `data` as it is."""
    if not key:
        return data
    k = [ord(c) & 0xFF for c in key]
    return "".join(chr((ord(c) & 0xFF) ^ k[i % len(k)]) for i, c in enumerate(data))
