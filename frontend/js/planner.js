(async function(){
  document.querySelectorAll('.interest-option input').forEach(input=>{
    const sync=()=>input.closest('.interest-option')?.classList.toggle('selected',input.checked);
    input.addEventListener('change',sync); sync();
  });
  const province=document.getElementById('province');
  const startDateInput=document.getElementById('startDate');
  const tripState=getTripState();
  if(tripState.days) document.getElementById('days').value=tripState.days;
  if(tripState.people) document.getElementById('people').value=tripState.people;
  if(tripState.budget!=null) document.getElementById('budget').value=tripState.budget;
  if(tripState.radius_km) document.getElementById('radiusKm').value=tripState.radius_km;
  if(Array.isArray(tripState.preferences)) document.querySelectorAll('.interest-option input').forEach(input=>{input.checked=tripState.preferences.includes(input.value);input.closest('.interest-option')?.classList.toggle('selected',input.checked);});
  if(startDateInput){
    const now=new Date();
    const today=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
    startDateInput.min=today;
    startDateInput.value=tripState.start_date||'';
  }
  function updateTripSummary(){
    const selected=province?.options?.[province.selectedIndex];
    const destination=document.getElementById('summaryDestinationValue');
    const days=document.getElementById('summaryDaysValue');
    const people=document.getElementById('summaryPeopleValue');
    const summaryDate=document.getElementById('summaryStartDateValue');
    const radius=document.getElementById('summaryRadiusValue');
    const budget=document.getElementById('summaryBudgetValue');
    const pace=document.getElementById('summaryPaceValue');
    const interests=document.getElementById('summaryInterestsValue');
    if(destination) destination.textContent=province?.value?(selected?.textContent||''):tr('summaryStart');
    if(days) days.textContent=`${document.getElementById('days')?.value||1} ${getLang()==='en'?'days':'ngày'}`;
    if(people) people.textContent=`${document.getElementById('people')?.value||1} ${getLang()==='en'?'people':'người'}`;
    if(summaryDate){
      const rawDate=startDateInput?.value;
      summaryDate.textContent=rawDate?new Date(`${rawDate}T12:00:00`).toLocaleDateString(getLang()==='en'?'en-US':'vi-VN',{weekday:'short',day:'numeric',month:'short',year:'numeric'}):tr('summaryDateOpen');
    }
    if(radius) radius.textContent=`${document.getElementById('radiusKm')?.value||8} km`;
    if(pace) pace.textContent=tr('paceAuto');
    const budgetValue=document.getElementById('budget')?.value;
    if(budget) budget.textContent=budgetValue?money(budgetValue):tr('summaryBudgetOpen');
    if(interests){
      interests.replaceChildren();
      const selectedInterests=[...document.querySelectorAll('.interest-option input:checked')];
      if(!selectedInterests.length){interests.textContent=tr('summaryNoInterests');return;}
      selectedInterests.forEach(input=>{
        const chip=document.createElement('span');
        chip.className='summary-chip';
        chip.textContent=input.closest('.interest-option')?.querySelector('.interest-copy b')?.textContent||input.value;
        interests.appendChild(chip);
      });
    }
  }
  [province,startDateInput,document.getElementById('days'),document.getElementById('people'),document.getElementById('budget'),document.getElementById('radiusKm'),document.getElementById('limitPerDay')].filter(Boolean).forEach(control=>{
    control.addEventListener('input',updateTripSummary);
    control.addEventListener('change',updateTripSummary);
  });
  document.querySelectorAll('.interest-option input').forEach(input=>input.addEventListener('change',updateTripSummary));
  document.addEventListener('languageChanged',()=>requestAnimationFrame(updateTripSummary));
  updateTripSummary();
  const updateProvincePlaceholder=()=>{
    const placeholder=province?.options?.[0];
    if(placeholder&&placeholder.value==='') placeholder.text=getLang()==='en'?'Select a destination':'Chọn tỉnh/thành phố';
  };
  const updatePlannerAccessibility=()=>{
    province?.setAttribute('aria-label',tr('destination'));
    document.querySelector('.interests-grid')?.setAttribute('aria-label',tr('interestGroupLabel'));
  };
  document.addEventListener('languageChanged',()=>{updateProvincePlaceholder();updatePlannerAccessibility();});
  updateProvincePlaceholder();
  updatePlannerAccessibility();
  try{
    const raw=await apiFetch('/api/provinces');
    const rows=Array.isArray(raw)?raw:(raw?.data||raw?.provinces||[]);
    if(!rows.length) throw new Error('API returned no destinations');
    province.innerHTML=`<option value="">${getLang()==='en'?'Select a destination':'Chọn tỉnh/thành phố'}</option>`+rows.map(p=>`<option value="${escapeHtml(p.code||'')}" data-id="${p.id??''}">${escapeHtml(p.name||'')}</option>`).join('');
    if(tripState.province_code) province.value=String(tripState.province_code);
    updateTripSummary();
  }catch(e){showAlert('alertBox',(getLang()==='en'?'Could not load destinations: ':'Không tải được tỉnh/thành: ')+e.message);}

  document.getElementById('nextBtn').addEventListener('click',()=>{
    const option=province.options[province.selectedIndex];
    const province_code=province.value;
    const days=Number(document.getElementById('days').value);
    const people=Number(document.getElementById('people').value);
    const budgetRaw=document.getElementById('budget').value;
    const radius_km=Number(document.getElementById('radiusKm').value);
    const start_date=startDateInput?.value||null;
    if(!province_code)return showAlert('alertBox',getLang()==='en'?'Please choose a destination.':'Vui lòng chọn điểm đến.');
    if(!Number.isInteger(days)||days<1||days>14)return showAlert('alertBox',getLang()==='en'?'Days must be between 1 and 14.':'Số ngày phải từ 1 đến 14.');
    if(!Number.isInteger(people)||people<1||people>100)return showAlert('alertBox',getLang()==='en'?'Travelers must be between 1 and 100.':'Số người phải từ 1 đến 100.');
    const budget=budgetRaw===''?null:Number(budgetRaw);
    if(budget===null||!Number.isFinite(budget)||budget<=0)return showAlert('alertBox',getLang()==='en'?'Enter a budget greater than zero.':'Vui lòng nhập ngân sách lớn hơn 0.');
    sessionStorage.removeItem(userStorageKey('travel_chat_history_v1'));
    saveTripState({province_code,province_id:Number(option.dataset.id||0)||null,province_name:option.text,days,people,budget,start_date,radius_km,preferences:getPrefs(),hotel_id:null,hotel_name:'',itinerary:null,plan:null,chat_avoided_ids:[]});
    location.href='hotels.html';
  });
})();
