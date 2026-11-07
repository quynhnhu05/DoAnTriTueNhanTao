-- =========================================================
-- QUERIES CHO HỆ THỐNG GỢI Ý DU LỊCH VIỆT NAM
-- Database: tourism_db
--
-- Tables chính:
--   places
--   provinces
--   gis_provinces
--
-- Lưu ý:
--   province trong places = dữ liệu RAW từ OSM
--   province_id = tỉnh/thành đã chuẩn hóa theo 34 đơn vị
-- =========================================================


-- =========================================================
-- 0. KIỂM TRA CẤU TRÚC / SỐ LƯỢNG DỮ LIỆU
-- =========================================================

-- 0.1. Tổng số địa điểm
SELECT COUNT(*) AS total_places
FROM places;


-- 0.2. Tổng số tỉnh/thành chuẩn hóa
SELECT COUNT(*) AS total_provinces
FROM provinces;


-- 0.3. Tổng số boundary tỉnh/thành
SELECT COUNT(*) AS total_gis_provinces
FROM gis_provinces;


-- =========================================================
-- 1. ĐẾM TỔNG SỐ ĐỊA ĐIỂM
-- =========================================================

SELECT
    COUNT(*) AS total_places
FROM places;


-- =========================================================
-- 2. THỐNG KÊ SỐ LƯỢNG THEO DANH MỤC
-- =========================================================

SELECT
    category,
    COUNT(*) AS total
FROM places
GROUP BY category
ORDER BY total DESC;


-- =========================================================
-- 3. THỐNG KÊ THEO 34 TỈNH/THÀNH ĐÃ CHUẨN HÓA
-- =========================================================

SELECT
    pr.code AS province_code,
    pr.name AS province_name,
    COUNT(p.id) AS total
FROM provinces pr
LEFT JOIN places p
    ON p.province_id = pr.id
GROUP BY
    pr.id,
    pr.code,
    pr.name
ORDER BY total DESC;


-- =========================================================
-- 4. THỐNG KÊ DANH MỤC TRONG TỪNG TỈNH/THÀNH
-- =========================================================

SELECT
    pr.code AS province_code,
    pr.name AS province_name,
    p.category,
    COUNT(*) AS total
FROM places p
JOIN provinces pr
    ON p.province_id = pr.id
GROUP BY
    pr.code,
    pr.name,
    p.category
ORDER BY
    pr.name,
    total DESC;


-- =========================================================
-- 5. XEM MỘT SỐ ĐỊA ĐIỂM MẪU
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,
    p.subcategory,

    pr.code AS province_code,
    pr.name AS province_name,

    p.province AS raw_province,
    p.district,
    p.ward,
    p.address,

    p.latitude,
    p.longitude,

    p.rating,
    p.review_count,
    p.price,
    p.price_min,
    p.price_max,
    p.currency,

    p.phone,
    p.website,
    p.opening_hours,

    p.source,
    p.source_url,
    p.osm_id
FROM places p
LEFT JOIN provinces pr
    ON p.province_id = pr.id
ORDER BY p.id
LIMIT 20;


-- =========================================================
-- 6. TÌM KHÁCH SẠN / LƯU TRÚ TẠI MỘT TỈNH
-- =========================================================
--
-- Ví dụ:
-- Đà Nẵng hiện hành = code 48
--
-- Sau này API sẽ truyền province_id,
-- không hard-code code 48.
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.district,
    p.ward,
    p.address,

    p.phone,
    p.website,

    p.price,
    p.price_min,
    p.price_max,
    p.currency,

    p.rating,
    p.review_count,

    p.latitude,
    p.longitude
FROM places p
JOIN provinces pr
    ON p.province_id = pr.id
WHERE p.category = 'Lưu trú'
AND pr.code = '48'
ORDER BY
    p.rating DESC NULLS LAST,
    p.review_count DESC NULLS LAST;


-- =========================================================
-- 7. TÌM NHÀ HÀNG / ĂN UỐNG TẠI MỘT TỈNH
-- =========================================================

SELECT
    p.id,
    p.name,

    pr.code AS province_code,
    pr.name AS province_name,

    p.district,
    p.ward,
    p.address,

    p.phone,

    p.price,
    p.price_min,
    p.price_max,
    p.currency,

    p.rating,
    p.review_count,

    p.latitude,
    p.longitude
