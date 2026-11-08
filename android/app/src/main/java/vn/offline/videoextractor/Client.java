package vn.offline.videoextractor;

import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/** Lớp bảo vệ thứ 2 (ngoài việc thiếu quyền INTERNET): chặn mọi URL ngoài app. */
public class Client extends WebViewClient {
    @Override public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest r) {
        String u = r.getUrl().toString();
        if (u.startsWith("file:///android_asset/") || u.startsWith("blob:") || u.startsWith("data:")) return null;
        return new WebResourceResponse("text/plain", "utf-8", null);
    }

    @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) { return true; }
}
