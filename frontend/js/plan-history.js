(() => {
  const list = document.getElementById('planList');
  const detail = document.getElementById('planDetail');
  const message = document.getElementById('historyMessage');
  let savedPlans = [];
  let currentUser = null;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const dateLabel = value => value ? new Intl.DateTimeFormat('vi-VN',{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)) : '—';

  async function loadPlans() {
    message.textContent = '';
    list.innerHTML = '<p class="history-empty">Đang tải lịch sử…</p>';
    try {
      const auth = await apiFetch('/api/auth/me');
      currentUser = auth.user;
      if (!currentUser) { location.href = 'account.html'; return; }
      setTripStateUser(currentUser);
      savedPlans = await apiFetch('/api/my/plans?limit=500');
      if (!savedPlans.length) {
        list.innerHTML = '<p class="history-empty">Bạn chưa có plan nào được lưu. Hãy đăng nhập rồi tạo một chuyến đi mới.</p>';
        detail.innerHTML = '<div class="history-empty">Plan sẽ xuất hiện ở đây sau khi được tạo.</div>';
        return;
      }
      list.innerHTML = savedPlans.map(plan => `<article class="history-row"><button class="history-open" type="button" data-plan-id="${plan.id}"><strong>${escape(plan.title)}</strong><span>${escape(plan.destination || 'Chuyến đi')}</span><small>Tạo ${dateLabel(plan.created_at)}${plan.updated_at !== plan.created_at ? ` · Cập nhật ${dateLabel(plan.updated_at)}` : ''}</small></button><button class="history-delete" type="button" data-delete-id="${plan.id}" aria-label="Xóa ${escape(plan.title)}">×</button></article>`).join('');
      list.querySelectorAll('[data-plan-id]').forEach(button => button.addEventListener('click', () => showPlan(Number(button.dataset.planId))));
      list.querySelectorAll('[data-delete-id]').forEach(button => button.addEventListener('click', () => deletePlan(Number(button.dataset.deleteId))));
      if (savedPlans.length) await showPlan(savedPlans[0].id);
    } catch (error) {
      list.innerHTML = '<p class="history-empty">Không tải được lịch sử.</p>';
      message.textContent = error.message;
      if (/401|đăng nhập/i.test(error.message)) setTimeout(() => { location.href = 'account.html'; }, 900);
    }
  }

  async function showPlan(id) {
    detail.innerHTML = '<div class="history-empty">Đang mở plan…</div>';
    try {
      const saved = await apiFetch(`/api/my/plans/${id}`);
      const plan = saved.plan || {};
      const days = Array.isArray(plan.itinerary) ? plan.itinerary : [];
      const prefs = Array.isArray(plan.preferences) ? plan.preferences.join(', ') : '';
      const cost = plan.cost_summary?.total_estimated_trip_cost;
      detail.innerHTML = `<header class="history-detail-head"><p class="account-eyebrow">${escape(saved.destination || 'CHUYẾN ĐI')}</p><h2>${escape(saved.title)}</h2><p>${Number(plan.days || days.length)} ngày · ${Number(plan.people || 1)} người${plan.budget != null ? ` · Ngân sách ${Number(plan.budget).toLocaleString('vi-VN')} ₫` : ''}</p>${prefs ? `<small>Sở thích: ${escape(prefs)}</small>` : ''}${cost != null ? `<small>Tổng dự kiến: ${Number(cost).toLocaleString('vi-VN')} ₫</small>` : ''}<button id="continuePlan" class="account-submit" type="button">Mở plan trong Travel Path</button></header><div class="history-days">${days.map(day => `<section class="history-day"><h3>Ngày ${escape(day.day)}${day.title ? ` · ${escape(day.title)}` : ''}</h3><ol>${(day.schedule || []).map(slot => `<li><time>${escape([slot.start_time,slot.end_time].filter(Boolean).join(' – ') || '')}</time><span><b>${escape(slot.activity || slot.type || 'Hoạt động')}</b>${slot.place?.name ? `<small>${escape(slot.place.name)}${slot.place.address ? ` · ${escape(slot.place.address)}` : ''}</small>` : ''}</span></li>`).join('')}</ol></section>`).join('')}</div>`;
      document.getElementById('continuePlan').addEventListener('click', () => {
        const tripState = {
          province_code: plan.province_code, province_name: plan.province_name || saved.destination,
          days: plan.days || days.length, people: plan.people || 1, budget: plan.budget ?? null,
          preferences: plan.preferences || [], hotel_id: plan.hotel?.id || null, hotel_name: plan.hotel?.name || '',
          plan, itinerary: days, plan_source: plan.plan_source || 'staging_chat',
          saved_plan_id: saved.id, saved_plan_owner_id: currentUser.id,
        };
        const conversation = [
          {role:'bot', text:`Mình đã mở lại plan ${saved.destination || ''}. Bạn muốn điều chỉnh gì?`},
          {kind:'plan', data:plan, history_id:saved.id},
        ];
        sessionStorage.setItem(userStorageKey('travel_path_assistant_v3'), JSON.stringify({state:tripState,conversation,wantsSuggestion:false}));
        saveTripState(tripState);
        location.href = 'index.html';
      });
    } catch (error) { detail.innerHTML = `<div class="history-empty">${escape(error.message)}</div>`; }
  }

  async function deletePlan(id) {
    const plan = savedPlans.find(item => item.id === id);
    if (!plan || !confirm(`Xóa plan “${plan.title}” khỏi lịch sử?`)) return;
    try {
      await apiFetch(`/api/my/plans/${id}`, {method:'DELETE'});
      savedPlans = savedPlans.filter(item => item.id !== id);
      await loadPlans();
      message.textContent = 'Đã xóa plan khỏi lịch sử.';
      message.classList.add('success');
    } catch (error) { message.textContent = error.message; }
  }

  document.getElementById('refreshPlans').addEventListener('click', loadPlans);
  loadPlans();
})();
