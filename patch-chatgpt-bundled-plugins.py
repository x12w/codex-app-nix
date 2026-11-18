"""Make bundled-plugin copies writable without changing their Nix store source."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile


IDENTIFIER = rb"[A-Za-z_$][A-Za-z0-9_$]*"
FS_ACCESSOR = IDENTIFIER + rb"(?:\.default|\(\))"
COPY = re.compile(
    rb"await (?P<fs>" + FS_ACCESSOR + rb")\.cp\("
    rb"(?P<source>" + IDENTIFIER + rb"),(?P<target>" + IDENTIFIER + rb"),"
    rb"\{recursive:!0,verbatimSymlinks:!0\}\)"
)
REMOVE = re.compile(
    rb"await (?P<fs>" + FS_ACCESSOR + rb")\.rm\("
    rb"(?P<entry>" + IDENTIFIER + rb")\.directory,"
    rb"\{force:!0,maxRetries:5,recursive:!0,retryDelay:100\}\)"
)
HELPER_NAME = b"__nixMakeBundledPluginWritable"
HELPER = b"""
async function __nixMakeBundledPluginWritable(root, fs, directoriesOnly = false) {
  let info;
  try {
    info = await fs.lstat(root);
  } catch (error) {
    if (error.code === 'ENOENT') return;
    throw error;
  }
  // Never chmod a symlink target, which may be outside the copied plugin.
  if (info.isSymbolicLink()) return;
  if (info.isDirectory()) {
    if (!(info.mode & 0o200)) await fs.chmod(root, (info.mode & 0o7777) | 0o200);
    for (const name of await fs.readdir(root)) {
      await __nixMakeBundledPluginWritable(root + '/' + name, fs, directoriesOnly);
    }
  } else if (!directoriesOnly && info.isFile() && !(info.mode & 0o200)) {
    await fs.chmod(root, (info.mode & 0o7777) | 0o200);
  }
}
"""


def entries(header, prefix=""):
    for name, entry in header.get("files", {}).items():
        path = f"{prefix}/{name}".lstrip("/")
        if "files" in entry:
            yield from entries(entry, path)
        elif "offset" in entry and not entry.get("unpacked"):
            yield path, entry


def read_header(archive):
    size_size, header_size, pickle_size, json_size = struct.unpack("<4I", archive.read(16))
    if size_size != 4 or pickle_size + 4 != header_size or json_size + 4 > pickle_size:
        raise ValueError("Unknown ASAR format")
    return json.loads(archive.read(json_size)), 8 + header_size


def integrity(data, block_size):
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(data).hexdigest(),
        "blockSize": block_size,
        "blocks": [
            hashlib.sha256(data[start : start + block_size]).hexdigest()
            for start in range(0, len(data), block_size)
        ],
    }


def encode_header(header):
    raw = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode()
    payload = struct.pack("<I", len(raw)) + raw
    payload += b"\0" * (-len(payload) % 4)
    pickle = struct.pack("<I", len(payload)) + payload
    return struct.pack("<II", 4, len(pickle)) + pickle


def read_bundle(path):
    with open(path, "rb") as archive:
        header, base = read_header(archive)
        candidates = []
        # New releases split the main process into early-bootstrap-* chunks.
        # Locate the plugin operations, not a particular bundler's filename.
        for name, entry in entries(header):
            if not (name.startswith(".vite/build/") and name.endswith(".js")):
                continue
            archive.seek(base + int(entry["offset"]))
            data = archive.read(entry["size"])
            if COPY.search(data) or REMOVE.search(data):
                candidates.append((name, entry, data))
        if len(candidates) != 1:
            raise ValueError("Bundled-plugin bundle changed; review this workaround")
        name, entry, original = candidates[0]
    return header, base, name, entry, original


def copy_bytes(source, target, count):
    while count:
        chunk = source.read(min(count, 1024 * 1024))
        if not chunk:
            raise ValueError("Truncated ASAR archive")
        target.write(chunk)
        count -= len(chunk)


def patch(path):
    path = Path(path)
    header, base, name, entry, original = read_bundle(path)
    copies, removals = list(COPY.finditer(original)), list(REMOVE.finditer(original))
    if (
        len(copies) != 1 or len(removals) != 1 or HELPER_NAME in original
        or copies[0]["fs"] != removals[0]["fs"]
    ):
        raise ValueError("Bundled-plugin code changed; review this workaround")
    old_integrity = entry.get("integrity", {})
    block_size = old_integrity.get("blockSize", 0)
    if block_size <= 0 or old_integrity != integrity(original, block_size):
        raise ValueError("Bundled-plugin ASAR integrity check failed")

    # Keep these as expressions: the new Linux branch is `return void await
    # fs().cp(...)`, so inserting a statement after it would never run.
    updated = COPY.sub(
        lambda match: b"(" + match[0] + b",await " + HELPER_NAME
        + b"(" + match["target"] + b"," + match["fs"] + b"))",
        original,
    )
    updated = REMOVE.sub(
        lambda match: b"(await " + HELPER_NAME + b"(" + match["entry"]
        + b".directory," + match["fs"] + b",true)," + match[0] + b")",
        updated,
    ) + HELPER
    old_offset = int(entry["offset"])
    old_size = entry["size"]
    delta = len(updated) - old_size
    for _, other in entries(header):
        if int(other["offset"]) > old_offset:
            other["offset"] = str(int(other["offset"]) + delta)
    entry["size"] = len(updated)
    entry["integrity"] = integrity(updated, block_size)

    # Rebuild the header and stream the payload: the added helper changes both
    # the plugin bundle's length and subsequent entries' offsets. Keep all other
    # payloads, unpacked flags, and per-file integrity metadata intact.
    temporary = None
    try:
        with path.open("rb") as source, tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".bundled-plugins-", delete=False
        ) as target:
            temporary = Path(target.name)
            os.fchmod(target.fileno(), path.stat().st_mode & 0o7777)
            target.write(encode_header(header))
            source.seek(base)
            copy_bytes(source, target, old_offset)
            target.write(updated)
            source.seek(base + old_offset + old_size)
            shutil.copyfileobj(source, target, length=1024 * 1024)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Patched {name}: writable bundled-plugin copies and read-only directory cleanup")


if __name__ == "__main__":
    patch(sys.argv[1])
