# 三维页面依赖

固定版本 Three.js r180（npm 0.180.0），MIT：

- 源：https://github.com/mrdoob/three.js/tree/r180
- `dist/vendor/three-r180/three.module.min.js`、`three.core.min.js`、`OrbitControls.js`、`GLTFLoader.js`、`BufferGeometryUtils.js`。
- 原许可证保留在同目录 `LICENSE.txt`。控制器/加载器/工具的裸 `three` 导入改为本地 `./three.module.min.js`，GLTFLoader 的 BufferGeometryUtils 路径改为同目录；其余代码未改。

Meshoptimizer 0.22 解码器来自 Three.js r180 的 `examples/jsm/libs/meshopt_decoder.module.js`，MIT，版权 Arseny Kapoulkine。原版权声明在文件顶部，完整许可证另存 `Meshopt-LICENSE.txt`。Wasm 二进制由该上游文件内嵌，无 CDN 或外部模型服务。

`dist/models/meshopt-sample.glb` 为本项目自制测试立方体，由 MeshoptEncoder 0.22 编码位置属性；不是采购模型、测绘成果或真实设备。此模型用于验证压缩 GLB 接入与解码，几何定义见测试。
