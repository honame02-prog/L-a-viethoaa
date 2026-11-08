# Trích xuất & bảo vệ video — công cụ OFFLINE

Một file duy nhất: `index.html`. Mở bằng Chrome/Edge/Firefox (bấm đúp, không cần server, không cài gì).

## Chức năng
1. **Két mã hóa**: mã hóa video/file bất kỳ thành `.vault` bằng mật khẩu (AES-256-GCM, khóa từ PBKDF2-SHA256 600.000 vòng). Giải mã lại bằng đúng mật khẩu.
2. **Trích xuất video** từ trang `.html` đã lưu và từ `.mht/.mhtml` (video base64 nhúng bên trong).
3. **Xem liên kết video trong MHT**: hiện link đầy đủ, gom nhóm (master / chất lượng / ảnh / đoạn / blob), tạo sẵn lệnh `ffmpeg`/`curl` để chạy trên máy bạn.
4. **Phân tích chữ ký byte**: đọc dữ liệu thật của từng phần trong `.mht` (và của ảnh bạn tải về) để phát hiện đoạn video HLS (MPEG-TS/fMP4) **ngụy trang đuôi `.jpg`**, bỏ "đầu ảnh giả", rồi ghép theo thứ tự tên thành `.ts`/`.mp4`. Chỉ làm được khi nội dung thật sự có trong file hoặc bạn đã tải về máy; link trơn thì app offline không thể lấy nội dung.
5. **Kiểm tra cách ly mạng**: tự thử fetch/XHR/WebSocket/beacon/ảnh/script ra ngoài và báo trình duyệt có chặn không.

## Công cụ HLS online (tùy chọn): `hls_tool.py`
Kiểm tra và tách video từ link `.m3u8` **không cần ffmpeg** (chỉ cần Python 3, không cài thêm thư viện). Đây là công cụ *tách riêng* vì muốn lấy video từ máy chủ thì bắt buộc phải có kết nối; `index.html` vẫn hoàn toàn offline.
- `python hls_tool.py --check`: phân tích (số đoạn, mã hóa, đuôi file, đọc chữ ký byte của đoạn đầu → kết luận **HLS ngụy trang đuôi ảnh** hay chỉ là ảnh).
- `python hls_tool.py`: tải, bỏ "đầu ảnh giả", giải mã AES-128 (khóa công khai của HLS), ghép đúng thứ tự thành `.ts`/`.mp4`.
- An toàn: chỉ thư viện chuẩn; in **mọi máy chủ** sẽ kết nối và hỏi `[y/N]` trước; chặn redirect sang máy chủ chưa xác nhận; kiểm tra chứng chỉ HTTPS; che token khi hiển thị; từ chối `file://`; **từ chối DRM** (SAMPLE-AES) và không cố vượt DRM.

## Mô hình bảo mật (nói thẳng giới hạn)
- Không thư viện, CDN, font, analytics. Không có mã nào gọi mạng, trừ các phép *thử* trong "Kiểm tra cách ly mạng" (đều nhắm `*.invalid`, bị chặn trước khi gửi).
- CSP `default-src 'none'; connect-src 'none'; form-action 'none'; frame-src 'none'` → trình duyệt tự chặn mọi kết nối, kể cả khi mã có lỗi. Đã kiểm chứng bằng máy chủ cục bộ: **0 kết nối nhận được**.
- Mật khẩu chỉ nằm trong bộ nhớ trang và bị xóa khỏi ô nhập sau mỗi lần dùng; không lưu ở đâu cả. **Mất mật khẩu = mất dữ liệu, không ai khôi phục được.**
- Mỗi khối 4 MB có IV riêng và được xác thực kèm số thứ tự + cờ "khối cuối": sửa 1 byte, đảo/cắt/thêm khối đều bị phát hiện. Tên file gốc cũng được mã hóa.
- **Không bảo vệ được**: máy dính virus/keylogger; tiện ích trình duyệt đọc được trang; người nhìn màn hình lúc video đang phát; mật khẩu yếu. Không có "bảo mật tuyệt đối" — chỉ có thể làm mọi thứ chạy cục bộ và khóa dữ liệu bằng mật mã mạnh.

## Khuyến nghị dùng cho dữ liệu nhạy cảm
1. Chép `index.html` sang máy bằng USB (hoặc tải từ nguồn bạn tin), kiểm tra hash: `sha256sum index.html` (Windows: `certutil -hashfile index.html SHA256`) và so với `index.html.sha256` do chính bạn lưu lần đầu.
2. **Ngắt mạng**, mở bằng cửa sổ ẩn danh đã tắt tiện ích (hoặc hồ sơ trình duyệt mới).
3. Bấm "Chạy kiểm tra" → phải hiện "✔ Đạt".
4. Dùng cụm mật khẩu ≥ 5 từ ngẫu nhiên. Giải mã thử trước khi xóa file gốc.
5. Xóa file gốc bằng công cụ xóa an toàn nếu cần (xóa thường không xóa dữ liệu khỏi đĩa).

## Android (APK)
`android/VideoOffline.apk` — WebView nạp đúng `index.html`, **không khai báo quyền INTERNET** (Android chặn mạng ở cấp hệ điều hành). File lưu vào Download. Tự build: `ANDROID_HOME=<sdk> android/build.sh` (JDK 17+, platform-34, build-tools 34.0.0). Khoá ký `android/release.keystore` không đưa lên git — hãy giữ lại.
