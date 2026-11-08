package vn.offline.videoextractor;

import android.net.Uri;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebView;

/** Mở bộ chọn file hệ thống (nhiều file) cho thẻ input type=file. */
public class Chrome extends WebChromeClient {
    private final MainActivity act;
    Chrome(MainActivity a) { act = a; }

    @Override public boolean onShowFileChooser(WebView w, ValueCallback<Uri[]> cb, FileChooserParams p) {
        act.openPicker(cb);
        return true;
    }
}
