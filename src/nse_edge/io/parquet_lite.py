"""Minimal pure-Python Parquet reader (fallback when pyarrow is unavailable).

Supports what pandas/pyarrow writes by default for this project's files:
flat schemas, data page v1/v2, PLAIN / PLAIN_DICTIONARY / RLE_DICTIONARY encodings,
UNCOMPRESSED / SNAPPY / GZIP codecs, INT32/INT64/FLOAT/DOUBLE/BOOLEAN/BYTE_ARRAY,
TIMESTAMP logical types. Anything else raises NotImplementedError (fail loudly).

On a machine with pyarrow installed, `nse_edge.io.loaders.read_parquet` uses pandas
directly and this module is never touched.
"""
from __future__ import annotations

import gzip
import json
import struct
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- thrift compact


class _Thrift:
    def __init__(self, buf: bytes, pos: int = 0):
        self.b = buf
        self.p = pos

    def byte(self) -> int:
        v = self.b[self.p]
        self.p += 1
        return v

    def varint(self) -> int:
        shift = res = 0
        while True:
            x = self.byte()
            res |= (x & 0x7F) << shift
            if not x & 0x80:
                return res
            shift += 7

    def zigzag(self) -> int:
        n = self.varint()
        return (n >> 1) ^ -(n & 1)

    def value(self, t: int):
        if t == 1:
            return True
        if t == 2:
            return False
        if t == 3:
            v = self.byte()
            return v - 256 if v > 127 else v
        if t in (4, 5, 6):
            return self.zigzag()
        if t == 7:
            v = struct.unpack_from("<d", self.b, self.p)[0]
            self.p += 8
            return v
        if t == 8:
            n = self.varint()
            v = self.b[self.p:self.p + n]
            self.p += n
            return v
        if t in (9, 10):
            h = self.byte()
            size, et = h >> 4, h & 0x0F
            if size == 15:
                size = self.varint()
            if et in (1, 2):  # bools inside lists are one byte each
                return [self.byte() == 1 for _ in range(size)]
            return [self.value(et) for _ in range(size)]
        if t == 11:
            size = self.varint()
            if size == 0:
                return {}
            kv = self.byte()
            kt, vt = kv >> 4, kv & 0x0F
            return {self.value(kt): self.value(vt) for _ in range(size)}
        if t == 12:
            return self.struct()
        raise NotImplementedError(f"thrift type {t}")

    def struct(self) -> dict:
        out, last = {}, 0
        while True:
            h = self.byte()
            if h == 0:
                return out
            delta, t = h >> 4, h & 0x0F
            fid = last + delta if delta else self.zigzag()
            out[fid] = self.value(t)
            last = fid


# ---------------------------------------------------------------- codecs


def _snappy(data: bytes) -> bytes:
    t = _Thrift(data)
    n = t.varint()
    out = bytearray(n)
    o, p, b = 0, t.p, data
    L = len(b)
    while p < L:
        tag = b[p]
        p += 1
        kind = tag & 3
        if kind == 0:
            ln = tag >> 2
            if ln >= 60:
                nb = ln - 59
                ln = int.from_bytes(b[p:p + nb], "little")
                p += nb
            ln += 1
            out[o:o + ln] = b[p:p + ln]
            o += ln
            p += ln
            continue
        if kind == 1:
            ln = 4 + ((tag >> 2) & 7)
            off = ((tag >> 5) << 8) | b[p]
            p += 1
        elif kind == 2:
            ln = (tag >> 2) + 1
            off = b[p] | (b[p + 1] << 8)
            p += 2
        else:
            ln = (tag >> 2) + 1
            off = int.from_bytes(b[p:p + 4], "little")
            p += 4
        s = o - off
        if off >= ln:
            out[o:o + ln] = out[s:s + ln]
        else:  # overlapping copy
            for k in range(ln):
                out[o + k] = out[s + k]
        o += ln
    return bytes(out)


def _decompress(codec: int, data: bytes) -> bytes:
    if codec == 0:
        return data
    if codec == 1:
        return _snappy(data)
    if codec == 2:
        return gzip.decompress(data)
    raise NotImplementedError(f"parquet codec {codec} (install pyarrow)")


# ---------------------------------------------------------------- encodings


def _rle_bitpacked(buf: bytes, pos: int, end: int, bw: int, count: int) -> np.ndarray:
    out = np.empty(count, dtype=np.int64)
    filled = 0
    t = _Thrift(buf, pos)
    vbytes = (bw + 7) // 8
    while filled < count and t.p < end:
        h = t.varint()
        if h & 1:
            groups = h >> 1
            nbytes = groups * bw
            raw = np.frombuffer(buf, dtype=np.uint8, count=nbytes, offset=t.p)
            t.p += nbytes
            if bw == 0:
                vals = np.zeros(groups * 8, dtype=np.int64)
            else:
                bits = np.unpackbits(raw, bitorder="little").reshape(-1, bw).astype(np.int64)
                vals = bits @ (1 << np.arange(bw, dtype=np.int64))
            k = min(len(vals), count - filled)
            out[filled:filled + k] = vals[:k]
            filled += k
        else:
            run = h >> 1
            v = int.from_bytes(buf[t.p:t.p + vbytes], "little") if vbytes else 0
            t.p += vbytes
            k = min(run, count - filled)
            out[filled:filled + k] = v
            filled += k
    if filled != count:
        raise ValueError("RLE stream ended early")
    return out


_NP = {1: "<i4", 2: "<i8", 4: "<f4", 5: "<f8"}


