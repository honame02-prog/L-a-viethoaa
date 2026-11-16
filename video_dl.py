#!/usr/bin/env python3
"""
video_dl.py — tải video từ link bài viết HTTPS.

Dán link bài viết bất kỳ → công cụ tự tìm video trong trang và cho bạn tải về.
Hỗ trợ video nhúng trong thẻ <video>, Open Graph, Twitter Card, JSON-LD,
link .mp4/.m3u8/.webm trong mã nguồn trang, và iframe nhúng.

Cách dùng:
  python video_dl.py <URL>                  # tìm video & tải
  python video_dl.py <URL> --list           # chỉ liệt kê, không tải
  python video_dl.py <URL> -o tenfile.mp4   # đặt tên file lưu
  python video_dl.py --serve                # mở giao diện web (mặc định cổng 8765)
  python video_dl.py --serve --port 9000    # đổi cổng

  Tùy chọn thêm:
  --header "Referer: https://..."           # thêm header (lặp được nhiều lần)
  --header "Cookie: abc=123"
  --quality 720                             # ưu tiên chất lượng gần nhất

An toàn:
  * Chỉ thư viện chuẩn Python + beautifulsoup4.
  * In mọi máy chủ sẽ kết nối và hỏi xác nhận [y/N] (CLI).
  * Chặn redirect sang máy chủ chưa xác nhận.
  * Kiểm tra chứng chỉ HTTPS. Che token khi hiển thị.
  * Không gửi dữ liệu cá nhân, không telemetry.
"""

import argparse
import html as html_mod
import http.server
import io
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# ────────────────── Cấu hình ──────────────────
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
VIDEO_EXTS = re.compile(r"\.(mp4|m4v|webm|ogv|ogg|mov|mkv|avi|ts|m3u8|mpd)(\?|#|$)", re.I)
VIDEO_MIME = re.compile(r"video/|application/x-mpegurl|application/dash\+xml", re.I)
ALLOWED_HOSTS = set()
EXTRA_HEADERS = {}

# ────────────────── Tiện ích ──────────────────

def mask_url(u):
    """Che token: chỉ hiện scheme + host + path."""
    p = urllib.parse.urlsplit(u)
    return f"{p.scheme}://{p.netloc}{p.path}" + ("?***" if p.query else "")


def hostkey(u):
    return urllib.parse.urlsplit(u).netloc.lower()


def resolve_url(base, href):
    if not href or href.startswith("data:") or href.startswith("blob:"):
        return None
    return urllib.parse.urljoin(base, href)


def fmt_size(n):
    if n is None or n < 0:
        return "?"
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}" if n != int(n) else f"{int(n)} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def die(msg, code=1):
    print(f"\n✘ {msg}", file=sys.stderr)
    sys.exit(code)


