# Đưa website Travel Path lên máy chủ

Website đã được đóng gói bằng Docker Compose. Cấu hình này chưa gắn tên miền; khi chạy trên máy chủ, trang có thể truy cập bằng địa chỉ IP và cổng 8080. Cần có tên miền và HTTPS mới có địa chỉ website bảo mật, dễ nhớ.

## Chạy thử trên máy hiện tại

Trong VS Code mở thư mục đồ án rồi chọn **Terminal → Run Build Task… → Trợ lý du lịch: Chạy website trong VS Code**. Hoặc chạy `pnpm start`. Sau khi các container khởi động xong, mở `http://127.0.0.1:8080` trên trình duyệt. Đây là địa chỉ xem thử trên chính máy này, không phải địa chỉ công khai.

## Chuẩn bị máy chủ khi có máy đích

1. Cài Docker Engine và Docker Compose plugin trên máy Linux.
2. Chép mã nguồn lên máy chủ, gồm `backend`, `database`, `data_collector`, `docker`, `frontend`, `docker-compose.yml`, `requirements.txt` và `data/media/crawled` để ảnh địa điểm hoạt động.
3. Tạo `.env.web` từ `.env.web.example`. Đặt mật khẩu DB dài, riêng cho máy chủ; thêm khóa Gemini nếu cần dùng tính năng AI. Không chép `.env` cá nhân lên nơi công khai và không đưa khóa vào Git.
4. Mở cổng TCP 8080 trong tường lửa của máy chủ. Chạy:

   ```sh
   docker compose --env-file .env.web -p doantrituenhantao up -d --build
   ```

5. Truy cập `http://IP_MAY_CHU:8080`. Khi có tên miền, có thể đặt reverse proxy HTTPS phía trước website mà không cần đổi đường dẫn API trong giao diện.

## Dữ liệu và vận hành

- Giao diện web gọi API cùng nguồn qua `/api`; Nginx chuyển tiếp yêu cầu đến backend.
- Chỉ cổng website được mở ra ngoài. PostgreSQL, API và Ollama được giới hạn ở loopback trên máy chủ.
- PostgreSQL, ảnh được tạo khi chạy và model Ollama lưu trong Docker volumes. Hãy sao lưu các volumes trước khi thay máy hoặc xóa dữ liệu.
- `data/media/crawled` là ảnh được lưu trong đồ án và cần chép cùng mã nguồn để các ảnh địa điểm hiện ra.
- Không chạy `docker compose down -v` nếu cần giữ DB, ảnh hoặc model.
- Xem trạng thái: `docker compose -p doantrituenhantao ps`; xem nhật ký: `docker compose -p doantrituenhantao logs -f frontend backend`.

## Ghi chú

Hiện chưa có máy chủ đích nên chưa có địa chỉ công khai. Chạy ở `localhost` chỉ để xem thử; website trở thành công khai sau khi triển khai các bước trên lên máy chủ và mở cổng mạng.