FROM places p
JOIN provinces pr
    ON p.province_id = pr.id
WHERE p.category = 'Ăn uống'
AND pr.code = '48'
ORDER BY
    p.rating DESC NULLS LAST,
    p.review_count DESC NULLS LAST;


-- =========================================================
-- 8. TÌM QUÁN CAFE TẠI MỘT TỈNH
-- =========================================================

SELECT
    p.id,
    p.name,

    pr.code AS province_code,
    pr.name AS province_name,

    p.district,
    p.ward,
    p.address,

    p.price,
    p.price_min,
    p.price_max,
    p.currency,

    p.rating,
    p.review_count,

    p.latitude,
    p.longitude
FROM places p
JOIN provinces pr
    ON p.province_id = pr.id
WHERE p.category = 'Cafe'
AND pr.code = '48'
ORDER BY
    p.rating DESC NULLS LAST,
    p.review_count DESC NULLS LAST;


-- =========================================================
-- 9. TÌM ĐỊA ĐIỂM CÓ ĐÁNH GIÁ CAO
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.address,
    p.rating,
    p.review_count
FROM places p
LEFT JOIN provinces pr
    ON p.province_id = pr.id
WHERE p.rating IS NOT NULL
ORDER BY
    p.rating DESC,
    p.review_count DESC NULLS LAST
LIMIT 50;


-- =========================================================
-- 10. TÌM ĐỊA ĐIỂM CÓ NHIỀU LƯỢT ĐÁNH GIÁ
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.address,
    p.rating,
    p.review_count
FROM places p
LEFT JOIN provinces pr
    ON p.province_id = pr.id
WHERE p.review_count IS NOT NULL
ORDER BY p.review_count DESC
LIMIT 50;


-- =========================================================
-- 11. TÌM ĐỊA ĐIỂM CÓ THÔNG TIN GIÁ
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.address,

    p.price,
    p.price_min,
    p.price_max,
    p.currency
FROM places p
LEFT JOIN provinces pr
    ON p.province_id = pr.id
WHERE p.price IS NOT NULL
   OR p.price_min IS NOT NULL
   OR p.price_max IS NOT NULL
ORDER BY
    p.category,
    p.name;


-- =========================================================
-- 12. TÌM ĐỊA ĐIỂM THEO TÊN
-- =========================================================
--
-- Ví dụ tìm "Vincom"
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.district,
    p.ward,
    p.address,

    p.rating,
    p.review_count,

    p.latitude,
    p.longitude
FROM places p
LEFT JOIN provinces pr
    ON p.province_id = pr.id
WHERE p.name ILIKE '%Vincom%'
ORDER BY p.name;


-- =========================================================
-- 13. TÌM ĐỊA ĐIỂM GẦN MỘT TỌA ĐỘ
-- =========================================================
--
-- Ví dụ:
-- Trung tâm Đà Nẵng
-- latitude  = 16.0544
-- longitude = 108.2022
-- bán kính  = 5 km
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.district,
    p.address,

    p.rating,
    p.review_count,

    p.latitude,
    p.longitude,

    ST_Distance(
        p.location,
        ST_SetSRID(
            ST_MakePoint(108.2022, 16.0544),
            4326
        )::geography
    ) / 1000 AS distance_km

FROM places p
LEFT JOIN provinces pr
    ON p.province_id = pr.id

WHERE p.location IS NOT NULL

AND ST_DWithin(
    p.location,
    ST_SetSRID(
        ST_MakePoint(108.2022, 16.0544),
        4326
    )::geography,
    5000
)

ORDER BY distance_km ASC;


-- =========================================================
-- 14. TÌM NHÀ HÀNG GẦN VỊ TRÍ NGƯỜI DÙNG
-- =========================================================
--
-- Ví dụ:
-- latitude  = 16.0544
-- longitude = 108.2022
-- bán kính  = 3 km
-- =========================================================

SELECT
    p.id,
    p.name,

    pr.code AS province_code,
    pr.name AS province_name,

    p.address,
    p.phone,

    p.price,
    p.price_min,
    p.price_max,
    p.currency,

    p.rating,
    p.review_count,

    ST_Distance(
        p.location,
        ST_SetSRID(
            ST_MakePoint(108.2022, 16.0544),
            4326
        )::geography
    ) / 1000 AS distance_km

