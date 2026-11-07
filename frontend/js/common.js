const API_BASE = "http://127.0.0.1:8000";

const I18N = {
  vi: {
    brandTag: "Du lịch thông minh · Trải nghiệm tốt hơn", home: "Trang chủ", planner: "Lập kế hoạch", places: "Địa điểm", hotels: "Khách sạn", itinerary: "Lịch trình",
    heroEyebrow: "AI SMART TRAVEL", heroTitle: "Hành trình của bạn,<br><em>được lên kế hoạch thông minh.</em>", heroText: "Chọn điểm đến, số ngày, số người và sở thích. Hệ thống sẽ dùng dữ liệu du lịch hiện có để gợi ý địa điểm, khách sạn và tạo lịch trình.", startPlanning: "Bắt đầu lập kế hoạch →", explorePlaces: "Khám phá địa điểm",
    destination: "Điểm đến", days: "Số ngày", people: "Số người", interests: "Bạn thích gì?", optional: "không bắt buộc", budget: "Ngân sách mục tiêu cho cả nhóm (VND)", budgetHint: "Để trống nếu không muốn giới hạn ngân sách.", budgetRequired: "Bắt buộc nhập để lập lịch phù hợp với ngân sách.", budgetTargetHint: "Giá công khai gần đây giúp ưu tiên quán/điểm phù hợp. Giá thiếu nguồn sẽ không được tự tạo.", nextHotels: "Tiếp tục chọn khách sạn →", findPlaces: "Tìm địa điểm phù hợp →",
    selectInterests: "Chọn một hoặc nhiều sở thích", attractions: "Tham quan", food: "Ăn uống", cafe: "Cafe", nature: "Thiên nhiên", entertainment: "Giải trí", play: "Vui chơi", shopping: "Mua sắm",
    howTitle: "Cách hệ thống hoạt động", step1: "1. Chọn điểm đến và sở thích.", step2: "2. Chọn khách sạn từ dữ liệu phù hợp.", step3: "3. Hệ thống tạo lịch trình quanh khách sạn.",
    hotelTitle: "Chọn nơi lưu trú", hotelIntro: "Khách sạn được xếp theo khu vực ưu tiên, ngân sách, rating và review từ dữ liệu backend.", preferredArea: "Khu vực ưu tiên", nearCenter: "Gần trung tâm", nearBeach: "Gần biển", nearAttractions: "Gần khu tham quan", findHotels: "Tìm khách sạn →", selected: "Đã chọn", choose: "Chọn khách sạn này", perNight: "/ đêm", noHotels: "Không tìm thấy khách sạn phù hợp.", candidates: "ứng viên", distance: "Khoảng cách", rating: "Rating", reviews: "review", priceUnavailable: "Chưa có giá trong dữ liệu", estimated: "Ước tính", real: "Dữ liệu DB", totalHotel: "Tổng tiền khách sạn dự kiến", nights: "đêm",
    itineraryTitle: "Lịch trình của bạn", itineraryIntro: "Các điểm được sắp theo từng ngày và lấy trực tiếp từ kết quả /api/plan.", hotelBase: "Nơi lưu trú", day: "Ngày", activities: "hoạt động", distanceFromHotel: "km từ khách sạn", costPerPerson: "Chi phí/người", totalCost: "Tổng chi phí", costSummary: "Tổng chi phí chuyến đi", realCost: "Chi phí có dữ liệu", estimatedCost: "Chi phí ước tính", withinBudget: "Trong ngân sách", overBudget: "Vượt ngân sách", noBudget: "Không đặt ngân sách", planAgain: "Lập chuyến khác", backHotels: "← Chọn lại khách sạn", noPlan: "Chưa có lịch trình", noPlanText: "Hãy lập chuyến đi và chọn khách sạn trước.",
    placesTitle: "Khám phá địa điểm", placesIntro: "Dữ liệu được lấy trực tiếp từ /api/places.", allProvinces: "Tất cả tỉnh/thành", allCategories: "Tất cả danh mục", category: "Danh mục", loadPlaces: "Tải dữ liệu →", noPlaces: "Không có dữ liệu phù hợp.", map: "Bản đồ", results: "kết quả", imageUnavailable: "Không có ảnh trong dữ liệu",
    loading: "Đang tải...", loadingHotels: "Đang tìm khách sạn...", loadingPlan: "Đang tạo lịch trình...", backendError: "Không thể kết nối backend. Hãy chạy FastAPI tại http://127.0.0.1:8000.", httpError: "Lỗi HTTP", required: "Vui lòng chọn điểm đến.", invalidPlan: "Dữ liệu kế hoạch không hợp lệ.", success: "Thành công", image: "Ảnh", address: "Địa chỉ", noAddress: "Chưa có địa chỉ", noRating: "Chưa có rating", noReviews: "Chưa có review", score: "Điểm xếp hạng", currency: "VND",
    homeCard1: "Dữ liệu tỉnh/thành từ backend", homeCard2: "Khách sạn được xếp hạng theo logic backend", homeCard3: "Lịch trình dựa trên tọa độ và địa điểm quanh khách sạn", language: "EN", switchTo: "English"
  },
  en: {
    brandTag: "Travel smarter · Travel better", home: "Home", planner: "Plan a trip", places: "Places", hotels: "Hotels", itinerary: "Itinerary",
    heroEyebrow: "AI SMART TRAVEL", heroTitle: "Your journey,<br><em>planned intelligently.</em>", heroText: "Choose a destination, number of days, travelers and interests. The system uses the available tourism data to recommend places, hotels and build an itinerary.", startPlanning: "Start planning →", explorePlaces: "Explore places",
    destination: "Destination", days: "Days", people: "Travelers", interests: "What do you enjoy?", optional: "optional", budget: "Target budget for the group (VND)", budgetHint: "Leave empty if you do not want a budget limit.", budgetRequired: "Required to build a plan within your budget.", budgetTargetHint: "Recent public prices help prioritize affordable venues. Prices without a source are never invented.", nextHotels: "Continue to hotels →", findPlaces: "Find matching places →",
    selectInterests: "Choose one or more interests", attractions: "Attractions", food: "Food & dining", cafe: "Cafes", nature: "Nature", entertainment: "Entertainment", play: "Activities", shopping: "Shopping",
    howTitle: "How it works", step1: "1. Choose a destination and interests.", step2: "2. Choose a hotel from the available data.", step3: "3. The system builds the itinerary around your hotel.",
    hotelTitle: "Choose a place to stay", hotelIntro: "Hotels are ranked by preferred area, budget, rating and reviews from the backend data.", preferredArea: "Preferred area", nearCenter: "Near city center", nearBeach: "Near the beach", nearAttractions: "Near attractions", findHotels: "Find hotels →", selected: "Selected", choose: "Choose this hotel", perNight: "/ night", noHotels: "No suitable hotels were found.", candidates: "candidates", distance: "Distance", rating: "Rating", reviews: "reviews", priceUnavailable: "No price in the data", estimated: "Estimated", real: "DB data", totalHotel: "Estimated hotel total", nights: "nights",
    itineraryTitle: "Your itinerary", itineraryIntro: "Activities are grouped by day and rendered directly from the /api/plan response.", hotelBase: "Your stay", day: "Day", activities: "activities", distanceFromHotel: "km from hotel", costPerPerson: "Cost/person", totalCost: "Total cost", costSummary: "Trip cost summary", realCost: "Data-backed cost", estimatedCost: "Estimated cost", withinBudget: "Within budget", overBudget: "Over budget", noBudget: "No budget limit", planAgain: "Plan another trip", backHotels: "← Choose another hotel", noPlan: "No itinerary yet", noPlanText: "Plan a trip and choose a hotel first.",
    placesTitle: "Explore places", placesIntro: "Data is loaded directly from /api/places.", allProvinces: "All provinces / cities", allCategories: "All categories", category: "Category", loadPlaces: "Load data →", noPlaces: "No matching data.", map: "Map", results: "results", imageUnavailable: "No image in the data",
    loading: "Loading...", loadingHotels: "Finding hotels...", loadingPlan: "Building itinerary...", backendError: "Cannot connect to the backend. Run FastAPI at http://127.0.0.1:8000.", httpError: "HTTP error", required: "Please choose a destination.", invalidPlan: "Invalid plan data.", success: "Success", image: "Image", address: "Address", noAddress: "No address", noRating: "No rating", noReviews: "No reviews", score: "Ranking score", currency: "VND",
    homeCard1: "Province/city data comes from the backend", homeCard2: "Hotels are ranked by backend logic", homeCard3: "Itineraries use coordinates and places around the selected hotel", language: "VI", switchTo: "Tiếng Việt"
  }
};

