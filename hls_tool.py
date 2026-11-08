#!/usr/bin/env python3
"""
hls_tool.py - công cụ kiểm tra và tách video HLS (.m3u8), KHÔNG cần ffmpeg.

Khác với index.html (hoàn toàn offline), công cụ này BẮT BUỘC phải kết nối mạng để lấy video,
nên nó được thiết kế để bạn kiểm soát từng kết nối:
  * Chỉ dùng thư viện chuẩn của Python. Không telemetry, không gọi máy chủ nào khác ngoài playlist của bạn.
  * Trước khi tải, in ra TOÀN BỘ máy chủ sẽ kết nối (lấy từ playlist) và hỏi xác nhận [y/N].
  * Chặn chuyển hướng (redirect) sang máy chủ chưa được bạn xác nhận. Luôn kiểm tra chứng chỉ HTTPS.
  * Không in token: khi hiển thị link, mọi thứ sau dấu "?" bị che.
  * Không hỗ trợ DRM (SAMPLE-AES, Widevine, FairPlay) và không cố vượt qua DRM.

Cách dùng:
  python hls_tool.py                       # sẽ hỏi link (nhập ẩn)
  python hls_tool.py --check               # chỉ phân tích, không ghép video
  python hls_tool.py --list                # liệt kê các chất lượng
  python hls_tool.py --quality 720 -o a.ts # chọn chất lượng
  python hls_tool.py --header "Referer: https://trang-cua-ban/"   (lặp lại được)

Kết quả là file .ts (xem bằng VLC/MPC-HC) hoặc .mp4 nếu nguồn là fMP4.
Đổi .ts -> .mp4 không cần mã hóa lại nếu sau này bạn có ffmpeg: ffmpeg -i a.ts -c copy a.mp4
"""
import argparse
import collections
import getpass
import hashlib
import ipaddress
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "hls-tool/1.0"
ALLOWED = set()          # các máy chủ (host[:port]) đã được người dùng xác nhận
HEADERS = {"User-Agent": UA}


def die(msg, code=1):
    print("\n✘ " + msg, file=sys.stderr)
    sys.exit(code)


def mask(u):
    """Che token: chỉ hiện máy chủ + đường dẫn."""
    p = urllib.parse.urlsplit(u)
    return f"{p.scheme}://{p.netloc}{p.path}" + ("?***" if p.query else "")


def hostkey(u):
    return urllib.parse.urlsplit(u).netloc.lower()


def check_scheme(u):
    s = urllib.parse.urlsplit(u).scheme.lower()
    if s not in ("http", "https"):
        die(f"Từ chối giao thức không an toàn/không hỗ trợ: {s}:// ({mask(u)})")


