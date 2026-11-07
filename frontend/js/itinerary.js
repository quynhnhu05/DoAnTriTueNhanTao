(async function(){
 let activePlan=null;
 let renderVersion=0;
 const dayMaps=new Map();
 const placeIcon=value=>{const c=String(value||'').toLowerCase();if(c.includes('ăn'))return '🍜';if(c.includes('cafe'))return '☕';if(c.includes('thiên nhiên'))return '🌿';if(c.includes('vui chơi'))return '🎡';if(c.includes('giải trí'))return '🎭';if(c.includes('mua sắm'))return '🛍️';return '🏛️';};
 const categoryI18n={'Ăn uống':'food','Cafe':'cafeCategory','Tham quan':'sightseeing','Vui chơi':'entertainment','Giải trí':'entertainment','Thiên nhiên':'nature','Mua sắm':'shopping','Lưu trú':'stayCategory','Di chuyển':'transportCategory'};
 const slotI18n={'Ăn sáng':'Breakfast','Tham quan buổi sáng':'Morning sightseeing','Vui chơi buổi sáng':'Morning activities','Ăn trưa':'Lunch','Nghỉ trưa':'Rest break','Nghỉ trưa tại khu vực tham quan':'Rest near the lunch area','Về khách sạn nghỉ trưa':'Return to the hotel for a midday rest','Nghỉ':'Rest','Tham quan buổi chiều':'Afternoon sightseeing','Thiên nhiên buổi chiều':'Nature in the afternoon','Vui chơi buổi chiều':'Afternoon activities','Cafe / ăn nhẹ':'Coffee / snack','Ăn tối':'Dinner','Vui chơi / tham quan buổi tối':'Evening activities','Tham quan buổi tối':'Evening sightseeing','Cafe buổi sáng':'Morning coffee','Thiên nhiên buổi sáng':'Nature in the morning','Mua sắm buổi chiều':'Afternoon shopping','Trở về trung tâm và nghỉ ngơi':'Return to the city and rest','Gửi hành lý / làm thủ tục lưu trú':'Drop off luggage / arrange your stay','Trả phòng / gửi hành lý':'Check out / store luggage','Nghỉ chân và uống nước':'Take a break and hydrate','Về khách sạn tắm rửa và nghỉ trước bữa tối':'Return to the hotel to freshen up','Về khách sạn nghỉ ngơi':'Return to the hotel and rest','Về khách sạn nhận hành lý và khởi hành':'Collect luggage and depart'};
 function formatTripDate(value,weekday=false){if(!value)return '';const date=new Date(`${value}T12:00:00`);return Number.isNaN(date.valueOf())?'':date.toLocaleDateString(getLang()==='en'?'en-US':'vi-VN',{...(weekday?{weekday:'long'}:{}),day:'numeric',month:'short',year:'numeric'});}
 function localCategory(value){const key=categoryI18n[value];return getLang()==='en'&&key?tr(key):value;}
 function localSlot(value){return getLang()==='en'?(slotI18n[value]||localCategory(value)):value;}
 const addressKey=value=>String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/đ/gi,'d').replace(/^(tinh|province)\s+/i,'').replace(/[^a-z0-9]+/g,' ').trim().toLowerCase();
 const legacyProvinceNames=new Set(['Hà Giang','Yên Bái','Bắc Kạn','Vĩnh Phúc','Hòa Bình','Bắc Giang','Thái Bình','Hải Dương','Hà Nam','Nam Định','Quảng Bình','Quảng Nam','Kon Tum','Bình Định','Ninh Thuận','Đắk Nông','Bình Thuận','Phú Yên','Bình Phước','Bà Rịa - Vũng Tàu','Bình Dương','Long An','Tiền Giang','Bến Tre','Trà Vinh','Sóc Trăng','Hậu Giang','Bạc Liêu','Kiên Giang'].map(addressKey));
 function addressLine(place){const ward=String(place?.ward||'').trim();const district=String(place?.district||'').trim();const province=String(place?.province||'').trim();const admin=[ward,district,province].map(addressKey).filter(Boolean);const base=String(place?.address||'').split(',').map(x=>x.trim()).filter(Boolean);const obsoleteLevel=/^(phường|phuong|xã|xa|thị trấn|thi tran|đặc khu|dac khu|quận|quan|huyện|huyen|thị xã|thi xa|thành phố|thanh pho|tỉnh|tinh)\b/i;const streets=base.filter(x=>{const key=addressKey(x);if(!key||obsoleteLevel.test(x)||legacyProvinceNames.has(key))return false;return !admin.some(level=>key===level||(Math.min(key.length,level.length)>=5&&(key.includes(level)||level.includes(key))));}).slice(0,2);if(streets.length===2&&/^\d+[a-z]?$/i.test(streets[0]))streets[0]=`${streets[0]} ${streets.pop()}`;const parts=[...streets,ward||district,province].filter(Boolean);const seen=new Set();const value=parts.filter(x=>{const key=addressKey(x);if(!key||seen.has(key))return false;seen.add(key);return true;}).join(', ');return value?`<p>📍 ${escapeHtml(value)}</p>`:'';}
 function coordinateLine(place){const lat=Number(place?.latitude),lon=Number(place?.longitude);if(!Number.isFinite(lat)||!Number.isFinite(lon))return '';const url=`https://www.google.com/maps/search/?api=1&query=${lat},${lon}`;return `<p>📍 <a href="${url}" target="_blank" rel="noopener">${lat.toFixed(5)}, ${lon.toFixed(5)} · Mở vị trí trên bản đồ ↗</a></p>`;}
 function localDayTitle(value,day){
   if(getLang()!=='en')return value||`Ngày ${day}`;
   const titles={'Khám phá và ẩm thực':'Exploration and local food','Trải nghiệm địa phương':'Local experiences','Ẩm thực và vui chơi':'Food and entertainment'};
   return titles[value]||value||`Explore Day ${day}`;
 }
 let state=getTripState();
 if(!state.province_code||!state.hotel_id){location.href='planner.html';return;}
 const summary=document.getElementById('tripSummary');
 summary.textContent=`${state.province_name||state.province_code} · ${state.days} ${getLang()==='en'?'days':'ngày'} · ${state.people} ${getLang()==='en'?'travelers':'người'}${state.start_date?` · ${formatTripDate(state.start_date)}`:''} · ${state.hotel_name||tr('hotel')}`;
 try{
   const payload={province_code:state.province_code,hotel_id:Number(state.hotel_id),days:Number(state.days),people:Number(state.people),budget:state.budget==null?null:Number(state.budget),preferences:Array.isArray(state.preferences)?state.preferences:[],start_date:state.start_date||null,radius_km:Number(state.radius_km||50)};
   const raw=await apiFetch('/api/plan',{method:'POST',body:JSON.stringify(payload)});
   if(raw?.success===false)throw new Error(raw.message||'Không tạo được plan');
   const data=raw?.data??raw;
   saveTripState({plan:data,itinerary:data.itinerary||data.plan||[]});
   // Hiện layout trước rồi mới khởi tạo Leaflet. Đây là lỗi chính khiến map trước đó chỉ là một ô xám lớn.
   document.getElementById('loading').classList.add('hidden');
   document.getElementById('content').classList.remove('hidden');
   render(data);
   window.dispatchEvent(new CustomEvent('trip:ready'));
 }catch(e){
   document.getElementById('loading').classList.add('hidden');
   if(/hotel_id/i.test(String(e.message||''))){
     const hotelName=String(state.hotel_name||'khách sạn đang chọn');
     saveTripState({hotel_id:null,hotel_name:''});
     sessionStorage.setItem('hotelAddressRequired',hotelName);
     location.replace('hotels.html');
     return;
   }
   document.getElementById('empty').classList.remove('hidden');
   showAlert('alertBox','Không tạo được lịch trình: '+e.message);
 }

 function render(data){
   const version=++renderVersion;
   activePlan=data;
   state={...state,...getTripState(),
     province_code:data.province_code??state.province_code,
     days:data.days??state.days,
     people:data.people??state.people,
     budget:data.budget??state.budget,
     start_date:data.start_date??state.start_date,
     radius_km:data.radius_km??state.radius_km,
     hotel_id:data.hotel?.id??state.hotel_id,
     hotel_name:data.hotel?.name??state.hotel_name};
   dayMaps.forEach(map=>{try{map.remove();}catch(_){}});
   dayMaps.clear();
   const hotel=data.hotel||data.accommodation||null;
   const radius=data.radius_km||state.radius_km||50;
   const meta=document.getElementById('tripSummary'); if(meta) meta.textContent=`${state.province_name||state.province_code} · ${state.days} ${getLang()==='en'?'days':'ngày'} · ${state.people} ${getLang()==='en'?'travelers':'người'}${state.start_date?` · ${formatTripDate(state.start_date)}`:''} · ${state.hotel_name||tr('hotel')} · ${radius} km`;
   if(hotel)document.getElementById('hotelSummary').innerHTML=`<div class="card hotel-summary"><figure class="hotel-photo"><div class="photo-placeholder">🏨</div><img data-place-image data-image-url="${escapeHtml(hotel.image_url||'')}" data-image-source-url="${escapeHtml(hotel.image_source_url||'')}" data-image-source-name="${escapeHtml(hotel.image_source_name||'')}" data-image-license="${escapeHtml(hotel.image_license||'')}" data-name="${escapeHtml(hotel.name||'')}" data-province="${escapeHtml(hotel.province||state.province_name||'')}" data-category="Lưu trú" alt="Ảnh ${escapeHtml(hotel.name||'khách sạn')}" loading="lazy"><a class="image-credit" aria-label="Nguồn ảnh" tabindex="-1"></a></figure><span class="eyebrow">${getLang()==='en'?'STAY':'NƠI LƯU TRÚ'}</span><h2>${escapeHtml(hotel.name)}</h2>${addressLine(hotel)}${coordinateLine(hotel)}<div class="stats"><span>⭐ ${hotel.rating!=null?Number(hotel.rating).toFixed(1):'—'}</span><span>💬 ${hotel.review_count??0}</span>${hotel.latitude!=null&&hotel.longitude!=null?`<span>🗺️ ${Number(hotel.latitude).toFixed(5)}, ${Number(hotel.longitude).toFixed(5)}</span>`:''}</div></div>`;
   if(hotel&&typeof loadPlaceImages==='function')loadPlaceImages(document.getElementById('hotelSummary'));
   const cost=data.cost_summary||null;
   const days=data.itinerary||data.plan||[];
   if(cost){
     const activities=days.flatMap(d=>(d.schedule||[]).map(s=>s.place).filter(Boolean));
     const travelerCount=Number(data.people??state.people??1);
     const target=Number(data.budget??state.budget);
     const targetSet=Number.isFinite(target)&&target>0;
     const used=Number(cost.total_estimated_trip_cost??cost.known_cost);
     const hasUsed=Number.isFinite(used);
     const missing=Number(cost.unpriced_places||0);
     const remaining=targetSet&&hasUsed&&missing===0?target-used:null;
     const targetText=targetSet?money(target):(getLang()==='en'?'Not set':'Chưa đặt');
     const usedText=hasUsed?money(used):(getLang()==='en'?'Insufficient data':'Chưa đủ dữ liệu');
     const remainingText=remaining==null?(targetSet&&missing>0?(getLang()==='en'?'Incomplete prices':'Thiếu giá một số điểm'):(getLang()==='en'?'—':'—')):money(remaining);
     const remainingClass=remaining!=null&&remaining<0?'budget-over':'budget-ok';
     const completeness=missing>0?(getLang()==='en'?` · ${missing} stops lack an estimate`:` · còn ${missing} điểm thiếu mức dự kiến`):'';
     document.getElementById('costSummary').innerHTML=`<div class="cost card budget-summary"><div><small>${getLang()==='en'?'TARGET BUDGET':'NGÂN SÁCH MỤC TIÊU'}</small><b>${targetText}</b></div><div><small>${getLang()==='en'?`PLANNED SPEND · ${travelerCount} TRAVELERS`:`CHI PHÍ DỰ KIẾN · ${travelerCount} NGƯỜI`}</small><b>${usedText}${completeness}</b></div><div class="${remainingClass}"><small>${getLang()==='en'?'BUDGET REMAINING':'NGÂN SÁCH CÒN LẠI'}</small><b>${remainingText}</b></div></div><p class="cost-disclosure">${getLang()==='en'?'Includes estimated lodging and itinerary meals/activities; transport is excluded. Venue and room costs are planning estimates unless a sourced price is shown.':'Đã gồm tiền phòng dự kiến và các bữa ăn/hoạt động trong lịch; chưa gồm di chuyển. Chi phí phòng và địa điểm là mức dự tính, trừ nơi có giá nguồn.'}</p>`;
   }
   if(!Array.isArray(days)||!days.length){document.getElementById('itinerary').innerHTML='<div class="empty"><h2>API không trả về itinerary</h2></div>';return;}
   const warningBox=document.getElementById('planningWarnings');
   if(warningBox){
     const warnings=(Array.isArray(data.planning_warnings)?data.planning_warnings:[])
       .filter(message=>!/(chưa đủ mục tiêu hoạt động|hoạt động chưa thể xếp|chưa có điểm tham quan)/i.test(String(message)));
     warningBox.innerHTML=warnings.map(message=>`<div class="planning-warning" role="status">${escapeHtml(message)}</div>`).join('');
   }
   document.getElementById('itinerary').innerHTML=days.map((d,i)=>renderDay(d,i,hotel,days.length)).join('');
   if(typeof loadPlaceImages==='function')loadPlaceImages(document.getElementById('itinerary'));
   requestAnimationFrame(()=>setTimeout(()=>{if(version===renderVersion)drawDayMaps(hotel,days);},120));
 }

 function renderDay(day,index,hotel,totalDays){
   const entries=day.schedule||[];
   const mapId=`day-map-${index}`;
   const dateLabel=formatTripDate(day.date,true);
   const activityCount=Number(day.activity_count??entries.filter(s=>s.place).length);
   const publishedCount=Number(day.published_price_places??entries.filter(s=>s.place&&(s.place.price_min!=null||s.place.price_max!=null)&&s.place.price_source_url&&s.place.price_checked_at).length);
   const withoutPublicPrice=Number(day.activities_without_public_price??Math.max(0,activityCount-publishedCount));
   const distribution=day.activity_distribution||{};
   const periodCounts={
     morning:Number(distribution.morning??entries.filter(s=>s.place&&s.activity_period==='morning').length),
     afternoon:Number(distribution.afternoon??entries.filter(s=>s.place&&s.activity_period==='afternoon').length),
     evening:Number(distribution.evening??entries.filter(s=>s.place&&s.activity_period==='evening').length)
   };
   const meals=entries.filter(s=>s.place&&['Ăn sáng','Ăn trưa','Ăn tối'].includes(s.type)).length;
   const cafes=entries.filter(s=>s.place&&s.type&&s.type.toLowerCase().includes('cafe')).length;
   const departureDay=index===totalDays-1&&totalDays>1;
   const structureText=departureDay
     ? (getLang()==='en'?`Departure day · ${periodCounts.morning+periodCounts.afternoon} light activity stops · ${cafes} cafe`:`Ngày khởi hành · ${periodCounts.morning+periodCounts.afternoon} hoạt động nhẹ · ${cafes} quán cafe`)
     : (getLang()==='en'?`Activities: morning ${periodCounts.morning}/4 · afternoon ${periodCounts.afternoon}/3 · evening ${periodCounts.evening}/2`:`Hoạt động: sáng ${periodCounts.morning}/4 · chiều ${periodCounts.afternoon}/3 · tối ${periodCounts.evening}/2`);
   const returnLeg=day.route?.return_to_hotel;
   const returnLabel=returnLeg
     ? (returnLeg.distance_method==='osrm_road_route'
       ? (getLang()==='en'?` · Return to hotel: ${returnLeg.distance_km} km, about ${returnLeg.estimated_minutes} min (arrival ${returnLeg.estimated_arrival}).`:` · Về khách sạn: ${returnLeg.distance_km} km, khoảng ${returnLeg.estimated_minutes} phút (dự kiến ${returnLeg.estimated_arrival}).`)
       : (getLang()==='en'?` · Return estimate: ${returnLeg.distance_km} km straight-line, about ${returnLeg.estimated_minutes} min.`:` · Đường về ước tính: ${returnLeg.distance_km} km đường chim bay, khoảng ${returnLeg.estimated_minutes} phút.`))
     : '';
   const routeDisclosure=(day.route?.source==='OSRM'
     ? (getLang()==='en'?'Road route estimated by OSRM using © OpenStreetMap data; no live traffic. Stops are numbered in visit order.':'Tuyến OSRM dùng dữ liệu © OpenStreetMap; chưa có giao thông trực tiếp. Số thứ tự là thứ tự ghé điểm.')
     : (getLang()==='en'?'Road routing is unavailable. The dashed line connects places directly and is not a road route. Stops are numbered in visit order.':'Chưa lấy được tuyến đường bộ. Đường nét đứt chỉ nối thẳng các điểm, không phải đường đi thực tế. Số thứ tự là thứ tự ghé điểm.'))+returnLabel;
   return `<article class="day card"><div class="day-head"><div><span class="eyebrow">${getLang()==='en'?'DAY':'NGÀY'}</span><h2>${escapeHtml(day.day)}</h2><p>${escapeHtml(localDayTitle(day.title,day.day))}</p>${dateLabel?`<small class="day-date">${escapeHtml(dateLabel)}</small>`:''}<small class="day-structure">${structureText} · ${getLang()==='en'?`meals ${meals}/3`:`bữa ăn ${meals}/3`}</small></div><div class="day-cost">${day.estimated_day_cost!=null?`<b>${money(day.estimated_day_cost)}</b>`:''}<small>${getLang()==='en'?`${activityCount} planned stops · ${withoutPublicPrice} without a published price`:`${activityCount} hoạt động · ${withoutPublicPrice} nơi chưa có giá công khai`}</small></div></div><div class="day-content"><div class="day-schedule">${entries.map(s=>renderSlot(s)).join('')}</div><aside class="mini-map-wrap"><div class="mini-map-title"><b>🗺️ ${getLang()==='en'?'Day':'Ngày'} ${escapeHtml(day.day)}</b><span>OpenStreetMap</span></div><div id="${mapId}" class="mini-map"></div><small class="route-disclosure">${routeDisclosure}</small><a class="map-link" data-map-link="${mapId}" target="_blank" rel="noopener">${tr('openMap')} ↗</a></aside></div></article>`;
 }

 function renderSlot(s){
   const p=s.place;
   if(!p&&s.status==='unavailable')return '';
   if(!p){const travel=s.travel_to_hotel;const travelLabel=travel?.estimated_minutes!=null?(getLang()==='en'?`Travel back to hotel · about ${travel.estimated_minutes} min${travel.distance_km!=null?` / ${Number(travel.distance_km).toFixed(1)} km`:''}`:`Di chuyển về khách sạn · khoảng ${travel.estimated_minutes} phút${travel.distance_km!=null?` / ${Number(travel.distance_km).toFixed(1)} km`:''}`):'';const location=s.location?`<p>📍 ${escapeHtml(s.location)}${s.location_address?` · ${escapeHtml(s.location_address)}`:''}</p>`:'';const mealClass=s.status==='flexible'&&s.type==='Ăn tối'?' meal-flexible':'';return `<div class="slot rest${mealClass}"><div class="time">${escapeHtml(s.start_time||'')} – ${escapeHtml(s.end_time||'')}</div><div><b>${escapeHtml(localSlot(s.type||'Nghỉ'))}</b><p>${escapeHtml((getLang()==='en'?s.description_en:s.description)||(getLang()==='en'?'Rest break':'Khoảng nghỉ'))}</p>${location}${travelLabel?`<small class="travel-estimate">${escapeHtml(travelLabel)}</small>`:''}</div></div>`;}
   const checked=p.price_checked_at?new Date(p.price_checked_at).toLocaleString(getLang()==='en'?'en-US':'vi-VN',{dateStyle:'short',timeStyle:'short'}):'';
   const hasPublishedPrice=(p.price_min!=null||p.price_max!=null)&&Boolean(p.price_source_url&&p.price_checked_at);
   const range=[p.price_min,p.price_max].filter(x=>x!=null).map(x=>money(x)).join(' – ');
   const priceBlock=p.cost_per_person!=null
     ? `<div class="activity-cost">${money(p.cost_per_person)}/người · ${money(p.total_cost)}${p.cost_type==='estimated'?'':`<small>${getLang()==='en'?'Calculated from a sourced per-person price.':'Tính từ giá/người có nguồn.'}</small>`}</div>`
     : hasPublishedPrice
       ? `<div class="activity-listed-price">${getLang()==='en'?'Published price':'Giá niêm yết'}: ${range}<small>${getLang()==='en'?'A listed item or offer; not included in the trip total.':'Giá món hàng/dịch vụ được niêm yết; chưa cộng vào tổng chuyến đi.'}</small></div>`
       : `<div class="activity-price-missing">${getLang()==='en'?'No verified public price':'Chưa có giá công khai được xác thực'}</div>`;
   const sourceBlock=hasPublishedPrice?`<small class="activity-price-source"><a href="${escapeHtml(p.price_source_url)}" target="_blank" rel="noopener">${getLang()==='en'?'Price source':'Nguồn giá'} ↗</a>${checked?` · ${getLang()==='en'?'checked':'lấy lúc'} ${escapeHtml(checked)}`:''}</small>`:'';
   const directoryUrl=String(p.directory_source_url||'');
   const directoryBlock=/^https:\/\//i.test(directoryUrl)?`<small class="activity-price-source"><a href="${escapeHtml(directoryUrl)}" target="_blank" rel="noopener">${getLang()==='en'?'Official tourism listing':'Thông tin từ Cục Du lịch'} ↗</a></small>`:'';
   const hoursBlock=p.opening_hours?`<small class="activity-hours">${getLang()==='en'?'Opening hours':'Giờ mở cửa'}: ${escapeHtml(p.opening_hours)}</small>`:p.opening_hours_unverified?`<small class="activity-hours-unverified">${getLang()==='en'?'Opening hours are not verified; check before visiting.':'Giờ mở cửa chưa được xác minh; hãy kiểm tra trước khi đến.'}</small>`:'';
   const onsiteBreaks=(p.onsite_breaks||[]).map(item=>`<p class="activity-onsite-break">${escapeHtml(item.start_time||'')}–${escapeHtml(item.end_time||'')} · ${escapeHtml(item.type||'')}</p>`).join('');
   const travel=s.travel_from_previous;
   const road=travel?.distance_method==='osrm_road_route';
   const distanceLabel=road
     ? (getLang()==='en'?'Estimated road route and driving time from OSRM; no live traffic.':'Quãng đường và thời gian lái xe ước tính theo tuyến OSRM; không có giao thông trực tiếp.')
     : (getLang()==='en'?'Straight-line distance, not road distance; travel time is only a rough estimate.':'Đây là đường chim bay, không phải quãng đường đi xe; thời gian chỉ ước tính sơ bộ.');
   const distanceText=travel?.distance_km!=null
     ? (road?`↗ ${Number(travel.distance_km).toFixed(1)} km đường bộ · ~${travel.estimated_minutes??'—'} ${getLang()==='en'?'min':'phút'}`:`↗ ${Number(travel.distance_km).toFixed(1)} km đường chim bay · ~${travel.estimated_minutes??'—'} ${getLang()==='en'?'min ước tính':'phút ước tính'}`)
     : '';
   const photoUrl=/^(?:https?:\/\/|\/media\/|data:image\/svg\+xml;base64,)/i.test(String(p.image_url||''))?String(p.image_url):'';
   const imageProvince=p.province||p.province_name||state.province_name||'';
   return `<div class="slot activity-slot"><div class="time">${escapeHtml(s.start_time||'')} – ${escapeHtml(s.end_time||'')}</div><div class="slot-body"><figure class="activity-photo"><div class="photo-placeholder">${placeIcon(p.category||'')}</div><img ${photoUrl?`src="${escapeHtml(photoUrl)}"`:''} data-place-image data-image-url="${escapeHtml(photoUrl)}" data-image-source-url="${escapeHtml(p.image_source_url||'')}" data-image-source-name="${escapeHtml(p.image_source_name||'')}" data-image-license="${escapeHtml(p.image_license||'')}" data-name="${escapeHtml(p.name||'')}" data-province="${escapeHtml(imageProvince)}" data-category="${escapeHtml(p.category||'')}" alt="Ảnh ${escapeHtml(p.name||'địa điểm du lịch')}" loading="lazy"><a class="image-credit" aria-label="Nguồn ảnh" tabindex="-1"></a></figure><div class="slot-info"><div class="slot-title"><span class="badge">${escapeHtml(localSlot(s.type||p.category||'Hoạt động'))}</span><h3>${escapeHtml(p.name||'Không có tên')}</h3></div>${s.category_substitution?`<small class="category-substitution">${getLang()==='en'?'Closest available category':'Đổi sang loại gần nhất còn địa điểm'}: ${escapeHtml(localCategory(p.category||''))}</small>`:''}${addressLine(p)}${coordinateLine(p)}<div class="stats"><span>⭐ ${p.rating!=null?Number(p.rating).toFixed(1):'—'}</span><span>💬 ${p.review_count??0}</span>${distanceText?`<span class="travel-estimate" title="${escapeHtml(distanceLabel)}">${distanceText}</span>`:''}</div>${onsiteBreaks}${hoursBlock}${priceBlock}${sourceBlock}${directoryBlock}</div></div></div>`;
 }

 function drawDayMaps(hotel,days){
   if(typeof L==='undefined')return;
   days.forEach((day,index)=>{
     const el=document.getElementById(`day-map-${index}`);
     if(!el)return;
     el.innerHTML='<div class="map-loading">⌛</div>';
     const points=[];
     if(hotel?.latitude!=null&&hotel?.longitude!=null)points.push({lat:Number(hotel.latitude),lon:Number(hotel.longitude),name:hotel.name,type:'Khách sạn',isHotel:true});
     (day.schedule||[]).forEach(s=>{const p=s.place;if(p?.latitude!=null&&p?.longitude!=null)points.push({lat:Number(p.latitude),lon:Number(p.longitude),name:p.name,type:localSlot(s.type||p.category||'Điểm đến')});else if(s.return_to_hotel&&hotel?.latitude!=null&&hotel?.longitude!=null)points.push({lat:Number(hotel.latitude),lon:Number(hotel.longitude),name:hotel.name,type:localSlot(s.type||'Nơi lưu trú'),isHotel:true});});
     if(!points.length){el.innerHTML=`<div class="map-empty">${getLang()==='en'?'Location data is unavailable for this day.':'Ngày này chưa có tọa độ địa điểm.'}</div>`;return;}
     el.innerHTML='';
     const map=L.map(el,{scrollWheelZoom:false,zoomControl:true,attributionControl:true,preferCanvas:true});
     dayMaps.set(el.id,map);
     map.setView([points[0].lat,points[0].lon],13);
     // Nguon tile chinh + fallback. OSM co the bi chan/rate-limit trong mot so mang.
     // Khong hien overlay "Map tiles unavailable" ngay khi 1 tile loi; chi fallback khi ca lop khong tai duoc.
     const tileSources = [
       {
         url:'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
         options:{maxZoom:19,attribution:'© OpenStreetMap contributors'}
       },
       {
         url:'https://{s}.tile.openstreetmap.de/{z}/{x}/{y}.png',
         options:{maxZoom:19,attribution:'© OpenStreetMap contributors'}
       },
       {
         url:'https://maps.wikimedia.org/osm-intl/{z}/{x}/{y}.png',
         options:{maxZoom:19,attribution:'© OpenStreetMap contributors · Wikimedia maps'}
       }
     ];

     let sourceIndex = 0;
     let activeLayer = null;
     let errorCount = 0;
     let switched = false;

     const addTileSource = (index) => {
       if(index >= tileSources.length) {
         el.classList.add('map-tile-error');
         return;
       }

       sourceIndex = index;
       errorCount = 0;
       switched = false;

       const source = tileSources[index];
       const nextLayer = L.tileLayer(source.url, source.options);

       nextLayer.on('tileload', () => {
         // Chi canh bao loi neu tat ca tile cua source that bai.
         el.classList.remove('map-tile-error');
       });

       nextLayer.on('tileerror', () => {
         errorCount += 1;
         // Neu mot source khong tai duoc, chuyen source khac sau vai tile loi.
         if(errorCount >= 2 && !switched) {
           switched = true;
           if(activeLayer) map.removeLayer(activeLayer);
           addTileSource(index + 1);
         }
       });

       activeLayer = nextLayer;
       nextLayer.addTo(map);
     };

     addTileSource(0);
     const latlngs=[];
     let stopNumber=0;
     points.forEach((p,i)=>{
       const isHotel=Boolean(p.isHotel)||(i===0&&hotel?.latitude!=null&&hotel?.longitude!=null);
       if(!isHotel)stopNumber+=1;
       const marker=L.marker([p.lat,p.lon],{icon:L.divIcon({className:'stop-number-icon',html:`<span>${isHotel?'H':stopNumber}</span>`,iconSize:[30,30],iconAnchor:[15,15]})}).addTo(map);
       marker.bindPopup(`<b>${escapeHtml(p.name)}</b><br>${escapeHtml(p.type)}`);
       latlngs.push([p.lat,p.lon]);
     });
     const routeCoordinates=Array.isArray(day.route?.geometry)?day.route.geometry:[];
     const routeLatLngs=routeCoordinates.map(c=>[Number(c[1]),Number(c[0])]).filter(c=>Number.isFinite(c[0])&&Number.isFinite(c[1]));
     if(routeLatLngs.length>1)L.polyline(routeLatLngs,{weight:4,color:'#1477bd',opacity:.86}).addTo(map);
     else if(latlngs.length>1)L.polyline(latlngs,{weight:3,color:'#1477bd',dashArray:'6 7',opacity:.6}).addTo(map);
     if(latlngs.length>1)map.fitBounds(routeLatLngs.length>1?routeLatLngs:latlngs,{padding:[24,24],maxZoom:14});
     requestAnimationFrame(()=>{try{map.invalidateSize({pan:false});}catch(_){} });
     const link=document.querySelector(`[data-map-link="day-map-${index}"]`);
     if(link){link.href=points.length>1?`https://www.google.com/maps/dir/${points.map(p=>`${p.lat},${p.lon}`).join('/')}`:`https://www.openstreetmap.org/?mlat=${points[0].lat}&mlon=${points[0].lon}#map=14/${points[0].lat}/${points[0].lon}`;}
   });
 }

 window.renderTripPlan=render;
 document.addEventListener('languageChanged',()=>{if(activePlan)render(activePlan);});
})();