let currentLanguage = localStorage.getItem("travel_language") || "vi";
if (!I18N[currentLanguage]) currentLanguage = "vi";

function t(key) { return I18N[currentLanguage][key] ?? I18N.en[key] ?? key; }
function lang() { return currentLanguage; }
function setLanguage(next) { if (!I18N[next]) return; currentLanguage = next; localStorage.setItem("travel_language", next); applyLanguage(); }
function toggleLanguage() { setLanguage(currentLanguage === "vi" ? "en" : "vi"); }

function applyLanguage() {
  document.documentElement.lang = currentLanguage;
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const value = t(el.dataset.i18n);
    if (value !== undefined) el.innerHTML = value;
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => el.placeholder = t(el.dataset.i18nPlaceholder));
  const langBtn = document.getElementById("languageToggle");
  if (langBtn) langBtn.textContent = t("language");
  document.dispatchEvent(new Event("languageChanged"));
}

function installLanguageToggle() {
  const btn = document.getElementById("languageToggle");
  if (btn) btn.onclick = toggleLanguage;
  applyLanguage();
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(API_BASE + path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) }
    });
  } catch (error) {
    throw new Error(t("backendError"));
  }
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; }
  catch { throw new Error(`${t("httpError")} ${response.status}`); }
  if (!response.ok) throw new Error(data.detail || data.message || `${t("httpError")} ${response.status}`);
  return data;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"}[c]));
}
function save(key, value) { localStorage.setItem(key, JSON.stringify(value)); }
function load(key) { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; } }
function remove(key) { localStorage.removeItem(key); }
function formatNumber(value, digits = 0) { const n = Number(value); return Number.isFinite(n) ? n.toLocaleString(currentLanguage === "vi" ? "vi-VN" : "en-US", { maximumFractionDigits: digits }) : "—"; }
function money(value, currency = "VND") { if (value == null || value === "") return "—"; const n = Number(value); if (!Number.isFinite(n)) return esc(value); return `${formatNumber(n)} ${currency === "VND" ? "₫" : esc(currency)}`; }
function showAlert(message, type = "error") { const box = document.getElementById("alert"); if (box) box.innerHTML = `<div class="alert ${type}">${esc(message)}</div>`; }
function clearAlert() { const box = document.getElementById("alert"); if (box) box.innerHTML = ""; }
function busy(button, value, label) { if (!button) return; button.disabled = value; if (value) { button.dataset.oldText = button.innerHTML; button.innerHTML = `<span class="spinner"></span>${esc(label || t("loading"))}`; } else if (button.dataset.oldText) button.innerHTML = button.dataset.oldText; }
function icon(category = "") { const c = String(category).toLowerCase(); if (c.includes("ăn") || c.includes("food")) return "🍜"; if (c.includes("tham") || c.includes("attract")) return "🏛️"; if (c.includes("cafe")) return "☕"; if (c.includes("thiên") || c.includes("nature")) return "🌿"; if (c.includes("mua") || c.includes("shop")) return "🛍️"; if (c.includes("giải") || c.includes("entertain")) return "🎭"; if (c.includes("vui") || c.includes("play")) return "🎡"; if (c.includes("lưu") || c.includes("hotel") || c.includes("stay")) return "🏨"; return "📍"; }
function imageHtml(url, alt, className = "place-image") { return url ? `<div class="${className}"><img src="${esc(url)}" alt="${esc(alt)}" loading="lazy" onerror="this.parentElement.classList.add('image-missing');this.remove()"></div>` : `<div class="${className} image-missing"><span>📷</span><small>${esc(t("imageUnavailable"))}</small></div>`; }
function invalidateMap(map) { if (map) requestAnimationFrame(() => setTimeout(() => { try { map.invalidateSize({pan:false, animate:false}); } catch (_) {} }, 80)); }
function getTrip() { return load("travel_trip"); }
function saveTrip(trip) { save("travel_trip", trip); save("travel_request", { province_code: trip.province_code, province_id: trip.province_id ?? null, days: trip.days, people: trip.people, budget: trip.budget, preferences: trip.preferences }); }

document.addEventListener("DOMContentLoaded", () => installLanguageToggle());
document.addEventListener("languageChanged", () => { const event = new CustomEvent("frontendLanguageChanged", { detail: currentLanguage }); document.dispatchEvent(event); });
