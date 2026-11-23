"""The two endpoints Vonage needs, on one port, and nothing else.

  GET|POST /answer?key=...&uuid=...&from=...   Vonage asks what to do with an inbound call. Only a request carrying the answer key is answered. The reply is the NCCO telling
                              it to stream the call's audio to our websocket, at an address carrying a short-lived signed token.
  websocket /ws/<uuid>?...    The call's audio (PCM16 8 kHz both ways) and the keys the caller presses, run through the voice bridge or the phone tree (config.line_mode). Refused without
                              a valid token.

Every other path is refused. There are no pages and no other routes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import websockets
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from .audio import play_pcm, ringback_pcm
from .calllog import CallLog, ended_by, turns_from_transcript
from .bridge import BridgeSettings, ToolFunction, VoiceBridge
from .config import Config
from .coverage import Fixture, make_tools
from .limits import Limits
from .persona import DISCLOSURE, build_system_prompt, call_reference
from .prompts import Prompts
from .refusals import PHRASES, Refusals
from .soniox_ncco import BRIDGE_PATH, build_ncco, inbound_ws_url
from .tokens import CallTokens, key_matches
from .tree import PhoneTree, TreeRunner

logger = logging.getLogger(__name__)

ANSWER_PATH = "/answer"
_CALL_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_WS_PATH = re.compile(r"^" + re.escape(BRIDGE_PATH) + r"/([A-Za-z0-9-]{1,64})$")

Tools = Dict[str, Tuple[Dict[str, Any], ToolFunction]]

TRANSFER_GREETING = "Thank you for holding. This is Acme Dental Benefits, provider services. How can I help you?"


class WebsocketCall:
    """The bridge's CallSocket over a websocket connection: audio is binary messages; text messages are Vonage's control frames and carry no audio."""

    def __init__(self, connection: ServerConnection):
        self._ws = connection

    async def receive(self) -> Optional[Tuple[str, Any]]:
        """The next thing from the caller: ("audio", bytes), ("dtmf", digit) for a key pressed (Vonage's `websocket:dtmf` text frame), or None when the call has gone.
        Other control frames are skipped."""
        while True:
            try:
                message = await self._ws.recv()
            except ConnectionClosed:
                return None
            if isinstance(message, (bytes, bytearray)):
                return "audio", bytes(message)
            try:
                event = json.loads(message)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("event") == "websocket:dtmf" and isinstance(event.get("digit"), str) and len(event["digit"]) == 1:
                return "dtmf", event["digit"]

    async def receive_audio(self) -> Optional[bytes]:
        while True:
            item = await self.receive()
            if item is None:
                return None
            if item[0] == "audio":
                return item[1]

    async def send_audio(self, data: bytes) -> None:
        await self._ws.send(data)

    async def close(self) -> None:
        await self._ws.close(code=1000)


class FakePayerServer:
    def __init__(self, config: Config, tools: Optional[Tools] = None, system_prompt: Optional[str] = None, limits: Optional[Limits] = None, refusals: Optional[Refusals] = None,
                 tree: Optional[PhoneTree] = None, prompts: Optional[Prompts] = None, fixture: Optional[Fixture] = None, chance: Optional[random.Random] = None, tree_pace: float = 0.02):
        """By default the line answers from the fixture with the persona's prompt (which carries the call's own reference number), or, for the calls `config.line_mode` gives to the
        phone tree, from tree.yaml. `tools`, `system_prompt`, `tree`, `prompts`, `fixture`, `chance` (what decides a call in "mixed" mode) and `tree_pace` replace them in tests."""
        self._config = config
        self._fixture = fixture if fixture is not None else Fixture.load()
        self._tools: Tools = tools if tools is not None else make_tools(self._fixture)
        self._tree = tree if tree is not None else PhoneTree.load()
        self._tree.check_against(self._fixture)
        self._prompts = prompts if prompts is not None else Prompts(config)
        self._chance = chance or random.Random()
        self._tree_pace = tree_pace
        self._system_prompt = system_prompt
        self._tokens = CallTokens(config.answer_key)
        self._limits = limits or Limits(config)
        self._refusals = refusals or Refusals()
        self._ring_audio = ringback_pcm(config.ring_count)
        self._active_calls: set = set()
        self._log = CallLog(config.call_log_dir)

    # ---- HTTP: the answer request, and a refusal for everything else ------

    def process_request(self, connection: ServerConnection, request: Request) -> Optional[Response]:
        parts = urlsplit(request.path)
        query = parse_qs(parts.query)
        if "websocket" in request.headers.get("Upgrade", "").lower():
            match = _WS_PATH.match(parts.path)
            if not match:
                return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
            if self._tokens.verify(match.group(1), query) is None:
                return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")           # not a call that came through the answer request
            return None
        if parts.path != ANSWER_PATH:
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        if not key_matches((query.get("key") or [""])[0], self._config.answer_key):
            return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")
        call_uuid = (query.get("uuid") or [""])[0]
        if not _CALL_ID.match(call_uuid):
            return connection.respond(HTTPStatus.BAD_REQUEST, "bad request\n")
        host = request.headers.get("Host", "")
        if not host:
            return connection.respond(HTTPStatus.BAD_REQUEST, "bad request\n")
        caller = self._tokens.caller((query.get("from") or [""])[0])
        response = connection.respond(HTTPStatus.OK, json.dumps(build_ncco(inbound_ws_url(host, call_uuid, self._tokens.issue(call_uuid, caller)))))
        del response.headers["Content-Type"]                 # respond() set text/plain; assigning would add a second header, not replace it
        response.headers["Content-Type"] = "application/json"
        return response

    # ---- websocket: one call ----------------------------------------------

    async def handle_call(self, connection: ServerConnection) -> None:
        parts = urlsplit(connection.request.path)
        match = _WS_PATH.match(parts.path)
        call_uuid = match.group(1) if match else "unknown"
        caller = self._tokens.verify(call_uuid, parse_qs(parts.query))
        call = WebsocketCall(connection)
        if caller is None or call_uuid in self._active_calls:         # the token was checked at the upgrade; a second stream for a call already running is not allowed
            await call.close()
            return
        decision, handle = self._limits.admit(caller.key)
        if not decision.allowed:
            logger.info("call %s refused (%s) caller=***%s", call_uuid, decision.reason, caller.last4)
            self._write_log(call_uuid, caller.last4, f"refused_{decision.reason}")
            audio = self._refusals.audio.get(decision.reason or "")
            if audio:
                try:
                    await play_pcm(call, audio)
                except Exception as exc:
                    logger.debug("spoken refusal failed (best-effort): %s", exc)
            await call.close()
            return
        self._active_calls.add(call_uuid)
        mode = self._choose_mode()
        started = time.monotonic()
        ended = "error"
        bridge: Optional[VoiceBridge] = None
        runner: Optional[TreeRunner] = None
        try:
            if mode == "tree":
                runner = TreeRunner(self._tree, self._fixture, self._prompts, call, reference=call_reference(call_uuid), ring_audio=self._ring_audio, answer_delay_seconds=self._config.answer_delay_seconds,
                                    pace=self._tree_pace)
                try:
                    result = await asyncio.wait_for(runner.run(), self._config.max_call_seconds or None)
                    ended = {"hangup": "tree_ended", "transfer": "transferred", "caller_gone": "caller_hung_up"}[result.outcome]
                except asyncio.TimeoutError:
                    ended = "time_limit"
                if ended != "transferred":
                    await call.close()                                   # the tree has hung up (or the caller has gone, or the time is up)
            if mode == "conversation" or ended == "transferred":
                bridge = VoiceBridge(self._bridge_settings(call_uuid, transferred=mode == "tree"), self._tools,
                                     self._system_prompt if self._system_prompt is not None else build_system_prompt(call_uuid))
                await bridge.run(call)
        except Exception:
            logger.exception("call %s ended with an error", call_uuid)
        finally:
            self._limits.release(handle)
            self._active_calls.discard(call_uuid)
            events = runner.events if runner is not None else []            # the tree's path, kept even when the call ended with an error
            if bridge is not None:
                self._write_log(call_uuid, caller.last4, ended_by(bridge.terminated_by), bridge.duration_seconds, turns_from_transcript(bridge.transcript), bridge.reply_latencies_ms,
                                mode=mode, events=events)
            else:
                self._write_log(call_uuid, caller.last4, ended, time.monotonic() - started, mode=mode, events=events)

    def _choose_mode(self) -> str:
        """Which line answers this call: the configured one, or in "mixed" mode the phone tree for `tree_fraction` of the calls and the representative for the rest."""
        if self._config.line_mode == "mixed":
            return "tree" if self._chance.random() < self._config.tree_fraction else "conversation"
        return self._config.line_mode

    def _bridge_settings(self, call_uuid: str, transferred: bool) -> BridgeSettings:
        """The conversational line's settings. A call the tree has handed over has already rung and been answered, so it gets a short greeting at once."""
        return BridgeSettings(
            call_uuid=call_uuid,
            greeting=TRANSFER_GREETING if transferred else DISCLOSURE,
            soniox_api_key=self._config.soniox_api_key,
            stt_model=self._config.stt_model,
            tts_model=self._config.tts_model,
            tts_voice=self._config.tts_voice,
            max_call_seconds=self._config.max_call_seconds,
            time_up_audio=self._refusals.audio.get("time"),
            hold_audio=self._refusals.audio.get("hold"),
            hold_text=PHRASES["hold"],
            ring_audio=b"" if transferred else self._ring_audio,
            answer_delay_seconds=0.0 if transferred else self._config.answer_delay_seconds,
            reply_delay_seconds=self._config.reply_delay_seconds,
            keep_the_floor=self._config.keep_the_floor,
        )

    def _write_log(self, call_uuid: str, last4: str, ended: str, duration: float = 0.0, turns=None, latencies=None, mode: str = "conversation", events=None) -> None:
        try:
            self._log.write(reference=call_reference(call_uuid), last4=last4, ended=ended, duration_seconds=duration, turns=turns, reply_latency_ms=latencies, mode=mode, events=events)
        except Exception:
            logger.exception("could not write the log entry for call %s", call_uuid)

    def cleanup_log(self) -> int:
        return self._log.cleanup(self._config.log_retention_days)

    def serve(self, host: Optional[str] = None, port: Optional[int] = None):
        """An async context manager running the server: ``async with server.serve(...)``."""
        return serve(self.handle_call, host or self._config.listen_host, port if port is not None else self._config.port, process_request=self.process_request)
