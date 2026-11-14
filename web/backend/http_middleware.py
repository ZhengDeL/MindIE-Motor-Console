"""Bound actual ASGI request bytes, including chunked bodies, before parsing."""
import json
from uuid import uuid4


class RequestBodyLimit:
    def __init__(self, app, max_bytes=5 * 1024 * 1024, logs=None, path_limits=None):
        self.app, self.max_bytes, self.logs = app, max_bytes, logs
        self.path_limits = dict(path_limits or {})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        maximum = self.path_limits.get(scope.get("path"), self.max_bytes)
        async def reject(code):
            request_id = uuid4().hex
            raw = json.dumps({"detail": "请求体超过限制" if code == 413 else "无效 Content-Length", "request_id": request_id}).encode()
            if self.logs:
                try: self.logs.write("http.body_rejected", request_id=request_id, path=scope.get("path"), status_code=code)
                except (OSError, RuntimeError): pass
            await send({"type": "http.response.start", "status": code, "headers": [(b"content-type", b"application/json"), (b"x-request-id", request_id.encode()), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": raw})
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        if b"content-length" in headers:
            try:
                size = int(headers[b"content-length"])
                if size < 0: raise ValueError()
            except ValueError: return await reject(400)
            if size > maximum: return await reject(413)
        parts, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect": return
            data = message.get("body", b"")
            total += len(data)
            if total > maximum: return await reject(413)
            parts.append(data)
            if not message.get("more_body", False): break
        pending = True
        async def replay():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": b"".join(parts), "more_body": False}
            return await receive()
        await self.app(scope, replay, send)
