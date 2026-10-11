/**
 * StashReleaseManager - History
 *
 * The *arrs' History + System > Events in one page (2026-09-28, v0.79.0,
 * user request -- see api/history.py for what gets logged and what
 * deliberately doesn't):
 *   - the log: per-scene history (added to the wishlist, grabbed,
 *     imported, import failed, blocklisted, ...) and system events (job
 *     results, connection errors, settings changes, error pop-ups),
 *     newest first, paged + filtered, updated live by the sidecar;
 *   - "Imported": the permanent list of imported releases, moved here
 *     from the Activity page (the log is pruned after
 *     history_retention_days, this list isn't).
 */
(function () {
  'use strict';

  let liveDetach = null;

  const LEVEL_LABEL = { info: 'Info', warning: 'Warning', error: 'Error' };
  const KIND_LABEL = { history: 'Scene', event: 'System' };

  async function render(container) {
    const { el } = window.SRW;
    if (liveDetach) { liveDetach(); liveDetach = null; }
    container.innerHTML = '';
    container.appendChild(window.SRW.buildPageHeader('history'));
    const hint = window.SRW.buildLiveHint();
    container.appendChild(hint);
    await window.SRW.loadMetadataProviderUrls();

    const { view: logView, group: logGroup, summary: logSummary } = buildLogSection();
    const imported = el('div');
    await renderImported(imported);
    // The two sections are tabs (v0.103.1), like on Activity and Releases: [Log (n)] [Imported (n)].
    logGroup.open = true;
    logGroup.classList.add('srw-panel-flat');
    const tabbed = el('div', { className: 'srw-history-tabbed' });
    tabbed.appendChild(window.SRW.buildPaneTabs([
      { key: 'log', label: () => window.SRW.sectionTabLabel(logGroup, 'Log'), pane: logGroup, watch: logSummary },
      { key: 'imported', label: () => window.SRW.sectionTabLabel(imported, 'Imported'), pane: imported, watch: imported },
    ], 'srw:history-section'));
    tabbed.append(logGroup, imported);
    container.appendChild(tabbed);

    let reloadTimer = null;
    const debounced = (fn) => { clearTimeout(reloadTimer); reloadTimer = setTimeout(fn, 500); };
    liveDetach = window.SRW.attachLive(hint, {
      onEvent: (msg) => { if (msg.type === 'history') debounced(() => logView.reload()); },
      onChange: () => renderImported(imported),
      onResync: () => { logView.reload(); renderImported(imported); },
      poll: () => logView.reload(),
      pollMs: 15000,
      hint,
    });
  }

  function buildLogSection() {
    const { el } = window.SRW;
    const extra = { level: '', kind: '', category: '' };
    const summary = el('summary', { textContent: 'Log — …' });
    const group = el('details', { className: 'srw-date-group' }, [summary]);
    window.SRW.rememberOpen(group, 'history:log', true);

    const levelSelect = select([['', 'All levels'], ['error', 'Errors'], ['warning', 'Warnings'], ['info', 'Info']]);
    const kindSelect = select([['', 'Scenes + system'], ['history', 'Scenes only'], ['event', 'System only']]);
    const categorySelect = select([['', 'All categories']]);
    const knownCategories = new Set();
    const controls = el('div', { className: 'srw-toolbar' }, [levelSelect, kindSelect, categorySelect]);
    group.appendChild(controls);

    const view = window.SRW.buildFilteredPagedList({
      filters: ['search', 'dates'],
      perPage: 50,
      emptyText: 'Nothing logged yet.',
      fetchPage: async (f, page, perPage) => {
        const response = await window.SRW.runOp('history_list', {
          q: f.search.trim(), levels: extra.level, kinds: extra.kind, categories: extra.category,
          date_from: f.dateFrom, date_until: f.dateUntil, page, per_page: perPage,
        });
        if (response.error) return response;
        (response.categories || []).forEach((c) => {
          if (knownCategories.has(c)) return;
          knownCategories.add(c);
          categorySelect.appendChild(el('option', { value: c, textContent: c }));
        });
        return { total: response.total, items: response.rows };
      },
      renderItems: (items) => el('ul', { className: 'srw-release-list srw-history-list' }, items.map(renderEntry)),
      onTotal: (total, active) => { summary.textContent = `Log — ${total}${active || anyExtra() ? ' (filtered)' : ''}`; },
    });
    const anyExtra = () => Boolean(extra.level || extra.kind || extra.category);
    [[levelSelect, 'level'], [kindSelect, 'kind'], [categorySelect, 'category']].forEach(([node, key]) => {
      node.addEventListener('change', () => { extra[key] = node.value; view.reset(); });
    });
    group.appendChild(view.node);
    view.reset();
    return { view, group, summary };
  }

  function select(options) {
    const { el } = window.SRW;
    return el('select', { className: 'srw-filter-preset-select' },
      options.map(([value, label]) => el('option', { value, textContent: label })));
  }

  function localTime(at) {
    const d = new Date(`${(at || '').replace(' ', 'T')}Z`);
    return Number.isNaN(d.getTime()) ? (at || '') : d.toLocaleString();
  }

  function renderEntry(entry) {
    const { el, sceneUrl } = window.SRW;
    const parts = [
      el('span', { className: 'srw-release-date', textContent: localTime(entry.at) }),
      el('span', {
        className: `srw-history-level srw-history-level-${entry.level}`,
        textContent: LEVEL_LABEL[entry.level] || entry.level,
      }),
      el('span', {
        className: 'srw-history-category',
        textContent: `${KIND_LABEL[entry.kind] || entry.kind} · ${entry.category}`,
      }),
    ];
    if (entry.scene_id && entry.provider) {
      parts.push(el('a', {
        className: 'srw-release-title', href: sceneUrl(entry.provider, entry.scene_id),
        target: '_blank', rel: 'noopener noreferrer', textContent: entry.scene_title || entry.scene_id,
      }));
    } else if (entry.scene_title) {
      parts.push(el('span', { className: 'srw-release-title', textContent: entry.scene_title }));
    }
    parts.push(el('span', { className: 'srw-history-message', textContent: entry.message }));
    const li = el('li', { className: 'srw-release-item srw-history-entry' }, parts);
    if (entry.details && (typeof entry.details !== 'object' || Object.keys(entry.details).length)) {
      li.appendChild(el('details', { className: 'srw-history-details' }, [
        el('summary', { textContent: 'Details' }),
        el('pre', {
          textContent: typeof entry.details === 'string' ? entry.details : JSON.stringify(entry.details, null, 2),
        }),
      ]));
    }
    return li;
  }

  // "Imported" -- the permanent list, moved from the Activity page
  // (2026-09-28); same section builder as there.
  async function renderImported(slot) {
    const response = await window.SRW.runOp('releases_query', { statuses: ['imported'], per_page: 0 });
    if (response.error) {
      slot.replaceChildren(window.SRW.el('p', { textContent: `Error: ${response.error}` }));
      return;
    }
    // Movies of the Movies wishlist that were imported (v0.128.2) sit in the same list, newest first.
    const films = await window.SRW.runOp('films_imported').catch(() => ({ rows: [] }));
    const section = await window.SRW_Activity.buildSection('Imported', window.SRW.groupByScene(response.rows), {
      emptyText: 'Nothing imported yet.', sortBy: 'imported_at', collapsed: true, showThumb: false,
      rememberKey: 'history:Imported', openScene: true,
      movieRows: window.SRW_Activity.movieRows((films && films.rows) || [], new Map(), slot, { thumb: false }), // text only, like the scenes here
    });
    section.open = true;
    section.classList.add('srw-panel-flat');
    slot.replaceChildren(section);
  }

  window.SRW_History = { render };
})();
