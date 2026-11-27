#!/usr/bin/env python3
"""Read-only dump of Hermes Desktop Local Storage (Chromium LevelDB) on the Mac.

Pure stdlib (no snappy/plyvel): decodes .ldb tables (snappy) and .log WAL, keeps the newest value per key.
Usage (on the Mac, /usr/bin/python3):
  read_desktop_localstorage.py 'composer-draft-retry|composer-outbox|pendingSubmissions'
Prints matching keys in write order with their value (empty line = cleared/deleted).
"""
import glob, os, re, sys

def varint(b, i):
    r = s = 0
    while True:
        c = b[i]; i += 1
        r |= (c & 0x7f) << s; s += 7
        if c < 0x80: return r, i

def snappy(b):
    n, i = varint(b, 0); out = bytearray()
    while i < len(b):
        t = b[i]; i += 1; k = t & 3
        if k == 0:
            L = t >> 2
            if L >= 60:
                nb = L - 59; L = int.from_bytes(b[i:i+nb], 'little'); i += nb
            L += 1; out += b[i:i+L]; i += L; continue
        if k == 1:
            L = ((t >> 2) & 7) + 4; off = ((t >> 5) << 8) | b[i]; i += 1
        elif k == 2:
            L = (t >> 2) + 1; off = int.from_bytes(b[i:i+2], 'little'); i += 2
        else:
            L = (t >> 2) + 1; off = int.from_bytes(b[i:i+4], 'little'); i += 4
        for _ in range(L): out.append(out[-off])
    return bytes(out)

def block(data, off, size):
    raw = data[off:off+size]
    return snappy(raw) if data[off+size] == 1 else raw

def entries(blk):
    nrest = int.from_bytes(blk[-4:], 'little'); end = len(blk) - 4 - 4 * nrest
    i = 0; key = b''
    while i < end:
        sh, i = varint(blk, i); ns, i = varint(blk, i); vl, i = varint(blk, i)
        key = key[:sh] + blk[i:i+ns]; i += ns
        yield key, blk[i:i+vl]; i += vl

def sst(path):
    d = open(path, 'rb').read(); f = d[-48:]
    _, j = varint(f, 0); _, j = varint(f, j); io, j = varint(f, j); isz, j = varint(f, j)
    for _, h in entries(block(d, io, isz)):
        o, k = varint(h, 0); sz, k = varint(h, k)
        for key, val in entries(block(d, o, sz)):
            yield key[:-8], int.from_bytes(key[-8:], 'little') >> 8, val

def wal(path):
    d = open(path, 'rb').read(); i = 0; buf = b''
    while i + 7 <= len(d):
        ln = int.from_bytes(d[i+4:i+6], 'little'); t = d[i+6]
        if ln == 0 and t == 0: i = (i // 32768 + 1) * 32768; continue
        buf += d[i+7:i+7+ln]; i += 7 + ln
        if t in (1, 4):
            rec = buf; buf = b''
            seq = int.from_bytes(rec[:8], 'little'); cnt = int.from_bytes(rec[8:12], 'little'); p = 12
            for n in range(cnt):
                kind = rec[p]; p += 1
                kl, p = varint(rec, p); k = rec[p:p+kl]; p += kl
                v = None
                if kind == 1:
                    vl, p = varint(rec, p); v = rec[p:p+vl]; p += vl
                yield k, seq + n, v
        if (i % 32768) > 32768 - 7: i = (i // 32768 + 1) * 32768

def dec(v):
    if v is None: return None
    return v[1:].decode('utf-16-le', 'replace') if v[:1] == b'\x00' else v[1:].decode('latin-1')

os.chdir(os.path.expanduser('~/Library/Application Support/Hermes/Local Storage/leveldb'))
latest = {}
for f in sorted(glob.glob('*.ldb')):
    for k, s, v in sst(f):
        if k not in latest or s > latest[k][0]: latest[k] = (s, v)
for f in sorted(glob.glob('*.log')):
    for k, s, v in wal(f):
        if k not in latest or s > latest[k][0]: latest[k] = (s, v)
pat = re.compile(sys.argv[1] if len(sys.argv) > 1 else 'composer-draft|composer-outbox|pendingSubmissions')
for k, (s, v) in sorted(latest.items(), key=lambda x: x[1][0]):
    ks = k.decode('latin-1')
    if pat.search(ks):
        print('==', ks, 'seq', s, 'DELETED' if v is None else '')
        if v is not None: print(dec(v)[:3000])
