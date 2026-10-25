# 三维电站演示与工程接入

比赛案例操作、参赛材料缺口及工程效果验证方法见 [比赛演示说明](COMPETITION_DEMO.md)。

本版采用浏览器 Three.js r180 / WebGL2，Meshopt Wasm 用于压缩 GLB 解码。开发、运行和发布不依赖商业编辑器登录或许可。

Issue #10；用户确认暂无图纸/勘测/模型，允许自主选择工具。本版采用 Three.js r180 + WebGL2。程序化模型是示意布局，不能作为真实电站坐标、接线拓扑、精度或工程验收成果。

## 当前交付

入口 `/#scene`。示意场景含 576 块组件、24 台逆变器、2 台箱变；支持旋转、平移、缩放、全站/俯视/设备近览/巡检视角、设备拾取、分类/待核查过滤、搜索、日间/傍晚光照和标准/低功耗模式。候选事件以红标记展示，空间位置与功率观测都明确标识为样例。

页面保留当前登录保护。2026-10-25 核对线上配置：主站与三维资源由 `frontend/current/dist` 直接提供，登录、通知和业务 API 仍走现有服务；无需复制或改写通知服务的旧首页。发布前核对实际 HTTP 资源哈希、当前前后端版本和登录跳转，保留同组成员的认证、路由与业务更新。

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

## 全屏与自由站位

“全屏”展开整个三维工作台，保留工具栏、设备参数与退出入口；浏览器不支持原生全屏时铺满页面。“设备面板”可收起索引，移动端全屏默认收起，按需展开。

“自由漫游”支持 WASD 或方向键前后/左右移动、Q/E 升降、Shift 加速、拖动鼠标或触屏调整朝向、R 回到 1.65 m 示意站高。屏幕方向按钮也可移动。退出漫游后恢复轨道观察。自由站位下选择设备只更新详情；点击“设备近览”才定位镜头。

输入设备搜索、输入框获得焦点、窗口失焦、页面隐藏及路由退出时停止移动输入。视点有最低高度和布局周边范围限制；当前未依据工程数据构建物理碰撞，不作为真实通行性验证。

## 日照示意与参考项目

参考 [EvergineTeam/DigitalTwin-Demo](https://github.com/EvergineTeam/DigitalTwin-Demo/tree/38675c7f6815c7759d91d8029abb241d1bc6ba85) 的光伏场景、日照变化和点击设备查参数的交互思路，保留现有 Three.js / WebGL2 页面。未复制其代码、模型、纹理或数据；其仓库标为 MIT，若以后实际复用代码或资产，须再核查各项来源并保留要求的版权声明。

核查发现：参考项目的 `SampleData.cs` 生成太阳/支架角度序列，`TimeLapseService.cs` 每 0.5 秒推进，`wwwroot/evergine.js` 为电压/电流生成随机显示值。这些是演示，不是已接入真实电站的证明。其 Web 构建说明仍写 .NET 6，而当前 Web 项目文件已是 .NET 10；接入时应以对应提交代码和实际构建为准。

本站时间轴覆盖 06:00–18:00 示意时刻，支持拖动、播放/暂停和回到正午；日间/傍晚预设同步到时间轴。播放速度为每秒 20 个示意分钟，到傍晚停止，手动再次播放从清晨开始。阴影、太阳方向、天空色调与光照亮度联动。默认暂停，页面隐藏、窗口失焦、画布离开可见区域或路由退出都会停止播放；暂停后静止场景不持续渲染。

`scene-daylight.js` 的 minute 是独立的示意钟表时间，`sunPosition` 是场景米制坐标，`altitude` 是示意角度，`simulated` 始终为 true；没有日期、经纬度、时区、真实气象或天文计算校验。时间轴不修改设备功率、事件时间、模型输出或工单。现有固定支架不旋转，不虚构跟踪系统。

后续接入顺序：核实现场支架类型和资产映射 → 提供带日期/时区与采样时刻的气象、辐照、遥测数据 → 校验单位、缺测与时效 → 通过独立适配器驱动场景和设备参数。真实历史回放必须显示数据时间、来源与质量状态；缺测不能用随机数补成实测值。

浏览器验收：`node frontend/tests/scene-daylight.browser.cjs`，使用与其它三维验收相同的环境变量；此测试身份夹具仅接受 localhost，公网保持正常登录。
