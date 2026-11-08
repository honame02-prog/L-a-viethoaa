#!/usr/bin/env bash
# Build APK không cần Gradle/Maven: chỉ cần JDK 17+ và Android SDK (platform-34 + build-tools 34.0.0).
set -euo pipefail
: "${ANDROID_HOME:?hãy đặt ANDROID_HOME}"
BT="$ANDROID_HOME/build-tools/34.0.0"; JAR="$ANDROID_HOME/platforms/android-34/android.jar"
HERE="$(cd "$(dirname "$0")" && pwd)"; OUT="$HERE/out"; rm -rf "$OUT"; mkdir -p "$OUT/assets" "$OUT/classes"
cp "$HERE/../index.html" "$OUT/assets/index.html"
"$BT/aapt2" link -I "$JAR" --manifest "$HERE/app/src/main/AndroidManifest.xml" -A "$OUT/assets" \
  --min-sdk-version 24 --target-sdk-version 34 --version-code 1 --version-name 1.0 -o "$OUT/base.apk"
javac --release 11 -g:none -Xlint:-deprecation -cp "$JAR" -d "$OUT/classes" $(find "$HERE/app/src/main/java" -name '*.java')
"$BT/d8" --min-api 24 --lib "$JAR" --output "$OUT" $(find "$OUT/classes" -name '*.class')
cp "$OUT/base.apk" "$OUT/unsigned.apk"; (cd "$OUT" && zip -q -j unsigned.apk classes.dex)
"$BT/zipalign" -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
KS="${KEYSTORE:-$HERE/release.keystore}"
[ -f "$KS" ] || keytool -genkeypair -keystore "$KS" -alias key -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass videooffline -keypass videooffline -dname "CN=VideoOffline" 2>/dev/null
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:videooffline --key-pass pass:videooffline --out "$HERE/VideoOffline.apk" "$OUT/aligned.apk"
"$BT/apksigner" verify "$HERE/VideoOffline.apk" && echo "OK: $HERE/VideoOffline.apk"
