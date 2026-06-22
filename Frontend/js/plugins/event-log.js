/**
 * event-log.js — Event Log Plugin
 *
 * Scrolling text feed showing VirTra scenario events.
 * Features:
 * - Color-coded by event type (shots, hits, misses, actor changes, system)
 * - Filter toggles to select/unselect event types
 * - Auto-scroll to latest in live mode, follows playhead in replay
 * - Click event to seek in replay mode
 */

(function () {
  // Event type → icon + color
  const EVENT_STYLES = {
    'Shot Fired':          { marker: 'SHOT',  color: 'var(--event-shot)',   label: 'Shots' },
    'Shot Hit':            { marker: 'HIT',   color: 'var(--event-hit)',    label: 'Hits' },
    'Shot Miss':           { marker: 'MISS',  color: 'var(--event-miss)',   label: 'Misses' },
    'Actor State Changed': { marker: 'STATE', color: 'var(--event-actor)', label: 'Actors' },
    'Event Triggered':     { marker: 'TRIG',  color: 'var(--event-actor)', label: 'Triggers' },
    'Scenario Started':    { marker: 'SYS',   color: 'var(--event-system)',label: 'System' },
    'Scenario Stopped':    { marker: 'SYS',   color: 'var(--event-system)',label: 'System' },
    'Actor IsFrozen Changed': { marker: 'FRZ', color: 'var(--event-actor)', label: 'Actors' },
  };

  const DEFAULT_STYLE = { marker: '--', color: 'var(--text-dim)', label: 'Other' };

  class EventLogChart {
    constructor(container, streamMeta) {
      this.streamMeta = streamMeta;
      this.events = [];
      this.activeFilters = new Set(Object.keys(EVENT_STYLES)); // all active by default

      // Build DOM
      this.el = document.createElement('div');
      this.el.className = 'event-log-container';

      // Filter bar
      this.filterBar = document.createElement('div');
      this.filterBar.className = 'event-log-filter';
      this._buildFilters();
      this.el.appendChild(this.filterBar);

      // Entries container
      this.entriesEl = document.createElement('div');
      this.entriesEl.className = 'event-log-entries';
      this.el.appendChild(this.entriesEl);

      container.appendChild(this.el);

      // Also listen to EventBus for VirTra events from any source
      this._busHandler = (evt) => this._addEvent(evt);
      LabReplay.EventBus.on('virtra-event', this._busHandler);
    }

    _buildFilters() {
      // Collect unique filter labels
      const labels = new Set();
      for (const s of Object.values(EVENT_STYLES)) labels.add(s.label);

      for (const label of labels) {
        const tag = document.createElement('span');
        tag.className = 'event-filter-tag active';
        tag.textContent = label;
        tag.dataset.label = label;
        tag.addEventListener('click', () => this._toggleFilter(label, tag));
        this.filterBar.appendChild(tag);
      }
    }

    _toggleFilter(label, tagEl) {
      // Toggle all event types under this label
      const types = Object.entries(EVENT_STYLES)
        .filter(([, s]) => s.label === label)
        .map(([t]) => t);

      const isActive = tagEl.classList.contains('active');
      if (isActive) {
        tagEl.classList.remove('active');
        types.forEach(t => this.activeFilters.delete(t));
      } else {
        tagEl.classList.add('active');
        types.forEach(t => this.activeFilters.add(t));
      }

      this._rerenderEntries();
    }

    pushSample(timestamp, data) {
      // Parse the VirTra event string
      const text = Array.isArray(data) ? data.join(' ') : String(data);
      this._addEvent({ timestamp, data: text, stream: this.streamMeta.name });
    }

    _addEvent(evt) {
      this.events.push(evt);

      // Trim to last 500 events
      if (this.events.length > 500) this.events.shift();

      // Parse event type from the text
      const eventType = this._detectType(evt.data);
      if (!this.activeFilters.has(eventType)) return;

      this._appendEntry(evt, eventType);
    }

    _appendEntry(evt, eventType) {
      const style = EVENT_STYLES[eventType] || DEFAULT_STYLE;
      const entry = document.createElement('div');
      entry.className = 'event-entry';

      // Format time
      const date = new Date(evt.timestamp * 1000);
      const timeStr = date.toLocaleTimeString('en-US', { hour12: false });

      entry.innerHTML = `
        <span class="event-time">${timeStr}</span>
        <span class="event-marker-tag" style="color:${style.color}">[${style.marker}]</span>
        <span class="event-text" style="color:${style.color}">${this._shortenText(evt.data)}</span>
      `;

      // Click to seek in replay mode
      entry.addEventListener('click', () => {
        const state = LabReplay.ModeManager.getState();
        if (state.mode === 'replay') {
          LabReplay.StreamRouter.sendTransport('seek', evt.timestamp);
        }
      });
      entry.style.cursor = 'pointer';

      this.entriesEl.appendChild(entry);

      // Auto-scroll
      this.entriesEl.scrollTop = this.entriesEl.scrollHeight;

      // Limit DOM entries
      while (this.entriesEl.children.length > 200) {
        this.entriesEl.removeChild(this.entriesEl.firstChild);
      }
    }

    _rerenderEntries() {
      this.entriesEl.innerHTML = '';
      for (const evt of this.events) {
        const eventType = this._detectType(evt.data);
        if (this.activeFilters.has(eventType)) {
          this._appendEntry(evt, eventType);
        }
      }
    }

    _detectType(text) {
      if (!text) return 'Other';
      for (const type of Object.keys(EVENT_STYLES)) {
        if (text.includes(type) || text.toLowerCase().includes(type.toLowerCase())) {
          return type;
        }
      }
      // Check for common patterns
      if (text.includes('Shot') && text.includes('Hit')) return 'Shot Hit';
      if (text.includes('Shot') && text.includes('Miss')) return 'Shot Miss';
      if (text.includes('Shot') && text.includes('Fired')) return 'Shot Fired';
      if (text.includes('state changed')) return 'Actor State Changed';
      if (text.includes('triggered')) return 'Event Triggered';
      return 'Event Triggered'; // default for unrecognized
    }

    _shortenText(text) {
      if (!text) return '';
      // Truncate long event text for display
      return text.length > 120 ? text.substring(0, 120) + '…' : text;
    }

    resize() {}
    destroy() {
      LabReplay.EventBus.off('virtra-event', this._busHandler);
    }
    getElement() { return this.el; }
  }

  LabReplay.registerPlugin({
    id: 'event-log',
    name: 'Event Log',
    streamTypes: ['events', 'Markers', 'VirTra', 'Marker'],
    create(container, streamMeta) {
      return new EventLogChart(container, streamMeta);
    }
  });
})();
