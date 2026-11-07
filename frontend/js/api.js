const isLocalDevServer = ['5500', '5501', '8081'].includes(location.port);
const defaultApiBase = isLocalDevServer ? 'http://127.0.0.1:8001' : '';
const API_BASE = (window.API_BASE_URL ?? defaultApiBase).replace(/\/$/, '');

async function apiFetch(path, options = {}) {
  const res = await fetch(API_BASE + path, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})}
  });
  const text = await res.text();
  const contentType = res.headers.get('content-type') || '';
  const isHtmlError = /text\/html/i.test(contentType) || /^\s*(?:<!doctype\s+html|<html\b|<head\b|<body\b)/i.test(text);
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = {message: text}; }
  if (!res.ok) {
    if (isHtmlError) throw new Error(`Máy chủ đang tạm lỗi (${res.status}). Bạn thử lại sau ít phút.`);
    const detail = body?.detail;
    const msg = Array.isArray(detail)
      ? detail.map(x => `${x.loc?.join('.') || ''}: ${x.msg || ''}`).join('; ')
      : (detail || body?.message || `HTTP ${res.status}`);
    if (/<(?:!doctype|html|head|body)\b/i.test(String(msg)) || String(msg).length > 600) {
      throw new Error(`Máy chủ đang tạm lỗi (${res.status}). Bạn thử lại sau ít phút.`);
    }
    throw new Error(msg);
  }
  if (isHtmlError) throw new Error('Máy chủ trả về trang lỗi thay vì dữ liệu. Bạn thử lại sau ít phút.');
  return body;
}

