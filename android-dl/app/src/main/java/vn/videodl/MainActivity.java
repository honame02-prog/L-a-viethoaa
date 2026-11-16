package vn.videodl;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.webkit.*;

public class MainActivity extends Activity {
    private WebView web;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        web = new WebView(this);
        setContentView(web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowFileAccessFromFileURLs(false);
        s.setAllowUniversalAccessFromFileURLs(false);
        s.setAllowContentAccess(false);
        s.setGeolocationEnabled(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        web.addJavascriptInterface(new Bridge(this), "AndroidBridge");
        web.setWebViewClient(new WebViewClient());
        web.loadUrl("file:///android_asset/video_dl.html");
        handleShareIntent(getIntent());
    }

    @Override protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        handleShareIntent(i);
    }

    private void handleShareIntent(Intent i) {
        if (i == null) return;
        String action = i.getAction();
        String type = i.getType();
        if (Intent.ACTION_SEND.equals(action) && "text/plain".equals(type)) {
            String text = i.getStringExtra(Intent.EXTRA_TEXT);
            if (text != null) {
                String url = extractUrl(text);
                if (url != null) {
                    final String u = url;
                    web.post(() -> web.evaluateJavascript(
                        "if(document.getElementById('urlInput')){" +
                        "document.getElementById('urlInput').value='" + u.replace("'", "\\'") + "';" +
                        "if(typeof scanUrl==='function')scanUrl();}", null));
                }
            }
        }
    }

    private String extractUrl(String text) {
        int idx = text.indexOf("http");
        if (idx < 0) return null;
        int end = text.indexOf(' ', idx);
        if (end < 0) end = text.indexOf('\n', idx);
        if (end < 0) end = text.length();
        String url = text.substring(idx, end).trim();
        if (url.startsWith("http://") || url.startsWith("https://")) return url;
        return null;
    }

    @Override public void onBackPressed() { finish(); }
}
