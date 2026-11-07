const card = document.getElementById('card');
const message = document.getElementById('message');
const details = document.getElementById('details');

window.travelDesktop.onStatus((status) => {
  if (!status) return;
  message.textContent = status.message || 'Đang khởi động…';
  if (status.kind === 'error') card.classList.add('error');
  if (status.details) details.textContent = status.details;
});

document.getElementById('retry').addEventListener('click', () => {
  card.classList.remove('error');
  details.textContent = '';
  window.travelDesktop.retryStartup();
});
document.getElementById('quit').addEventListener('click', () => window.close());
