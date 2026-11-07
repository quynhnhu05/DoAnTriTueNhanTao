# Travel Path — Trợ lý du lịch thông minh

Travel Path là website lập kế hoạch du lịch. Giao diện mở trong trình duyệt; Docker Desktop chạy API, cơ sở dữ liệu và các dịch vụ cần thiết. Hiện tại dự án được dùng trên máy cá nhân; chưa cần tên miền hay máy chủ công khai.

## Chạy website bằng VS Code

### Yêu cầu

- Visual Studio Code
- Node.js 22.12 trở lên và pnpm 11
- Docker Desktop đang mở và báo **Engine running**

1. Trong VS Code chọn **File → Open Folder…** và mở `D:\DoAnTriTueNhanTao`.
2. Chọn **Terminal → Run Build Task…**, rồi chọn **Trợ lý du lịch: Chạy website trong VS Code**. Phím tắt là `Ctrl+Shift+B`.
3. Mở website trong trình duyệt tại <http://127.0.0.1:8080>.

Hoặc dùng terminal tích hợp:

```powershell
pnpm start
```

Lần đầu chạy, Docker Desktop sẽ tải và dựng các dịch vụ cần thiết. Các lần sau, dùng `pnpm start` để bật website lại.

### Tài khoản và quản trị viên

