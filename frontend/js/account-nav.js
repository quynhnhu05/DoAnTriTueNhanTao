document.addEventListener('DOMContentLoaded', async () => {
  const link = document.getElementById('accountLink');
  const pageNav = document.querySelector('.admin-nav');
  if (!link && !pageNav) return;
  try {
    const result = await apiFetch('/api/auth/me');
    const user = result?.user;
    if (!user) return;
    if (link) {
      link.textContent = user.role === 'admin' ? 'Quản trị' : user.display_name;
      link.href = user.role === 'admin' ? 'admin.html' : 'account.html';
      link.title = user.email;
      const historyLink = document.createElement('a');
      historyLink.className = 'account-link history-nav-link';
      historyLink.href = 'history.html';
      historyLink.textContent = 'Lịch sử plan';
      historyLink.setAttribute('aria-label', 'Lịch sử plan đã lưu');
      link.after(historyLink);
    }

    const logoutButton = document.createElement('button');
    logoutButton.className = 'account-link account-logout';
    logoutButton.type = 'button';
    logoutButton.textContent = 'Đăng xuất';
    logoutButton.addEventListener('click', async () => {
      logoutButton.disabled = true;
      logoutButton.textContent = 'Đang đăng xuất…';
      await apiFetch('/api/auth/logout', {method: 'POST', body: '{}'}).catch(() => {});
      location.replace('account.html');
    });
    (pageNav || link.parentElement).append(logoutButton);
  } catch (_) { /* Keep the sign-in link visible when the API is unavailable. */ }
});
