-- Đồng bộ danh mục tỉnh/thành sau sắp xếp hành chính năm 2025.
-- Giữ tên ngắn "TP Hồ Chí Minh" để khớp tên địa điểm đang lưu trong places.
BEGIN;

INSERT INTO provinces (code, name, type) VALUES
    ('01', 'Hà Nội', 'Thành phố'),
    ('04', 'Cao Bằng', 'Tỉnh'),
    ('08', 'Tuyên Quang', 'Tỉnh'),
    ('11', 'Điện Biên', 'Tỉnh'),
    ('12', 'Lai Châu', 'Tỉnh'),
    ('14', 'Sơn La', 'Tỉnh'),
    ('15', 'Lào Cai', 'Tỉnh'),
    ('19', 'Thái Nguyên', 'Tỉnh'),
    ('20', 'Lạng Sơn', 'Tỉnh'),
    ('22', 'Quảng Ninh', 'Tỉnh'),
    ('24', 'Bắc Ninh', 'Tỉnh'),
    ('25', 'Phú Thọ', 'Tỉnh'),
    ('31', 'Hải Phòng', 'Thành phố'),
    ('33', 'Hưng Yên', 'Tỉnh'),
    ('37', 'Ninh Bình', 'Tỉnh'),
    ('38', 'Thanh Hóa', 'Tỉnh'),
    ('40', 'Nghệ An', 'Tỉnh'),
    ('42', 'Hà Tĩnh', 'Tỉnh'),
    ('44', 'Quảng Trị', 'Tỉnh'),
    ('46', 'Huế', 'Thành phố'),
    ('48', 'Đà Nẵng', 'Thành phố'),
    ('51', 'Quảng Ngãi', 'Tỉnh'),
    ('52', 'Gia Lai', 'Tỉnh'),
    ('56', 'Khánh Hòa', 'Tỉnh'),
    ('66', 'Đắk Lắk', 'Tỉnh'),
    ('68', 'Lâm Đồng', 'Tỉnh'),
    ('75', 'Đồng Nai', 'Thành phố'),
    ('79', 'TP Hồ Chí Minh', 'Thành phố'),
    ('80', 'Tây Ninh', 'Tỉnh'),
    ('82', 'Đồng Tháp', 'Tỉnh'),
    ('86', 'Vĩnh Long', 'Tỉnh'),
    ('91', 'An Giang', 'Tỉnh'),
    ('92', 'Cần Thơ', 'Thành phố'),
    ('96', 'Cà Mau', 'Tỉnh')
ON CONFLICT (code) DO UPDATE
SET name = EXCLUDED.name,
    type = EXCLUDED.type;

-- Quảng Nam cũ thuộc thành phố Đà Nẵng mới. Giữ các bản ghi hiện có
-- nhưng gắn lại vào đơn vị hành chính hiện hành.
UPDATE places SET province = 'Đà Nẵng' WHERE province = 'Quảng Nam';
DO $$
BEGIN
    IF to_regclass('public.wards') IS NOT NULL THEN
        UPDATE wards
        SET province_id = (SELECT id FROM provinces WHERE code = '48')
        WHERE province_id = (SELECT id FROM provinces WHERE code = '49');
    END IF;
END $$;

DELETE FROM provinces
WHERE code NOT IN (
    '01','04','08','11','12','14','15','19','20','22','24','25','31','33','37','38',
    '40','42','44','46','48','51','52','56','66','68','75','79','80','82','86','91','92','96'
);

DO $$
BEGIN
    IF (SELECT COUNT(*) FROM provinces) <> 34 THEN
        RAISE EXCEPTION 'Danh mục tỉnh/thành sau migration phải có đúng 34 dòng';
    END IF;
END $$;

COMMIT;
