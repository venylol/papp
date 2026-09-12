# AP background PNG runtime

The existing PAPP PNG renderers run on a native Canvas implementation when the browser is closed.

Vendored packages: `@napi-rs/canvas` and `@napi-rs/canvas-win32-x64-msvc`, both version 1.0.9, downloaded from their npm registry tarballs. Each package retains its license and package metadata. No adjacent repository or online runtime is required. This bundled native build supports Windows x64.

Upstream: https://github.com/Brooooooklyn/canvas