FROM places p
LEFT JOIN provinces pr
    ON p.province_id = pr.id

WHERE p.category = 'Ăn uống'

AND p.location IS NOT NULL

AND ST_DWithin(
    p.location,
    ST_SetSRID(
        ST_MakePoint(108.2022, 16.0544),
        4326
    )::geography,
    3000
)

ORDER BY distance_km ASC;


-- =========================================================
-- 15. KIỂM TRA DỮ LIỆU THIẾU TỌA ĐỘ
-- =========================================================

SELECT
    COUNT(*) AS missing_location
FROM places
WHERE latitude IS NULL
   OR longitude IS NULL;


-- =========================================================
-- 16. KIỂM TRA ĐỊA ĐIỂM CHƯA ĐƯỢC CHUẨN HÓA TỈNH
-- =========================================================

SELECT
    COUNT(*) AS missing_province_id
FROM places
WHERE province_id IS NULL;


-- =========================================================
-- 17. KIỂM TRA DỮ LIỆU THIẾU ĐỊA CHỈ
-- =========================================================

SELECT
    COUNT(*) AS missing_address
FROM places
WHERE address IS NULL
   OR address = '';


-- =========================================================
-- 18. THỐNG KÊ DỮ LIỆU THEO NGUỒN
-- =========================================================

SELECT
    source,
    COUNT(*) AS total
FROM places
GROUP BY source
ORDER BY total DESC;


-- =========================================================
-- 19. THỐNG KÊ DỮ LIỆU CÓ TỌA ĐỘ / LOCATION
-- =========================================================

SELECT
    COUNT(*) AS total,
    COUNT(location) AS with_location,
    COUNT(*) - COUNT(location) AS without_location
FROM places;


-- =========================================================
-- 20. TOP ĐỊA ĐIỂM THEO RATING TRONG MỘT TỈNH
-- =========================================================
--
-- Ví dụ Đà Nẵng = code 48
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.address,
    p.rating,
    p.review_count

FROM places p
JOIN provinces pr
    ON p.province_id = pr.id

WHERE pr.code = '48'
AND p.rating IS NOT NULL

ORDER BY
    p.rating DESC,
    p.review_count DESC NULLS LAST

LIMIT 20;


-- =========================================================
-- 21. THỐNG KÊ CHUẨN HÓA TỈNH
-- =========================================================

SELECT
    COUNT(*) AS total_places,
    COUNT(*) FILTER (
        WHERE province_id IS NOT NULL
    ) AS normalized_places,
    COUNT(*) FILTER (
        WHERE province_id IS NULL
    ) AS unnormalized_places
FROM places;


-- =========================================================
-- 22. XEM CÁC ĐỊA ĐIỂM CHƯA XÁC ĐỊNH TỈNH
-- =========================================================

SELECT
    id,
    name,
    category,
    province AS raw_province,
    district,
    ward,
    address,
    latitude,
    longitude,
    source,
    osm_id
FROM places
WHERE province_id IS NULL
ORDER BY id
LIMIT 100;


-- =========================================================
-- 23. SO SÁNH RAW PROVINCE VÀ PROVINCE ĐÃ CHUẨN HÓA
-- =========================================================

SELECT
    p.province AS raw_province,
    pr.code AS normalized_code,
    pr.name AS normalized_province,
    COUNT(*) AS total
FROM places p
LEFT JOIN provinces pr
    ON p.province_id = pr.id
GROUP BY
    p.province,
    pr.code,
    pr.name
ORDER BY total DESC;


-- =========================================================
-- 24. KIỂM TRA 34 TỈNH CÓ ĐỊA ĐIỂM HAY KHÔNG
-- =========================================================

SELECT
    pr.code,
    pr.name,
    pr.type,
    COUNT(p.id) AS total_places
FROM provinces pr
LEFT JOIN places p
    ON p.province_id = pr.id
GROUP BY
    pr.id,
    pr.code,
    pr.name,
    pr.type
ORDER BY
    pr.code;


