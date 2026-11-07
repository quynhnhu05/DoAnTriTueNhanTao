-- Verified Gia Lai (former Quy Nhon/Binh Dinh) tourism highlights absent
-- from the POI import. Coordinates are from OpenStreetMap except Ky Co,
-- whose coordinates are published by Vietnam's environmental gazetteer.
-- Re-runnable: an existing POI in the current province is left untouched.
WITH seed(name, category, subcategory, address, ward_id, lat, lon, osm_id, osm_type, source, source_url, opening_hours) AS (
    VALUES
        ('Bãi biển Kỳ Co', 'Thiên nhiên', 'Bãi biển', 'Khu du lịch Kỳ Co, Phường Quy Nhơn Đông, Gia Lai', 11652, 13.851667::double precision, 109.291944::double precision, NULL::bigint, NULL::varchar, 'VNAT_CSDL', 'https://csdl.vietnamtourism.gov.vn/dest/?item=14', NULL::text),
        ('Eo Gió', 'Thiên nhiên', 'Điểm tham quan', 'Khu du lịch biển Nhơn Lý - Cát Tiến, Phường Quy Nhơn Đông, Gia Lai', 11652, 13.89088, 109.29141, 4132751593::bigint, 'node', 'VNAT_CSDL + OpenStreetMap', 'https://csdl.vietnamtourism.gov.vn/dest/?item=917', NULL::text),
        ('Hòn Khô', 'Thiên nhiên', 'Đảo', 'Thôn Hải Đông, Phường Quy Nhơn Đông, Gia Lai', 11652, 13.76443, 109.29841, 186491956::bigint, 'way', 'OpenStreetMap', 'https://www.openstreetmap.org/way/186491956', NULL::text),
        ('Ghềnh Ráng - Tiên Sa', 'Tham quan', 'Danh thắng', '06, Đường Ngô Gia Tự, Phường Quy Nhơn Nam, Gia Lai', 11653, 13.74332, 109.21476, 4172034894::bigint, 'node', 'VNAT_CSDL + OpenStreetMap', 'https://csdl.vietnamtourism.gov.vn/dest/?item=486', 'Mo-Su 07:00-18:00'),
        ('Tháp Đôi', 'Tham quan', 'Di tích Chăm', 'Đường Tháp Đôi, Phường Quy Nhơn, Gia Lai', 11650, 13.78625, 109.21108, 1534828632::bigint, 'node', 'VNAT_CSDL + OpenStreetMap', 'https://csdl.vietnamtourism.gov.vn/dest/?item=257', NULL::text),
        ('Tháp Bánh Ít', 'Tham quan', 'Di tích Chăm', 'Thôn Đại Lễ, Xã Tuy Phước Bắc, Gia Lai', 11663, 13.86842, 109.1352, 252581207::bigint, 'way', 'VNAT_CSDL + OpenStreetMap', 'https://csdl.vietnamtourism.gov.vn/dest/?item=132', NULL::text)
)
INSERT INTO places (
    name, category, subcategory, province, ward, address, latitude, longitude,
    location, province_id, ward_id, osm_id, osm_type, source, source_url, opening_hours
)
SELECT s.name, s.category, s.subcategory, 'Gia Lai', w.name, s.address,
       s.lat, s.lon, ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326)::geography,
       23, s.ward_id, s.osm_id, s.osm_type, s.source, s.source_url, s.opening_hours
FROM seed s
JOIN wards w ON w.id = s.ward_id AND w.province_id = 23
WHERE NOT EXISTS (
    SELECT 1 FROM places p WHERE p.province_id = 23 AND lower(p.name) = lower(s.name)
);

-- Connect the imported POIs to their official national tourism directory
-- entries so the provenance remains queryable from one place.
UPDATE tourism_directory_records d
SET place_id = p.id
FROM places p
WHERE d.provider = 'VNAT_CSDL'
  AND d.provider_category = 'dest'
  AND (d.provider_id, lower(d.name)) IN (
      ('14', lower('Bãi biển Kỳ Co')),
      ('917', lower('Thắng cảnh Eo Gió')),
      ('486', lower('Ghềnh Ráng Tiên Sa')),
      ('257', lower('Tháp Đôi')),
      ('132', lower('Tháp Bánh Ít'))
  )
  AND p.province_id = 23
  AND (
      (d.provider_id = '14' AND lower(p.name) = lower('Bãi biển Kỳ Co')) OR
      (d.provider_id = '917' AND lower(p.name) = lower('Eo Gió')) OR
      (d.provider_id = '486' AND lower(p.name) = lower('Ghềnh Ráng - Tiên Sa')) OR
      (d.provider_id = '257' AND lower(p.name) = lower('Tháp Đôi')) OR
      (d.provider_id = '132' AND lower(p.name) = lower('Tháp Bánh Ít'))
  );
