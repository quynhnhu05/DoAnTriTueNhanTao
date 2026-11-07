BEGIN;
UPDATE places AS place
SET province_id = province.id
FROM provinces AS province
WHERE place.province_id IS NULL
  AND TRIM(COALESCE(place.province, '')) = TRIM(province.name);
COMMIT;