-- =========================================================
-- 25. TÌM ĐỊA ĐIỂM THEO TỈNH + DANH MỤC
-- =========================================================
--
-- Ví dụ:
-- Đà Nẵng = 48
-- Ăn uống
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,
    p.subcategory,

    pr.code AS province_code,
    pr.name AS province_name,

    p.district,
    p.ward,
    p.address,

    p.price,
    p.price_min,
    p.price_max,
    p.currency,

    p.rating,
    p.review_count,

    p.latitude,
    p.longitude

FROM places p
JOIN provinces pr
    ON p.province_id = pr.id

WHERE pr.code = '48'
AND p.category = 'Ăn uống'

ORDER BY
    p.rating DESC NULLS LAST,
    p.review_count DESC NULLS LAST;


-- =========================================================
-- 26. TÌM KHÁCH SẠN GẦN MỘT ĐỊA ĐIỂM
-- =========================================================
--
-- Ví dụ:
-- Tọa độ trung tâm Đà Nẵng
-- bán kính 5 km
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.address,

    p.price,
    p.price_min,
    p.price_max,
    p.currency,

    p.rating,
    p.review_count,

    ST_Distance(
        p.location,
        ST_SetSRID(
            ST_MakePoint(108.2022, 16.0544),
            4326
        )::geography
    ) / 1000 AS distance_km

FROM places p
LEFT JOIN provinces pr
    ON p.province_id = pr.id

WHERE p.category = 'Lưu trú'
AND p.location IS NOT NULL

AND ST_DWithin(
    p.location,
    ST_SetSRID(
        ST_MakePoint(108.2022, 16.0544),
        4326
    )::geography,
    5000
)

ORDER BY
    distance_km ASC;


-- =========================================================
-- 27. TÌM ĐỊA ĐIỂM CÓ GIÁ TRONG MỘT KHOẢNG
-- =========================================================
--
-- Ví dụ: từ 100.000 đến 500.000
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.price,
    p.price_min,
    p.price_max,
    p.currency

FROM places p
LEFT JOIN provinces pr
    ON p.province_id = pr.id

WHERE
    (
        p.price_min BETWEEN 100000 AND 500000
        OR
        p.price_max BETWEEN 100000 AND 500000
    )

ORDER BY
    p.price_min NULLS LAST;


-- =========================================================
-- 28. TÌM ĐỊA ĐIỂM CÓ ĐỦ DỮ LIỆU CƠ BẢN
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.address,
    p.latitude,
    p.longitude,
    p.price,
    p.rating,
    p.review_count,
    p.source

FROM places p
LEFT JOIN provinces pr
    ON p.province_id = pr.id

WHERE p.name IS NOT NULL
AND p.latitude IS NOT NULL
AND p.longitude IS NOT NULL
AND p.province_id IS NOT NULL

ORDER BY p.id

LIMIT 100;


-- =========================================================
-- 29. KIỂM TRA BOUNDARY 34 TỈNH
-- =========================================================

SELECT
    province_code,
    province_name,
    GeometryType(geometry) AS geometry_type,
    ST_SRID(geometry) AS srid,
    ST_IsValid(geometry) AS geometry_valid
FROM gis_provinces
ORDER BY province_code;


-- =========================================================
-- 30. THỐNG KÊ ĐỊA ĐIỂM THEO TỈNH VÀ CATEGORY
-- DÙNG CHO RECOMMENDATION
-- =========================================================

SELECT
    pr.code AS province_code,
    pr.name AS province_name,
    p.category,

    COUNT(*) AS total_places,

    COUNT(*) FILTER (
        WHERE p.rating IS NOT NULL
    ) AS places_with_rating,

    COUNT(*) FILTER (
        WHERE p.price IS NOT NULL
           OR p.price_min IS NOT NULL
           OR p.price_max IS NOT NULL
    ) AS places_with_price

FROM places p
JOIN provinces pr
    ON p.province_id = pr.id

GROUP BY
    pr.code,
    pr.name,
    p.category

ORDER BY
    pr.name,
    total_places DESC;


-- =========================================================
-- 31. DỮ LIỆU PHỤC VỤ RECOMMENDATION
-- =========================================================
--
-- Lấy các trường cần thiết:
-- địa điểm + tỉnh + tọa độ + rating + review + giá
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,
    p.subcategory,

    pr.code AS province_code,
    pr.name AS province_name,

    p.address,

    p.latitude,
    p.longitude,
    p.location,

    p.price,
    p.price_min,
    p.price_max,
    p.currency,

    p.rating,
    p.review_count,

    p.source,
    p.source_url