# ---------- HTTP: chặn redirect tới máy chủ chưa xác nhận ----------
class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if hostkey(newurl) not in ALLOWED:
            raise urllib.error.URLError(f"Chuyển hướng tới máy chủ CHƯA xác nhận bị chặn: {hostkey(newurl)}")
        if urllib.parse.urlsplit(req.full_url).scheme == "https" and urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.URLError("Chuyển hướng từ HTTPS xuống HTTP bị chặn.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(SafeRedirect())   # mặc định xác minh chứng chỉ TLS


def http_get(url, extra=None, retries=3, timeout=30, limit=None):
    check_scheme(url)
    if hostkey(url) not in ALLOWED:
        die(f"Nội bộ: định kết nối tới máy chủ chưa xác nhận: {hostkey(url)}")
    last = None
    for attempt in range(retries):
        try:
            h = dict(HEADERS)
            if extra:
                h.update(extra)
            with OPENER.open(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return r.read() if limit is None else r.read(limit)
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 410):
                hint = {401: "cần đăng nhập", 403: "bị từ chối - token hết hạn hoặc cần Referer/cookie (--header)",
                        404: "không tồn tại", 410: "đã bị xóa/hết hạn", 400: "yêu cầu sai"}[e.code]
                raise RuntimeError(f"HTTP {e.code} ({hint}): {mask(url)}") from None
            last = e
        except Exception as e:  # mạng chập chờn, timeout...
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Không tải được sau {retries} lần: {mask(url)} ({last})")


def confirm_hosts(urls, yes):
    """In các máy chủ MỚI sắp kết nối và hỏi xác nhận."""
    new = sorted({hostkey(u) for u in urls} - ALLOWED)
    if not new:
        return
    print("\nCông cụ sắp kết nối tới các máy chủ sau (lấy từ playlist của bạn):")
    for h in new:
        name = h.rsplit(":", 1)[0].strip("[]")
        note = ""
        try:
            ip = ipaddress.ip_address(name)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                note = "   ⚠ địa chỉ NỘI BỘ"
        except ValueError:
            pass
        print(f"   - {h}{note}")
    if not yes and input("Cho phép? [y/N] ").strip().lower() not in ("y", "yes"):
        die("Đã hủy theo yêu cầu - chưa tải gì từ các máy chủ trên.", 0)
    ALLOWED.update(new)


# ---------- Phân tích chữ ký byte (giống phần phân tích trong index.html) ----------
def sniff(b):
    n = len(b)
    if n == 0:
        return "empty", 0
    if b.startswith(b"#EXTM3U") or (b[:3] == b"\xef\xbb\xbf" and b[3:10] == b"#EXTM3U"):
        return "m3u8", 0
    for k in range(0, min(8192, n - 565)):
        if b[k] == 0x47 and b[k + 188] == 0x47 and b[k + 376] == 0x47 and b[k + 564] == 0x47:
            return "ts", k
    for k in range(0, min(n - 8, 4096)):
        box = b[k + 4:k + 8]
        if box in (b"ftyp", b"moov"):
            return "mp4", k
        if box in (b"styp", b"moof", b"sidx"):
            return "fmp4", k
    if b[:4] == b"\x1a\x45\xdf\xa3":
        return "webm", 0
    if b[:3] == b"\xff\xd8\xff":
        return "jpeg", 0
    if b[:4] == b"\x89PNG":
        return "png", 0
    if b[:4] == b"GIF8":
        return "gif", 0
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp", 0
    if n >= 1024:
        c = collections.Counter(b[:4096])
        m = min(n, 4096)
        if -sum(v / m * math.log2(v / m) for v in c.values()) > 7.6:
            return "high", 0
    return "unknown", 0


LABEL = {"ts": "MPEG-TS (video thật)", "fmp4": "fMP4 (video thật)", "mp4": "MP4", "webm": "WebM",
         "m3u8": "playlist", "jpeg": "ẢNH JPEG thật", "png": "ẢNH PNG thật", "gif": "ẢNH GIF thật",
         "webp": "ẢNH WebP thật", "high": "dữ liệu ngẫu nhiên (có thể còn mã hóa)", "unknown": "không nhận dạng",
         "empty": "rỗng"}
VIDEO_KINDS = ("ts", "fmp4", "mp4", "webm")


# ---------- AES-128-CBC (khóa HLS công khai) ----------
def aes_cbc_decrypt(key, iv, data):
    if len(data) % 16:
        raise RuntimeError("Dữ liệu mã hóa không chia hết 16 byte (đoạn bị cắt/hỏng).")
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        out = d.update(data) + d.finalize()
        pad = out[-1] if out else 0
        return out[:-pad] if 1 <= pad <= 16 and out.endswith(bytes([pad]) * pad) else out
    except ImportError:
        pass
    if shutil.which("openssl"):
        r = subprocess.run(["openssl", "enc", "-d", "-aes-128-cbc", "-K", key.hex(), "-iv", iv.hex()],
                           input=data, capture_output=True)
        if r.returncode == 0:
            return r.stdout
        raise RuntimeError("openssl giải mã thất bại (sai khóa/IV?).")
    die("Video có mã hóa AES-128 nhưng máy chưa có thư viện giải mã.\n"
        "   Cài một trong hai:  pip install cryptography   |   hoặc cài OpenSSL.")


# ---------- Phân tích playlist ----------
def parse_attrs(s):
    return {k: v.strip('"') for k, v in re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', s)}


def parse_master(txt, base):
    variants, lines = [], txt.splitlines()
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-STREAM-INF:"):
            a = parse_attrs(l.split(":", 1)[1])
            uri = next((x.strip() for x in lines[i + 1:] if x.strip() and not x.startswith("#")), None)
            if uri:
                res = a.get("RESOLUTION", "")
                variants.append({"bw": int(a.get("BANDWIDTH", 0) or 0), "res": res,
                                 "h": int(res.split("x")[1]) if "x" in res else 0,
                                 "url": urllib.parse.urljoin(base, uri)})
    return variants


def parse_media(txt, base):
    segs, key, seq, dur, br_next, last_end, mp = [], None, 0, 0.0, None, 0, None
    for l in txt.splitlines():
        l = l.strip()
        if l.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(l.split(":")[1])
        elif l.startswith("#EXT-X-KEY:"):
            a = parse_attrs(l.split(":", 1)[1])
            m = a.get("METHOD", "NONE")
            key = None if m == "NONE" else {"method": m, "uri": urllib.parse.urljoin(base, a["URI"]) if a.get("URI") else None, "iv": a.get("IV")}
        elif l.startswith("#EXT-X-MAP:"):
            a = parse_attrs(l.split(":", 1)[1])
            mp = urllib.parse.urljoin(base, a["URI"])
        elif l.startswith("#EXTINF:"):
            dur = float(l[8:].split(",")[0] or 0)
        elif l.startswith("#EXT-X-BYTERANGE:"):
            ln, _, off = l.split(":")[1].partition("@")
            br_next = (int(ln), int(off) if off else last_end)
        elif l and not l.startswith("#"):
            rng = None
            if br_next:
                rng = (br_next[1], br_next[1] + br_next[0] - 1)
                last_end = br_next[1] + br_next[0]
                br_next = None
            segs.append({"url": urllib.parse.urljoin(base, l), "dur": dur, "seq": seq + len(segs), "key": key, "range": rng})
    return segs, mp


def seg_iv(seg):
    iv = seg["key"].get("iv")
    if iv:
        return bytes.fromhex(iv[2:] if iv.lower().startswith("0x") else iv).rjust(16, b"\0")
    return seg["seq"].to_bytes(16, "big")


def fetch_segment(seg, keys):
    extra = {"Range": f"bytes={seg['range'][0]}-{seg['range'][1]}"} if seg["range"] else None
    data = http_get(seg["url"], extra)
    k = seg["key"]
    if k:
        data = aes_cbc_decrypt(keys[k["uri"]], seg_iv(seg), data)
    return data


def main():
    ap = argparse.ArgumentParser(description="Kiểm tra & tách video HLS, không cần ffmpeg.")
    ap.add_argument("url", nargs="?", help="link .m3u8 (bỏ trống để nhập ẩn)")
    ap.add_argument("-o", "--output", help="tên file ra (mặc định video-hls.ts/.mp4)")
    ap.add_argument("--check", action="store_true", help="chỉ phân tích, không ghép video")
    ap.add_argument("--list", action="store_true", help="liệt kê các chất lượng rồi thoát")
    ap.add_argument("--quality", type=int, help="chọn chiều cao (vd 720); mặc định: cao nhất")
    ap.add_argument("--header", action="append", default=[], help='thêm header, vd "Referer: https://..."')
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--yes", action="store_true", help="bỏ qua hỏi xác nhận máy chủ (không khuyên dùng)")
    ap.add_argument("--force", action="store_true", help="ghi đè file ra nếu đã có")
    a = ap.parse_args()

    for h in a.header:
        if ":" not in h:
            die(f"Header sai dạng 'Tên: giá trị': {h}")
        k, v = h.split(":", 1)
        HEADERS[k.strip()] = v.strip()

    url = a.url or getpass.getpass("Dán link .m3u8 (nhập ẩn, nhấn Enter): ").strip()
    check_scheme(url)
    if url.startswith("http://"):
        print("⚠ Link dùng HTTP không mã hóa: ai trên đường truyền đều đọc/sửa được dữ liệu.")
    ALLOWED.add(hostkey(url))          # máy chủ do chính bạn nhập
    print(f"\n[1] Tải playlist: {mask(url)}")
    try:
        txt = http_get(url, limit=5_000_000).decode("utf-8", "replace")
    except RuntimeError as e:
        die(str(e))
    if not txt.lstrip("﻿").startswith("#EXTM3U"):
        kind, _ = sniff(txt.encode("latin-1", "ignore")[:9000])
        die(f"Đây không phải playlist HLS (nhận dạng: {LABEL.get(kind, kind)}).")

    base = url
    variants = parse_master(txt, base)
    if variants:
        variants.sort(key=lambda v: (v["h"], v["bw"]), reverse=True)
        print("    Playlist chính (master). Các chất lượng:")
        for i, v in enumerate(variants):
            print(f"      [{i}] {v['res'] or '?':>10}  {v['bw'] // 1000:>6} kbps  {mask(v['url'])}")
        if a.list:
            return
        pick = next((v for v in variants if v["h"] == a.quality), None) if a.quality else variants[0]
        if a.quality and not pick:
            die(f"Không có chất lượng {a.quality}p. Dùng --list để xem.")
        print(f"    → chọn: {pick['res'] or '?'} ({pick['bw'] // 1000} kbps)")
        confirm_hosts([pick["url"]], a.yes)
        base = pick["url"]
        print(f"[2] Tải playlist chất lượng: {mask(base)}")
        try:
            txt = http_get(base, limit=20_000_000).decode("utf-8", "replace")
        except RuntimeError as e:
            die(str(e))
    segs, init = parse_media(txt, base)
    if not segs:
        die("Playlist không có đoạn video nào (có thể link đã hết hạn hoặc là playlist rỗng).")

    # ---- báo cáo phân tích ----
    methods = {s["key"]["method"] for s in segs if s["key"]}
    bad = methods - {"AES-128"}
    exts = collections.Counter(os.path.splitext(urllib.parse.urlsplit(s["url"]).path)[1].lower() or "(không đuôi)" for s in segs)
    total = sum(s["dur"] for s in segs)
    print(f"\n=== PHÂN TÍCH ===\n  Số đoạn      : {len(segs)}  (~{int(total // 60)} phút {int(total % 60)} giây)")
    print(f"  Đuôi file đoạn: {dict(exts)}")
    print(f"  Mã hóa       : {', '.join(sorted(methods)) or 'KHÔNG'}")
    if bad:
        die(f"Mã hóa {', '.join(sorted(bad))} thường là DRM. Công cụ này không hỗ trợ và không vượt DRM.\n"
            "   Hãy dùng file gốc của bạn.")
    hosts = [s["url"] for s in segs] + ([init] if init else []) + [s["key"]["uri"] for s in segs if s["key"] and s["key"]["uri"]]
    confirm_hosts(hosts, a.yes)
    keys = {}
    for s in segs:
        k = s["key"]
        if k and k["uri"] not in keys:
            print(f"  Lấy khóa     : {mask(k['uri'])}")
            try:
                kb = http_get(k["uri"], limit=1024)
            except RuntimeError as e:
                die(str(e))
            if len(kb) != 16:
                die(f"Máy chủ trả về khóa dài {len(kb)} byte (cần 16). Có thể khóa cần đăng nhập/Referer: dùng --header.")
            keys[k["uri"]] = kb

    # ---- kiểm tra đoạn đầu: đây có thật là video không? ----
    try:
        first = fetch_segment(segs[0], keys)
    except RuntimeError as e:
        die(str(e))
    kind, off = sniff(first[:9000])
    print(f"  Đoạn đầu     : {len(first):,} byte → {LABEL.get(kind, kind)}" + (f", bỏ {off} byte đầu giả" if off else ""))
    disguised = exts and all(e in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".ico") for e in exts) and kind in VIDEO_KINDS
    if disguised:
        print("  ⚠ KẾT LUẬN: đây là HLS NGỤY TRANG — các đoạn đuôi ảnh nhưng bên trong là video thật.")
    if kind not in VIDEO_KINDS:
        print("  ✘ Đoạn đầu KHÔNG phải video" + (" (ảnh thật: có lẽ đây chỉ là ảnh minh họa)" if kind in ("jpeg", "png", "gif", "webp") else
              " - có thể chưa giải mã được/xáo trộn."))
        if a.check or not a.yes:
            if a.check:
                return
            if input("  Vẫn tiếp tục tải toàn bộ? [y/N] ").strip().lower() not in ("y", "yes"):
                die("Đã dừng.", 0)
    if a.check:
        print("\n✔ Chế độ --check: không ghép video. Bỏ --check để tải và ghép.")
        return

    ext = ".mp4" if (init or kind in ("fmp4", "mp4")) else ".ts"
    out = a.output or ("video-hls" + ext)
    if os.path.exists(out) and not a.force:
        die(f"File '{out}' đã tồn tại. Dùng -o tên_khác hoặc --force.")
    part = out + ".part"
    print(f"\n[3] Tải {len(segs)} đoạn → {out}")
    done, t0, warn = 0, time.time(), 0
    try:
        with open(part, "wb") as f, ThreadPoolExecutor(max_workers=max(1, min(a.workers, 8))) as ex:
            if init:
                f.write(http_get(init))
            window = collections.deque()
            it = iter(segs)
            def submit():
                s = next(it, None)
                if s is not None:
                    window.append(ex.submit(fetch_segment, s, keys))
            for _ in range(max(2, a.workers * 2)):
                submit()
            while window:
                data = window.popleft().result()
                submit()
                k, o = sniff(data[:9000])
                if k in VIDEO_KINDS:
                    data = data[o:]
                elif k in ("jpeg", "png", "gif", "webp"):
                    warn += 1
                    print(f"\n  ⚠ đoạn #{done + 1} là ảnh thật → bỏ qua")
                    done += 1
                    continue
                f.write(data)
                done += 1
                mb = f.tell() / 1e6
                print(f"\r    {done}/{len(segs)} đoạn · {mb:,.1f} MB · {mb / max(1e-6, time.time() - t0):.1f} MB/s   ", end="", flush=True)
        os.replace(part, out)
    except (RuntimeError, OSError) as e:
        if os.path.exists(part):
            os.remove(part)
        die(f"\nLỗi khi tải: {e}")
    h = hashlib.sha256()
    with open(out, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    print(f"\n\n✔ Xong: {out} ({os.path.getsize(out) / 1e6:,.1f} MB){f' · bỏ {warn} đoạn ảnh' if warn else ''}\n  SHA-256: {h.hexdigest()}")
    if ext == ".ts":
        print("  Mở bằng VLC/MPC-HC. Muốn .mp4 và đã có ffmpeg: ffmpeg -i \"%s\" -c copy video.mp4" % out)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        die("Đã dừng (Ctrl+C).", 130)
