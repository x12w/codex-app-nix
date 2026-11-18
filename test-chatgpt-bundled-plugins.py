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
    b"async function Nne(e,t){await b.default.cp(e,t,{recursive:!0,verbatimSymlinks:!0});return}"
    b"async function Jo(e){await b.default.rm(e.directory,{force:!0,maxRetries:5,recursive:!0,retryDelay:100})}"
)
WEBPACK_FIXTURE = (
    b'exports.modules={123:(e,t,r)=>{const copy=async function(e,t){'
    b'if("darwin"===tb().platform)return void await iX("/usr/bin/ditto",["--noqtn",e,t]);'
    b'if("win32"!==tb().platform)return void await tr().cp(e,t,{recursive:!0,verbatimSymlinks:!0});'
    b'let{copyDirectoryAllowDecryptedDestinationOnEncryptionFailure:n}=await r.e(904).then(r.bind(r,87115));'
    b'await n({copy:()=>tr().cp(e,t,{recursive:!0,verbatimSymlinks:!0}),destination:t,source:e})};'
    b'async function ss(e){try{await tr().rm(e.directory,{force:!0,maxRetries:5,recursive:!0,retryDelay:100})}'
    b'catch(t){throw iZ.warning("plugin_marketplace_folder_remove_failed",{sensitive:{error:t}}),t}}}};'
)
LAYOUTS = (
    (".vite/build/main-test.js", FIXTURE),
    (".vite/build/early-bootstrap-100-test.js", WEBPACK_FIXTURE),
)


def make_archive(path, source=FIXTURE, bundle_name=LAYOUTS[0][0], extra_payloads=None):
    payloads = {
        "before.txt": b"before",
        ".vite/build/early-bootstrap.js": b"// Entry point without plugin logic",
        bundle_name: source,
        "after.txt": b"after",
        **(extra_payloads or {}),
    }
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


def function(source, operation):
    """Extract the enclosing named or anonymous function from either bundle."""
    operation_match = operation.search(source.encode())
    if operation_match is None:
        raise AssertionError("Missing plugin operation")
    operation_start = len(source.encode()[:operation_match.start()].decode())
    candidates = list(re.finditer(r"async function(?: [A-Za-z0-9_$]+)?\([^)]*\)\{", source[:operation_start]))
    if not candidates:
        raise AssertionError("Missing enclosing plugin function")
    start = candidates[-1].start()
    # These small functions have quoted strings, but no regex literals or
    # interpolated templates. Track quotes so braces in strings are ignored.
    depth, quote, escaped = 0, None, False
    for index in range(candidates[-1].end() - 1, len(source)):
        char = source[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("Unterminated plugin function")


def binding(accessor, value):
    if accessor.endswith("()"):
        return f"const {accessor[:-2]} = () => {value};\n"
    if accessor.endswith(".default"):
        return f"const {accessor[:-8]} = {{default: {value}}};\n"
    return f"const {accessor} = {value};\n"


class BundledPluginTests(unittest.TestCase):
    def test_asar_offsets_hashes_and_unpacked_entries(self):
        for bundle_name, source in LAYOUTS:
            with self.subTest(bundle=bundle_name), tempfile.TemporaryDirectory() as directory:
                self.check_archive(Path(directory) / "app.asar", source, bundle_name)

    def check_archive(self, path, source, bundle_name):
        make_archive(path, source, bundle_name)
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
                elif name == bundle_name:
                    self.assertIn(patcher.HELPER, data)
                    subprocess.run(["node", "--check"], input=data, check=True)
                else:
                    self.assertEqual(data, b"// Entry point without plugin logic")
            self.assertEqual(header["files"]["external.node"], {"size": 99, "unpacked": True})

    def test_upstream_change_leaves_archive_untouched(self):
        for source in (
            patcher.COPY.sub(b"return", FIXTURE),
            FIXTURE + FIXTURE,
            FIXTURE.replace(b"b.default.rm", b"other.default.rm"),
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "app.asar"
                make_archive(path, source)
                original = path.read_bytes()
                with self.assertRaisesRegex(ValueError, "code changed"):
                    patcher.patch(path)
                self.assertEqual(path.read_bytes(), original)

    def test_ambiguous_bundle_leaves_archive_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.asar"
            make_archive(path, extra_payloads={LAYOUTS[1][0]: WEBPACK_FIXTURE})
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "bundle changed"):
                patcher.patch(path)
            self.assertEqual(path.read_bytes(), original)

    def test_read_only_copy_edit_cleanup_and_symlinks(self):
        for bundle_name, source in LAYOUTS:
            with self.subTest(bundle=bundle_name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "app.asar"
                make_archive(path, source, bundle_name)
                patcher.patch(path)
                self.check_permissions(path, directory)
        if installed_asar is not None:
            with tempfile.TemporaryDirectory() as directory:
                self.check_permissions(installed_asar, directory)

    def check_permissions(self, path, directory):
        _, _, _, entry, data = patcher.read_bundle(path)
        self.assertEqual(entry["integrity"], patcher.integrity(data, entry["integrity"]["blockSize"]))
        source = data.decode()
        self.assertIn(patcher.HELPER.decode(), source)
        subprocess.run(["node", "--check"], input=data, check=True)
        copy_function = function(source, patcher.COPY)
        remove_function = function(source, patcher.REMOVE)
        bindings = binding(patcher.COPY.search(data)["fs"].decode(), "fs")
        platform = re.search(r"(" + patcher.FS_ACCESSOR.decode() + r")\.platform", copy_function)
        if platform:
            bindings += binding(platform[1], "process")
        logger = re.search(r"(" + patcher.IDENTIFIER.decode() + r")\.warning\(", remove_function)
        if logger:
            bindings += binding(logger[1], "{warning() {}}")
        code = """
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
""" + bindings + f"const copyPlugin = ({copy_function});\nconst removePlugin = ({remove_function});\n" + patcher.HELPER.decode() + """
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
    await removePlugin({directory: broken});
    await assert.rejects(fs.lstat(broken), {code: 'ENOENT'});

    await copyPlugin(source, target);
    await fs.writeFile(path.join(target, 'nested/plugin.json'), 'changed');
    assert.equal((await fs.stat(path.join(target, 'run'))).mode & 0o777, 0o755);
    assert.equal((await fs.stat(path.join(source, 'nested/plugin.json'))).mode & 0o777, 0o444);
    assert.equal((await fs.stat(source)).mode & 0o777, 0o555);
    assert.equal((await fs.stat(outside)).mode & 0o777, 0o444);
    assert.equal(await fs.readFile(outside, 'utf8'), 'unchanged');
    await removePlugin({directory: target});
    await removePlugin({directory: target}); // Missing staging directories are normal.
    assert.equal((await fs.stat(outside)).mode & 0o777, 0o444);
    const link = path.join(root, 'root-link');
    await fs.symlink(source, link);
    await removePlugin({directory: link});
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