FROM places p

JOIN provinces pr
    ON p.province_id = pr.id

WHERE p.province_id IS NOT NULL
AND p.latitude IS NOT NULL
AND p.longitude IS NOT NULL;


-- =========================================================
-- 32. DANH SÁCH 34 TỈNH/THÀNH HIỆN HÀNH
-- =========================================================

SELECT
    id,
    code,
    name,
    type
FROM provinces
ORDER BY
    code;


-- =========================================================
-- 33. TÌM ĐỊA ĐIỂM THEO TỈNH BẰNG TÊN
-- =========================================================
--
-- Ví dụ: 'Đà Nẵng'
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.address,
    p.latitude,
    p.longitude,
    p.rating,
    p.review_count,
    p.price

FROM places p
JOIN provinces pr
    ON p.province_id = pr.id

WHERE pr.name = 'Đà Nẵng'

ORDER BY p.name;


-- =========================================================
-- 34. TÌM ĐỊA ĐIỂM THEO TỈNH + TÊN
-- =========================================================
--
-- Ví dụ:
-- Tỉnh: Đà Nẵng
-- Từ khóa: Vincom
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.district,
    p.ward,
    p.address,

    p.rating,
    p.review_count,
    p.price,

    p.latitude,
    p.longitude

FROM places p
JOIN provinces pr
    ON p.province_id = pr.id

WHERE pr.name = 'Đà Nẵng'
AND p.name ILIKE '%Vincom%'

ORDER BY p.name;


-- =========================================================
-- 35. THỐNG KÊ DỮ LIỆU CÓ / THIẾU RATING
-- =========================================================

SELECT
    COUNT(*) AS total_places,

    COUNT(*) FILTER (
        WHERE rating IS NOT NULL
    ) AS with_rating,

    COUNT(*) FILTER (
        WHERE rating IS NULL
    ) AS without_rating

FROM places;


-- =========================================================
-- 36. THỐNG KÊ DỮ LIỆU CÓ / THIẾU REVIEW_COUNT
-- =========================================================

SELECT
    COUNT(*) AS total_places,

    COUNT(*) FILTER (
        WHERE review_count IS NOT NULL
    ) AS with_review_count,

    COUNT(*) FILTER (
        WHERE review_count IS NULL
    ) AS without_review_count

FROM places;


-- =========================================================
-- 37. THỐNG KÊ DỮ LIỆU CÓ / THIẾU PRICE
-- =========================================================

SELECT
    COUNT(*) AS total_places,

    COUNT(*) FILTER (
        WHERE price IS NOT NULL
           OR price_min IS NOT NULL
           OR price_max IS NOT NULL
    ) AS with_price,

    COUNT(*) FILTER (
        WHERE price IS NULL
          AND price_min IS NULL
          AND price_max IS NULL
    ) AS without_price

FROM places;


-- =========================================================
-- 38. TOP ĐỊA ĐIỂM PHỤC VỤ GỢI Ý
-- =========================================================
--
-- Có rating + review + tọa độ
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.code AS province_code,
    pr.name AS province_name,

    p.rating,
    p.review_count,

    p.price,
    p.price_min,
    p.price_max,

    p.latitude,
    p.longitude

FROM places p
JOIN provinces pr
    ON p.province_id = pr.id

WHERE p.province_id IS NOT NULL
AND p.location IS NOT NULL
AND p.rating IS NOT NULL

ORDER BY
    p.rating DESC,
    p.review_count DESC NULLS LAST

LIMIT 100;


-- =========================================================
-- 39. TÌM CÁC ĐỊA ĐIỂM GẦN NHAU
-- =========================================================
--
-- Ví dụ quanh trung tâm Đà Nẵng trong 1 km
-- =========================================================

SELECT
    p.id,
    p.name,
    p.category,

    pr.name AS province_name,

    ROUND(
        (
            ST_Distance(
                p.location,
                ST_SetSRID(
                    ST_MakePoint(108.2022, 16.0544),
                    4326
                )::geography
            ) / 1000
        )::numeric,
        2
    ) AS distance_km

FROM places p
LEFT JOIN provinces pr
    ON p.province_id = pr.id

WHERE p.location IS NOT NULL

