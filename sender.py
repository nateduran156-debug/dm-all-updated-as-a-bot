import asyncio
import logging
import time

import aiohttp

API = "https://discord.com/api/v10"

log = logging.getLogger("dm.sender")


class NoTokens(Exception):
    pass


class TokenDead(Exception):
    pass


class CantDM(Exception):
    pass


class Sender:
    def __init__(self, tokens, gap=6.0):
        self.gap = gap
        self.tokens = []
        self.dead = {}
        self._last = {}
        self.session = None
        self.set_tokens(tokens)

    def set_tokens(self, tokens):
        clean = []
        for t in tokens:
            t = t.strip()
            if t and t not in clean:
                clean.append(t)
        self.tokens = clean
        self.dead = {k: v for k, v in self.dead.items() if k in clean}

    def live(self):
        return [t for t in self.tokens if t not in self.dead]

    def add(self, token):
        token = token.strip()
        if not token:
            raise ValueError("empty token")
        if token in self.tokens:
            raise ValueError("that token is already in the pool")
        self.tokens.append(token)
        self.dead.pop(token, None)
        return len(self.tokens)

    def remove(self, index):
        if index < 1 or index > len(self.tokens):
            raise ValueError(f"index must be 1..{len(self.tokens)}")
        token = self.tokens.pop(index - 1)
        self.dead.pop(token, None)
        return token

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def _pace(self, token):
        wait = self.gap - (time.monotonic() - self._last.get(token, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        self._last[token] = time.monotonic()

    async def _post(self, token, url, payload):
        headers = {"Authorization": f"Bot {token}"}
        for _ in range(5):
            await self._pace(token)
            async with self.session.post(url, json=payload, headers=headers) as r:
                if r.status == 429:
                    try:
                        wait = float((await r.json()).get("retry_after", 5))
                    except Exception:
                        wait = 5.0
                    await asyncio.sleep(min(wait, 60) + 0.5)
                    continue
                if r.status == 401:
                    raise TokenDead("401")
                if r.status >= 400:
                    body = await r.text()
                    raise CantDM(f"{r.status} {body[:120]}")
                return await r.json()
        raise CantDM("429 loop")

    async def _send_one(self, token, uid, message):
        mention = f"<@{uid}>"
        content = f"{mention} {message[: 2000 - len(mention) - 1]}"
        channel = await self._post(token, f"{API}/users/@me/channels", {"recipient_id": str(uid)})
        await self._post(token, f"{API}/channels/{channel['id']}/messages", {"content": content})

    async def send_all(self, user_ids, message, on_progress=None):
        live = self.live()
        if not live:
            raise NoTokens

        if self.session is None:
            self.session = aiohttp.ClientSession()

        queue = asyncio.Queue()
        for uid in user_ids:
            queue.put_nowait(uid)

        stats = {"total": len(user_ids), "done": 0, "sent": 0, "failed": 0, "dead": 0}

        async def worker(token):
            while True:
                if token in self.dead:
                    return
                try:
                    uid = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await self._send_one(token, uid, message)
                    stats["sent"] += 1
                except TokenDead as exc:
                    log.warning("token %s dead (%s)", token[:10], exc)
                    self.dead[token] = str(exc)
                    stats["dead"] += 1
                    queue.task_done()
                    queue.put_nowait(uid)
                    if not self.live():
                        while True:
                            try:
                                queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                            queue.task_done()
                    return
                except CantDM as exc:
                    log.info("skip %s: %s", uid, exc)
                    stats["failed"] += 1
                except Exception as exc:
                    log.warning("error on %s: %s", uid, exc)
                    stats["failed"] += 1
                stats["done"] += 1
                queue.task_done()
                if on_progress:
                    await on_progress(stats)

        workers = [asyncio.create_task(worker(t)) for t in live]
        await queue.join()
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        return stats
