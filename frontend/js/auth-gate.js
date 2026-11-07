(() => {
  apiFetch('/api/auth/me')
    .then(result => {
      if (!result?.user) {
        location.replace('account.html');
        return;
      }
      setTripStateUser(result.user);
      document.documentElement.classList.remove('auth-pending');
    })
    .catch(() => {
      location.replace('account.html?connection=required');
    });
})();
