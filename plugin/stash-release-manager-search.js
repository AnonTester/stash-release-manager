/**
 * StashReleaseManager - Search page
 * Reachable at /plugins/stash-release-manager/search.
 *
 * Added 2026-09-19 (see PROGRESS.md/roadmap memory) to replace the
 * previous workflow of searching StashDB.org's own website and handing
 * results to Stash/Whisparr via the separate Stasharr browser
 * extension -- this searches directly against whichever metadata
 * provider is configured on the Settings page (StashDB and/or
 * ThePornDB), unlike Whisparr's own search which is locked to
 * whichever single stash-box endpoint Whisparr itself talks to.
 *
 * Two actions once something's found:
 * - Performer/Studio: "Favorite" -- creates the local Stash entity if
 *   it doesn't exist yet (or just flips its favorite flag), then the
 *   existing Favorite-driven watchlist sync picks it up automatically
 *   before the next scan. Labelled "Watch" when the watchlist follows
 *   the watchlist tag instead (2026-09-26, api/watchlist_source.py) --
 *   then it sets that tag, not Favorite.
 * - Scene: "Add to Wishlist" (renamed from "Send to Whisparr"
 *   2026-09-19, Milestone 3 -- the actual indexer search now runs in
 *   this sidecar, not Whisparr) -- StashDB only for now (Whisparr can
 *   only look up StashDB scene ids, same constraint as the Releases
 *   page's own hand-off button).
 */
