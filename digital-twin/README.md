# 3ds Max → Unity → WebGL / Wasm 原生工程

用户在本轮明确要求必须使用 3ds Max 与 Unity。本目录实现这条工程链，不将既有 Three.js 页面或几何质检预览称为 Unity 发布成果。

## 当前状态与输入边界

- 已准备设备部件定义、3ds Max 建模/导出脚本、Unity 场景生成与交互、WebGL 模板、浏览器适配器和发布产物核验脚本。
- Unity Hub、Unity 6.3 LTS 和 WebGL Build Support 已安装到 E 盘。编辑器尚未识别到有效许可，Max 官方下载/授权仍在进行；当前未完成原生编译。只有实际完成 Max 导出、Unity 编译和 WebGL 浏览器验收后，才能把状态改为已构建/已发布。
- 未取得工程图纸、设备厂家尺寸及现场勘测数据。当前 602 个对象沿用构造布局，部件尺寸用于展示。没有现场精度、接线、厂家型号或故障确认结论。
- `preview/` 只使用 Three.js 检查与 Max 脚本共用的部件几何；它不生成 `.max`，也不证明 Unity 工程已编译。

## 设备细节

| 模型 | 部件定义数 | 内容 |
| --- | --- | --- |
| 光伏组件 | 297 | 电池片、汇流条、玻璃面、铝框、背板、接线盒与线缆 |
| 逆变器 | 54 | 柜门密封、顶盖、铰链、把手、屏幕面板、通风格栅、风扇护圈、电缆接头与基础 |
| 箱式变压器 | 94 | 多扇门、百叶、散热片、顶盖、绝缘子、端子、地脚与电缆基础 |
| 组件支架 | 14 | 支脚、底座、横梁与斜撑；安装尺寸待图纸确认 |

LOD0 保留全部部件；LOD1 去掉标记为 `detail` 的细部。数量代表建模结构，不是工程精度指标。Unity 为重复网格启用实例化材质，并使用 LOD、阴影距离和低功耗模式。

## 目录

```text
source/                 米制、Y 向上的设备部件与示意布局 JSON
3dsmax/                 MAXScript 入口及 Max 内置 Python/pymxs 建模器
unity/                  Unity 6.3 LTS 项目源码
scripts/Build.ps1        原生导出、Unity 编译与产物验证
scripts/VerifyBuild.mjs  检查真实 Wasm、模板替换、资源哈希及 Max 来源
preview/                本地几何质检，不参与正式 Unity 发布
tests/                  数据约束与几何预览检查
artifacts/              原生 MAX/FBX、日志与 WebGL 输出，不提交 Git
```

工具安装目标是 `E:\FumaiTools`。Unity 固定为官网当前 LTS `6000.3.27f1 (672d562a42a7)`，安装 WebGL Build Support。Hub 的可运行入口为 `E:\FumaiTools\Unity\HubPortable\app\Unity Hub.exe`（从已验证签名和哈希的官方 MSIX 原样释放），编辑器为 `E:\FumaiTools\Unity\Editors\6000.3.27f1\Editor\Unity.exe`，WebGL 模块已实测存在。3ds Max 使用用户在 Autodesk 官网登录后取得并激活的官方安装程序，不处理账号密码或绕过授权。

## 原生构建

先在已激活的 3ds Max 中运行 `3dsmax/BuildEquipment.ms`。必须从空场景运行；脚本不自动重置已有场景。它会用 Max 原生 Box、Cylinder、Torus、Chamfer 和 Physical Material 建模，按材质合并、保存 `.max`，通过 Autodesk FBX 插件导出两个 LOD，并记录原始文件与 FBX 的 SHA-256。

导出结果进入 `artifacts/max/<时间戳>/`，包含八对 MAX/FBX、`max-export.json` 与 `latest.json` 指针。源轴为 Y 向上，转换到 Max 的 Z 向上，再以 FBX Y 向上导出；Unity 必须实测核对尺度、旋转、原点和法线，未通过时停止发布。

在 PowerShell 中使用安装后的真实路径运行：

