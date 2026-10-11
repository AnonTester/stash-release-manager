/**
 * StashReleaseManager - Bootstrap
 * Wires route detection to the entity-panel injection and the plugin's own
 * page (Releases / Settings), mirroring stash-sense2's bootstrap pattern.
 */
(function () {
  'use strict';

  // Same duplicate-mount race as the entity panel's injectPanel() (see
  // that file's comment) -- createPluginRoot() awaits before it creates
  // anything, so two near-simultaneous calls (init()'s own call racing a
  // MutationObserver-detected change) can both find no existing root and
  // both create one. Guarded the same way, with its own token counter.
  let mountToken = 0;

  async function pluginPageMount(route) {
    // /plugins/stash-release-manager              -> releases list
    // /plugins/stash-release-manager/activity     -> download/import activity
    // /plugins/stash-release-manager/search       -> ad-hoc provider search
    // /plugins/stash-release-manager/maintenance  -> library check / bulk correction
    // /plugins/stash-release-manager/watchlist    -> watched studios/performers
    // /plugins/stash-release-manager/settings     -> settings
    const myToken = ++mountToken;
    let container = document.querySelector('#srw-plugin-root');
    if (!container) {
      container = await createPluginRoot();
      if (myToken !== mountToken) { container.remove(); return; } // superseded
    }
    if (route.subpath === '/settings') {
      window.SRW_Settings.render(container);
    } else if (route.subpath === '/setup') {
      window.SRW_Settings.renderSetup(container);
    } else if (route.subpath === '/watchlist') {
      window.SRW_Settings.renderWatchlistPage(container);
    } else if (route.subpath === '/maintenance' || route.subpath.startsWith('/maintenance/')) {
      window.SRW_Maintenance.render(container);
    } else if (route.subpath === '/search') {
      window.SRW_Search.render(container);
    } else if (route.subpath === '/activity') {
      window.SRW_Activity.render(container);
    } else if (route.subpath === '/history') {
      window.SRW_History.render(container);
    } else {
      window.SRW_Releases.render(container);
    }
  }

  async function createPluginRoot() {
    // Stash's own content area for a route it doesn't have a React
    // handler for (like our custom /plugins/<id> page) is
    // div.main.container-fluid -- confirmed live 2026-09-11 by
    // inspecting the actual DOM (nav + div.main.container-fluid +
    // toast-container, all siblings under div#root). Appending to
    // plain <body> instead (an earlier fallback) mounts outside
    // div#root entirely, losing Stash's page padding/layout.
    //
    // Must be awaited, not a synchronous querySelector -- confirmed
    // live 2026-09-11: on a fresh page load this function runs from
    // DOMContentLoaded, before Stash's React app has necessarily
    // rendered div.main.container-fluid yet, so an immediate
    // querySelector silently falls through to the body fallback every
    // time even though the element appears moments later. waitForElement
    // polls instead, the same pattern already used successfully for the
    // entity-panel injection's .tab-content wait.
    const host =
      (await window.SRW.waitForElement('.main.container-fluid', { timeout: 4000 })) ||
      document.querySelector('main') ||
      document.body;
    const root = document.createElement('div');
    root.id = 'srw-plugin-root';
    host.appendChild(root);
    return root;
  }

  async function handleRoute(route) {
    if (route.type === 'plugin') {
      await pluginPageMount(route);
      return;
    }
    document.getElementById('srw-plugin-root')?.remove();
    window.SRW_EntityPanel.injectPanel(route);
  }

  function init() {
    window.SRW.initNavigationWatcher();
    window.SRW.onNavigate(handleRoute);
    handleRoute(window.SRW.getRoute());
    console.log(`[${window.SRW.PLUGIN_NAME}] Initialized`);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
