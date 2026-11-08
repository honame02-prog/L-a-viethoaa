package vn.offline.videoextractor;

import android.app.Activity;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import android.webkit.*;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.util.HashMap;
import java.util.Map;

public class MainActivity extends Activity {
    private WebView web;
    private ValueCallback<Uri[]> chooserCb;
    private static final int REQ_PICK = 1;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        web = new WebView(this);
        setContentView(web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setAllowFileAccess(true);                 // chỉ để đọc file:///android_asset
        s.setAllowFileAccessFromFileURLs(false);
        s.setAllowUniversalAccessFromFileURLs(false);
        s.setAllowContentAccess(true);              // đọc file người dùng chọn (content://)
        s.setDomStorageEnabled(false);
        s.setGeolocationEnabled(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        web.addJavascriptInterface(new Saver(this), "AndroidSave");
        web.setWebViewClient(new Client());
        web.setWebChromeClient(new Chrome(this));
        web.loadUrl("file:///android_asset/index.html");
    }

    void openPicker(ValueCallback<Uri[]> cb) {
        if (chooserCb != null) chooserCb.onReceiveValue(null);
        chooserCb = cb;
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        i.addCategory(Intent.CATEGORY_OPENABLE);
        i.setType("*/*");
        i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        startActivityForResult(i, REQ_PICK);
    }

    @Override protected void onActivityResult(int req, int res, Intent d) {
        if (req != REQ_PICK || chooserCb == null) return;
        Uri[] out = null;
        if (res == RESULT_OK && d != null) {
            if (d.getClipData() != null) {
                out = new Uri[d.getClipData().getItemCount()];
                for (int i = 0; i < out.length; i++) out[i] = d.getClipData().getItemAt(i).getUri();
            } else if (d.getData() != null) out = new Uri[]{d.getData()};
        }
        chooserCb.onReceiveValue(out);
        chooserCb = null;
    }

    @Override public void onBackPressed() { finish(); }
}
