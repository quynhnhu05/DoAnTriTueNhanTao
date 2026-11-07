(() => {
  const form = document.getElementById('accountForm');
  const title = document.getElementById('accountTitle');
  const intro = document.getElementById('accountIntro');
  const nameField = document.getElementById('nameField');
  const passwordField = document.getElementById('passwordField');
  const confirmField = document.getElementById('confirmField');
  const otpField = document.getElementById('otpField');
  const username = document.getElementById('username');
  const password = document.getElementById('password');
  const passwordConfirm = document.getElementById('passwordConfirm');
  const resetPassword = document.getElementById('resetPassword');
  const resetPasswordConfirm = document.getElementById('resetPasswordConfirm');
  const resetPasswordFields = document.getElementById('resetPasswordFields');
  const otp = document.getElementById('otp');
  const submit = document.getElementById('submitButton');
  const switchText = document.getElementById('modeSwitch');
  const switchButton = document.getElementById('switchButton');
  const resendButton = document.getElementById('resendOtp');
  const forgotRow = document.getElementById('forgotPasswordRow');
  const forgotRegisterRow = document.getElementById('forgotRegisterRow');
  const forgotButton = document.getElementById('forgotPasswordButton');
  const registerFromForgot = document.getElementById('registerFromForgot');
  const message = document.getElementById('accountMessage');
  const signedIn = document.getElementById('signedInPanel');
  [password, document.getElementById('passwordConfirm'), resetPassword, resetPasswordConfirm].forEach(input => {
    input.addEventListener('focus', () => {
      input.value = '';
      input.readOnly = false;
    }, {once: true});
  });
  let mode = new URLSearchParams(location.search).get('mode') === 'register' ? 'register' : 'login';
  let otpRequested = false;
  let resendTimer = null;

  function showMessage(text = '', success = false) {
    message.textContent = text;
    message.classList.toggle('success', success);
  }
  function updateResendTimer(seconds = 60) {
    clearInterval(resendTimer);
    let remaining = seconds;
    resendButton.disabled = true;
    resendButton.textContent = `Gửi lại mã sau ${remaining} giây`;
    resendTimer = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        clearInterval(resendTimer);
        resendButton.disabled = false;
        resendButton.textContent = 'Gửi lại mã OTP';
      } else resendButton.textContent = `Gửi lại mã sau ${remaining} giây`;
    }, 1000);
  }
  function setMode(next) {
    mode = next;
    const registering = mode === 'register';
    const recovering = mode === 'forgot';
    title.textContent = registering ? 'Tạo tài khoản' : (recovering ? 'Quên mật khẩu' : 'Chào mừng bạn');
    intro.textContent = registering
      ? 'Tạo tài khoản mới bằng email và mã OTP.'
      : (recovering ? 'Nhập email đã đăng ký để nhận mã OTP và đặt mật khẩu mới.' : 'Đăng nhập bằng email để tiếp tục, hoặc đăng ký tài khoản mới.');
    nameField.hidden = !registering;
    passwordField.hidden = recovering;
    confirmField.hidden = !registering;
    resetPasswordFields.hidden = !(recovering && otpRequested);
    resendButton.hidden = !(registering || recovering) || !otpRequested;
    otpField.hidden = !(registering || recovering) || !otpRequested;
    username.required = registering;
    password.required = !recovering;
    passwordConfirm.required = registering;
    resetPassword.required = recovering && otpRequested;
    resetPasswordConfirm.required = recovering && otpRequested;
    otp.required = (registering || recovering) && otpRequested;
    username.disabled = registering && otpRequested;
    password.disabled = registering && otpRequested;
    passwordConfirm.disabled = registering && otpRequested;
    document.getElementById('email').disabled = (registering || recovering) && otpRequested;
    password.autocomplete = 'off';
    submit.textContent = registering
      ? (otpRequested ? 'Xác nhận OTP và tạo tài khoản' : 'Gửi mã OTP')
      : (recovering ? (otpRequested ? 'Đặt lại mật khẩu' : 'Gửi mã đặt lại mật khẩu') : 'Đăng nhập');
    forgotRow.hidden = mode !== 'login';
    forgotRegisterRow.hidden = !recovering;
    switchText.firstChild.textContent = registering ? 'Đã có tài khoản? ' : (recovering ? 'Nhớ mật khẩu? ' : 'Chưa có tài khoản? ');
    switchButton.textContent = registering || recovering ? 'Đăng nhập' : 'Đăng ký';
  }
  function registrationPayload() {
    return {
      email: document.getElementById('email').value.trim(),
      username: username.value.trim(),
      password: password.value,
      password_confirm: passwordConfirm.value,
    };
  }
  async function sendOtp() {
    if (password.value !== passwordConfirm.value) throw new Error('Hai lần nhập mật khẩu chưa trùng khớp.');
    const result = await apiFetch('/api/auth/register/request-otp', {
      method: 'POST', body: JSON.stringify(registrationPayload()),
    });
    otpRequested = true;
    otp.value = '';
    setMode('register');
    updateResendTimer();
    showMessage(result.message || 'Đã gửi mã OTP đến email. Mã có hiệu lực trong 10 phút.', true);
    otp.focus();
  }

  async function sendPasswordResetOtp() {
    const result = await apiFetch('/api/auth/password/forgot/request-otp', {
      method: 'POST', body: JSON.stringify({email: document.getElementById('email').value.trim()}),
    });
    otpRequested = true;
    otp.value = '';
    setMode('forgot');
    updateResendTimer();
    showMessage(result.message || 'Nếu email đã đăng ký, mã OTP sẽ được gửi đến hộp thư của bạn.', true);
    otp.focus();
  }

  switchButton.addEventListener('click', () => {
    showMessage();
    otpRequested = false;
    clearInterval(resendTimer);
    password.value = '';
    passwordConfirm.value = '';
    resetPassword.value = '';
    resetPasswordConfirm.value = '';
    otp.value = '';
    setMode(mode === 'login' ? 'register' : 'login');
  });
  forgotButton.addEventListener('click', () => {
    otpRequested = false;
    clearInterval(resendTimer);
    password.value = '';
    passwordConfirm.value = '';
    resetPassword.value = '';
    resetPasswordConfirm.value = '';
    otp.value = '';
    showMessage();
    setMode('forgot');
  });
  registerFromForgot.addEventListener('click', () => {
    otpRequested = false;
    clearInterval(resendTimer);
    password.value = '';
    passwordConfirm.value = '';
    resetPassword.value = '';
    resetPasswordConfirm.value = '';
    otp.value = '';
    showMessage();
    setMode('register');
  });
  resendButton.addEventListener('click', async () => {
    showMessage();
    try {
      if (mode === 'forgot') await sendPasswordResetOtp();
      else await sendOtp();
    } catch (error) { showMessage(error.message); }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    showMessage();
    submit.disabled = true;
    const originalText = submit.textContent;
    submit.textContent = mode === 'login' ? 'Đang đăng nhập…' : (mode === 'forgot' && otpRequested ? 'Đang đặt lại…' : (otpRequested ? 'Đang xác nhận…' : 'Đang gửi mã…'));
    const email = document.getElementById('email').value.trim();
    const pass = password.value;
    try {
      if (mode === 'register' && !otpRequested) {
        await sendOtp();
      } else if (mode === 'register') {
        const result = await apiFetch('/api/auth/register/verify-otp', {
          method: 'POST', body: JSON.stringify({email, otp: otp.value.trim()}),
        });
        otpRequested = false;
        clearInterval(resendTimer);
        setMode('login');
        password.value = '';
        passwordConfirm.value = '';
        otp.value = '';
        username.value = '';
        showMessage(`Tài khoản ${result.user.username} đã được tạo. Hãy đăng nhập bằng email và mật khẩu.`, true);
      } else if (mode === 'forgot' && !otpRequested) {
        await sendPasswordResetOtp();
      } else if (mode === 'forgot') {
        if (resetPassword.value !== resetPasswordConfirm.value) throw new Error('Hai lần nhập mật khẩu mới chưa trùng khớp.');
        const result = await apiFetch('/api/auth/password/forgot/verify-otp', {
          method: 'POST', body: JSON.stringify({
            email, otp: otp.value.trim(), new_password: resetPassword.value, password_confirm: resetPasswordConfirm.value,
          }),
        });
        otpRequested = false;
        clearInterval(resendTimer);
        password.value = '';
        passwordConfirm.value = '';
        resetPassword.value = '';
        resetPasswordConfirm.value = '';
        otp.value = '';
        setMode('login');
        showMessage(result.message || 'Đặt lại mật khẩu thành công. Hãy đăng nhập bằng mật khẩu mới.', true);
      } else {
        const result = await apiFetch('/api/auth/login', {method:'POST', body:JSON.stringify({email, password:pass})});
        location.href = result.user.role === 'admin' ? 'admin.html' : 'index.html';
      }
    } catch (error) {
      showMessage(error.message || 'Không thể thực hiện yêu cầu. Vui lòng thử lại.');
    } finally {
      submit.disabled = false;
      if (submit.isConnected) submit.textContent = mode === 'register'
        ? (otpRequested ? 'Xác nhận OTP và tạo tài khoản' : 'Gửi mã OTP')
        : (mode === 'forgot' ? (otpRequested ? 'Đặt lại mật khẩu' : 'Gửi mã đặt lại mật khẩu') : 'Đăng nhập');
      if (!submit.isConnected) submit.textContent = originalText;
    }
  });

  document.getElementById('logoutButton').addEventListener('click', async () => {
    await apiFetch('/api/auth/logout', {method:'POST', body:'{}'}).catch(() => {});
    location.reload();
  });

  async function init() {
    setMode(mode);
    try {
      const result = await apiFetch('/api/auth/me');
      if (!result.user) {
        const homeLink = document.querySelector('.account-topbar .account-home');
        if (homeLink) homeLink.hidden = true;
        return;
      }
      form.hidden = true;
      switchText.hidden = true;
      signedIn.hidden = false;
      title.textContent = 'Tài khoản của bạn';
      document.getElementById('signedInName').textContent = `${result.user.display_name} · ${result.user.email}`;
      document.getElementById('adminLink').hidden = result.user.role !== 'admin';
      document.getElementById('historyLink').hidden = false;
    } catch (_) { /* The form remains available if the API is offline. */ }
  }
  init();
})();
