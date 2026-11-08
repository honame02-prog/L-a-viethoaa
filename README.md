# Trích xuất video offline

Mở `index.html` bằng trình duyệt (double-click, không cần server). Chọn thư mục chứa trang HTML đã lưu + video, app liệt kê video trong trang để xem trước và tải về.

Hỗ trợ: trang `.html/.htm` (video qua `<video>`, `<source>`, liên kết, `data:`), file `.mht/.mhtml` (video base64 nhúng bên trong, giải mã theo khối 4MB), và video chọn trực tiếp.

## Android (APK)
`android/VideoOffline.apk` — cài trực tiếp (bật "Cài từ nguồn không xác định"). App là WebView nạp đúng `index.html`; **không khai báo quyền INTERNET** nên Android chặn mọi kết nối mạng ở cấp hệ điều hành. Video được lưu vào thư mục Download.
Tự build lại: `ANDROID_HOME=<sdk> android/build.sh` (cần JDK 17+, platform-34, build-tools 34.0.0; không cần Gradle/Maven). Khoá ký `android/release.keystore` được tạo lần đầu và không đưa lên git — hãy giữ lại nếu muốn cập nhật đè bản cũ.

## Bảo mật
- Một file duy nhất, không thư viện/CDN/font ngoài.
- CSP `connect-src 'none'`, `default-src 'none'`: trình duyệt chặn mọi fetch/XHR/WebSocket/tài nguyên ngoài.
- Trang HTML được phân tích bằng `DOMParser` (không chạy script, không tải tài nguyên).
- Liên kết `http(s)://` bị bỏ qua, không bao giờ được tải.
- Video không rời khỏi máy bạn; không có telemetry.

Để chắc chắn tuyệt đối, hãy tắt mạng khi dùng và chép file này sang máy của bạn. Không cần đưa video cho bất kỳ ai/AI nào.
