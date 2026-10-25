# 三维电站演示与工程接入

比赛案例操作、参赛材料缺口及工程效果验证方法见 [比赛演示说明](COMPETITION_DEMO.md)。

本版采用浏览器 Three.js r180 / WebGL2，Meshopt Wasm 用于压缩 GLB 解码。开发、运行和发布不依赖商业编辑器登录或许可。

Issue #10；用户确认暂无图纸/勘测/模型，允许自主选择工具。本版采用 Three.js r180 + WebGL2。程序化模型是示意布局，不能作为真实电站坐标、接线拓扑、精度或工程验收成果。

## 当前交付

入口 `/#scene`。示意场景含 576 块组件、24 台逆变器、2 台箱变；支持旋转、平移、缩放、全站/俯视/设备近览/巡检视角、设备拾取、分类/待核查过滤、搜索、日间/傍晚光照和标准/低功耗模式。候选事件以红标记展示，空间位置与功率观测都明确标识为样例。

页面保留当前登录保护。现有服务器的 `/` 与 `/index.html` 由通知服务静态目录代理，因此发布本页同时把同样的场景脚本、样式和导航增量合入该入口；不更改认证、Nginx 或后端逻辑。后续发布应同步这两个入口，避免只更新前端版本标识而漏掉实际首页。

已有样例事件可打开证据或智能体。模型选择不自动触发诊断或控制设备。导入的工程布局不自动继承同编号样例事件；真实业务状态须在完成身份、编号和时间语义映射后另行接入。无 WebGL 时仍可使用设备索引。

## 本轮精细化

组件增加铝框、背板、电池片与汇流条、接线盒及线缆；支架包含底座、横梁和斜撑；逆变器、箱变增加柜门、散热与接线细节。地表使用程序纹理，补充草丛、道路、围栏、服务建筑、天空与环境反射。尺寸与布局均为示意，待图纸或测绘资料替换。

重复组件和支架使用实例化网格；设备部件按材质合并。远景使用简化结构，选中组件近览时才显示完整细节，柜体细部按相机距离显示；低功耗模式关闭细部、草丛和动态阴影。静止后停止请求渲染帧。性能仍需在比赛设备上实际测量。

共享设备定义位于 `frontend/dist/scene-equipment-data.js`，渲染器位于 `scene-equipment.js`，环境位于 `scene-landscape.js`；不需要生成或导入原生编辑器工程。

## 模型工具与精度

后续可使用能够导出 GLB 的建模或工程设计工具。网页接收自包含 **GLB 2.0**，不直接执行 .max/.rvt 文件。收集图纸版本、测量日期、坐标系统/原点、设备资产编号及误差验收标准后，再替换示意模型；没有这些资料不宣称高精度或现场数字孪生。

推荐流程：核对图纸与测量 → 统一米制、Y 向上与局部原点 → 清理隐藏面/合并材质/保留设备边界 → 导出 GLB → 必要时做 Meshopt 压缩 → 核对尺度、朝向、编号和拾取 → 设备、位置及状态联调 → 工程精度验收。压缩或减面后须复核关键尺寸。

GLB 顶点以局部模型原点组织，布局负责场景平移及角度；模型应按底部/安装基准建原点。position 单位米，rotation 是 XYZ 欧拉角（度），scale 仅用于导入模型。本站文件路径必须形如 `./models/device.glb`，单模型至多 25 MiB；所有图像/几何自包含，外部资源 URI 拒绝。暂不接入 Draco/KTX2，需转为普通或 Meshopt GLB。

## 布局 JSON

页面“下载接入模板”导出当前布局；“导入场景”仅更新当前浏览器，不上传或更改生产数据库。最多 5000 个设备、2 MiB JSON，坐标限局部 ±10 km；大型测绘坐标先依据记录的 origin 转为局部坐标，origin 本身仅记录来源，不自动进行投影转换。

```json
{
  "version": 1,
  "source": "engineering_design",
  "name": "工程布局（待验收）",
  "units": "m",
  "up": "Y",
  "coordinateSystem": "local_site_2026",
  "origin": [0, 0, 0],
  "assets": [{
    "id": "inv_07",
    "type": "inverter",
    "name": "逆变器 07",
    "zone": "B 区",
    "position": [12, 0, -8],
    "rotation": [0, 90, 0],
    "scale": [1, 1, 1],
    "model": "./models/inverter.glb"
  }]
}
```

source 可为 constructed_demo / engineering_design / field_survey，后两者都显示“用户导入、待验收”；不因自报来源就声称已验证。设备 type 为 panel / inverter / transformer；可选 group 指向同场景已有编号，示意组件用它关联逆变器。设备编号唯一且只允许英数字、下划线及短横线。编号映射必须经过业务确认，不能仅凭名称关联真实事件。

随附 `/models/meshopt-sample.json` 和 `.glb` 是自制立方体接入样例，导入该 JSON 可验证 Wasm 压缩模型加载。后续工程模型随静态发布放到 `frontend/dist/models/`，不将未授权图纸或敏感测绘数据直接提交公共仓库。

## WebGL / Wasm 的职责

- 重复组件用 InstancedMesh 批处理，限制像素比，静止时停止绘制，页面隐藏暂停；路由退出释放控制器、观察器、纹理、几何和 WebGL 上下文。
- **Wasm 已实际用于 Meshopt 压缩几何的 CPU 解码**；加载 GLB 时按需初始化上游 MeshoptDecoder。它不替代 GPU 的 WebGL 渲染，也不承诺提升任意场景帧率。程序化示意场景不需要解码，不为演示额外做无意义 Wasm 运算。
- 后续大场景可依据实测增加分块、LOD、纹理压缩与 Worker；高精度模型必须给出包大小、解码时间、显存与目标终端交互帧时间预算，再选择优化方案。

## 验证

```bash
node frontend/tests/scene.test.mjs
node --test frontend/tests/agent-task.test.mjs frontend/tests/intent.test.mjs
node frontend/serve.mjs
```

浏览器回归 `frontend/tests/scene.browser.cjs`，设置 FUMAI_CHECK_URL / FUMAI_PLAYWRIGHT_MODULE / FUMAI_BROWSER_EXECUTABLE / FUMAI_TEST_OUTPUT。包含实际 WebGL、射线拾取、模型加载、真实 Wasm 解码、无 WebGL、导入失败回退、路由释放和手机布局。测试内核的 WebGL 性能不能当作现场终端帧率保证。

仅本地回归用拦截的测试身份，不更改应用认证代码。公网可显式设置 FUMAI_SCENE_REGISTER_VIEWER=1，走已有正常注册 API 创建一次无设备绑定的 viewer 测试账号，验收后注销会话；使用保留的 example.invalid 邮箱，不发通知。生产登录重定向另行验证，不通过拦截模拟公网身份。

原理与许可证：[InstancedMesh](https://threejs.org/docs/pages/InstancedMesh.html)、[GLTFLoader](https://threejs.org/docs/pages/GLTFLoader.html)、[Meshoptimizer 解码器](https://github.com/zeux/meshoptimizer/tree/v0.22/js)、`frontend/THIRD_PARTY_NOTICES.md`。
