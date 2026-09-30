# OpenAI Codex 桌面版 — Nix 包

把 OpenAI 官方发布的 Linux 版 Codex 桌面应用打包成 Nix 包。

OpenAI 在 Linux 上把它当作 `chatgpt` 这个包名发布（deb 的 `Package:` 字段就是 `chatgpt`，主页指向
`developers.openai.com/codex/app`），所以这里沿用同样的命名。nixpkgs 里已有的 `chatgpt` 只有
macOS 版，Linux 版此前没有打包。

## 包含什么

| 属性 | 说明 |
| --- | --- |
| `chatgpt` | 桌面应用本体（默认包） |
| `codex-cli` | 应用内置的 Codex agent 与 ripgrep，单独暴露，`bin/codex` + `bin/rg` |

应用自带了完整的 Electron/Chromium 运行时，以及 Codex agent（`codex`）、ripgrep、Node.js、
tectonic 等，都在 `lib/chatgpt/resources/` 下。本包只做两件事：把运行时重新链接到 Nix store，
以及生成一个指向正确库/数据目录的启动器。

deb 由 `fetchurl` 直接从 OpenAI 的软件源拉取（就是 `.deb` 的 postinst 会写进
`/etc/apt/sources.list.d/chatgpt.sources` 的那个源），仓库里不再存放二进制。

## 使用

### 直接运行

```bash
nix run /home/x12w/projects/nix/codex
```

### 作为 flake input

```nix
{
  inputs.codex.url = "path:/home/x12w/projects/nix/codex";

  outputs = { self, nixpkgs, codex, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [
        {
          environment.systemPackages = [
            codex.packages.x86_64-linux.chatgpt
          ];
        }
      ];
    };
  };
}
```

### overlay

```nix
nixpkgs.overlays = [ codex.overlays.default ];
environment.systemPackages = [ pkgs.chatgpt ];
```

## 构建

```bash
nix build .#chatgpt      # 桌面应用
nix build .#codex-cli    # 内置的 codex agent + ripgrep
```

## 说明

### Electron 沙箱

Chromium 需要非特权用户命名空间。NixOS 的 `security.allowUserNamespaces` 默认为 `true`，
所以开箱即用。如果没有用户命名空间，应用会启动失败，此时只能：

```bash
chatgpt --no-sandbox
```

这会显著削弱渲染进程的隔离，仅在万不得已时使用。

### Qt 平台后端（可选）

Chromium 只会在被要求使用 Qt 后端时才 `dlopen` 内置的 `libqt{5,6}_shim.so`，默认走 GTK
（GTK 对话框在 Plasma 下也能正常工作）。要启用 KDE 原生文件对话框：

```nix
codex.packages.x86_64-linux.chatgpt.override { withQtShims = true; }
```

代价是引入 Qt5 + Qt6 的闭包（约 +900 MB）。Plasma 用户系统里本来就有 Qt，增量接近于零。

### 关于 musl 预编译产物

应用里带了给 Alpine 用的 `*-musl.node` 预编译模块（node-hid / serialport）。它们在
glibc 系统上永远不会被选中，`autoPatchelf` 会跳过对它们的链接。

### 数据目录

`~/.config/Codex`（Electron 的 `owl-app.ini` 里 `UserDataDirectoryName=Codex`）。

## 更新

```bash
./update.sh
nix build .#chatgpt
```

`update.sh` 从软件源的 `dists/stable/main/binary-amd64/Packages` 索引里读出最新的
`Version` 和 `SHA256`，把后者转成 SRI 后写回 `package.nix`。需要 `curl` 和 `nix`。

因为是定值输出（FOD），哈希对不上时构建会直接失败，不会静默拿到别的东西。

也可以手动改 `package.nix` 顶部的 `version` 和 `hash` 两项——它们放在一起就是为了这个。

## 免责声明

打包的是 OpenAI 的闭源二进制，`license = unfree`，
`sourceProvenance = [ binaryNativeCode ]`。使用需遵守 OpenAI 的服务条款。
