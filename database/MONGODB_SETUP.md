# Chuyển dữ liệu Travel Path sang MongoDB Atlas

Ứng dụng hỗ trợ MongoDB Atlas làm kho dữ liệu runtime. PostgreSQL hiện tại được giữ nguyên làm bản nguồn cho đến khi xác nhận chuyển đổi thành công.

## Cấu hình

1. Tạo MongoDB Atlas cluster và database user.
2. Lấy connection string `mongodb+srv://...` của cluster.
3. Đặt cấu hình trong file `.env` cạnh `docker-compose.yml` (không commit file này):

```dotenv
DATA_BACKEND=mongodb
MONGODB_URI=mongodb+srv://<user>:<password>@<cluster-host>/?retryWrites=true&w=majority
MONGODB_DATABASE=travel_path
```

Trong Atlas, thêm địa chỉ IP của máy chạy ứng dụng vào Network Access. Không chia sẻ connection string công khai.

## Sao chép dữ liệu, không xóa PostgreSQL

Chạy importer trong thư mục dự án sau khi cấu hình URI. Ví dụ với Docker Compose:

```powershell
docker compose run --rm backend python /app/data_collector/migrate_postgres_to_mongodb.py
```

Importer upsert dữ liệu vào các collection `provinces`, `places`, `tourism_directory_records`, tạo index và in số bản ghi để đối chiếu. Nó không xóa hay sửa PostgreSQL.

Sau khi số lượng dữ liệu đã khớp, khởi động backend bằng `DATA_BACKEND=mongodb`:

```powershell
docker compose up -d --build backend frontend
```

Kiểm tra `GET /api/test-db` phải trả `engine: MongoDB` cùng số địa điểm đã nhập. Chỉ gỡ PostgreSQL sau khi đã sao lưu và xác nhận mọi API hoạt động với MongoDB.

## Ghi chú

- Crawler đang ghi dữ liệu vào PostgreSQL. Nếu tiếp tục dùng crawler sau khi chuyển backend, cần chuyển phần ghi của crawler sang MongoDB hoặc đồng bộ lại sau mỗi đợt crawl.
- Ảnh hiện được lưu trong volume `tourism_media`; các document MongoDB giữ đường dẫn `/media/places/...`. Khi đưa frontend lên hosting công khai, cần đặt media volume lên dịch vụ lưu trữ ảnh có URL công khai.
