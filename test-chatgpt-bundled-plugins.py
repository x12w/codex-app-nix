"""Regression checks for ASAR rewriting and Nix-style read-only plugin trees."""

import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("patcher", sys.argv[1])
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)
installed_asar = Path(sys.argv[2]) if len(sys.argv) > 2 else None
sys.argv[1:] = []

FIXTURE = (
    b"async function Nne(e,t){" + patcher.COPY + b"}"
    b"async function Jo(e){" + patcher.REMOVE + b"}"
)


def make_archive(path, source=FIXTURE):
    payloads = {"before.txt": b"before", ".vite/build/main-test.js": source, "after.txt": b"after"}
    header = {"files": {}}
    offset = 0
    for name, data in payloads.items():
        parent = header
        parts = name.split("/")
        for part in parts[:-1]:
            parent = parent["files"].setdefault(part, {"files": {}})
        parent["files"][parts[-1]] = {
            "size": len(data), "offset": str(offset), "integrity": patcher.integrity(data, 64)
        }
        offset += len(data)
    header["files"]["external.node"] = {"size": 99, "unpacked": True}
    path.write_bytes(patcher.encode_header(header) + b"".join(payloads.values()))


def function(source, name):
    match = re.search(r"async function " + name + r"\(", source)
    if match is None:
        raise AssertionError(f"Missing patched function {name}")
    end = re.search(r"(?:async )?function [A-Za-z0-9_$]+\(", source[match.end():])
    return source[match.start():match.end() + end.start() if end else len(source)]


class BundledPluginTests(unittest.TestCase):
    def test_asar_offsets_hashes_and_unpacked_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.asar"
            make_archive(path)
            patcher.patch(path)
            with path.open("rb") as archive:
                header, base = patcher.read_header(archive)
                for name, entry in patcher.entries(header):
                    archive.seek(base + int(entry["offset"]))
                    data = archive.read(entry["size"])
                    self.assertEqual(entry["integrity"], patcher.integrity(data, 64))
                    if name == "before.txt":
                        self.assertEqual(data, b"before")
                    elif name == "after.txt":
                        self.assertEqual(data, b"after")
                    else:
                        self.assertIn(patcher.HELPER, data)
                self.assertEqual(header["files"]["external.node"], {"size": 99, "unpacked": True})

    def test_upstream_change_leaves_archive_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.asar"
            make_archive(path, FIXTURE.replace(patcher.COPY, b"return"))
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "code changed"):
                patcher.patch(path)
            self.assertEqual(path.read_bytes(), original)

    def test_read_only_copy_edit_cleanup_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.asar"
            if installed_asar is None:
                make_archive(path)
                patcher.patch(path)
            else:
                path = installed_asar
            _, _, _, entry, data = patcher.read_main(path)
            self.assertEqual(entry["integrity"], patcher.integrity(data, entry["integrity"]["blockSize"]))
            source = data.decode()
            self.assertIn(patcher.HELPER.decode(), source)
            code = """
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const b = {default: fs}, P = {default: process}, jo = {warning() {}};
""" + function(source, "Nne") + "\n" + function(source, "Jo") + patcher.HELPER.decode() + """
(async () => {
  assert.notEqual(process.getuid(), 0, 'Run permission regression checks as a non-root user');
  const root = process.argv[1];
  const source = path.join(root, 'source');
  const target = path.join(root, 'copy');
  const outside = path.join(root, 'outside.json');
  await fs.mkdir(path.join(source, 'nested'), {recursive: true});
  await fs.writeFile(path.join(source, 'nested/plugin.json'), '{}', {mode: 0o444});
  await fs.writeFile(path.join(source, 'run'), '#!/bin/sh', {mode: 0o555});
  await fs.writeFile(outside, 'unchanged', {mode: 0o444});
  await fs.symlink(outside, path.join(source, 'external'));
  await fs.chmod(path.join(source, 'nested'), 0o555);
  await fs.chmod(source, 0o555);
  try {
    // The unfixed copy reproduces both failures seen in the desktop logs.
    const broken = path.join(root, 'broken');
    await fs.cp(source, broken, {recursive: true, verbatimSymlinks: true});
    await assert.rejects(fs.writeFile(path.join(broken, 'nested/plugin.json'), 'changed'), {code: 'EACCES'});
    await assert.rejects(fs.rm(broken, {recursive: true, force: true}), {code: 'EACCES'});
    await Jo({directory: broken});
    await assert.rejects(fs.lstat(broken), {code: 'ENOENT'});

    await Nne(source, target);
    await fs.writeFile(path.join(target, 'nested/plugin.json'), 'changed');
    assert.equal((await fs.stat(path.join(target, 'run'))).mode & 0o777, 0o755);
    assert.equal((await fs.stat(path.join(source, 'nested/plugin.json'))).mode & 0o777, 0o444);
    assert.equal((await fs.stat(source)).mode & 0o777, 0o555);
    assert.equal((await fs.stat(outside)).mode & 0o777, 0o444);
    assert.equal(await fs.readFile(outside, 'utf8'), 'unchanged');
    await Jo({directory: target});
    await Jo({directory: target}); // Missing staging directories are normal.
    assert.equal((await fs.stat(outside)).mode & 0o777, 0o444);
    const link = path.join(root, 'root-link');
    await fs.symlink(source, link);
    await Jo({directory: link});
    assert.equal((await fs.stat(source)).mode & 0o777, 0o555);
  } finally {
    await __nixMakeBundledPluginWritable(root, fs, true);
    await fs.rm(root, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
            fixture_root = Path(directory) / "permissions"
            fixture_root.mkdir()
            subprocess.run(["node", "-e", code, str(fixture_root)], check=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