AND ST_DWithin(
    p.location,
    ST_SetSRID(
        ST_MakePoint(108.2022, 16.0544),
        4326
    )::geography,
    1000
)

ORDER BY distance_km;


-- =========================================================
-- 40. KIỂM TRA CÁC ĐỊA ĐIỂM CHƯA CÓ province_id
-- NHƯNG CÓ RAW PROVINCE
-- =========================================================

SELECT
    id,
    name,
    category,
    province AS raw_province,
    district,
    ward,
    address,
    latitude,
    longitude
FROM places
WHERE province_id IS NULL
AND province IS NOT NULL
AND TRIM(province) <> ''
ORDER BY id;


-- =========================================================
-- KẾT THÚC QUERIES
-- =========================================================


-- =========================================================
-- 41. BÁO CÁO ĐẦY ĐỦ CHO 34 TỈNH/THÀNH HIỆN HÀNH
--    Luôn trả đủ 34 dòng, kể cả tỉnh chưa có địa điểm.
-- =========================================================
SELECT
    pr.code AS province_code,
    pr.name AS province_name,
    pr.type AS province_type,
    COUNT(DISTINCT w.id) AS total_wards,
    COUNT(DISTINCT p.id) AS total_places,
    COUNT(DISTINCT p.id) FILTER (WHERE p.ward_id IS NOT NULL) AS places_with_ward,
    COUNT(DISTINCT p.id) FILTER (
        WHERE p.province_id IS NOT NULL AND p.ward_id IS NULL
    ) AS places_without_ward
FROM provinces AS pr
LEFT JOIN wards AS w
    ON w.province_id = pr.id
LEFT JOIN places AS p
    ON p.province_id = pr.id
GROUP BY pr.id, pr.code, pr.name, pr.type
ORDER BY pr.code;


-- =========================================================
-- 42. LẤY TOÀN BỘ ĐỊA ĐIỂM Ở 34 TỈNH, KÈM XÃ/PHƯỜNG ĐÃ CHUẨN HÓA
--    ward/district/province RAW vẫn được trả nguyên vẹn.
-- =========================================================
SELECT
    p.id AS place_id,
    p.name AS place_name,
    p.category,
    p.subcategory,
    pr.code AS province_code,
    pr.name AS province_name,
    w.id AS ward_id,
    w.code AS ward_source_id,
    w.name AS normalized_ward_name,
    w.type AS normalized_ward_type,
    p.province AS raw_province,
    p.district AS raw_district,
    p.ward AS raw_ward,
    p.address,
    p.latitude,
    p.longitude,
    p.rating,
    p.review_count,
    p.source,
    p.source_url
FROM places AS p
JOIN provinces AS pr
    ON pr.id = p.province_id
LEFT JOIN wards AS w
    ON w.id = p.ward_id
ORDER BY pr.code, w.name NULLS LAST, p.name;


-- =========================================================
-- 43. LẤY DỮ LIỆU MỘT TỈNH THEO CODE (thay '48' bằng mã cần lấy)
--    Ví dụ '48' là Đà Nẵng.
-- =========================================================
SELECT
    p.id AS place_id,
    p.name AS place_name,
    p.category,
    pr.code AS province_code,
    pr.name AS province_name,
    w.id AS ward_id,
    w.name AS ward_name,
    w.type AS ward_type,
    p.district AS raw_district,
    p.ward AS raw_ward,
    p.address,
    p.latitude,
    p.longitude
FROM places AS p
JOIN provinces AS pr
    ON pr.id = p.province_id
LEFT JOIN wards AS w
    ON w.id = p.ward_id
WHERE pr.code = '48'
ORDER BY w.name NULLS LAST, p.name;


-- =========================================================
-- 44. ĐỊA ĐIỂM CHƯA GHÉP ĐƯỢC XÃ/PHƯỜNG, PHÂN THEO 34 TỈNH
-- =========================================================
SELECT
    pr.code AS province_code,
    pr.name AS province_name,
    p.id AS place_id,
    p.name AS place_name,
    p.province AS raw_province,
    p.district AS raw_district,
    p.ward AS raw_ward,
    p.address,
    p.latitude,
    p.longitude
FROM places AS p
JOIN provinces AS pr
    ON pr.id = p.province_id
WHERE p.ward_id IS NULL
ORDER BY pr.code, p.id;
