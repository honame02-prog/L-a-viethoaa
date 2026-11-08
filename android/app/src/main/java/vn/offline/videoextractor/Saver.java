package vn.offline.videoextractor;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import android.webkit.JavascriptInterface;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.util.HashMap;
import java.util.Map;

    /** Ghi file vào thư mục Download của máy, hoàn toàn cục bộ. */
public class Saver {
        private final android.content.Context ctx;
        Saver(android.content.Context c) { ctx = c; }
        private final Map<Integer, OutputStream> streams = new HashMap<>();
        private final Map<Integer, Uri> uris = new HashMap<>();
        private int next = 1;

        @JavascriptInterface public synchronized int begin(String name, String mime) {
            try {
                name = name.replaceAll("[\\\\/:*?\"<>|]", "_");
                OutputStream os; Uri uri = null;
                if (Build.VERSION.SDK_INT >= 29) {
                    ContentValues v = new ContentValues();
                    v.put(MediaStore.Downloads.DISPLAY_NAME, name);
                    v.put(MediaStore.Downloads.MIME_TYPE, mime);
                    v.put(MediaStore.Downloads.IS_PENDING, 1);
                    ContentResolver cr = ctx.getContentResolver();
                    uri = cr.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
                    os = cr.openOutputStream(uri);
                } else {
                    File dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
                    dir.mkdirs();
                    os = new FileOutputStream(new File(dir, name));
                }
                int id = next++;
                streams.put(id, os);
                if (uri != null) uris.put(id, uri);
                return id;
            } catch (Exception e) { return -1; }
        }

        @JavascriptInterface public synchronized void write(int id, String b64) throws Exception {
            streams.get(id).write(Base64.decode(b64, Base64.DEFAULT));
        }

        @JavascriptInterface public synchronized boolean end(int id) {
            try {
                OutputStream os = streams.remove(id); if (os != null) os.close();
                Uri u = uris.remove(id);
                if (u != null) {
                    ContentValues v = new ContentValues();
                    v.put(MediaStore.Downloads.IS_PENDING, 0);
                    ctx.getContentResolver().update(u, v, null, null);
                }
                return true;
            } catch (Exception e) { return false; }
        }
    }
