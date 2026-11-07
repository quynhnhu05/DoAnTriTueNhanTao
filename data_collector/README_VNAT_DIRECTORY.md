# Bổ sung dữ liệu từ Cơ sở dữ liệu ngành Du lịch

Nguồn: <https://csdl.vietnamtourism.gov.vn/> (Cục Du lịch Quốc gia Việt Nam).
Danh mục có điểm đến, khu du lịch, vui chơi giải trí, nhà hàng, cơ sở lưu trú
và điểm mua sắm.

## Nhập và làm giàu dữ liệu

```sh
python /app/data_collector/crawl_vietnamtourism_directory.py \
  --categories dest,area,vcgt,rest,shop,cslt --delay 1.5
```

Lệnh này đi qua các trang danh sách, chỉ giữ bản ghi có địa chỉ sử dụng được,
lưu mã và đường dẫn nguồn vào `tourism_directory_records`, rồi liên kết với
`places` khi tên khớp chính xác. Địa chỉ chính thức chỉ thay địa chỉ đang có
khi dài và cụ thể hơn; số điện thoại, website chỉ điền vào ô trống.

Các bản ghi chưa khớp vẫn được lưu trong bảng nguồn để kiểm tra. Bộ thu thập
không tự tạo tọa độ hoặc giá; vì vậy bản ghi chưa có tọa độ không được biến
thành điểm trên bản đồ/lịch trình. Có thể chạy lặp lại an toàn: khóa nguồn
`(provider, provider_category, provider_id)` tránh nạp trùng.

`--max-pages N` giới hạn lượt cập nhật để thử nhanh; `--province "48,49"` dùng
mã bộ lọc tỉnh của trang nguồn. Mặc định nghỉ ít nhất 1,5 giây giữa các trang.

Ảnh được đọc từ thẻ Open Graph/Twitter Card trong HTML website chính thức của
cơ sở; giá được đọc từ HTML/JSON-LD hoặc menu/bảng giá công khai trên cùng
website. Crawler lưu URL nguồn và thời điểm kiểm tra. Ảnh được tìm trước trên
website cơ sở; nếu không có ảnh khớp, crawler tìm Wikimedia Commons và chỉ nhận
tệp có tên/metadata khớp địa điểm, đồng thời lưu giấy phép. Không lấy ảnh minh
họa mặc định. Crawler chỉ
quét website lấy từ danh bạ hoặc đã gắn với địa điểm trong DB; tuân thủ
`robots.txt`, giới hạn tốc độ, và dừng khi website từ chối truy cập.

Dùng task VS Code **Trợ lý du lịch: Cào dữ liệu web thực tế vào DB** để chạy
nhập danh bạ, ảnh HTML và giá HTML theo thứ tự. Danh bạ công khai không bao
phủ mọi cơ sở trên toàn quốc; số lượng ảnh/giá thu được phụ thuộc việc từng
website có công bố dữ liệu và cho phép crawler truy cập hay không.
