(() => {
  const tbody = document.getElementById('usersTable');
  const message = document.getElementById('adminMessage');
  const formatDate = value => value ? new Intl.DateTimeFormat('vi-VN', {dateStyle:'medium', timeStyle:'short'}).format(new Date(value)) : 'Chưa đăng nhập';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function loadUsers() {
    message.textContent = '';
    tbody.innerHTML = '<tr><td colspan="5">Đang tải danh sách…</td></tr>';
    try {
      const users = await apiFetch('/api/admin/users?limit=1000');
      tbody.innerHTML = users.length ? users.map(user => `<tr><td><strong>${escape(user.display_name)}</strong><small>${escape(user.email)}</small></td><td><span class="role-badge ${user.role === 'admin' ? 'admin' : ''}">${user.role === 'admin' ? 'Quản trị viên' : 'Người dùng'}</span></td><td>${formatDate(user.created_at)}</td><td>${formatDate(user.last_login_at)}</td><td>${Number(user.login_count) || 0}</td></tr>`).join('') : '<tr><td colspan="5">Chưa có tài khoản nào.</td></tr>';
    } catch (error) {
      tbody.innerHTML = '<tr><td colspan="5">Không tải được danh sách.</td></tr>';
      message.textContent = error.message.includes('403') || error.message.includes('quản trị') ? 'Tài khoản của bạn không có quyền quản trị.' : error.message.includes('401') || error.message.includes('đăng nhập') ? 'Bạn cần đăng nhập bằng tài khoản quản trị.' : error.message;
      if (message.textContent.includes('đăng nhập')) setTimeout(() => { location.href = 'account.html'; }, 1200);
    }
  }
  document.getElementById('refreshUsers').addEventListener('click', loadUsers);
  loadUsers();
})();
