/**
 * StashReleaseManager - Activity
 *
 * Consolidates what the Releases page's "On Wishlist"/"Imported"
 * sections already show (2026-09-21, user request after noticing
 * 'import_failed' releases had NO visible home anywhere in this
 * plugin's UI -- only a transient alert() after manually clicking "Run
 * file import now" on the Settings page) into one dedicated view:
 * what's currently downloading (live progress), what got imported, and
 * what failed to import. Read-only/informational by design -- the
 * triage actions (Search now, Fix wrong download, Restore a dismiss,
 * the review-candidate box) stay on the Releases page, which already
 * has them; this page is a complementary status view, not a
 * replacement.
 *
 * What to actually DO about an 'import_failed' release (retry? surface
 * the failure reason, which isn't persisted anywhere today? a manual
 * fix flow?) is explicitly a follow-up decision, not answered here --
 * this page only makes the existing gap visible.
 */
(function () {
  'use strict';

  // Live push since v0.78.0 (2026-09-26, see core.js's attachLive()):
  // detach() of the current subscription.
  let liveDetach = null;
  // Scene ids of every release in "Downloading & importing" at the last render.
  let listedDownloadIds = new Set();
  // Renders can overlap (a push event arrives while one is still loading):
  // only the newest one gets to put its result on the page.
  let renderSeq = 0;

  let uploadEnabled = false; // Settings > fingerprint_upload_enabled, set by render(), read by buildSection()'s Import failed rows

  async function render(container) {
    const { el } = window.SRW;
    const mySeq = ++renderSeq;
    if (liveDetach) { liveDetach(); liveDetach = null; }
    // Built off-page and swapped in at the end -- a re-render keeps the
    // current content visible until the new one is ready (no flicker).
    const root = el('div');
    root.appendChild(window.SRW.buildPageHeader('activity'));
    const hint = window.SRW.buildLiveHint();
    hint.classList.add('srw-activity-page'); // marks "the Activity page is showing"
    root.appendChild(hint);
    const wrap = el('div');
    root.appendChild(wrap);
    if (!container.querySelector('.srw-activity-page')) {
      container.replaceChildren();
      container.appendChild(window.SRW.buildPageHeader('activity'));
      container.appendChild(el('div', { className: 'srw-activity-page', textContent: 'Loading...' }));
    }
    // Only if this is still the newest render AND the user hasn't moved
    // on to another page meanwhile (they share `container`).
    const show = () => {
      if (mySeq === renderSeq && container.querySelector('.srw-activity-page')) {
        container.replaceChildren(...root.childNodes);
      }
    };
    // Must resolve before any row builds a scene link (sceneUrl() reads
    // this cache synchronously) -- see core.js's own comment.
    await window.SRW.loadMetadataProviderUrls();

    // All rows, no hidden cap (2026-09-26: was `limit: 500` per list) --
    // per_page 0 = unpaged; each section below pages + filters itself.
    uploadEnabled = false; // read from the settings just below, used by the Import failed rows (module-level: buildSection() reads it)
    // Settings > Content = movies only: no scene queries at all (same as the Releases page).
    const scenesOn = await window.SRW.scopeAllows('scenes');
    const fetchAll = (status) => (scenesOn ? window.SRW.runOp('releases_query', { statuses: [status], per_page: 0 }) : { rows: [] });
    // "Imported" moved to the History page (2026-09-28): Activity shows
    // only what's running or needs attention, like the *arrs.
    const [responses, reviews, appSettings, filmsData] = await Promise.all([
      Promise.all([fetchAll('sent_to_whisparr'), fetchAll('import_failed')]),
      scenesOn ? window.SRW.runOp('releases_reviews') : {},
      window.SRW.runOp('settings_get_all').catch(() => ({})),
      (await window.SRW.scopeAllows('movies')) ? window.SRW.runOp('films_activity').catch(() => ({ rows: [] })) : { rows: [] }, // Movies wishlist: downloading / importing / import failed (v0.126.0; not with Settings > Content = scenes)
    ]);
    const films = (filmsData && filmsData.rows) || [];
    // Movies sit in the same sections as scenes (v0.126.1): downloading / importing, source file missing (the download left no file to
    // import), import failed. Stuck and Needs review follow with the automatic movie search.
    const fileMissing = (f) => f.status === 'grabbed' && /^Downloaded file not found/.test(f.import_error || '');
    const filmsMissing = films.filter(fileMissing);
    const filmsDownloading = films.filter((f) => f.status === 'grabbed' && !fileMissing(f));
    const filmsFailed = films.filter((f) => f.status === 'import_failed');
    const filmsReview = films.filter((f) => f.status === 'wanted' && f.review_candidate); // the automatic search found something to decide on
    const filmsStuck = films.filter((f) => f.status === 'wanted' && !f.review_candidate);   // searched, nothing usable
    // Settings > Schedule & Import: may the fingerprints of a confirmed file be submitted to StashDB?
    uploadEnabled = !!appSettings && appSettings.fingerprint_upload_enabled === 'true';
    const firstError = responses.find((r) => r.error);
    if (firstError) {
      wrap.textContent = `Error: ${firstError.error}`;
      show();
      return;
    }
    if (mySeq !== renderSeq) return;
    const [sent, failed] = responses.map((r) => r.rows);

    const sentGrouped = window.SRW.groupByScene(sent);
    await window.SRW.fillCast(sentGrouped); // the cast next to the studio on the Stuck / wishlist rows
    // Only releases with an actual download job -- the rest of
    // 'sent_to_whisparr' is either still searching (nothing to show yet)
    // or "stuck" below (see the Releases page's own "On Wishlist"
    // section for the broader queued/searching view).
    const downloading = sentGrouped.filter((r) => r.download_job_id);
    listedDownloadIds = new Set(downloading.map((r) => r.scene_stashdb_id));
    // One-file movies carry their progress under "film:<id>" in the same live map (api/routers/releases.py download_progress()).
    films.filter((f) => f.status === 'grabbed' && f.mode === 'file').forEach((f) => listedDownloadIds.add(f.key));
    // "Stuck" (2026-09-21, user report -- a real release, "All Natural
    // Beauty Melanie Marie Creampie", sat here with a real
    // `last_search_result` of 'no_quality_match' and was invisible on
    // both the Calendar -- see releases.js's own isStuck()/pill-color
    // fix -- and here, since neither "Downloading" nor "Import failed"
    // covered it): searched at least once, nothing usable found, so it
    // won't progress on its own without either a config change or a
    // new indexer result appearing.
    const stuck = sentGrouped.filter((r) => !r.download_job_id && r.last_search_result);
    // "Source file missing" (2026-09-23, see file_importer.py's own
    // 'source_missing' failure path / project_roadmap_todo.md memory's
    // "Import-failed reason + conditional Retry" entry) -- deliberately
    // a SEPARATE section from "Stuck" above, not folded in: this is a
    // download that WAS grabbed (or is import-pending) but whose file
    // couldn't be found on disk when this sidecar tried to import it,
    // a different condition from "searched, nothing usable found".
    // Status stays 'sent_to_whisparr' on purpose (see
    // release_repo.set_import_error_for_scene()'s own docstring) so the
    // existing automatic per-cycle retry keeps running -- this section
    // only makes that previously-silent condition visible, it's not a
    // dead end needing a Retry button of its own.
    const sourceMissing = sentGrouped.filter((r) => !r.download_job_id && r.import_error_code === 'source_missing');

    // Every open "possible match" decision in one place (2026-09-28, user
    // request) -- before: wishlist ones only inside the Releases page's
    // collapsed "On Wishlist" rows, upgrade ones nowhere at all.
    const progressElements = new Map();
    const sections = [
      ['review', 'Needs review', buildNeedsReview(reviews, container, filmsReview)],
      ['downloading', 'Downloading & importing', await buildSection('Downloading & importing', downloading, {
        emptyText: 'Nothing downloading or importing right now.',
        showProgress: true, progressElements, movieRows: movieRows(filmsDownloading, progressElements, container),
      })],
      ['stuck', 'Stuck', await withStuckToolbar(await buildSection('Stuck (searched, nothing usable found)', stuck, {
        emptyText: 'Nothing stuck right now.',
        showSearchStatus: true, showSearchActions: true, showImportError: true, sortBy: 'last_search_at', onChanged: () => render(container),
        movieRows: movieRows(filmsStuck, progressElements, container),
      }))],
      ['missing', 'Source file missing', await buildSection('Source file missing (still retrying automatically)', sourceMissing, {
        emptyText: 'Nothing missing right now.',
        showImportError: true, showSearchActions: true, sortBy: 'discovered_at', onChanged: () => render(container),
        movieRows: movieRows(filmsMissing, progressElements, container),
      })],
      ['failed', 'Import failed', await buildSection('Import failed', window.SRW.groupByScene(failed), {
        emptyText: 'No failed imports.',
        showImportError: true, showFixWrongDownload: true, sortBy: 'discovered_at', onChanged: () => render(container),
        movieRows: movieRows(filmsFailed, progressElements, container),
      })],
    ];
    // The sections are tabs (v0.102.2), like on the Releases page: [Needs review (4)] [Downloading & importing (0)] ...
    // Their long headings stay on the sections themselves (hidden by CSS) and carry the live numbers into the tab labels.
    sections.forEach(([, , g]) => { g.open = true; g.classList.add('srw-panel-flat'); });
    wrap.classList.add('srw-activity-tabbed');
    wrap.appendChild(window.SRW.buildPaneTabs(
      sections.map(([key, short, g]) => ({ key, label: () => window.SRW.sectionTabLabel(g, short), pane: g, watch: g.querySelector('summary') })),
      'srw:activity-section',
    ));
    sections.forEach(([, , g]) => wrap.appendChild(g));

    if (mySeq !== renderSeq) return;
    show();

    // Pushed by the sidecar since v0.78.0 (was: polled every 5/10 s, user
    // report 2026-09-26: a download started while the page was open only
    // showed up after a reload): progress bars update in place, any
    // release change re-renders. Without a live connection the old
    // polling runs instead (5 s while something's running, else 10 s).
    liveDetach = window.SRW.attachLive(hint, {
      onProgress: (data) => applyProgressData(progressElements, container, data),
      // (a search panel the user has open stays until it is closed, see whenNoOpenPanel)
      onChange: () => window.SRW.whenNoOpenPanel(container, () => render(container)),
      onResync: () => window.SRW.whenNoOpenPanel(container, () => render(container)),
      poll: () => updateDownloadProgress(progressElements, container),
      pollMs: progressElements.size > 0 ? 5000 : 10000,
      hint,
    });
  }

  // "Search all stuck now" (v0.201.0): ONE targeted indexer query for every stuck scene release, once, on request (the normal RSS mode only
  // queries a release daily in its first week, then weekly). The sidecar lists what it would query first (dry run) and the click confirms it.
  async function withStuckToolbar(group, container) {
    const { el, confirmInline } = window.SRW;
    let preview = null;
    try { preview = await window.SRW.runOp('indexer_search_stuck_preview'); } catch (e) { preview = null; }
    if (!preview || preview.error || !preview.count) return group;
    const status = el('span', { className: 'srw-key-status' });
    const btn = el('button', {
      type: 'button', className: 'srw-btn', textContent: `Search all stuck now (${preview.count})`,
      title: 'One targeted indexer query per stuck release, right now. Releases already found to be duplicates are left out. Scenes only; the movie search runs on its own.',
    });
    btn.addEventListener('click', () => confirmInline(btn, `Send ${preview.count} indexer queries now, one per stuck release?`, async () => {
      status.textContent = ' starting...';
      try {
        const r = await window.SRW.runOp('indexer_search_run_stuck');
        status.textContent = r.error ? ` error: ${r.error}`
          : (r.started ? ` running in the background (${r.count} release(s)) -- the result appears in History; reload this page afterwards` : ' nothing to search');
      } catch (err) { status.textContent = ` error: ${err.message}`; }
    }));
    const bar = el('div', { className: 'srw-toolbar' }, [btn, status]);
    const summary = group.querySelector(':scope > summary');
    if (summary) summary.after(bar); else group.prepend(bar);
    return group;
  }

  async function buildSection(label, releases, {
    emptyText, showProgress, progressElements, showSearchStatus, sortBy, collapsed,
    showImportError, showFixWrongDownload, onChanged, showThumb = true, rememberKey, showSearchActions, movieRows = [], openScene = false,
  } = {}) {
    const {
      el, sceneUrl, buildEntityIcons, buildLocalEntityLink, buildReleaseThumb, buildStudioContext, confirmInline,
    } = window.SRW;
    // Movies of the Movies wishlist are items of the SAME list as the scenes (v0.128.4): one filter bar, one pager, one sort order.
    // `movieRows` = the finished <li> of movieRows() below, each carrying its movie in `li._film`.
    const movieItems = movieRows.map((li) => {
      const f = li._film || {};
      return { _filmLi: li, title: f.title, studio_name: f.studio_name || '', scene_date: (f.release_date || '').slice(0, 10),
               _when: f.imported_at || f.last_search_at || f.grabbed_at || '' };
    });
    const sorted = (sortBy
      ? [...releases, ...movieItems].sort((a, b) => ((b[sortBy] || b._when || '')).localeCompare((a[sortBy] || a._when || '')))
      : [...releases, ...movieItems]);
    const group = el('details', { className: 'srw-date-group' }, [
      el('summary', { textContent: `${label} — ${sorted.length}` }),
    ]);
    // Open/closed remembered per browser (2026-09-25), `collapsed` is
    // only the default until the user toggles it -- see core.js's
    // rememberOpen().
    window.SRW.rememberOpen(group, rememberKey || `activity:${label}`, !collapsed);
    if (sorted.length === 0) {
      group.appendChild(el('p', { className: 'srw-hint', textContent: emptyText || 'Nothing here.' }));
      return group;
    }
    // Paged at 40 per page (2026-09-25, user request -- "Imported" only
    // ever grows); with the shared filter bar since 2026-09-26. Rows are
    // only built for the visible page.
    function renderRow(r) {
      if (r._filmLi) return r._filmLi; // a movie
      const li = [
        showThumb ? buildReleaseThumb(r) : null,
        buildEntityIcons(r.entity_types),
        buildLocalEntityLink(r),
        buildStudioContext(r),
        el('a', {
          className: 'srw-release-title', href: sceneUrl(r.provider, r.scene_stashdb_id),
          target: '_blank', rel: 'noopener noreferrer', textContent: r.title || '(untitled)',
        }),
        el('span', { className: 'srw-film-class srw-film-class-likely', textContent: 'Scene' }), // like the Movie pill
      ].filter(Boolean);
      if (openScene) { // the scene in Stash: the link is set once the page's local scene ids are known (resolveOpenScenes)
        li.push(el('a', { className: 'srw-btn srw-open-scene', textContent: 'Open scene', target: '_blank', rel: 'noopener noreferrer', hidden: true,
          'data-provider': r.provider, 'data-scene': r.scene_stashdb_id, title: 'Open this scene in Stash (new tab)' }));
      }
      // The scene's release date comes first in the row (arrangeReleaseRow); this timestamp says what happened when.
      const when = r.imported_at || r.handoff_at || r.discovered_at;
      const whenLabel = r.imported_at ? 'imported' : (r.handoff_at ? 'added' : 'found');
      li.push(el('span', { className: 'srw-release-date', title: 'When this happened',
        textContent: when ? `${whenLabel} ${when.slice(0, 16).replace('T', ' ')}` : '' }));
      if (showSearchStatus) {
        li.push(el('span', {
          className: 'srw-search-status',
          title: r.last_search_at ? `Last checked: ${r.last_search_at.slice(0, 16).replace('T', ' ')}` : '',
          textContent: window.SRW.searchStatusLabel(r.download_job_id, r.last_search_result),
        }));
      }
      // The same search actions as on the Releases page's On Wishlist rows (v0.106.9): Last search (what was searched and why
      // nothing was taken), Search via Whisparr, and Search… (own query + Grab).
      if (showSearchActions) li.push(window.SRW.buildSearchActions(r, () => (onChanged ? onChanged() : null)));
      if (showSearchActions && r.status === 'sent_to_whisparr' && !r.download_job_id) li.push(window.SRW.buildWishlistDismiss(r, () => (onChanged ? onChanged() : null)));
      if (showProgress && r.download_job_id) {
        const fill = el('div', { className: 'srw-download-progress-fill' });
        const text = el('span', { className: 'srw-download-progress-text' });
        const wrap = el('span', { className: 'srw-download-progress' }, [
          el('div', { className: 'srw-download-progress-track' }, [fill]),
          text,
        ]);
        li.push(wrap);
        progressElements.set(r.scene_stashdb_id, { fill, text, wrap });
      }
      // Persisted failure reason (2026-09-23) -- see
      // release_repo.set_import_error_for_scene(). Shown as plain text,
      // same spot searchStatusLabel occupies above, since the two never
      // appear in the same section.
      // Imported without fingerprint verification (2026-09-25): the
      // provider had no fingerprints for this scene yet; re-checked
      // automatically, see file_importer.recheck_unverified_imports().
      if (r.import_error_code === 'unverified') {
        li.push(el('span', {
          className: 'srw-unverified-badge', textContent: 'unverified',
          title: r.import_error_detail || 'Imported without fingerprint verification -- re-checked automatically',
        }));
      }
      if (showImportError && r.import_error_detail) {
        li.push(el('span', { className: 'srw-search-status', textContent: r.import_error_detail }));
        // When that download was grabbed (2026-09-26): a second wrong
        // download otherwise reads exactly like the first one.
        if (r.grabbed_at) {
          const at = new Date(`${r.grabbed_at.replace(' ', 'T')}Z`);
          li.push(el('span', {
            className: 'srw-release-date', title: r.grabbed_title || '',
            textContent: `downloaded ${Number.isNaN(at.getTime()) ? r.grabbed_at : at.toLocaleString()}`,
          }));
        }
      } else if (showFixWrongDownload && r.status === 'import_failed' && !r.import_error_code) {
        // No stored reason (2026-09-26): failures from before v0.51
        // recorded why -- say so instead of showing nothing.
        li.push(el('span', {
          className: 'srw-search-status',
          textContent: 'Failed before failure reasons were recorded -- search again or dismiss',
        }));
      }
      // Look at the downloaded video before deciding (v0.106.4): the scene Stash created from the imported file, as a real link
      // (new tab; never blocked like a popup). The scene is looked up when the row is drawn; no file in Stash -> no link.
      if (r.status === 'import_failed' && r.pending_import_path) {
        const openLink = el('a', {
          className: 'srw-btn', textContent: 'Open file in Stash', target: '_blank', rel: 'noopener noreferrer', hidden: true,
          title: 'Open the downloaded file (the scene Stash created from it) in a new tab to play it',
        });
        window.SRW.runOp('releases_imported_file', { provider: r.provider, scene_stashdb_id: r.scene_stashdb_id }).then((found) => {
          if (found && found.scene_id) { openLink.href = `/scenes/${found.scene_id}`; openLink.hidden = false; }
        }).catch(() => {});
        li.push(openLink);
      }
      // Generic actions for failures without an automatic fix (2026-09-26):
      // back on the wishlist for a new search, or dismiss.
      if (showFixWrongDownload && r.status === 'import_failed' && r.import_error_code !== 'fingerprint_mismatch') {
        const actStatus = el('span', { className: 'srw-key-status' });
        if (r.import_error_code !== 'scene_gone') {
          li.push(el('button', {
            className: 'srw-btn srw-btn-primary', textContent: 'Search again',
            title: 'Back on the wishlist -- the next search run looks for it again',
            onClick: async () => {
              const result = await window.SRW.runOp('releases_retry_failed', {
                provider: r.provider, scene_stashdb_id: r.scene_stashdb_id,
              });
              if (result.error) { actStatus.textContent = ` error: ${result.error}`; return; }
              window.SRW.notify('Search again', `"${r.title}" is back on the wishlist.`, 'success');
              if (onChanged) onChanged();
            },
          }));
        }
        li.push(el('button', {
          className: 'srw-btn srw-btn-dismiss', textContent: 'Dismiss',
          onClick: async () => {
            const result = await window.SRW.runOp('releases_dismiss', {
              provider: r.provider, scene_stashdb_id: r.scene_stashdb_id,
            });
            if (result && result.error) { actStatus.textContent = ` error: ${result.error}`; return; }
            window.SRW.releaseDismissedNotice(r, result, () => { if (onChanged) onChanged(); });
            if (onChanged) onChanged();
          },
        }));
        li.push(actStatus);
      }
      // "Fix wrong download" retry (2026-09-23) -- ONLY for
      // 'fingerprint_mismatch' (a release this sidecar itself imported
      // that turned out not to match StashDB's own fingerprints).
      // 'scene_gone' gets no retry at all (permanent, nothing to fix);
      // 'source_missing' never reaches this section in the first place
      // (see render()'s own section split above). See
      // routers/releases.py's fix_wrong_download() for the full
      // sequence this triggers.
      if (showFixWrongDownload && r.import_error_code === 'fingerprint_mismatch') {
        const fixStatus = el('span', { className: 'srw-key-status' });
        const fixBtn = el('button', {
          className: 'srw-btn srw-btn-primary', textContent: 'Fix wrong download',
          onClick: (e) => {
            confirmInline(e.currentTarget,
              `Move the wrong file for "${r.title}" to the recycle bin, blocklist that download, and put the scene back on the wishlist for a new search?`,
              async () => {
                fixStatus.textContent = ' fixing...';
                try {
                  const result = await window.SRW.runOp('releases_fix_wrong_download', {
                    provider: r.provider, entity_type: r.entity_type, entity_stashdb_id: r.entity_stashdb_id,
                    scene_stashdb_id: r.scene_stashdb_id,
                  });
                  if (result.error) { fixStatus.textContent = ` error: ${result.error}`; return; }
                  if (onChanged) onChanged();
                } catch (err) {
                  fixStatus.textContent = ` error: ${err.message}`;
                }
              },
              { danger: true },
            );
          },
        });
        const confirmBtn = el('button', {
          className: 'srw-btn', textContent: 'File is correct',
          title: 'The video IS this scene (e.g. a re-cut nobody registered a fingerprint for): keep the file and count it as imported',
          onClick: (e) => confirmInline(e.currentTarget,
            `Keep the file for "${r.title}" although it doesn't match the fingerprints? The scene gets this release's metadata and counts as imported.`,
            async () => {
              fixStatus.textContent = ' applying...';
              const result = await window.SRW.runOp('releases_confirm_file', {
                provider: r.provider, scene_stashdb_id: r.scene_stashdb_id,
              });
              if (result.error) { fixStatus.textContent = ` error: ${result.error}`; window.SRW.notify('File is correct', result.error, 'error'); return; }
              window.SRW.notify('File is correct', `"${r.title}" is imported (marked as confirmed by you).`, 'success');
              if (onChanged) onChanged();
            }),
        });
        // + Upload (v0.106.7): the same, and afterwards the file's fingerprints go to StashDB (public). StashDB releases only; locked
        // while Settings > Schedule & Import > "Allow submitting fingerprints" is off.
        let uploadBtn = null;
        if (r.provider === 'stashdb') {
          uploadBtn = el('button', {
            className: 'srw-btn', textContent: 'Correct + submit to StashDB',
            title: uploadEnabled ? 'Like "File is correct", and the fingerprints of the file are submitted to StashDB (public, cannot be undone)'
              : 'Switched off: Settings > Schedule & Import > "Allow submitting fingerprints to StashDB"',
            ...(uploadEnabled ? {} : { disabled: true }),
            onClick: (e) => confirmInline(e.currentTarget,
              `Keep the file for "${r.title}" AND submit its fingerprints to StashDB? That is public and cannot be undone.`,
              async () => {
                fixStatus.textContent = ' applying...';
                const result = await window.SRW.runOp('releases_confirm_file', {
                  provider: r.provider, scene_stashdb_id: r.scene_stashdb_id, submit: true,
                });
                if (result.error) { fixStatus.textContent = ` error: ${result.error}`; window.SRW.notify('File is correct', result.error, 'error'); return; }
                if (result.submit_error) window.SRW.notify('Submit to StashDB', `The file is confirmed, but the submission failed: ${result.submit_error}`, 'error');
                else window.SRW.notify('File is correct', `"${r.title}" is imported and its fingerprints were submitted to StashDB.`, 'success');
                if (onChanged) onChanged();
              }, { danger: true }),
          });
        }
        li.push(confirmBtn, ...(uploadBtn ? [uploadBtn] : []), fixBtn, fixStatus);
      }
      return window.SRW.arrangeReleaseRow(el('li', { className: 'srw-release-item', 'data-scene-id': r.scene_stashdb_id }, li), r);
    }
    // Filter bar + paging (2026-09-26, user principle), same component as
    // everywhere else; the bar shows once a section has >10 entries.
    const view = window.SRW.buildFilteredPagedList({
      filters: ['search', 'description', 'type', 'vr', 'dates', 'providers'],
      perPage: 40,
      showFilterBarFrom: 10,
      fetchPage: window.SRW.localPageFetcher(() => sorted, {
        text: (r) => `${r.title || ''} ${r.studio_name || ''}`,
        description: (r) => r.details,
        type: (r) => (r.entity_types && r.entity_types.length === 1 ? r.entity_types[0] : r.entity_type),
        vr: (r) => r.is_vr,
        provider: (r) => r.provider,
        date: (r) => r.scene_date || r.scene_created,
      }),
      renderItems: (items) => {
        const ul = el('ul', { className: 'srw-release-list' }, items.map(renderRow));
        if (openScene) resolveOpenScenes(ul);
        return ul;
      },
    });
    group.appendChild(view.node);
    await view.reset(); // rows (and their progress bars) exist before the poll below starts
    return group;
  }

  // One request for the page: which of its scenes are in the library -> "Open scene" gets its link (scenes not in Stash keep it hidden).
  function resolveOpenScenes(ul) { return window.SRW.resolveSceneLinks(ul); }

  // Movie rows for the Activity sections (v0.126.0/.1): the Movies wishlist entries that are downloading / importing / missing a file /
  // failed, in the same rows-with-a-bar look as the scenes. Each row can open the list of its files as Stash scenes (thumbnail + link) to
  // check them. The full movie tools stay on the Releases page > Movies.
  function movieRows(films, progressElements, container, { thumb = true } = {}) {
    const { el, notify, confirmInline } = window.SRW;
    return films.map((f) => {
      const slot = el('div');
      const children = [
        thumb ? movieThumb(f) : null,
        el('span', { className: 'srw-release-title', textContent: `${f.title}${f.year ? ` (${f.year})` : ''}` }),
        el('span', { className: 'srw-film-class srw-film-class-sure', textContent: 'Movie' }),
      ].filter(Boolean);
      if (f.status === 'grabbed' && f.mode === 'file' && !/^Downloaded file not found/.test(f.import_error || '')) {
        const fill = el('div', { className: 'srw-download-progress-fill' });
        const text = el('span', { className: 'srw-download-progress-text' });
        const wrap = el('span', { className: 'srw-download-progress' }, [el('div', { className: 'srw-download-progress-track' }, [fill]), text]);
        children.push(wrap);
        progressElements.set(f.key, { fill, text, wrap });
      } else if (f.status === 'grabbed' && f.mode === 'scenes') {
        children.push(el('span', { className: 'srw-hint', textContent: `Put together from StashDB scenes: ${f.scenes_imported || 0} of ${f.scenes_total || 0} imported` }));
      }
      if (f.status === 'wanted') { // searched by the automatic movie search without a usable result
        const why = { no_match: 'nothing usable found', rejected: 'the last candidate was rejected', error: 'the search failed' }[f.last_search_result] || 'searched';
        children.push(el('span', { className: 'srw-search-status', textContent: `${why}${f.last_search_at ? ` (${f.last_search_at.slice(0, 16)} UTC)` : ''}` }));
        children.push(el('button', { className: 'srw-btn', textContent: 'Search now', title: 'Ask the indexers for this movie again now (1-3 requests)',
          onClick: async (e) => {
            e.currentTarget.disabled = true;
            const res = await window.SRW.runOp('films_search', { id: f.id });
            if (res.error) { notify('Movie search', res.error, 'error'); } else {
              notify('Movie search', res.result === 'needs_review' ? `Candidate found: ${res.candidate.title}` : (res.result === 'no_match' ? 'Nothing usable found.' : `Search failed: ${res.error || ''}`), res.result === 'needs_review' ? 'success' : 'info');
            }
            render(container);
          } }));
      }
      // Own query + Grab (v0.211.1), the same panel as on the Movies page and for scenes; prefill "Title Year".
      let li = null;
      if (['wanted', 'import_failed'].includes(f.status)) {
        const searchSlot = el('span', { className: 'srw-search-actions' });
        window.SRW.getSearchCapabilities().then((caps) => {
          const sources = (caps && caps.manual_sources) || [];
          if (!sources.length) return;
          const asRelease = { provider: 'theporndb', scene_stashdb_id: f.tpdb_id || `film-${f.id}`, title: `${f.title}${f.year ? ` ${f.year}` : ''}` };
          searchSlot.appendChild(window.SRW.buildManualSearch(asRelease, sources, caps.backend, () => render(container), {
            host: () => li,
            grab: (candidate) => window.SRW.runOp('films_grab', { id: f.id, candidate }),
          }));
        });
        children.push(searchSlot);
      }
      if (f.status === 'imported' && f.imported_at) children.push(el('span', { className: 'srw-release-date', textContent: `imported ${f.imported_at.slice(0, 16)}` }));
      if (f.import_error) children.push(el('span', { className: 'srw-hint', textContent: f.import_error }));
      if ((f.scenes || []).length) {
        let open = false;
        children.push(el('button', { className: 'srw-btn', textContent: `Scenes (${f.scenes.length})`,
          title: 'Open the files of this movie as Stash scenes to check that they belong together',
          onClick: () => {
            open = !open;
            slot.replaceChildren(...(open ? [buildFilmScenes(f)] : []));
          } }));
      }
      if (f.stash_group_id) {
        children.push(el('a', { className: 'srw-btn', href: `/groups/${f.stash_group_id}`, target: '_blank', rel: 'noopener noreferrer', textContent: 'Open group' }));
      }
      if (f.status === 'import_failed') {
        children.push(el('button', { className: 'srw-btn', textContent: 'Group anyway',
          title: 'Build the Group from the files as they are, although the length does not fit',
          onClick: (e) => confirmInline(e.currentTarget, `Build the Group for "${f.title}" regardless of the length?`, async () => {
            const res = await window.SRW.runOp('films_finalize', { id: f.id, force: true });
            if (res.error) { notify('Movie', res.error, 'error'); return; }
            notify('Movie', `"${f.title}": ${res.status || 'done'}`, 'success');
            render(container);
          }) }));
      }
      if (f.status === 'import_failed' && f.has_files) { // the wrong movie (e.g. another part of a series): remove it again
        const discard = (dismiss) => async () => {
          const res = await window.SRW.runOp('films_discard', { id: f.id, dismiss });
          if (res.error) { notify('Movie', res.error, 'error'); return; }
          notify('Movie', `"${f.title}": ${res.files.length} file(s) moved to the recycle bin${res.scenes.length ? `, ${res.scenes.length} Stash scene(s) removed` : ''}; `
            + (dismiss ? 'dismissed.' : 'the release is blocklisted for this movie and it is searched again.'), 'success');
          render(container);
        };
        children.push(el('button', { className: 'srw-btn', textContent: 'Wrong movie: delete + search again',
          title: 'The downloaded file(s) go to the recycle bin, their Stash scene(s) are removed, this release is never proposed again for this movie, and it is searched again',
          onClick: (e) => confirmInline(e.currentTarget, `Delete the downloaded file(s) of "${f.title}" (recycle bin) and search again?`, discard(false), { danger: true }) }));
        children.push(el('button', { className: 'srw-btn srw-btn-danger', textContent: 'Delete + dismiss',
          title: 'The downloaded file(s) go to the recycle bin, their Stash scene(s) are removed, and the movie is dismissed from the wishlist',
          onClick: (e) => confirmInline(e.currentTarget, `Delete the downloaded file(s) of "${f.title}" (recycle bin) and dismiss the movie?`, discard(true), { danger: true }) }));
      }
      // Same row layout as the scenes (cover | title + pill + meta | buttons); the opened scene list goes below the row.
      li = window.SRW.arrangeReleaseRow(el('li', { className: 'srw-release-item' }, children), { scene_date: (f.release_date || '').slice(0, 10) });
      li.appendChild(slot);
      li._film = f;
      return li;
    });
  }

  // The movie's cover (ThePornDB poster), same look as the scene thumbnails; links to the Group when it exists. A blocked/missing image leaves nothing behind.
  function movieThumb(f) {
    if (!f.image_url) return null;
    const { el } = window.SRW;
    const img = el('img', { className: 'srw-release-thumb srw-movie-cover', src: f.image_url, alt: '', loading: 'lazy' });
    const wrap = f.stash_group_id
      ? el('a', { className: 'srw-release-thumb-link', href: `/groups/${f.stash_group_id}`, target: '_blank', rel: 'noopener noreferrer' }, [img])
      : el('span', { className: 'srw-release-thumb-link' }, [img]);
    img.addEventListener('error', () => wrap.remove());
    return wrap;
  }

  // The files of one movie as Stash scenes: thumbnail + link (new tab), length, and the Group each is in.
  function buildFilmScenes(f) {
    const { el } = window.SRW;
    const total = f.scenes.reduce((n, sc) => n + (sc.minutes || 0), 0);
    return el('div', { className: 'srw-manual-search-panel' }, [
      el('p', { className: 'srw-hint', textContent: `${f.scenes.length} file(s), ${Math.round(total)} min in total. Open a scene (new tab) to check that it belongs to this movie.` }),
      ...f.scenes.map((sc) => el('div', { className: 'srw-manual-search-result srw-group-scene' }, [
        sc.scene_id
          ? el('a', { className: 'srw-vr-thumb-link', href: `/scenes/${sc.scene_id}`, target: '_blank', rel: 'noopener noreferrer', title: 'Open the scene in Stash (new tab)' },
            [el('img', { className: 'srw-vr-thumb', src: `/scene/${sc.scene_id}/screenshot`, loading: 'lazy', alt: '' })])
          : el('span', { className: 'srw-hint', textContent: 'no scene yet' }),
        el('div', {}, [
          sc.scene_id ? el('a', { className: 'srw-release-title', href: `/scenes/${sc.scene_id}`, target: '_blank', rel: 'noopener noreferrer', textContent: sc.title || sc.file })
            : el('span', { className: 'srw-release-title', textContent: sc.file }),
          el('div', { className: 'srw-hint', textContent: `${sc.file}${sc.minutes ? ` — ${sc.minutes} min` : ''}${(sc.groups || []).length ? ` — in group ${sc.groups.map((g) => g.name).join(', ')}` : ' — no group'}` }),
        ]),
      ])),
    ]);
  }

  // Same polling shape as the Releases page's own updateDownloadProgress()
  // (stash-release-manager-releases.js) -- not centralized since it's
  // the only piece of logic the two pages would share, and each page's
  // own `progressElements` map/timer variable already needs to live in
  // its own closure regardless.
  async function updateDownloadProgress(progressElements, container) {
    let data;
    try {
      data = await window.SRW.runOp('releases_download_progress');
    } catch (e) {
      return;
    }
    if (!data || data.error) return;
    applyProgressData(progressElements, container, data);
  }

  // Shared by the pushed 'progress' events and the fallback poll.
  function applyProgressData(progressElements, container, data) {
    if (!container.querySelector('.srw-activity-page')) return; // page left
    // Keeps the header's running-count indicator right even when the live stream is unavailable (this page polls then).
    window.SRW.setActivityCount(Object.values(data || {}).filter(Boolean).length);
    // Download AND import phases since 2026-09-26, see core.js's applyProgress().
    progressElements.forEach((elements, sceneId) => {
      window.SRW.applyProgress(elements, data[sceneId]);
    });
    // Re-render when the set of active downloads/imports changed: a new
    // one appeared that isn't shown yet, or a shown one finished (it then
    // moves to "Imported" / "Import failed"). Open sections stay open
    // (rememberOpen).
    // Compared against ALL listed downloads, not just the rendered page of
    // them (the section is paged) -- otherwise page 2 would re-render forever.
    const active = new Set(Object.keys(data).filter((k) => data[k]));
    const shown = listedDownloadIds;
    const appeared = [...active].some((k) => !shown.has(k));
    const finished = [...shown].some((k) => !active.has(k));
    if (appeared || finished) render(container);
  }

  const UPGRADE_REASON_LABEL = {
    vr_2d: 'VR scene, 2D file', vr_mono: 'VR file is mono', vr_low_quality: 'weak VR quality',
    below_cutoff: 'below the quality cutoff',
  };

  function buildNeedsReview(reviews, container, filmsReview = []) {
    const { el, sceneUrl, releaseTitleNode, confirmInline } = window.SRW;
    const wishlist = (reviews && reviews.wishlist) || [];
    const upgrades = (reviews && reviews.upgrades) || [];
    const total = wishlist.length + upgrades.length + filmsReview.length;
    const group = el('details', { className: 'srw-date-group' }, [
      el('summary', { textContent: `Needs review — ${total}` }),
    ]);
    window.SRW.rememberOpen(group, 'activity:Needs review', true);
    if (reviews && reviews.error) {
      group.appendChild(el('p', { textContent: `Error: ${reviews.error}` }));
      return group;
    }
    if (!total) {
      group.appendChild(el('p', { className: 'srw-hint', textContent: 'No open decisions.' }));
      return group;
    }
    group.appendChild(el('p', {
      className: 'srw-hint',
      textContent: 'The search found a release that looks right but didn\'t clear the bar for an automatic grab. '
        + 'Accept downloads it, Reject blocklists it for this scene.',
    }));

    // `sceneDate` (wishlist rows): a candidate release that is much OLDER than the scene's own release date cannot be this scene.
    function candidateLine(c, sceneDate) {
      const facts = [c.indexer, c.protocol === 'torrent' ? `Torrent${c.seeders != null ? `, ${c.seeders} seeders` : ''}` : null,
        c.size ? `${(c.size / 1e9).toFixed(1)} GB` : null,
        c.age != null ? `${c.age} days old` : null].filter(Boolean).join(' · ');
      let warning = null;
      if (sceneDate && c.age != null) {
        const sceneAge = Math.floor((Date.now() - new Date(`${sceneDate}T00:00:00`).getTime()) / 86400000);
        if (Number.isFinite(sceneAge) && c.age > sceneAge + 30) {
          warning = el('span', {
            className: 'srw-review-warning',
            title: `The scene is dated ${sceneDate} (${Math.max(sceneAge, 0)} days ago); this release was posted ${c.age} days ago.`,
            textContent: 'older than the scene — probably a different release',
          });
        }
      }
      return el('div', { className: 'srw-review-box' }, [
        releaseTitleNode(c.title, c.infoUrl),
        facts ? el('span', { className: 'srw-release-date', textContent: facts }) : null,
        warning,
      ]);
    }

    // Cover of a scene row (v0.131.2): the release's own image (wishlist) or the Stash screenshot (upgrade); a missing image leaves nothing behind.
    function reviewThumb(src, href) {
      if (!src) return null;
      const img = el('img', { className: 'srw-release-thumb', src, alt: '', loading: 'lazy' });
      const wrap = href
        ? el('a', { className: 'srw-release-thumb-link', href, target: '_blank', rel: 'noopener noreferrer' }, [img])
        : el('span', { className: 'srw-release-thumb-link' }, [img]);
      img.addEventListener('error', () => wrap.remove());
      return wrap;
    }

    // One row: what the scene is + the candidate on the left, the decision on the right.
    function reviewRow(head, candidate, buttons, thumb = null) {
      return el('li', { className: `srw-release-item srw-review-row${thumb ? ' srw-review-row-thumb' : ''}` }, [
        thumb,
        el('div', { className: 'srw-review-info' }, [el('div', { className: 'srw-review-head' }, head), candidate]),
        el('div', { className: 'srw-review-actions' }, buttons),
      ]);
    }

    function decisionButtons(onAccept, onReject, title) {
      const status = el('span', { className: 'srw-key-status' });
      const run = async (fn, label) => {
        status.textContent = ` ${label}...`;
        try {
          const result = await fn();
          if (result && result.error) { status.textContent = ` error: ${result.error}`; return; }
          render(container);
        } catch (err) {
          status.textContent = ` error: ${err.message}`;
        }
      };
      return [
        el('button', {
          className: 'srw-btn srw-btn-primary', textContent: 'Accept',
          onClick: (e) => confirmInline(e.currentTarget, `Download "${title}"?`, () => run(onAccept, 'grabbing')),
        }),
        el('button', {
          className: 'srw-btn', textContent: 'Reject',
          onClick: (e) => confirmInline(e.currentTarget, `Blocklist "${title}" for this scene?`,
            () => run(onReject, 'rejecting'), { danger: true }),
        }),
        status,
      ];
    }

    const ul = el('ul', { className: 'srw-release-list' });
    wishlist.forEach((r) => {
      const c = r.review_candidate || {};
      ul.appendChild(reviewRow([
        el('span', { className: 'srw-history-category', textContent: 'Wishlist' }),
        el('a', {
          className: 'srw-release-title', href: sceneUrl(r.provider, r.scene_stashdb_id),
          target: '_blank', rel: 'noopener noreferrer', textContent: r.title || '(untitled)',
        }),
        el('span', { className: 'srw-release-date', textContent: [r.studio_name, r.scene_date].filter(Boolean).join(' · ') }),
      ], candidateLine(c, r.scene_date), decisionButtons(
        () => window.SRW.runOp('releases_review_candidate_accept', { provider: r.provider, scene_stashdb_id: r.scene_stashdb_id }),
        () => window.SRW.runOp('releases_review_candidate_reject', { provider: r.provider, scene_stashdb_id: r.scene_stashdb_id }),
        c.title,
      ), reviewThumb(r.image_url, sceneUrl(r.provider, r.scene_stashdb_id))));
    });
    filmsReview.forEach((f) => { // movies: the automatic movie search found a plausible release (Reject = never this release for this movie)
      const c = f.review_candidate || {};
      ul.appendChild(reviewRow([
        el('span', { className: 'srw-history-category', textContent: 'Movie' }),
        el('span', { className: 'srw-release-title', textContent: `${f.title}${f.year ? ` (${f.year})` : ''}` }),
      ], candidateLine(c), decisionButtons(
        () => window.SRW.runOp('films_review_accept', { id: f.id }),
        () => window.SRW.runOp('films_review_reject', { id: f.id }),
        c.title,
      ), movieThumb(f)));
    });
    upgrades.forEach((u) => {
      const c = u.review_candidate || {};
      const now = u.cur_width ? `now ${u.cur_width}x${u.cur_height}` : '';
      ul.appendChild(reviewRow([
        el('span', {
          className: 'srw-history-category',
          textContent: `Upgrade · ${UPGRADE_REASON_LABEL[u.reason] || u.reason}`,
        }),
        el('a', {
          className: 'srw-release-title', href: `/scenes/${u.scene_id}`,
          target: '_blank', rel: 'noopener noreferrer', textContent: u.scene_title || '(untitled)',
        }),
        now ? el('span', { className: 'srw-release-date', textContent: now }) : null,
      ], candidateLine(c), decisionButtons(
        () => window.SRW.runOp('upgrades_review_accept', { id: u.id }),
        () => window.SRW.runOp('upgrades_review_reject', { id: u.id }),
        c.title,
      ), reviewThumb(u.scene_id ? `/scene/${u.scene_id}/screenshot` : null, u.scene_id ? `/scenes/${u.scene_id}` : null)));
    });
    group.appendChild(ul);
    return group;
  }

  // buildSection is shared with the History page's "Imported" list.
  window.SRW_Activity = { render, buildSection, movieRows };
})();
