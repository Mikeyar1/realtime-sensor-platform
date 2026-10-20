/** Bootstrap the live and session-history pages after the classic scripts load. */
function init() {
  LabReview.TopBarManager.init(document.getElementById('topbar-slot'));
  LabReview.LiveIntelPage.init();
  LabReview.PerfReviewPage.init();
  LabReview.Sidebar.init();
  LabReview.StreamRouter.connect();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
