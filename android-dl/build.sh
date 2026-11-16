#!/usr/bin/env bash
# Build APK "Tải Video" không cần Gradle: JDK 17+ và Android SDK (platform-34 + build-tools 34.0.0).
set -euo pipefail
: "${ANDROID_HOME:?hãy đặt ANDROID_HOME}"
BT="$ANDROID_HOME/build-tools/34.0.0"; JAR="$ANDROID_HOME/platforms/android-34/android.jar"
HERE="$(cd "$(dirname "$0")" && pwd)"; OUT="$HERE/out"; rm -rf "$OUT"; mkdir -p "$OUT/assets" "$OUT/classes" "$OUT/res"
VC="${VERSION_CODE:-$(( $(date +%s) / 60 ))}"

# Copy HTML vào assets
sed "s/phiên bản: dev/phiên bản: $VC/" "$HERE/video_dl.html" > "$OUT/assets/video_dl.html"

# Copy res/xml nếu có
if [ -d "$HERE/app/src/main/res" ]; then
  cp -r "$HERE/app/src/main/res/"* "$OUT/res/" 2>/dev/null || true
fi

# Compile resources
"$BT/aapt2" compile --dir "$OUT/res" -o "$OUT/compiled.zip" 2>/dev/null || true
LINK_ARGS=(-I "$JAR" --manifest "$HERE/app/src/main/AndroidManifest.xml"
  -A "$OUT/assets"
  --min-sdk-version 24 --target-sdk-version 34
  --version-code "$VC" --version-name "1.0.$VC"
  -o "$OUT/base.apk")
if [ -f "$OUT/compiled.zip" ]; then
  LINK_ARGS+=(-R "$OUT/compiled.zip")
fi
"$BT/aapt2" link "${LINK_ARGS[@]}"

# Compile Java
javac --release 11 -g:none -Xlint:-deprecation -cp "$JAR" -d "$OUT/classes" \
  $(find "$HERE/app/src/main/java" -name '*.java')

# Dex
"$BT/d8" --min-api 24 --lib "$JAR" --output "$OUT" $(find "$OUT/classes" -name '*.class')

# Package
cp "$OUT/base.apk" "$OUT/unsigned.apk"; (cd "$OUT" && zip -q -j unsigned.apk classes.dex)

# Align
"$BT/zipalign" -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"

# Sign
KS="${KEYSTORE:-$HERE/release.keystore}"
[ -f "$KS" ] || keytool -genkeypair -keystore "$KS" -alias key -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass videodl -keypass videodl -dname "CN=VideoDL" 2>/dev/null
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:videodl --key-pass pass:videodl --out "$HERE/VideoDL.apk" "$OUT/aligned.apk"
"$BT/apksigner" verify "$HERE/VideoDL.apk" && echo "OK: $HERE/VideoDL.apk (versionCode=$VC)"
