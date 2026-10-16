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
    { key: 'movies', label: 'Movies' }, // ThePornDB only (it has movies; the stash-boxes do not)
  ];
  // What a provider can search: movies only on ThePornDB; the tag search of scenes only on the stash-boxes (ThePornDB's tag filter does not work).
  const hasMovies = (provider) => provider === 'theporndb';
  const hasTagSearch = (provider) => provider !== 'theporndb';
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
      onClick: () => { provider = p.key; if (!hasMovies(provider) && type === 'movies') type = 'scenes'; if (!hasTagSearch(provider) && type === 'tags') type = 'scenes'; updateToggles(); },
    }));
    const typeButtons = TYPES.map((t) => el('button', {
      className: 'srw-btn srw-btn-sm', textContent: t.label,
      onClick: () => { type = t.key === 'scenes' && type === 'tags' ? 'tags' : t.key; updateToggles(); },
    }));

    const tagSelection = { provider: 'stashdb' }; // tag ids belong to one provider: switching it empties the selection
    function updateToggles() {
      providerButtons.forEach((btn, i) => btn.classList.toggle('srw-btn-primary', PROVIDERS[i].key === provider));
      typeButtons.forEach((btn, i) => {
        btn.hidden = TYPES[i].key === 'movies' && !hasMovies(provider);
        btn.classList.toggle('srw-btn-primary', TYPES[i].key === type || (TYPES[i].key === 'scenes' && type === 'tags'));
      });
      // "By title" / "By tag" belongs to the scene search only
      const sceneMode = type === 'scenes' || type === 'tags';
      scenesModeBar.hidden = !(sceneMode && hasTagSearch(provider));
      byTitleBtn.classList.toggle('srw-btn-primary', type === 'scenes');
      byTagBtn.classList.toggle('srw-btn-primary', type === 'tags');
      tagPanel.hidden = type !== 'tags';
      tagSceneArea.hidden = type !== 'tags';
      if (tagSelection.provider !== provider) { tagSelection.provider = provider; selectedTags = []; renderSelectedTags(); tagSceneArea.innerHTML = ''; resultsArea.innerHTML = ''; }
      fillTagPresets('');
      termInput.placeholder = type === 'tags' ? 'Search for a tag…' : type === 'movies' ? 'Search movies by title…' : 'Search by name or title…';
    }
    const byTitleBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'By title', onClick: () => { type = 'scenes'; updateToggles(); } });
    const byTagBtn = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'By tag', onClick: () => { type = 'tags'; updateToggles(); } });
    const scenesModeBar = el('span', { className: 'srw-view-toggle' }, [byTitleBtn, byTagBtn]);
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
      .then((settings) => {
        try { const parsed = JSON.parse((settings && settings.search_tag_presets) || '[]'); tagPresets = Array.isArray(parsed) ? parsed : []; } catch (e) { tagPresets = []; }
        fillTagPresets('');
        if (settings && settings.watchlist_source) watchMode = settings.watchlist_source; if (settings && settings.catalog_on_watch) catalogOnWatch = settings.catalog_on_watch;
      })
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
        if (type === 'tags') { await runTagSearch(term); return; }
        const response = type === 'movies' ? await window.SRW.runOp('films_search_tpdb', { q: term, limit: 40 })
          : await window.SRW.runOp(`search_${type}`, { provider, term });
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
    // "By tag" (v0.207.0, several tags at once + presets v0.210.0): the term finds tags (buttons); a click selects or deselects one. The
    // scenes below carry ALL selected tags (newest first) and are reloaded on every change. A selection stays while searching further tags.
    let selectedTags = []; // [{id, name, exclude}] of the current provider (exclude: scenes WITH this tag are left out; v0.211.0)
    const included = () => selectedTags.filter((t) => !t.exclude);
    const tagState = (id) => { const t = selectedTags.find((x) => x.id === id); return t ? (t.exclude ? 'exclude' : 'include') : ''; };
    function styleTagPick(b) {
      const st = tagState(b.dataset.tagId);
      b.classList.toggle('srw-btn-primary', st === 'include');
      b.classList.toggle('srw-btn-danger', st === 'exclude');
      b.textContent = (st === 'exclude' ? '− ' : '') + b.dataset.tagName;
    }
    const restyleTagPicks = () => document.querySelectorAll('.srw-search-tag-pick').forEach(styleTagPick);
    let tagPresets = []; // [{name, provider, tags: [{id, name}]}]
    const tagSceneArea = el('div', { className: 'srw-search-tag-scenes' });
    const selectedBar = el('span', { className: 'srw-filter-bar' });
    const presetSelect = el('select', { className: 'srw-filter-preset-select' });
    const presetDelete = el('button', { className: 'srw-btn srw-btn-sm', textContent: '×', title: 'Delete selected preset' });
    const presetSave = el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Save preset', title: 'Save the selected tags under a name' });
    const tagPanel = el('div', { className: 'srw-search-tag-panel' }, [
      el('div', { className: 'srw-filter-bar' }, [selectedBar, el('span', { className: 'srw-filter-presets' }, [presetSelect, presetDelete, presetSave])]),
    ]);
    const providerPresets = () => tagPresets.filter((p) => p.provider === provider);

    function fillTagPresets(selectedName) {
      const list = providerPresets();
      presetSelect.innerHTML = '';
      presetSelect.appendChild(el('option', { value: '', textContent: list.length ? 'Presets…' : 'No presets' }));
      list.forEach((p) => presetSelect.appendChild(el('option', { value: p.name, textContent: p.name })));
      presetSelect.value = selectedName || '';
      presetDelete.disabled = !presetSelect.value;
      presetSave.disabled = !selectedTags.length;
    }
    async function persistTagPresets(next, selectedName) {
      const result = await window.SRW.runOp('settings_set', { key: 'search_tag_presets', value: JSON.stringify(next) });
      if (result && result.error) { window.SRW.notify('Presets', `Saving presets failed: ${result.error}`, 'error'); return; }
      tagPresets = next;
      fillTagPresets(selectedName);
    }
    function renderSelectedTags() {
      selectedBar.innerHTML = '';
      if (!selectedTags.length) {
        selectedBar.appendChild(el('span', { className: 'srw-hint', textContent: 'No tag selected — search for tags and click them: once = must have it, twice = must not have it.' }));
      } else {
        selectedTags.forEach((t) => selectedBar.appendChild(el('span', { className: 'srw-view-toggle' }, [
          el('button', {
            className: `srw-btn srw-btn-sm ${t.exclude ? 'srw-btn-danger' : 'srw-btn-primary'}`, textContent: `${t.exclude ? '− ' : ''}${t.name}`,
            title: t.exclude ? 'Scenes with this tag are left out — click to require it instead' : 'Scenes must have this tag — click to leave out scenes with it instead',
            onClick: () => { t.exclude = !t.exclude; tagSelectionChanged(); },
          }),
          el('button', { className: 'srw-btn srw-btn-sm', textContent: '×', title: 'Remove this tag',
            onClick: () => { selectedTags = selectedTags.filter((x) => x.id !== t.id); tagSelectionChanged(); } }),
        ])));
        selectedBar.appendChild(el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Clear', onClick: () => { selectedTags = []; tagSelectionChanged(); } }));
      }
      presetSave.disabled = !selectedTags.length;
    }
    let tagLoadSeq = 0;
    async function loadTagScenes() {
      const seq = ++tagLoadSeq; // a slow answer of an earlier selection must not overwrite a newer one
      tagSceneArea.innerHTML = '';
      if (!included().length) {
        currentResults = [];
        if (selectedTags.length) tagSceneArea.appendChild(el('p', { className: 'srw-hint', textContent: 'Select at least one tag a scene must have — excluded tags alone cannot be searched.' }));
        return;
      }
      const without = selectedTags.filter((t) => t.exclude);
      tagSceneArea.appendChild(el('p', { className: 'srw-hint', textContent: `Loading scenes tagged ${included().map((t) => `"${t.name}"`).join(' + ')}${without.length ? ` without ${without.map((t) => `"${t.name}"`).join(', ')}` : ''}…` }));
      try {
        const r = await window.SRW.runOp('search_scenes_by_tag', { provider, tag_ids: included().map((t) => t.id), exclude_ids: selectedTags.filter((t) => t.exclude).map((t) => t.id) });
        if (seq !== tagLoadSeq) return;
        if (r && r.error) { tagSceneArea.innerHTML = ''; tagSceneArea.appendChild(el('p', { className: 'srw-hint', textContent: `Error: ${r.error}` })); return; }
        renderResults(Array.isArray(r.results) ? r.results : [], r.total); // the shared result state and paging, drawn into tagSceneArea
      } catch (e) {
        if (seq !== tagLoadSeq) return;
        tagSceneArea.innerHTML = ''; tagSceneArea.appendChild(el('p', { className: 'srw-hint', textContent: `Error: ${e.message}` }));
      }
    }
    function tagSelectionChanged() {
      renderSelectedTags();
    updateToggles(); // after the term input and the tag panel exist (it sets the placeholder and shows/hides the panel)
      restyleTagPicks();
      fillTagPresets('');
      loadTagScenes();
    }
    presetSelect.addEventListener('change', () => {
      presetDelete.disabled = !presetSelect.value;
      const preset = providerPresets().find((p) => p.name === presetSelect.value);
      if (preset) { selectedTags = preset.tags.map((t) => ({ id: t.id, name: t.name, exclude: !!t.exclude })); renderSelectedTags(); restyleTagPicks(); loadTagScenes(); }
    });
    presetDelete.addEventListener('click', () => {
      const name = presetSelect.value;
      if (!name) return;
      window.SRW.confirmInline(presetDelete, `Delete preset "${name}"?`,
        () => persistTagPresets(tagPresets.filter((p) => !(p.provider === provider && p.name === name)), ''), { danger: true });
    });
    presetSave.addEventListener('click', () => {
      presetSave.style.display = 'none';
      const nameInput = el('input', { type: 'text', placeholder: 'Preset name', value: presetSelect.value, className: 'srw-filter-preset-name' });
      const close = () => { form.remove(); presetSave.style.display = ''; };
      const doSave = async () => {
        const name = nameInput.value.trim();
        if (!name || !selectedTags.length) return;
        const next = tagPresets.filter((p) => !(p.provider === provider && p.name === name));
        next.push({ name, provider, tags: selectedTags.map((t) => ({ id: t.id, name: t.name, exclude: !!t.exclude })) });
        next.sort((a, b) => a.name.localeCompare(b.name));
        await persistTagPresets(next, name);
        close();
      };
      nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSave(); if (e.key === 'Escape') close(); });
      const form = el('span', { className: 'srw-inline-confirm' }, [
        nameInput,
        el('button', { className: 'srw-btn srw-btn-sm srw-btn-primary', textContent: 'Save', onClick: doSave }),
        el('button', { className: 'srw-btn srw-btn-sm', textContent: 'Cancel', onClick: close }),
      ]);
      presetSave.after(form);
      nameInput.focus();
    });

    async function runTagSearch(term) {
      const response = await window.SRW.runOp('search_tags', { provider, term });
      resultsArea.innerHTML = '';
      if (response && response.error) {
        resultsArea.appendChild(el('p', { className: 'srw-hint', textContent: `Error: ${response.error}` }));
        return;
      }
      const tags = Array.isArray(response.results) ? response.results : [];
      if (!tags.length) { resultsArea.appendChild(el('p', { className: 'srw-hint', textContent: 'No tags found.' })); return; }
      const tagButtons = tags.map((t) => el('button', {
        className: 'srw-btn srw-btn-sm srw-search-tag-pick',
        textContent: t.name, title: t.description || '', 'data-tag-id': t.id, 'data-tag-name': t.name,
        onClick: () => {
          // off -> must have -> must not have -> off
          const st = tagState(t.id);
          if (!st) selectedTags = [...selectedTags, { id: t.id, name: t.name, exclude: false }];
          else if (st === 'include') selectedTags.find((x) => x.id === t.id).exclude = true;
          else selectedTags = selectedTags.filter((x) => x.id !== t.id);
          tagSelectionChanged();
        },
      }));
      resultsArea.appendChild(el('p', { className: 'srw-hint', textContent: `${tags.length}${response.total > tags.length ? ` of ${response.total}` : ''} tag(s) — click once = must have the tag, twice = must not have it, three times = off.` }));
      tagButtons.forEach(styleTagPick);
      resultsArea.appendChild(el('div', { className: 'srw-filter-bar' }, tagButtons));
    }
    termInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') runSearch(); });

    container.appendChild(el('div', { className: 'srw-filter-bar' }, [
      el('span', { className: 'srw-view-toggle' }, providerButtons),
      el('span', { className: 'srw-view-toggle' }, typeButtons),
      scenesModeBar,
    ]));
    container.appendChild(el('div', { className: 'srw-toolbar' }, [
      termInput,
      el('button', { className: 'srw-btn srw-btn-primary', textContent: 'Search', onClick: runSearch }),
    ]));
    container.appendChild(tagPanel);
    container.appendChild(resultsArea);
    container.appendChild(tagSceneArea);
    renderSelectedTags();

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
      const out = type === 'tags' ? tagSceneArea : resultsArea; // below the tag selection in tag mode
      out.innerHTML = '';
      if (!currentResults.length) {
        out.appendChild(el('p', { className: 'srw-hint', textContent: 'No results.' }));
        return;
      }
      // `total` is null when the provider itself doesn't report one
      // (StashDB's studio search has no count at all -- see
      // stashdb_client.py's search_studios() docstring) -- shown as a
      // plain count in that case, since "more may exist" can't be said
      // with any actual confidence either way.
      if (currentTotal != null && currentTotal > currentResults.length) {
        out.appendChild(el('p', {
          className: 'srw-callout',
          textContent: type === 'tags'
            ? `Showing the newest ${currentResults.length} of ${currentTotal} scenes with ${included().length > 1 ? 'these tags' : 'this tag'}.`
            : `Showing ${currentResults.length} of ${currentTotal} matches — try a more specific `
              + 'search term to find one further down the list (this provider doesn’t rank results by '
              + 'relevance to your search).',
        }));
      } else {
        out.appendChild(el('p', { className: 'srw-hint', textContent: `${currentResults.length} result(s).` }));
      }
      const totalPages = Math.max(1, Math.ceil(currentResults.length / PAGE_SIZE));
      resultsPage = Math.min(Math.max(resultsPage, 1), totalPages);
      const pageItems = currentResults.slice((resultsPage - 1) * PAGE_SIZE, resultsPage * PAGE_SIZE);
      const grid = el('div', { className: 'srw-search-grid' });
      pageItems.forEach((r) => grid.appendChild(
        type === 'movies' ? buildMovieCard(r) : type === 'scenes' || type === 'tags' ? buildSceneCard(r) : buildEntityCard(r, type === 'performers' ? 'performer' : 'studio'),
      ));
      if (totalPages > 1) out.appendChild(buildPager(resultsPage, totalPages)); // top and bottom, like Stash's own lists
      out.appendChild(grid);
      if (totalPages > 1) out.appendChild(buildPager(resultsPage, totalPages));
    }

    // Same box for every card (v0.205.2): a missing or broken image becomes a grey placeholder of the same size, so the text and the
    // buttons of cards in one row start at the same height. The buttons sit in a footer pushed to the bottom of the card.
    function cardImage(url, landscape = false, contain = false) {
      const cls = `srw-search-card-image${landscape ? ' srw-search-card-image-scene' : ''}${contain ? ' srw-search-card-image-contain' : ''}`;
      const box = () => el('div', { className: `${cls} srw-search-card-noimage` });
      if (!url) return box();
      const img = el('img', { src: url, className: cls, alt: '', loading: 'lazy' });
      img.addEventListener('error', () => img.replaceWith(box()));
      return img;
    }
    // The image and the title open the item's page on the provider's own website in a new tab (`source_url` comes with the result).
    const linked = (node, url) => (url
      ? el('a', { href: url, target: '_blank', rel: 'noopener noreferrer', className: 'srw-search-card-link', title: 'Open on the provider’s website' }, [node])
      : node);
    const cardFooter = (...nodes) => el('div', { className: 'srw-search-card-actions' }, nodes);

    // A ThePornDB movie: "Add to film wishlist" puts it on the Films wishlist (the same call as the Films page's "Add film").
    function buildMovieCard(m) {
      const card = el('div', { className: 'srw-search-card' });
      const slug = (m.record || {}).slug;
      const link = slug ? `https://theporndb.net/movies/${slug}` : null;
      card.appendChild(linked(cardImage(m.image_url), link));
      card.appendChild(linked(el('div', { className: 'srw-search-card-title', textContent: m.title || '(untitled)' }), link));
      const meta = [m.year, m.studio_name, m.duration_min ? `${Math.round(m.duration_min)} min` : null].filter(Boolean).join(' · ');
      if (meta) card.appendChild(el('p', { className: 'srw-hint', textContent: meta }));
      const cast = (m.performers || []).slice(0, 8).join(', ');
      if (cast) card.appendChild(el('p', { className: 'srw-hint', textContent: cast + ((m.performers || []).length > 8 ? ', …' : '') }));
      const status = el('span', { className: 'srw-key-status' });
      const footer = cardFooter();
      const onList = () => { footer.replaceChildren(el('span', { className: 'srw-unverified-badge', textContent: 'on film wishlist' })); };
      if (m.on_list) { onList(); card.appendChild(footer); return card; }
      const btn = el('button', { className: 'srw-btn srw-btn-primary', textContent: 'Add to film wishlist', title: 'Put the movie on the Films wishlist' });
      btn.addEventListener('click', () => window.SRW.confirmInline(btn, `Add "${m.title}" to the film wishlist?`, async () => {
        status.textContent = ' adding...';
        try {
          const r = await window.SRW.runOp('films_add', { movie: m.record });
          if (r.error) { status.textContent = ` error: ${r.error}`; return; }
          status.textContent = '';
          window.SRW.notify(r.created ? `Film wishlist: ${m.title}` : `Already on the film wishlist: ${m.title}`, '', 'success');
          onList();
        } catch (err) { status.textContent = ` error: ${err.message}`; }
      }));
      footer.replaceChildren(btn, status);
      card.appendChild(footer);
      return card;
    }

    function buildEntityCard(entity, entityType) {
      const card = el('div', { className: 'srw-search-card' });
      card.appendChild(linked(cardImage(entity.image_url, false, entityType === 'studio'), entity.source_url)); // a studio logo is shown whole, not cropped
      const icon = window.SRW.entityIcon(entityType);
      card.appendChild(linked(el('div', { className: 'srw-search-card-title' }, [icon, ' ' + entity.name]), entity.source_url));
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
      card.appendChild(cardFooter(btn, status));
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

    // A scene card knows what this program has done with the scene (v0.206.0): `scene.release_status` comes with the search result
    // (sent_to_whisparr / imported / import_failed / dismissed, or nothing). The card takes the calendar's colour for that state and offers
    // what fits it: Add / Add + search, or Search now / Dismiss for a scene on the wishlist, or Restore for a dismissed one.
    const STATE_LABEL = { sent_to_whisparr: 'On Wishlist', in_library: 'In library', imported: 'Imported', import_failed: 'Import failed', dismissed: 'Dismissed' };

    function buildSceneCard(scene) {
      const card = el('div', { className: 'srw-search-card' });
      const imageLink = linked(cardImage(scene.image_url, true), scene.source_url); // scenes are landscape (3/2), people and studios portrait (2/3)
      card.appendChild(imageLink);
      const badge = el('span', { className: 'srw-search-card-badge' });
      const titleLink = linked(el('div', { className: 'srw-search-card-title', textContent: scene.title || '(untitled)' }), scene.source_url);
      card.appendChild(titleLink);
      const meta = [(scene.studio || {}).name, scene.date].filter(Boolean).join(' · ');
      if (meta) card.appendChild(el('p', { className: 'srw-hint', textContent: meta }));
      const performerNames = (scene.performers || []).map((p) => p.name).join(', ');
      if (performerNames) card.appendChild(el('p', { className: 'srw-hint', textContent: performerNames }));

      const status = el('span', { className: 'srw-key-status' });
      const footer = cardFooter();
      const title = scene.title || scene.id;
      let state = scene.release_status || null;

      const sceneOp = (mode, extra = {}) => window.SRW.runOp(mode, { provider, scene_stashdb_id: scene.id, ...extra });
      // Add (search = false) / Add + search / Search now: all go through the one hand-off.
      const handoff = async (search) => {
        status.textContent = search ? ' adding and searching...' : ' adding...';
        try {
          const result = await window.SRW.runOp('search_handoff_scene', { provider, scene, ...(search ? {} : { search: false }) });
          if (result.error) {
            status.textContent = ` error: ${result.error}`;
            window.SRW.notify(`Adding failed: ${title}`, result.error, 'error');
            return;
          }
          window.SRW.notifyHandoff(title, result);
          status.textContent = '';
          if (result.stash_scene_id) scene.stash_scene_id = result.stash_scene_id;
          setState(result.already_in_library ? 'in_library' : 'sent_to_whisparr');
        } catch (err) {
          status.textContent = ` error: ${err.message}`;
        }
      };
      const button = (text, cls, titleText, onClick) => el('button', { className: `srw-btn ${cls}`.trim(), textContent: text, title: titleText, onClick });
      const confirmed = (text, cls, titleText, question, run) => {
        const b = button(text, cls, titleText);
        b.addEventListener('click', () => window.SRW.confirmInline(b, question, run));
        return b;
      };

      function setState(next) {
        state = next;
        Object.keys(STATE_LABEL).forEach((k) => card.classList.toggle(`srw-search-card-state-${k}`, k === state));
        // A scene Stash has: image and title open it in your Stash (otherwise the provider's page).
        [imageLink, titleLink].forEach((a) => {
          if (!a || a.tagName !== 'A') return;
          const own = state === 'in_library' && scene.stash_scene_id;
          a.href = own ? `/scenes/${scene.stash_scene_id}` : (scene.source_url || a.href);
          a.title = own ? 'Open the scene in your Stash' : 'Open on the provider’s website';
        });
        badge.textContent = state ? STATE_LABEL[state] : '';
        badge.hidden = !state;
        const buttons = [];
        if (!state) {
          buttons.push(
            confirmed('Add', '', 'Put it on the wishlist without searching now -- the scheduled search finds it later',
              `Add "${title}" to your wishlist? It is searched by the schedule, not right now.`, () => handoff(false)),
            confirmed('Add + search', 'srw-btn-primary', 'Put it on the wishlist and search the indexers right now',
              `Add "${title}" to your wishlist and search for it?`, () => handoff(true)));
        } else if (state === 'sent_to_whisparr') {
          buttons.push(
            button('Search now', 'srw-btn-whisparr', 'Ask the indexers for it right now', () => handoff(true)),
            button('Dismiss', 'srw-btn-dismiss', 'Take it off the wishlist (Restore brings it back)', async () => {
              status.textContent = ' dismissing...';
              const r = await sceneOp('releases_dismiss');
              if (r.error) { status.textContent = ` error: ${r.error}`; return; }
              status.textContent = '';
              setState('dismissed');
            }));
        } else if (state === 'in_library' && scene.stash_scene_id) {
          // Fetch it again although a file exists (a sample, a cut or a wrong file): v0.209.0. The old file is replaced after a verified download.
          const readd = async (search) => {
            status.textContent = search ? ' re-adding and searching...' : ' re-adding...';
            try {
              const r = await window.SRW.runOp('search_readd_scene', { provider, scene, ...(search ? {} : { search: false }) });
              if (r.error) { status.textContent = ` error: ${r.error}`; return; }
              window.SRW.notifyReadd(title, r);
              status.textContent = r.grabbed ? ' grabbed' : ' on the upgrade list';
            } catch (err) { status.textContent = ` error: ${err.message}`; }
          };
          const readdText = `Fetch “${title}” again although Stash has a file? A verified download replaces that file; the old one goes to the recycle bin.`;
          buttons.push(
            confirmed('Re-add', '', 'Put it on the upgrade list although a file exists -- the scheduled search finds a new one',
              `${readdText} It is searched by the schedule, not right now.`, () => readd(false)),
            confirmed('Re-add + search', '', 'The same, and search the indexers right now', readdText, () => readd(true)));
        } else if (state === 'dismissed') {
          buttons.push(button('Restore', '', 'Back to the releases list; Add puts it on the wishlist again', async () => {
            status.textContent = ' restoring...';
            const r = await sceneOp('releases_undismiss');
            if (r.error) { status.textContent = ` error: ${r.error}`; return; }
            status.textContent = '';
            setState(null);
          }));
        }
        footer.replaceChildren(...buttons, status);
      }
      card.insertBefore(badge, card.children[1]);
      setState(state);
      card.appendChild(footer);
      return card;
    }
  }

  window.SRW_Search = { render };
})();
