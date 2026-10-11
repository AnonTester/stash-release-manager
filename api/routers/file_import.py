from fastapi import APIRouter

router = APIRouter(prefix="/file-import", tags=["file-import"])

_run_import = None
_last_result = None
_get_whisparr_client = None
_get_file_import_source = None
_run_recycle_bin_cleanup = None
_last_recycle_bin_result = None
_on_scene_created = None
_on_download_complete = None


def init_file_import_router(run_import, get_whisparr_client=None, get_file_import_source=None,
                             run_recycle_bin_cleanup=None, on_scene_created=None, on_download_complete=None):
    global _run_import, _get_whisparr_client, _get_file_import_source, _run_recycle_bin_cleanup
    global _on_scene_created, _on_download_complete
    _on_scene_created = on_scene_created
    _on_download_complete = on_download_complete
    _run_import = run_import
    _get_whisparr_client = get_whisparr_client
    _get_file_import_source = get_file_import_source
    _run_recycle_bin_cleanup = run_recycle_bin_cleanup


@router.post("/run")
def run_now():
    """Manual "run file import now" trigger -- same pattern as
    /scan/run and /import-check/run."""
    global _last_result
    _last_result = _run_import()
    return _last_result


@router.post("/scene-created")
def scene_created(body: dict):
    """Called by the plugin's Stash `Scene.Create.Post` hook (2026-09-25)
    for every scene Stash creates -- verifies it right away if it's one
    of this pipeline's own pending imports, see
    file_importer.verify_created_scene(). Must stay cheap: a big library
    scan calls this once per new scene."""
    scene_id = body.get("scene_id")
    if scene_id is None:
        return {"matched": False}
    return _on_scene_created(scene_id)


@router.post("/download-complete")
def download_complete(body: dict):
    """SABnzbd completion webhook (2026-09-25) -- called by the
    post-processing script sabnzbd/srw_notify.py after every
    finished SABnzbd job (not only ours: it's set on SABnzbd's default
    category). Returns immediately; see main.py's on_download_complete()."""
    return _on_download_complete(body.get("nzo_id"), body.get("status"))


@router.get("/status")
def status():
    return {"last_result": _last_result}


@router.get("/source-status")
def source_status():
    """Backs the Settings page's file-import-source switch (2026-09-19,
    see routers/settings.py's `file_import_source` DEFAULTS entry) --
    shows the currently-saved setting alongside Whisparr's own *live*
    `enableCompletedDownloadHandling` state, so a mismatch (e.g. the
    setting says "sidecar_sabnzbd" but Whisparr's own toggle is still
    ON, or vice versa) is visible instead of silently producing zero
    imports on either side."""
    configured = _get_file_import_source()
    try:
        whisparr_cdh_enabled = _get_whisparr_client().get_completed_download_handling()
    except Exception as e:
        return {"file_import_source": configured, "whisparr_cdh_enabled": None, "in_sync": None, "error": str(e)}
    expected_cdh = configured == "whisparr_built_in"
    return {
        "file_import_source": configured,
        "whisparr_cdh_enabled": whisparr_cdh_enabled,
        "in_sync": whisparr_cdh_enabled == expected_cdh,
    }


@router.post("/sync-source")
def sync_source():
    """Flips Whisparr's own Completed Download Handling to match the
    currently-saved `file_import_source` setting -- an explicit action
    rather than an automatic side effect of saving the setting, so
    changing the dropdown never silently reaches out to Whisparr on its
    own; the Settings page calls this separately once the user is ready
    to apply it."""
    configured = _get_file_import_source()
    enabled = configured == "whisparr_built_in"
    whisparr_cdh_enabled = _get_whisparr_client().set_completed_download_handling(enabled)
    return {"file_import_source": configured, "whisparr_cdh_enabled": whisparr_cdh_enabled, "in_sync": True}


@router.post("/recycle-bin/cleanup")
def recycle_bin_cleanup_run():
    """Manual "clean up now" trigger -- same pattern as /run above."""
    global _last_recycle_bin_result
    _last_recycle_bin_result = _run_recycle_bin_cleanup()
    return _last_recycle_bin_result


@router.get("/recycle-bin/status")
def recycle_bin_cleanup_status():
    return {"last_result": _last_recycle_bin_result}
