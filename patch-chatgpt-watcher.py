"""Select the known NixOS watcher ABI without Electron's broken getReport()."""

import hashlib
import json
import struct
import sys


def patch(path):
    with open(path, "r+b") as archive:
        prefix = archive.read(16)
        size_size, header_size, pickle_size, json_size = struct.unpack("<4I", prefix)
        assert size_size == 4 and pickle_size + 4 == header_size, "Unknown ASAR format"
        raw_header = archive.read(json_size)
        header = json.loads(raw_header)
        entry = header
        for component in "node_modules/@parcel/watcher/index.js".split("/"):
            entry = entry["files"][component]
        assert not entry.get("unpacked"), "Watcher moved outside ASAR"
        offset = 8 + header_size + int(entry["offset"])
        archive.seek(offset)
        original = archive.read(entry["size"])
        old = b"const family = familySync();"
        new = b"const family = 'glibc';"
        assert original.count(old) == 1, "Watcher changed; review this workaround"
        # Keep offsets and lengths unchanged, including ASAR's header size.
        updated = original.replace(old, new.ljust(len(old)))
        integrity = entry["integrity"]
        old_hash = hashlib.sha256(original).hexdigest()
        assert integrity["algorithm"] == "SHA256"
        assert integrity["hash"] == old_hash
        assert integrity["blocks"] == [old_hash]
        assert len(original) <= integrity["blockSize"]
        new_hash = hashlib.sha256(updated).hexdigest()
        assert raw_header.count(old_hash.encode()) == 2
        updated_header = raw_header.replace(old_hash.encode(), new_hash.encode())
        assert len(updated_header) == len(raw_header)
        archive.seek(16)
        archive.write(updated_header)
        archive.seek(offset)
        archive.write(updated)
        print("Patched @parcel/watcher to select glibc; updated ASAR SHA256 hashes")


if __name__ == "__main__":
    patch(sys.argv[1])
