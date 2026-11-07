from data_collector.crawl_vietnamtourism_directory import (
    has_usable_address,
    parse_page,
)


def test_vnat_directory_parser_keeps_item_identity_and_address():
    html = '''
      <h5>Tổng số: 16 kết quả</h5>
      <div class="listing-shot">
        <div class="cslt-items">
          <h4><a href="/dest/?item=1288">Bảo tàng văn hóa Sa Huỳnh</a></h4>
          <span class="d-block"><i></i> Địa chỉ: 149 Trần Phú, Hội An, Thành phố Đà Nẵng</span>
          <span class="d-block">Điện thoại di động: 0235 000 000</span>
        </div>
      </div>
      <ul><a href="?page=2">2</a><a href="?page=2" rel="next">next</a>
      <a href="?page=2">Cuối cùng</a></ul>
    '''
    records, total, pages = parse_page("dest", html)
    assert (total, pages) == (16, 2)
    assert len(records) == 1
    assert records[0].item_id == "1288"
    assert records[0].category == "Tham quan"
    assert records[0].provider_category == "dest"
    assert records[0].address.endswith("Thành phố Đà Nẵng")
    assert records[0].phone == "0235 000 000"


def test_vnat_directory_rejects_blank_or_unhelpful_addresses():
    assert has_usable_address("Pù Luông, Bá Thước, Thanh Hoá")
    assert not has_usable_address(", Thành phố Cao Lãnh")
    assert not has_usable_address("")
