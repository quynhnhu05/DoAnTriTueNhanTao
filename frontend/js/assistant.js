(function () {
  const form = document.getElementById('assistantForm');
  const input = document.getElementById('assistantInput');
  const messages = document.getElementById('assistantMessages');
  const sendButton = document.getElementById('assistantSend');
  if (!form || !input || !messages) return;

  const SESSION_KEY = 'travel_path_assistant_v3';
  const sessionKey = () => userStorageKey(SESSION_KEY);
  const knownCategories = ['Ăn uống', 'Cafe', 'Tham quan', 'Thiên nhiên', 'Mua sắm', 'Vui chơi'];
  let provinces = [];
  let busy = false;
  let state = {
    province_code: null, province_name: null, days: null, people: null,
    budget: null, preferences: [], start_date: null, radius_km: 50,
    hotel_id: null, hotel_name: '', plan: null, itinerary: [],
    chat_avoided_ids: [],
    hotel_history_ids: [],
  };
  let conversation = [];
  let wantsSuggestion = false;
  let currentUser = null;

  const fold = (value) => String(value || '').normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '').replace(/đ/gi, 'd')
    .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

  function saveSession() {
    try {
      sessionStorage.setItem(sessionKey(), JSON.stringify({ state, conversation, wantsSuggestion }));
      saveTripState(state);
    } catch (_) {}
  }

  function restoreSession() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(sessionKey()) || 'null');
      if (!saved || !Array.isArray(saved.conversation)) return false;
      state = { ...state, ...(saved.state || {}) };
      if (state.plan_source !== 'staging_chat') state.hotel_id = null;
      const restored = saved.conversation.slice(-50);
      const isWelcome = (item) => item?.role === 'bot'
        && fold(item.text).startsWith('chao ban minh la travel path');
      const lastWelcomeIndex = restored.findLastIndex(isWelcome);
      conversation = restored.filter((item, index) => !isWelcome(item) || index === lastWelcomeIndex);
      wantsSuggestion = Boolean(saved.wantsSuggestion);
      conversation.forEach((item) => item.kind === 'plan'
        ? appendPlan(item.data, false)
        : appendMessage(item.role, item.text, false));
      if (conversation.length !== restored.length) saveSession();
      return true;
    } catch (_) { return false; }
  }

  function restorePlanRotationHistory() {
    try {
      const saved = getTripState();
      const savedHotelId = Number(saved?.plan?.hotel?.id ?? saved?.hotel_id);
      state.hotel_history_ids = [...new Set([
        ...(state.hotel_history_ids || []).map(Number),
        ...(saved?.hotel_history_ids || []).map(Number),
        ...(Number.isSafeInteger(savedHotelId) ? [savedHotelId] : []),
      ])];
      state.chat_avoided_ids = [...new Set([
        ...(state.chat_avoided_ids || []).map(Number),
        ...(saved?.chat_avoided_ids || []).map(Number),
      ])];
      state.plan_generation = Math.max(
        Number(state.plan_generation) || 0,
        Number(saved?.plan_generation) || 0,
      );
    } catch (_) {}
  }

  async function refreshSavedPlan() {
    if (state.plan_source !== 'staging_chat' || !state.province_code || !state.plan?.itinerary?.length
      || Number(state.plan.plan_version || 0) >= 11) return;
    try {
      const response = await apiFetch('/api/chat-plan', {
        method: 'POST',
        body: JSON.stringify({
          province_code: state.province_code,
          days: state.days || state.plan.days || state.plan.itinerary.length,
          people: state.people || state.plan.people || 1,
          budget: state.budget ?? state.plan.budget ?? null,
          preferences: state.preferences || state.plan.preferences || [],
          avoid_hotel_ids: state.hotel_history_ids || [],
          variation_seed: state.plan_generation || 0,
        }),
      });
      if (response?.success === false) return;
      const plan = response?.data || response;
      if (!plan?.itinerary?.length) return;
      plan.province_name = state.province_name || planDestination(plan);
      const lastSavedPlan = state.plan;
      if (lastSavedPlan?.history_id) {
        plan.history_id = lastSavedPlan.history_id;
        plan.history_owner_id = lastSavedPlan.history_owner_id;
      }
      state.plan = plan;
      state.itinerary = plan.itinerary;
      state.hotel_name = plan.hotel?.name || state.hotel_name;
      let lastPlan = -1;
      conversation.forEach((item, index) => { if (item.kind === 'plan') lastPlan = index; });
      if (lastPlan >= 0) {
        conversation[lastPlan].data = plan;
        conversation[lastPlan].history_id = plan.history_id || conversation[lastPlan].history_id;
      }
      else conversation.push({ kind: 'plan', data: plan });
      await persistPlanHistory(plan, false);
      messages.replaceChildren();
      conversation.forEach((item) => item.kind === 'plan'
        ? appendPlan(item.data, false)
        : appendMessage(item.role, item.text, false));
      saveSession();
    } catch (_) {}
  }

  async function persistPlanHistory(plan, createNew = false) {
    if (!currentUser || !plan?.itinerary?.length) return null;
    const ownerId = Number(plan.history_owner_id || state.saved_plan_owner_id || 0);
    const existingId = Number(plan.history_id || state.saved_plan_id || 0);
    const planId = !createNew && ownerId === Number(currentUser.id) && existingId > 0 ? existingId : null;
    const savedPlan = JSON.parse(JSON.stringify(plan));
    delete savedPlan.history_id;
    delete savedPlan.history_owner_id;
    try {
      const saveBody = id => JSON.stringify({plan: savedPlan, plan_id: id || null});
      let saved;
      try {
        saved = await apiFetch('/api/my/plans', {method:'POST', body:saveBody(planId)});
      } catch (error) {
        if (!planId || !/404|không thuộc|không tìm thấy/i.test(String(error.message || ''))) throw error;
        saved = await apiFetch('/api/my/plans', {method:'POST', body:saveBody(null)});
      }
      plan.history_id = saved.id;
      plan.history_owner_id = Number(currentUser.id);
      state.saved_plan_id = saved.id;
      state.saved_plan_owner_id = Number(currentUser.id);
      const lastPlan = [...conversation].reverse().find(item => item.kind === 'plan' && item.data === plan);
      if (lastPlan) lastPlan.history_id = saved.id;
      saveSession();
      return saved.id;
    } catch (error) {
      if (/đăng nhập|401/i.test(String(error.message || ''))) currentUser = null;
      appendMessage('bot', `Mình chưa lưu được plan vào lịch sử tài khoản: ${error.message}`);
      return null;
    }
  }

  function appendMessage(role, text, persist = true) {
    const row = document.createElement('div');
    row.className = `assistant-message ${role}`;
    const bubble = document.createElement('div');
    bubble.className = 'assistant-bubble';
    if (role === 'bot' && String(text || '').startsWith('Chào bạn! Mình là Travel Path.')) {
      row.classList.add('assistant-welcome');
      bubble.innerHTML = '<span class="welcome-kicker">✦ TRAVEL PATH · TRỢ LÝ DU LỊCH</span><strong>Chào bạn, mình là Travel Path.</strong><span class="welcome-copy">Mình sẽ tìm địa điểm phù hợp với bạn. Bạn thích thiên nhiên, tham quan, ẩm thực, cà phê, mua sắm hay vui chơi?</span>';
    } else {
      bubble.textContent = String(text || '');
    }
    row.appendChild(bubble);
    const stamp = document.createElement('time');
    stamp.dateTime = new Date().toISOString();
    stamp.textContent = new Intl.DateTimeFormat('vi-VN', { hour: '2-digit', minute: '2-digit' }).format(new Date());
    row.appendChild(stamp);
    messages.appendChild(row);
    messages.scrollTop = messages.scrollHeight;
    if (persist) {
      conversation.push({ role, text: String(text || '') });
      saveSession();
    }
    return row;
  }

  function appendPlan(plan, persist = true) {
    const row = document.createElement('div');
    row.className = 'assistant-message bot';
    const card = document.createElement('article');
    card.className = 'assistant-plan';
    const head = document.createElement('header');
    head.className = 'assistant-plan-head';
    const eyebrow = document.createElement('small');
    eyebrow.textContent = 'LỊCH TRÌNH TỪ DỮ LIỆU ĐỊA ĐIỂM';
    const title = document.createElement('h2');
    const destination = planDestination(plan) || state.province_name || 'Chuyến đi của bạn';
    const planDays = Number(plan.days) || (plan.itinerary || []).length;
    title.textContent = `${destination} · ${planDays} ngày`;
    const summary = document.createElement('p');
    const planPeople = Number(plan.people) || Number(state.people) || 1;
    const planBudget = plan.budget ?? state.budget;
    const planPreferences = plan.preferences || state.preferences || [];
    summary.textContent = `${planPeople} người${planBudget ? ` · Ngân sách ${money(planBudget)}` : ''} · Sở thích: ${planPreferences.join(', ') || 'đa dạng'}`;
    head.append(eyebrow, title, summary);
    card.appendChild(head);

    const cost = plan.cost_summary || {};
    const costBox = document.createElement('section');
    costBox.className = 'assistant-cost-summary';
    const costTitle = document.createElement('h3');
    costTitle.textContent = 'Chi phí dự kiến và ngân sách';
    costBox.appendChild(costTitle);
    const costGrid = document.createElement('div');
    costGrid.className = 'assistant-cost-grid';
    const hasBudget = Number.isFinite(Number(cost.budget ?? planBudget));
    const knownTripCost = Number(cost.total_estimated_trip_cost ?? cost.known_cost);
    const hasKnownTripCost = Number.isFinite(knownTripCost);
    const remainingBudget = cost.remaining_budget != null
      ? Number(cost.remaining_budget)
      : (hasBudget && hasKnownTripCost ? Number(cost.budget ?? planBudget) - knownTripCost : null);
    const budgetOverrun = remainingBudget != null && remainingBudget < 0;
    const remainingLabel = budgetOverrun
      ? 'Vượt ngân sách ít nhất'
      : (cost.partial || Number(cost.unpriced_places) > 0 ? 'Còn lại tạm tính' : 'Còn lại');
    const costItems = [
      ['Lưu trú', cost.lodging_cost],
      [cost.partial ? 'Tạm tính cả chuyến' : 'Tổng cả chuyến', cost.total_estimated_trip_cost],
      ['Ước tính mỗi người', cost.per_person_estimate],
      ['Ngân sách chuyến đi', cost.budget],
      [remainingLabel, remainingBudget == null ? null : Math.abs(remainingBudget)],
    ];
    costItems.forEach(([label, amount]) => {
      const item = document.createElement('div');
      item.className = 'assistant-cost-item';
      const caption = document.createElement('small');
      caption.textContent = label;
      const value = document.createElement('b');
      value.textContent = amount == null ? 'Chưa đủ dữ liệu' : money(amount);
      item.append(caption, value);
      costGrid.appendChild(item);
    });
    costBox.appendChild(costGrid);
    card.appendChild(costBox);

    const daysWrap = document.createElement('div');
    daysWrap.className = 'assistant-plan-days';
    (plan.itinerary || []).forEach((day) => {
      const dayCard = document.createElement('section');
      dayCard.className = 'assistant-plan-day-card';
      const dayHead = document.createElement('header');
      dayHead.className = 'assistant-plan-day-head';
      const dayNumber = document.createElement('span');
      dayNumber.className = 'assistant-plan-day-number';
      dayNumber.textContent = String(day.day || '');
      const dayTitle = document.createElement('div');
      const dayName = document.createElement('h3');
      dayName.textContent = `Ngày ${day.day}${day.title ? ` · ${day.title.replace(/^Gợi ý ngày\s*\d+\s*/i, '')}` : ''}`;
      const dayMeta = document.createElement('small');
      dayMeta.textContent = `${(day.schedule || []).length} hoạt động${day.estimated_day_cost ? ` · khoảng ${money(day.estimated_day_cost)}` : ''}`;
      dayTitle.append(dayName, dayMeta);
      dayHead.append(dayNumber, dayTitle);
      dayCard.appendChild(dayHead);
      const timeline = document.createElement('div');
      timeline.className = 'assistant-plan-timeline';
      (day.schedule || []).forEach((slot) => {
        const stop = document.createElement('article');
        stop.className = `assistant-plan-stop${slot.place ? ' has-place' : ' no-place'}`;
        const time = document.createElement('time');
        time.textContent = [slot.start_time, slot.end_time].filter(Boolean).join(' – ') || 'Linh hoạt';
        const marker = document.createElement('span');
        marker.className = 'assistant-plan-stop-marker';
        marker.setAttribute('aria-hidden', 'true');
        const body = document.createElement('div');
        body.className = 'assistant-plan-stop-body';
        const activity = document.createElement('h4');
        activity.textContent = slot.activity || slot.type || 'Hoạt động';
        body.appendChild(activity);
        if (slot.place) {
          const place = slot.place;
          const placeCard = document.createElement('div');
          placeCard.className = 'assistant-plan-place';
          if (place.image_url) {
            const img = document.createElement('img');
            img.src = place.image_url;
            img.alt = `Ảnh ${place.name || 'địa điểm'}`;
            img.loading = 'lazy';
            img.referrerPolicy = 'no-referrer';
            img.addEventListener('error', () => img.remove(), { once: true });
            placeCard.appendChild(img);
          }
          const details = document.createElement('div');
          details.className = 'assistant-plan-place-details';
          const name = document.createElement('b');
          name.textContent = place.name || 'Địa điểm trong dữ liệu';
          details.appendChild(name);
          const isStay = fold(place.category) === fold('Lưu trú');
          if (!isStay && place.price_text) {
            const sourcePrice = document.createElement('p');
            sourcePrice.className = 'assistant-plan-source-price';
            sourcePrice.textContent = `Giá theo nguồn: ${place.price_text}`;
            details.appendChild(sourcePrice);
          }
          if (place.address) {
            const address = document.createElement('p');
            address.className = 'assistant-plan-address';
            address.textContent = `📍 ${place.address}`;
            details.appendChild(address);
          }
          if (!isStay && slot.estimated_per_person != null) {
            const price = document.createElement('p');
            price.className = 'assistant-plan-price';
            price.textContent = `Chi phí kế hoạch: khoảng ${money(slot.estimated_per_person)}/người`;
            details.appendChild(price);
          } else if (!isStay && !place.price_text) {
            const price = document.createElement('p');
            price.className = 'assistant-plan-price';
            price.textContent = 'Chưa có giá trong dữ liệu';
            details.appendChild(price);
          }
          placeCard.appendChild(details);
          body.appendChild(placeCard);
        }
        stop.append(time, marker, body);
        timeline.appendChild(stop);
      });
      dayCard.appendChild(timeline);
      daysWrap.appendChild(dayCard);
    });
    card.appendChild(daysWrap);
    row.appendChild(card);
    messages.appendChild(row);
    messages.scrollTop = messages.scrollHeight;
    if (persist) {
      conversation.push({ kind: 'plan', data: plan });
      saveSession();
    }
  }

  function showTyping() {
    const row = document.createElement('div');
    row.className = 'assistant-message bot typing';
    row.setAttribute('role', 'status');
    const bubble = document.createElement('div');
    bubble.className = 'assistant-bubble';
    bubble.innerHTML = '<i class="typing-dot"></i><i class="typing-dot"></i><i class="typing-dot"></i>';
    row.appendChild(bubble);
    messages.appendChild(row);
    messages.scrollTop = messages.scrollHeight;
    return row;
  }

  function renderGalleryRail(rail, places, horizontal = false, initialOffset = 0) {
    if (!rail || !places.length) return;
    const track = document.createElement('div');
    track.className = `gallery-track${horizontal ? ' gallery-track-horizontal' : ''}`;
    const group = document.createElement('div');
    group.className = 'gallery-group';
    const tiles = [];
    places.slice(0, 5).forEach(() => {
      const tile = document.createElement('figure');
      tile.className = 'gallery-tile';
      const image = document.createElement('img');
      image.alt = 'Ảnh địa điểm du lịch';
      image.loading = 'eager';
      image.decoding = 'async';
      tile.append(image);
      group.appendChild(tile);
      tiles.push(image);
    });
    track.append(group);
    rail.replaceChildren(track);

    let offset = initialOffset % places.length;
    const showBatch = (fade = false, slide = false) => {
      tiles.forEach((image, index) => {
        const place = places[(offset + index) % places.length];
        image.alt = place.name ? `Ảnh ${place.name}` : 'Ảnh địa điểm du lịch';
        if (fade || slide) image.classList.add('is-loading');
        const finishLoad = () => {
          image.classList.remove('is-loading');
          const aspect = image.naturalWidth / Math.max(image.naturalHeight, 1);
          image.classList.toggle('image-unavailable', image.naturalWidth < 240 || image.naturalHeight < 140 || aspect < .48 || aspect > 3.5);
          if (slide) {
            image.classList.remove('slide-enter');
            void image.offsetWidth;
            image.classList.add('slide-enter');
          }
        };
        image.onload = finishLoad;
        image.onerror = () => { image.classList.remove('is-loading'); image.classList.add('image-unavailable'); };
        image.src = place.image_url;
        if (image.complete && image.naturalWidth > 0) finishLoad();
      });
      offset = (offset + tiles.length) % places.length;
    };
    showBatch();
    return (fade = true, slide = false) => showBatch(fade, slide);
  }

  async function loadGalleryRails() {
    const response = await apiFetch('/api/chat/places?limit=500&gallery=true');
    const places = Array.isArray(response) ? response : (response?.data || response?.places || []);
    const seen = new Set();
    const decorativeImage = /(zalo|messenger|whatsapp|telegram|wechat|viber|tiktok|youtube|logo|avatar|favicon|placeholder|gravatar|icon[_-]?(chat|social|share)|skytrax|airline|airways|flag|quoc-ky|quốc-kỳ|banner|poster|collage|logo|icon|map|ban-do|bản-đồ|interior|indoor|restaurant|food|menu|portrait|selfie|family|tourist|people|woman|man|girl|boy|model|church|temple|pagoda|museum|monument|building|architecture|cinema|hotel|homestay)/i;
    const foldPhotoName = value => String(value || '').normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '').replace(/đ/gi, 'd').toLowerCase();
    const stopPhotoWords = new Set(['khu', 'du', 'lich', 'tham', 'quan', 'diem', 'dia', 'viet', 'nam', 'cong', 'vien', 'chua', 'den', 'nha', 'tho']);
    const scenicFeature = /\b(bien|bai|vinh|dao|quan dao|ban dao|nui|deo|thac|suoi|ho|rung|hang|doi|thung lung|ghenh|mui|cao nguyen|dam|pha|ruong|canh dong|con cat|song|vuon quoc gia|waterfall|beach|island|bay|mountain|lake|forest|cave|valley|river|coast|waterfront|landscape)\b/i;
    const nonScenicPlace = /\b(chua|den|mieu|nha tho|tuong dai|bao tang|di tich|thanh co|thap|pho co|pho di bo|lang nghe|cho noi|cho dem|nha hat|rap phim|cong vien|khu vui choi|cap treo|du thuyen|san golf|san van dong|nha hang|quan an|quan cafe|homestay|khach san|resort|cong trinh|toa nha|cua khau|nghia trang|tuong niem|ho chi minh|mausoleum|memorial|museum|temple|pagoda|church|monument)\b/i;
    const filenameMatchesPlace = (url, name) => {
      let filename = String(url).split(/[?#]/)[0].split('/').pop() || '';
      try { filename = decodeURIComponent(filename); } catch (_) {}
      const imageWords = foldPhotoName(filename).replace(/[^a-z0-9]+/g, ' ').split(/\s+/).filter(word => word.length > 2);
      const placeWords = foldPhotoName(name).replace(/[^a-z0-9]+/g, ' ').split(/\s+/).filter(word => word.length > 2 && !stopPhotoWords.has(word));
      // Crawled assets commonly include a place or destination slug. Skip a
      // clearly different place's photo, while allowing opaque CDN filenames.
      const namedImageWords = imageWords.filter(word => !/^\d+$/.test(word) && !/^[a-f0-9]{8,}$/.test(word));
      if (namedImageWords.length < 2 || placeWords.length === 0) return true;
      return placeWords.some(word => namedImageWords.includes(word));
    };
    const photos = places.filter((place) => {
      if (!['Tham quan', 'Thiên nhiên'].includes(place.category)) return false;
      const url = String(place.image_url || '').trim();
      const source = String(place.image_source_url || '').trim();
      const normalizedPlaceName = foldPhotoName(place.name).replace(/[^a-z0-9]+/g, ' ').trim();
      if (!scenicFeature.test(normalizedPlaceName) || nonScenicPlace.test(normalizedPlaceName)) return false;
      if (decorativeImage.test(url) || decorativeImage.test(source) || /\/(social|icon|logo|maps?)\//i.test(source)) return false;
      if (!filenameMatchesPlace(url, place.name)) return false;
      if (!url || seen.has(url)) return false;
      seen.add(url);
      return true;
    });
    const left = document.getElementById('leftGallery');
    const right = document.getElementById('rightGallery');
    const top = document.getElementById('topGallery');
    if (photos.length < 2) return;
    const third = Math.ceil(photos.length / 3);
    const leftPlaces = photos.slice(0, third);
    const rightPlaces = photos.slice(third, third * 2);
    const topPlaces = photos.slice(third * 2);
    const rotateLeft = renderGalleryRail(left, leftPlaces.length >= 5 ? leftPlaces : photos);
    const rotateRight = renderGalleryRail(right, rightPlaces.length >= 5 ? rightPlaces : photos, false, 5);
    const rotateTop = renderGalleryRail(top, topPlaces.length >= 5 ? topPlaces : photos, true, 10);
    window.setInterval(() => {
      rotateTop?.(true, true);
    }, 4200);
    window.setInterval(() => {
      rotateLeft?.(true, true);
      rotateRight?.(true, true);
    }, 4200);
  }

  function setBusy(value) {
    busy = value;
    input.disabled = value;
    sendButton.disabled = value;
    sendButton.querySelector('span').textContent = value ? 'Đang lên lịch…' : 'Gửi tin nhắn';
  }

  function extractBudget(text) {
    const source = fold(text);
    const patterns = [
      /(?:ngan sach|budget|chi phi|khoang|tam|toi co|co)\s*(?:la\s*)?(\d[\d.,]*)\s*(trieu|tr|million|m|nghin|ngan|k|vnd|dong|d)?\b/i,
      /(\d[\d.,]*)\s*(trieu|tr|million|nghin|ngan|k|vnd|dong)\b/i,
    ];
    let match = null;
    for (const pattern of patterns) { match = source.match(pattern); if (match) break; }
    if (!match) return null;
    const raw = match[1];
    const unit = match[2] || '';
    let amount;
    if (raw.includes(',') && /^\d+[.,]\d{1,2}$/.test(raw)) amount = Number(raw.replace(',', '.'));
    else amount = Number(raw.replace(/[.,]/g, ''));
    if (!Number.isFinite(amount) || amount <= 0) return null;
    if (['trieu', 'tr', 'million', 'm'].includes(unit)) amount *= 1_000_000;
    else if (['nghin', 'ngan', 'k'].includes(unit)) amount *= 1_000;
    else if (!unit && amount < 100_000) return null;
    return Math.round(amount);
  }

  function extractNumbers(text) {
    const source = fold(text);
    const days = source.match(/\b(\d{1,2})\s*(ngay|dem|day|days)\b/);
    const people = source.match(/\b(\d{1,3})\s*(nguoi|ng|khach|people|persons|person|travelers|travellers)\b/);
    return {
      days: days ? Math.min(30, Math.max(1, Number(days[1]) + (days[2] === 'dem' ? 1 : 0))) : null,
      people: people ? Math.min(100, Math.max(1, Number(people[1]))) : null,
      budget: extractBudget(text),
    };
  }

  function findProvince(text) {
    const source = fold(text);
    const province = [...provinces].sort((a, b) => fold(b.name).length - fold(a.name).length)
      .find((province) => {
        const name = fold(province.name);
        return name && (` ${source} `).includes(` ${name} `);
      }) || null;
    if (province) return province;

    // Users usually name the tourist city/island, while the planner's DB is
    // keyed by the current province-level administrative unit.
    const destinationAliases = [
      ['Đà Lạt', 'Lâm Đồng'],
      ['Sa Pa', 'Lào Cai'], ['Sapa', 'Lào Cai'],
      ['Hội An', 'Đà Nẵng'],
      ['Phú Quốc', 'An Giang'],
      ['Vũng Tàu', 'Thành phố Hồ Chí Minh'],
      ['Quy Nhơn', 'Gia Lai'],
      ['Nha Trang', 'Khánh Hòa'],
      ['Hạ Long', 'Quảng Ninh'],
      ['Mũi Né', 'Lâm Đồng'], ['Phan Thiết', 'Lâm Đồng'],
    ];
    const alias = destinationAliases.find(([name]) => (` ${source} `).includes(` ${fold(name)} `));
    if (!alias) return null;
    const parent = provinces.find((item) => fold(item.name).includes(fold(alias[1])));
    return parent ? { ...parent, name: alias[0] } : null;
  }

  function extractPreferences(text) {
    const source = fold(text);
    const groups = [
      ['Ăn uống', ['an uong', 'am thuc', 'mon ngon', 'mon dia phuong', 'hai san', 'nha hang', 'quan an', 'food', 'restaurant']],
      ['Cafe', ['cafe', 'coffee', 'ca phe', 'quan nuoc', 'banh ngot', 'tiem banh']],
      ['Tham quan', ['tham quan', 'kham pha', 'danh thang', 'di tich', 'bao tang', 'lich su', 'van hoa', 'tam linh', 'chup anh', 'check in', 'landmark', 'sightseeing', 'museum', 'history', 'culture']],
      ['Thiên nhiên', ['thien nhien', 'bien', 'nui', 'rung', 'suoi', 'thac', 'trekking', 'sinh thai', 'nature', 'beach', 'mountain', 'waterfall']],
      ['Mua sắm', ['mua sam', 'cho dem', 'cho dia phuong', 'mua qua', 'lang nghe', 'shopping', 'market']],
      ['Vui chơi', ['vui choi', 'giai tri', 'cong vien', 'tro choi', 'show', 'mao hiem', 'duoi nuoc', 'the thao', 'gia dinh', 'tre em', 'entertainment', 'amusement', 'family']],
    ];
    return groups.filter(([, words]) => words.some((word) => source.includes(word))).map(([category]) => category);
  }

  function hasNoDestination(text) {
    const source = fold(text);
    return /\b(chua biet|khong biet|goi y|ban chon giup|bat ky dau|anywhere|not sure|suggest)\b/.test(source);
  }

  function mergeUserDetails(text) {
    const province = findProvince(text);
    if (province) {
      state.province_code = province.code;
      state.province_name = province.name;
      state.province_id = province.id;
      state.hotel_id = null;
      state.hotel_name = '';
    }
    const values = extractNumbers(text);
    if (values.days) state.days = values.days;
    if (values.people) state.people = values.people;
    if (values.budget) state.budget = values.budget;
    const interests = extractPreferences(text);
    if (interests.length) state.preferences = [...new Set([...(state.preferences || []), ...interests])];
    if (hasNoDestination(text)) wantsSuggestion = true;
    return { province, values, interests };
  }

  function missingBasics() {
    return [!state.province_code && !wantsSuggestion ? 'điểm đến' : '', !state.days ? 'số ngày' : '',
      !state.people ? 'số người' : '', !state.budget ? 'ngân sách' : ''].filter(Boolean);
  }

  function planEntries() {
    return conversation.map((item, index) => ({ item, index }))
      .filter(({ item }) => item.kind === 'plan' && item.data?.itinerary?.length);
  }

  function planDestination(plan) {
    return plan?.province_name || provinces.find((province) => String(province.code) === String(plan?.province_code))?.name || '';
  }

  function planSearchText(plan) {
    const names = [planDestination(plan), plan?.hotel?.name, plan?.hotel?.address];
    (plan?.itinerary || []).forEach((day) => (day.schedule || []).forEach((slot) => {
      if (slot.place?.name) names.push(slot.place.name);
      if (slot.place?.address) names.push(slot.place.address);
    }));
    return fold(names.filter(Boolean).join(' '));
  }

  function planTarget(text) {
    const source = normalizePlanTypos(text).replace(/^(?:hay )?(?:xoa|bo|delete|remove|tai|download|xuat|export|save)\s+(?:plan|ke hoach|lich trinh|itinerary|trip)\s*/, '');
    const ignored = new Set(['plan', 'ke', 'hoach', 'lich', 'trinh', 'itinerary', 'trip', 'moi', 'nhat', 'ten', 'dia', 'diem', 'di', 'den', 'giup', 'toi', 'nhe', 'nay', 'pdf', 'thanh', 'file']);
    return source.split(/\s+/).filter((word) => word && !ignored.has(word)).join(' ');
  }

  function findPlanIndex(text) {
    const entries = planEntries();
    if (!entries.length) return -1;
    const destination = findProvince(text);
    const target = planTarget(text);
    const candidates = destination
      ? entries.filter(({ item }) => String(item.data?.province_code) === String(destination.code))
      : target ? entries.filter(({ item }) => planSearchText(item.data).includes(target)) : entries;
    return candidates.length ? candidates[candidates.length - 1].index : -1;
  }

  function isDeletePlanCommand(text) {
    const source = fold(text);
    return /\b(xoa|bo|delete|remove)\b/.test(source) && /\b(plan|ke hoach|lich trinh|itinerary|trip)\b/.test(source);
  }

  function isDownloadPlanCommand(text) {
    const source = normalizePlanTypos(text);
    return /\b(tai|download|xuat|export|save)\b/.test(source) && /\b(plan|ke hoach|lich trinh|itinerary|trip|pdf)\b/.test(source);
  }

  function pdfPlanPayload(plan) {
    const placeFields = ['name', 'address', 'price_text', 'image_url'];
    const pick = (value, fields) => Object.fromEntries(fields.map((field) => [field, value?.[field] ?? null]));
    return {
      province_name: plan?.province_name,
      province: plan?.province,
      province_code: plan?.province_code,
      days: plan?.days,
      people: plan?.people,
      preferences: Array.isArray(plan?.preferences) ? plan.preferences : [],
      hotel: plan?.hotel ? pick(plan.hotel, placeFields) : {},
      cost_summary: pick(plan?.cost_summary, ['lodging_cost', 'total_estimated_trip_cost', 'budget', 'remaining_budget']),
      itinerary: (Array.isArray(plan?.itinerary) ? plan.itinerary : []).map((day) => ({
        day: day?.day,
        title: day?.title,
        schedule: (Array.isArray(day?.schedule) ? day.schedule : []).map((slot) => ({
          start_time: slot?.start_time,
          end_time: slot?.end_time,
          estimated_per_person: slot?.estimated_per_person,
          activity: slot?.activity,
          type: slot?.type,
          place: slot?.place ? pick(slot.place, placeFields) : null,
        })),
      })),
    };
  }

  function normalizePlanTypos(text) {
    return fold(text).replace(/\b(?:flan|plam|plna|plann)\b/g, 'plan');
  }

  async function removePlan(text) {
    const planIndex = findPlanIndex(text);
    if (planIndex < 0) {
      appendMessage('bot', planEntries().length ? 'Mình chưa tìm thấy plan có tên địa điểm bạn vừa nhắc.' : 'Hiện chưa có plan nào để xóa.');
      return;
    }
    const removed = conversation[planIndex];
    if (removed.history_id && currentUser) {
      try { await apiFetch(`/api/my/plans/${removed.history_id}`, {method:'DELETE'}); }
      catch (_) {}
    }
    const planCardIndex = conversation.slice(0, planIndex).filter((item) => item.kind === 'plan').length;
    const cards = [...messages.querySelectorAll('.assistant-message')].filter((row) => row.querySelector('.assistant-plan'));
    cards[planCardIndex]?.remove();
    conversation.splice(planIndex, 1);
    state.plan = null;
    state.itinerary = [];
    state.plan_source = null;
    state.hotel_id = null;
    state.hotel_name = '';
    state.saved_plan_id = null;
    state.saved_plan_owner_id = null;
    saveSession();
    const destination = planDestination(removed.data);
    appendMessage('bot', `Đã xóa plan${destination ? ` ${destination}` : ''}. Các tin nhắn và những plan khác vẫn được giữ lại.`);
  }

  async function downloadPlan(text) {
    const planIndex = findPlanIndex(text);
    if (planIndex < 0) {
      appendMessage('bot', planEntries().length ? 'Mình chưa tìm thấy plan có tên địa điểm bạn vừa nhắc.' : 'Hiện chưa có plan nào để tải PDF.');
      return;
    }
    const plan = conversation[planIndex].data;
    const destination = planDestination(plan) || 'chuyen-di';
    try {
      const response = await fetch('/api/chat-plan/pdf', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan: pdfPlanPayload(plan) }),
      });
      if (!response.ok) {
        let message = 'Không tạo được PDF.';
        try {
          const detail = await response.json();
          message = detail.detail || detail.message || message;
        } catch (_) {
          if (response.status === 413) message = 'Plan quá lớn để tải. Hãy thử lại sau khi tạo lại lịch.';
        }
        throw new Error(message);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `Travel-Path-${fold(destination).replace(/\s+/g, '-')}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      appendMessage('bot', `Đã tải PDF plan ${destination} về máy.`);
    } catch (error) {
      appendMessage('bot', `Mình chưa tải được PDF: ${error.message}`);
    }
  }

  function basicQuestion() {
    const missing = missingBasics();
    if (!missing.length) return '';
    if (missing.length === 4) return 'Bạn muốn đến tỉnh/thành nào, đi mấy ngày, mấy người và dự kiến ngân sách bao nhiêu? Nếu chưa biết điểm đến, cứ nói “chưa biết” rồi kể điều bạn thích, mình sẽ tìm gợi ý từ dữ liệu có sẵn.';
    return `Mình còn cần ${missing.join(', ')}. Bạn nhắn các thông tin đó giúp mình nhé${!state.province_code && !wantsSuggestion ? ' — nếu chưa biết nơi đi, có thể nói “chưa biết”' : ''}.`;
  }

  async function suggestDestination() {
    if (!state.preferences?.length) {
      appendMessage('bot', 'Mình sẽ tìm địa điểm phù hợp với bạn. Bạn thích thiên nhiên, tham quan, ẩm thực, cà phê, mua sắm hay vui chơi?');
      return;
    }
    const raw = await apiFetch('/api/chat/places?limit=500');
    const rows = Array.isArray(raw) ? raw : (raw?.data || []);
    const matching = rows.filter((place) => place.category !== 'Lưu trú' &&
      state.preferences.some((preference) => fold(preference) === fold(place.category)));
    const source = matching.length ? matching : rows.filter((place) => place.category !== 'Lưu trú');
    const grouped = new Map();
    source.forEach((place) => {
      const name = place.province || place.province_name;
      if (name) grouped.set(name, (grouped.get(name) || 0) + 1);
    });
    const names = [...grouped.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([name]) => name);
    if (!names.length) {
      appendMessage('bot', 'Hiện DB chưa có đủ địa điểm sẵn sàng để gợi ý tỉnh theo sở thích. Bạn nhắn tên tỉnh/thành đang cân nhắc nhé; mình sẽ kiểm tra lưu trú và địa điểm trong DB.');
      return;
    }
    appendMessage('bot', `Từ các địa điểm sẵn sàng trong DB và sở thích của bạn, mình gợi ý: ${names.join(', ')}. Bạn muốn khám phá tỉnh/thành nào?`);
  }

  async function createPlan() {
    appendMessage('bot', `Mình đã ghi nhận: ${state.province_name}, ${state.days} ngày, ${state.people} người, ngân sách ${money(state.budget)}; ưu tiên ${state.preferences.join(', ')}. Mình đang tìm nơi lưu trú và xếp lịch từ DB nhé.`);
    const nextPlanGeneration = (Number(state.plan_generation) || 0) + 1;
    const previousPlaceIds = (state.plan?.itinerary || []).flatMap((day) =>
      (day.schedule || []).map((slot) => Number(slot.place?.id)).filter(Number.isFinite));
    const previousHotelId = Number(state.plan?.hotel?.id);
    const hotelHistory = [...new Set([
      ...(state.hotel_history_ids || []).map(Number),
      ...(Number.isFinite(previousHotelId) ? [previousHotelId] : []),
    ])];
    const result = await apiFetch('/api/chat-plan', {
      method: 'POST',
      body: JSON.stringify({
        province_code: state.province_code, days: state.days, people: state.people,
        budget: state.budget, preferences: state.preferences,
        avoid_hotel_ids: hotelHistory,
        avoid_place_ids: [...new Set([...(state.chat_avoided_ids || []).map(Number), ...previousPlaceIds])],
        variation_seed: Date.now() + nextPlanGeneration,
      }),
    });
    if (result?.success === false) {
      appendMessage('bot', result.message || 'DB chưa có đủ dữ liệu sẵn sàng để xếp lịch ở tỉnh này.');
      return;
    }
    const plan = result?.data || result;
    plan.province_name = state.province_name || planDestination(plan);
    const hotel = plan.hotel || {};
    if (hotel.id != null) {
      state.hotel_history_ids = [...new Set([...hotelHistory, Number(hotel.id)])];
    }
    state.chat_avoided_ids = [...new Set([...(state.chat_avoided_ids || []).map(Number), ...previousPlaceIds])];
    state.plan_source = 'staging_chat';
    state.plan_generation = nextPlanGeneration;
    state.hotel_id = null;
    state.hotel_name = hotel.name || '';
    state.plan = plan;
    state.itinerary = plan.itinerary || [];
    const hotelDetails = [hotel.address, hotel.price_text].filter(Boolean).join(' · ');
    appendMessage('bot', `Mình chọn ${state.hotel_name || 'nơi lưu trú'} từ dữ liệu sẵn sàng trong DB${hotelDetails ? `: ${hotelDetails}` : ''}. Lịch gợi ý dùng địa chỉ, không dùng tọa độ. Bạn có thể nhắn để đổi sở thích hoặc chỉnh số ngày.`);
    appendPlan(plan);
    saveSession();
    await persistPlanHistory(plan, true);
  }

  async function continueTrip(message) {
    const result = await apiFetch('/api/chat', {
      method: 'POST',
      body: JSON.stringify({
        message, state, history: conversation.filter((item) => item.role).slice(-12),
      }),
    });
    const data = result?.data || {};
    const startsNewPlan = result?.action === 'create_new_plan';
    const previousPlan = state.plan;
    if (startsNewPlan) {
      state.saved_plan_id = null;
      state.saved_plan_owner_id = null;
    } else if (data.plan && previousPlan?.history_id) {
      data.plan.history_id = previousPlan.history_id;
      data.plan.history_owner_id = previousPlan.history_owner_id;
    }
    if (data.state) state = { ...state, ...data.state };
    if (data.plan) {
      data.plan.province_name = state.province_name || planDestination(data.plan);
      state.plan = data.plan;
    }
    if (Array.isArray(data.itinerary)) state.itinerary = data.itinerary;
    if (!data.plan && Array.isArray(data.itinerary) && state.plan) state.plan.itinerary = data.itinerary;
    const reply = result?.message || 'Mình chưa cập nhật được lịch. Bạn nói rõ ngày và điều muốn đổi giúp mình nhé.';
    appendMessage('bot', reply);
    if (data.plan?.itinerary) appendPlan(data.plan);
    else if (state.plan?.itinerary && /updated|updated itinerary|đã cập nhật|đã tạo lại|đã sắp xếp/i.test(reply)) appendPlan(state.plan);
    const changedPlan = data.plan?.itinerary ? data.plan : (state.plan?.itinerary && /updated|updated itinerary|đã cập nhật|đã tạo lại|đã sắp xếp/i.test(reply) ? state.plan : null);
    if (changedPlan) await persistPlanHistory(changedPlan, startsNewPlan);
    saveSession();
  }

  async function respondToUser(text) {
    if (isDeletePlanCommand(text)) {
      await removePlan(text);
      return;
    }
    if (isDownloadPlanCommand(text)) {
      await downloadPlan(text);
      return;
    }
    if (state.plan_source === 'staging_chat' && state.plan?.itinerary?.length) {
      await continueTrip(text);
      return;
    }
    if (state.hotel_id && state.plan?.itinerary?.length) {
      await continueTrip(text);
      return;
    }
    mergeUserDetails(text);
    const missing = missingBasics();
    if (missing.length) {
      appendMessage('bot', basicQuestion());
      return;
    }
    if (wantsSuggestion && !state.province_code) {
      await suggestDestination();
      return;
    }
    if (!state.preferences?.length) {
      appendMessage('bot', `Bạn thích trải nghiệm gì trong chuyến đi ${state.province_name || ''}? Bạn có thể kể tự nhiên, ví dụ: thích thiên nhiên, cafe và món địa phương.`);
      return;
    }
    await createPlan();
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || busy) return;
    appendMessage('user', text);
    input.value = '';
    input.style.height = 'auto';
    setBusy(true);
    const typing = showTyping();
    try {
      await respondToUser(text);
    } catch (error) {
      const row = appendMessage('bot', `Mình chưa thể hoàn tất yêu cầu: ${error.message}. Bạn có thể nhắn lại sau ít phút.`);
      row.classList.add('assistant-error-row');
    } finally {
      typing.remove();
      setBusy(false);
      input.focus();
    }
  });

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  document.querySelectorAll('[data-travel-prompt]').forEach((button) => {
    button.addEventListener('click', () => {
      if (busy) return;
      input.value = button.dataset.travelPrompt || '';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.focus();
    });
  });

  (async function init() {
    loadGalleryRails().catch(() => {});
    try {
      const auth = await apiFetch('/api/auth/me');
      currentUser = auth?.user || null;
      if (!currentUser) { location.replace('account.html'); return; }
      setTripStateUser(currentUser);
      const hadSession = restoreSession();
      restorePlanRotationHistory();
      const response = await apiFetch('/api/provinces');
      provinces = Array.isArray(response) ? response : (response?.data || response?.provinces || []);
      if (hadSession) await refreshSavedPlan();
      if (!hadSession) {
        appendMessage('bot', 'Chào bạn! Mình là Travel Path. Hãy kể điểm đến, số ngày, số người, ngân sách và sở thích như biển, thiên nhiên, lịch sử, vui chơi, mua sắm, món địa phương hoặc cafe. Sau khi có plan, bạn vẫn có thể nhắn thêm sở thích; mình sẽ xếp lại lịch theo sở thích mới.');
      }
    } catch (_) {
      appendMessage('bot', 'Chào bạn! Mình là Travel Path. Bạn cứ kể chuyến đi mình mong muốn; hiện mình đang kết nối danh sách tỉnh/thành trong DB.');
    }
  })();
})();
