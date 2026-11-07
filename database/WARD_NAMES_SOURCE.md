# Ward and commune display names

`ward_names_source.json` supplies Vietnamese display names and type labels for
the boundary records imported into `wards`. Its sanitized geometry filenames
do not retain Vietnamese diacritics, so `normalize_ward_names.py` matches by
province and normalized name before restoring the official-form labels.

Source: [Vietnam Address Data](https://github.com/phucanhle/vn-xaphuong-2025),
which describes the data as effective from 1 July 2025 and provides it under
the MIT license. The government announcement for the 34 provinces and 3,321
commune-level units is [here](https://baochinhphu.vn/bang-danh-muc-va-ma-so-cua-34-tinh-thanh-moi-3321-don-vi-hanh-chinh-cap-xa-moi-102250704153652947.htm).

The reconciliation updates labels only; it does not change ward codes or
boundary geometry. Records without an exact name match retain their imported
label and are still shown with a readable `Phường`/`Xã` prefix inferred from
the boundary name.
