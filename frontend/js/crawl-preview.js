(async function(){
 const provinceKey=new URLSearchParams(location.search).get('province');
 const isLamDong=provinceKey==='lamdong';
 const provinceName=isLamDong?'Lâm Đồng':'Đà Nẵng';
 const dataFile=isLamDong?'lamdong-preview.json':'danang-preview.json';
 const categorySelect=document.getElementById('category');
 const completeOnly=document.getElementById('completeOnly');
 const reloadButton=document.getElementById('reload');
 const container=document.getElementById('places');
 const summary=document.getElementById('summary');
 document.querySelector('.heading h1').textContent=`Dữ liệu crawl ${provinceName}`;
 document.querySelector('.crawl-note').textContent=`Bản demo lấy dữ liệu ${provinceName} mới nhất trong cơ sở dữ liệu. Mặc định chỉ hiển thị địa điểm đã có cả ảnh và giá; bỏ chọn bộ lọc để xem toàn bộ mục đã crawl.`;
 let allRows=[];
 const esc=value=>escapeHtml(value??'');
 const icon=category=>String(category||'').includes('Ăn uống')?'🍜':String(category||'').includes('Cafe')?'☕':String(category||'').includes('Vui chơi')?'🎡':'🏛️';
 function render(){
  const category=categorySelect.value;
  const eligible=completeOnly.checked?allRows.filter(row=>row.image_url&&row.price_text):allRows;
  const rows=category?eligible.filter(row=>row.category===category):eligible;
  summary.textContent=`Đang hiển thị ${rows.length}/${allRows.length} mục · ${rows.filter(row=>row.image_url).length} ảnh · ${rows.filter(row=>row.address).length} địa chỉ · ${rows.filter(row=>row.price_text).length} giá · ${rows.filter(row=>row.opening_hours).length} giờ mở cửa`;
  if(!rows.length){container.innerHTML='<div class="empty crawl-empty"><h2>Chưa có dữ liệu trong danh mục này</h2></div>';return;}
  container.innerHTML=rows.map(row=>{
   const image=String(row.image_url||'');
   const source=(row.source_urls||[])[0]||'';
   const missing=(row.missing_fields||[]).filter(value=>value!=='coordinates'&&!(completeOnly.checked&&['image','price'].includes(value)));
   const imageCredit=row.image_source_url?`<a class="image-credit" href="${esc(row.image_source_url)}" target="_blank" rel="noopener">Ảnh nguồn ↗</a>`:row.image_url?`<span class="image-credit">Ảnh bạn cung cấp</span>`:'';
   return `<article class="place-card crawl-card"><figure class="place-photo"><div class="photo-placeholder">${icon(row.category)}</div>${image?`<img src="${esc(image)}" alt="Ảnh ${esc(row.name)}" loading="lazy" onload="this.classList.add('is-loaded')" onerror="this.remove()">`:''}${imageCredit}</figure><div class="place-card-content"><div class="category">${esc(row.category)}</div><h3>${esc(row.name)}</h3>${row.address?`<p>📍 ${esc(row.address)}</p>`:''}${row.description?`<p class="crawl-description">${esc(row.description.slice(0,500))}${row.description.length>500?'…':''}</p>`:''}${row.price_text?`<p class="crawl-price">💰 ${esc(row.price_text)}</p>`:''}${row.opening_hours?`<small>🕒 ${esc(row.opening_hours)}</small>`:''}${missing.length?`<span class="crawl-missing">Còn thiếu: ${esc(missing.join(', '))}</span>`:''}${source?`<br><a class="crawl-source" href="${esc(source)}" target="_blank" rel="noopener">Mở trang nguồn ↗</a>`:''}</div></article>`;
  }).join('');
 }
 async function load(){
  reloadButton.disabled=true;container.innerHTML=`<div class="loading">Đang tải dữ liệu ${provinceName}…</div>`;
  try{
   const response=await fetch(`data/${dataFile}?refresh=${Date.now()}`);
   if(!response.ok)throw new Error(`HTTP ${response.status}`);
   allRows=await response.json();
   const categories=[...new Set(allRows.map(row=>row.category).filter(Boolean))].sort();
   const selected=categorySelect.value;
   categorySelect.innerHTML='<option value="">Tất cả danh mục</option>'+categories.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('');
   if(categories.includes(selected))categorySelect.value=selected;
   render();
  }catch(error){container.replaceChildren();summary.textContent='';showAlert('alertBox','Không tải được dữ liệu xem trước: '+error.message);}
  finally{reloadButton.disabled=false;}
 }
 reloadButton.addEventListener('click',load);
 categorySelect.addEventListener('change',render);
 completeOnly.addEventListener('change',render);
 await load();
})();
