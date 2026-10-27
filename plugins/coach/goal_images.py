"""Bounded inline PNG projection of already authenticated native capture only.

No fetching, OCR, authority inference, or mutation of the raw capture. PNG is
intentionally a narrow subset: static, 8-bit, non-interlaced grayscale/RGB/RGBA.
"""
import base64
import copy
import hashlib
import json
import struct
import zlib

MAX_IMAGES = 4
MAX_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 4_000_000
MAX_TOTAL_PIXELS = 8_000_000


MAX_METADATA = 1024 * 1024


def _metadata(kind, body):
    """Validate bounded screenshot metadata; preserve original bytes verbatim.

    Never interpret metadata as instructions. Reject animation and unknown
    chunks; compressed profiles/text get an independent expansion ceiling.
    """
    if len(body) > MAX_METADATA:
        raise ValueError('image_metadata_limit')
    compressed = None
    if kind == b'iCCP':
        name, rest = body.split(b'\0', 1)
        if not 1 <= len(name) <= 79 or not rest or rest[0] != 0:
            raise ValueError('image_invalid_png')
        compressed = rest[1:]
    elif kind == b'iTXt':
        name, rest = body.split(b'\0', 1)
        if not 1 <= len(name) <= 79 or len(rest) < 2 or rest[0] not in (0, 1) or rest[1] != 0:
            raise ValueError('image_invalid_png')
        language, translated, text = rest[2:].split(b'\0', 2)
        language.decode('ascii')
        translated.decode('utf-8')
        if rest[0]:
            compressed = text
        else:
            text.decode('utf-8')
    elif kind in (b'cICP', b'pHYs', b'iDOT'):
        if len(body) != {b'cICP': 4, b'pHYs': 9, b'iDOT': 28}[kind]:
            raise ValueError('image_invalid_png')
    elif kind == b'eXIf':
        if not body.startswith((b'II\x2a\x00', b'MM\x00\x2a')):
            raise ValueError('image_invalid_png')
    else:
        raise ValueError('image_format_unsupported')
    if compressed is not None:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(compressed, MAX_METADATA + 1)
        if len(decoded) > MAX_METADATA or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('image_metadata_limit')
        if kind == b'iTXt':
            decoded.decode('utf-8')
        return len(body) + len(decoded)
    return len(body)


def _png(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError('image_format_unsupported')
    pos, chunks, compressed, shape, ended = 8, 0, bytearray(), None, False
    metadata_bytes = 0
    while pos < len(data):
        chunks += 1
        if chunks > 1024 or pos + 12 > len(data):
            raise ValueError('image_invalid_png')
        size = struct.unpack('>I', data[pos:pos+4])[0]
        kind = data[pos+4:pos+8]
        end = pos + 12 + size
        if end > len(data):
            raise ValueError('image_invalid_png')
        body = data[pos+8:end-4]
        if zlib.crc32(kind + body) != struct.unpack('>I', data[end-4:end])[0]:
            raise ValueError('image_invalid_png')
        if chunks == 1:
            if kind != b'IHDR' or size != 13:
                raise ValueError('image_invalid_png')
            w, h, depth, color, comp, filt, interlace = struct.unpack('>IIBBBBB', body)
            if not w or not h or w*h > MAX_PIXELS:
                raise ValueError('image_pixel_limit')
            if depth != 8 or color not in (0, 2, 4, 6) or comp or filt or interlace:
                raise ValueError('image_format_unsupported')
            shape = (w, h, {0: 1, 2: 3, 4: 2, 6: 4}[color])
        elif kind == b'IDAT':
            compressed.extend(body)
        elif kind == b'IEND' and not size:
            ended = end == len(data)
            break
        else:
            metadata_bytes += _metadata(kind, body)
            if metadata_bytes > MAX_METADATA:
                raise ValueError('image_metadata_limit')
        pos = end
    if not ended or not compressed or shape is None:
        raise ValueError('image_invalid_png')
    w, h, channels = shape
    expected = h * (1 + w * channels)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(bytes(compressed), expected + 1)
    if (len(raw) != expected or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail
            or any(raw[i] > 4 for i in range(0, expected, 1 + w * channels))):
        raise ValueError('image_invalid_png')
    return w*h


def project(value):
    """Return text-budget projection + ordered original attachment blocks.

    Callers must first authenticate scope through recover_scope/_user_row;
    content_source here is an internal projection label, not a wire credential.
    """
    result = copy.deepcopy(value)
    rows = list(result.values()) if isinstance(result, dict) else result
    images, total_bytes, total_pixels = [], 0, 0
    for row in rows:
        content = row.get('content')
        if row.get('role') != 'user' or not isinstance(content, list):
            continue
        if row.get('content_source') != 'native_content' or len(content) > 200:
            raise ValueError('scope_unsupported_content')
        for index, block in enumerate(content):
            if not isinstance(block, dict):
                raise ValueError('image_payload_unsupported')
            if block.get('type') == 'text' and set(block) == {'type', 'text'} and isinstance(block['text'], str):
                continue
            if set(block) != {'type', 'image_url'} or block['type'] != 'image_url':
                raise ValueError('image_payload_unsupported')
            image = block['image_url']
            if (not isinstance(image, dict) or set(image) - {'url', 'detail'}
                    or image.get('detail', 'auto') not in ('auto', 'high', 'low')):
                raise ValueError('image_payload_unsupported')
            url = image.get('url')
            prefix = 'data:image/png;base64,'
            if not isinstance(url, str) or not url.startswith(prefix):
                raise ValueError('image_format_unsupported')
            if len(images) >= MAX_IMAGES or len(url) - len(prefix) > 4*((MAX_BYTES+2)//3):
                raise ValueError('image_budget_limit')
            data = base64.b64decode(url[len(prefix):], validate=True)
            total_bytes += len(data)
            if len(data) > MAX_BYTES or total_bytes > MAX_TOTAL_BYTES:
                raise ValueError('image_budget_limit')
            total_pixels += _png(data)
            if total_pixels > MAX_TOTAL_PIXELS:
                raise ValueError('image_pixel_limit')
            images.append(copy.deepcopy(block))
            content[index] = {'attachment_index': len(images)-1,
                              'sha256': hashlib.sha256(data).hexdigest(),
                              'media_type': 'image/png', 'bytes': len(data)}
    return result, images


def serialized_size(value):
    return len(json.dumps(project(value)[0]))


def prompt_content(prompt):
    try:
        payload = json.loads(prompt)
    except json.JSONDecodeError:
        if len(prompt) > 32000:
            raise ValueError('context_limit')
        return prompt, False
    evidence, images = project(payload['evidence'])
    if not images:
        return prompt, False
    payload['evidence'] = evidence
    text = json.dumps(payload)
    if len(text) > 32000:
        raise ValueError('context_limit')
    # JSON array positions bind attachment indices to the following image blocks.
    # The system prompt explicitly denies screenshots any authority.
    return [{'type': 'text', 'text': text}, *images], True