def _plain(ptype: int, buf: bytes, pos: int, n: int):
    if ptype in _NP:
        dt = np.dtype(_NP[ptype])
        return np.frombuffer(buf, dtype=dt, count=n, offset=pos).copy()
    if ptype == 0:
        bits = np.unpackbits(np.frombuffer(buf, np.uint8, offset=pos, count=(n + 7) // 8), bitorder="little")
        return bits[:n].astype(bool)
    if ptype == 6:
        vals, p = [], pos
        for _ in range(n):
            ln = struct.unpack_from("<I", buf, p)[0]
            p += 4
            vals.append(buf[p:p + ln].decode("utf-8", "replace"))
            p += ln
        return np.array(vals, dtype=object)
    raise NotImplementedError(f"parquet physical type {ptype}")


# ---------------------------------------------------------------- reader


def _read_column(raw: bytes, cmeta: dict, ptype: int, optional: bool, nrows: int):
    codec = cmeta[4]
    start = cmeta.get(11) or cmeta[9]
    if cmeta.get(11) and cmeta[9] < cmeta[11]:
        start = cmeta[9]
    total = cmeta[5]
    pos = start
    dictionary = None
    pieces = []
    seen = 0
    while seen < total:
        t = _Thrift(raw, pos)
        ph = t.struct()
        pos = t.p
        csize, usize = ph[3], ph[2]
        page = raw[pos:pos + csize]
        pos += csize
        kind = ph[1]
        if kind == 2:  # dictionary
            d = _decompress(codec, page)
            dictionary = _plain(ptype, d, 0, ph[7][1])
            continue
        if kind == 0:
            dh = ph[5]
            nvals, enc = dh[1], dh[2]
            d = _decompress(codec, page)
            p = 0
            if optional:
                ln = struct.unpack_from("<I", d, 0)[0]
                defs = _rle_bitpacked(d, 4, 4 + ln, 1, nvals)
                p = 4 + ln
            else:
                defs = np.ones(nvals, dtype=np.int64)
        elif kind == 3:
            dh = ph[8]
            nvals, enc = dh[1], dh[4]
            dl, rl = dh.get(5, 0), dh.get(6, 0)
            lv = page[:dl + rl]
            body = page[dl + rl:]
            compressed = dh.get(7, True)
            d = lv + (_decompress(codec, body) if compressed else body)
            defs = _rle_bitpacked(d, rl, rl + dl, 1, nvals) if optional else np.ones(nvals, dtype=np.int64)
            p = dl + rl
        else:
            raise NotImplementedError(f"page type {kind}")
        nnon = int(defs.sum())
        if enc == 0:
            vals = _plain(ptype, d, p, nnon)
        elif enc in (2, 8):
            bw = d[p]
            idx = _rle_bitpacked(d, p + 1, len(d), bw, nnon)
            vals = dictionary[idx]
        else:
            raise NotImplementedError(f"encoding {enc}")
        if nnon != nvals:
            if vals.dtype.kind in "fiu":
                full = np.full(nvals, np.nan)
            else:
                full = np.full(nvals, None, dtype=object)
            full[defs.astype(bool)] = vals
            vals = full
        pieces.append(vals)
        seen += nvals
    return np.concatenate(pieces) if pieces else np.array([])


def read_parquet_lite(path: str | Path) -> pd.DataFrame:
    raw = Path(path).read_bytes()
    if raw[:4] != b"PAR1" or raw[-4:] != b"PAR1":
        raise ValueError(f"{path}: not a parquet file")
    n = struct.unpack("<I", raw[-8:-4])[0]
    meta = _Thrift(raw[-8 - n:-8]).struct()
    schema = meta[2][1:]  # skip root
    kv = {x.get(1, b"").decode(): x.get(2, b"") for x in meta.get(5, [])}
    tzinfo = {}
    if "pandas" in kv:
        try:
            for c in json.loads(kv["pandas"])["columns"]:
                if c.get("metadata") and c["metadata"].get("timezone"):
                    tzinfo[c["name"]] = c["metadata"]["timezone"]
        except Exception:
            pass
    cols = {s[4].decode(): [] for s in schema}
    nrows_total = 0
    for rg in meta[4]:
        nrows = rg[3]
        nrows_total += nrows
        for s, cc in zip(schema, rg[1]):
            name = s[4].decode()
            optional = s.get(3, 0) == 1
            cols[name].append(_read_column(raw, cc[3], s[1], optional, nrows))
    out = {}
    for s in schema:
        name = s[4].decode()
        arr = np.concatenate(cols[name]) if cols[name] else np.array([])
        logical = s.get(10) or {}
        unit = None
        if 8 in logical:  # TIMESTAMP
            u = logical[8].get(2, {})
            unit = "ms" if 1 in u else "us" if 2 in u else "ns"
        elif s.get(6) in (9, 10):  # converted TIMESTAMP_MILLIS / MICROS
            unit = "ms" if s[6] == 9 else "us"
        if unit:
            if arr.dtype.kind == "f":  # nulls present
                mask = np.isnan(arr)
                ints = np.where(mask, 0, arr).astype("int64")
                ser = pd.Series(pd.to_datetime(ints, unit=unit, utc=True))
                ser[mask] = pd.NaT
                ser = pd.DatetimeIndex(ser)
            else:
                ser = pd.to_datetime(arr.astype("int64"), unit=unit, utc=True)
            tz = tzinfo.get(name)
            if tz:
                ser = ser.tz_convert(_tz(tz))
            out[name] = ser
        else:
            out[name] = arr
    return pd.DataFrame(out)


def _tz(spec: str):
    if spec.startswith(("+", "-")) and ":" in spec:
        return "Asia/Kolkata" if spec == "+05:30" else spec
    return spec
