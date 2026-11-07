(async function(){
 const province=document.getElementById('province');
 const category=document.getElementById('category');
 const limit=document.getElementById('limit');
 const hasPublicPrice=document.getElementById('hasPublicPrice');
 const results=document.getElementById('places');
 const loadButton=document.getElementById('loadBtn');
 let provinceRows=[];
 let placeRows=[];
 const categoryKeys={
  'Ăn uống':'food','Cafe':'cafeCategory','Tham quan':'sightseeing',
  'Thiên nhiên':'nature','Giải trí':'entertainment','Vui chơi':'entertainment','Mua sắm':'shopping',
  'Lưu trú':'stayCategory','Di chuyển':'transportCategory'
 };

 function categoryLabel(value){
  const key=categoryKeys[value];
  return key?tr(key):value;
 }

 function placeIcon(value){
  const category=String(value||'').toLocaleLowerCase();
  if(category.includes('ăn'))return '🍜';
  if(category.includes('cafe')||category.includes('cà phê'))return '☕';
  if(category.includes('thiên nhiên'))return '🌿';
  if(category.includes('vui chơi')||category.includes('giải trí'))return '🎡';
  if(category.includes('mua sắm'))return '🛍️';
  if(category.includes('lưu trú')||category.includes('khách sạn'))return '🏨';
  return '🏛️';
 }

 function sourceHref(value){
  try{
   const url=new URL(value);
   return ['http:','https:'].includes(url.protocol)?escapeHtml(url.href):'';
  }catch(_){return '';}
 }

 function coordinateLink(place){
  const lat=Number(place?.latitude),lon=Number(place?.longitude);
  if(!Number.isFinite(lat)||!Number.isFinite(lon))return '';
  const url=`https://www.google.com/maps/search/?api=1&query=${lat},${lon}`;
  return `<small><a href="${url}" target="_blank" rel="noopener">📍 ${getLang()==='en'?'Open map pin':'Mở vị trí trên bản đồ'} ↗</a></small>`;
 }

 function renderPlaces(){
  if(!results)return;
  if(!placeRows.length){
   results.innerHTML=`<div class="empty"><h2>${escapeHtml(tr('noData'))}</h2></div>`;
   return;
  }
  results.innerHTML=placeRows.map(place=>{
   const source=sourceHref(place.price_source_url);
   const verified=(place.price_min!=null||place.price_max!=null)&&Boolean(source&&place.price_checked_at);
   const priceMin=place.display_price_min??place.price_min;
   const priceMax=place.display_price_max??place.price_max;
   const price=priceMin!=null||priceMax!=null?`<span>💰 ${priceMin!=null?money(priceMin):'—'} – ${priceMax!=null?money(priceMax):'—'}</span>`:'';
   const checkedAt=verified?new Date(place.price_checked_at):null;
   const checkedLabel=checkedAt&&!Number.isNaN(checkedAt.valueOf())?`${tr('priceChecked')} ${checkedAt.toLocaleDateString(getLang()==='en'?'en-GB':'vi-VN')}`:'';
   const priceStatus=verified
    ? `<small><a href="${source}" target="_blank" rel="noopener">${escapeHtml(tr('priceSource'))}</a>${checkedLabel?` · ${escapeHtml(checkedLabel)}`:''}</small>`
    : '';
   const directorySource=sourceHref(place.directory_source_url);
   const directoryLink=directorySource?`<small><a href="${directorySource}" target="_blank" rel="noopener">${getLang()==='en'?'Official tourism listing':'Nguồn: Cục Du lịch'} ↗</a></small>`:'';
   const directoryWebsite=sourceHref(place.directory_website);
   const websiteLink=directoryWebsite?`<small><a href="${directoryWebsite}" target="_blank" rel="noopener">${getLang()==='en'?'Venue website':'Website cơ sở'} ↗</a></small>`:'';
   const phone=String(place.phone||'').replace(/[^+\d]/g,'');
   const address=[place.address,place.ward,place.district,place.province].map(x=>String(x||'').trim()).filter(Boolean).filter((x,i,a)=>a.findIndex(y=>y.toLocaleLowerCase()===x.toLocaleLowerCase())===i).join(', ');
   const image=String(place.image_url||'');
   const imageProvince=place.province||provinceRows.find(row=>String(row.code)===String(place.province_code))?.name||'';
   return `<article class="place-card"><figure class="place-photo"><div class="photo-placeholder">${escapeHtml(placeIcon(place.category||''))}</div><img ${/^(?:https?:\/\/|\/media\/|data:image\/svg\+xml;base64,)/i.test(image)?`src="${escapeHtml(image)}"`:''} data-place-image data-image-url="${escapeHtml(image)}" data-image-source-url="${escapeHtml(place.image_source_url||'')}" data-image-source-name="${escapeHtml(place.image_source_name||'')}" data-image-license="${escapeHtml(place.image_license||'')}" data-name="${escapeHtml(place.name||'')}" data-province="${escapeHtml(imageProvince)}" data-category="${escapeHtml(place.category||'')}" alt="Ảnh ${escapeHtml(place.name||'địa điểm du lịch')}" loading="lazy"><a class="image-credit" aria-label="Nguồn ảnh" tabindex="-1"></a></figure><div class="place-card-content"><div class="category">${escapeHtml(categoryLabel(place.category||''))}</div><h3>${escapeHtml(place.name)}</h3>${address?`<p>📍 ${escapeHtml(address)}</p>`:''}${coordinateLink(place)}${phone?`<small>☎️ <a href="tel:${escapeHtml(phone)}">${escapeHtml(place.phone)}</a></small>`:''}<div class="stats"><span>⭐ ${place.rating!=null?Number(place.rating).toFixed(1):'—'}</span><span>💬 ${place.review_count??0}</span>${price}</div>${priceStatus}${place.opening_hours?`<small>🕒 ${escapeHtml(place.opening_hours)}</small>`:''}${directoryLink}${websiteLink}</div></article>`;
  }).join('');
  if(typeof loadPlaceImages==='function')loadPlaceImages(results);
 }

 function updateProvinceOptions(){
  if(!province)return;
  const selected=province.value;
  province.innerHTML=`<option value="">${escapeHtml(tr('allProvinces'))}</option>`+
   provinceRows.map(item=>`<option value="${escapeHtml(item.code||'')}">${escapeHtml(item.name||'')}</option>`).join('');
  if(provinceRows.some(item=>String(item.code)===selected))province.value=selected;
 }

 async function load(){
  if(loadButton)loadButton.disabled=true;
  if(results){results.innerHTML=`<div class="loading">${escapeHtml(tr('loadingPlaces'))}</div>`;}
  try{
   const query=new URLSearchParams();
   if(province.value)query.set('province_code',province.value);
   if(category.value)query.set('category',category.value);
   if(hasPublicPrice?.checked)query.set('has_public_price','true');
   const count=Math.max(1,Math.min(100,Number(limit.value)||30));
   limit.value=String(count);
   query.set('limit',String(count));
   const raw=await apiFetch('/api/places?'+query.toString());
   placeRows=Array.isArray(raw)?raw:(raw?.data||raw?.places||[]);
   renderPlaces();
  }catch(error){
   placeRows=[];
   if(results)results.replaceChildren();
   showAlert('alertBox',tr('placeLoadError')+error.message);
  }finally{
   if(loadButton)loadButton.disabled=false;
  }
 }

 try{
  const raw=await apiFetch('/api/provinces');
  provinceRows=Array.isArray(raw)?raw:(raw?.data||raw?.provinces||[]);
  updateProvinceOptions();
 }catch(error){
  showAlert('alertBox',tr('provinceLoadError')+error.message);
 }

 document.addEventListener('languageChanged',()=>{
  updateProvinceOptions();
  renderPlaces();
 });
 loadButton?.addEventListener('click',load);
 province?.addEventListener('change',load);
 category?.addEventListener('change',load);
 hasPublicPrice?.addEventListener('change',load);
 load();
})();