- Người dùng đăng ký bằng email, tên đăng nhập, mật khẩu nhập hai lần và mã OTP gửi tới email. Sau khi xác nhận OTP, họ đăng nhập bằng email và mật khẩu.
- Trước khi đăng ký hoạt động, điền thông tin SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`) trong `.env` để website gửi mã OTP.
- Plan được tạo khi đã đăng nhập sẽ tự lưu vào tài khoản. Người dùng mở **Lịch sử plan** để xem lại, tiếp tục chỉnh sửa hoặc xóa plan của mình.
- Để tạo tài khoản quản trị, đặt `ADMIN_EMAIL` và `ADMIN_PASSWORD` (ít nhất 8 ký tự) trong file `.env` ở thư mục đồ án rồi chạy lại `pnpm start`. Đừng gửi mật khẩu quản trị qua chat hoặc đưa lên Git.
- Đăng nhập bằng tài khoản đó để mở trang **Quản trị** và xem email, ngày đăng ký, lần đăng nhập gần nhất và số lần đăng nhập. Mật khẩu không hiển thị cho quản trị viên.

### Chế độ desktop cũ (không bắt buộc)

```powershell
pnpm install
pnpm run start:desktop
```

Chế độ này chỉ giữ lại để tham khảo; cách chạy chính của đồ án là website trong trình duyệt.

Lần đầu sử dụng chatbot, sau khi ứng dụng đã khởi chạy, có thể chạy task **Trợ lý du lịch: Tải model chatbot (lần đầu)** trong VS Code. Model `qwen3.5:4b` được lưu trong Docker volume. Nếu chưa tải model, API vẫn chạy và chatbot dùng bộ nhận diện yêu cầu dự phòng.

### Các task Docker trong VS Code

- **Trợ lý du lịch: Xem trạng thái Docker**: xem các dịch vụ có đang chạy không.
- **Trợ lý du lịch: Cào dữ liệu web thực tế vào DB**: chạy một file collector riêng, lấy danh bạ website du lịch chính thức, lưu ảnh có giấy phép/nguồn và quét giá VND công khai.
- **Trợ lý du lịch: Báo cáo hạn mức địa điểm 200 mỗi tỉnh**: xem số bản ghi từng nhóm và phần còn thiếu; ảnh, giá, website được thống kê riêng.
- **Trợ lý du lịch: Crawler HTML 4 engine vào DB**: làm giàu thông tin từ website đã gắn với địa điểm trong DB, tối đa 200 địa điểm mỗi nhóm ở mỗi tỉnh trong phạm vi lượt chạy.
- **Trợ lý du lịch: Tải model chatbot (lần đầu)**: tải model Ollama.
- Dừng thủ công: `docker compose -p doantrituenhantao stop`.
- Nhật ký API: `docker compose -p doantrituenhantao logs -f backend`.

Không dùng `docker compose down -v` nếu muốn giữ dữ liệu cơ sở dữ liệu và model.

## Thu thập dữ liệu thực tế vào PostgreSQL

`crawl_real_tourism_data.py` là file thu thập riêng, lấy dữ liệu từ website danh bạ du lịch chính thức và website từng cơ sở; không gọi Overpass hay cào OSM. File lưu tên, địa chỉ, số điện thoại, website, trang nguồn, ảnh/giấy phép, giá VND/URL/thời điểm kiểm tra vào PostgreSQL. Địa điểm chưa có tọa độ xác minh vẫn được giữ ở `tourism_directory_records`; ứng dụng chỉ đưa vào bản đồ/lịch trình khi đã ghép chắc chắn với địa điểm có tọa độ. Ảnh chỉ được gắn khi khớp đủ tên; không lưu ảnh minh họa thành ảnh thật. Service chạy cập nhật theo ngày và làm mới các danh mục khách sạn riêng bằng task đầy đủ khi cần.

Để chạy crawler ngay trong VS Code, chọn **Terminal → Run Task… → Trợ lý du lịch: Cào dữ liệu web thực tế vào DB** khi Docker đang chạy. Bộ nhập danh bạ tránh nạp trùng bằng khóa nguồn; bộ quét ảnh lưu nguồn/giấy phép; bộ quét giá chỉ nhận giá VND còn hiệu lực khi xác nhận được đúng địa điểm. Nếu thiếu giá thật, hệ thống để trống thay vì đoán. Dữ liệu giá món lẻ không được coi là tổng hóa đơn.

Giá do crawler xác minh được lưu vào các cột giá nguồn trong DB. Với điểm chưa có giá nguồn, giao diện và lịch dùng một khoảng chi phí mẫu ổn định theo mã địa điểm để có thể lập ngân sách đồ án; mức này không được ghi vào các cột giá nguồn và không được tính là giá đã xác minh.

### Nguồn dữ liệu và cách thu thập

Luồng cập nhật tự động dùng Python đọc HTML từ danh bạ du lịch công khai và các website cơ sở đã xác định được. Ảnh ưu tiên lấy từ trang cơ sở khi tên địa điểm khớp; nếu không tìm thấy, crawler tìm Wikimedia Commons, kiểm tra tên trong tiêu đề/mô tả, rồi lưu URL tệp, nguồn và giấy phép. Giá lấy từ nội dung HTML, JSON-LD, menu hoặc bảng giá công khai. Dữ liệu ảnh không khớp tên sẽ bị bỏ qua; ảnh không có nguồn công khai phù hợp sẽ vẫn để trống. Luồng không cào Google Maps, Facebook hay Tripadvisor. Crawler giới hạn tốc độ và dừng khi website từ chối yêu cầu. Không có crawler nào bảo đảm có ảnh cho mọi địa điểm: kết quả phụ thuộc ảnh và metadata công khai của từng cơ sở.

Crawler dùng Gemini API để trích xuất địa điểm từ trang nguồn. Điền GEMINI_API_KEY trong .env; có thể thêm khóa dự phòng bằng GEMINI_API_KEY_2 đến GEMINI_API_KEY_10 hoặc danh sách phân tách bằng dấu phẩy trong GEMINI_API_KEYS. Khi quota ngày của các khóa Gemini đã cấu hình hết, crawler dùng Ollama cục bộ theo OLLAMA_BASE_URL và OLLAMA_FALLBACK_MODEL (mặc định qwen3.5:4b); API cục bộ cần Ollama đang chạy và model đã được tải về. Hạn mức Gemini được tính theo project, nên các khóa cùng project dùng chung quota. Ollama nhận schema JSON để kiểm tra kết quả trước khi ghi DB. Có thể đặt model Gemini chính qua GEMINI_CRAWL_MODEL (mặc định gemini-3.8-flash) và model dự phòng qua GEMINI_FALLBACK_MODEL. Tìm trang ảnh bổ sung bằng Gemini với Google Search grounding là tùy chọn, giới hạn mặc định 20 lượt/ngày qua GEMINI_IMAGE_SEARCH_LIMIT; đặt 0 để tắt. Tìm kiếm chỉ đề xuất trang nguồn; crawler vẫn tải và kiểm tra trang, metadata và ngữ cảnh ảnh trước khi lưu. Ảnh đã lưu giữ liên kết trang nguồn để người dùng kiểm tra.

API nội bộ `/api/...` vẫn được giữ để giao diện desktop/web lấy dữ liệu từ backend; việc bỏ ở đây là API dữ liệu bên thứ ba.

### Crawler HTML nhiều engine

Task **Trợ lý du lịch: Crawler HTML 4 engine vào DB** chạy `data_collector/crawl_html_multi_engine.py`:

- Requests + BeautifulSoup đọc trang chủ HTML tĩnh.
- Scrapy tìm một số trang cùng website có dấu hiệu là menu/bảng giá; có giới hạn trang, tốc độ và `ROBOTSTXT_OBEY`.
- HTTPX + asyncio tải các trang đã tìm thấy với giới hạn kết nối theo host.
- Playwright chỉ render khi trang trả HTML thành công nhưng nội dung gần như rỗng, thường do JavaScript.

Crawler chỉ bắt đầu từ website đã có trong DB, không tìm/cào mọi website trên Internet. Giá và ảnh được ghi khi khớp tên địa điểm trên chính website đó; giá không có trên trang thì để trống. Tripadvisor bị loại khỏi crawler: điều khoản của họ cấm thu thập tự động nếu chưa có chấp thuận bằng văn bản. Muốn dùng dữ liệu Tripadvisor cần quyền/điều kiện truy cập do họ cấp. Bộ khung `vn-food-crawler.zip` chỉ có nguồn ví dụ đang tắt (`directory.example`), không phải danh sách nguồn dữ liệu thật.

### Mục tiêu số lượng theo 34 tỉnh/thành

Mục tiêu cấu hình là 200 địa điểm đủ dữ liệu mỗi nhóm ở mỗi tỉnh/thành: nhà hàng/quán ăn, khách sạn, quán cà phê, tham quan/vui chơi và siêu thị. Một địa điểm chỉ được tính là đủ khi cùng bản ghi có địa chỉ, ảnh kèm nguồn ảnh và giá niêm yết kèm nguồn. Siêu thị chỉ nhận diện theo tên chuỗi siêu thị lớn hoặc phân loại siêu thị; cửa hàng tiện lợi/tạp hóa không được tính. Task **Báo cáo hạn mức địa điểm 200 mỗi tỉnh** đối chiếu số bản ghi đã có với mục tiêu và báo riêng từng trường. Có thể đổi con số bằng `--target` khi chạy file `data_collector/report_province_quotas.py`.

Con số 200 là hạn mức/mục tiêu, không phải cam kết rằng mọi tỉnh đều có sẵn 200 địa điểm đủ dữ liệu. Crawler HTML nhiều engine chỉ bổ sung ảnh/giá từ website cơ sở đã lưu trong DB; nó không tự tạo ra hàng nghìn địa điểm mới khi không có nguồn công khai phù hợp. Chỉ các giá có nguồn mới được tính là giá có nguồn; không đủ dữ liệu thì để trống và báo thiếu, không bịa để lấp hạn mức.

## Dừng website

```powershell
docker compose -p doantrituenhantao stop
```

## Nếu ứng dụng không mở

- Kiểm tra Docker Desktop đang chạy.
- Chạy task **Trợ lý du lịch: Xem trạng thái Docker**.
- Xem nhật ký bằng `docker compose -p doantrituenhantao logs --tail 100 frontend backend postgres`.
- Nếu cổng `8080` bị chương trình khác sử dụng, đổi `FRONTEND_PORT` trong file `.env` ở thư mục gốc, rồi khởi động lại.
