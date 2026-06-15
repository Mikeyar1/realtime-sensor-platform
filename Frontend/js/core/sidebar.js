/**
 * sidebar.js
 *
 * Handles page navigation for the new 4-page grouped layout.
 * Manages:
 *   - Nav item activation (highlight active item)
 *   - Page container show/hide
 *   - Event bar visibility (shown only for Live Monitor & Replay)
 *   - TopBarManager activation
 *   - Stream groups section (context-dependent)
 *   - EventBus 'page-changed' broadcast
 *
 * Pages with event bar: live-monitor, replay
 * Pages without event bar: live-intel, workspace
 */

window.LabReplay = window.LabReplay || {};

LabReplay.Sidebar = (function () {

  // Default page on load
  let _activePage = 'live-monitor';

  // Pages that show the event bar (VirTra + Speech) at the bottom
  const PAGES_WITH_EVENT_BAR = new Set(['live-monitor', 'replay']);

  // Pages that show the stream groups section in the sidebar
  const PAGES_WITH_STREAMS = new Set(['live-monitor', 'replay']);

  function init() {
    // Bind nav item clicks
    document.querySelectorAll('.nav-item[data-page]').forEach(item => {
      item.addEventListener('click', () => switchPage(item.dataset.page));
    });

    // Listen for catalog updates — update stream groups if on a page that shows them
    LabReplay.EventBus.on('catalog-updated', (catalog) => {
      if (PAGES_WITH_STREAMS.has(_activePage)) {
        _renderStreamGroups(catalog);
      }
    });

    // Activate default page (Live Monitor)
    switchPage(_activePage);
  }

  /**
   * Switch to a named page.
   * Handles: nav highlight, page visibility, event bar, TopBarManager, stream groups.
   */
  function switchPage(page) {
    _activePage = page;

    // ── Nav highlight ──────────────────────────────────────────
    document.querySelectorAll('.nav-item[data-page]').forEach(el => {
      el.classList.toggle('active', el.dataset.page === page);
    });

    // ── Page containers ────────────────────────────────────────
    document.querySelectorAll('.page').forEach(el => {
      el.classList.toggle('active', el.id === `page-${page}`);
    });

    // ── Event bar visibility ───────────────────────────────────────────────
    const app = document.getElementById('app');
    if (app) {
      const show = PAGES_WITH_EVENT_BAR.has(page);
      app.classList.toggle('event-bar-hidden', !show);

      // Directly set --event-bar-h inline so the grid row collapses/expands.
      // CSS class rules cannot override inline custom properties, so we must
      // write the inline value ourselves here.
      if (!show) {
        app.style.setProperty('--event-bar-h', '0px');
      } else {
        const stored = parseInt(localStorage.getItem('labReplay.eventBarHeight'), 10);
        const h = stored && stored >= 80 ? stored : 150;
        app.style.setProperty('--event-bar-h', `${h}px`);
      }
    }

    // ── Stream groups section ──────────────────────────────────
    const streamGroups = document.getElementById('stream-groups');
    if (streamGroups) {
      streamGroups.style.display = PAGES_WITH_STREAMS.has(page) ? '' : 'none';
    }

    // ── Top bar slot ───────────────────────────────────────────
    // Delegate to TopBarManager if it is initialized
    if (LabReplay.TopBarManager) {
      LabReplay.TopBarManager.activate(page);
    }

    // ── Broadcast ─────────────────────────────────────────────
    LabReplay.EventBus.emit('page-changed', page);
  }

  // ── Stream groups (sidebar context section) ───────────────────

  const STREAM_CATEGORIES = [
    { label: 'Cardiac',      types: ['ECG', 'HR', 'HRV', 'PPG', 'PPI'],              color: '#CF5C5C' },
    { label: 'Eye Tracking', types: ['Gaze', 'Pupil', 'eye_events', 'Event'],         color: '#8B7EC8' },
    { label: 'Motion',       types: ['ACC', 'GYRO', 'IMU', 'Motion', 'Pose'],         color: '#5B8DEF' },
    { label: 'Events',       types: ['VirTraEvents', 'Markers', 'SessionData'],        color: '#C4A63A' },
    { label: 'Other',        types: [],                                                color: '#6B7280' },
  ];

  function _renderStreamGroups(catalog) {
    const container = document.getElementById('stream-groups');
    if (!container) return;
    container.innerHTML = '';

    if (!catalog || catalog.length === 0) {
      container.innerHTML = `
        <div class="stream-group">
          <div class="stream-group-title">Streams</div>
          <div class="stream-group-subtitle">No active streams</div>
        </div>`;
      return;
    }

    // Bucket streams into categories (first match wins; last bucket is catch-all)
    const buckets = STREAM_CATEGORIES.map(c => ({ ...c, streams: [] }));

    for (const stream of catalog) {
      const nameLo = stream.name.toLowerCase();
      const typeLo = (stream.stream_type || '').toLowerCase();
      let placed = false;
      for (const bucket of buckets.slice(0, -1)) {
        if (bucket.types.some(t => nameLo.includes(t.toLowerCase()) || typeLo.includes(t.toLowerCase()))) {
          bucket.streams.push(stream);
          placed = true;
          break;
        }
      }
      if (!placed) buckets[buckets.length - 1].streams.push(stream);
    }

    for (const bucket of buckets) {
      if (bucket.streams.length === 0) continue;
      const group = document.createElement('div');
      group.className = 'stream-group';
      group.innerHTML = `
        <div class="stream-group-title">${bucket.label}</div>
        <div class="stream-group-subtitle">${bucket.streams.length} stream${bucket.streams.length > 1 ? 's' : ''}</div>
      `;
      for (const s of bucket.streams) {
        const item = document.createElement('div');
        item.className = 'stream-item';
        item.dataset.stream = s.name;
        item.innerHTML = `
          <div class="stream-dot" style="background:${bucket.color}"></div>
          <span class="stream-label">${s.name}</span>
          <span class="stream-toggle" title="Toggle">on</span>
        `;
        item.querySelector('.stream-toggle').addEventListener('click', (e) => {
          e.stopPropagation();
          _toggleStream(s.name, item);
        });
        group.appendChild(item);
      }
      container.appendChild(group);
    }
  }

  function _toggleStream(streamName, itemEl) {
    const router = LabReplay.StreamRouter;
    if (!router) return;
    const subscribed = router.getSubscribed ? router.getSubscribed().has(streamName) : false;
    if (subscribed) {
      router.unsubscribe(streamName);
      itemEl.querySelector('.stream-toggle').style.opacity = '0.3';
      itemEl.querySelector('.stream-toggle').textContent = 'off';
    } else {
      router.subscribe(streamName);
      itemEl.querySelector('.stream-toggle').style.opacity = '1';
      itemEl.querySelector('.stream-toggle').textContent = 'on';
    }
  }

  function getActivePage() { return _activePage; }

  return { init, switchPage, getActivePage };
})();
