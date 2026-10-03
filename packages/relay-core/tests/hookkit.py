"""Shared helpers for the webhook tests: a fake DNS, a scripted receiver on httpx's MockTransport (no real network)
and a terminal job that has a webhook waiting."""

import json
import socket

import httpx

PUBLIC = "93.184.216.34"
URL = "https://hooks.example.test/in"
DNS = {"hooks.example.test": [PUBLIC], "other.example.test": [PUBLIC], "evil.test": ["10.0.0.7"]}


async def resolver(host: str, port: int) -> list[str]:
    if host not in DNS:
        raise socket.gaierror("no such host")
    return DNS[host]


class Receiver:
    """Answers with `script` in order (the last answer repeats); an Exception in the script is raised. `on_request`
    runs while the request is in flight, which is where a test changes the job under the sender."""

    def __init__(self, *script, on_request=None):
        self.script = list(script) or [200]
        self.on_request = on_request
        self.seen: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.on_request:
            self.on_request(request)
        answer = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, httpx.Response) else httpx.Response(answer, content=b"")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.seen]


def hook_job(db, key_id, *, url: str = URL, status: str = "failed"):
    """A terminal job whose webhook is pending and due now."""
    job_id = db.job(key_id)
    db.run("UPDATE jobs SET status = %s, finished_at = clock_timestamp(), callback_url = %s, "
           "callback_status = 'pending', callback_attempts = 0, callback_next_at = clock_timestamp(), "
           "callback_for = clock_timestamp() WHERE id = %s", status, url, job_id)
    return job_id


def hook(db, job_id):
    """(callback_status, callback_attempts, callback_last)"""
    return db.one("SELECT callback_status, callback_attempts, callback_last FROM jobs WHERE id = %s", job_id)


def make_due(db, job_id) -> None:
    db.run("UPDATE jobs SET callback_next_at = clock_timestamp() WHERE id = %s", job_id)