function showAlert(id, message, type='error') {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = `<div class="alert ${type}">${escapeHtml(message)}</div>`;
}
function escapeHtml(v) { return String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function money(v, currency='VND') {
  if (v === null || v === undefined || v === '') return 'Chưa có dữ liệu giá';
  const n = Number(v); if (!Number.isFinite(n)) return 'Chưa có dữ liệu giá';
  return `${n.toLocaleString('vi-VN')} ${currency === 'VND' ? '₫' : currency}`;
}
function getPrefs() { return [...document.querySelectorAll('input[type=checkbox][value]:checked')].map(x => x.value); }
let tripStateUserId = null;
function setTripStateUser(user) {
  tripStateUserId = user?.id != null ? String(user.id) : null;
  // Old versions kept a single shared plan for every browser account. It has no
  // owner metadata, so it cannot be safely assigned to the signed-in user.
  localStorage.removeItem('tripState');
  sessionStorage.removeItem('travel_path_assistant_v3');
  sessionStorage.removeItem('travel_chat_history_v1');
}
function userStorageKey(key) { return `${key}:user:${tripStateUserId || 'anonymous'}`; }
function saveTripState(extra={}) {
  const key = userStorageKey('tripState');
  let old = {};
  try { old = JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (_) {}
  localStorage.setItem(key, JSON.stringify({...old, ...extra}));
}
function getTripState() { try { return JSON.parse(localStorage.getItem(userStorageKey('tripState')) || '{}'); } catch { return {}; } }
function clearTripState() { localStorage.removeItem(userStorageKey('tripState')); }

// Lightweight UI language layer used by the main travel flow.
const UI_TEXT = {
  vi: {
    brandTag:'Trợ lý du lịch thông minh · Việt Nam', home:'Trang chủ', plan:'Lập kế hoạch', places:'Địa điểm', newTrip:'Chuyến mới', back:'Quay lại', homeEyebrow:'✦ TRAVEL PATH · TRỢ LÝ DU LỊCH AI', homeHeadline:'Một cú chạm,', homeEmphasis:'vạn hành trình.', homeIntro:'Lên lịch theo điểm đến, ngày đi, ngân sách và sở thích của bạn. Travel Path sắp xếp điểm tham quan, bữa ăn, quán cà phê và khách sạn thành từng ngày để bạn dễ xem và trò chuyện điều chỉnh.', explorePlaces:'Khám phá địa điểm',
    destination:'Điểm đến', days:'Số ngày', travelers:'Số người', travelDate:'Ngày khởi hành', travelDateHint:'Không bắt buộc; giúp kiểm tra giờ mở cửa theo ngày trong tuần.', budget:'Ngân sách', interests:'Sở thích', optional:'không bắt buộc',
    continueHotels:'Tiếp tục chọn nơi lưu trú', hotelTitle:'Chọn nơi lưu trú', hotelSub:'Chọn khu vực bạn muốn ở. Vị trí được tính từ tọa độ trong cơ sở dữ liệu.',
    preferredArea:'Khu vực ưu tiên', nearCenter:'Gần trung tâm', nearBeach:'Gần biển', nearAttractions:'Gần khu tham quan', findHotels:'Tìm nơi lưu trú', choose:'Chọn nơi này',
    itinerary:'Lịch trình', chatTitle:'Trợ lý chuyến đi', chatSub:'Gộp nhiều yêu cầu trong một tin nhắn; mình sẽ cập nhật lịch theo dữ liệu chuyến đi.', chatOnline:'ĐANG HỖ TRỢ', quickHotel:'Đổi khách sạn', quickFood:'Thêm quán ăn', quickDay:'Đổi lịch một ngày', chatFootnote:'Bạn có thể hỏi về lịch hiện tại hoặc yêu cầu đổi nhiều điều cùng lúc.', clear:'Xóa chat', send:'Gửi', newPlan:'Tạo lịch mới',
    chatPlaceholder:'Ví dụ: đổi khách sạn gần biển, 4 người, thêm quán ăn ngày 2…', hotel:'Nơi lưu trú', total:'Tổng dự kiến', day:'Ngày', openMap:'Mở bản đồ', noAddress:'Chưa có địa chỉ', noPlan:'Chưa có lịch trình', startAgain:'Lập chuyến mới',
    loadingHotels:'Đang tìm nơi lưu trú…', loadingPlan:'Đang tạo lịch trình…', loading:'Đang xử lý…', selectPlace:'Chọn địa điểm', noHotels:'Không tìm thấy nơi lưu trú phù hợp.', noData:'Không có dữ liệu phù hợp.', error:'Có lỗi xảy ra: ',
    changed:'Đã cập nhật lịch trình.', assistantWelcome:'Bạn có thể hỏi về chuyến đi hoặc gộp nhiều thay đổi trong một tin nhắn — ví dụ đổi khách sạn, số người và thêm quán ăn ngày 2.', planTitle:'Thiết kế chuyến đi của bạn', planSub:'Chọn điểm đến, thời gian và cách bạn muốn khám phá. Bạn có thể thay đổi mọi thứ sau đó bằng trợ lý AI.', tripBasics:'Thông tin chuyến đi', tripBasicsSub:'Chỉ nhập những gì bạn đã biết.', routeControl:'Điều khiển lịch trình', routeSub:'Bán kính tính đường chim bay từ khách sạn; có thể mở rộng tới Hội An, Tam Kỳ ở tỉnh rộng. Thời gian di chuyển là ước tính.', radius:'Bán kính khám phá từ khách sạn', placesPerDay:'Điểm / ngày', interestSub:'AI ưu tiên điểm tham quan nổi bật trong phạm vi đã chọn và tránh lặp địa điểm.', food:'Ăn uống', sightseeing:'Tham quan', nature:'Thiên nhiên', shopping:'Mua sắm', entertainment:'Giải trí', plannerSideTitle:'Một chuyến đi linh hoạt', benefit1:'Chọn khách sạn theo khu vực bạn ưu tiên.', benefit2:'Mở rộng phạm vi để khám phá nhiều vùng trong tỉnh mới.', benefit3:'Đổi 1, 2 hoặc nhiều địa điểm bằng chat.', benefit4:'Tạo lại toàn bộ lịch với các địa điểm mới.'
  },
  en: {
    brandTag:'Smart travel assistant · Vietnam', home:'Home', plan:'Plan a trip', places:'Places', newTrip:'New trip', back:'Back', homeEyebrow:'✦ TRAVEL PATH · AI TRAVEL ASSISTANT', homeHeadline:'One tap,', homeEmphasis:'a journey made yours.', homeIntro:'Plan around your destination, dates, budget and interests. Travel Path arranges attractions, meals, cafés and hotels by day, then lets you refine the itinerary in chat.', explorePlaces:'Explore places',
    destination:'Destination', days:'Days', travelers:'Travelers', travelDate:'Start date', travelDateHint:'Optional; lets the planner check opening days of the week.', budget:'Budget', interests:'Interests', optional:'optional',
    continueHotels:'Continue to stay', hotelTitle:'Choose your stay', hotelSub:'Choose the area you prefer. Location is calculated from coordinates in the database.',
    preferredArea:'Preferred area', nearCenter:'Near city center', nearBeach:'Near the beach', nearAttractions:'Near attractions', findHotels:'Find stays', choose:'Choose this stay',
    itinerary:'Itinerary', chatTitle:'Trip assistant', chatSub:'Combine several requests in one message. I will update the itinerary using your trip data.', chatOnline:'READY TO HELP', quickHotel:'Change stay', quickFood:'Add a restaurant', quickDay:'Refresh one day', chatFootnote:'Ask about your current plan or request several changes at once.', clear:'Clear chat', send:'Send', newPlan:'Create a new itinerary',
    chatPlaceholder:'For example: stay near the beach, 4 travelers, add food places on Day 2…', hotel:'Your stay', total:'Estimated total', day:'Day', openMap:'Open map', noAddress:'No address', noPlan:'No itinerary yet', startAgain:'Plan a new trip',
    loadingHotels:'Finding stays…', loadingPlan:'Building your itinerary…', loading:'Working…', selectPlace:'Choose a destination', noHotels:'No suitable stays found.', noData:'No matching data.', error:'Something went wrong: ',
    changed:'Itinerary updated.', assistantWelcome:'Ask about your trip or combine several changes in one message — for example, change the hotel, traveler count, and add food places on Day 2.', planTitle:'Design your trip', planSub:'Choose where, when and how you want to explore. You can change everything later with the AI assistant.', tripBasics:'Trip details', tripBasicsSub:'Only enter what you already know.', routeControl:'Route controls', routeSub:'Radius uses straight-line distance from your stay. In a merged province, expand it to explore farther areas. Travel times are estimates.', radius:'Exploration radius from your stay', placesPerDay:'Stops / day', interestSub:'AI prioritizes notable attractions within your chosen range and avoids repeats.', food:'Food', sightseeing:'Sightseeing', nature:'Nature', shopping:'Shopping', entertainment:'Entertainment', plannerSideTitle:'A flexible trip', benefit1:'Choose a stay based on the area you prefer.', benefit2:'Expand your radius to explore more of a merged province.', benefit3:'Change 1, 2 or many places through chat.', benefit4:'Regenerate the whole itinerary with new places.'
  }
};
Object.assign(UI_TEXT.vi, {
  destination:'Chọn tỉnh/thành phố', interestFoodHint:'Quán ngon, món địa phương',
  interestSightseeingHint:'Danh thắng, văn hóa', interestCafeHint:'Cà phê, tiệm bánh',
  interestNatureHint:'Biển, núi, không gian xanh', interestShoppingHint:'Chợ, cửa hàng địa phương',
  interestFunHint:'Vui chơi, hoạt động'
});
Object.assign(UI_TEXT.en, {
  destination:'Choose a province or city', interestFoodHint:'Local dishes and restaurants',
  interestSightseeingHint:'Landmarks and culture', interestCafeHint:'Coffee shops and bakeries',
  interestNatureHint:'Beaches, mountains and green spaces', interestShoppingHint:'Markets and local shops',
  interestFunHint:'Attractions and activities'
});
Object.assign(UI_TEXT.vi, {
  radius3:'3 km · Rất gần', radius5:'5 km · Gần', radius8:'8 km · Cân bằng',
  radius15:'15 km · Khám phá rộng', radius25:'25 km · Khám phá xa', radius50:'50 km · Đi trong vùng', radius80:'80 km · Khám phá tỉnh rộng',
  stops4:'4 điểm · Thoải mái', stops5:'5 điểm', stops6:'6 điểm · Cân bằng', stops7:'7 điểm · Nhiều hoạt động',
  interestGroupLabel:'Chọn sở thích'
});
Object.assign(UI_TEXT.en, {
  radius3:'3 km · Very close', radius5:'5 km · Nearby', radius8:'8 km · Balanced',
  radius15:'15 km · Explore more', radius25:'25 km · Explore farther', radius50:'50 km · Explore the region', radius80:'80 km · Explore the wider province',
  stops4:'4 stops · Relaxed', stops5:'5 stops', stops6:'6 stops · Balanced', stops7:'7 stops · More activities',
  interestGroupLabel:'Choose interests'
});
Object.assign(UI_TEXT.vi, {
  tripSummaryLabel:'TÓM TẮT CHUYẾN ĐI', summaryDestination:'Điểm đến', summaryStart:'Chọn điểm đến để bắt đầu',
  summaryDuration:'Thời gian', summaryTravelers:'Số người', summaryStartDate:'Ngày khởi hành', summaryDateOpen:'Chưa chọn', summaryRadius:'Phạm vi', summaryBudget:'Ngân sách',
  summaryInterests:'Sở thích đã chọn', summaryNoInterests:'Chưa chọn sở thích', summaryBudgetOpen:'Chưa giới hạn', summaryPace:'Nhịp độ', paceAuto:'AI tự phân bổ',
  summaryNote:'AI tự xếp bữa ăn, điểm nổi bật, giờ nghỉ vừa phải và hoạt động buổi tối theo số ngày, sở thích, quãng đường và giờ mở cửa.'
});
Object.assign(UI_TEXT.en, {
  tripSummaryLabel:'TRIP SUMMARY', summaryDestination:'Destination', summaryStart:'Choose a destination to begin',
  summaryDuration:'Duration', summaryTravelers:'Travelers', summaryStartDate:'Start date', summaryDateOpen:'Not set', summaryRadius:'Radius', summaryBudget:'Budget',
  summaryInterests:'Selected interests', summaryNoInterests:'No interests selected', summaryBudgetOpen:'No limit', summaryPace:'Pace', paceAuto:'AI balances each day',
  summaryNote:'AI schedules meals, notable places, sensible breaks and evening activities around your trip length, interests, travel time and opening hours.'
});
Object.assign(UI_TEXT.vi, {
  placesEyebrow:'KHÁM PHÁ VIỆT NAM', placesTitle:'Địa điểm', placesIntro:'Khám phá địa điểm và giá công khai có nguồn trong hệ thống.',
  filterProvince:'Tỉnh / thành', allProvinces:'Tất cả tỉnh/thành', filterCategory:'Danh mục', allCategories:'Tất cả',
  resultLimit:'Số kết quả', searchPlaces:'Tìm địa điểm', onlyVerifiedPrices:'Chỉ hiện giá có nguồn xác thực', placesAttribution:'Dữ liệu địa điểm ©',
  priceSource:'Nguồn giá ↗', priceChecked:'Kiểm tra:', priceUnavailable:'Chưa có giá công khai xác thực', noAddress:'Chưa có địa chỉ',
  noData:'Không có dữ liệu phù hợp.', loadingPlaces:'Đang tải địa điểm…', provinceLoadError:'Không tải được tỉnh/thành: ',
  placeLoadError:'Không tải được địa điểm: ', cafeCategory:'Cafe', stayCategory:'Lưu trú', transportCategory:'Di chuyển'
});
Object.assign(UI_TEXT.en, {
  placesEyebrow:'EXPLORE VIETNAM', placesTitle:'Places', placesIntro:'Explore places and sourced public prices available in the database.',
  filterProvince:'Province / city', allProvinces:'All provinces and cities', filterCategory:'Category', allCategories:'All categories',
  resultLimit:'Number of results', searchPlaces:'Find places', onlyVerifiedPrices:'Show verified prices only', placesAttribution:'Place data ©',
  priceSource:'Price source ↗', priceChecked:'Checked:', priceUnavailable:'No verified public price', noAddress:'No address available',
  noData:'No matching places found.', loadingPlaces:'Loading places…', provinceLoadError:'Could not load provinces: ',
  placeLoadError:'Could not load places: ', cafeCategory:'Cafe', stayCategory:'Stay', transportCategory:'Transport'
});
Object.assign(UI_TEXT.vi, {
  homeProof1:'34 tỉnh, thành', homeProof2:'Theo sở thích và ngân sách', homeProof3:'Có địa chỉ và tuyến đi',
  previewBadge:'✦ HÀNH TRÌNH CỦA BẠN', previewRegion:'CÁ NHÂN HÓA THEO SỞ THÍCH', previewTrip:'3 ngày · 2 người · Tham quan, ăn uống, nghỉ ngơi',
  previewStay:'Nơi lưu trú', previewStayDetail:'Khách sạn gần trung tâm', previewVisit:'Khám phá thành phố',
  previewVisitDetail:'Điểm tham quan phù hợp', previewFood:'Thưởng thức đặc sản', previewFoodDetail:'Gợi ý ăn uống quanh bạn',
  previewRouteCaption:'✦ Được sắp xếp theo khoảng cách', homeSectionEyebrow:'BẮT ĐẦU THẬT DỄ',
  homeSectionTitle:'Hành trình hợp với bạn', homeSectionIntro:'Chọn nơi đến, nơi ở và điều bạn thích. Ứng dụng xếp lịch theo tuyến, thời gian và chi phí công khai có nguồn.',
  homeStep1:'01 · CHỌN ĐIỂM ĐẾN', homeStep1Title:'34 tỉnh, thành để khám phá', homeStep1Text:'Chọn ngày đi, ngân sách và sở thích để lịch trình được cá nhân hóa.',
  homeStep2:'02 · CHỌN NƠI Ở', homeStep2Title:'So sánh nhiều khách sạn', homeStep2Text:'Xem địa chỉ, ảnh và giá có nguồn công khai khi dữ liệu đã được xác minh.',
  homeStep3:'03 · ĐIỀU CHỈNH CÙNG AI', homeStep3Title:'Nhắn điều muốn thay đổi', homeStep3Text:'Yêu cầu thêm, bỏ hoặc đổi hoạt động theo ngày. Lịch được cập nhật cùng tuyến di chuyển.'
});
Object.assign(UI_TEXT.en, {
  homeProof1:'34 provinces and cities', homeProof2:'Your interests and budget', homeProof3:'Addresses and route details',
  previewBadge:'✦ YOUR JOURNEY', previewRegion:'PERSONALIZED TO YOUR INTERESTS', previewTrip:'3 days · 2 travelers · Explore, eat and rest',
  previewStay:'Your stay', previewStayDetail:'A hotel near the city center', previewVisit:'Explore the city',
  previewVisitDetail:'A well-matched attraction', previewFood:'Taste local specialties', previewFoodDetail:'Food suggestions nearby',
  previewRouteCaption:'✦ Arranged by distance', homeSectionEyebrow:'GET STARTED WITH EASE',
  homeSectionTitle:'A journey shaped around you', homeSectionIntro:'Choose a destination, a stay and the experiences you like. The app arranges stops by route, timing and sourced public prices.',
  homeStep1:'01 · CHOOSE A DESTINATION', homeStep1Title:'Explore 34 provinces and cities', homeStep1Text:'Choose your dates, budget and interests to personalize the itinerary.',
  homeStep2:'02 · CHOOSE A STAY', homeStep2Title:'Compare more hotels', homeStep2Text:'Review addresses, photos and sourced public prices when verified data is available.',
  homeStep3:'03 · REFINE WITH AI', homeStep3Title:'Tell the assistant what to change', homeStep3Text:'Ask to add, remove or replace activities by day. The itinerary and route update together.'
});
let uiLang = localStorage.getItem('travel_language') || 'vi';
function tr(key){ return (UI_TEXT[uiLang] && UI_TEXT[uiLang][key]) || UI_TEXT.en[key] || key; }
function getLang(){ return uiLang; }
function setLang(next){ uiLang = next === 'en' ? 'en' : 'vi'; localStorage.setItem('travel_language', uiLang); document.documentElement.lang=uiLang; document.dispatchEvent(new CustomEvent('languageChanged',{detail:uiLang})); applyStaticLanguage(); }
function toggleLang(){ setLang(uiLang === 'vi' ? 'en' : 'vi'); }
function applyStaticLanguage(){
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = tr(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = tr(el.dataset.i18nPlaceholder); });
  const b=document.getElementById('languageToggle'); if(b) b.textContent=uiLang==='vi'?'EN':'VI';
}
document.addEventListener('DOMContentLoaded',()=>{ document.documentElement.lang=uiLang; applyStaticLanguage(); const b=document.getElementById('languageToggle'); if(b)b.addEventListener('click',toggleLang); });

