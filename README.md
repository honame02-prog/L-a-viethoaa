# Trích xuất video offline

Mở `index.html` bằng trình duyệt (double-click, không cần server). Chọn thư mục chứa trang HTML đã lưu + video, app liệt kê video trong trang để xem trước và tải về.

## Bảo mật
- Một file duy nhất, không thư viện/CDN/font ngoài.
- CSP `connect-src 'none'`, `default-src 'none'`: trình duyệt chặn mọi fetch/XHR/WebSocket/tài nguyên ngoài.
- Trang HTML được phân tích bằng `DOMParser` (không chạy script, không tải tài nguyên).
- Liên kết `http(s)://` bị bỏ qua, không bao giờ được tải.
- Video không rời khỏi máy bạn; không có telemetry.

Để chắc chắn tuyệt đối, hãy tắt mạng khi dùng và chép file này sang máy của bạn. Không cần đưa video cho bất kỳ ai/AI nào.