# ────────────────── HTTP an toàn ──────────────────

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        h = hostkey(newurl)
        if ALLOWED_HOSTS and h not in ALLOWED_HOSTS:
            raise urllib.error.URLError(f"Redirect tới máy chủ chưa xác nhận bị chặn: {h}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_opener():
    return urllib.request.build_opener(SafeRedirect())

OPENER = build_opener()


def http_get(url, headers=None, timeout=30, max_bytes=None):
    """GET an toàn, trả bytes."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    for k, v in EXTRA_HEADERS.items():
        req.add_header(k, v)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    resp = OPENER.open(req, timeout=timeout)
    if max_bytes:
        return resp.read(max_bytes), resp
    return resp.read(), resp


def http_head(url, timeout=15):
    """HEAD request, trả headers."""
    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", UA)
    for k, v in EXTRA_HEADERS.items():
        req.add_header(k, v)
    try:
        resp = OPENER.open(req, timeout=timeout)
        return dict(resp.headers)
    except Exception:
        return {}


# ────────────────── Trích xuất video ──────────────────

class VideoInfo:
    def __init__(self, url, source="", ext="", width=0, height=0, mime="", label=""):
        self.url = url
        self.source = source
        self.ext = ext or self._guess_ext(url)
        self.width = width
        self.height = height
        self.mime = mime
        self.label = label
        self.size = -1

    def _guess_ext(self, url):
        path = urllib.parse.urlsplit(url).path.lower()
        m = re.search(r"\.(mp4|m4v|webm|ogv|ogg|mov|mkv|avi|ts|m3u8|mpd)$", path)
        return m.group(1) if m else ""

    @property
    def quality_str(self):
        if self.height:
            return f"{self.height}p"
        if self.width:
            return f"{self.width}w"
        if self.label:
            return self.label
        return ""

    def __repr__(self):
        q = self.quality_str
        s = fmt_size(self.size) if self.size > 0 else ""
        parts = [self.ext.upper() or "?", q, s, f"[{self.source}]"]
        return " | ".join(p for p in parts if p)


def extract_videos(page_url, html_text):
    """Trích xuất tất cả video từ HTML."""
    videos = OrderedDict()

    def add(url, **kw):
        if not url or url in videos:
            return
        if not url.startswith(("http://", "https://")):
            return
        videos[url] = VideoInfo(url, **kw)

    if HAS_BS4:
        _extract_bs4(page_url, html_text, add)
    _extract_regex(page_url, html_text, add)

    return list(videos.values())


def _extract_bs4(page_url, html_text, add):
    soup = BeautifulSoup(html_text, "html.parser")

    # 1. <video src> và <source src>
    for tag in soup.find_all(["video", "source"]):
        src = tag.get("src")
        url = resolve_url(page_url, src)
        if url:
            w = _int(tag.get("width"))
            h = _int(tag.get("height"))
            mime = tag.get("type", "")
            label = tag.get("label", "") or tag.get("data-quality", "")
            add(url, source="video-tag", width=w, height=h, mime=mime, label=label)

    # 2. Open Graph meta
    for prop in ("og:video", "og:video:url", "og:video:secure_url"):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            url = resolve_url(page_url, tag["content"])
            if url:
                w = _int(_meta_val(soup, "og:video:width"))
                h = _int(_meta_val(soup, "og:video:height"))
                mime = _meta_val(soup, "og:video:type") or ""
                add(url, source="og:video", width=w, height=h, mime=mime)

    # 3. Twitter Card
    for name in ("twitter:player:stream", "twitter:player"):
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", property=name)
        if tag and tag.get("content"):
            url = resolve_url(page_url, tag["content"])
            if url:
                add(url, source="twitter-card")

    # 4. JSON-LD VideoObject
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            _extract_jsonld(page_url, data, add)
        except (json.JSONDecodeError, TypeError):
            pass

    # 5. <iframe> video nhúng
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src") or iframe.get("data-src")
        if src and _is_video_embed(src):
            url = resolve_url(page_url, src)
            if url:
                add(url, source="iframe")

    # 6. data-video-src, data-src trên các thẻ
    for tag in soup.find_all(attrs={"data-video-src": True}):
        url = resolve_url(page_url, tag["data-video-src"])
        if url:
            add(url, source="data-attr")
    for tag in soup.find_all(attrs={"data-src": True}):
        src = tag["data-src"]
        if VIDEO_EXTS.search(src):
            url = resolve_url(page_url, src)
            if url:
                add(url, source="data-src")


def _extract_jsonld(base_url, data, add):
    if isinstance(data, list):
        for item in data:
            _extract_jsonld(base_url, item, add)
        return
    if not isinstance(data, dict):
        return
    t = data.get("@type", "")
    if t in ("VideoObject", "Video") or "Video" in str(t):
        for key in ("contentUrl", "embedUrl", "url"):
            v = data.get(key)
            if v:
                url = resolve_url(base_url, v)
                if url:
                    w = _int(data.get("width"))
                    h = _int(data.get("height"))
                    add(url, source="json-ld", width=w, height=h)
        for enc in data.get("encoding", []) if isinstance(data.get("encoding"), list) else [data.get("encoding")] if data.get("encoding") else []:
            if isinstance(enc, dict) and enc.get("contentUrl"):
                url = resolve_url(base_url, enc["contentUrl"])
                if url:
                    add(url, source="json-ld/encoding")
    for v in data.values():
        if isinstance(v, (dict, list)):
            _extract_jsonld(base_url, v, add)


def _extract_regex(page_url, html_text, add):
    """Quét regex tìm URL video trong mã nguồn."""
    # URL trực tiếp .mp4, .webm, .m3u8, ...
    for m in re.finditer(r'https?://[^\s"\'<>\)]+?\.(?:mp4|m4v|webm|m3u8|mpd|mov|ts)(?:\?[^\s"\'<>\)]*)?', html_text):
        url = html_mod.unescape(m.group(0).rstrip(",;"))
        add(url, source="regex-url")

    # Pattern: "videoUrl":"...", "video_url":"...", "src":"...mp4..."
    for m in re.finditer(r'["\']((?:video[_-]?(?:url|src|file|link)|src|file|source|stream[_-]?url|media[_-]?url|content[_-]?url|hls[_-]?url|dash[_-]?url|playback[_-]?url))["\']:\s*["\'](https?://[^\s"\']+?)["\']', html_text, re.I):
        url = html_mod.unescape(m.group(2))
        if VIDEO_EXTS.search(url) or "video" in m.group(1).lower():
            add(url, source=f"js:{m.group(1)}")

    # Pattern JSON: {"url":"...","type":"video/mp4",...}
    for m in re.finditer(r'\{[^{}]*"url"\s*:\s*"(https?://[^"]+)"[^{}]*"type"\s*:\s*"(video/[^"]+)"[^{}]*\}', html_text):
        add(html_mod.unescape(m.group(1)), source="json-inline", mime=m.group(2))
    # Reversed order
    for m in re.finditer(r'\{[^{}]*"type"\s*:\s*"(video/[^"]+)"[^{}]*"url"\s*:\s*"(https?://[^"]+)"[^{}]*\}', html_text):
        add(html_mod.unescape(m.group(2)), source="json-inline", mime=m.group(1))


def _is_video_embed(src):
    """Kiểm tra URL iframe có phải embed video không."""
    embed_patterns = [
        r"youtube\.com/embed/",
        r"youtu\.be/",
        r"player\.vimeo\.com/",
        r"dailymotion\.com/embed/",
        r"facebook\.com/plugins/video",
        r"tiktok\.com/embed",
        r"/embed/?\?",
        r"/player/?\?",
    ]
    return any(re.search(p, src, re.I) for p in embed_patterns)


def _meta_val(soup, prop):
    tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
    return tag["content"] if tag and tag.get("content") else None


def _int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


# ────────────────── Phân giải thông tin video ──────────────────

def resolve_video_info(videos):
    """Thử HEAD request để lấy kích thước file."""
    for v in videos:
        try:
            h = http_head(v.url, timeout=10)
            cl = h.get("Content-Length") or h.get("content-length")
            if cl:
                v.size = int(cl)
            ct = h.get("Content-Type") or h.get("content-type") or ""
            if not v.mime and ct:
                v.mime = ct.split(";")[0].strip()
            if not v.ext and v.mime:
                ext_map = {
                    "video/mp4": "mp4", "video/webm": "webm", "video/ogg": "ogv",
                    "video/quicktime": "mov", "application/x-mpegurl": "m3u8",
                    "application/vnd.apple.mpegurl": "m3u8", "application/dash+xml": "mpd",
                }
                v.ext = ext_map.get(v.mime, "")
        except Exception:
            pass


# ────────────────── Tải video ──────────────────

def download_video(url, output_path, page_url=None):
    """Tải video với progress bar."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    if page_url:
        req.add_header("Referer", page_url)
    for k, v in EXTRA_HEADERS.items():
        req.add_header(k, v)

    resp = OPENER.open(req, timeout=60)
    total = int(resp.headers.get("Content-Length", 0))
    downloaded = 0
    start = time.time()

    with open(output_path, "wb") as f:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            elapsed = time.time() - start
            speed = downloaded / elapsed if elapsed > 0 else 0
            if total > 0:
                pct = downloaded * 100 / total
                bar = "█" * int(pct / 2.5) + "░" * (40 - int(pct / 2.5))
                print(f"\r  [{bar}] {pct:.1f}% | {fmt_size(downloaded)}/{fmt_size(total)} | {fmt_size(speed)}/s", end="", flush=True)
            else:
                print(f"\r  Đã tải: {fmt_size(downloaded)} | {fmt_size(speed)}/s", end="", flush=True)
    print()
    return downloaded


# ────────────────── Giao diện CLI ──────────────────

def ask_confirm_hosts(hosts):
    """Hỏi xác nhận các máy chủ sẽ kết nối."""
    print(f"\n{'─' * 50}")
    print("Các máy chủ sẽ kết nối:")
    for i, h in enumerate(sorted(hosts), 1):
        print(f"  {i}. {h}")
    print(f"{'─' * 50}")
    ans = input("Cho phép kết nối? [y/N]: ").strip().lower()
    if ans not in ("y", "yes"):
        die("Đã hủy.")
    ALLOWED_HOSTS.update(hosts)


def cli_main():
    parser = argparse.ArgumentParser(description="Tải video từ link bài viết HTTPS")
    parser.add_argument("url", nargs="?", help="URL bài viết chứa video")
    parser.add_argument("--list", action="store_true", help="Chỉ liệt kê video, không tải")
    parser.add_argument("-o", "--output", help="Tên file lưu")
    parser.add_argument("--quality", type=int, help="Ưu tiên chất lượng (VD: 720)")
    parser.add_argument("--header", action="append", default=[], help="Header tùy chỉnh (lặp được)")
    parser.add_argument("--serve", action="store_true", help="Chạy giao diện web")
    parser.add_argument("--port", type=int, default=8765, help="Cổng web server (mặc định 8765)")
    parser.add_argument("--no-confirm", action="store_true", help="Bỏ qua hỏi xác nhận host (dùng trong script)")
    args = parser.parse_args()

    # Parse headers
    for h in args.header:
        if ":" in h:
            k, v = h.split(":", 1)
            EXTRA_HEADERS[k.strip()] = v.strip()

    if args.serve:
        start_server(args.port)
        return

    if not args.url:
        args.url = input("Dán link bài viết: ").strip()
    if not args.url:
        die("Chưa nhập URL.")
    if not args.url.startswith(("http://", "https://")):
        die("Chỉ hỗ trợ URL http:// hoặc https://")

    print(f"\n⏳ Đang tải trang: {mask_url(args.url)}")

    # Xác nhận host trang
    page_host = hostkey(args.url)
    if not args.no_confirm:
        ask_confirm_hosts({page_host})
    else:
        ALLOWED_HOSTS.add(page_host)

    # Tải trang
    try:
        page_bytes, resp = http_get(args.url, max_bytes=10 * 1024 * 1024)
    except Exception as e:
        die(f"Không tải được trang: {e}")

    ct = resp.headers.get("Content-Type", "")
    enc = "utf-8"
    m = re.search(r"charset=([^\s;]+)", ct)
    if m:
        enc = m.group(1)

    # Nếu URL trỏ thẳng vào file video
    if VIDEO_MIME.search(ct) or VIDEO_EXTS.search(args.url):
        print("→ URL trỏ thẳng vào file video.")
        videos = [VideoInfo(args.url, source="direct-url")]
    else:
        html_text = page_bytes.decode(enc, errors="replace")
        print(f"✓ Đã tải trang ({fmt_size(len(page_bytes))}). Đang phân tích...")
        videos = extract_videos(args.url, html_text)

    if not videos:
        die("Không tìm thấy video nào trong trang.")

    # Thu thập tất cả hosts mới
    new_hosts = set()
    for v in videos:
        h = hostkey(v.url)
        if h not in ALLOWED_HOSTS:
            new_hosts.add(h)
    if new_hosts and not args.no_confirm:
        print(f"\nTìm thấy {len(videos)} video trên {len(new_hosts)} máy chủ mới.")
        ask_confirm_hosts(new_hosts)

    # Lấy thông tin kích thước
    print("⏳ Đang kiểm tra thông tin video...")
    resolve_video_info(videos)

    # Hiển thị danh sách
    print(f"\n{'═' * 60}")
    print(f" Tìm thấy {len(videos)} video:")
    print(f"{'═' * 60}")
    for i, v in enumerate(videos, 1):
        print(f"  [{i}] {v}")
        print(f"      {mask_url(v.url)}")
    print(f"{'═' * 60}")

    if args.list:
        return

    # Chọn video
    if len(videos) == 1:
        choice = 0
    elif args.quality:
        # Tìm chất lượng gần nhất
        def q_diff(v):
            return abs(v.height - args.quality) if v.height else 9999
        choice = min(range(len(videos)), key=lambda i: q_diff(videos[i]))
        print(f"→ Chọn tự động gần {args.quality}p: [{choice + 1}]")
    else:
        try:
            choice = int(input(f"\nChọn video [1-{len(videos)}]: ")) - 1
            if choice < 0 or choice >= len(videos):
                die("Số không hợp lệ.")
        except (ValueError, EOFError):
            die("Đã hủy.")

    video = videos[choice]

    # Tên file
    if args.output:
        out_name = args.output
    else:
        slug = re.sub(r"[^\w\-.]", "_", urllib.parse.urlsplit(video.url).path.split("/")[-1] or "video")
        if not slug.endswith(f".{video.ext}") and video.ext:
            slug = f"{slug}.{video.ext}"
        if slug == f".{video.ext}" or not slug.strip("._"):
            slug = f"video_{int(time.time())}.{video.ext or 'mp4'}"
        out_name = slug

    print(f"\n⏳ Đang tải: {mask_url(video.url)}")
    print(f"   Lưu tại: {out_name}")

    try:
        size = download_video(video.url, out_name, page_url=args.url)
        print(f"✓ Hoàn tất! Đã tải {fmt_size(size)} → {out_name}")
    except Exception as e:
        die(f"Lỗi khi tải: {e}")


# ────────────────── Giao diện Web ──────────────────

WEB_HTML = r"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tải video từ link bài viết</title>
<style>
:root {
  --bg: #f8f9fb; --fg: #1a1d23; --card: #ffffff; --border: #e2e5ea;
  --accent: #2563eb; --accent-hover: #1d4ed8; --accent-light: #dbeafe;
  --green: #16a34a; --green-bg: #dcfce7; --red: #dc2626; --red-bg: #fef2f2;
  --gray: #6b7280; --shadow: 0 1px 3px rgba(0,0,0,.08);
  --radius: 12px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1117; --fg: #e4e6eb; --card: #1a1d25; --border: #2d3140;
    --accent: #5b92ff; --accent-hover: #7aabff; --accent-light: #1e2a4a;
    --green: #4ade80; --green-bg: #0a2618; --red: #f87171; --red-bg: #2a1010;
    --gray: #9ca3af; --shadow: 0 1px 3px rgba(0,0,0,.3);
  }
}
* { box-sizing: border-box; margin: 0 }
body { background: var(--bg); color: var(--fg); font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.container { max-width: 720px; margin: 0 auto; padding: 32px 16px; }
h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }
.subtitle { color: var(--gray); margin-bottom: 24px; font-size: .95rem; }
.card {
  background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 20px; margin-bottom: 16px; box-shadow: var(--shadow);
}
.input-group { display: flex; gap: 8px; }
.input-group input {
  flex: 1; padding: 12px 16px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg); color: var(--fg); font: inherit; outline: none;
  transition: border-color .2s;
}
.input-group input:focus { border-color: var(--accent); }
.btn {
  padding: 12px 24px; border: none; border-radius: 8px; font: inherit; font-weight: 600;
  cursor: pointer; transition: all .2s; display: inline-flex; align-items: center; gap: 6px;
  white-space: nowrap;
}
.btn-primary { background: var(--accent); color: #fff; }
.btn-primary:hover { background: var(--accent-hover); }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.btn-sm { padding: 8px 16px; font-size: .9rem; }
.btn-outline { background: transparent; color: var(--accent); border: 1px solid var(--accent); }
.btn-outline:hover { background: var(--accent-light); }
.spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid #fff4;
  border-top-color: #fff; border-radius: 50%; animation: spin .6s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.status { padding: 12px 16px; border-radius: 8px; margin: 12px 0; font-size: .9rem; }
.status-info { background: var(--accent-light); color: var(--accent); }
.status-ok { background: var(--green-bg); color: var(--green); }
.status-err { background: var(--red-bg); color: var(--red); }
.video-list { display: flex; flex-direction: column; gap: 10px; margin-top: 12px; }
.video-item {
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 14px;
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  transition: border-color .2s;
}
.video-item:hover { border-color: var(--accent); }
.video-info { flex: 1; min-width: 0; }
.video-badges { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 4px; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: .75rem;
  font-weight: 600; text-transform: uppercase;
}
.badge-ext { background: var(--accent-light); color: var(--accent); }
.badge-quality { background: var(--green-bg); color: var(--green); }
.badge-size { background: var(--bg); border: 1px solid var(--border); color: var(--gray); }
.video-url {
  font-size: .8rem; color: var(--gray); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; max-width: 100%;
}
.header-row { display: flex; gap: 8px; margin-top: 8px; }
.header-row input { flex: 1; padding: 8px 12px; border: 1px solid var(--border);
  border-radius: 6px; background: var(--bg); color: var(--fg); font-size: .9rem; }
.header-row input::placeholder { color: var(--gray); }
.help { margin-top: 16px; font-size: .85rem; color: var(--gray); line-height: 1.5; }
.help b { color: var(--fg); }
.progress-bar { width: 100%; height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; margin-top: 8px; }
.progress-fill { height: 100%; background: var(--accent); transition: width .3s; border-radius: 3px; }
footer { text-align: center; margin-top: 32px; font-size: .8rem; color: var(--gray); }
</style>
</head>
<body>
<div class="container">
  <h1>Tải video từ link bài viết</h1>
  <p class="subtitle">Dán link bài viết HTTPS &rarr; tự động tìm và tải video về máy.</p>

  <div class="card">
    <div class="input-group">
      <input type="url" id="urlInput" placeholder="Dán link bài viết vào đây..." autofocus>
      <button class="btn btn-primary" id="scanBtn" onclick="scanUrl()">
        <span id="scanText">Tìm video</span>
      </button>
    </div>
    <details style="margin-top: 10px">
      <summary style="cursor:pointer; font-size:.9rem; color:var(--gray)">Tùy chọn nâng cao</summary>
      <div id="headers">
        <div class="header-row">
          <input placeholder="Tên header (VD: Referer)" class="hdr-key">
          <input placeholder="Giá trị" class="hdr-val">
        </div>
      </div>
      <button class="btn btn-outline btn-sm" style="margin-top:6px" onclick="addHeader()">+ Thêm header</button>
    </details>
  </div>

  <div id="statusArea"></div>
  <div id="resultArea"></div>

  <div class="help">
    <b>Hướng dẫn:</b> Dán link bài viết từ bất kỳ trang web nào. Công cụ sẽ phân tích trang
    và tìm tất cả video nhúng trong đó (thẻ &lt;video&gt;, Open Graph, JSON-LD, link trực tiếp...).
    Mọi thứ xử lý trên máy của bạn, qua server Python cục bộ này.
  </div>

  <footer>video_dl.py &mdash; chỉ kết nối tới trang bạn chọn, không gửi dữ liệu đi đâu khác.</footer>
</div>

<script>
const statusArea = document.getElementById("statusArea");
const resultArea = document.getElementById("resultArea");

function showStatus(msg, type) {
  statusArea.innerHTML = `<div class="status status-${type}">${msg}</div>`;
}

function addHeader() {
  const div = document.getElementById("headers");
  const row = document.createElement("div");
  row.className = "header-row";
  row.innerHTML = '<input placeholder="Tên header" class="hdr-key"><input placeholder="Giá trị" class="hdr-val">';
  div.appendChild(row);
}

function getHeaders() {
  const headers = {};
  document.querySelectorAll(".header-row").forEach(row => {
    const k = row.querySelector(".hdr-key").value.trim();
    const v = row.querySelector(".hdr-val").value.trim();
    if (k && v) headers[k] = v;
  });
  return headers;
}

async function scanUrl() {
  const url = document.getElementById("urlInput").value.trim();
  if (!url) { showStatus("Chưa nhập URL.", "err"); return; }
  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    showStatus("Chỉ hỗ trợ URL http:// hoặc https://", "err"); return;
  }

  const btn = document.getElementById("scanBtn");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Đang tìm...';
  resultArea.innerHTML = "";
  showStatus("Đang tải trang và phân tích...", "info");

  try {
    const resp = await fetch("/api/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, headers: getHeaders() })
    });
    const data = await resp.json();
    if (data.error) {
      showStatus("Lỗi: " + data.error, "err");
    } else if (!data.videos || data.videos.length === 0) {
      showStatus("Không tìm thấy video nào trong trang.", "err");
    } else {
      showStatus(`Tìm thấy ${data.videos.length} video!`, "ok");
      renderVideos(data.videos, url);
    }
  } catch (e) {
    showStatus("Loi ket noi server: " + e.message, "err");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "Tìm video";
  }
}

