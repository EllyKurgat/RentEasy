/* ================================================================
   RentEasy – UI Helpers: Toast + Alert + Loading States
   ================================================================
   • Provides global toast notification system
   • Auto-converts Django messages to toasts
   • Loading state helpers for buttons
   ================================================================ */

// Clean up old dark-mode preference if present
(function () { localStorage.removeItem('re-dark'); document.body.classList.remove('dark'); })();

// DOM ready – wire up UI helpers
document.addEventListener('DOMContentLoaded', function () {

  // ── 1. Toast notification system ────────────────────────────────
  var toastContainer = document.createElement('div');
  toastContainer.id = 're-toast-container';
  toastContainer.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:10px;pointer-events:none;';
  document.body.appendChild(toastContainer);

  window.reToast = function(message, type, duration) {
    type = type || 'info';
    duration = duration || 4000;
    var toast = document.createElement('div');
    toast.className = 're-toast ' + type;
    toast.style.pointerEvents = 'auto';
    var icon = type === 'success' ? 'fa-check-circle' : type === 'error' ? 'fa-exclamation-circle' : 'fa-info-circle';
    toast.innerHTML = '<i class="fa-solid ' + icon + '"></i><span>' + message + '</span><button onclick="this.parentElement.remove()" style="background:none;border:none;color:inherit;margin-left:auto;cursor:pointer;font-size:16px;padding:0 0 0 12px;opacity:0.7;">&times;</button>';
    toastContainer.appendChild(toast);
    setTimeout(function() {
      toast.style.animation = 're-toast-out 0.3s ease-in forwards';
      setTimeout(function() { toast.remove(); }, 300);
    }, duration);
  };

  // ── 4. Auto-convert .re-alert messages to toasts ────────────────
  var alerts = document.querySelectorAll('.re-alert');
  alerts.forEach(function(alert) {
    var text = alert.textContent.trim();
    var type = 'info';
    if (alert.classList.contains('success')) type = 'success';
    else if (alert.classList.contains('error')) type = 'error';
    else if (alert.classList.contains('warning')) type = 'warning';
    if (text) {
      window.reToast(text, type, 5000);
      // Fade out the inline alert after a moment
      setTimeout(function() {
        alert.style.transition = 'opacity 0.3s, max-height 0.3s';
        alert.style.opacity = '0';
        alert.style.maxHeight = '0';
        alert.style.overflow = 'hidden';
        alert.style.marginBottom = '0';
        alert.style.padding = '0';
        setTimeout(function() { alert.remove(); }, 300);
      }, 1000);
    }
  });

  // ── 5. Form loading states ──────────────────────────────────────
  document.querySelectorAll('form[data-loading]').forEach(function(form) {
    form.addEventListener('submit', function() {
      var btn = form.querySelector('button[type="submit"]');
      if (btn && !btn.disabled) {
        btn.disabled = true;
        btn.dataset.originalText = btn.innerHTML;
        btn.innerHTML = '<span class="re-loading" style="width:16px;height:16px;"></span> Processing...';
      }
    });
  });

  // ── 6. Auto-dismiss old-style .messages ─────────────────────────
  var msgList = document.querySelector('.messages');
  if (msgList) {
    setTimeout(function() {
      msgList.style.transition = 'opacity 0.5s';
      msgList.style.opacity = '0';
      setTimeout(function() { msgList.remove(); }, 500);
    }, 5000);
  }
});