```powershell
.\scripts\Build.ps1 -MaxBatch 'E:\FumaiTools\Autodesk\3ds Max\3dsmaxbatch.exe' `
  -UnityEditor 'E:\FumaiTools\Unity\Editors\6000.3.27f1\Editor\Unity.exe'
```

也可在 Unity 中打开 `unity/`，依次使用 `Fumai / Build native Max scene` 与 `Fumai / Build WebGL Wasm`。缺少真实 Max 导出记录、原文件哈希不符、Unity 编译失败或没有 Wasm 时都会停止，不用替代网格悄悄生成“成功”结果。

Unity 构建器导入 FBX、匹配 PBR 材质和 LOD，生成场景地表、运维道路、围栏、设备及服务建筑，保存实际 `.unity` 场景并使用 IL2CPP 编译 WebGL。输出 `artifacts/WebGL/`；`VerifyBuild.mjs` 确认二进制 Wasm 头、Unity 模板已处理、资源哈希和来源记录。

## 浏览器与监控联动

Unity 原生运行旋转、平移、缩放、低视角、俯视、设备近览、拾取、选中高亮、昼夜光照和画质切换。模板内含设备索引与只读参数面板。未知温度、电压等不填模拟数值。

浏览器到 Unity 使用 `SendMessage`；Unity 到浏览器通过 `.jslib` 的 `FumaiNotify` 发出选中和状态事件。父子窗口只接受同源、指定 iframe 和 `fumai-unity-v1` 协议的数据，不接收任意跨源命令。网页接入代码为 `frontend/dist/scene-unity.js`，在真实发布资源可用后接入现有页面。它不会自动触发智能体分析或工单。

当前监控字段：`source=constructed_demo`、`observed_at`、`values[]`。每项含 `id`、`state`、`has_actual_kw/actual_kw`、`has_reference_kw/reference_kw`。Unity 仅接受场景已存在的 ID、有效非负功率与演示来源；不把现场数据按同名 ID 直接接到示意设备。候选状态为 `unreviewed` 或 `needs_investigation` 时显示红色标记。

Unity WebGL/IL2CPP 自身产生 Wasm 用于运行 C# 逻辑；图形由 WebGL/GPU 绘制。是否提升加载、帧率或计算性能要在目标电脑测试，不以“使用 Wasm”代替测量。

## 验收与发布

1. Max 保存和导出成功：模型可重新打开、材质/尺度/法线正确，LOD 可用。
2. Unity 导入后：三个设备的外形、细节、朝向及原点正确，交互不穿过地面。
3. 对比全站、组件、逆变器、箱变的 Unity 真实截图，记录硬件、浏览器、加载包大小、加载时间、帧时间和内存，修复后再发布。
4. 验证网页选中、参数、红色候选标记、证据和智能体联动；检查非法数据、窄屏、断网、加载失败和退出释放。
5. 核验通过的 WebGL 完整目录发布至静态资源 `unity-scene/`，保持现有账号与 API 认证。发布前保留回滚版本。不把源工程或质检网页作为构建产物上线。

源码检查：

```bash
node digital-twin/tests/recipe.test.mjs
node digital-twin/preview/serve.mjs
node digital-twin/tests/preview.browser.cjs
```

原生编译、Max 脚本运行、Unity 导入及现场精度不能由上述源码/预览检查替代。

## 官方接口依据

- [Autodesk：3ds Max Batch](https://help.autodesk.com/cloudhelp/2026/ENU/3DSMax-Batch/files/GUID-48A78515-C24B-4E46-AC5F-884FBCF40D59.htm)
- [Autodesk：FBX 导出脚本接口](https://help.autodesk.com/cloudhelp/2026/ENU/MAXScript-Help/files/MAXScript-Tools-and-Interaction/Interacting-with-the-3ds-Max/FBX-Import-and-Export-Dialog/GUID-54B1F140-B304-48A2-9829-C68B345E2044.html)
- [Unity：命令行构建](https://docs.unity.com/en-us/engine/6000.6/manual/building-and-publishing/build-command-line)
- [Unity：浏览器调用 C#](https://docs.unity3d.com/ja/current/Manual/web-interacting-browser-unity-to-js.html)
