"""Can the sidecar see the folder a download client puts finished downloads in? (v0.252.0)

The importer can only take what it can see. A torrent client calls its folders by its own paths (/downloads/complete/...), the sidecar has the
same folder mounted under another name; the pair in the client's settings translates one into the other. With the pair wrong or empty the finished
download waits for import for ever -- this check finds that when the client is checked, not days later."""
import os


def check_folder(client_path, remote_root, local_mount):
    """`client_path`: the completed folder as the client reports it. `remote_root` / `local_mount`: the pair as the importer uses it (the client's
    own pair, else the SABnzbd downloads pair of Settings > File Import). Returns {known, ok, client_path, local_path, message}."""
    client_path = (client_path or "").strip().rstrip("/")
    if not client_path:
        return {"known": False, "ok": None, "message": "the client does not say where it puts finished downloads, so the folder cannot be checked"}
    if not os.path.isabs(client_path):
        return {"known": False, "ok": None, "client_path": client_path,
                "message": f"the client reports its completed folder as {client_path} (not an absolute path), so it cannot be checked from here"}
    root = (remote_root or "").strip().rstrip("/")
    mount = (local_mount or "").strip().rstrip("/")
    if root or mount:
        if not client_path.startswith(root):
            return {"known": True, "ok": False, "client_path": client_path, "local_path": None,
                    "message": f"the client puts finished downloads in {client_path}, which is not inside {root or '(empty)'} -- the "
                               "completed-downloads folder as the client calls it is entered wrongly"}
        local_path = mount + client_path[len(root):]
    else:
        local_path = client_path
    if os.path.isdir(local_path):
        return {"known": True, "ok": True, "client_path": client_path, "local_path": local_path,
                "message": f"completed folder visible: {client_path} is {local_path} here"}
    return {"known": True, "ok": False, "client_path": client_path, "local_path": local_path,
            "message": f"this program cannot see the completed folder: the client uses {client_path}, here it would be {local_path}, which does not exist. "
                       "Mount the client's completed-downloads folder into this container and enter both paths (as the client calls it / as this program sees it)"}
