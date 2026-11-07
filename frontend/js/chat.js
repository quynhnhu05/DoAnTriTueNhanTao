(function(){
  const form = document.getElementById('chatForm');
  const input = document.getElementById('chatInput');
  const messages = document.getElementById('chatMessages');
  const clear = document.getElementById('chatClear');
  const newPlanBtn = document.getElementById('newPlanBtn');
  const fab = document.getElementById('chatFab');
  const panel = document.getElementById('chatPanel');
  const backdrop = document.getElementById('chatBackdrop');
  const closeBtn = document.getElementById('chatClose');
  const suggestions = document.getElementById('chatSuggestions');
  const modelStatus = document.getElementById('chatModelStatus');
  if (!form || !input || !messages) return;

  const historyKey = () => userStorageKey('travel_chat_history_v1');
  const maxHistory = 24;
  let busy = false;

  function userFacingError(error){
    const message = String(error?.message || '');
    if(/<html|502 bad gateway|504 gateway|nginx/i.test(message))
      return getLang()==='en'
        ? 'The trip service is restarting or taking too long. Please wait a moment and send your request again.'
        : 'Dịch vụ lập lịch đang khởi động lại hoặc phản hồi quá lâu. Bạn chờ một chút rồi gửi lại yêu cầu nhé.';
    if(/failed to fetch|networkerror|load failed/i.test(message))
      return getLang()==='en'
        ? 'I cannot reach the trip service right now. Please try again in a moment.'
        : 'Hiện chưa kết nối được với dịch vụ chuyến đi. Bạn thử gửi lại sau một lát nhé.';
    return message.replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').slice(0,300) || (getLang()==='en'?'Request failed.':'Yêu cầu chưa thực hiện được.');
  }

  async function sendChatRequest(payload){
    for(let attempt=0;attempt<2;attempt++){
      try{
        return await apiFetch('/api/chat', {method:'POST', body:JSON.stringify(payload)});
      }catch(error){
        const transient=/failed to fetch|networkerror|load failed|connection reset/i.test(String(error?.message||''));
        if(!transient||attempt===1) throw error;
        await new Promise(resolve=>setTimeout(resolve,1500));
      }
    }
  }

  async function updateModelStatus(){
    if(!modelStatus) return;
    const label = modelStatus.querySelector('span');
    try{
      const status = await apiFetch('/api/chat/status');
      modelStatus.dataset.mode = status?.available ? 'local-ai' : 'rules';
      if(label) label.textContent = status?.available
        ? (getLang()==='en'?'LOCAL AI READY':'AI SẴN SÀNG')
        : (getLang()==='en'?'BASIC ASSISTANT':'TRỢ LÝ CƠ BẢN');
    }catch(_){
      modelStatus.dataset.mode = 'rules';
      if(label) label.textContent = getLang()==='en'?'BASIC ASSISTANT':'TRỢ LÝ CƠ BẢN';
    }
  }

  function renderMessage(role, text, save = true){
    const row = document.createElement('div');
    row.className = `chat-message ${role}`;
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble';
    bubble.textContent = String(text || '');
    row.appendChild(bubble);
    messages.appendChild(row);
    messages.scrollTop = messages.scrollHeight;
    if(save) persistHistory();
  }

  function readHistory(){
    try {
      const history = JSON.parse(sessionStorage.getItem(historyKey()) || '[]');
      return Array.isArray(history) ? history.filter(x => ['user','bot'].includes(x.role) && typeof x.text === 'string').slice(-maxHistory) : [];
    } catch (_) { return []; }
  }

  function persistHistory(){
    const history = [...messages.querySelectorAll('.chat-message:not(.typing)')].map(row => ({
      role: row.classList.contains('user') ? 'user' : 'bot',
      text: row.querySelector('.chat-bubble')?.textContent || ''
    })).slice(-maxHistory);
    sessionStorage.setItem(historyKey(), JSON.stringify(history));
  }

  function welcome(){
    if(messages.children.length) return;
    const history = readHistory();
    if(history.length) history.forEach(item => renderMessage(item.role, item.text, false));
    else renderMessage('bot', tr('assistantWelcome'));
  }

  function toggleChat(open){
    if(!panel) return;
    panel.classList.toggle('hidden', !open);
    backdrop?.classList.toggle('hidden', !open);
    document.body.classList.toggle('chat-open', !!open);
    fab?.setAttribute('aria-expanded', String(!!open));
    if(open){ welcome(); setTimeout(() => input?.focus(), 80); }
  }

  function setBusy(value){
    busy = value;
    const button = form.querySelector('button[type=submit]');
    if(button) button.disabled = value;
    input.disabled = value;
    suggestions?.querySelectorAll('button').forEach(button => button.disabled = value);
    if(value){
      const typing = document.createElement('div');
      typing.className = 'chat-message bot typing';
      typing.setAttribute('role','status');
      typing.setAttribute('aria-label', getLang()==='en'?'Assistant is thinking':'Trợ lý đang suy nghĩ');
      typing.innerHTML = '<div class="chat-bubble typing-dots"><i></i><i></i><i></i></div>';
      messages.appendChild(typing);
      messages.scrollTop = messages.scrollHeight;
    }else messages.querySelector('.typing')?.remove();
  }

  async function sendText(text){
    text = String(text || '').trim();
    if(!text || busy) return;
    renderMessage('user', text);
    input.value = '';
    setBusy(true);
    try{
      const conversation = readHistory();
      const res = await sendChatRequest({message:text,state:getTripState(),history:conversation.slice(0,-1)});
      if(res?.success === false) throw new Error(res.message || 'Request failed');
      renderMessage('bot', res.message || tr('changed'));
      const next = res?.data?.state;
      const plan = res?.data?.plan;
      if(next) saveTripState(next);
      if(plan){
        saveTripState({plan, itinerary:plan.itinerary || []});
        if(plan.hotel?.name) saveTripState({hotel_id:plan.hotel.id, hotel_name:plan.hotel.name});
        window.renderTripPlan?.(plan);
      }else if(res?.data?.itinerary){
        saveTripState({itinerary:res.data.itinerary});
        const current = getTripState();
        if(current.plan){ current.plan.itinerary = res.data.itinerary; saveTripState(current); }
        window.renderTripPlan?.(current.plan || res.data);
      }
    }catch(err){
      renderMessage('bot', (getLang()==='en'?'I could not complete that request: ':'Mình chưa thực hiện được yêu cầu này: ') + userFacingError(err));
    }finally{ setBusy(false); }
  }

  fab?.addEventListener('click', () => toggleChat(true));
  closeBtn?.addEventListener('click', () => toggleChat(false));
  backdrop?.addEventListener('click', () => toggleChat(false));
  document.addEventListener('keydown', e => { if(e.key === 'Escape') toggleChat(false); });
  clear?.addEventListener('click', () => { messages.replaceChildren(); sessionStorage.removeItem(historyKey()); welcome(); });
  newPlanBtn?.addEventListener('click', () => sendText(getLang()==='en'?'Create a completely different itinerary with new places.':'Tạo một lịch trình hoàn toàn khác, ưu tiên các địa điểm mới.'));
  suggestions?.addEventListener('click', e => {
    const button = e.target.closest('[data-prompt]');
    if(button) sendText(button.dataset.prompt);
  });
  form.addEventListener('submit', e => { e.preventDefault(); sendText(input.value); });
  document.addEventListener('languageChanged', () => { if(messages.children.length === 0) welcome(); updateModelStatus(); });
  updateModelStatus();
  welcome();
})();
