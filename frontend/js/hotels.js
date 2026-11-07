(function(){
 const state=getTripState(); const summary=document.getElementById('tripSummary'); const box=document.getElementById('hotels'); const preference=document.getElementById('accommodationPreference');
 const batchSize=12; let rows=[]; let shown=0;
 let noHotels=false; let lastError=''; let noHotelMessage='';
 if(!state.province_code){location.href='planner.html';return;}
 const rejectedHotel=sessionStorage.getItem('hotelAddressRequired');
 if(rejectedHotel){
   sessionStorage.removeItem('hotelAddressRequired');
   const message=getLang()==='en'
     ? `${rejectedHotel} has no verified street address, so it cannot be used in a travel itinerary. Choose a stay with a street address to continue.`
     : `${rejectedHotel} chưa có địa chỉ đường xác thực nên không thể dùng trong lịch trình. Hãy chọn nơi lưu trú có tên đường để tiếp tục.`;
   showAlert('alertBox',message);
 }
 function updateSummary(){summary.textContent=`${state.province_name||state.province_code} · ${state.days} ${getLang()==='en'?'days':'ngày'} · ${state.people} ${getLang()==='en'?'travelers':'người'}`;}
 updateSummary();
 async function load(){
   document.getElementById('loading').classList.remove('hidden'); box.innerHTML=''; noHotels=false; lastError=''; noHotelMessage='';
   try{
     const res=await apiFetch('/api/recommend/hotels',{method:'POST',body:JSON.stringify({province_code:state.province_code,days:Number(state.days||1),people:Number(state.people||1),budget:state.budget==null?null:Number(state.budget),accommodation_preference:preference.value,limit:100})});
     if(res?.success===false)throw new Error(res.message||'Hotel API error');
     rows=res?.recommendations||res?.data||[]; shown=0;
     if(!rows.length){noHotels=true;noHotelMessage=String(res?.message||'');renderEmpty();return;}
     box.innerHTML=''; appendBatch();
   }catch(e){lastError=e.message;showAlert('alertBox',(getLang()==='en'?'Could not load stays: ':'Không lấy được khách sạn: ')+lastError);}
   finally{document.getElementById('loading').classList.add('hidden');}
 }
 function renderEmpty(){const detail=noHotelMessage?`<p>${escapeHtml(noHotelMessage)}</p>`:`<p>${getLang()==='en'?'No stay with a usable street address and coordinates was found in this area. Try another area or destination.':'Chưa tìm thấy nơi lưu trú có địa chỉ và tọa độ dùng được trong khu vực này. Hãy thử khu vực hoặc tỉnh/thành khác.'}</p>`;box.innerHTML=`<div class="empty"><h2>${tr('noHotels')}</h2>${detail}</div>`;}
 function appendBatch(count=batchSize){
   box.querySelector('#loadMoreHotels')?.remove();
   const next=rows.slice(shown,shown+count);
   box.insertAdjacentHTML('beforeend',next.map((hotel,index)=>hotelCard(hotel,shown+index)).join(''));
   if(typeof loadPlaceImages==='function')loadPlaceImages(box);
   shown+=next.length;
   box.querySelectorAll('[data-hotel]:not([data-bound])').forEach(btn=>{btn.dataset.bound='1';btn.addEventListener('click',()=>{saveTripState({hotel_id:Number(btn.dataset.hotel),hotel_name:btn.dataset.name||'',accommodation_preference:preference.value});location.href='itinerary.html';});});
   if(shown<rows.length){
     const more=document.createElement('button'); more.id='loadMoreHotels'; more.className='btn secondary'; more.type='button';
     more.textContent=`${getLang()==='en'?'Show more stays':'Xem thêm nơi lưu trú'} (${shown}/${rows.length})`;
     more.addEventListener('click',()=>appendBatch()); box.append(more);
   }
 }
 document.addEventListener('languageChanged',()=>{
   updateSummary();
   if(noHotels)renderEmpty();
   else if(rows.length){const visible=shown;box.innerHTML='';shown=0;appendBatch(visible);}
   if(lastError)showAlert('alertBox',(getLang()==='en'?'Could not load stays: ':'Không lấy được khách sạn: ')+lastError);
 });
 function hotelCard(h,i){
   const hasVerifiedPrice=(h.price_min!=null||h.price_max!=null)&&Boolean(h.price_source_url&&h.price_checked_at);
   const price=hasVerifiedPrice?(h.published_price??h.estimated_price_per_night??(h.price_min??h.price_max)):(h.estimated_price_per_night??h.display_price_min);
   const nightly=hasVerifiedPrice&&h.price_unit_verified===true;
   const km=Number(h.location_distance_km);
   const distance=h.location_distance_km!=null?(km>0&&km<0.1?'< 0.1 km':`${km.toFixed(1)} km`):'';
   const rangeMin=hasVerifiedPrice?h.price_min:h.display_price_min;
   const rangeMax=hasVerifiedPrice?h.price_max:h.display_price_max;
   const range=rangeMin!=null||rangeMax!=null?`${rangeMin!=null?money(rangeMin):'—'} – ${rangeMax!=null?money(rangeMax):'—'}${!hasVerifiedPrice||h.display_price_unit==='per_night'?(getLang()==='en'?'/ night':'/ đêm'):''}`:null;
   const checked=h.price_checked_at?new Date(h.price_checked_at).toLocaleString(getLang()==='en'?'en-US':'vi-VN',{dateStyle:'short',timeStyle:'short'}):'';
   const priceLabel=price==null?'':!hasVerifiedPrice?`${money(price)}${getLang()==='en'?'/ night':'/ đêm'}`:`${money(price)}${nightly?(getLang()==='en'?'/ night':'/ đêm'):(getLang()==='en'?' · published offer (not date-confirmed)':' · giá niêm yết tham khảo, chưa xác nhận theo ngày ở')}`;
   const base=String(h.address||'').trim();const address=[base,...[h.ward,h.district,h.province].map(x=>String(x||'').trim()).filter(x=>x&&!base.toLocaleLowerCase().includes(x.toLocaleLowerCase()))].filter(Boolean).filter((x,i,a)=>a.findIndex(y=>y.toLocaleLowerCase()===x.toLocaleLowerCase())===i).join(', ');
   const directoryUrl=String(h.directory_source_url||'');
   const officialLink=/^https:\/\//i.test(directoryUrl)?`<small><a href="${escapeHtml(directoryUrl)}" target="_blank" rel="noopener">${getLang()==='en'?'Official tourism listing':'Thông tin từ Cục Du lịch'} ↗</a></small>`:'';
   const directoryWebsite=String(h.directory_website||'');
   const websiteLink=/^https?:\/\//i.test(directoryWebsite)?`<small><a href="${escapeHtml(directoryWebsite)}" target="_blank" rel="noopener">${getLang()==='en'?'Property website':'Website cơ sở'} ↗</a></small>`:'';
   const phone=String(h.phone||'').replace(/[^+\d]/g,'');
   return `<article class="hotel-card"><figure class="hotel-photo"><div class="photo-placeholder">🏨</div><img ${/^(?:https?:\/\/|\/media\/|data:image\/svg\+xml;base64,)/i.test(String(h.image_url||''))?`src="${escapeHtml(h.image_url)}"`:''} data-place-image data-image-url="${escapeHtml(h.image_url||'')}" data-image-source-url="${escapeHtml(h.image_source_url||'')}" data-image-source-name="${escapeHtml(h.image_source_name||'')}" data-image-license="${escapeHtml(h.image_license||'')}" data-name="${escapeHtml(h.name||'')}" data-province="${escapeHtml(h.province||state.province_name||'')}" data-category="Lưu trú" alt="Ảnh ${escapeHtml(h.name||'khách sạn')}" loading="lazy"><a class="image-credit" aria-label="Nguồn ảnh" tabindex="-1"></a></figure><div class="rank">${String(i+1).padStart(2,'0')}</div><div class="hotel-top"><span class="hotel-type">${escapeHtml(h.subcategory||h.category||'Accommodation')}</span>${distance?`<span class="distance">${distance}</span>`:''}</div><h3>${escapeHtml(h.name||'Unnamed stay')}</h3>${address?`<p>📍 ${escapeHtml(address)}</p>`:''}${phone?`<p>☎️ <a href="tel:${escapeHtml(phone)}">${escapeHtml(h.phone)}</a></p>`:''}<div class="stats"><span>⭐ ${h.rating!=null?Number(h.rating).toFixed(1):'—'}</span><span>💬 ${h.review_count??0}</span></div>${h.opening_hours?`<small class="hotel-hours">🕒 ${escapeHtml(h.opening_hours)}</small>`:''}<div class="price">${priceLabel} ${range?`<small>${range}</small>`:''}${hasVerifiedPrice?`<small><a href="${escapeHtml(h.price_source_url)}" target="_blank" rel="noopener">${getLang()==='en'?'Price source':'Nguồn giá'} ↗</a>${checked?` · ${getLang()==='en'?'checked':'lấy lúc'} ${escapeHtml(checked)}`:''}</small>`:''}${officialLink}${websiteLink}</div><button class="btn primary" data-hotel="${h.id}" data-name="${escapeHtml(h.name||'')}">${tr('choose')}</button></article>`;
 }
 document.getElementById('hotelBtn').addEventListener('click',load); preference.addEventListener('change',load); load();
})();