function renderVideos(videos, pageUrl) {
  let html = '<div class="video-list">';
  videos.forEach((v, i) => {
    const badges = [];
    if (v.ext) badges.push(`<span class="badge badge-ext">${v.ext}</span>`);
    if (v.quality) badges.push(`<span class="badge badge-quality">${v.quality}</span>`);
    if (v.size && v.size !== "?") badges.push(`<span class="badge badge-size">${v.size}</span>`);
    badges.push(`<span class="badge" style="background:var(--bg);border:1px solid var(--border);color:var(--gray)">${v.source}</span>`);

    html += `
      <div class="video-item">
        <div class="video-info">
          <div class="video-badges">${badges.join("")}</div>
          <div class="video-url" title="${v.masked_url}">${v.masked_url}</div>
          <div id="prog-${i}"></div>
        </div>
        <button class="btn btn-primary btn-sm" id="dlbtn-${i}" onclick="downloadVideo(${i}, '${encodeURIComponent(v.url)}', '${encodeURIComponent(pageUrl)}')">Tải về</button>
      </div>`;
  });
  html += '</div>';
  resultArea.innerHTML = html;
}

async function downloadVideo(idx, encodedUrl, encodedPage) {
  const btn = document.getElementById("dlbtn-" + idx);
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  const progDiv = document.getElementById("prog-" + idx);
  progDiv.innerHTML = '<div class="progress-bar"><div class="progress-fill" id="pf-' + idx + '" style="width:0%"></div></div>';

  try {
    const resp = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: decodeURIComponent(encodedUrl),
        page_url: decodeURIComponent(encodedPage),
        headers: getHeaders()
      })
    });

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.error || "Lỗi tải video");
    }

    const cd = resp.headers.get("Content-Disposition") || "";
    let filename = "video.mp4";
    const m = cd.match(/filename="?([^";\n]+)"?/);
    if (m) filename = m[1];

    const total = parseInt(resp.headers.get("Content-Length") || "0");
    const reader = resp.body.getReader();
    const chunks = [];
    let received = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      if (total > 0) {
        const pct = Math.min(100, received * 100 / total);
        document.getElementById("pf-" + idx).style.width = pct + "%";
      }
    }

    const blob = new Blob(chunks);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);

    btn.innerHTML = "Đã tải!";
    btn.style.background = "var(--green)";
    progDiv.innerHTML = "";
  } catch (e) {
    btn.innerHTML = "Lỗi!";
    btn.style.background = "var(--red)";
    progDiv.innerHTML = `<div class="status status-err" style="margin:4px 0">${e.message}</div>`;
  }
}

