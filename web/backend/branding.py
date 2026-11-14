"""Database-backed presentation settings; protocol and native identities stay fixed."""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct
import unicodedata
import zlib

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ports.storage import RevisionConflict
from .store import now
from .version import PRODUCT_NAME


DEFAULT_SUBTITLE = "分布式推理控制台"
MAX_ICON_BYTES = 256 * 1024
MAX_ICON_DIMENSION = 512
_DATA_PREFIX = "data:image/png;base64,"
_MAX_DATA_LENGTH = len(_DATA_PREFIX) + 4 * ((MAX_ICON_BYTES + 2) // 3)
_KIND, _IDENTIFIER = "platform_settings", "branding"
_ICON_PATH = re.compile(r"/api/branding/icons/[^/]{1,128}\.png")
_HASH = re.compile(r"[0-9a-f]{64}")


def public_branding_read(path: str, method: str) -> bool:
    return method in {"GET", "HEAD"} and (path == "/api/branding" or _ICON_PATH.fullmatch(path) is not None)


def branding_icon_read(path: str, method: str) -> bool:
    return method in {"GET", "HEAD"} and _ICON_PATH.fullmatch(path) is not None


def _text(value):
    if isinstance(value, str):
        value = value.strip()
        if any(unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for character in value):
            raise ValueError("品牌文字不能含控制字符、换行或不可见格式字符")
    return value


class BrandingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(strict=True, min_length=1, max_length=40)
    subtitle: str = Field(strict=True, max_length=80)
    expected_revision: int = Field(strict=True, ge=0)
    icon_data_url: str | None = Field(default=None, strict=True, max_length=_MAX_DATA_LENGTH, repr=False)

    @field_validator("name", "subtitle", mode="before")
    @classmethod
    def clean_text(cls, value):
        return _text(value)


def _invalid_icon():
    return HTTPException(422, "图标须为有效的非交错 8 位 RGB/RGBA PNG，边长不超过 512 像素，文件不超过 256 KiB")


def _png(raw: bytes) -> bytes:
    """Validate the entire bounded canonical PNG produced by the browser canvas.

    RGB/RGBA 8-bit samples make every reconstructed sample value legal, without
    delegating palette, interlace, text metadata or animation parsing to a codec.
    The decompressor receives a strict output ceiling, including filter bytes.
    """
    if not 57 <= len(raw) <= MAX_ICON_BYTES or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise _invalid_icon()
    offset, width, height, channels = 8, 0, 0, 0
    seen, compressed, ended, idat = set(), bytearray(), False, False
    while offset < len(raw):
        if len(raw) - offset < 12:
            raise _invalid_icon()
        length = struct.unpack_from(">I", raw, offset)[0]
        kind = raw[offset + 4:offset + 8]
        finish = offset + 12 + length
        if length > MAX_ICON_BYTES or finish > len(raw):
            raise _invalid_icon()
        payload = raw[offset + 8:offset + 8 + length]
        checksum = struct.unpack_from(">I", raw, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != checksum:
            raise _invalid_icon()
        if not seen and kind != b"IHDR":
            raise _invalid_icon()
        if kind == b"IHDR":
            if seen or length != 13:
                raise _invalid_icon()
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if (not 1 <= width <= MAX_ICON_DIMENSION or not 1 <= height <= MAX_ICON_DIMENSION
                    or depth != 8 or color not in {2, 6} or compression or filtering or interlace):
                raise _invalid_icon()
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            idat = True
            compressed.extend(payload)
        elif kind == b"IEND":
            if not idat or length or finish != len(raw):
                raise _invalid_icon()
            ended = True
        elif kind in {b"sRGB", b"gAMA", b"cHRM", b"pHYs"}:
            if idat or kind in seen:
                raise _invalid_icon()
            sizes = {b"sRGB": 1, b"gAMA": 4, b"cHRM": 32, b"pHYs": 9}
            if (length != sizes[kind] or kind == b"sRGB" and payload[0] > 3
                    or kind == b"gAMA" and not int.from_bytes(payload, "big")
                    or kind == b"pHYs" and payload[-1] > 1):
                raise _invalid_icon()
        else:
            # Includes APNG, SVG/polyglot text, palettes, profiles and unknown chunks.
            raise _invalid_icon()
        seen.add(kind)
        offset = finish
        if ended:
            break
    if not ended or not compressed:
        raise _invalid_icon()
    stride = 1 + width * channels
    expected = height * stride
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(bytes(compressed), expected + 1)
    except zlib.error as error:
        raise _invalid_icon() from error
    if (len(pixels) != expected or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data
            or any(pixels[row * stride] > 4 for row in range(height))):
        raise _invalid_icon()
    return raw


def icon_bytes(value: str) -> bytes:
    if not value.startswith(_DATA_PREFIX) or len(value) > _MAX_DATA_LENGTH:
        raise _invalid_icon()
    encoded = value[len(_DATA_PREFIX):]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise _invalid_icon() from error
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise _invalid_icon()
    return _png(raw)


class BrandingManager:
    def __init__(self, store, lock):
        self.store, self.lock = store, lock

    @staticmethod
    def project(record: dict | None) -> dict:
        if record is None:
            return {"name": PRODUCT_NAME, "subtitle": DEFAULT_SUBTITLE, "icon_url": None, "revision": 0}
        digest = record.get("icon_sha256")
        return {"name": record["name"], "subtitle": record["subtitle"], "revision": record["revision"],
                "icon_url": "/api/branding/icons/" + digest + ".png" if digest else None,
                "updated_at": record["updated_at"]}

    def public(self) -> dict:
        return self.project(self.store.get(_KIND, _IDENTIFIER))

    def save(self, settings: BrandingInput) -> dict:
        replacement = None
        if "icon_data_url" in settings.model_fields_set and settings.icon_data_url is not None:
            replacement = icon_bytes(settings.icon_data_url)
        # Creation and replacement share the database's single-writer transaction
        # and audit commit; the first revision is never a cross-transaction upsert.
        with self.lock, self.store.unit_of_work(write=True) as unit:
            previous = unit.resources.get(_KIND, _IDENTIFIER)
            if settings.expected_revision != (previous["revision"] if previous else 0):
                raise RevisionConflict("平台品牌已更新，请读取最新配置后再保存")
            record = {"id": _IDENTIFIER, "name": settings.name, "subtitle": settings.subtitle,
                      "revision": settings.expected_revision + 1, "updated_at": now()}
            if "icon_data_url" not in settings.model_fields_set:
                record.update({key: previous[key] for key in ("icon_base64", "icon_sha256") if previous and key in previous})
            elif replacement is not None:
                record.update(icon_base64=base64.b64encode(replacement).decode("ascii"),
                              icon_sha256=hashlib.sha256(replacement).hexdigest())
            if previous is None:
                unit.resources.put(_KIND, record)
            elif not unit.resources.compare_and_replace(_KIND, _IDENTIFIER, previous, record):
                raise RevisionConflict("平台品牌已更新，请读取最新配置后再保存")
            self.store.audit("修改平台品牌", "平台外观", "success", connection=unit,
                metadata={"revision": record["revision"], "icon_present": bool(record.get("icon_sha256"))})
        return self.project(record)

    def icon(self, digest: str) -> bytes:
        if not _HASH.fullmatch(digest):
            raise HTTPException(404, "图标不存在")
        record = self.store.get(_KIND, _IDENTIFIER)
        if record is None or record.get("icon_sha256") != digest or not isinstance(record.get("icon_base64"), str):
            raise HTTPException(404, "图标不存在")
        try:
            raw = icon_bytes(_DATA_PREFIX + record["icon_base64"])
        except HTTPException as error:
            raise HTTPException(503, "平台图标暂不可用，请由管理员重新保存") from error
        if hashlib.sha256(raw).hexdigest() != digest:
            raise HTTPException(503, "平台图标暂不可用，请由管理员重新保存")
        return raw


def create_branding_router(manager: BrandingManager) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.api_route("/branding", methods=["GET", "HEAD"])
    def branding():
        return manager.public()

    @router.put("/branding")
    def save_branding(settings: BrandingInput):
        return manager.save(settings)

    @router.api_route("/branding/icons/{digest}.png", methods=["GET", "HEAD"])
    def icon(digest: str, request: Request):
        raw = manager.icon(digest)
        etag = '"' + digest + '"'
        headers = {"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable",
                   "X-Content-Type-Options": "nosniff", "Content-Disposition": 'inline; filename="platform-icon.png"'}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(raw, media_type="image/png", headers=headers)

    return router
