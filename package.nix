# OpenAI Codex desktop app ("ChatGPT for Linux"), repackaged from the Debian
# package OpenAI publishes at
#   https://persistent.oaistatic.com/codex-app-prod/linux/deb
#
# The archive ships a self-contained Electron/Chromium runtime plus the Codex
# agent itself under lib/chatgpt/resources (codex, rg, node, tectonic, ...).
# Everything below only relinks that runtime against the Nix store and installs
# a wrapper that points the process at the right library/data directories.
{
  lib,
  stdenv,
  binutils,
  fetchurl,
  autoPatchelfHook,
  makeWrapper,
  xz,
  # Chromium only dlopen()s the bundled libqt{5,6}_shim.so when it is asked to
  # use the Qt platform backend, so pulling in a full Qt is opt-in.
  withQtShims ? false,
  qt5,
  qt6,

  # Libraries the bundled Chromium/Electron runtime links against.
  alsa-lib,
  at-spi2-atk,
  at-spi2-core,
  atk,
  cairo,
  cups,
  dbus,
  expat,
  fontconfig,
  freetype,
  gdk-pixbuf,
  glib,
  gtk3,
  libdrm,
  libgbm,
  libGL,
  libnotify,
  libpulseaudio,
  libusb1,
  libxkbcommon,
  libX11,
  libxcb,
  libXcomposite,
  libXdamage,
  libXext,
  libXfixes,
  libXrandr,
  libXScrnSaver,
  libXtst,
  nspr,
  nss,
  pango,
  systemd,
  vulkan-loader,
}:

let
  # Both of these are refreshed from the signed repository index by update.sh.
  version = "26.928.21956";
  src = fetchurl {
    url = "https://persistent.oaistatic.com/codex-app-prod/linux/deb/pool/main/c/chatgpt/chatgpt_${version}_amd64.deb";
    hash = "sha256-msjQcRtGATaNSd7dv1Co/lI1jLYbbZ96XBi0HtRQCtg=";
  };

  runtimeLibs = [
    alsa-lib
    at-spi2-atk
    at-spi2-core
    atk
    cairo
    cups
    dbus
    expat
    fontconfig
    freetype
    gdk-pixbuf
    glib
    gtk3
    libdrm
    libgbm
    libGL
    libnotify
    libpulseaudio
    libusb1
    libxkbcommon
    libX11
    libxcb
    libXcomposite
    libXdamage
    libXext
    libXfixes
    libXrandr
    libXScrnSaver
    libXtst
    nspr
    nss
    pango
    systemd # libudev
    vulkan-loader
  ]
  ++ lib.optionals withQtShims [
    qt5.qtbase
    qt6.qtbase
  ];

  libPath = lib.makeLibraryPath runtimeLibs;

  # Alpine-flavoured prebuilds of node-hid/serialport. They are shipped next to
  # the glibc ones and never selected on NixOS, so there is no musl to link to.
  muslPrebuilds = [ "libc.musl-x86_64.so.1" ];

  qtShimLibs = [
    "libQt5Core.so.5"
    "libQt5Gui.so.5"
    "libQt5Widgets.so.5"
    "libQt6Core.so.6"
    "libQt6Gui.so.6"
    "libQt6Widgets.so.6"
  ];
in
stdenv.mkDerivation {
  pname = "chatgpt";
  inherit version src;

  strictDeps = true;

  nativeBuildInputs = [
    binutils
    autoPatchelfHook
    makeWrapper
    xz
  ];

  buildInputs = runtimeLibs;

  dontConfigure = true;
  dontBuild = true;
  dontStrip = true;

  autoPatchelfIgnoreMissingDeps = muslPrebuilds ++ lib.optionals (!withQtShims) qtShimLibs;

  unpackPhase = ''
    runHook preUnpack

    ar x "$src"
    tar -xf data.tar.xz

    runHook postUnpack
  '';

  installPhase = ''
    runHook preInstall

    appdir="$out/lib/chatgpt"
    mkdir -p "$appdir" "$out/bin"
    cp -a usr/lib/chatgpt/. "$appdir/"

    install -Dm644 usr/share/applications/chatgpt.desktop \
      "$out/share/applications/chatgpt.desktop"
    install -Dm644 usr/share/pixmaps/chatgpt.png \
      "$out/share/pixmaps/chatgpt.png"
    install -Dm644 usr/share/pixmaps/chatgpt.png \
      "$out/share/icons/hicolor/1024x1024/apps/chatgpt.png"
    install -Dm644 usr/share/metainfo/com.openai.chatgpt.metainfo.xml \
      "$out/share/metainfo/com.openai.chatgpt.metainfo.xml"
    install -Dm644 usr/share/swcatalog/xml/com.openai.chatgpt.xml \
      "$out/share/swcatalog/xml/com.openai.chatgpt.xml"

    # The .desktop file is only discoverable when this package is on the
    # session's XDG_DATA_DIRS, so the launcher has to be addressed absolutely.
    substituteInPlace "$out/share/applications/chatgpt.desktop" \
      --replace-fail 'Exec=chatgpt' "Exec=$out/bin/chatgpt"

    runHook postInstall
  '';

  # The bundled native modules (better-sqlite3, node-pty, node-hid, ...) are
  # relinked by autoPatchelfHook; this only adds the environment the Chromium
  # runtime expects when it is not installed system-wide.
  postFixup = ''
    makeWrapper "$out/lib/chatgpt/codex-launcher" "$out/bin/chatgpt" \
      --prefix LD_LIBRARY_PATH : "${libPath}" \
      --prefix XDG_DATA_DIRS : "$out/share" \
      --prefix XDG_DATA_DIRS : "${gdk-pixbuf}/share" \
      --set GDK_PIXBUF_MODULE_FILE "${gdk-pixbuf}/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache" \
      --set GSETTINGS_SCHEMA_DIR "${glib}/share/glib-2.0/schemas"
  '';

  passthru.updateScript = ./update.sh;

  meta = {
    description = "Desktop application for OpenAI Codex";
    longDescription = ''
      The OpenAI Codex desktop app (shipped by OpenAI as the "chatgpt" Debian
      package on Linux). It bundles its own Electron runtime plus the Codex
      agent, ripgrep and Node.js under lib/chatgpt/resources.
    '';
    homepage = "https://developers.openai.com/codex/app";
    downloadPage = "https://persistent.oaistatic.com/codex-app-prod/linux/deb";
    license = lib.licenses.unfree;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    platforms = [ "x86_64-linux" ];
    mainProgram = "chatgpt";
  };
}
