package vn.videodl;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.regex.*;

import javax.net.ssl.HttpsURLConnection;

/**
 * Cầu nối Java ↔ JavaScript: tải trang, trích xuất video, tải file về Download.
 */
public class Bridge {
    private final Context ctx;
    private static final String UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36";

    Bridge(Context c) { ctx = c; }

    // ──── Tải trang và trích xuất video ────

    @JavascriptInterface
    public String extractVideos(String pageUrl, String headersJson) {
        try {
            HttpURLConnection conn = openConn(pageUrl, headersJson);
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(30000);
            conn.setInstanceFollowRedirects(true);
            int code = conn.getResponseCode();
            if (code < 200 || code >= 400) return "{\"error\":\"HTTP " + code + "\"}";

            String ct = conn.getContentType();
            if (ct == null) ct = "";
            String charset = "UTF-8";
            Matcher cm = Pattern.compile("charset=([\\w-]+)").matcher(ct);
            if (cm.find()) charset = cm.group(1);

            // Đọc tối đa 10 MB
            InputStream is = conn.getInputStream();
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] tmp = new byte[8192];
            int n, total = 0;
            while ((n = is.read(tmp)) > 0 && total < 10_000_000) {
                buf.write(tmp, 0, n);
                total += n;
            }
            is.close();
            conn.disconnect();

            String html = buf.toString(charset);
            List<Map<String, String>> videos = extract(pageUrl, html);

            // Lấy thông tin kích thước (tối đa 10 video)
            int limit = Math.min(videos.size(), 10);
            for (int i = 0; i < limit; i++) {
                resolveInfo(videos.get(i), headersJson);
            }

            return toJson(videos);
        } catch (Exception e) {
            return "{\"error\":\"" + esc(e.getMessage()) + "\"}";
        }
    }

    // ──── Tải video ────

    @JavascriptInterface
    public String downloadVideo(String videoUrl, String pageUrl, String filename, String headersJson) {
        try {
            filename = filename.replaceAll("[\\\\/:*?\"<>|]", "_");
            if (filename.isEmpty()) filename = "video.mp4";

            HttpURLConnection conn = openConn(videoUrl, headersJson);
            if (pageUrl != null && !pageUrl.isEmpty()) {
                conn.setRequestProperty("Referer", pageUrl);
            }
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(60000);
            conn.setInstanceFollowRedirects(true);
            int code = conn.getResponseCode();
            if (code < 200 || code >= 400) return "{\"error\":\"HTTP " + code + "\"}";

            long contentLength = conn.getContentLengthLong();
            InputStream is = conn.getInputStream();

            OutputStream os;
            Uri uri = null;
            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues v = new ContentValues();
                v.put(MediaStore.Downloads.DISPLAY_NAME, filename);
                v.put(MediaStore.Downloads.MIME_TYPE, guessMime(filename));
                v.put(MediaStore.Downloads.IS_PENDING, 1);
                ContentResolver cr = ctx.getContentResolver();
                uri = cr.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
                if (uri == null) return "{\"error\":\"Không tạo được file\"}";
                os = cr.openOutputStream(uri);
            } else {
                File dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
                dir.mkdirs();
                os = new FileOutputStream(new File(dir, filename));
            }

            byte[] tmp = new byte[65536];
            int n;
            long downloaded = 0;
            while ((n = is.read(tmp)) > 0) {
                os.write(tmp, 0, n);
                downloaded += n;
            }
            os.close();
            is.close();
            conn.disconnect();

            if (uri != null && Build.VERSION.SDK_INT >= 29) {
                ContentValues v = new ContentValues();
                v.put(MediaStore.Downloads.IS_PENDING, 0);
                ctx.getContentResolver().update(uri, v, null, null);
            }

            return "{\"ok\":true,\"size\":" + downloaded + ",\"file\":\"" + esc(filename) + "\"}";
        } catch (Exception e) {
            return "{\"error\":\"" + esc(e.getMessage()) + "\"}";
        }
    }

    // ──── Trích xuất video từ HTML ────

    private List<Map<String, String>> extract(String baseUrl, String html) {
        LinkedHashMap<String, Map<String, String>> found = new LinkedHashMap<>();

        // 1. <video src="..."> và <source src="...">
        findTag(html, baseUrl, found, "<video[^>]*\\ssrc=[\"']([^\"']+)[\"']", "video-tag");
        findTag(html, baseUrl, found, "<source[^>]*\\ssrc=[\"']([^\"']+)[\"']", "video-tag");

        // 2. og:video meta
        findMeta(html, baseUrl, found, "og:video", "og:video");
        findMeta(html, baseUrl, found, "og:video:url", "og:video");
        findMeta(html, baseUrl, found, "og:video:secure_url", "og:video");

        // 3. Twitter Card
        findMeta(html, baseUrl, found, "twitter:player:stream", "twitter-card");

        // 4. JSON-LD contentUrl
        Pattern jsonld = Pattern.compile("\"contentUrl\"\\s*:\\s*\"(https?://[^\"]+)\"", Pattern.CASE_INSENSITIVE);
        Matcher m = jsonld.matcher(html);
        while (m.find()) addVideo(found, resolve(baseUrl, unescape(m.group(1))), "json-ld");

        // 5. URL trực tiếp .mp4, .webm, .m3u8
        Pattern direct = Pattern.compile("https?://[^\\s\"'<>)]+?\\.(?:mp4|m4v|webm|m3u8|mpd|mov|ts)(?:\\?[^\\s\"'<>)]*)?");
        m = direct.matcher(html);
        while (m.find()) addVideo(found, unescape(m.group(0).replaceAll("[,;]+$", "")), "regex-url");

        // 6. JSON "video_url":"...", "videoUrl":"...", "src":"...mp4..."
        Pattern jsonPat = Pattern.compile(
            "[\"'](?:video[_-]?(?:url|src|file|link)|src|file|source|stream[_-]?url|media[_-]?url|hls[_-]?url|playback[_-]?url)" +
            "[\"']\\s*:\\s*[\"'](https?://[^\\s\"']+?)[\"']", Pattern.CASE_INSENSITIVE);
        m = jsonPat.matcher(html);
        while (m.find()) {
            String u = unescape(m.group(1));
            if (isVideoUrl(u)) addVideo(found, u, "js-var");
        }

        // 7. data-video-src
        Pattern dataSrc = Pattern.compile("data-video-src=[\"']([^\"']+)[\"']", Pattern.CASE_INSENSITIVE);
        m = dataSrc.matcher(html);
        while (m.find()) addVideo(found, resolve(baseUrl, unescape(m.group(1))), "data-attr");

        // 8. iframe embed
        Pattern iframe = Pattern.compile("<iframe[^>]*\\ssrc=[\"']([^\"']+)[\"']", Pattern.CASE_INSENSITIVE);
        m = iframe.matcher(html);
        while (m.find()) {
            String src = unescape(m.group(1));
            if (isVideoEmbed(src)) addVideo(found, resolve(baseUrl, src), "iframe");
        }

        return new ArrayList<>(found.values());
    }

    private void findTag(String html, String base, Map<String, Map<String, String>> found, String regex, String source) {
        Matcher m = Pattern.compile(regex, Pattern.CASE_INSENSITIVE).matcher(html);
        while (m.find()) addVideo(found, resolve(base, unescape(m.group(1))), source);
    }

    private void findMeta(String html, String base, Map<String, Map<String, String>> found, String prop, String source) {
        // property="..." content="..."
        Pattern p1 = Pattern.compile("(?:property|name)=[\"']" + Pattern.quote(prop) + "[\"'][^>]*content=[\"']([^\"']+)[\"']", Pattern.CASE_INSENSITIVE);
        Pattern p2 = Pattern.compile("content=[\"']([^\"']+)[\"'][^>]*(?:property|name)=[\"']" + Pattern.quote(prop) + "[\"']", Pattern.CASE_INSENSITIVE);
        Matcher m = p1.matcher(html);
        while (m.find()) addVideo(found, resolve(base, unescape(m.group(1))), source);
        m = p2.matcher(html);
        while (m.find()) addVideo(found, resolve(base, unescape(m.group(1))), source);
    }

    private void addVideo(Map<String, Map<String, String>> found, String url, String source) {
        if (url == null || url.isEmpty() || found.containsKey(url)) return;
        if (!url.startsWith("http://") && !url.startsWith("https://")) return;
        Map<String, String> info = new HashMap<>();
        info.put("url", url);
        info.put("masked_url", maskUrl(url));
        info.put("source", source);
        info.put("ext", guessExt(url));
        info.put("quality", "");
        info.put("size", "?");
        info.put("mime", "");
        found.put(url, info);
    }

    // ──── Thông tin video (HEAD request) ────

    private void resolveInfo(Map<String, String> v, String headersJson) {
        try {
            HttpURLConnection conn = openConn(v.get("url"), headersJson);
            conn.setRequestMethod("HEAD");
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(8000);
            conn.setInstanceFollowRedirects(true);
            int code = conn.getResponseCode();
            if (code >= 200 && code < 400) {
                long cl = conn.getContentLengthLong();
                if (cl > 0) v.put("size", fmtSize(cl));
                String ct = conn.getContentType();
                if (ct != null) {
                    v.put("mime", ct.split(";")[0].trim());
                    if (v.get("ext").isEmpty()) {
                        String ext = mimeToExt(ct);
                        if (!ext.isEmpty()) v.put("ext", ext);
                    }
                }
            }
            conn.disconnect();
        } catch (Exception ignored) {}
    }

    // ──── Tiện ích ────

    private HttpURLConnection openConn(String url, String headersJson) throws Exception {
        URL u = new URL(url);
        HttpURLConnection conn;
        if ("https".equals(u.getProtocol())) {
            conn = (HttpsURLConnection) u.openConnection();
        } else {
            conn = (HttpURLConnection) u.openConnection();
        }
        conn.setRequestProperty("User-Agent", UA);
        if (headersJson != null && !headersJson.isEmpty() && !headersJson.equals("{}")) {
            try {
                // Parse simple {"key":"value"} JSON
                Matcher m = Pattern.compile("\"([^\"]+)\"\\s*:\\s*\"([^\"]+)\"").matcher(headersJson);
                while (m.find()) conn.setRequestProperty(m.group(1), m.group(2));
            } catch (Exception ignored) {}
        }
        return conn;
    }

    private static String maskUrl(String u) {
        try {
            URL url = new URL(u);
            String base = url.getProtocol() + "://" + url.getHost() + url.getPath();
            return url.getQuery() != null ? base + "?***" : base;
        } catch (Exception e) { return u; }
    }

    private static String guessExt(String url) {
        try {
            String path = new URL(url).getPath().toLowerCase();
            Matcher m = Pattern.compile("\\.(mp4|m4v|webm|ogv|ogg|mov|mkv|avi|ts|m3u8|mpd)$").matcher(path);
            return m.find() ? m.group(1) : "";
        } catch (Exception e) { return ""; }
    }

    private static boolean isVideoUrl(String url) {
        return Pattern.compile("\\.(mp4|m4v|webm|ogv|ogg|mov|mkv|avi|ts|m3u8|mpd)(\\?|#|$)", Pattern.CASE_INSENSITIVE)
            .matcher(url).find() || url.contains("video");
    }

    private static boolean isVideoEmbed(String src) {
        String[] patterns = {"youtube.com/embed/", "youtu.be/", "player.vimeo.com/",
            "dailymotion.com/embed/", "facebook.com/plugins/video", "tiktok.com/embed",
            "/embed/?", "/player/?"};
        String lower = src.toLowerCase();
        for (String p : patterns) if (lower.contains(p)) return true;
        return false;
    }

    private static String resolve(String base, String href) {
        if (href == null || href.startsWith("data:") || href.startsWith("blob:")) return null;
        if (href.startsWith("http://") || href.startsWith("https://")) return href;
        try {
            return new URL(new URL(base), href).toString();
        } catch (Exception e) { return null; }
    }

    private static String unescape(String s) {
        return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", "\"").replace("&#39;", "'").replace("\\/", "/");
    }

    private static String fmtSize(long n) {
        if (n < 1024) return n + " B";
        if (n < 1024 * 1024) return String.format("%.1f KB", n / 1024.0);
        if (n < 1024 * 1024 * 1024) return String.format("%.1f MB", n / (1024.0 * 1024));
        return String.format("%.1f GB", n / (1024.0 * 1024 * 1024));
    }

    private static String mimeToExt(String ct) {
        if (ct == null) return "";
        ct = ct.split(";")[0].trim().toLowerCase();
        switch (ct) {
            case "video/mp4": return "mp4";
            case "video/webm": return "webm";
            case "video/ogg": return "ogv";
            case "video/quicktime": return "mov";
            case "application/x-mpegurl":
            case "application/vnd.apple.mpegurl": return "m3u8";
            case "application/dash+xml": return "mpd";
            default: return "";
        }
    }

    private static String guessMime(String filename) {
        String lower = filename.toLowerCase();
        if (lower.endsWith(".mp4")) return "video/mp4";
        if (lower.endsWith(".webm")) return "video/webm";
        if (lower.endsWith(".mkv")) return "video/x-matroska";
        if (lower.endsWith(".mov")) return "video/quicktime";
        if (lower.endsWith(".ts")) return "video/mp2t";
        if (lower.endsWith(".m3u8")) return "application/x-mpegurl";
        return "video/mp4";
    }

    private static String esc(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ");
    }

    private String toJson(List<Map<String, String>> videos) {
        StringBuilder sb = new StringBuilder("{\"videos\":[");
        for (int i = 0; i < videos.size(); i++) {
            if (i > 0) sb.append(",");
            sb.append("{");
            Map<String, String> v = videos.get(i);
            int j = 0;
            for (Map.Entry<String, String> e : v.entrySet()) {
                if (j++ > 0) sb.append(",");
                sb.append("\"").append(esc(e.getKey())).append("\":\"").append(esc(e.getValue())).append("\"");
            }
            sb.append("}");
        }
        sb.append("],\"count\":").append(videos.size()).append("}");
        return sb.toString();
    }
}
