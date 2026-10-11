/**
 * StashReleaseManager - Maintenance page
 * Reachable at /plugins/stash-release-manager/maintenance.
 *
 * Anchors scripts/audit_library_fingerprints.py +
 * scripts/fix_library_mismatches.py's retroactive-mismatch workflow
 * permanently in the plugin itself (2026-09-16 request), instead of
 * only being runnable by hand from the terminal. A "Library Check"
 * (full library, or scoped to one or more directories picked via
 * Stash's own real directory browser -- see buildDirectoryPicker()
 * below) checks every local scene carrying a StashDB stash_id against
 * StashDB's own registered fingerprints (api/library_check.py,
 * api/routers/maintenance.py). Confirmed mismatches (exactly one
 * alternative candidate) can be selected individually or all at once
 * and corrected in bulk, Scene-Tagger-style; ambiguous/unverifiable
 * findings are shown for information only, same "never guess" rule
 * the CLI scripts follow.
 */
(function () {
  'use strict';

  let selectedIds = new Set();
  let pollTimer = null;

  // Maintenance areas (2026-10-04, UI review): every tool is its own page with its own address
  // (/plugins/<id>/maintenance/<key>), reached through the side navigation -- no more collapsed sections on one long page.
  const MAINTENANCE_PATH = '/plugins/stash-release-manager/maintenance';
  // Groups of the side menu (v0.153.0); the order here is the order in the menu. Keys and addresses are unchanged.
  const AREA_GROUPS = [
    { key: 'check', label: 'Check library' },
    { key: 'resolve', label: 'Resolve' },
    { key: 'files', label: 'Files & quality' },
    { key: 'movies', label: 'Movies' },
  ];
  const AREAS = [
    { key: 'library-check', group: 'check', icon: 'faClipboardCheck', label: 'Library check', build: (c) => renderLibraryCheck(c) },
    { key: 'performers', group: 'check', icon: 'faUserSlash', label: 'Performers without scenes', build: (c) => c.appendChild(buildPerformersArea()) },
    { key: 'unidentified', group: 'check', icon: 'faCircleQuestion', label: 'Unidentified scenes', build: (c) => c.appendChild(buildUnidentifiedArea()) },
    { key: 'vr-check', group: 'check', icon: 'faVrCardboard', label: 'VR: 2D / mono files', build: (c) => c.appendChild(flatten(buildVrCheckSection())) },
    { key: 'unknown-quality', group: 'check', icon: 'faQuestion', label: 'Unknown quality', build: (c) => { c.appendChild(window.SRW.el('p', { className: 'srw-hint', textContent: unknownQualityReason })); c.appendChild(buildUnknownQualitySection()); } },
    { key: 'review', group: 'resolve', icon: 'faFingerprint', label: 'Review: Identify Scene', build: (c) => buildReviewArea(c) },
    { key: 'compare', group: 'resolve', icon: 'faCodeCompare', label: 'Compare scenes', build: (c) => { c.appendChild(buildSenseNotice()); c.appendChild(flatten(buildCompareSection('compare'))); } },
    { key: 'import', group: 'files', icon: 'faFileImport', label: 'Import folder', build: (c) => { c.appendChild(window.SRW.buildSafetyPanel({ compact: true })); c.appendChild(buildImportSection()); } },
    { key: 'organize', group: 'files', icon: 'faFolderTree', label: 'Organize library', build: (c) => { c.appendChild(window.SRW.buildSafetyPanel({ compact: true })); c.appendChild(buildOrganizeSection()); } },
    { key: 'upgrades', group: 'files', icon: 'faCircleUp', label: 'Upgrade wanted', build: (c) => c.appendChild(flatten(buildUpgradeSection())) },
    { key: 'movies', group: 'movies', icon: 'faFilm', label: 'Movies & groups', build: (c) => c.appendChild(buildMoviesSection()) },
  ];

  // A tool that used to be a collapsible <details> panel is shown open and without the fold-away behaviour.
  function flatten(node) {
    if (node && node.tagName === 'DETAILS') {
      node.open = true;
      node.classList.add('srw-panel-flat');
    }
    return node;
  }

  let visibleAreas = AREAS;
  // "Unknown quality" (v0.106.2) only matters where Whisparr names/imports files itself (preset full_whisparr -- every other preset
  // imports with this sidecar's own naming, which no longer writes "Unknown"), or when such files are still in the library.
  let unknownQualityReason = '';

  // The area named in the address; without one the first area where something waits for a decision (Review, then Compare), else the first.
  let defaultAreaKey = null;
  function areaFromRoute() {
    const m = window.location.pathname.match(/\/maintenance\/([^/]+)/);
    const found = m && visibleAreas.find((a) => a.key === m[1]);
    return found ? found.key : (defaultAreaKey || visibleAreas[0].key);
  }

  // Face recognition is NOT built in (v0.172.0): the separate Stash-Sense 2 plugin does it on Stash's own scene page, and a call from here
  // could break with any update of that plugin. This plugin only points to it.
  const FACE_HINT = 'Face recognition: run Stash-Sense 2 on the scene page in Stash.';
  function buildFaceHint(s) {
    const { el } = window.SRW;
    return el('div', { className: 'srw-hint srw-face-hint' }, [
      `${FACE_HINT} `,
      el('a', { href: `/scenes/${s.id}`, target: '_blank', rel: 'noopener noreferrer', textContent: `Open scene #${s.id} in Stash ↗` }),
    ]);
  }

  // Notice at the top of Review / Compare.
  function buildSenseNotice() {
    const { el } = window.SRW;
    return el('div', { className: 'srw-sense-notice' }, [
      el('strong', { textContent: 'Face recognition by Stash-Sense 2. ' }),
      'This plugin does not call it. To recognise the performers in a scene, open the scene in Stash (the link under each player) and run Stash-Sense 2 there '
        + '(it is a separate plugin with its own authors; it has to be installed and enabled in Stash).',
    ]);
  }

  let contentScope = 'both'; // Settings > Content, set by render() before any area is built
  function buildReviewArea(content) {
    content.appendChild(buildSenseNotice());
    content.appendChild(flatten(buildCompareSection('review')));
  }

  async function render(container) {
    const { el } = window.SRW;
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    container.innerHTML = '';
    container.appendChild(window.SRW.buildPageHeader('maintenance'));

    unknownQualityReason = '';
    try {
      const settings = (await window.SRW.runOp('settings_get_all')) || {};
      if (settings.whisparr_scope_preset === 'full_whisparr') {
        unknownQualityReason = 'Shown because Whisparr imports files itself (Settings > Whisparr scope: full Whisparr management) -- its naming can write "Unknown".';
      } else {
        const found = (await window.SRW.runOp('unknown_quality_list')) || {};
        if ((found.items || []).length) unknownQualityReason = `Shown because ${found.items.length} file(s) with "Unknown" in the name are still in the library.`;
      }
    } catch (e) { /* hidden */ }
    // Settings > Content: the areas of the switched-off kind are hidden (Review, Import folder and Organize library serve both).
    const SCENE_AREAS = ['library-check', 'compare', 'unknown-quality', 'upgrades', 'vr-check'];
    const scope = await window.SRW.getContentScope();
    contentScope = scope;
    visibleAreas = AREAS.filter((a) => (a.key !== 'unknown-quality' || unknownQualityReason)
      && !(scope === 'movies' && SCENE_AREAS.includes(a.key)) && !(scope === 'scenes' && a.key === 'movies'));

    // What waits for a decision (one cheap local request): numbers behind Review, Compare and Upgrade wanted, and the area opened when the
    // address names none -- the first of Review, Compare that has something open.
    const countFor = (counts) => ({ review: counts && counts.review_open, compare: counts && counts.pairs_open, upgrades: counts && counts.upgrades_open });
    let badges = countFor(null);
    try { badges = countFor(await window.SRW.runOp('maintenance_counts')); } catch (e) { /* no badges */ }
    defaultAreaKey = ['review', 'compare'].find((k) => badges[k] > 0 && visibleAreas.some((a) => a.key === k)) || null;

    const navItems = [];
    AREA_GROUPS.forEach((g) => {
      const inGroup = visibleAreas.filter((a) => a.group === g.key);
      if (!inGroup.length) return;
      navItems.push({ heading: g.label });
      inGroup.forEach((a) => navItems.push({ key: a.key, icon: a.icon, label: a.label, href: `${MAINTENANCE_PATH}/${a.key}`, badge: badges[a.key] }));
    });
    const content = el('div', { className: 'srw-maintenance-area' });
    let current = null;
    const side = window.SRW.buildSideNav(
      navItems,
      areaFromRoute(),
      (key) => {
        // Own address per area, drawn here without rebuilding the whole page (the watcher is told about the new address).
        try { history.pushState(null, '', `${MAINTENANCE_PATH}/${key}`); } catch (e) { /* ignore */ }
        window.SRW.acknowledgeUrl();
        showArea(key);
      },
    );
    container.appendChild(side.layout(content));

    function showArea(key) {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      const area = visibleAreas.find((a) => a.key === key) || visibleAreas[0];
      current = area.key;
      side.setActive(area.key);
      content.innerHTML = '';
      area.build(content);
      refreshBadges();
    }
    // The numbers change while the areas are worked through: asked again whenever an area is opened.
    async function refreshBadges() {
      try {
        const fresh = countFor(await window.SRW.runOp('maintenance_counts'));
        ['review', 'compare', 'upgrades'].forEach((k) => side.setBadge(k, fresh[k]));
      } catch (e) { /* keep the old numbers */ }
    }
    const start = areaFromRoute();
    // An address of an area that is not offered (e.g. a scene area while only movies are on) is corrected to the area shown.
    const asked = (window.location.pathname.match(/\/maintenance\/([^/]+)/) || [])[1];
    if (asked && asked !== start) {
      try { history.replaceState(null, '', `${MAINTENANCE_PATH}/${start}`); } catch (e) { /* ignore */ }
      window.SRW.acknowledgeUrl();
    }
    showArea(current || start);
  }

  async function renderLibraryCheck(content) {
    const { el } = window.SRW;
    content.appendChild(el('h3', { textContent: 'Library check' }));
    content.appendChild(el('p', {
      className: 'srw-hint',
      textContent: 'Checks the existing library against StashDB’s own registered fingerprints '
        + '(independent of anything this project itself has handed off to Whisparr) -- finds a local '
        + 'scene pointing at the wrong StashDB scene id, and identifies what it actually is.',
    }));

    const startBtn = el('button', { className: 'srw-btn srw-btn-primary', textContent: 'Start Library Check' });
    const progressWrap = el('div', { className: 'srw-maintenance-progress' });
    const resultsWrap = el('div', { className: 'srw-maintenance-results' });

    const picker = buildDirectoryPicker();
    content.appendChild(picker.el);
    content.appendChild(el('div', { className: 'srw-toolbar' }, [startBtn]));
    content.appendChild(progressWrap);
    content.appendChild(resultsWrap);

    startBtn.addEventListener('click', async () => {
      const scopedPaths = picker.getScopePaths();
      startBtn.disabled = true;
      resultsWrap.innerHTML = '';
      try {
        const started = await window.SRW.runOp('maintenance_check_run', { paths: scopedPaths });
        if (started.skipped) {
          progressWrap.innerHTML = '';
          progressWrap.appendChild(el('span', { className: 'srw-hint', textContent: started.reason || 'A check is already running.' }));
          pollStatus(progressWrap, resultsWrap, startBtn);
          return;
        }
      } catch (e) {
        progressWrap.innerHTML = '';
        progressWrap.appendChild(el('span', { className: 'srw-release-mismatch-warning', textContent: `Failed to start: ${e.message}` }));
        startBtn.disabled = false;
        return;
      }
      pollStatus(progressWrap, resultsWrap, startBtn);
    });

    // Also reflect an already-running or already-finished check on
    // plain page load, not only right after clicking Start -- e.g.
    // reopening this page after navigating away mid-run.
    let status;
    try {
      status = await window.SRW.runOp('maintenance_check_status');
    } catch (e) {
      return;
    }
    if (status.phase === 'running') {
      startBtn.disabled = true;
      pollStatus(progressWrap, resultsWrap, startBtn);
    } else if (status.phase === 'done' || status.phase === 'failed') {
      renderProgress(progressWrap, status);
      const counts = await renderFindings(resultsWrap);
      renderProgress(progressWrap, status, counts);
    }
  }

  // Mirrors Stash's own native "Select folders" dialog (the one
  // behind Scan/Clean/Generate's "Selective..." buttons) as closely
  // as possible, using the exact same server-side directory browse
  // (stash_client.browse_directory(), confirmed live 2026-09-16 via
  // schema introspection to be the real `directory(path)` query Stash
  // itself uses) -- replaces an earlier flat "all 7 configured
  // top-level paths, checked by default" checkbox design that
  // couldn't pick a subdirectory at all.
  //
  // Interaction, same as Stash's own dialog: a "File path" box shows
  // the currently browsed directory; clicking a subdirectory row
  // navigates into it; "Up a directory" goes to its parent; "+ Add"
  // commits the CURRENTLY BROWSED path into a selected-paths list
  // (shown as removable chips) -- distinct from "the path you're
  // looking at right now". Quick-jump buttons for Stash's own
  // configured top-level library paths sit above the browser so
  // starting from e.g. /data/Scenes doesn't require manually
  // navigating there from the filesystem root every time.
  function buildDirectoryPicker() {
    const { el } = window.SRW;
    let mode = 'full'; // 'full' | 'selective'
    let selectedPaths = [];
    let libraryPaths = [];
    let browsePath = null;
    let browseParent = null;
    let browseDirs = [];

    const fullRadio = el('input', { type: 'radio', name: 'srw-maint-scope' });
    fullRadio.checked = true;
    const selectiveRadio = el('input', { type: 'radio', name: 'srw-maint-scope' });

    const shortcutsWrap = el('div', { className: 'srw-maintenance-shortcuts' });
    const chipsWrap = el('div', { className: 'srw-maintenance-chips' });
    const browserWrap = el('div', { className: 'srw-maintenance-browser' });
    const selectiveDetail = el('div', { className: 'srw-maintenance-selective', hidden: true }, [
      shortcutsWrap, chipsWrap, browserWrap,
    ]);

    const wrap = el('div', { className: 'srw-maintenance-paths' }, [
      el('div', { className: 'srw-hint' }, ['Library scope:']),
      el('label', { className: 'srw-maintenance-path-label' }, [fullRadio, ' Full library']),
      el('label', { className: 'srw-maintenance-path-label' }, [selectiveRadio, ' Select directories…']),
      selectiveDetail,
    ]);

    function setMode(next) {
      mode = next;
      selectiveDetail.hidden = mode !== 'selective';
    }
    fullRadio.addEventListener('change', () => { if (fullRadio.checked) setMode('full'); });
    selectiveRadio.addEventListener('change', () => { if (selectiveRadio.checked) setMode('selective'); });

    function renderChips() {
      chipsWrap.innerHTML = '';
      selectedPaths.forEach((p) => {
        const removeBtn = el('button', { className: 'srw-chip-remove', textContent: '×', title: 'Remove' });
        removeBtn.addEventListener('click', () => {
          selectedPaths = selectedPaths.filter((x) => x !== p);
          renderChips();
        });
        chipsWrap.appendChild(el('span', { className: 'srw-chip' }, [p, removeBtn]));
      });
    }

    async function browseTo(path) {
      browserWrap.innerHTML = '';
      browserWrap.appendChild(el('span', { className: 'srw-hint', textContent: 'Loading…' }));
      try {
        const res = await window.SRW.runOp('maintenance_directory_browse', path ? { path } : {});
        browsePath = res.path;
        browseParent = res.parent;
        browseDirs = res.directories || [];
      } catch (e) {
        browserWrap.innerHTML = '';
        browserWrap.appendChild(el('span', { className: 'srw-release-mismatch-warning', textContent: `Failed to browse: ${e.message}` }));
        return;
      }
      renderBrowser();
    }

    function renderBrowser() {
      browserWrap.innerHTML = '';
      const pathBox = el('input', { type: 'text' });
      pathBox.value = browsePath || '';
      pathBox.readOnly = true;
      const addBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: '+ Add' });
      addBtn.addEventListener('click', () => {
        if (browsePath && !selectedPaths.includes(browsePath)) {
          selectedPaths = [...selectedPaths, browsePath];
          renderChips();
        }
      });
      browserWrap.appendChild(el('div', { className: 'srw-maintenance-browse-row' }, [pathBox, addBtn]));

      const list = el('ul', { className: 'srw-maintenance-browse-list' });
      if (browseParent) {
        const upBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: '↑ Up a directory' });
        upBtn.addEventListener('click', () => browseTo(browseParent));
        list.appendChild(el('li', {}, [upBtn]));
      }
      browseDirs.forEach((d) => {
        const name = d.split('/').filter(Boolean).pop() || d;
        const dirBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: `📁 ${name}` });
        dirBtn.addEventListener('click', () => browseTo(d));
        list.appendChild(el('li', {}, [dirBtn]));
      });
      browserWrap.appendChild(list);
    }

    (async () => {
      try {
        const res = await window.SRW.runOp('maintenance_library_paths');
        libraryPaths = res.paths || [];
      } catch (e) {
        libraryPaths = [];
      }
      shortcutsWrap.appendChild(el('span', { className: 'srw-hint', textContent: 'Jump to: ' }));
      libraryPaths.forEach((p) => {
        const btn = el('button', { className: 'srw-btn srw-btn-sm', textContent: p });
        btn.addEventListener('click', () => browseTo(p));
        shortcutsWrap.appendChild(btn);
      });
      await browseTo(libraryPaths[0] || null);
    })();

    return {
      el: wrap,
      getScopePaths: () => (mode === 'full' ? [] : selectedPaths),
    };
  }

  // `pendingCounts` (optional {mismatchCount, reviewCount}) overrides
  // status.mismatch_count/unverifiable_count once known -- those two
  // status fields are just a tally of what THIS run itself found, but
  // the findings list below is the true CURRENT pending backlog across
  // every run ever (older findings from a differently-scoped run stay
  // pending until resolved, see library_check_repo.insert_finding()'s
  // per-scene dedup). Showing the run-scoped number here while the box
  // below shows the true total was confusing (user report 2026-09-16:
  // "unter der Progressbar steht 2 need review und in dem Kasten
  // Needs manual review steht 3") -- `match_count` alone stays
  // run-scoped, worded as such, since there's no comparable persisted
  // "matches" list anywhere else to disagree with it.
  function renderProgress(progressWrap, status, pendingCounts) {
    const { el } = window.SRW;
    progressWrap.innerHTML = '';
    if (!status.phase || status.phase === 'idle') return;
    const pct = status.total ? Math.round((status.current / status.total) * 100) : 0;
    let label;
    if (status.phase === 'running') {
      label = `Checking... ${status.current}/${status.total}`;
    } else if (status.phase === 'failed') {
      label = `Failed: ${status.error || 'unknown error'}`;
    } else {
      const mismatchCount = pendingCounts ? pendingCounts.mismatchCount : status.mismatch_count;
      const reviewCount = pendingCounts ? pendingCounts.reviewCount : status.unverifiable_count;
      label = `Done: ${status.match_count} match (this run). Pending: ${mismatchCount} mismatch, `
        + `${reviewCount} need review`;
    }
    progressWrap.appendChild(el('div', { className: 'srw-progress-track' }, [
      el('div', { className: 'srw-progress-fill', style: `width: ${pct}%` }),
    ]));
    progressWrap.appendChild(el('div', { className: 'srw-hint', textContent: label }));
  }

  function pollStatus(progressWrap, resultsWrap, startBtn) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      let status;
      try {
        status = await window.SRW.runOp('maintenance_check_status');
      } catch (e) {
        return;
      }
      renderProgress(progressWrap, status);
      if (status.phase !== 'running') {
        clearInterval(pollTimer);
        pollTimer = null;
        startBtn.disabled = false;
        const counts = await renderFindings(resultsWrap);
        renderProgress(progressWrap, status, counts);
      }
    }, 2000);
  }

  // Returns {mismatchCount, reviewCount} -- the TRUE current pending
  // totals (see renderProgress()'s docstring above), so the caller can
  // reconcile the progress summary with what this function just
  // rendered.
  async function renderFindings(resultsWrap) {
    const { el } = window.SRW;
    resultsWrap.innerHTML = '';
    selectedIds = new Set();

    let mismatches = [];
    let others = [];
    try {
      const [mRes, aRes, uRes] = await Promise.all([
        window.SRW.runOp('maintenance_check_findings', { status: 'pending', kind: 'mismatch' }),
        window.SRW.runOp('maintenance_check_findings', { status: 'pending', kind: 'ambiguous' }),
        window.SRW.runOp('maintenance_check_findings', { status: 'pending', kind: 'unverifiable' }),
      ]);
      mismatches = mRes.findings || [];
      others = [...(aRes.findings || []), ...(uRes.findings || [])];
    } catch (e) {
      resultsWrap.appendChild(el('span', { className: 'srw-release-mismatch-warning', textContent: `Failed to load findings: ${e.message}` }));
      return { mismatchCount: 0, reviewCount: 0 };
    }

    if (!mismatches.length && !others.length) {
      resultsWrap.appendChild(el('p', { className: 'srw-hint', textContent: 'No issues found.' }));
      return { mismatchCount: 0, reviewCount: 0 };
    }

    if (mismatches.length) {
      resultsWrap.appendChild(buildMismatchSection(mismatches, resultsWrap));
    }
    if (others.length) {
      resultsWrap.appendChild(buildInfoSection(others, resultsWrap));
    }
    return { mismatchCount: mismatches.length, reviewCount: others.length };
  }

  function buildMismatchSection(mismatches, resultsWrap) {
    const { el } = window.SRW;
    // "Select all" selects the findings of the CURRENT PAGE only (v0.157.0); when more match the search, a button selects all of them on purpose.
    const selectAll = el('input', { type: 'checkbox', title: 'Select every finding on this page' });
    const selectMore = el('button', { type: 'button', className: 'srw-btn' });
    selectMore.hidden = true;
    let pageFindings = [];
    let matchingFindings = [];
    const syncSelectAll = () => {
      selectAll.checked = pageFindings.length > 0 && pageFindings.every((f) => selectedIds.has(f.id));
      const rest = matchingFindings.filter((f) => !selectedIds.has(f.id)).length;
      selectMore.hidden = !(selectAll.checked && rest > 0);
      selectMore.textContent = `Select all ${matchingFindings.length} matching`;
    };
    const applyBtn = el('button', { className: 'srw-btn srw-btn-danger', textContent: 'Apply selected (0)' });
    applyBtn.disabled = true;

    const updateApplyBtn = () => {
      applyBtn.textContent = `Apply selected (${selectedIds.size})`;
      applyBtn.disabled = selectedIds.size === 0;
      syncSelectAll();
    };

    // Paged + searchable since 2026-09-26; the selection (selectedIds) lives across pages.
    let rowCheckboxes = [];
    const buildRow = (f) => {
      const cb = el('input', { type: 'checkbox' });
      cb.checked = selectedIds.has(f.id);
      rowCheckboxes.push(cb);
      cb.addEventListener('change', () => {
        if (cb.checked) selectedIds.add(f.id); else selectedIds.delete(f.id);
        updateApplyBtn();
      });
      const candidateTitle = (f.candidates[0] && f.candidates[0].title) || '?';
      const dismissBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Dismiss' });
      dismissBtn.addEventListener('click', async () => {
        dismissBtn.disabled = true;
        try {
          const res = await window.SRW.runOp('maintenance_check_dismiss', { finding_id: f.id });
          if (res && res.error) throw new Error(res.error);
          window.SRW.dismissedNotice(`Dismissed: ${f.scene_title || 'finding'}`, '',
            () => window.SRW.runOp('maintenance_check_restore', { finding_id: f.id, status: res.previous || 'pending' }),
            () => renderFindings(resultsWrap));
          await renderFindings(resultsWrap);
        } catch (e) {
          window.SRW.notify('Dismiss', `Dismiss failed: ${e.message}`, 'error');
          dismissBtn.disabled = false;
        }
      });
      return el('li', { className: 'srw-release-item' }, [
        cb,
        el('span', { textContent: `${f.scene_title} → ${candidateTitle}` }),
        f.scene_id ? window.SRW.buildStashLink('scene', f.scene_id) : null,
        dismissBtn,
      ]);
    };
    const view = window.SRW.buildFilteredPagedList({
      filters: ['search'],
      perPage: 50,
      showFilterBarFrom: 10,
      fetchPage: async (f, page, perPage) => {
        const fetcher = window.SRW.localPageFetcher(() => mismatches, {
          text: (x) => `${x.scene_title} ${(x.candidates[0] && x.candidates[0].title) || ''}`,
        });
        const shown = await fetcher(f, page, perPage);
        pageFindings = shown.items || [];
        matchingFindings = (await fetcher(f, 1, 1000000)).items || []; // local data, no request
        syncSelectAll();
        return shown;
      },
      renderItems: (items) => {
        rowCheckboxes = [];
        return el('ul', { className: 'srw-release-list' }, items.map(buildRow));
      },
    });
    view.reset();

    selectAll.addEventListener('change', () => {
      pageFindings.forEach((f) => { if (selectAll.checked) selectedIds.add(f.id); else selectedIds.delete(f.id); });
      rowCheckboxes.forEach((cb) => { cb.checked = selectAll.checked; });
      updateApplyBtn();
    });
    selectMore.addEventListener('click', () => {
      matchingFindings.forEach((f) => selectedIds.add(f.id));
      updateApplyBtn();
      view.reload();
    });

    applyBtn.addEventListener('click', () => runApplySelected(mismatches, applyBtn, resultsWrap));

    return el('div', { className: 'srw-maintenance-mismatches' }, [
      el('h3', { textContent: `Mismatches (${mismatches.length})` }),
      el('div', { className: 'srw-toolbar' }, [
        el('label', {}, [selectAll, ' Select all on this page']),
        selectMore,
        applyBtn,
      ]),
      view.node,
    ]);
  }

  function runApplySelected(mismatches, applyBtn, resultsWrap) {
    const ids = Array.from(selectedIds);
    window.SRW.confirmInline(applyBtn,
      `Correct ${ids.length} scene(s)? Each one's title/date/details/studio/performers/tags and StashDB id `
      + `will be overwritten with the matched scene's own data. This only ever changes local Stash metadata `
      + `-- never a file on disk or Whisparr.`,
      async () => {
        // confirmInline hides applyBtn on Yes -- undone immediately
        // since it's also this loop's own progress indicator (its
        // textContent), same reasoning as entity-panel.js's
        // runBulkHandoff().
        applyBtn.style.display = '';
        applyBtn.disabled = true;
        let succeeded = 0;
        const failures = [];
        for (let i = 0; i < ids.length; i++) {
          const id = ids[i];
          const finding = mismatches.find((m) => m.id === id);
          const label = finding ? finding.scene_title : id;
          applyBtn.textContent = `Correcting... (${i + 1}/${ids.length})`;
          try {
            const result = await window.SRW.runOp('maintenance_check_apply', { finding_id: id });
            if (result.error) failures.push(`${label}: ${result.error}`);
            else succeeded += 1;
          } catch (e) {
            failures.push(`${label}: ${e.message}`);
          }
        }
        if (failures.length) {
          window.SRW.notify('Library check', `Corrected ${succeeded} of ${ids.length} scene(s). ${failures.length} failed:\n\n${failures.join('\n')}`, 'warning');
        }
        await renderFindings(resultsWrap);
      },
      { danger: true },
    );
  }

  // Ambiguous (2+ StashDB candidates) and unverifiable (no fingerprint
  // data at all) findings -- informational only, no correction is
  // safe to automate for either (see api/library_check.py's
  // find_library_mismatches() docstring). Only action offered is
  // Dismiss, to declutter the list once reviewed. Shows each file's
  // own local path(s) (2026-09-16 request) so they can be checked
  // directly on disk.
  function buildInfoSection(others, resultsWrap) {
    const { el } = window.SRW;
    const buildItem = (f) => {
      const detail = f.kind === 'ambiguous'
        ? `candidates: ${(f.candidates || []).map((c) => c.title).join(', ')}`
        : (f.reason || '');
      const dismissBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Dismiss' });
      dismissBtn.addEventListener('click', async () => {
        dismissBtn.disabled = true;
        try {
          const res = await window.SRW.runOp('maintenance_check_dismiss', { finding_id: f.id });
          if (res && res.error) throw new Error(res.error);
          window.SRW.dismissedNotice(`Dismissed: ${f.scene_title || 'finding'}`, '',
            () => window.SRW.runOp('maintenance_check_restore', { finding_id: f.id, status: res.previous || 'pending' }),
            () => renderFindings(resultsWrap));
          await renderFindings(resultsWrap);
        } catch (e) {
          window.SRW.notify('Dismiss', `Dismiss failed: ${e.message}`, 'error');
          dismissBtn.disabled = false;
        }
      });
      const pathLines = (f.file_paths || []).map((p) => el('div', { className: 'srw-maintenance-file-path', textContent: p }));
      return el('li', { className: 'srw-release-item' }, [
        el('div', {}, [
          el('span', { textContent: `[${f.kind}] ${f.scene_title} -- ${detail}` }),
          ...pathLines,
        ]),
        f.scene_id ? window.SRW.buildStashLink('scene', f.scene_id) : null,
        dismissBtn,
      ]);
    };
    const view = window.SRW.buildFilteredPagedList({
      filters: ['search'],
      perPage: 50,
      showFilterBarFrom: 10,
      fetchPage: window.SRW.localPageFetcher(() => others, {
        text: (f) => `${f.kind} ${f.scene_title} ${(f.file_paths || []).join(' ')} ${f.reason || ''}`,
      }),
      renderItems: (items) => el('ul', { className: 'srw-release-list' }, items.map(buildItem)),
    });
    view.reset();

    return window.SRW.rememberOpen(el('details', { className: 'srw-panel' }, [
      el('summary', { textContent: `Needs manual review (${others.length})` }),
      view.node,
    ]), 'maintenance:needs-manual-review', false);
  }

  // ==================== VR check (2026-09-26, api/vr_check.py) ====================
  //
  // "VR scene, but the file is 2D": compares the left/right (and top/
  // bottom) halves of three frames per file -- both eyes of a real VR file
  // show nearly the same picture. Quick = only files with an unusual
  // aspect ratio; Full = every VR-tagged file (cached per file, so a
  // repeat only analyses new/changed files).
  let vrPollTimer = null;

  function buildVrCheckSection() {
    const { el, confirmInline } = window.SRW;
    const status = el('span', { className: 'srw-key-status' });
    const list = el('div');
    const dismissedList = el('div');
    let findings = []; // declared before the tabs: their labels read the counts straight away
    let dismissed = [];
    const counts = el('span', { hidden: true }); // text changes -> the tab labels follow (buildPaneTabs `watch`)
    const quickBtn = el('button', {
      className: 'srw-btn srw-btn-primary', textContent: 'Quick check',
      title: 'Only VR files whose aspect ratio is neither ~2:1 nor ~1:1 (a few minutes)',
      onClick: () => start('quick'),
    });
    const fullBtn = el('button', {
      className: 'srw-btn', textContent: 'Full check',
      title: 'Every VR-tagged file (~1.7 s each, already checked files are skipped)',
      onClick: () => start('full'),
    });
    const section = el('details', { className: 'srw-settings-section srw-panel srw-vr-check' }, [
      el('summary', { textContent: 'VR scenes with a 2D or mono file' }),
      el('p', {
        className: 'srw-hint',
        textContent: 'Finds scenes tagged "Virtual Reality" whose file is actually flat (no left/right or top/bottom '
          + 'eye pair). Aspect ratio alone isn\'t enough -- "half SBS" VR files are 16:9 too -- so it compares the '
          + 'picture halves of three frames per file. For 2D/unclear files the local phash is also compared with '
          + 'the provider\'s fingerprints -- a match usually means an official 2D version of the scene. '
          + 'Monoscopic 360°/180° VR (single image, "VR mono") is listed as an upgrade candidate.',
      }),
      // What the pills lead to (v0.103.4): this page only LISTS findings; the search for a better file is a separate area.
      el('p', { className: 'srw-hint' }, [
        el('strong', { textContent: 'What happens with a finding: ' }),
        '“2D” and “VR mono” files become upgrade candidates in ',
        el('a', { href: '/plugins/stash-release-manager/maintenance/upgrades', textContent: 'Upgrade wanted' }),
        ' (searched there: Dry run / Search now). “Unclear” stays a finding for you to look at (Frame from file) -- there is no search for it. ',
        '“Dismiss” means: this file is fine -- a dismissed 2D file gets no upgrade search (it stays under “Dismissed”, with Restore).',
      ]),
      el('div', { className: 'srw-toolbar' }, [quickBtn, fullBtn, status]),
      window.SRW.buildPaneTabs([
        { key: 'findings', label: () => `Findings (${findings.length})`, pane: list, watch: counts },
        { key: 'dismissed', label: () => `Dismissed (${dismissed.length})`, pane: dismissedList, watch: counts },
      ], 'srw:vr-section'),
      list,
      dismissedList,
      counts,
    ]);

    async function start(mode) {
      quickBtn.disabled = true; fullBtn.disabled = true;
      const result = await window.SRW.runOp('vr_check_run', { mode });
      if (result.error) { status.textContent = ` error: ${result.error}`; }
      poll();
    }

    async function poll() {
      clearTimeout(vrPollTimer);
      const st = await window.SRW.runOp('vr_check_status');
      const job = (st && st.job) || {};
      quickBtn.disabled = !!job.running; fullBtn.disabled = !!job.running;
      if (job.running) {
        status.textContent = job.phase === 'checking'
          ? ` ${job.mode} check: ${job.current} / ${job.total}${job.title ? ` — ${job.title}` : ''}`
          : ' preparing…';
        vrPollTimer = setTimeout(poll, 3000);
      } else {
        const last = st && st.last_run;
        status.textContent = last
          ? ` last ${last.mode} check ${String(last.finished_at || '').replace('T', ' ').slice(0, 16)} UTC: `
            + `${last.files} file(s), ${last.analysed} newly analysed -- ${last.stereo} stereo, ${last.mono} 2D, `
            + `${last.vr_mono || 0} VR mono, ${last.unclear} unclear, ${last.error} error(s)`
          : ' not run yet';
      }
      await loadFindings();
    }

    let view = null;
    let dismissedView = null;
    async function loadFindings() {
      const [res, resDismissed] = await Promise.all([
        window.SRW.runOp('vr_check_findings', { status: 'pending' }),
        window.SRW.runOp('vr_check_findings', { status: 'dismissed' }),
      ]);
      findings = (res && res.findings) || [];
      dismissed = (resDismissed && resDismissed.findings) || [];
      counts.textContent = `${findings.length}/${dismissed.length}`;
      if (!dismissedView) {
        dismissedView = window.SRW.buildFilteredPagedList({
          filters: ['search'],
          perPage: 40,
          showFilterBarFrom: 10,
          emptyText: 'Nothing dismissed.',
          fetchPage: window.SRW.localPageFetcher(() => dismissed, {
            text: (f) => `${f.scene_title} ${f.file_path}`,
          }),
          renderItems: (items) => el('ul', { className: 'srw-release-list' }, items.map((f) => buildRow(f, true))),
        });
        dismissedList.appendChild(dismissedView.node);
      }
      dismissedView.reload();
      if (!view) {
        view = window.SRW.buildFilteredPagedList({
          filters: ['search'],
          perPage: 40,
          showFilterBarFrom: 10,
          emptyText: 'No 2D files found (among the files checked so far).',
          fetchPage: window.SRW.localPageFetcher(() => findings, {
            text: (f) => `${f.scene_title} ${f.file_path}`,
          }),
          renderItems: (items) => el('ul', { className: 'srw-release-list' }, items.map((f) => buildRow(f))),
        });
        list.appendChild(view.node);
      }
      view.reload();
    }

    function buildRow(f, isDismissed = false) {
      const verdict = f.verdict === 'mono' ? '2D' : (f.verdict === 'vr_mono' ? 'VR mono' : 'unclear');
      // phash (checked live 2026-09-26): most flat files DO match a provider
      // fingerprint -- the providers also list the studios' official 2D
      // versions -- so a match means "known 2D version of this scene",
      // not "correct VR file"; NO match hints at an unknown re-encode.
      const phash = f.phash_match === 1 ? 'phash matches a provider fingerprint (likely an official 2D version)'
        : (f.phash_match === 0 ? 'phash matches NO provider fingerprint (unknown encode)' : 'phash not compared');
      const scores = `L/R ${f.lr_score != null ? f.lr_score.toFixed(2) : '?'}, T/B ${f.tb_score != null ? f.tb_score.toFixed(2) : '?'}`;
      const remove = () => {
        const at = findings.findIndex((x) => x.id === f.id);
        findings = findings.filter((x) => x.id !== f.id);
        view.reload();
        return at;
      };
      const dismissBtn = el('button', {
        className: 'srw-btn srw-btn-sm', textContent: 'Dismiss', title: 'The file is fine -- don\'t show it again (a 2D file then gets no upgrade search)',
        onClick: async () => {
          const r = await window.SRW.runOp('vr_check_dismiss', { finding_id: f.id });
          if (r.error) { window.SRW.notify('VR check', r.error, 'error'); return; }
          window.SRW.dismissedNotice(`Dismissed: ${f.scene_title || 'VR finding'}`, 'Find it again under “Dismissed”.',
            () => window.SRW.runOp('vr_check_restore', { finding_id: f.id, status: r.previous || 'pending' }),
            () => loadFindings());
          await loadFindings();
        },
      });
      // Stash's screenshot is often the scene's cover, not a frame of this
      // file -- this grabs a real frame from the middle of the file.
      const frameBtn = el('button', {
        className: 'srw-btn srw-btn-sm', textContent: 'Frame from file',
        title: 'Show a frame from the middle of this file (two eyes side by side = VR)',
        onClick: async () => {
          frameBtn.disabled = true;
          frameBtn.textContent = 'Loading…';
          const r = await window.SRW.runOp('vr_check_frame', { finding_id: f.id });
          frameBtn.disabled = false;
          frameBtn.textContent = 'Frame from file';
          if (r.error) { window.SRW.notify('Frame from file', r.error, 'error'); return; }
          thumb.querySelector('img').src = r.data_url;
          thumb.querySelector('img').classList.add('srw-vr-thumb-frame');
          thumb.title = 'Frame from the file -- click to open the scene in Stash';
        },
      });
      // Own verdict (v0.105.0): the check can be wrong (a 360° mono file judged "2D"). Kept over re-checks of the same file.
      const verdictBtns = [['mono', '2D'], ['vr_mono', 'VR mono'], ['unclear', 'Unclear']].map(([value, label]) => el('button', {
        className: `srw-btn srw-btn-sm${f.verdict === value ? ' srw-btn-primary' : ''}`, textContent: label,
        title: f.verdict === value ? `Currently: ${label}${f.verdict_manual ? ' (set by you)' : ''}` : `This file is: ${label}`,
        ...(f.verdict === value ? { disabled: true } : {}),
        onClick: async () => {
          const r = await window.SRW.runOp('vr_check_set_verdict', { finding_id: f.id, verdict: value });
          if (r.error) { window.SRW.notify('VR check', r.error, 'error'); return; }
          const prev = r.previous || {};
          f.verdict = value; f.verdict_manual = 1; view.reload();
          window.SRW.dismissedNotice(`Marked as ${label}: ${f.scene_title || 'file'}`,
            'Kept when the check runs again. “2D” and “VR mono” show up in Upgrade wanted at its next refresh.',
            () => window.SRW.runOp('vr_check_restore_verdict', { finding_id: f.id, verdict: prev.verdict, manual: !!prev.verdict_manual,
              finding_status: prev.finding_status || 'pending' }),
            () => { f.verdict = prev.verdict; f.verdict_manual = prev.verdict_manual; view.reload(); });
        },
      }));
      const identifyBtn = el('button', {
        className: 'srw-btn srw-btn-sm', textContent: 'Identify Scene',
        title: 'The file is not the scene it is filed under: send this scene to Review > Identify Scene',
        onClick: async () => {
          const r = await window.SRW.runOp('compare_review_add', { scene_id: f.scene_id });
          if (r.error) { window.SRW.notify('Identify Scene', r.error, 'error'); return; }
          if (r.already) { window.SRW.notify('Identify Scene', 'The scene is already in the Review list.', 'info'); return; }
          window.SRW.dismissedNotice(`Sent to Identify Scene: ${f.scene_title || 'scene'}`, 'Tag “Review: Identify Scene” added (nothing else changed).',
            () => window.SRW.runOp('compare_review_done', { scene_id: f.scene_id }), null);
        },
      });
      const restoreBtn = el('button', {
        className: 'srw-btn srw-btn-sm srw-btn-primary', textContent: 'Restore',
        title: 'Back to the findings (the file is judged as a finding again)',
        onClick: async () => {
          const r = await window.SRW.runOp('vr_check_restore', { finding_id: f.id, status: 'pending' });
          if (r.error) { window.SRW.notify('Restore', r.error, 'error'); return; }
          window.SRW.notify('Restore', `“${f.scene_title || 'file'}” is back under Findings.`, 'success');
          await loadFindings();
        },
      });
      const actions = isDismissed ? [restoreBtn, identifyBtn, frameBtn] : [...verdictBtns, identifyBtn, frameBtn, dismissBtn];
      if (!isDismissed && f.sidecar_release) {
        actions.unshift(el('button', {
          className: 'srw-btn srw-btn-sm srw-btn-danger', textContent: 'Fix wrong download',
          onClick: (e) => confirmInline(e.currentTarget,
            `Blocklist this download of "${f.scene_title}", move the file to the recycle bin and search again?`,
            async () => {
              const r = await window.SRW.runOp('vr_check_fix', { finding_id: f.id });
              if (r.error) { window.SRW.notify('Fix wrong download', r.error, 'error'); return; }
              window.SRW.notify('Fix wrong download', `"${f.scene_title}": blocklisted${r.blocklisted_title ? ` (${r.blocklisted_title})` : ''}, file moved to the recycle bin.`, 'success');
              remove();
            }, { danger: true }),
        }));
      }
      // Quick visual check (2026-09-26, user request): Stash's own scene
      // screenshot as thumbnail -- a real VR file shows two eyes side by
      // side (or stacked) right there -- and a clearly styled title link;
      // both open the scene in a new tab so this list stays put.
      const sceneHref = `/scenes/${f.scene_id}`;
      const thumb = el('a', {
        className: 'srw-vr-thumb-link', href: sceneHref, target: '_blank', rel: 'noopener noreferrer',
        title: 'Open the scene in Stash (new tab)',
      }, [el('img', { className: 'srw-vr-thumb', src: `/scene/${f.scene_id}/screenshot`, loading: 'lazy', alt: '' })]);
      return el('li', { className: 'srw-release-item' }, [
        el('span', { className: `srw-vr-verdict srw-vr-verdict-${f.verdict}`, textContent: verdict }),
        thumb,
        el('div', { className: 'srw-row-info' }, [
          el('a', {
            className: 'srw-release-title', href: sceneHref, target: '_blank', rel: 'noopener noreferrer',
            textContent: f.scene_title || `Scene ${f.scene_id}`,
          }),
          el('div', { className: 'srw-hint', textContent: `${f.width}×${f.height} — ${scores}`
            + `${f.verdict === 'vr_mono' ? '' : ` — ${phash}`}` }),
          // Monoscopic 360/180 VR (2026-09-26, user decision): fine to keep,
          // but should be replaced by a stereoscopic version as an upgrade.
          f.verdict === 'vr_mono' ? el('div', {
            className: 'srw-hint srw-vr-mono-note',
            textContent: 'Monoscopic 360°/180° VR -- OK to keep, upgrade candidate: replace with a stereoscopic '
              + 'version when one is available.',
          }) : null,
          // No phash match at all (2026-09-26, user-confirmed on candlj.mpg):
          // the file may not even be the scene Stash links it to.
          f.phash_match === 0 ? el('div', {
            className: 'srw-hint srw-vr-misid',
            textContent: 'Possibly misidentified in Stash (file matches none of the scene\'s fingerprints) -- '
              + 'use “Identify Scene” (or the Library Check above) to find the right scene.',
          }) : null,
          el('div', { className: 'srw-maintenance-file-path', textContent: f.file_path }),
        ]),
        el('div', { className: 'srw-row-actions' }, actions),
      ]);
    }

    poll();
    return window.SRW.rememberOpen(section, 'maintenance:vr-check', false);
  }

  // ==================== Upgrade search (2026-09-26, api/upgrades.py) ====================
  const UPGRADE_REASON = {
    // Badge = current file → upgrade target (2026-09-26: "VR → real VR" read
    // as if the file were already VR).
    vr_2d: ['2D → VR', 'VR scene, but the file is 2D -- replace with real stereoscopic VR'],
    vr_mono: ['mono → stereo', 'monoscopic 360°/180° VR, should become stereoscopic'],
    vr_low_quality: ['VR quality', 'VR below 4K or below the minimum bitrate'],
    below_cutoff: ['below cutoff', 'below the quality profile\'s cutoff resolution'],
  };

  function buildOrganizeSection() {
    const { el, confirmInline } = window.SRW;
    let studios = [];
    const status = el('span', { className: 'srw-key-status' });
    const planSlot = el('div');
    const short = (p) => p.replace(/^.*?\/scenes\//, '');
    const studioView = window.SRW.buildFilteredPagedList({
      filters: ['search'],
      perPage: 25,
      emptyText: 'Nothing to organize.',
      fetchPage: window.SRW.localPageFetcher(() => studios, { text: (x) => x.studio }),
      renderItems: (page) => el('ul', { className: 'srw-release-list' }, page.map((x) => el('li', { className: 'srw-release-item' }, [
        el('strong', { textContent: x.studio }),
        el('span', { className: 'srw-release-date', textContent: `${x.rename} rename · ${x.move} move` }),
        el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Organize…', onClick: () => showPlan(x) }),
      ]))),
    });

    async function loadStudios(refresh) {
      status.textContent = ' loading…';
      const r = await window.SRW.runOp('organize_studios', { refresh: Boolean(refresh) });
      if (r.error) { status.textContent = ` error: ${r.error}`; return; }
      studios = r.studios || [];
      const files = studios.reduce((n, x) => n + x.total, 0);
      status.textContent = ` ${studios.length} studio(s), ${files} file(s) differ from the naming scheme`;
      studioView.reset();
    }

    function itemRow(x, kind, studio) {
      const rowStatus = el('span', { className: 'srw-key-status' });
      const parts = [
        el('a', {
          className: 'srw-release-title', href: `/scenes/${x.scene_id}`, target: '_blank',
          rel: 'noopener noreferrer', textContent: x.title,
        }),
        x.duplicate ? el('span', { className: 'srw-history-category', textContent: 'identical copy elsewhere' }) : null,
        el('div', { className: 'srw-review-box' }, [
          el('span', { className: 'srw-history-message', textContent: `${short(x.path)}\n→ ${short(x.new_path)}` }),
        ]),
      ];
      if (kind === 'rename' || kind === 'move') {
        parts.push(el('button', {
          className: 'srw-btn srw-btn-sm', textContent: kind === 'rename' ? 'Rename' : 'Move',
          onClick: async (e) => {
            e.target.disabled = true;
            rowStatus.textContent = ' working…';
            const r = await window.SRW.runOp('organize_apply', { studio_id: studio.studio_id, file_ids: [x.file_id], kinds: [kind] });
            const failed = (r.failed || [])[0];
            rowStatus.textContent = r.error ? ` error: ${r.error}` : failed ? ` failed: ${failed.reason}` : ' done';
            if (!r.error && !failed) showPlan(studio, true);
            else e.target.disabled = false;
          },
        }), rowStatus);
      } else {
        parts.push(el('span', { className: 'srw-release-date', textContent: x.reason || x.check }));
      }
      return el('li', { className: 'srw-release-item' }, parts);
    }

    function group(label, items, kind, studio, hint) {
      const g = el('details', { className: 'srw-date-group' }, [el('summary', { textContent: `${label} — ${items.length}` })]);
      window.SRW.rememberOpen(g, `organize:${kind}`, kind === 'rename');
      if (hint) g.appendChild(el('p', { className: 'srw-hint', textContent: hint }));
      if ((kind === 'rename' || kind === 'move') && items.length) {
        const bulkStatus = el('span', { className: 'srw-key-status' });
        g.appendChild(el('div', { className: 'srw-toolbar' }, [
          el('button', {
            className: 'srw-btn srw-btn-primary', textContent: `${kind === 'rename' ? 'Rename' : 'Move'} all ${items.length}`,
            onClick: (e) => confirmInline(e.currentTarget, `${kind === 'rename' ? 'Rename' : 'Move'} ${items.length} file(s)? Stash keeps each scene.`, async () => {
              bulkStatus.textContent = ' working -- the folders are rescanned afterwards, this can take a while…';
              const r = await window.SRW.runOp('organize_apply', {
                studio_id: studio.studio_id, file_ids: items.map((x) => x.file_id), kinds: [kind],
              });
              bulkStatus.textContent = r.error ? ` error: ${r.error}`
                : ` done ${(r.done || []).length}, failed ${(r.failed || []).length} (details in History)`;
              showPlan(studio, true);
            }),
          }),
          bulkStatus,
        ]));
      }
      const view = window.SRW.buildFilteredPagedList({
        filters: ['search'], perPage: 50, emptyText: 'None.',
        fetchPage: window.SRW.localPageFetcher(() => items, { text: (x) => `${x.title || ''} ${x.path || ''}` }),
        renderItems: (page) => el('ul', { className: 'srw-release-list' }, page.map((x) => (kind === 'multi'
          ? el('li', { className: 'srw-release-item' }, [
            el('a', { className: 'srw-release-title', href: `/scenes/${x.scene_id}`, target: '_blank', rel: 'noopener noreferrer', textContent: x.title }),
            el('span', { className: 'srw-history-message', textContent: x.paths.map(short).join('\n') }),
          ])
          : itemRow(x, kind, studio)))),
      });
      g.appendChild(view.node);
      view.reset();
      return g;
    }

    const planYears = {}; // studio id -> chosen year, kept across re-plans
    async function showPlan(studio, keepStatus) {
      if (!keepStatus) planSlot.replaceChildren(el('p', { className: 'srw-hint', textContent: `Checking ${studio.studio} against StashDB fingerprints…` }));
      const p = await window.SRW.runOp('organize_plan', { studio_id: studio.studio_id });
      if (p.error) { planSlot.replaceChildren(el('p', { textContent: `Error: ${p.error}` })); return; }
      // Quick per-studio / per-year view (2026-09-29): the same plan, filtered by the scene's year.
      const years = [...new Set(['rename', 'move', 'conflict', 'needs_check'].flatMap((k) => (p[k] || []).map((x) => (x.date || '').slice(0, 4)).filter(Boolean)))].sort();
      let year = planYears[studio.studio_id] || '';
      if (year && !years.includes(year)) year = '';
      const byYear = (items) => (year ? (items || []).filter((x) => (x.date || '').startsWith(year)) : (items || []));
      const body = el('div');
      const render = () => body.replaceChildren(
        group('Rename in the same folder (verified)', byYear(p.rename), 'rename', studio),
        group('Move to another folder (verified)', byYear(p.move), 'move', studio),
        group('Name conflict (not touched)', byYear(p.conflict), 'conflict', studio,
          'The target name is already taken (usually two versions of one scene in a folder). Decide which file to keep.'),
        group('Needs a check first (fingerprints don\'t match the tag)', byYear(p.needs_check), 'check', studio,
          'Not touched: the file doesn\'t match the scene it\'s tagged with -- a wrong tag must not end up in the file name. Use the Library Check to fix the tag first.'),
        group('Scenes with several files (not touched)', p.multi_file || [], 'multi', studio),
      );
      const yearSelect = el('select', {
        className: 'srw-input', onChange: (e) => { year = e.target.value; planYears[studio.studio_id] = year; render(); },
      }, [el('option', { value: '', textContent: 'All years' }), ...years.map((y) => el('option', { value: y, textContent: y }))]);
      yearSelect.value = year;
      planSlot.replaceChildren(
        el('h4', { textContent: `${studio.studio} → folder "${p.studio}"` }),
        el('div', { className: 'srw-toolbar' }, [el('label', {}, ['Year: ', yearSelect]),
          el('span', { className: 'srw-hint', textContent: ' narrows the lists below and the "all" buttons to one release year' })]),
        body,
      );
      render();
      if (keepStatus) loadStudios(false);
    }

    const section = el('div', { className: 'srw-settings-section' }, [
      el('h3', { textContent: 'Organize library (rename / move)' }),
      el('p', {
        className: 'srw-hint',
        textContent: 'Brings the Whisparr library folder (scenes/) in line with the naming scheme, studio by studio: '
          + 'the scene\'s own studio in StashDB\'s spelling, then title and date. Only files whose fingerprints match '
          + 'their tag are offered. .funscript/.nfo with the same name are renamed along; if the file is the only video '
          + 'in its folder, folder.jpg etc. move with it and the empty folder is removed. Stash keeps every scene '
          + '(rating, history); each change is logged in History. Suggested order: renames first, then moves.',
      }),
      el('div', { className: 'srw-toolbar' }, [
        el('button', { className: 'srw-btn', textContent: 'Load studios', onClick: () => loadStudios(false) }),
        el('button', { className: 'srw-btn', textContent: 'Recount', onClick: () => loadStudios(true) }),
        status,
      ]),
      studioView.node,
      planSlot,
    ]);
    return section;
  }

  // ==================== Import folder (2026-09-29, api/library_import.py) ====================
  function buildImportSection() {
    const { el, confirmInline } = window.SRW;
    const status = el('span', { className: 'srw-key-status' });
    const sourcesSlot = el('div');
    const resultSlot = el('div');
    const short = (p) => p.replace(/^\/data\//, '');
    const picks = {}; // scene_id -> {provider, provider_id} confirmed from the name suggestions

    async function loadSources() {
      status.textContent = ' loading…';
      const r = await window.SRW.runOp('import_sources');
      if (r.error) { status.textContent = ` error: ${r.error}`; return; }
      status.textContent = '';
      sourcesSlot.replaceChildren(el('ul', { className: 'srw-release-list' }, (r.sources || []).map((s) => el('li', { className: 'srw-release-item' }, [
        el('strong', { textContent: s.root }),
        el('span', { className: 'srw-release-date', textContent: s.exists ? `${s.scenes} scene(s) in Stash · ${s.with_id} already identified` : 'folder not found' }),
        s.exists ? el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Analyse', onClick: () => analyse(s.root) }) : null,
        s.exists ? el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Finish source', onClick: () => finishSource(s.root) }) : null,
      ]))));
    }

    async function finishSource(root) {
      resultSlot.replaceChildren(el('p', { className: 'srw-hint', textContent: `Checking ${root}…` }));
      const r = await window.SRW.runOp('import_finish_check', { root });
      if (r.error) { resultSlot.replaceChildren(el('p', { textContent: `Error: ${r.error}` })); return; }
      const doneStatus = el('span', { className: 'srw-key-status' });
      const rest = [...(r.videos || []), ...(r.unknown || [])].slice(0, 15);
      resultSlot.replaceChildren(el('div', {}, [
        el('h4', { textContent: `Finish ${root}` }),
        el('p', {
          className: 'srw-hint',
          textContent: `${(r.videos || []).length} video(s), ${(r.junk || []).length} junk file(s) (recycle bin), ${(r.unknown || []).length} other file(s), `
            + `${r.stash_scenes || 0} scene(s) still listed in Stash below it.`,
        }),
        (r.problems || []).length ? el('ul', {}, r.problems.map((p) => el('li', { textContent: p }))) : null,
        rest.length ? el('pre', { className: 'srw-maintenance-file-path', textContent: rest.join('\n') }) : null,
        r.can_finish ? el('div', { className: 'srw-toolbar' }, [
          el('button', {
            className: 'srw-btn srw-btn-primary', textContent: 'Finish: remove from Stash, folder to the recycle bin',
            onClick: (e) => confirmInline(e.currentTarget,
              `Remove ${root} from Stash's libraries and move the empty folder to the recycle bin (deleted after 7 days)?`, async () => {
                doneStatus.textContent = ' working…';
                const f = await window.SRW.runOp('import_finish', { root });
                doneStatus.textContent = f.error ? ` error: ${f.error}` : f.done ? ' done -- the folder is in the recycle bin, the old library list is saved' : ` not done: ${(f.problems || []).join('; ')}`;
                if (f.done) loadSources();
              }),
          }),
          doneStatus,
        ]) : el('p', { className: 'srw-hint', textContent: 'Not possible yet -- fix the points above first.' }),
      ]));
    }

    async function analyse(root, keepStatus) {
      if (!keepStatus) resultSlot.replaceChildren(el('p', { className: 'srw-hint', textContent: `Analysing ${root} (fingerprint lookups at StashDB and ThePornDB, this takes a minute or two)…` }));
      const r = await window.SRW.runOp('import_analyse', { root });
      if (r.error) { resultSlot.replaceChildren(el('p', { textContent: `Error: ${r.error}` })); return; }
      const c = r.counts || {};
      resultSlot.replaceChildren(
        el('h4', { textContent: `${root} — ${c.ready || 0} ready · ${c.movie || 0} movies · ${c.identify || 0} identified by fingerprint · ${c.review || 0} to review · ${c.unknown || 0} unknown` }),
        group('Ready to import', r.items.filter((x) => x.kind === 'ready'), 'ready', root,
          'The scene already has a provider id and the fingerprints match. Import = apply the provider\'s metadata (title, date, studio, performers; VR tag for VR files), move to the naming scheme, keep .funscript/.srt/.vtt, junk to the recycle bin.'),
        group('Movie scenes (grouped)', r.items.filter((x) => x.kind === 'movie'), 'movie', root,
          'The scene is in one Stash group that has a ThePornDB movie link (or you marked the scene Organized). It goes to Movies/<Title> (<Year>)/<Title> (<Year>) [- part N] <Quality> -- the layout of the film import; title and year come from the group. Nothing is looked up at the providers and the scene keeps the metadata you gave it. A scene in a group without a link stays under Unknown until you mark it Organized in Stash.'),
        group('Identified by fingerprint', r.items.filter((x) => x.kind === 'identify'), 'identify', root,
          'No id yet, but the provider knows this exact file.'),
        group('Needs a look', r.items.filter((x) => x.kind === 'review'), 'review', root,
          'Not touched: ambiguous, target name taken, several files in the scene, or a near match of a file older than the release.'),
        group('Unknown — name suggestions', r.items.filter((x) => x.kind === 'unknown'), 'unknown', root,
          'No fingerprint match. "Suggest" searches StashDB and ThePornDB with words from the file name; nothing is imported unless you pick a match. Old films with CD parts belong to the movie handling, not here.'),
      );
    }

    function candidateRow(x, c, rowStatus) {
      const chosen = picks[x.scene_id] && picks[x.scene_id].provider_id === c.provider_id;
      const btn = el('button', {
        className: 'srw-btn srw-btn-sm', textContent: chosen ? 'Picked ✓' : 'Use this',
        onClick: (e) => {
          picks[x.scene_id] = { provider: c.provider, provider_id: c.provider_id };
          e.currentTarget.textContent = 'Picked ✓';
          rowStatus.textContent = ' picked -- press "Import picked" above';
        },
      });
      return el('li', { className: 'srw-release-item' }, [
        el('span', { className: 'srw-release-date', textContent: `${Math.round(c.score * 100)}%` }),
        el('span', { className: 'srw-history-category', textContent: window.SRW.providerLabel(c.provider) }),
        el('span', { textContent: ` ${c.studio || ''} — ${c.title || ''} (${c.date || '?'}${c.minutes ? `, ${c.minutes} min` : ''}) ${c.performers.join(', ')}` }),
        btn,
      ]);
    }

    function itemRow(x, kind, root) {
      const rowStatus = el('span', { className: 'srw-key-status' });
      const sugSlot = el('ul', { className: 'srw-release-list' });
      const parts = [
        el('a', { className: 'srw-release-title', href: `/scenes/${x.scene_id}`, target: '_blank', rel: 'noopener noreferrer', textContent: x.title }),
        x.vr ? el('span', { className: 'srw-history-category', textContent: 'VR' }) : null,
        el('div', { className: 'srw-review-box' }, [
          el('span', { className: 'srw-history-message', textContent: x.new_path ? `${short(x.path)}\n→ ${short(x.new_path)}` : short(x.path) }),
        ]),
        el('span', { className: 'srw-release-date', textContent: x.how || '' }),
      ];
      if (x.kind === 'ready' || x.kind === 'identify' || x.kind === 'movie') {
        parts.push(el('button', {
          className: 'srw-btn srw-btn-sm', textContent: 'Import',
          onClick: async (e) => {
            e.target.disabled = true; rowStatus.textContent = ' working…';
            const r = await window.SRW.runOp('import_apply', { root, scene_ids: [x.scene_id] });
            const failed = (r.failed || [])[0];
            rowStatus.textContent = r.error ? ` error: ${r.error}` : failed ? ` failed: ${failed.reason}` : ' done';
            if (!r.error && !failed) analyse(root, true); else e.target.disabled = false;
          },
        }), rowStatus);
      } else if (x.kind === 'unknown' || x.kind === 'review') {
        if (x.file_id) {
          parts.push(el('button', {
            className: 'srw-btn srw-btn-sm', textContent: 'Suggest',
            onClick: async (e) => {
              e.target.disabled = true; rowStatus.textContent = ' searching…';
              const r = await window.SRW.runOp('import_suggest', { root, scene_ids: [x.scene_id] });
              e.target.disabled = false;
              if (r.error) { rowStatus.textContent = ` error: ${r.error}`; return; }
              const s = (r.suggestions || {})[String(x.scene_id)] || { candidates: [] };
              rowStatus.textContent = s.candidates.length ? ` searched "${s.query}"` : ` nothing found for "${s.query}"`;
              sugSlot.replaceChildren(...s.candidates.map((c) => candidateRow(x, c, rowStatus)));
            },
          }), rowStatus);
        }
        parts.push(sugSlot);
      }
      return el('li', { className: 'srw-release-item' }, parts);
    }

    function group(label, items, kind, root, hint) {
      const g = el('details', { className: 'srw-date-group' }, [el('summary', { textContent: `${label} — ${items.length}` })]);
      window.SRW.rememberOpen(g, `import:${kind}`, kind === 'ready');
      if (hint) g.appendChild(el('p', { className: 'srw-hint', textContent: hint }));
      const bulkStatus = el('span', { className: 'srw-key-status' });
      if ((kind === 'ready' || kind === 'identify' || kind === 'movie') && items.length) {
        g.appendChild(el('div', { className: 'srw-toolbar' }, [
          el('button', {
            className: 'srw-btn srw-btn-primary', textContent: `Import all ${items.length}`,
            onClick: (e) => confirmInline(e.currentTarget, `Import ${items.length} file(s)? Each is moved to the library; Stash keeps the scene.`, async () => {
              bulkStatus.textContent = ' working -- this can take a while (Stash rescans the folders)…';
              const r = await window.SRW.runOp('import_apply', { root, scene_ids: items.map((x) => x.scene_id) });
              bulkStatus.textContent = r.error ? ` error: ${r.error}`
                : ` done ${(r.done || []).length}, failed ${(r.failed || []).length}${(r.leftovers || []).length ? `, ${r.leftovers.length} folder(s) with leftovers` : ''} (details in History)`;
              analyse(root, true);
            }),
          }),
          bulkStatus,
        ]));
      }
      if (kind === 'unknown' && items.length) {
        g.appendChild(el('div', { className: 'srw-toolbar' }, [
          el('button', {
            className: 'srw-btn', textContent: 'Import picked',
            onClick: async (e) => {
              const ids = Object.keys(picks);
              if (!ids.length) { bulkStatus.textContent = ' nothing picked yet'; return; }
              e.target.disabled = true; bulkStatus.textContent = ` importing ${ids.length} picked file(s)…`;
              const r = await window.SRW.runOp('import_apply', { root, scene_ids: ids, picks });
              e.target.disabled = false;
              bulkStatus.textContent = r.error ? ` error: ${r.error}`
                : ` done ${(r.done || []).length}, failed ${(r.failed || []).length} (details in History)`;
              ids.forEach((i) => delete picks[i]);
              analyse(root, true);
            },
          }),
          bulkStatus,
        ]));
      }
      const view = window.SRW.buildFilteredPagedList({
        filters: ['search'], perPage: 50, emptyText: 'None.',
        fetchPage: window.SRW.localPageFetcher(() => items, { text: (x) => `${x.title || ''} ${x.path || ''}` }),
        renderItems: (page) => el('ul', { className: 'srw-release-list' }, page.map((x) => itemRow(x, kind, root))),
      });
      g.appendChild(view.node);
      view.reset();
      return g;
    }

    const section = el('div', { className: 'srw-settings-section' }, [
      el('h3', { textContent: 'Import folder' }),
      el('p', {
        className: 'srw-hint',
        textContent: 'Brings the files of the temporary source folders (/data/Scenes, /data/import, /data/_VR) into the library: '
          + 'identify by fingerprint at StashDB/ThePornDB, apply the provider\'s metadata (VR files get the "Virtual Reality" tag), '
          + 'move to the naming scheme. Only .funscript/.srt/.vtt move along; jpg/nfo/txt/url/rar go to the recycle bin (7 days); '
          + 'empty folders are removed, the source folder itself stays. Files no fingerprint knows stay put until you pick a match.',
      }),
      el('div', { className: 'srw-toolbar' }, [
        el('button', { className: 'srw-btn', textContent: 'Load sources', onClick: loadSources }),
        status,
      ]),
      sourcesSlot,
      resultSlot,
    ]);
    return section;
  }

  // ==================== Movies & groups (2026-09-29, api/library_movies.py) ====================
  // A Stash list that loads itself when it is opened and again when it is older than a minute (v0.169.0); "Refresh" loads it at once.
  // loadFn() returns the status text on success or false after it has shown an error itself; the load time is added to the text.
  function autoLoader(statusEl, loadFn) {
    let at = 0; let busy = false;
    return async function run(force = false) {
      if (busy || (!force && at && Date.now() - at < 60000)) return;
      busy = true;
      statusEl.textContent = ' loading…';
      try {
        const text = await loadFn();
        if (text !== false) {
          at = Date.now();
          statusEl.textContent = `${text} (loaded ${new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })})`;
        }
      } finally { busy = false; }
    };
  }

  // Unidentified scenes (v0.170.0): scenes without any provider id outside the film folders. "Check fingerprints" is a DRY RUN (StashDB, exact
  // fingerprints, 40 scenes per request, nothing is written); only the clear matches can then be assigned. Everything else goes to Review >
  // Identify Scene, which has the name search, ThePornDB, playing and the other tools.
  function buildUnidentifiedArea() {
    const { el, confirmInline, notify } = window.SRW;
    let items = [];
    let inReview = 0;
    const results = {}; // scene id -> check result
    let resultFilter = '';
    const status = el('span', { className: 'srw-key-status' });
    const summary = el('div', { className: 'srw-hint' });
    // The "show:" choice: one result class, "any match" (every checked file StashDB knows something about: not "no match"), or "not checked yet".
    const passesResult = (x) => {
      if (!resultFilter) return true;
      const r = results[x.id];
      if (resultFilter === 'unchecked') return !r;
      if (resultFilter === 'matched') return Boolean(r) && r.state !== 'none';
      return Boolean(r) && r.state === resultFilter;
    };
    const STATE_LABEL = { unique: 'clear match', ambiguous: 'several matches', held: 'already in your library', shared: 'two files, one match', none: 'no match' };
    const view = window.SRW.buildFilteredPagedList({
      filters: ['search'], perPage: 50, emptyText: 'No unidentified scenes.',
      fetchPage: window.SRW.localPageFetcher(
        () => items.filter(passesResult),
        { text: (x) => `${x.file} ${x.folder} ${x.title}` }),
      renderItems: (page) => el('ul', { className: 'srw-release-list' }, page.map((x) => {
        const rowStatus = el('span', { className: 'srw-key-status' });
        const r = results[x.id];
        const sdbLink = (m) => el('a', { href: `https://stashdb.org/scenes/${m.id}`, target: '_blank', rel: 'noopener noreferrer', textContent: `${m.title || m.id} ↗` });
        const remove = () => { items = items.filter((i) => i.id !== x.id); view.reset(); updateSummary(); };
        const review = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Send to Identify Scene',
          title: 'Adds the tag “Review: Identify Scene” (nothing else changes); the tools to identify it are there',
          onClick: async () => {
            const res = await window.SRW.runOp('compare_review_add', { scene_id: x.id });
            if (res.error) { rowStatus.textContent = ` error: ${res.error}`; return; }
            remove();
          } });
        return el('li', { className: 'srw-release-item' }, [
          el('span', { className: 'srw-release-title', textContent: x.title || x.file }),
          el('span', { className: 'srw-release-date', textContent: [x.studio_folder, x.minutes ? `${x.minutes} min` : null, x.height ? `${x.height}p` : null, x.mb ? `${x.mb} MB` : null].filter(Boolean).join(' · ') }),
          el('div', { className: 'srw-hint', textContent: x.folder }),
          r ? el('div', {}, [el('span', { className: 'srw-history-category', textContent: STATE_LABEL[r.state] || r.state }),
            ...r.matches.map((m) => el('span', {}, [' ', sdbLink(m)])),
            r.held_by ? el('span', { className: 'srw-hint', textContent: ` scene ${r.held_by} has it` }) : null]) : null,
          window.SRW.buildStashLink('scene', x.id, 'Play in Stash'),
          r && r.state === 'unique' ? el('button', { className: 'srw-btn srw-btn-sm srw-btn-primary', textContent: 'Assign',
            onClick: (e) => confirmInline(e.currentTarget, `Put the StashDB scene “${r.matches[0].title}” on this file? Title, date, studio, performers and tags are filled in; the old state is saved to the backup file.`, async () => {
              const res = (await window.SRW.runOp('compare_unidentified_apply', { scene_ids: [x.id] })).results || {};
              const one = res[x.id] || {};
              if (one.error) { rowStatus.textContent = ` error: ${one.error}`; return; }
              delete results[x.id]; remove();
            }) }) : null,
          review, rowStatus,
        ]);
      })),
    });
    const filterSel = el('select', { onChange: (e) => { resultFilter = e.target.value; view.reset(); } },
      [['', 'all'], ['matched', 'any match (not “no match”)'], ['unchecked', 'not checked yet'], ['unique', 'clear match'], ['ambiguous', 'several matches'], ['held', 'already in your library'],
        ['shared', 'two files, one match'], ['none', 'no match']].map(([v, t]) => el('option', { value: v, textContent: t })));
    const count = (state) => items.filter((i) => (results[i.id] || {}).state === state).length;
    const assignBtn = el('button', { className: 'srw-btn srw-btn-primary', textContent: 'Assign the clear matches', hidden: true });
    function updateSummary() {
      const checked = items.filter((i) => results[i.id]).length;
      const unique = count('unique');
      const when = items.map((i) => (results[i.id] || {}).checked_at).filter(Boolean).sort();
      summary.textContent = `${items.length} scene(s) without a provider id`
        + (inReview ? ` (${inReview} more are already in Review: Identify Scene)` : '')
        + (checked ? ` — checked ${checked} (last ${when[when.length - 1]} UTC, oldest ${when[0].slice(0, 10)}): ${unique} clear, ${count('ambiguous')} several matches, ${count('held')} already in your library, ${count('shared')} two files / one match, ${count('none')} no match` : '');
      assignBtn.hidden = !unique;
      assignBtn.textContent = `Assign the ${unique} clear match(es)`;
    }
    async function load() {
      const r = await window.SRW.runOp('compare_unidentified');
      if (r.error) { status.textContent = ` error: ${r.error}`; return false; }
      items = r.scenes || [];
      inReview = r.in_review || 0;
      Object.keys(results).forEach((k) => delete results[k]);
      Object.assign(results, r.checks || {}); // the stored results of the last checks (they survive a reload)
      view.reset(); updateSummary();
      return ` ${items.length} scene(s)`;
    }
    const loader = autoLoader(status, load);
    const checkBtn = el('button', { className: 'srw-btn', textContent: 'Check fingerprints (dry run)',
      title: 'Asks StashDB which scene each file is, by exact fingerprint: one request per 40 files. Nothing is changed.' });
    const runCheck = async (all) => {
      const todo = items.filter((i) => all || !results[i.id]).map((i) => i.id);
      if (!todo.length) { notify('Unidentified scenes', 'Everything listed was checked already.', 'info'); return; }
      checkBtn.disabled = true;
      try {
        for (let i = 0; i < todo.length; i += 120) {
          status.textContent = ` checking ${Math.min(i + 120, todo.length)} / ${todo.length} at StashDB…`;
          const r = await window.SRW.runOp('compare_unidentified_check', { scene_ids: todo.slice(i, i + 120) });
          if (r.error) { status.textContent = ` error: ${r.error}`; break; }
          Object.assign(results, r.results || {});
          updateSummary(); view.reset();
        }
        status.textContent = ' check done';
      } finally { checkBtn.disabled = false; }
    };
    checkBtn.addEventListener('click', () => runCheck(false));
    const recheckBtn = el('button', { className: 'srw-btn', textContent: 'Check everything again',
      title: 'Asks StashDB again for every listed file, also the ones checked before (one request per 40 files)' });
    recheckBtn.addEventListener('click', () => confirmInline(recheckBtn, `Ask StashDB again for all ${items.length} files (about ${Math.ceil(items.length / 40)} requests)?`, () => runCheck(true)));
    assignBtn.addEventListener('click', () => confirmInline(assignBtn,
      `Assign the StashDB scene to ${count('unique')} file(s) that matched exactly one scene? Title, date, studio, performers and tags are filled in; each old state is saved to the backup file first.`,
      async () => {
        const ids = items.filter((i) => (results[i.id] || {}).state === 'unique').map((i) => i.id);
        let done = 0; let failed = 0;
        for (let i = 0; i < ids.length; i += 20) {
          status.textContent = ` assigning ${Math.min(i + 20, ids.length)} / ${ids.length}…`;
          const res = (await window.SRW.runOp('compare_unidentified_apply', { scene_ids: ids.slice(i, i + 20) })).results || {};
          Object.entries(res).forEach(([sid, one]) => { if (one.ok) { done += 1; delete results[sid]; items = items.filter((x) => x.id !== sid); } else failed += 1; });
        }
        status.textContent = ` ${done} assigned${failed ? `, ${failed} failed (see History)` : ''}`;
        view.reset(); updateSummary();
      }));
    const bulkReview = el('button', { className: 'srw-btn', textContent: 'Send the shown ones to Identify Scene',
      title: 'Adds the review tag to every scene the current search / filter shows' });
    bulkReview.addEventListener('click', async () => {
      const q = view.state.search || '';
      const shown = items.filter((x) => passesResult(x)
        && (!q || `${x.file} ${x.folder} ${x.title}`.toLowerCase().includes(q.toLowerCase())));
      if (!shown.length) return;
      confirmInline(bulkReview, `Send ${shown.length} scene(s) to Review: Identify Scene?`, async () => {
        for (const x of shown) {
          status.textContent = ` sending ${shown.indexOf(x) + 1} / ${shown.length}…`;
          const res = await window.SRW.runOp('compare_review_add', { scene_id: x.id });
          if (!res.error) items = items.filter((i) => i.id !== x.id);
        }
        status.textContent = ' done'; view.reset(); updateSummary();
      });
    });
    const pane = el('div', {}, [
      el('p', { className: 'srw-hint', textContent: 'Files in your scene folders that Stash has no StashDB / ThePornDB id for (the film folders and the import folder are left out). '
        + '“Check fingerprints” asks StashDB which scene each file is and changes nothing (the results are kept, also after a reload; files already checked are skipped); only a file that matches exactly ONE StashDB scene, which no other file of yours has, can be assigned. '
        + 'Several matches, none or doubtful cases go to Review: Identify Scene (name search, ThePornDB, playing).' }),
      el('div', { className: 'srw-toolbar' }, [el('button', { className: 'srw-btn', textContent: 'Refresh', onClick: () => loader(true) }), checkBtn, recheckBtn, assignBtn, bulkReview,
        el('label', {}, ['show: ', filterSel]), status]),
      summary,
      view.node,
    ]);
    loader();
    return pane;
  }

  function buildPerformersArea() {
    const { el } = window.SRW;
    let performers = [];
    let hideWatchlist = true;
    const perfStatus = el('span', { className: 'srw-key-status' });
    const perfView = window.SRW.buildFilteredPagedList({
      filters: ['search'], perPage: 50, emptyText: 'No performers without scenes.',
      fetchPage: window.SRW.localPageFetcher(() => performers.filter((p) => !(hideWatchlist && p.on_watchlist)), { text: (x) => x.name }),
      renderItems: (page) => el('ul', { className: 'srw-release-list' }, page.map((p) => el('li', { className: 'srw-release-item' }, [
        el('a', { className: 'srw-release-title', href: `/performers/${p.id}`, target: '_blank', rel: 'noopener noreferrer', textContent: p.name + (p.disambiguation ? ` (${p.disambiguation})` : '') }),
        p.on_watchlist ? el('span', { className: 'srw-history-category', textContent: 'watchlist / favorite' }) : null,
      ]))),
    });
    async function loadPerformers() {
      const r = await window.SRW.runOp('movies_performers_empty');
      if (r.error) { perfStatus.textContent = ` error: ${r.error}`; return false; }
      performers = r.performers || [];
      perfView.reset();
      return ` ${performers.length} performer(s) without scenes, ${performers.filter((p) => p.on_watchlist).length} of them on the watchlist`;
    }
    const loader = autoLoader(perfStatus, loadPerformers);
    const hideBox = el('input', { type: 'checkbox', checked: true, onChange: (e) => { hideWatchlist = e.target.checked; perfView.reset(); } });
    const perfPane = el('div', {}, [
      el('div', { className: 'srw-toolbar' }, [
        el('button', { className: 'srw-btn', textContent: 'Refresh', onClick: () => loader(true) }),
        el('label', {}, [hideBox, ' hide watchlist / favorites (they are meant to have none yet)']), perfStatus,
      ]),
      perfView.node,
    ]);
    loader(); // loads itself when the page opens
    return perfPane;

  }

  function buildMoviesSection() {
    const { el, confirmInline } = window.SRW;
    const section = el('div', { className: 'srw-settings-section' }, [
      el('h3', { textContent: 'Movies & groups' }),
      el('p', {
        className: 'srw-hint',
        textContent: 'Only the movie folders (/data/Movies, /data/movies) are looked at -- normal scenes have no group by design. '
          + 'A film is a Group in Stash: one video = one scene + a group of the same title; a film in parts = one group with one scene per part.',
      }),
    ]);
    // The five parts are tabs (v0.154.0), like the sections of Activity, History and the entity panel; the count of a list shows in its tab once loaded.
    const whisparrPane = el('div');
    if (window.SRW_Films && window.SRW_Films.buildWhisparrSyncTools) whisparrPane.appendChild(window.SRW_Films.buildWhisparrSyncTools());
    if (window.SRW_Films && window.SRW_Films.buildWhisparrFileTools) whisparrPane.appendChild(window.SRW_Films.buildWhisparrFileTools());
    if (window.SRW_Films && window.SRW_Films.buildWhisparrHistoryTools) whisparrPane.appendChild(window.SRW_Films.buildWhisparrHistoryTools());
    const seedPane = el('div');
    if (window.SRW_Films && window.SRW_Films.buildSeedTools) seedPane.appendChild(window.SRW_Films.buildSeedTools());

    // -- videos without a group
    let folders = [];
    let noGroupLoaded = false;
    const noGroupStatus = el('span', { className: 'srw-key-status' });
    const noGroupView = window.SRW.buildFilteredPagedList({
      filters: ['search'], perPage: 25, emptyText: 'Nothing to group.',
      fetchPage: window.SRW.localPageFetcher(() => folders, { text: (x) => `${x.title} ${x.folder}` }),
      renderItems: (page) => el('ul', { className: 'srw-release-list' }, page.map((x) => {
        const rowStatus = el('span', { className: 'srw-key-status' });
        const titleInput = el('input', { type: 'text', value: x.title, className: 'srw-input' });
        return el('li', { className: 'srw-release-item' }, [
          titleInput,
          el('span', { className: 'srw-release-date', textContent: `${x.year || 'no year'} · ${x.videos.length} video(s) · ${x.minutes} min` }),
          ...x.flags.map((f) => el('span', { className: 'srw-history-category', textContent: f })),
          el('div', { className: 'srw-review-box' }, [
            el('span', { className: 'srw-history-message', textContent: `${x.root}/${x.folder}\n${x.videos.map((v) => v.file).join('\n')}` }),
          ]),
          // to look at the video(s) before deciding what the movie is: one link per file (to the scene in Stash)
          ...x.videos.filter((v) => v.scene_id).map((v, i) => window.SRW.buildStashLink('scene', v.scene_id, x.videos.length > 1 ? `Play part ${i + 1}` : 'Play in Stash')),
          x.existing_group ? el('span', {
            className: 'srw-history-category',
            textContent: `existing group "${x.existing_group.name}" (${x.existing_group.date || 'no date'}, ${x.existing_group.scenes} scene(s))`,
          }) : null,
          el('button', {
            className: 'srw-btn srw-btn-sm', textContent: x.existing_group ? 'Add to that group' : 'Create group',
            onClick: (e) => confirmInline(e.currentTarget, x.existing_group
              ? `Add ${x.videos.length} scene(s) to the existing group "${x.existing_group.name}"?`
              : `Create the group "${titleInput.value}" with ${x.videos.length} scene(s)?`, async () => {
              rowStatus.textContent = ' working…';
              const r = await window.SRW.runOp('movies_create_group', {
                title: titleInput.value, scene_ids: x.videos.map((v) => v.scene_id), group_id: x.existing_group ? x.existing_group.id : null,
              });
              rowStatus.textContent = r.error ? ` error: ${r.error}` : ' done';
              if (!r.error) { folders = folders.filter((f) => f !== x); noGroupStatus.textContent = ` ${folders.length} film folder(s) / video(s) without a group`; noGroupView.reset(); }
            }),
          }),
          rowStatus,
        ]);
      })),
    });
    async function loadNoGroup() {
      const r = await window.SRW.runOp('movies_no_group');
      if (r.error) { noGroupStatus.textContent = ` error: ${r.error}`; return false; }
      folders = r.folders || [];
      noGroupLoaded = true;
      noGroupView.reset();
      return ` ${folders.length} film folder(s) / video(s) without a group`;
    }
    const noGroupLoader = autoLoader(noGroupStatus, loadNoGroup);
    const noGroupPane = el('div', {}, [
      el('p', { className: 'srw-hint', textContent: 'Videos in the movie folders that are not in a group yet.' }),
      el('div', { className: 'srw-toolbar' }, [el('button', { className: 'srw-btn', textContent: 'Refresh', onClick: () => noGroupLoader(true) }), noGroupStatus]),
      noGroupView.node,
    ]);

    // -- groups without files
    let groups = [];
    let groupsLoaded = false;
    const groupStatus = el('span', { className: 'srw-key-status' });
    const groupView = window.SRW.buildFilteredPagedList({
      filters: ['search'], perPage: 50, emptyText: 'No empty groups.',
      fetchPage: window.SRW.localPageFetcher(() => groups, { text: (x) => x.name }),
      renderItems: (page) => el('ul', { className: 'srw-release-list' }, page.map((g) => el('li', { className: 'srw-release-item' }, [
        el('a', { className: 'srw-release-title', href: `/groups/${g.id}`, target: '_blank', rel: 'noopener noreferrer', textContent: g.name }),
        el('span', { className: 'srw-release-date', textContent: `${g.date || ''} ${g.reason}` }),
      ]))),
    });
    async function loadGroups() {
      const r = await window.SRW.runOp('movies_groups_empty');
      if (r.error) { groupStatus.textContent = ` error: ${r.error}`; return false; }
      groups = r.groups || [];
      groupsLoaded = true;
      groupView.reset();
      return ` ${groups.length} group(s) without files`;
    }
    const groupLoader = autoLoader(groupStatus, loadGroups);
    const groupPane = el('div', {}, [
      el('div', { className: 'srw-toolbar' }, [el('button', { className: 'srw-btn', textContent: 'Refresh', onClick: () => groupLoader(true) }), groupStatus]),
      groupView.node,
    ]);

    // -- already in the library (stage D, v0.160.0): wanted movies whose file Whisparr / Stash already have
    let libItems = [];
    let libLoaded = false;
    const libStatus = el('span', { className: 'srw-key-status' });
    const libSummary = el('span', { className: 'srw-hint' });
    const applyClearBtn = el('button', { className: 'srw-btn srw-btn-primary', textContent: 'Use the existing file for the clear ones' });
    applyClearBtn.hidden = true;
    const libView = window.SRW.buildFilteredPagedList({
      filters: ['search'], perPage: 25, emptyText: 'Nothing to show.',
      fetchPage: window.SRW.localPageFetcher(() => libItems, { text: (x) => `${x.title} ${x.year || ''}` }),
      renderItems: (page) => el('ul', { className: 'srw-release-list' }, page.map((v) => {
        const rowStatus = el('span', { className: 'srw-key-status' });
        const group = v.groups && v.groups[0];
        const what = v.action === 'link_group' ? `is already in the group "${group.name}" (${group.id}) -- the movie is linked to it` : 'is in Stash without a group -- it is put into the movie\'s group';
        const act = async (force) => {
          rowStatus.textContent = ' working…';
          const r = await window.SRW.runOp('films_library_apply', { film_ids: [v.film_id], force });
          const one = (r.applied || [])[0] || {};
          rowStatus.textContent = r.error ? ` error: ${r.error}` : (one.error ? ` error: ${one.error}` : ` ${one.status}`);
          if (!r.error && !one.error) { libItems = libItems.filter((x) => x.film_id !== v.film_id); libView.reset(); }
        };
        return el('li', { className: 'srw-release-item' }, [
          el('span', { className: 'srw-release-title', textContent: `${v.title}${v.year ? ` (${v.year})` : ''}` }),
          el('span', { className: 'srw-history-category', textContent: v.verdict === 'clear' ? 'clear' : 'to decide' }),
          el('span', { className: 'srw-release-date', textContent: `${v.resolution} · ${v.minutes} min · ${v.size_mb} MB` }),
          el('div', { className: 'srw-hint', textContent: `${v.verdict === 'clear' ? 'Length fits' : `ThePornDB says ${v.expected_min ? Math.round(v.expected_min) : '?'} min, the file has ${Math.round(v.minutes)} min`}; the file ${what}. Whisparr entry ${v.whisparr_entry}.` }),
          window.SRW.buildStashLink('scene', v.scene_id),
          group ? window.SRW.buildStashLink('group', group.id, 'Open group') : null,
          el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Use the existing file',
            onClick: (e) => confirmInline(e.currentTarget, v.verdict === 'clear' ? `Use the existing file for "${v.title}"? Nothing is downloaded, moved or deleted.`
              : `The length does not fit. Use the existing file for "${v.title}" anyway? Nothing is downloaded, moved or deleted.`, () => act(v.verdict !== 'clear')) }),
          el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Keep searching', title: 'The file is not good enough (e.g. 480p): the movie stays on the wishlist and leaves this list',
            onClick: async () => { await window.SRW.runOp('films_library_keep', { id: v.film_id }); libItems = libItems.filter((x) => x.film_id !== v.film_id); libView.reset(); } }),
          rowStatus,
        ]);
      })),
    });
    async function loadLibrary() {
      libStatus.textContent = ' checking (reads Whisparr\'s movie list once, can take half a minute)…';
      const r = await window.SRW.runOp('films_library_check');
      if (r.error) { libStatus.textContent = ` error: ${r.error}`; return; }
      libItems = [...(r.clear || []), ...(r.manual || [])];
      libLoaded = true;
      const clear = (r.clear || []).length;
      libStatus.textContent = '';
      libSummary.textContent = libItems.length
        ? `${clear} clear, ${(r.manual || []).length} to decide, ${r.other || 0} wanted movie(s) without a file known in Stash.`
        : `Nothing to decide: no wanted movie has a file in the library (${r.other || 0} wanted movie(s) without a file known in Stash).`;
      applyClearBtn.hidden = !clear;
      applyClearBtn.textContent = `Use the existing file for the ${clear} clear one(s)`;
      libView.reset();
    }
    applyClearBtn.addEventListener('click', (e) => confirmInline(e.currentTarget, 'Use the existing file for all clear movies? Nothing is downloaded, moved or deleted; the movies become imported.', async () => {
      libStatus.textContent = ' working…';
      const r = await window.SRW.runOp('films_library_apply', { film_ids: null, force: false });
      libStatus.textContent = r.error ? ` error: ${r.error}` : ` ${(r.applied || []).filter((x) => x.status === 'imported').length} of ${(r.applied || []).length} imported`;
      if (!r.error) await loadLibrary();
    }));
    const libraryPane = el('div', {}, [
      el('p', { className: 'srw-hint', textContent: 'Wanted movies whose file is ALREADY in the library (Whisparr imported it earlier, often under another ThePornDB id) -- they would be downloaded again. “Clear” = the length fits '
        + 'and the scene is in at most one group; “to decide” = the length does not fit (ThePornDB sometimes lists one disc only) or the file is in several groups. Using the existing file downloads, moves and deletes nothing.' }),
      el('div', { className: 'srw-toolbar' }, [el('button', { className: 'srw-btn', textContent: 'Check', onClick: loadLibrary }), applyClearBtn, libStatus]),
      libSummary,
      libView.node,
    ]);
    libSummary.textContent = 'Click Check to look for wanted movies whose file is already in the library.';

    const panes = [
      { key: 'whisparr', label: 'Whisparr', pane: whisparrPane },
      { key: 'library', label: () => `Already in the library${libLoaded ? ` (${libItems.length})` : ''}`, pane: libraryPane, watch: libSummary },
      { key: 'seed', label: 'Look-up on ThePornDB', pane: seedPane },
      { key: 'no-group', label: () => `Videos without a group${noGroupLoaded ? ` (${folders.length})` : ''}`, pane: noGroupPane, watch: noGroupStatus, onShow: () => noGroupLoader() },
      { key: 'groups', label: () => `Groups without files${groupsLoaded ? ` (${groups.length})` : ''}`, pane: groupPane, watch: groupStatus, onShow: () => groupLoader() },
    ];
    section.appendChild(window.SRW.buildPaneTabs(panes, 'srw:maintenance-movies-tab'));
    panes.forEach((p) => section.appendChild(p.pane));
    return section;
  }

  function buildUnknownQualitySection() {
    const { el, confirmInline } = window.SRW;
    let items = [];
    const status = el('span', { className: 'srw-key-status' });
    const base = (p) => p.split('/').pop();
    const listSlot = el('div');
    const view = window.SRW.buildFilteredPagedList({
      filters: ['search', 'vr'],
      perPage: 50,
      emptyText: 'No file names with "Unknown" as quality.',
      fetchPage: window.SRW.localPageFetcher(() => items, {
        text: (x) => `${x.title} ${x.path}`, vr: (x) => x.is_vr,
      }),
      renderItems: (page) => el('ul', { className: 'srw-release-list' }, page.map((x) => {
        const rowStatus = el('span', { className: 'srw-key-status' });
        return el('li', { className: 'srw-release-item' }, [
          el('a', {
            className: 'srw-release-title', href: `/scenes/${x.scene_id}`, target: '_blank',
            rel: 'noopener noreferrer', textContent: x.title || `Scene ${x.scene_id}`,
          }),
          el('span', { className: 'srw-release-date', textContent: `${x.width}x${x.height}${x.is_vr ? ' · VR' : ''}` }),
          el('div', { className: 'srw-review-box' }, [
            el('span', { className: 'srw-history-message', textContent: `${base(x.path)}  →  ${base(x.new_path)}` }),
          ]),
          el('button', {
            className: 'srw-btn srw-btn-sm', textContent: 'Rename',
            onClick: async (e) => {
              e.target.disabled = true;
              rowStatus.textContent = ' renaming…';
              const r = await window.SRW.runOp('unknown_quality_apply', { paths: [x.path] });
              rowStatus.textContent = r.error ? ` error: ${r.error}`
                : (r.renamed || []).length ? ' done' : ` failed: ${((r.failed || r.skipped || [])[0] || {}).reason || '?'}`;
              if (!r.error && (r.renamed || []).length) load();
              else e.target.disabled = false;
            },
          }),
          rowStatus,
        ]);
      })),
    });
    async function load() {
      status.textContent = ' loading…';
      const r = await window.SRW.runOp('unknown_quality_list');
      if (r.error) { status.textContent = ` error: ${r.error}`; return; }
      items = r.items || [];
      status.textContent = ` ${items.length} file(s)`;
      view.reset();
    }
    const renameAllBtn = el('button', {
      className: 'srw-btn srw-btn-primary', textContent: 'Rename all',
      onClick: (e) => confirmInline(e.currentTarget, `Rename all ${items.length} file(s)? Stash keeps each scene.`,
        async () => {
          status.textContent = ` renaming ${items.length} file(s) and rescanning -- can take a few minutes…`;
          const r = await window.SRW.runOp('unknown_quality_apply', { paths: items.map((x) => x.path) });
          status.textContent = r.error ? ` error: ${r.error}` : ` renamed ${(r.renamed || []).length}, `
            + `failed ${(r.failed || []).length}, skipped ${(r.skipped || []).length} (details in History)`;
          load();
        }),
    });
    const section = el('div', { className: 'srw-settings-section' }, [
      el('h3', { textContent: 'File names with "Unknown" quality' }),
      el('p', {
        className: 'srw-hint',
        textContent: 'Replaces only the quality part of the name: the source from the original release name when '
          + 'this sidecar grabbed it, otherwise WEBDL (your library\'s convention for untagged site releases); the '
          + 'resolution from the file itself (VR: width class like 8K). Stash keeps each scene (rating, history) -- '
          + 'the folders are rescanned afterwards. Every rename is logged in History.',
      }),
      el('div', { className: 'srw-toolbar' }, [
        el('button', { className: 'srw-btn', textContent: 'Find files', onClick: () => load() }), renameAllBtn, status,
      ]),
      listSlot,
    ]);
    listSlot.appendChild(view.node);
    return section;
  }

  function buildUpgradeSection() {
    const { el, confirmInline } = window.SRW;
    const status = el('span', { className: 'srw-key-status' });
    const list = el('div');
    const reasonSelect = el('select', {}, [
      el('option', { value: '', textContent: 'All reasons' }),
      ...Object.entries(UPGRADE_REASON).map(([k, [label]]) => el('option', { value: k, textContent: label })),
      el('option', { value: 'done', textContent: 'Done (replaced)' }),
    ]);
    // The automatic search is a job: switched on/off and timed in Settings > Jobs (v0.155.0); here only what it is set to.
    const autoLine = el('span', { className: 'srw-hint' });
    const refreshBtn = el('button', {
      className: 'srw-btn srw-btn-primary', textContent: 'Find candidates',
      title: 'Rebuild the list from the VR check results and the library files',
      onClick: async () => {
        refreshBtn.disabled = true;
        status.textContent = ' collecting…';
        const r = await window.SRW.runOp('upgrades_refresh');
        refreshBtn.disabled = false;
        status.textContent = r.error ? ` error: ${r.error}`
          : ` found: ${r.vr_2d} 2D-in-VR, ${r.vr_mono} VR mono, ${r.vr_low_quality} low-quality VR, ${r.below_cutoff} below cutoff`;
        load();
      },
    });
    const section = el('details', { className: 'srw-settings-section srw-panel srw-upgrades' }, [
      el('summary', { textContent: 'Upgrade wanted' }),
      el('p', {
        className: 'srw-hint',
        textContent: 'Library files that should be replaced by a better version: 2D files of VR scenes → real VR, '
          + 'monoscopic VR → stereoscopic, VR below 4K / low bitrate → higher resolution (never above 6K / 15 GB), '
          + 'non-VR below the profile cutoff. Only candidates that are actually better are grabbed; after the '
          + 'verified import the old file goes to the recycle bin. "Dry run" shows what would be grabbed.',
      }),
      el('div', { className: 'srw-toolbar' }, [
        refreshBtn,
        reasonSelect, status,
      ]),
      el('p', { className: 'srw-hint' }, [autoLine, ' ', el('a', { href: '/plugins/stash-release-manager/settings#jobs', textContent: 'Change in Settings > Jobs' })]),
      list,
    ]);

    let candidates = [];
    let view = null;
    async function load() {
      const r = await window.SRW.runOp('upgrades_list');
      if (r.error) { status.textContent = ` error: ${r.error}`; return; }
      candidates = r.candidates || [];
      autoLine.textContent = r.enabled
        ? `Automatic search: on, a few candidates (${r.per_run || '20'} per run) at most every ${r.interval_hours || '12'} h.`
        : 'Automatic search: off.';
      if (!view) {
        view = window.SRW.buildFilteredPagedList({
          filters: ['search'],
          perPage: 30,
          showFilterBarFrom: 10,
          emptyText: 'No upgrade candidates (click "Find candidates").',
          fetchPage: window.SRW.localPageFetcher(() => {
            const want = reasonSelect.value;
            if (want === 'done') return candidates.filter((c) => c.status === 'done');
            return candidates.filter((c) => c.status !== 'done' && (!want || c.reason === want));
          }, { text: (c) => `${c.scene_title} ${c.file_path}` }),
          renderItems: (items) => el('ul', { className: 'srw-release-list' }, items.map(buildRow)),
        });
        list.appendChild(view.node);
        reasonSelect.addEventListener('change', () => view.reset());
      }
      view.reload();
    }

    function fmtGb(bytes) { return bytes ? `${(bytes / 1e9).toFixed(1)} GB` : '?'; }

    function buildRow(c) {
      const [badge, why] = UPGRADE_REASON[c.reason] || [c.reason, ''];
      const sceneHref = `/scenes/${c.scene_id}`;
      const result = el('div', { className: 'srw-hint' });
      const statusText = {
        wanted: c.last_search_at ? `last searched ${c.last_search_at.slice(0, 16)}: ${c.last_search_result || '—'}` : 'not searched yet',
        grabbed: 'upgrade downloading / importing',
        review: 'upgrade imported but unverified -- old file kept, please check',
        done: 'replaced (old file in the recycle bin)',
      }[c.status] || c.status;
      const actions = [];
      if (c.status === 'wanted') {
        const dryBtn = el('button', {
          className: 'srw-btn srw-btn-sm', textContent: 'Dry run', title: 'Search and show what would be grabbed -- nothing is downloaded',
          onClick: async () => {
            dryBtn.disabled = true; result.textContent = 'searching…';
            const r = await window.SRW.runOp('upgrades_search', { id: c.id, dry_run: true });
            dryBtn.disabled = false;
            if (r.error) { result.textContent = `error: ${r.error}`; return; }
            const counts = {};
            (r.candidates || []).forEach((x) => { counts[x.verdict] = (counts[x.verdict] || 0) + 1; });
            const summary = Object.entries(counts).map(([k, n]) => `${n}× ${k}`).join(', ');
            result.textContent = r.would_grab
              ? `would grab: ${r.would_grab.title} (${fmtGb(r.would_grab.size)}, ${r.would_grab.indexer}) — ${summary}`
              : `nothing better found (${r.result})${summary ? ` — ${summary}` : ''}`;
          },
        });
        const grabBtn = el('button', {
          className: 'srw-btn srw-btn-sm srw-btn-primary', textContent: 'Search now',
          onClick: (e) => confirmInline(e.currentTarget,
            `Search an upgrade for "${c.scene_title}" and download it? After the verified import the current file `
            + 'goes to the recycle bin.', async () => {
              const r = await window.SRW.runOp('upgrades_search', { id: c.id, dry_run: false });
              if (r.error) { window.SRW.notify('Upgrade', r.error, 'error'); return; }
              window.SRW.notify('Upgrade', r.grab ? `Grabbed: ${r.grab.title}` : `Nothing better found (${r.result}).`,
                r.grab ? 'success' : 'info');
              load();
            }),
        });
        const dismissBtn = el('button', {
          className: 'srw-btn srw-btn-sm', textContent: 'Dismiss', title: 'Keep the current file, don\'t search an upgrade',
          onClick: async () => {
            const res = await window.SRW.runOp('upgrades_dismiss', { id: c.id });
            if (res && res.error) { window.SRW.notify('Dismiss', `Dismiss failed: ${res.error}`, 'error'); return; }
            const at = candidates.findIndex((x) => x.id === c.id);
            candidates = candidates.filter((x) => x.id !== c.id);
            view.reload();
            window.SRW.dismissedNotice(`Dismissed: ${c.scene_title || 'upgrade'}`, 'The current file is kept, no upgrade search.',
              () => window.SRW.runOp('upgrades_restore', { id: c.id, status: res.previous || 'wanted' }),
              () => { candidates.splice(at < 0 ? candidates.length : at, 0, c); view.reload(); });
          },
        });
        // Own query (v0.106.0), same panel as the wishlist rows: type a search, pick a result, Grab. The grab is recorded as an
        // upgrade of THIS file (old file to the recycle bin after the verified import).
        const searchSlot = el('span', { className: 'srw-search-actions' });
        window.SRW.getSearchCapabilities().then((caps) => {
          const sources = (caps && caps.manual_sources) || [];
          if (!sources.length) return;
          const asRelease = { provider: c.provider, scene_stashdb_id: c.provider_scene_id, title: c.scene_title,
            studio_name: c.studio_name, scene_date: c.scene_date };
          searchSlot.appendChild(window.SRW.buildManualSearch(asRelease, sources, caps.backend, () => load(), {
            host: () => rowLi,
            grab: (candidate) => window.SRW.runOp('upgrades_manual_grab', { id: c.id, candidate }),
          }));
        });
        actions.push(dryBtn, grabBtn, searchSlot, dismissBtn);
      }
      const mbps = c.cur_mbps ? `, ${Math.round(c.cur_mbps)} Mbit/s` : '';
      const rowLi = el('li', { className: 'srw-release-item' }, [
        el('span', { className: `srw-upgrade-reason srw-upgrade-reason-${c.reason}`, textContent: badge, title: why }),
        el('a', { className: 'srw-vr-thumb-link', href: sceneHref, target: '_blank', rel: 'noopener noreferrer' },
          [el('img', { className: 'srw-vr-thumb', src: `/scene/${c.scene_id}/screenshot`, loading: 'lazy', alt: '' })]),
        el('div', {}, [
          el('a', { className: 'srw-release-title', href: sceneHref, target: '_blank', rel: 'noopener noreferrer',
            textContent: c.scene_title || `Scene ${c.scene_id}` }),
          el('div', { className: 'srw-hint', textContent: `now: ${c.cur_width}×${c.cur_height}, ${fmtGb(c.cur_size)}${mbps} — ${statusText}` }),
          el('div', { className: 'srw-maintenance-file-path', textContent: c.file_path }),
          result,
        ]),
        el('div', { className: 'srw-upgrade-actions' }, actions),
      ]);
      return rowLi;
    }

    load();
    return window.SRW.rememberOpen(section, 'maintenance:upgrade-wanted', false);
  }


  // ==================== Compare scenes (2026-09-30, api/scene_compare.py) ====================
  //
  // Two Stash scenes side by side, both playable, to decide visually which one carries the right provider id
  // (or which file to keep). Pairs come from the Organize name-conflict list, scripts or "Add pair" below.
  // The only write is "Remove this ID" (backed up to compare_removed_ids.jsonl + History).
  function buildCompareSection(part = 'both') {
    const { el, confirmInline } = window.SRW;
    const status = el('span', { className: 'srw-key-status' });
    const list = el('div');
    const viewer = el('div', { className: 'srw-cmp-viewer' });
    // Filter buttons (2026-10-02): Open + Later is the default so a "later" pair never disappears; the counts show what is hidden.
    const FILTERS = [['active', 'Open + Later'], ['pending', 'Open'], ['skipped', 'Later'], ['done', 'Done'], ['', 'All']];
    let statusFilter = 'active';
    const filterBtns = FILTERS.map(([value, label]) => {
      const b = el('button', { className: 'srw-btn srw-btn-sm', textContent: label });
      b.addEventListener('click', () => { statusFilter = value; paintFilters(); applyFilter(); });
      return { value, label, b };
    });
    const paintFilters = () => filterBtns.forEach((f) => f.b.classList.toggle('srw-btn-primary', f.value === statusFilter));
    const statusBar = el('span', {}, filterBtns.map((f) => f.b));
    const aIn = el('input', { type: 'text', placeholder: 'Scene A id', style: 'width: 6rem' });
    const bIn = el('input', { type: 'text', placeholder: 'Scene B id', style: 'width: 6rem' });
    const labelIn = el('input', { type: 'text', placeholder: 'Label (optional)', style: 'width: 12rem' });
    const addBtn = el('button', {
      className: 'srw-btn', textContent: 'Add pair',
      onClick: async () => {
        if (!aIn.value.trim() || !bIn.value.trim()) { window.SRW.notify('Compare scenes', 'Enter both scene ids.', 'warning'); return; }
        const r = await window.SRW.runOp('compare_add', { a: aIn.value.trim(), b: bIn.value.trim(), label: labelIn.value.trim() });
        if (r.error) { window.SRW.notify('Compare scenes', r.error, 'error'); return; }
        window.SRW.notify('Compare scenes', r.added ? 'Pair added.' : 'That pair already exists.', r.added ? 'success' : 'info');
        aIn.value = ''; bIn.value = ''; labelIn.value = '';
        load();
      },
    });
    const refreshBtn = el('button', { className: 'srw-btn', textContent: 'Refresh', onClick: () => load() });
    const closeBtn = el('button', {
      className: 'srw-btn', textContent: 'Close resolved pairs',
      title: 'Open pairs where one scene is gone, or both scenes have different provider ids and neither is marked for review',
      onClick: async () => {
        const dry = await window.SRW.runOp('compare_close_resolved', { dry_run: true });
        if (dry.error) { window.SRW.notify('Compare scenes', dry.error, 'error'); return; }
        if (!(dry.pairs || []).length) { window.SRW.notify('Compare scenes', 'No resolved open pairs.', 'info'); return; }
        window.SRW.confirmInline(closeBtn, `Close ${dry.pairs.length} resolved pair(s)? ${dry.pairs.slice(0, 4).map((x) => x.label.slice(0, 40)).join(' | ')}${dry.pairs.length > 4 ? ' …' : ''}`, async () => {
          const r = await window.SRW.runOp('compare_close_resolved', { dry_run: false });
          if (r.error) { window.SRW.notify('Compare scenes', r.error, 'error'); return; }
          window.SRW.notify('Compare scenes', `${r.closed} pair(s) closed.`, 'success');
          load();
        });
      },
    });
    const aliasPanel = buildAliasPanel();
    const section = el('details', { className: 'srw-settings-section srw-panel srw-compare' }, [
      el('summary', { textContent: 'Compare scenes' }),
      el('p', {
        className: 'srw-hint',
        textContent: 'Two scenes side by side -- play both, compare file and metadata and what each provider id really '
          + 'points to, then remove the wrong ID or mark the pair done. Nothing is removed without a confirmation; the '
          + 'scene is saved to compare_removed_ids.jsonl and a History entry is written first.',
      }),
      el('div', { className: 'srw-toolbar' }, [statusBar, refreshBtn, closeBtn, status]),
      el('div', { className: 'srw-toolbar' }, [aIn, bIn, labelIn, addBtn]),
      list,
      viewer,
      aliasPanel,
    ]);

    let pairs = [];
    let view = null;
    let openId = null;

    let allPairs = [];
    const applyFilter = () => {
      pairs = allPairs.filter((p) => (statusFilter === 'active' ? p.status !== 'done' : !statusFilter || p.status === statusFilter));
      const n = (st) => allPairs.filter((p) => p.status === st).length;
      const counts = { active: n('pending') + n('skipped'), pending: n('pending'), skipped: n('skipped'), done: n('done'), '': allPairs.length };
      filterBtns.forEach((f) => { f.b.textContent = `${f.label} (${counts[f.value]})`; });
      status.textContent = ` ${pairs.length} pair(s)`;
      if (view) view.reload();
    };

    async function load() {
      status.textContent = ' loading…';
      const r = await window.SRW.runOp('compare_pairs', { status: '' });
      if (r.error) { status.textContent = ` error: ${r.error}`; return; }
      allPairs = r.pairs || [];
      if (!view) {
        view = window.SRW.buildFilteredPagedList({
          filters: ['search'], perPage: 20, showFilterBarFrom: 10,
          emptyText: 'No pairs here. Add one above (scene ids from the Stash scene URLs).',
          fetchPage: window.SRW.localPageFetcher(() => pairs, {
            text: (p) => `${p.label || ''} ${p.a ? p.a.title : ''} ${p.b ? p.b.title : ''} ${p.a ? p.a.studio : ''} ${p.scene_a} ${p.scene_b}`,
          }),
          renderItems: (items) => el('ul', { className: 'srw-release-list' }, items.map(buildPairRow)),
        });
        list.appendChild(view.node);
      }
      paintFilters();
      applyFilter();
    }

    function sceneSummary(tag, s, id) {
      if (!s) return `${tag}: #${id} (not in Stash)`;
      const res = s.height ? `${s.height}p` : '?';
      return `${tag}: #${s.id} ${s.title || '(no title)'} — ${res}, ${s.minutes} min, ${s.mb} MB`;
    }

    function buildPairRow(p) {
      const badge = { pending: 'open', skipped: 'later', done: 'done' }[p.status] || p.status;
      return el('li', { className: 'srw-release-item' }, [
        el('span', { className: `srw-upgrade-reason srw-upgrade-reason-${p.status === 'done' ? 'done' : 'vr_2d'}`, textContent: badge }),
        el('div', {}, [
          el('div', { className: 'srw-release-title', textContent: p.label || (p.a && p.a.title) || `Pair ${p.id}` }),
          el('div', { className: 'srw-hint', textContent: sceneSummary('A', p.a, p.scene_a) }),
          el('div', { className: 'srw-hint', textContent: sceneSummary('B', p.b, p.scene_b) }),
          p.note ? el('div', { className: 'srw-hint', textContent: `note: ${p.note}` }) : null,
        ]),
        el('button', { className: 'srw-btn srw-btn-sm srw-btn-primary', textContent: 'Compare', onClick: () => openPair(p.id) }),
      ]);
    }

    // After a change: pairs reload both scenes, the review view (p.refresh) reloads its single scene.
    const reopen = (p) => (p.refresh ? p.refresh() : openPair(p.id));
    const fmtDur = (s) => { if (!s) return '?'; const m = Math.floor(s / 60); return `${m}:${String(Math.round(s % 60)).padStart(2, '0')} min`; };
    const fmtMb = (b) => (b ? (b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : `${Math.round(b / 1e6)} MB`) : '?');

    async function openPair(id) {
      openId = id;
      viewer.replaceChildren(el('p', { className: 'srw-hint', textContent: 'Loading both scenes and the provider entries…' }));
      viewer.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const p = await window.SRW.runOp('compare_pair', { id });
      if (openId !== id) return;
      if (p.error) { viewer.replaceChildren(el('p', { className: 'srw-hint', textContent: `error: ${p.error}` })); return; }
      renderPair(p);
    }

    function renderPair(p) {
      const videos = [];
      const cols = ['a', 'b'].map((side) => buildColumn(p, side, p[side], videos));
      const dist = p.phash_distance;
      const distText = dist == null ? '' : dist <= 8
        ? ` Both files look like the SAME video (phash distance ${dist}).`
        : ` The files are DIFFERENT videos (phash distance ${dist}).`;
      const sharedText = p.shared_ids && p.shared_ids.length
        ? `Both scenes carry the same provider id (${p.shared_ids.map((x) => x.slice(0, 8)).join(', ')}).` : 'The scenes carry different provider ids.';
      const audio = el('select', { title: 'Which scene plays sound' }, [
        el('option', { value: 'a', textContent: 'Sound: A' }), el('option', { value: 'b', textContent: 'Sound: B' }),
        el('option', { value: '', textContent: 'Sound: off' }),
      ]);
      const applyAudio = () => videos.forEach((pl, i) => { pl.video.muted = audio.value !== ['a', 'b'][i]; });
      audio.addEventListener('change', applyAudio);
      applyAudio();
      const each = (fn) => videos.forEach((pl) => { try { fn(pl); } catch (e) { /* not ready yet */ } });
      const playAll = () => each((pl) => { const pr = pl.video.play(); if (pr && pr.catch) pr.catch(() => { /* autoplay policy: needs a user gesture */ }); });
      const bar = el('div', { className: 'srw-toolbar srw-cmp-bar' }, [
        el('button', { className: 'srw-btn srw-btn-sm', textContent: '▶ both', onClick: playAll }),
        el('button', { className: 'srw-btn srw-btn-sm', textContent: '⏸ both', onClick: () => each((pl) => pl.video.pause()) }),
        el('button', { className: 'srw-btn srw-btn-sm', textContent: '−10 s', onClick: () => each((pl) => pl.seek(pl.time() - 10)) }),
        el('button', { className: 'srw-btn srw-btn-sm', textContent: '+10 s', onClick: () => each((pl) => pl.seek(pl.time() + 10)) }),
        el('button', { className: 'srw-btn srw-btn-sm', textContent: '+60 s', onClick: () => each((pl) => pl.seek(pl.time() + 60)) }),
        el('button', { className: 'srw-btn srw-btn-sm', title: 'Jump both to the same position (the time of A)',
          textContent: 'B to A’s time', onClick: () => { if (videos[1] && videos[0]) videos[1].seek(videos[0].time()); } }),
        el('button', { className: 'srw-btn srw-btn-sm', title: 'Both to the middle of their own length -- the quick way to compare different cuts',
          textContent: 'Jump to 50 %', onClick: () => each((pl) => pl.seek(pl.total * 0.5)) }),
        audio,
      ]);
      const setStatus = async (s) => {
        const r = await window.SRW.runOp('compare_status', { id: p.id, status: s });
        if (r.error) { window.SRW.notify('Compare scenes', r.error, 'error'); return; }
        window.SRW.notify('Compare scenes', s === 'done' ? 'Marked done.' : s === 'skipped' ? 'Put on "later".' : 'Reopened.', 'success');
        viewer.replaceChildren(); openId = null; load();
      };
      const footer = el('div', { className: 'srw-toolbar' }, [
        el('button', { className: 'srw-btn srw-btn-primary', textContent: 'Done', onClick: () => setStatus('done') }),
        el('button', { className: 'srw-btn', textContent: 'Later', onClick: () => setStatus('skipped') }),
        el('button', { className: 'srw-btn', textContent: 'Reopen', onClick: () => setStatus('pending') }),
        el('button', { className: 'srw-btn', textContent: 'Close', onClick: () => { viewer.replaceChildren(); openId = null; } }),
      ]);
      viewer.replaceChildren(
        el('h4', { textContent: p.label || `Pair ${p.id}` }),
        el('p', { className: 'srw-hint', textContent: `${sharedText}${distText}` }),
        bar,
        el('div', { className: 'srw-cmp-grid' }, cols),
        footer,
      );
    }

    function buildColumn(p, side, s, videos) {
      const tag = side === 'review' ? 'Scene' : side.toUpperCase();
      if (!s || s.missing) return el('div', { className: 'srw-cmp-col' }, [el('h5', { textContent: `${tag}: scene not found` })]);
      const f = (s.files && s.files[0]) || {};
      // Containers a browser can not play natively go through Stash's transcode from the start; seeking in a
      // transcoded stream = reload from a start time (the native scrubber only knows the buffered part then).
      const ext = ((f.path || '').split('.').pop() || '').toLowerCase();
      const NATIVE_OK = ['mp4', 'm4v', 'webm', 'mov', 'mkv'];
      const total = f.duration || 0;
      // No poster: Stash's screenshot is the provider's cover image, not a frame of this file.
      const video = el('video', { className: 'srw-cmp-video', controls: 'controls', preload: 'metadata', playsinline: 'playsinline' });
      const pl = {
        video, total, transcoded: false, offset: 0,
        url(start) { return `/scene/${s.id}/stream.mp4?resolution=STANDARD${start ? `&start=${Math.floor(start)}` : ''}`; },
        time() { return this.offset + (video.currentTime || 0); },
        seek(to) {
          const tt = Math.max(0, Math.min(this.total || to, to));
          if (!this.transcoded) { video.currentTime = tt; return; }
          const wasPlaying = !video.paused;
          const wasMuted = video.muted;
          this.offset = tt; video.src = this.url(tt); video.muted = wasMuted; video.load();
          if (wasPlaying) { const pr = video.play(); if (pr && pr.catch) pr.catch(() => {}); }
        },
        useTranscode(msg) {
          this.transcoded = true; this.offset = 0; video.src = this.url(0); video.load();
          note.textContent = msg || 'Played through Stash’s transcode (lower quality). Use the buttons above to jump; the player’s own bar only covers what is loaded.';
        },
      };
      videos.push(pl);
      const note = el('div', { className: 'srw-hint' });
      if (NATIVE_OK.includes(ext)) {
        video.src = `/scene/${s.id}/stream`;
        video.addEventListener('error', () => {
          if (pl.transcoded) { note.textContent = 'Stash’s transcode failed too -- open the scene in Stash.'; return; }
          note.replaceChildren('The browser can not play this file directly. ',
            el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Play through Stash’s transcode', onClick: () => pl.useTranscode() }));
        });
      } else {
        pl.useTranscode(`.${ext || '?'} is not playable in a browser -- played through Stash’s transcode (lower quality, starts slower). `
          + 'Use the buttons above to jump; the player’s own bar only covers what is loaded.');
        video.addEventListener('error', () => { note.textContent = 'Stash’s transcode failed -- open the scene in Stash.'; });
      }
      const rows = [
        ['Title', s.title || '(none)'], ['Date', s.date || '—'], ['Studio', s.studio || '—'],
        ['Performers', (s.performers || []).join(', ') || '—'],
        ['Length', fmtDur(f.duration)], ['Resolution', f.width ? `${f.width}×${f.height}` : '?'],
        ['Bitrate', f.bit_rate ? `${(f.bit_rate / 1e6).toFixed(1)} Mbit/s` : '?'], ['Size', fmtMb(f.size)],
        ['Codec', f.codec ? `${f.codec}${f.fps ? `, ${Math.round(f.fps)} fps` : ''}` : '?'],
        ['File', (s.files || []).map((x) => x.path).join('\n') || '—'],
        ['Markers', (s.markers || []).length ? s.markers.map((m) => `${Math.round(m.seconds)}s ${m.tag || ''}`).join(', ') : '0'],
        ['Rating / plays', `${s.rating100 == null ? '—' : s.rating100} / ${s.plays || 0} plays, O ${s.o || 0}`],
        ['Tags', `${(s.tags || []).length}${(s.tags || []).length ? `: ${s.tags.slice(0, 8).join(', ')}${s.tags.length > 8 ? '…' : ''}` : ''}`],
        ['Group', (s.groups || []).join(', ') || '—'],
      ];
      const table = el('table', { className: 'srw-cmp-table' }, rows.map(([k, v]) =>
        el('tr', {}, [el('th', { textContent: k }), el('td', { className: k === 'File' ? 'srw-maintenance-file-path' : '', textContent: v })])));
      const ids = (s.stash_ids || []).length ? s.stash_ids.map((i) => {
        const ps = i.provider_scene;
        const info = !ps ? 'not found at the provider' : ps.error ? `lookup failed: ${ps.error}`
          : `“${ps.title || '?'}” · ${ps.date || '?'} · ${ps.studio || '?'} · ${(ps.performers || []).join(', ') || '—'}`;
        const rm = el('button', { className: 'srw-btn srw-btn-sm srw-btn-danger', textContent: `Remove this ID from ${tag}` });
        rm.addEventListener('click', (e) => confirmInline(e.currentTarget,
          `Remove ${i.provider || 'this'} id ${i.stash_id.slice(0, 8)}… from scene #${s.id}? The scene stays as it is; the id is saved to a backup file.`,
          async () => {
            const r = await window.SRW.runOp('compare_remove_id', { id: p.id, scene_id: s.id, stash_id: i.stash_id });
            if (r.error) { window.SRW.notify('Compare scenes', r.error, 'error'); return; }
            window.SRW.notify('Compare scenes', `ID removed from scene #${s.id}.`, 'success');
            reopen(p);
          }, { danger: true }));
        return el('div', { className: 'srw-cmp-id' }, [
          el('div', {}, [
            el('strong', { textContent: `${i.provider || i.endpoint} ` }),
            i.url ? el('a', { href: i.url, target: '_blank', rel: 'noopener noreferrer', textContent: `${i.stash_id.slice(0, 8)}…` }) : i.stash_id.slice(0, 8),
          ]),
          el('div', { className: 'srw-hint', textContent: info }),
          rm,
        ]);
      }) : [el('div', { className: 'srw-hint', textContent: 'No provider id on this scene.' })];
      const wrap = el('div', { className: 'srw-cmp-videowrap', style: 'position: relative' }, [video]);
      return el('div', { className: 'srw-cmp-col' }, [
        el('h5', {}, [`${tag}: `, el('a', { href: `/scenes/${s.id}`, target: '_blank', rel: 'noopener noreferrer', textContent: `#${s.id} open in Stash` })]),
        wrap, note, buildFaceHint(s), buildFramesStrip(s), table,
        el('div', { className: 'srw-cmp-ids' }, ids),
        (s.stash_ids || []).length ? null : buildIdentifyPanel(p, s, tag),
      ]);
    }

    // Alias provider ids: a StashDB scene whose video is owned as ANOTHER local scene (the producer sold one video under two
    // titles and the two local scenes were merged) counts as "in the library" -- so it is not listed as missing in
    // "Missing from Catalog", not wishlisted/re-downloaded, and counts for the completeness ratio (api/scene_alias.py).
    function buildAliasPanel() {
      const { el, confirmInline } = window.SRW;
      const listEl = el('div');
      const aIn = el('input', { type: 'text', placeholder: 'StashDB scene id (uuid)', style: 'width: 22rem' });
      const lIn = el('input', { type: 'text', placeholder: 'Local scene id', style: 'width: 7rem' });
      const nIn = el('input', { type: 'text', placeholder: 'Note (optional)', style: 'width: 14rem' });
      const addBtn = el('button', { className: 'srw-btn', textContent: 'Add alias', onClick: async () => {
        const r = await window.SRW.runOp('compare_alias_add', { alias_scene_id: aIn.value.trim(), local_scene_id: lIn.value.trim(), note: nIn.value.trim() });
        if (r.error) { window.SRW.notify('Alias ids', r.error, 'error'); return; }
        window.SRW.notify('Alias ids', 'Alias recorded: that StashDB scene now counts as in the library.', 'success');
        aIn.value = ''; lIn.value = ''; nIn.value = ''; load();
      } });
      async function load() {
        const r = await window.SRW.runOp('compare_aliases');
        if (r.error) { listEl.textContent = `error: ${r.error}`; return; }
        if (!(r.aliases || []).length) { listEl.replaceChildren(el('div', { className: 'srw-hint', textContent: 'No aliases yet.' })); return; }
        listEl.replaceChildren(...r.aliases.map((a) => {
          const rm = el('button', { className: 'srw-btn srw-btn-sm srw-btn-danger', textContent: 'Remove' });
          rm.addEventListener('click', (e) => confirmInline(e.currentTarget, 'Remove this alias? That StashDB scene is listed as missing again.', async () => {
            await window.SRW.runOp('compare_alias_remove', { row_id: a.id }); load();
          }, { danger: true }));
          const isDup = a.kind === 'duplicate_entry';
          const resolvable = isDup && a.status !== 'open';
          const statusText = { open: 'watching for a StashDB merge', merged: 'StashDB merged it', gone: 'removed on StashDB' }[a.status] || a.status;
          const resolveBtn = resolvable ? el('button', { className: 'srw-btn srw-btn-primary', textContent: 'Resolve',
            title: 'Use the StashDB data again and take this entry off the list',
            onClick: async (e) => {
              const btn = e.currentTarget;
              const prev = await window.SRW.runOp('compare_alias_resolve_preview', { row_id: a.id });
              if (prev.error) { window.SRW.notify('Duplicate entry', prev.error, 'error'); return; }
              confirmInline(btn, prev.text, async () => {
                const done = await window.SRW.runOp('compare_alias_resolve', { row_id: a.id });
                if (done.error) { window.SRW.notify('Duplicate entry', done.error, 'error'); return; }
                window.SRW.dismissedNotice('StashDB duplicate entry resolved', `Backup: ${done.backup}`,
                  () => window.SRW.runOp('compare_alias_resolve_undo', { applied: done.applied, row: done.row }), () => load());
                load();
              });
            } }) : null;
          return el('div', { className: 'srw-cmp-id' }, [
            el('div', {}, [
              isDup ? el('span', { className: `srw-film-class ${a.status === 'open' ? 'srw-film-class-unsure' : 'srw-film-class-sure'}`, textContent: 'StashDB duplicate',
                title: 'StashDB lists this scene twice under the same title. It is watched; when StashDB merges the entries you can resolve it here.' }) : null,
              isDup ? el('span', { className: 'srw-hint', textContent: ` ${statusText} ` }) : null,
              el('a', { href: `https://stashdb.org/scenes/${a.alias_scene_id}`, target: '_blank', rel: 'noopener noreferrer',
                textContent: a.title || a.alias_scene_id }), ` ${a.scene_date || ''} → local scene `,
              el('a', { href: `/scenes/${a.local_scene_id}`, target: '_blank', rel: 'noopener noreferrer', textContent: `#${a.local_scene_id} ${a.local_title || ''}` })].filter(Boolean)),
            el('div', { className: 'srw-hint', textContent: a.note || '' }), resolveBtn, rm].filter(Boolean));
        }));
      }
      const det = el('details', { className: 'srw-panel' }, [
        el('summary', { textContent: 'Alias ids (same video under a second title) and StashDB duplicates' }),
        el('p', { className: 'srw-hint', textContent: 'A provider scene listed here counts as owned although no local scene carries its id.' }),
        el('div', { className: 'srw-toolbar' }, [aIn, lIn, nIn, addBtn]), listEl]);
      det.addEventListener('toggle', () => { if (det.open) load(); });
      return det;
    }

    // Real frames cut from the file (5 positions) -- the Stash screenshot/cover is the provider's image and says nothing about the file.
    function buildFramesStrip(s) {
      const strip = el('div', { className: 'srw-cmp-frames' });
      const btn = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Show frames from the file',
        title: 'Five frames cut from THIS file (10/30/50/70/90 %). Stash’s own screenshot is only the provider’s cover image.',
        onClick: async () => {
          btn.disabled = true; strip.textContent = 'cutting frames…';
          const r = await window.SRW.runOp('compare_frames', { scene_id: s.id });
          btn.disabled = false;
          if (r.error) { strip.textContent = `error: ${r.error}`; return; }
          strip.replaceChildren(...(r.frames || []).map((f) => el('figure', { className: 'srw-cmp-frame' }, [
            f.data_url ? el('img', { src: f.data_url, alt: '' }) : el('div', { className: 'srw-hint', textContent: 'no frame' }),
            el('figcaption', { className: 'srw-hint', textContent: `${Math.floor(f.t / 60)}:${String(f.t % 60).padStart(2, '0')}` }),
          ])));
        } });
      return el('div', {}, [el('div', { className: 'srw-toolbar' }, [btn, el('span', { className: 'srw-hint', textContent: 'Stash’s cover image is the provider’s picture, not from this file.' })]), strip]);
    }

    // For a scene without a provider id: find out what it really is (exact fingerprints, then a name search), put
    // the provider's id + metadata on it, or clear what a wrong match left behind and mark it for review.
    function buildIdentifyPanel(p, s, tag) {
      const out = el('div', { className: 'srw-cmp-identify-out' });
      const stem = ((s.files && s.files[0] && s.files[0].path) || '').split('/').pop()
        .replace(/\.[a-z0-9]{2,4}$/i, '').replace(/\s*[-\[(]?\s*(WEBDL|WEBRip|HDTV|BluRay)[^\])]*[\])]?/i, '')
        .replace(/\s*-?\s*\d{4}-\d{2}-\d{2}/, '').replace(/[._]+/g, ' ').trim();
      const q = el('input', { type: 'text', value: stem, style: 'width: 18rem', title: 'Search text (pre-filled from the file name)' });

      const renderCandidates = (items, fingerprint) => {
        if (!items.length) { out.replaceChildren(el('div', { className: 'srw-hint', textContent: fingerprint ? 'No fingerprint match at StashDB or ThePornDB.' : 'No results.' })); return; }
        out.replaceChildren(el('div', { className: 'srw-hint', textContent: fingerprint ? (items.some((c) => c.provider === 'theporndb')
          ? 'Fingerprint match at ThePornDB (its hash lookup is similarity-based, NOT guaranteed exact -- compare by playing before using it):'
          : 'Exact fingerprint match at StashDB:') : 'Name search results (not verified -- compare by playing):' }),
          ...items.map((c) => {
            const useBtn = el('button', { className: 'srw-btn srw-btn-sm srw-btn-primary', textContent: `Use this for ${tag}` });
            if (c.held_by_scene) { useBtn.disabled = true; useBtn.textContent = `held by scene #${c.held_by_scene}`; }
            useBtn.addEventListener('click', (e) => window.SRW.confirmInline(e.currentTarget,
              `Put ${c.provider} scene “${c.title || '?'}” (${c.date || '?'}) on scene #${s.id}? Title, date, studio, performers and tags are replaced; the old state is saved to the backup file.`,
              async () => {
                const r = await window.SRW.runOp('compare_adopt', { id: p.id, scene_id: s.id, provider: c.provider, provider_id: c.provider_id });
                if (r.error) { window.SRW.notify('Compare scenes', r.error, 'error'); return; }
                window.SRW.notify('Compare scenes', `Scene #${s.id} is now “${r.scene.title}”. The next Organize run renames the file.${r.review_closed ? ' It left the Review list.' : ''}`, 'success');
                reopen(p);
              }));
            return el('div', { className: 'srw-cmp-id' }, [
              el('div', {}, [el('strong', { textContent: `${c.provider} ` }),
                c.url ? el('a', { href: c.url, target: '_blank', rel: 'noopener noreferrer', textContent: c.title || c.provider_id }) : (c.title || c.provider_id)]),
              el('div', { className: 'srw-hint', textContent: `${c.date || '?'} · ${c.studio || '?'} · ${(c.performers || []).join(', ') || '—'}${c.minutes ? ` · ${c.minutes} min` : ''}` }),
              useBtn,
            ]);
          }));
      };

      const idBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Identify by fingerprint',
        title: 'Looks the file\'s hashes up at StashDB (then ThePornDB) -- exact matches only',
        onClick: async () => {
          idBtn.disabled = true; out.textContent = 'looking up…';
          const r = await window.SRW.runOp('compare_identify', { id: p.id, scene_id: s.id });
          idBtn.disabled = false;
          if (r.error) { out.textContent = `error: ${r.error}`; return; }
          renderCandidates(r.matches || [], true);
        } });
      const searchBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Search by name',
        onClick: async () => {
          searchBtn.disabled = true; out.textContent = 'searching…';
          const r = await window.SRW.runOp('compare_search', { id: p.id, scene_id: s.id, query: q.value });
          searchBtn.disabled = false;
          if (r.error) { out.textContent = `error: ${r.error}`; return; }
          const errs = (r.results || []).filter((x) => x.error);
          renderCandidates((r.results || []).filter((x) => !x.error), false);
          errs.forEach((x) => out.appendChild(el('div', { className: 'srw-hint', textContent: `${x.provider}: ${x.error}` })));
        } });
      const resetBtn = el('button', { className: 'srw-btn srw-btn-sm srw-btn-danger', textContent: 'Reset metadata' });
      resetBtn.addEventListener('click', (e) => window.SRW.confirmInline(e.currentTarget,
        `Clear title, date, studio, performers, URLs and tags of scene #${s.id} and mark it “Review: Identify Scene”? Markers, rating and play history stay; the old state is saved to the backup file.`,
        async () => {
          const r = await window.SRW.runOp('compare_reset_metadata', { id: p.id, scene_id: s.id });
          if (r.error) { window.SRW.notify('Compare scenes', r.error, 'error'); return; }
          window.SRW.notify('Compare scenes', `Scene #${s.id} reset and marked for review.`, 'success');
          reopen(p);
        }, { danger: true }));
      // Stash's own scrapers (AdultEmpire, IAFD, data18 ...): one request through Stash, results shown, nothing applied unasked.
      const scraperSel = el('select', { title: 'Scraper installed in Stash' });
      window.SRW.runOp('compare_scrapers').then((r) => (r.scrapers || []).forEach((sc) => scraperSel.appendChild(
        el('option', { value: sc.id, textContent: `${sc.name}${sc.name_search ? ' (title)' : ''}${sc.url ? ' (URL)' : ''}` }))));
      const sq = el('input', { type: 'text', value: stem, style: 'width: 18rem', placeholder: 'title, or a page URL (IAFD, data18, AdultEmpire ...)' });
      // IAFD and data18 read a page URL only; the text field says so for the selected scraper.
      const nameSearch = {};
      window.SRW.runOp('compare_scrapers').then((r) => (r.scrapers || []).forEach((sc) => { nameSearch[sc.id] = sc.name_search; }));
      scraperSel.addEventListener('change', () => {
        sq.placeholder = nameSearch[scraperSel.value] === false ? 'page URL of the scene (this scraper has no title search)' : 'title, or a page URL';
      });
      const sBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Search via Stash scraper', onClick: async () => {
        sBtn.disabled = true; out.textContent = 'asking the scraper…';
        const r = await window.SRW.runOp('compare_scrape', { scraper_id: scraperSel.value, query: sq.value });
        sBtn.disabled = false;
        if (r.error) { out.textContent = `error: ${r.error}`; return; }
        if (!(r.results || []).length) { out.textContent = 'The scraper found nothing.'; return; }
        out.replaceChildren(el('div', { className: 'srw-hint', textContent: 'Scraper results (no provider id -- "Apply" only fills title/date/details/URL):' }),
          ...r.results.map((x) => {
            const ap = el('button', { className: 'srw-btn srw-btn-sm', textContent: `Apply to ${tag}` });
            ap.addEventListener('click', (e) => window.SRW.confirmInline(e.currentTarget,
              `Put title “${x.title || '?'}”, date, details and URL on scene #${s.id}? Studio and performers stay as they are; the old state is saved.`,
              async () => {
                const r2 = await window.SRW.runOp('compare_apply_scrape', { id: p.id, scene_id: s.id, title: x.title, date: x.date, details: x.details, url: (x.urls || [])[0] });
                if (r2.error) { window.SRW.notify('Compare scenes', r2.error, 'error'); return; }
                window.SRW.notify('Compare scenes', `Scene #${s.id} updated.`, 'success'); reopen(p);
              }));
            return el('div', { className: 'srw-cmp-id' }, [
              el('div', {}, [el('strong', { textContent: x.title || '(no title)' }), ` ${x.date || ''} · ${x.studio || '?'} · ${(x.performers || []).join(', ') || '—'}`]),
              el('div', { className: 'srw-hint', textContent: (x.details || '').slice(0, 220) }),
              (x.urls || [])[0] ? el('a', { href: x.urls[0], target: '_blank', rel: 'noopener noreferrer', textContent: x.urls[0] }) : null, ap]);
          }));
      } });
      return el('div', { className: 'srw-cmp-identify' }, [
        el('div', { className: 'srw-hint', textContent: `${tag} has no provider id -- what is it?` }),
        el('div', { className: 'srw-toolbar' }, [idBtn]),
        el('div', { className: 'srw-toolbar' }, [q, searchBtn]),
        el('div', { className: 'srw-toolbar' }, [scraperSel, sq, sBtn]),
        el('div', { className: 'srw-toolbar' }, [resetBtn]),
        out,
      ]);
    }

    // ===== Review: Identify Scene (2026-10-02): single scenes carrying that Stash tag, same tools as the pairs =====
    const reviewStatus = el('span', { className: 'srw-key-status' });
    const reviewList = el('div');
    const reviewViewer = el('div', { className: 'srw-cmp-viewer' });
    let reviewScenes = [];
    let allReview = [];
    let reviewView = null;
    let reviewOpenId = null;
    // Open = not looked at / still to do; Later = looked at, cannot be resolved now (stays tagged in Stash).
    const REVIEW_FILTERS = [['open', 'Open'], ['later', 'Later'], ['ids', 'Has id'], ['', 'All']];
    let reviewFilter = 'open';
    const reviewFilterBtns = REVIEW_FILTERS.map(([value, label]) => {
      const b = el('button', { className: 'srw-btn srw-btn-sm', textContent: label });
      b.addEventListener('click', () => { reviewFilter = value; applyReviewFilter(); });
      return { value, label, b };
    });
    // Two tabs (2026-10-04): single scenes, and films in the movie folders (their review adds the ThePornDB movie tools below the player).
    // Settings > Content: only the tab of the area that is switched on (a single tab is not shown as a choice).
    const REVIEW_TABS = [['scene', 'Scenes'], ['movie', 'Movies & groups']].filter(([value]) => contentScope === 'both' || (contentScope === 'movies') === (value === 'movie'));
    let reviewTab = REVIEW_TABS[0][0];
    const reviewTabBtns = REVIEW_TABS.map(([value, label]) => {
      const b = el('button', { className: 'srw-btn srw-btn-sm' });
      b.addEventListener('click', () => { reviewTab = value; reviewViewer.replaceChildren(); reviewOpenId = null; applyReviewFilter(); });
      return { value, label, b };
    });
    const applyReviewFilter = () => {
      const inTab = allReview.filter((x) => (x.kind || 'scene') === reviewTab);
      reviewScenes = inTab.filter((x) => (reviewFilter === 'ids' ? x.provider_ids > 0 : (!reviewFilter || x.status === reviewFilter)));
      const n = (st) => inTab.filter((x) => x.status === st).length;
      const counts = { open: n('open'), later: n('later'), ids: inTab.filter((x) => x.provider_ids > 0).length, '': inTab.length };
      reviewFilterBtns.forEach((f) => {
        f.b.textContent = `${f.label} (${counts[f.value]})`;
        f.b.classList.toggle('srw-btn-primary', f.value === reviewFilter);
      });
      reviewTabBtns.forEach((t) => {
        const all = allReview.filter((x) => (x.kind || 'scene') === t.value);
        t.b.textContent = `${t.label} (${all.filter((x) => x.status === 'open').length} open / ${all.length})`;
        t.b.classList.toggle('srw-btn-primary', t.value === reviewTab);
      });
      reviewStatus.textContent = ` ${reviewScenes.length} shown`;
      const identified = inTab.filter((x) => x.provider_ids > 0).length;
      closeIdentifiedBtn.hidden = !identified || reviewTab !== 'scene'; // not for films: an id alone does not finish a film (it needs its Group)
      closeIdentifiedBtn.textContent = `Close the ${identified} identified one(s)`;
      if (reviewView) reviewView.reload();
    };

    // Scenes that were identified some other way (they carry a provider id now) can leave the list in one go: the same "Review done" as in the viewer,
    // one scene after the other (each is backed up first).
    const closeIdentifiedBtn = el('button', { className: 'srw-btn', textContent: 'Close the identified ones', hidden: true,
      title: 'Takes the review tag off every scene of this list that has a provider id now' });
    closeIdentifiedBtn.addEventListener('click', () => {
      const todo = allReview.filter((x) => (x.kind || 'scene') === reviewTab && x.provider_ids > 0);
      window.SRW.confirmInline(closeIdentifiedBtn, `Take the tag “Review: Identify Scene” off ${todo.length} scene(s) that have a provider id? Everything else stays; each state is saved to the backup file first.`, async () => {
        let done = 0;
        for (const x of todo) {
          reviewStatus.textContent = ` closing ${done + 1} / ${todo.length}…`;
          const r = await window.SRW.runOp('compare_review_done', { scene_id: x.id });
          if (r.error) { window.SRW.notify('Review', `#${x.id}: ${r.error}`, 'error'); break; }
          done += 1;
        }
        reviewViewer.replaceChildren(); reviewOpenId = null;
        await loadReview();
        window.SRW.notify('Review', `${done} scene(s) removed from the review list.`, 'success');
      });
    });
    async function loadReview() {
      reviewStatus.textContent = ' loading…';
      const r = await window.SRW.runOp('compare_review_list');
      if (r.error) { reviewStatus.textContent = ` error: ${r.error}`; return; }
      allReview = r.scenes || [];
      if (!reviewView) {
        reviewView = window.SRW.buildFilteredPagedList({
          filters: ['search'], perPage: 20, showFilterBarFrom: 10,
          emptyText: 'No scene here. “Later” holds the cases you looked at and could not resolve yet; the tag is “Review: Identify Scene”.',
          fetchPage: window.SRW.localPageFetcher(() => reviewScenes, {
            text: (x) => `${x.title || ''} ${x.studio || ''} ${x.path || ''} ${x.id} ${(x.performers || []).join(' ')}`,
          }),
          renderItems: (items) => el('ul', { className: 'srw-release-list' }, items.map(buildReviewRow)),
        });
        reviewList.appendChild(reviewView.node);
      }
      applyReviewFilter();
    }

    // File name next to the scene it was given (v0.171.2): for the files that carry a provider id, so a wrong match shows up at a glance.
    const reviewWords = (text) => new Set((text || '').toLowerCase().replace(/\.[a-z0-9]{2,4}$/, '').replace(/\b(webdl|webrip|dvd|bluray|hdtv|\d{3,4}p)\b|\(\d{4}\)|[^a-z0-9 ]+/g, ' ')
      .split(/\s+/).filter((w) => w.length >= 3 && !/^\d+$/.test(w) && !['the', 'and', 'scene', 'part', 'episode', 'with', 'for'].includes(w)));
    function reviewCompare(x) {
      if (!(x.provider_ids > 0)) return null;
      const file = (x.path || '').split('/').pop();
      const a = reviewWords(file);
      const b = reviewWords(x.title);
      const same = [...a].some((w) => b.has(w));
      const meta = [x.title ? x.title : 'no title (id only)', x.date, x.studio].filter(Boolean).join(' — ');
      return el('div', { className: 'srw-review-compare' }, [
        el('span', { className: `srw-upgrade-reason ${same ? 'srw-upgrade-reason-ok' : 'srw-upgrade-reason-vr_low_quality'}`, textContent: !x.title ? 'no metadata' : (same ? 'names share words' : 'names differ') }),
        el('div', { className: 'srw-hint', textContent: `File:  ${file}` }),
        el('div', { className: 'srw-hint', textContent: `Scene: ${meta}` }),
      ]);
    }

    function buildReviewRow(x) {
      return el('li', { className: 'srw-release-item' }, [
        el('span', { className: 'srw-upgrade-reason srw-upgrade-reason-vr_2d', textContent: x.status === 'later' ? 'later' : (x.provider_ids ? 'has id' : 'no id') }),
        el('div', {}, [
          el('div', { className: 'srw-release-title', textContent: x.title || x.path || `Scene ${x.id}` }),
          el('div', { className: 'srw-hint', textContent: `#${x.id} — ${x.height ? `${x.height}p` : '?'}, ${x.minutes == null ? '?' : x.minutes} min, ${x.mb == null ? '?' : x.mb} MB, `
            + `${x.markers} marker(s)${(x.performers || []).length ? `, ${x.performers.join(', ')}` : ''}` }),
          reviewCompare(x),
          el('div', { className: 'srw-hint srw-maintenance-file-path', textContent: x.path || '' }),
        ]),
        el('button', { className: 'srw-btn srw-btn-sm srw-btn-primary', textContent: 'Review', onClick: () => openReview(x.id) }),
      ]);
    }

    async function openReview(id) {
      reviewOpenId = id;
      reviewViewer.replaceChildren(el('p', { className: 'srw-hint', textContent: 'Loading the scene and its provider entries…' }));
      reviewViewer.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const s = await window.SRW.runOp('compare_review_scene', { scene_id: id });
      if (reviewOpenId !== id) return;
      if (s.error) { reviewViewer.replaceChildren(el('p', { className: 'srw-hint', textContent: `error: ${s.error}` })); return; }
      renderReview(s);
    }

    // Film review (2026-10-04): the folder of this scene (all its ungrouped parts), what the earlier ThePornDB runs found, a ThePornDB movie
    // search, and "create Group" -- GROUP data only (studio only when a local one exists), every part of the folder goes in.
    function buildMoviePanel(s) {
      const box = el('div', { className: 'srw-panel', style: 'padding: .6rem; margin-top: .6rem' }, [
        el('h5', { textContent: 'Movie / Group' }), el('div', { className: 'srw-hint', textContent: 'Reading the film folder…' })]);
      window.SRW.runOp('compare_movie_info', { scene_id: s.id }).then((info) => {
        if (info.error) { box.replaceChildren(el('h5', { textContent: 'Movie / Group' }), el('div', { className: 'srw-hint', textContent: info.error })); return; }
        if (info.grouped) {
          // Already in a Group: only its studio is left to decide (e.g. a studio that was linked by a similar name).
          const studioIn = el('input', { type: 'text', placeholder: 'studio name (an existing one is used, otherwise it is created)', style: 'width: 24rem' });
          const finish = (r, text) => {
            if (r.error) { window.SRW.notify('Movies', r.error, 'error'); return; }
            window.SRW.notify('Movies', text, 'success'); reviewViewer.replaceChildren(); reviewOpenId = null; loadReview();
          };
          const setBtn = el('button', { className: 'srw-btn srw-btn-sm srw-btn-primary', textContent: 'Set studio' });
          setBtn.addEventListener('click', (e) => window.SRW.confirmInline(e.currentTarget,
            `Set the studio of the Group(s) to “${studioIn.value}”? A studio of that exact name is used, otherwise it is created.`,
            async () => finish(await window.SRW.runOp('compare_group_studio', { scene_id: s.id, name: studioIn.value }), `Studio set to ${studioIn.value}.`)));
          const clearBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Remove the studio from the Group' });
          clearBtn.addEventListener('click', (e) => window.SRW.confirmInline(e.currentTarget, 'Remove the studio from the Group(s) of this film?',
            async () => finish(await window.SRW.runOp('compare_group_studio', { scene_id: s.id, clear: true }), 'Studio removed from the Group.')));
          box.replaceChildren(el('h5', { textContent: 'Movie / Group' }),
            el('div', { className: 'srw-hint', textContent: `This film is already in ${info.grouped.map((g) => `“${g.name}” (studio: ${g.studio || 'none'})`).join(', ')}.` }),
            el('div', { className: 'srw-toolbar' }, [studioIn, setBtn, clearBtn]));
          return;
        }
        const done = (r) => {
          window.SRW.notify('Movies', `Group “${r.name}” created with ${r.scenes} scene(s)${(r.notes || []).length ? ` (${r.notes.join('; ')})` : ''}.`, 'success');
          reviewViewer.replaceChildren(); reviewOpenId = null; loadReview();
        };
        const candidateRow = (c) => {
          const btn = el('button', { className: 'srw-btn srw-btn-sm srw-btn-primary', textContent: 'Create group from this movie' });
          btn.addEventListener('click', (e) => window.SRW.confirmInline(e.currentTarget,
            `Create the Group “${c.title}” (${c.year || '?'}) from ThePornDB and put ${info.scene_ids.length} scene(s) of this folder in it? Group data only; the studio is set only when a local one exists.`,
            async () => {
              btn.disabled = true;
              const r = await window.SRW.runOp('compare_movie_create', { scene_id: s.id, tpdb_id: c.id });
              btn.disabled = false;
              if (r.error) { window.SRW.notify('Movies', r.error, 'error'); return; }
              done(r);
            }));
          const fit = c.length_ok === undefined ? (c.minutes && info.minutes && Math.abs(c.minutes / info.minutes - 1) <= 0.10) : c.length_ok;
          return el('div', { className: 'srw-cmp-id' }, [
            el('div', {}, [el('strong', { textContent: c.title || '?' }),
              el('span', { className: 'srw-hint', textContent: ` ${c.year || '?'} · ${c.minutes ? `${c.minutes} min` : 'no length'} (folder ${info.minutes} min)${fit ? ' ✓ length matches' : ''} · ${c.site || '?'}${c.from ? ` · ${c.from}` : ''} ` })]),
            btn]);
        };
        const stored = el('div', {}, (info.stored || []).length
          ? [el('div', { className: 'srw-hint', textContent: 'Found by the earlier ThePornDB runs (no new request):' }), ...info.stored.map(candidateRow)]
          : [el('div', { className: 'srw-hint', textContent: 'The earlier ThePornDB runs found nothing for this folder.' })]);
        const sameGroups = el('div', {}, (info.same_title_groups || []).length ? [
          el('div', { className: 'srw-hint', textContent: 'A Group with this title already exists -- put the film into it:' }),
          ...info.same_title_groups.map((g) => {
            const btn = el('button', { className: 'srw-btn srw-btn-sm srw-btn-primary', textContent: 'Add to this group' });
            btn.addEventListener('click', (e) => window.SRW.confirmInline(e.currentTarget,
              `Put ${info.scene_ids.length} scene(s) of this folder into the existing Group “${g.name}”?`,
              async () => {
                btn.disabled = true;
                const r = await window.SRW.runOp('compare_group_add', { scene_id: s.id, group_id: g.id });
                btn.disabled = false;
                if (r.error) { window.SRW.notify('Movies', r.error, 'error'); return; }
                window.SRW.notify('Movies', `Added to the Group “${r.name}”.`, 'success');
                reviewViewer.replaceChildren(); reviewOpenId = null; loadReview();
              }));
            return el('div', { className: 'srw-cmp-id' }, [
              el('div', {}, [el('a', { href: `/groups/${g.id}`, target: '_blank', rel: 'noopener noreferrer', textContent: g.name }),
                el('span', { className: 'srw-hint', textContent: ` ${g.year || '?'} · ${g.minutes ? `${g.minutes} min` : 'no length'} · ${g.scenes} scene(s)${g.studio ? ` · ${g.studio}` : ''}` })]), btn]);
          })] : []);
        const results = el('div');
        const q = el('input', { type: 'text', value: info.title, style: 'width: 20rem', title: 'ThePornDB movie search text (pre-filled from the folder name)' });
        const searchBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Search ThePornDB movies',
          onClick: async () => {
            searchBtn.disabled = true; results.textContent = 'asking ThePornDB…';
            const r = await window.SRW.runOp('compare_movie_search', { scene_id: s.id, q: q.value });
            searchBtn.disabled = false;
            if (r.error) { results.textContent = `error: ${r.error}`; return; }
            if (!(r.results || []).length) { results.textContent = `ThePornDB has no movie for “${r.query}”.`; return; }
            results.replaceChildren(...r.results.map(candidateRow));
          } });
        const nameInput = el('input', { type: 'text', value: info.title, style: 'width: 20rem', title: 'Name of the Group' });
        const manualBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Create group from the folder name' });
        manualBtn.addEventListener('click', (e) => window.SRW.confirmInline(e.currentTarget,
          `Create the Group “${nameInput.value}” without provider data and put ${info.scene_ids.length} scene(s) of this folder in it?`,
          async () => {
            manualBtn.disabled = true;
            const r = await window.SRW.runOp('compare_movie_create_manual', { scene_id: s.id, name: nameInput.value });
            manualBtn.disabled = false;
            if (r.error) { window.SRW.notify('Movies', r.error, 'error'); return; }
            done(r);
          }));
        box.replaceChildren(
          el('h5', { textContent: `Movie / Group — ${info.title}${info.year ? ` (${info.year})` : ''}` }),
          el('div', { className: 'srw-hint srw-maintenance-file-path', textContent: `${info.folder}\n${info.videos.length} video(s), ${info.minutes} min: ${info.videos.map((v) => `${v.file} (${v.minutes})`).join(', ')}` }),
          sameGroups,
          stored,
          el('div', { className: 'srw-toolbar' }, [q, searchBtn]), results,
          el('div', { className: 'srw-toolbar' }, [nameInput, manualBtn]));
      });
      return box;
    }

    function renderReview(s) {
      const videos = [];
      // p.id 0 = "no pair": the backend then checks the review tag instead of pair membership.
      // After an action the list is read again: a scene that was identified has lost its tag and is gone (v0.171.0); otherwise it stays open.
      const col = buildColumn({ id: 0, refresh: async () => { await loadReview(); if (allReview.some((x) => x.id === s.id)) openReview(s.id); else { reviewViewer.replaceChildren(); reviewOpenId = null; } } }, 'review', s, videos);
      const each = (fn) => videos.forEach((pl) => { try { fn(pl); } catch (e) { /* not ready yet */ } });
      const bar = el('div', { className: 'srw-toolbar srw-cmp-bar' }, [
        el('button', { className: 'srw-btn srw-btn-sm', textContent: '▶', onClick: () => each((pl) => { const pr = pl.video.play(); if (pr && pr.catch) pr.catch(() => {}); }) }),
        el('button', { className: 'srw-btn srw-btn-sm', textContent: '⏸', onClick: () => each((pl) => pl.video.pause()) }),
        el('button', { className: 'srw-btn srw-btn-sm', textContent: '−10 s', onClick: () => each((pl) => pl.seek(pl.time() - 10)) }),
        el('button', { className: 'srw-btn srw-btn-sm', textContent: '+10 s', onClick: () => each((pl) => pl.seek(pl.time() + 10)) }),
        el('button', { className: 'srw-btn srw-btn-sm', textContent: '+60 s', onClick: () => each((pl) => pl.seek(pl.time() + 60)) }),
        el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Jump to 50 %', onClick: () => each((pl) => pl.seek(pl.total * 0.5)) }),
      ]);
      const setLater = async (later) => {
        const r = await window.SRW.runOp('compare_review_status', { scene_id: s.id, status: later ? 'later' : 'open' });
        if (r.error) { window.SRW.notify('Review', r.error, 'error'); return; }
        window.SRW.notify('Review', later ? `Scene #${s.id} put on “Later” (still tagged in Stash).` : `Scene #${s.id} is open again.`, 'success');
        reviewViewer.replaceChildren(); reviewOpenId = null; loadReview();
      };
      const laterBtn = el('button', { className: 'srw-btn', textContent: 'Later (unresolved)',
        title: 'Looked at, cannot be matched now: stays tagged in Stash, leaves the Open list', onClick: () => setLater(true) });
      const reopenBtn = el('button', { className: 'srw-btn', textContent: 'Reopen', onClick: () => setLater(false) });
      const parkBtn = el('button', { className: 'srw-btn', textContent: 'Move to _UNMATCHED',
        title: 'Parks the file in scenes/_UNMATCHED (old sub folders kept) and takes the review tag off, so it leaves this list' });
      parkBtn.hidden = (s.files || []).some((f) => (f.path || '').includes('/_UNMATCHED/')); // already parked (in any _UNMATCHED folder)
      parkBtn.addEventListener('click', (e) => window.SRW.confirmInline(e.currentTarget,
        `Move the file of scene #${s.id} to scenes/_UNMATCHED/? The scene stays in Stash (path changes, a scan follows) and the review tag is removed.`,
        async () => {
          parkBtn.disabled = true;
          const r = await window.SRW.runOp('compare_review_unmatched', { scene_id: s.id });
          parkBtn.disabled = false;
          if (r.error) { window.SRW.notify('Review', r.error, 'error'); return; }
          window.SRW.notify('Review', `Moved to ${r.path}`, 'success');
          reviewViewer.replaceChildren(); reviewOpenId = null; loadReview();
        }));
      const doneBtn = el('button', { className: 'srw-btn srw-btn-primary', textContent: 'Review done' });
      doneBtn.addEventListener('click', (e) => window.SRW.confirmInline(e.currentTarget,
        `Take the tag “Review: Identify Scene” off scene #${s.id}? Everything else on the scene stays; the state is saved to the backup file.`,
        async () => {
          const r = await window.SRW.runOp('compare_review_done', { scene_id: s.id });
          if (r.error) { window.SRW.notify('Review', r.error, 'error'); return; }
          window.SRW.notify('Review', `Scene #${s.id} removed from the review list.`, 'success');
          reviewViewer.replaceChildren(); reviewOpenId = null; loadReview();
        }));
      reviewViewer.replaceChildren(
        el('h4', { textContent: s.title || `Scene ${s.id}` }), bar,
        el('div', { className: 'srw-cmp-grid' }, [col]),
        (allReview.find((x) => x.id === s.id) || {}).kind === 'movie' ? buildMoviePanel(s) : null,
        el('div', { className: 'srw-toolbar' }, [doneBtn, (allReview.find((x) => x.id === s.id) || {}).status === 'later' ? reopenBtn : laterBtn, parkBtn,
          el('button', { className: 'srw-btn', textContent: 'Close', onClick: () => { reviewViewer.replaceChildren(); reviewOpenId = null; } })]),
      );
    }

    const reviewSection = el('details', { className: 'srw-settings-section srw-panel srw-compare' }, [
      el('summary', { textContent: 'Review: Identify Scene' }),
      el('p', {
        className: 'srw-hint',
        textContent: 'Scenes and films marked with the Stash tag “Review: Identify Scene” (wrong matches that were reset, cases no provider knows). The tab “Movies & groups” holds the films of the movie folders: ThePornDB movie search and “create group” come with the player. '
          + 'Play the file, look at real frames, identify by fingerprint or name, search through a Stash scraper, or put a provider id on it. '
          + '“Review done” takes the tag off. Every change is saved to compare_removed_ids.jsonl and History first.',
      }),
      REVIEW_TABS.length > 1 ? el('div', { className: 'srw-subtabs' }, reviewTabBtns.map((t) => t.b)) : null,
      el('div', { className: 'srw-toolbar' }, [el('span', { className: 'srw-view-toggle' }, reviewFilterBtns.map((f) => f.b)), el('button', { className: 'srw-btn', textContent: 'Refresh', onClick: () => loadReview() }), closeIdentifiedBtn, reviewStatus]),
      reviewList,
      reviewViewer,
    ]);
    // Only the part that is shown is loaded (each area is its own page now).
    if (part === 'review') {
      reviewSection.open = true;
      loadReview();
      return reviewSection;
    }
    load();
    return section;
  }

  window.SRW_Maintenance = { render };
})();