(function () {
  'use strict';

  const TYPES = [
    { key: 'performers', label: 'Performers' },
    { key: 'studios', label: 'Studios' },
    { key: 'scenes', label: 'Scenes' },
  ];
  // Matches the Watchlist page's own page size (stash-release-manager-
  // settings.js) -- same reasoning: a result grid this size per page
  // reads comfortably without an endless scroll, and reusing the exact
  // same number/pager UI (2026-09-19, user request) keeps pagination
  // feeling consistent across the plugin instead of each page
  // reinventing its own convention.
  const PAGE_SIZE = 40;

  async function render(container) {
    const { el } = window.SRW;
    container.innerHTML = '';
    container.appendChild(window.SRW.buildPageHeader('search'));
    // Every enabled metadata provider (StashDB, ThePornDB and the stash-boxes added in Settings > Connections), from the provider registry.
    await window.SRW.loadProviders();
    const PROVIDERS = window.SRW.providerKeys().filter((k) => window.SRW.providerInfo(k).enabled !== false)
      .map((k) => ({ key: k, label: window.SRW.providerLabel(k) }));

    container.appendChild(el('p', {
      className: 'srw-hint',
      textContent: 'Find a Performer, Studio, or Scene that isn’t on the watchlist yet — against '
        + 'whichever metadata provider you pick here, not just whichever one Whisparr itself is '
        + 'configured against. Favoriting a Performer/Studio adds it to the watchlist the same way '
        + 'starring it in Stash directly would.',
    }));

    // Per-render UI state, not persisted -- same idea as the Releases
    // page's own type/search filters.
    let provider = 'stashdb';
    let type = 'performers';

    const providerButtons = PROVIDERS.map((p) => el('button', {
      className: 'srw-btn srw-btn-sm', textContent: p.label,
      onClick: () => { provider = p.key; updateToggles(); },
    }));
    const typeButtons = TYPES.map((t) => el('button', {
      className: 'srw-btn srw-btn-sm', textContent: t.label,
      onClick: () => { type = t.key; updateToggles(); },
    }));

    function updateToggles() {
      providerButtons.forEach((btn, i) => btn.classList.toggle('srw-btn-primary', PROVIDERS[i].key === provider));
      typeButtons.forEach((btn, i) => btn.classList.toggle('srw-btn-primary', TYPES[i].key === type));
    }
    updateToggles();

    const termInput = el('input', {
      type: 'search', placeholder: 'Search by name or title…', className: 'srw-search-input',
    });
    const resultsArea = el('div', { className: 'srw-search-results' });

    // Paginated client-side over whatever this search already fetched
    // (up to 100 per search_${type}, see the client's own docstrings)
    // -- reset on every new search, not on a page-only re-render.
    // 'favorites' | 'tag' -- decides the entity button's label; loaded
    // once per page visit, the backend decides the actual action anyway.
    let watchMode = 'favorites';
    let catalogOnWatch = 'off'; // Settings: off / ask / auto -- load the catalog of a newly watched StashDB entity
    window.SRW.runOp('settings_get_all')
      .then((settings) => { if (settings && settings.watchlist_source) watchMode = settings.watchlist_source; if (settings && settings.catalog_on_watch) catalogOnWatch = settings.catalog_on_watch; })
      .catch(() => {});
    let currentResults = [];
    let currentTotal = null;
    let resultsPage = 1;

    async function runSearch() {
      const term = termInput.value.trim();
      if (!term) { resultsArea.innerHTML = ''; return; }
      resultsArea.innerHTML = '';
      resultsArea.appendChild(el('p', { className: 'srw-hint', textContent: 'Searching…' }));
      try {
        const response = await window.SRW.runOp(`search_${type}`, { provider, term });
        if (response && response.error) {
          resultsArea.innerHTML = '';
          resultsArea.appendChild(el('p', { className: 'srw-hint', textContent: `Error: ${response.error}` }));
          return;
        }
        // {"total", "results"} -- not a plain array (2026-09-19, see
        // stashdb_client.py's search_performers() docstring for why:
        // a broad term's own result ordering isn't relevance-ranked,
        // so the actually-wanted match can rank well past this
        // request's own cutoff without `total` there'd be no way to
        // tell "here are all the matches" from "here are the first N
        // of many more").
        renderResults(Array.isArray(response.results) ? response.results : [], response.total);
      } catch (e) {
        resultsArea.innerHTML = '';
        resultsArea.appendChild(el('p', { className: 'srw-hint', textContent: `Error: ${e.message}` }));
      }
    }
    termInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') runSearch(); });

    container.appendChild(el('div', { className: 'srw-filter-bar' }, [
      el('span', { className: 'srw-view-toggle' }, providerButtons),
      el('span', { className: 'srw-view-toggle' }, typeButtons),
    ]));
    container.appendChild(el('div', { className: 'srw-toolbar' }, [
      termInput,
      el('button', { className: 'srw-btn srw-btn-primary', textContent: 'Search', onClick: runSearch }),
    ]));
    container.appendChild(resultsArea);

    function renderResults(results, total) {
      currentResults = results;
      currentTotal = total;
      resultsPage = 1;
      renderResultsPage();
    }

    // Same Prev/Next/"Page X of Y" pager as the Watchlist page's own
    // (stash-release-manager-settings.js's buildPager()) -- deliberately
    // not that same function reused directly (this file has no
    // dependency on settings.js loading first/at all), just the same
    // markup/classes so it looks and behaves identically.
    function buildPager(currentPage, totalPages) {
      return window.SRW.buildPager({ page: currentPage, totalPages, total: currentResults.length, perPage: PAGE_SIZE, onPage: (n) => { resultsPage = n; renderResultsPage(); } });
    }

    function renderResultsPage() {
      resultsArea.innerHTML = '';
      if (!currentResults.length) {
        resultsArea.appendChild(el('p', { className: 'srw-hint', textContent: 'No results.' }));
        return;
      }
      // `total` is null when the provider itself doesn't report one
      // (StashDB's studio search has no count at all -- see
      // stashdb_client.py's search_studios() docstring) -- shown as a
      // plain count in that case, since "more may exist" can't be said
      // with any actual confidence either way.
      if (currentTotal != null && currentTotal > currentResults.length) {
        resultsArea.appendChild(el('p', {
          className: 'srw-callout',
          textContent: `Showing ${currentResults.length} of ${currentTotal} matches — try a more specific `
            + 'search term to find one further down the list (this provider doesn’t rank results by '
            + 'relevance to your search).',
        }));
      } else {
        resultsArea.appendChild(el('p', { className: 'srw-hint', textContent: `${currentResults.length} result(s).` }));
      }
      const totalPages = Math.max(1, Math.ceil(currentResults.length / PAGE_SIZE));
      resultsPage = Math.min(Math.max(resultsPage, 1), totalPages);
      const pageItems = currentResults.slice((resultsPage - 1) * PAGE_SIZE, resultsPage * PAGE_SIZE);
      const grid = el('div', { className: 'srw-search-grid' });
      pageItems.forEach((r) => grid.appendChild(
        type === 'scenes' ? buildSceneCard(r) : buildEntityCard(r, type === 'performers' ? 'performer' : 'studio'),
      ));
      if (totalPages > 1) resultsArea.appendChild(buildPager(resultsPage, totalPages)); // top and bottom, like Stash's own lists
      resultsArea.appendChild(grid);
      if (totalPages > 1) resultsArea.appendChild(buildPager(resultsPage, totalPages));
    }

    function buildEntityCard(entity, entityType) {
      const card = el('div', { className: 'srw-search-card' });
      if (entity.image_url) {
        card.appendChild(el('img', { src: entity.image_url, className: 'srw-search-card-image' }));
      }
      const icon = window.SRW.entityIcon(entityType);
      card.appendChild(el('div', { className: 'srw-search-card-title' }, [icon, ' ' + entity.name]));
      if (entity.is_favorite) {
        const providerLabel = window.SRW.providerLabel(provider);
        card.appendChild(el('p', {
          className: 'srw-hint', textContent: `★ already favorited on ${providerLabel}`,
        }));
      }
      const status = el('span', { className: 'srw-key-status' });
      const tagMode = watchMode === 'tag';
      const btn = el('button', {
        className: 'srw-btn srw-btn-primary', textContent: tagMode ? 'Watch' : 'Favorite',
        title: tagMode ? 'Adds it to the watchlist (watchlist tag in Stash), creating it in Stash if needed' : '',
      });
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        status.textContent = ' saving...';
        try {
          const op = entityType === 'performer' ? 'search_favorite_performer' : 'search_favorite_studio';
          const result = await window.SRW.runOp(op, { provider, stashdb_id: entity.id, name: entity.name });
          if (result.error) {
            status.textContent = ` error: ${result.error}`;
            btn.disabled = false;
            return;
          }
          const watched = result.watch_mode === 'tag';
          status.textContent = result.created
            ? (watched ? ' created & added to watchlist' : ' created & favorited')
            : (watched ? ' added to watchlist' : ' favorited');
          btn.textContent = watched ? 'Watching' : 'Favorited';
          if (provider !== 'theporndb' && catalogOnWatch !== 'off') offerCatalogLoad(card, entity, entityType);
        } catch (err) {
          status.textContent = ` error: ${err.message}`;
          btn.disabled = false;
        }
      });
      card.appendChild(btn);
      card.appendChild(status);
      return card;
    }

    // Setting "Load the catalog for new watchlist entries": 'ask' = a button with a confirmation, 'auto' = starts at once.
    function offerCatalogLoad(card, entity, entityType) {
      const { el } = window.SRW;
      const status = el('span', { className: 'srw-key-status' });
      const start = async () => {
        status.textContent = ' starting...';
        try {
          const r = await window.SRW.runOp('backlog_load_catalog', {
            provider: provider, entity_type: entityType, entity_stashdb_id: entity.id, name: entity.name,
          });
          status.textContent = r.error ? ` error: ${r.error}`
            : (r.started ? ` loading ${r.years.length} year(s) in the background -- see Missing from Catalog` : ' nothing to load (all years loaded or outside the date window)');
        } catch (err) { status.textContent = ` error: ${err.message}`; }
      };
      if (catalogOnWatch === 'auto') { start(); card.appendChild(status); return; }
      const loadBtn = el('button', { className: 'srw-btn', textContent: 'Load releases' });
      loadBtn.addEventListener('click', () => window.SRW.confirmInline(loadBtn, `Load the releases of "${entity.name}" now?`, start));
      card.appendChild(loadBtn);
      card.appendChild(status);
    }

    function buildSceneCard(scene) {
      const card = el('div', { className: 'srw-search-card' });
      if (scene.image_url) {
        card.appendChild(el('img', { src: scene.image_url, className: 'srw-search-card-image' }));
      }
      card.appendChild(el('div', { className: 'srw-search-card-title', textContent: scene.title || '(untitled)' }));
      const meta = [(scene.studio || {}).name, scene.date].filter(Boolean).join(' · ');
      if (meta) card.appendChild(el('p', { className: 'srw-hint', textContent: meta }));
      const performerNames = (scene.performers || []).map((p) => p.name).join(', ');
      if (performerNames) card.appendChild(el('p', { className: 'srw-hint', textContent: performerNames }));

      const status = el('span', { className: 'srw-key-status' });
      // Renamed from "Send to Whisparr" 2026-09-19 (Milestone 3) -- the
      // actual indexer search now runs in this sidecar, not Whisparr,
      // see entity-panel.js's confirmHandoff() rename note.
      const btn = el('button', { className: 'srw-btn srw-btn-primary', textContent: 'Add to Wishlist' });
      // Any provider since 2026-09-25 (non-StashDB scenes are handled
      // without Whisparr, see handoff.hand_off()).
      {
        btn.addEventListener('click', () => {
          window.SRW.confirmInline(btn, `Add "${scene.title || scene.id}" to your wishlist and search for it?`, async () => {
            status.textContent = ' adding...';
            try {
              const result = await window.SRW.runOp('search_handoff_scene', { provider, scene });
              if (result.error) {
                status.textContent = ` error: ${result.error}`;
                window.SRW.notify(`Adding failed: ${scene.title || scene.id}`, result.error, 'error');
                return;
              }
              window.SRW.notifyHandoff(scene.title || scene.id, result);
              status.textContent = result.already_sent ? ' already added' : ' added';
              btn.textContent = 'Added';
              btn.disabled = true; // stays visible (confirmInline restores display on success too) but no second click
            } catch (err) {
              status.textContent = ` error: ${err.message}`;
            }
          });
        });
      }
      card.appendChild(btn);
      card.appendChild(status);
      return card;
    }
  }

  window.SRW_Search = { render };
})();