document.getElementById("urlInput").addEventListener("keydown", e => {
  if (e.key === "Enter") scanUrl();
});
</script>
</body>
</html>"""


class VideoDLHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # im lặng

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            body = WEB_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_response({"error": "JSON không hợp lệ"}, 400)
            return

        if self.path == "/api/extract":
            self._handle_extract(data)
        elif self.path == "/api/download":
            self._handle_download(data)
        else:
            self.send_error(404)

    def _handle_extract(self, data):
        url = data.get("url", "").strip()
        if not url:
            self._json_response({"error": "Chưa nhập URL"}, 400)
            return
        if not url.startswith(("http://", "https://")):
            self._json_response({"error": "Chỉ hỗ trợ http:// hoặc https://"}, 400)
            return

        # Web mode: tự động cho phép tất cả host (không hỏi CLI)
        ALLOWED_HOSTS.add(hostkey(url))

        # Custom headers
        for k, v in data.get("headers", {}).items():
            EXTRA_HEADERS[k] = v

        try:
            page_bytes, resp = http_get(url, max_bytes=10 * 1024 * 1024)
        except Exception as e:
            self._json_response({"error": f"Không tải được trang: {e}"})
            return

        ct = resp.headers.get("Content-Type", "")
        enc = "utf-8"
        m = re.search(r"charset=([^\s;]+)", ct)
        if m:
            enc = m.group(1)

        if VIDEO_MIME.search(ct) or VIDEO_EXTS.search(url):
            videos = [VideoInfo(url, source="direct-url")]
        else:
            html_text = page_bytes.decode(enc, errors="replace")
            videos = extract_videos(url, html_text)

        # Cho phép hosts video
        for v in videos:
            ALLOWED_HOSTS.add(hostkey(v.url))

        resolve_video_info(videos)

        result = []
        for v in videos:
            result.append({
                "url": v.url,
                "masked_url": mask_url(v.url),
                "ext": v.ext,
                "quality": v.quality_str,
                "size": fmt_size(v.size) if v.size > 0 else "?",
                "mime": v.mime,
                "source": v.source,
            })

        self._json_response({"videos": result, "count": len(result)})

    def _handle_download(self, data):
        url = data.get("url", "").strip()
        page_url = data.get("page_url", "")
        if not url:
            self._json_response({"error": "Thiếu URL video"}, 400)
            return

        ALLOWED_HOSTS.add(hostkey(url))

        for k, v in data.get("headers", {}).items():
            EXTRA_HEADERS[k] = v

        # Tên file từ URL
        path_part = urllib.parse.urlsplit(url).path.split("/")[-1] or "video"
        filename = re.sub(r"[^\w\-.]", "_", path_part)
        if not re.search(r"\.\w+$", filename):
            filename += ".mp4"

        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", UA)
            if page_url:
                req.add_header("Referer", page_url)
            for k, v in EXTRA_HEADERS.items():
                req.add_header(k, v)

            resp = OPENER.open(req, timeout=60)
            total = resp.headers.get("Content-Length", "")
            ct = resp.headers.get("Content-Type", "application/octet-stream")

            self.send_response(200)
            self.send_header("Content-Type", ct)
            if total:
                self.send_header("Content-Length", total)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self._cors()
            self.end_headers()

            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

        except Exception as e:
            self._json_response({"error": str(e)}, 500)


def start_server(port):
    if not HAS_BS4:
        print("⚠ beautifulsoup4 chưa cài. Chạy: pip install beautifulsoup4")
        print("  (Vẫn hoạt động nhưng chỉ dùng regex, kém chính xác hơn)")

    server = http.server.HTTPServer(("127.0.0.1", port), VideoDLHandler)
    print(f"""
╔══════════════════════════════════════════════════╗
║  Tải video từ link bài viết                      ║
║  ─────────────────────────────────────────────   ║
║  Mở trình duyệt:  http://127.0.0.1:{port:<5}       ║
║  Nhấn Ctrl+C để tắt.                            ║
║                                                  ║
║  Chỉ chạy trên máy bạn. Không gửi gì ra ngoài. ║
╚══════════════════════════════════════════════════╝
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n✓ Đã tắt server.")
        server.server_close()


# ────────────────── Main ──────────────────

if __name__ == "__main__":
    cli_main()
